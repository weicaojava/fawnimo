"""Trusted isolated decoder preserving integer PTS and rational stream clocks."""
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import threading
from fractions import Fraction

ACTIVE=None

V2_PARAMETERS={'baseline_radius':10,'baseline_exclusion':2,'baseline_floor':0.012,'minimum_mad':0.045,'minimum_spike_ratio':2.8,'histogram_bins':16,'minimum_histogram_change':0.18,'minimum_structure_change':0.35,'persistence_frames':3,'minimum_persistent_frames':2,'cluster_us':150000}
V3_PARAMETERS={'minimum_mad':0.025,'neighbor_offsets':[-2,-1,1,2],'baseline_floor':0.008,'minimum_spike_ratio':2.5,'minimum_jump_excess':0.020,'minimum_immediate_neighbor_ratio':1.8,'persistence_frames':3,'minimum_persistent_frames':2,'cluster_us':150000}
V4_PARAMETERS={**V3_PARAMETERS,'patch_columns':8,'patch_rows':6,'patch_minimum_mad':0.025,'patch_global_mad_fraction':0.35,'minimum_changed_patch_fraction':0.70,'cluster_us':0}


def discrete_candidates(features, frames, *, cluster=True):
    """Use discrete temporal jumps without rejecting similar-composition hard cuts."""
    import numpy as np
    p=V3_PARAMETERS;found=[]
    differences=[0.0]+[float(np.abs(features[i]-features[i-1]).mean()/255) for i in range(1,len(features))]
    for i in range(1,len(features)-1):
        d=differences[i]
        neighbors=[differences[i+offset] for offset in p['neighbor_offsets'] if 1<=i+offset<len(features)]
        baseline=max(p['baseline_floor'],float(np.median(neighbors)))
        if d<p['minimum_mad'] or d/baseline<p['minimum_spike_ratio'] or d-baseline<p['minimum_jump_excess']:continue
        if max(differences[i-1],differences[i+1])>d/p['minimum_immediate_neighbor_ratio']:continue
        before=np.median(np.stack(features[max(0,i-3):i]),axis=0)
        after=features[i:min(len(features),i+3)]
        if sum(float(np.abs(image-before).mean()/255)>=p['minimum_mad'] for image in after)<2:continue
        candidate={'frame_index':i,'relative_us':frames[i]['relative_us'],'score':round(d/baseline,6),'mad':round(d,6),'baseline':round(baseline,6),'kind':'candidate'}
        if cluster and found and candidate['relative_us']-found[-1]['relative_us']<p['cluster_us']:
            if candidate['score']>found[-1]['score']:found[-1]=candidate
        else:found.append(candidate)
    if len(found)>255:raise ValueError('Candidate/keyframe limit exceeds 256')
    return found


def spatial_candidates(features, frames):
    """Reject local foreground changes while retaining temporally distinct fast edits."""
    import numpy as np
    p=V4_PARAMETERS;found=[]
    for candidate in discrete_candidates(features,frames,cluster=False):
        i=candidate['frame_index']
        before=np.median(np.stack(features[max(0,i-3):i]),axis=0)
        after=np.median(np.stack(features[i:min(len(features),i+3)]),axis=0)
        difference=np.abs(after-before)/255
        threshold=max(p['patch_minimum_mad'],float(difference.mean())*p['patch_global_mad_fraction'])
        # Exactly 48 equal-area patches: 36 rows / 6 and 64 columns / 8.
        patch_mad=difference.reshape(6,6,8,8,3).mean(axis=(1,3,4))
        coverage=float((patch_mad>=threshold).mean())
        if coverage>=p['minimum_changed_patch_fraction']:
            found.append({**candidate,'changed_patch_fraction':round(coverage,6),'patch_mad_threshold':round(threshold,6)})
    return found


