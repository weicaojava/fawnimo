"""Private v2 FFmpeg compositor; imports v1 decoder/sampler without changing its hard-cut renderer."""
import copy
import json
from pathlib import Path
import sys
import threading
import zipfile
import media_child as media

PROFILE = 'portrait-360p30-linear-crossfade-v2'


def seconds(milliseconds):
    """Serialize exact integer milliseconds into generated filter parameters, never user expressions."""
    return f'{milliseconds // 1000}.{milliseconds % 1000:03d}'


def assemble(parameters):
    """Resample real original display intervals, then actually mix adjacent pictures and PCM samples."""
    folder, ffmpeg = Path(parameters['folder']).resolve(), Path(parameters['ffmpeg']).resolve()
    payload = parameters['payload']
    if not ffmpeg.is_file() or not folder.is_dir() or any(folder.iterdir()): raise ValueError('Invalid private transition attempt')
    clips, edges = payload['clips'], payload['transitions']
    if payload['schema_version'] != 2 or payload['profile'] != PROFILE or not 1 <= len(clips) <= 100 or len(edges) != len(clips) - 1:
        raise ValueError('Invalid v2 profile')
    expected_duration = sum(clip['source_out_ms'] - clip['source_in_ms'] for clip in clips) - sum(edge['duration_ms'] for edge in edges)
    if expected_duration != payload['duration_ms'] or not 100 <= expected_duration <= 300000 or expected_duration % 100:
        raise ValueError('Invalid overlap duration')
    checked = {}
    for index, clip in enumerate(clips):
        source, path = clip['source'], Path(clip['path']).resolve()
        if source['id'] not in checked: checked[source['id']] = media.probe(path, source['sha256'])
        actual = checked[source['id']]
        if actual['video'] != source['video'] or actual['audio'] != source['audio']: raise ValueError('Source clocks changed')
        if not any(start <= clip['source_in_ms'] * 1000 and end >= clip['source_out_ms'] * 1000 for start, end in actual['video']['coverage_us']): raise ValueError('Missing actual video interval')
        if clip['source_audio'] and actual['audio'] is not None and not any(start <= clip['source_in_ms'] * 1000 and end >= clip['source_out_ms'] * 1000 for start, end in actual['audio']['coverage_us']): raise ValueError('Missing actual audio interval')
        expected_frames = (clip['source_out_ms'] - clip['source_in_ms']) * 3 // 100
        if media.encode_video([clip], folder / f'part-{index}.mp4') != expected_frames: raise ValueError('Source sampling incomplete')
    command = [str(ffmpeg), '-nostdin', '-v', 'error', '-y', '-threads', '1']
    for index in range(len(clips)):
        command.extend(['-protocol_whitelist', 'file,pipe', '-enable_drefs', '0', '-f', 'mov', '-i', str(folder / f'part-{index}.mp4')])
    # These intermediates have already been sampled on the verified 30fps grid; fps advertises
    # that exact rate to xfade, rather than guessing or extending an original source's clock.
    filters = [f'[{index}:v:0]settb=AVTB,setpts=PTS-STARTPTS,fps=30:start_time=0:eof_action=pass[v{index}]' for index in range(len(clips))]
    video_label, accumulated = 'v0', clips[0]['source_out_ms'] - clips[0]['source_in_ms']
    for index, edge in enumerate(edges, 1):
        fade = edge['duration_ms']
        if (edge['left_shot_id'], edge['right_shot_id']) != (clips[index - 1]['shot_id'], clips[index]['shot_id']): raise ValueError('Transition order changed')
        if (edge['kind'] == 'hard_cut' and fade) or (edge['kind'] == 'crossfade' and (fade < 100 or fade > 1000 or fade % 100)): raise ValueError('Invalid transition edge')
        output = f'vm{index}raw'
        if fade:
            filters.append(f'[{video_label}][v{index}]xfade=transition=fade:duration={seconds(fade)}:offset={seconds(accumulated - fade)}[{output}]')
        else:
            filters.append(f'[{video_label}][v{index}]concat=n=2:v=1:a=0[{output}]')
        video_label = f'vm{index}'
        filters.append(f'[{output}]settb=AVTB,setpts=PTS-STARTPTS,fps=30:start_time=0:eof_action=pass[{video_label}]')
        accumulated += clips[index]['source_out_ms'] - clips[index]['source_in_ms'] - fade
    audio_input = len(clips)
    for index, clip in enumerate(clips):
        count = (clip['source_out_ms'] - clip['source_in_ms']) * 48
        if clip['source_audio'] and clip['source']['audio'] is not None:
            command.extend(['-protocol_whitelist', 'file,pipe', '-enable_drefs', '0', '-f', 'mov', '-i', str(clip['path'])])
            video = clip['source']['video']
            expression = f'PTS-({video["first_pts_num"]}/{video["first_pts_den"]})/TB'
            filters.append(f'[{audio_input}:a:0]asetpts={expression},aresample=48000:async=0:first_pts=0,aformat=sample_fmts=fltp:channel_layouts=stereo,atrim=start_sample={clip["source_in_ms"] * 48}:end_sample={clip["source_out_ms"] * 48},asetpts=N/SR/TB[a{index}]')
            audio_input += 1
        else:
            filters.append(f'anullsrc=r=48000:cl=stereo,atrim=end_sample={count},asetpts=N/SR/TB[a{index}]')
    audio_label = 'a0'
    for index, edge in enumerate(edges, 1):
        output = f'am{index}'
        if edge['duration_ms']:
            filters.append(f'[{audio_label}][a{index}]acrossfade=ns={edge["duration_ms"] * 48}:c1=tri:c2=tri[{output}]')
        else:
            filters.append(f'[{audio_label}][a{index}]concat=n=2:v=0:a=1[{output}]')
        audio_label = output
    filters.extend([f'[{video_label}]format=yuv420p[video]', f'[{audio_label}]atrim=end_sample={expected_duration * 48},asetpts=N/SR/TB[audio]'])
    command.extend(['-filter_complex_threads', '1', '-filter_complex', ';'.join(filters), '-map', '[video]', '-map', '[audio]',
        '-c:v', 'libx264', '-preset', 'veryfast', '-crf', '18', '-threads', '1', '-r', '30', '-fps_mode', 'cfr', '-pix_fmt', 'yuv420p',
        '-c:a', 'aac', '-b:a', '128k', '-ar', '48000', '-ac', '2', '-movflags', '+faststart', '-fs', str(media.LIMIT), str(folder / 'video.mp4')])
    media.run_encoder(command, folder / 'encoder-v2.log')
    facts = media.probe(folder / 'video.mp4', output=True)
    target_frames = expected_duration * 3 // 100
    if facts['video']['frames'] != target_frames or abs(facts['video']['end_us'] - expected_duration * 1000) > 1:
        raise ValueError('Crossfade output frame count or duration differs from the locked plan')
    if facts['audio'] is None or abs(facts['audio']['end_us'] - expected_duration * 1000) > 25000 or abs(facts['audio']['start_us']) > 1000:
        raise ValueError('Crossfade audio endpoint differs from the locked output clock')
    for clip in clips:
        if media.checksum(clip['path']) != clip['source']['sha256']: raise ValueError('Source changed during transition export')
    (folder / 'subtitles.srt').write_text(media.captions(payload['subtitles']), 'utf-8')
    (folder / 'subtitles.vtt').write_text(media.captions(payload['subtitles'], True), 'utf-8')
    portable = copy.deepcopy(payload)
    for clip in portable['clips']:
        clip.pop('path', None)
        for field in ('relative_path', 'stat'): clip['source'].pop(field, None)
    manifest = {'schema_version': 2, 'timeline_id': parameters['publication_id'], 'timeline_hash': parameters['publication_hash'],
        'portable_view_only': True, 'human_declarations_only': True, 'model_quality_qualified': False, 'timeline': portable,
        'files': {name: {'sha256': media.checksum(folder / name), 'size': (folder / name).stat().st_size} for name in ('video.mp4', 'subtitles.srt', 'subtitles.vtt')}}
    (folder / 'manifest.json').write_text(json.dumps(manifest, ensure_ascii=False, indent=2), 'utf-8')
    with zipfile.ZipFile(folder / 'export.zip', 'x', compression=zipfile.ZIP_STORED) as archive:
        for name in ('video.mp4', 'subtitles.srt', 'subtitles.vtt', 'manifest.json'): archive.write(folder / name, name)
    if sum(item.stat().st_size for item in folder.iterdir() if item.is_file()) > media.QUOTA: raise ValueError('Attempt quota exceeded')
    return {'facts': facts, 'files': {name: {'sha256': media.checksum(folder / name), 'size': (folder / name).stat().st_size} for name in ('video.mp4', 'subtitles.srt', 'subtitles.vtt', 'export.zip')}}


def main():
    """Accept only server-built bounded inputs, with the same killable encoder watchdog as v1."""
    try:
        parameters = json.loads(sys.stdin.buffer.read(2 * 1024 * 1024 + 1))
        if parameters.get('deadline') != 120: raise ValueError('Invalid v2 deadline')
        threading.Thread(target=media.watchdog, args=(120, parameters.get('folder')), daemon=True).start()
        print(json.dumps(assemble(parameters), ensure_ascii=False, allow_nan=False))
    except Exception as error:
        print(json.dumps({'error': 'Bounded transition-v2 renderer rejected input', 'error_type': type(error).__name__}))
        sys.exit(1)
    finally:
        media.STOP.set(); media.terminate()


if __name__ == '__main__':
    main()