def adaptive_candidates(features, frames):
    """Detect persistent distribution/structure discontinuities above local motion.

    Fixed v2 parameters are independent of any particular clip or ground truth.
    This remains a candidate detector: prolonged abrupt occlusion is intrinsically
    ambiguous with a hard edit and requires review rather than invented certainty.
    """
    import numpy as np
    p=V2_PARAMETERS
    differences=[0.0]+[float(np.abs(features[i]-features[i-1]).mean()/255) for i in range(1,len(features))]
    proposed=[]
    for i in range(1,len(features)):
        neighbors=[differences[j] for j in range(max(1,i-10),min(len(features),i+11)) if abs(j-i)>2]
        baseline=max(p['baseline_floor'],float(np.median(neighbors)) if neighbors else 0)
        ratio=differences[i]/baseline
        if differences[i]<p['minimum_mad'] or ratio<p['minimum_spike_ratio']:continue
        before=np.median(np.stack(features[max(0,i-3):i]),axis=0)
        after_frames=features[i:min(len(features),i+3)]
        if len(after_frames)<2:continue
        after=np.median(np.stack(after_frames),axis=0)
        hist=[]
        for channel in range(3):
            left=np.histogram(before[:,:,channel],bins=16,range=(0,256))[0]/before[:,:,channel].size
            right=np.histogram(after[:,:,channel],bins=16,range=(0,256))[0]/after[:,:,channel].size
            hist.append(float(np.abs(left-right).sum()/2))
        histogram=float(np.mean(hist))
        left=before.mean(axis=2);right=after.mean(axis=2)
        left=(left-left.mean())/max(float(left.std()),8)
        right=(right-right.mean())/max(float(right.std()),8)
        structure=float(np.minimum(np.abs(left-right)/4,1).mean())
        persistent=sum(float(np.abs(image-before).mean()/255)>=p['minimum_mad'] for image in after_frames)
        if persistent<2 or (histogram<p['minimum_histogram_change'] and structure<p['minimum_structure_change']):continue
        candidate={'frame_index':i,'relative_us':frames[i]['relative_us'],'score':round(ratio,6),'mad':round(differences[i],6),'baseline':round(baseline,6),'histogram_change':round(histogram,6),'structure_change':round(structure,6),'kind':'candidate'}
        if proposed and candidate['relative_us']-proposed[-1]['relative_us']<p['cluster_us']:
            if candidate['score']>proposed[-1]['score']:proposed[-1]=candidate
        else:proposed.append(candidate)
    if len(proposed)>255:raise ValueError('Candidate/keyframe limit exceeds 256')
    return proposed


def micros(value):
    """Convert a rational timestamp to nearest integer microseconds without floats."""
    return round(value*1000000)


def duration(stream):
    """Report declared stream duration when present, without inventing nominal timing."""
    return micros(Fraction(stream.duration)*stream.time_base) if stream.duration is not None else None


def run_ffmpeg(executable, args):
    """Run only trusted argv with bounded subprocess time and no stdin or network."""
    global ACTIVE
    ACTIVE=subprocess.Popen([executable,'-nostdin','-v','error','-threads','1','-protocol_whitelist','file,pipe',*args],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
    try:
        _,error=ACTIVE.communicate(timeout=75)
        if ACTIVE.returncode:raise ValueError('FFmpeg derivative creation failed')
    finally:
        if ACTIVE.poll() is None:ACTIVE.kill();ACTIVE.wait()
        ACTIVE=None


def analyze(options):
    """Decode each original frame and derive candidate cuts plus review artifacts."""
    import av
    import numpy as np
    source=Path(options['path']);out=Path(options['output_dir']);executable=options['ffmpeg']
    if source.stat().st_size>64*1024*1024:raise ValueError('Input exceeds 64MB')
    digest=hashlib.sha256(source.read_bytes()).hexdigest()
    frames=[];cuts=[];keyframes=[];features=[];previous=None;first=None;last=None
    with av.open(str(source),options={'protocol_whitelist':'file,pipe','enable_drefs':'0'}) as container:
        videos=list(container.streams.video)
        if len(videos)!=1:raise ValueError('Exactly one video track required')
        stream=videos[0]
        if not 0<stream.width<=1920 or not 0<stream.height<=1920:raise ValueError('Dimensions exceed 1920x1920')
        if container.duration is not None and container.duration>120000000:raise ValueError('Container duration exceeds 120 seconds')
        if duration(stream) is not None and duration(stream)>120000000:raise ValueError('Video duration exceeds 120 seconds')
        audio={'present':bool(container.streams.audio),'tracks':[{'index':s.index,'time_base_num':s.time_base.numerator,'time_base_den':s.time_base.denominator,'start_pts':s.start_time,'duration_us':duration(s)} for s in container.streams.audio],'wav_path':None}
        if any(track['duration_us'] is not None and track['duration_us']>120000000 for track in audio['tracks']):raise ValueError('Audio duration exceeds 120 seconds')
        metadata={'duration_us':container.duration,'format':container.format.name}
        video={'width':stream.width,'height':stream.height,'time_base_num':stream.time_base.numerator,'time_base_den':stream.time_base.denominator,'start_pts':stream.start_time,'declared_duration_us':duration(stream)}
        for frame in container.decode(stream):
            if len(frames)>=7200:raise ValueError('Frame count exceeds 7200')
            if not 0<frame.width<=1920 or not 0<frame.height<=1920:raise ValueError('Decoded dimensions exceed 1920x1920')
            if frame.pts is None or frame.time_base is None:raise ValueError('Original frame missing PTS/time_base')
            stamp=Fraction(frame.pts)*frame.time_base
            if first is None:first=stamp
            if last is not None and stamp<last:raise ValueError('Non-monotonic presentation timestamps')
            relative=micros(stamp-first)
            if relative>120000000:raise ValueError('Decoded timeline exceeds 120 seconds')
            last=stamp;index=len(frames)
            frames.append({'index':index,'pts':frame.pts,'time_base_num':frame.time_base.numerator,'time_base_den':frame.time_base.denominator,'relative_us':relative})
            rgb=frame.reformat(width=64,height=36,format='rgb24').to_ndarray().astype(np.int16)
            features.append(rgb)
            score=float(np.abs(rgb-previous).mean()/255) if previous is not None else 0.0
            is_cut=previous is not None and score>=0.20
            if is_cut:
                if len(cuts)>=255:raise ValueError('Candidate/keyframe limit exceeds 256')
                cuts.append({'frame_index':index,'relative_us':relative,'score':round(score,6),'kind':'candidate'})
            if index==0 or is_cut:
                name=f'keyframe-{index:05d}.jpg';image=frame.to_image();image.thumbnail((480,480));image.save(out/name,format='JPEG',quality=85)
                keyframes.append({'frame_index':index,'relative_us':relative,'path':name})
            previous=rgb
    if not frames:raise ValueError('No decodable video frames')
    v1_cuts=cuts
    v2_cuts=adaptive_candidates(features,frames)
    v3_cuts=discrete_candidates(features,frames)
    cuts=spatial_candidates(features,frames)
    wanted={0,*[item['frame_index'] for item in cuts]}
    keyframes=[]
    with av.open(str(source),options={'protocol_whitelist':'file,pipe','enable_drefs':'0'}) as key_container:
        for index,frame in enumerate(key_container.decode(video=0)):
            if index in wanted:
                name=f'keyframe-{index:05d}.jpg';image=frame.to_image();image.thumbnail((480,480));image.save(out/name,format='JPEG',quality=85)
                keyframes.append({'frame_index':index,'relative_us':frames[index]['relative_us'],'path':name})
    for orphan in out.glob('keyframe-*.jpg'):
        if int(orphan.stem.split('-')[-1]) not in wanted:orphan.unlink()
    video['frames']=frames;video['decoded_span_us']=frames[-1]['relative_us']
    for track in audio['tracks']:
        with av.open(str(source),options={'protocol_whitelist':'file,pipe','enable_drefs':'0'}) as audio_container:
            audio_stream=audio_container.streams[track['index']]
            first_audio=next(audio_container.decode(audio_stream),None)
            if first_audio is None or first_audio.pts is None:raise ValueError('Audio track has no decoded PTS')
            track['first_decoded_pts']=first_audio.pts
            track['decoded_time_base_num']=first_audio.time_base.numerator
            track['decoded_time_base_den']=first_audio.time_base.denominator
            track['origin_relative_us']=micros(Fraction(first_audio.pts)*first_audio.time_base-first)
    ratio=min(1,960/max(video['width'],video['height']));pw=max(2,int(video['width']*ratio)//2*2);ph=max(2,int(video['height']*ratio)//2*2)
    mapping=[];cursor=0
    # Construct the CFR grid directly from rational original presentation times.
    original_times=[Fraction(f['pts']*f['time_base_num'],f['time_base_den'])-first for f in frames]
    last_interval=original_times[-1]-original_times[-2] if len(frames)>1 else Fraction(1,30)
    end=original_times[-1]+last_interval
    for grid in range(min(3601,int(end*30+1))):
        instant=Fraction(grid,30)
        if instant>=end:break
        while cursor+1<len(frames) and abs(original_times[cursor+1]-instant)<abs(original_times[cursor]-instant):cursor+=1
        timestamp=micros(instant);original=frames[cursor]['relative_us']
        mapping.append({'proxy_index':grid,'relative_us':timestamp,'source_index':cursor,'source_relative_us':original,'error_us':timestamp-original})
    if len(mapping)>3600:raise ValueError('Proxy exceeds 120 seconds')
    with av.open(str(out/'proxy.mp4'),'w') as encoded:
        output_stream=encoded.add_stream('libx264',rate=30)
        output_stream.width=pw;output_stream.height=ph;output_stream.pix_fmt='yuv420p'
        output_stream.codec_context.thread_count=1;output_stream.codec_context.time_base=Fraction(1,30)
        next_grid=0
        with av.open(str(source),options={'protocol_whitelist':'file,pipe','enable_drefs':'0'}) as originals:
            for source_index,frame in enumerate(originals.decode(video=0)):
                while next_grid<len(mapping) and mapping[next_grid]['source_index']==source_index:
                    selected=frame.reformat(width=pw,height=ph,format='yuv420p')
                    selected.pts=next_grid;selected.time_base=Fraction(1,30)
                    for packet in output_stream.encode(selected):encoded.mux(packet)
                    next_grid+=1
                if (out/'proxy.mp4').exists() and (out/'proxy.mp4').stat().st_size>64*1024*1024:raise ValueError('Proxy exceeds 64MB')
        for packet in output_stream.encode():encoded.mux(packet)
    if (out/'proxy.mp4').stat().st_size>64*1024*1024:raise ValueError('Proxy exceeds 64MB')
    warnings=[]
    decoded_count=0
    with av.open(str(out/'proxy.mp4'),options={'protocol_whitelist':'file,pipe','enable_drefs':'0'}) as proxy:
        origin=None
        for proxy_index,frame in enumerate(proxy.decode(video=0)):
            decoded_count+=1
            stamp=Fraction(frame.pts)*frame.time_base
            if origin is None:origin=stamp
            timestamp=micros(stamp-origin)
            if proxy_index>=len(mapping) or abs(timestamp-mapping[proxy_index]['relative_us'])>1:raise ValueError('Proxy timestamps do not match encoded grid')
            cursor=mapping[proxy_index]['source_index']
            decoded_rgb=frame.reformat(width=64,height=36,format='rgb24').to_ndarray().astype(np.int16)
            visual_error=float(np.abs(decoded_rgb-features[cursor]).mean()/255)
            mapping[proxy_index]['visual_error_mean']=round(visual_error,6)
            if visual_error>0.10 and 'proxy_visual_mapping_mismatch' not in warnings:warnings.append('proxy_visual_mapping_mismatch')
    if decoded_count!=len(mapping):raise ValueError('Proxy decoded frame count does not match encoded mapping')
    gaps=[frames[i]['relative_us']-frames[i-1]['relative_us'] for i in range(1,len(frames))]
    if not mapping or mapping[-1]['relative_us']+33334<frames[-1]['relative_us']:raise ValueError('Proxy truncated before original final frame')
    if mapping and gaps and abs(mapping[-1]['error_us'])>gaps[-1]:warnings.append('proxy_tail_exceeds_original_frame_interval')
    if audio['present']:
        run_ffmpeg(executable,['-enable_drefs','0','-i',str(source),'-map','0:a:0','-vn','-ac','1','-ar','16000','-c:a','pcm_s16le','-y',str(out/'audio.wav')]);audio['wav_path']='audio.wav'
    if sum(p.stat().st_size for p in out.iterdir() if p.is_file())>128*1024*1024:raise ValueError('Probe outputs exceed 128MB')
    return {'schema_version':1,'source':{'sha256':digest,'bytes':source.stat().st_size},'container':metadata,'video':video,'cuts':cuts,'candidate_algorithm':'spatial_coverage_discrete_rgb_v4','candidate_parameters':V4_PARAMETERS,'v1_cuts':v1_cuts,'v2_cuts':v2_cuts,'v3_cuts':v3_cuts,'scene_threshold':0.20,'scene_metric':'rgb_mean_absolute_64x36_v1','cuts_are_ground_truth':False,'keyframes':keyframes,'audio':audio,'proxy':{'path':'proxy.mp4','mapping_method':'encoded_nearest_original_rational_pts','fps_num':30,'fps_den':1,'frame_mapping':mapping,'max_error_us':max((abs(m['error_us']) for m in mapping),default=0),'warnings':warnings,'degraded':bool(warnings)}}


def main():
    """Enforce a child watchdog independent of the application's event loop."""
    options=json.loads(sys.stdin.buffer.read())
    def expire():
        """Kill an active derivative process before terminating this decoder."""
        if ACTIVE is not None:
            try:ACTIVE.kill();ACTIVE.wait(timeout=2)
            except Exception:pass
        os._exit(124)
    timer=threading.Timer(min(90,float(options['deadline'])),expire);timer.daemon=True;timer.start()
    try:
        result=analyze(options);sys.stdout.write(json.dumps(result));sys.stdout.flush()
    except Exception as exc:
        sys.stdout.write(json.dumps({'error':str(exc)[:240]}));sys.stdout.flush();sys.exit(2)
    finally:timer.cancel()


if __name__=='__main__':main()
