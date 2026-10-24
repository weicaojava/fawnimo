"""Real isolated PyAV/FFmpeg fixture checks; these clips are not model generations."""
import hashlib
import os
from pathlib import Path
import subprocess
import pytest
from app.services.reverse.video_probe import probe


def executable():
    """Use only an explicitly trusted test binary or the workspace tool."""
    path=Path(os.environ.get('TEST_MEDIA_PROBE',str(Path(__file__).resolve().parents[4]/'ffmpeg.exe')))
    assert path.is_file()
    return path


def fixture(path, *, audio=False, offset=False, vfr=False, duration=1):
    """Create bounded real clips with controlled presentation clocks and track layout."""
    args=[str(executable()),'-nostdin','-v','error','-f','lavfi','-i',f'testsrc2=size=64x48:rate=30:duration={duration}']
    if audio:args+=['-f','lavfi','-i',f'sine=frequency=440:duration={duration}']
    if vfr:args+=['-vf',"select='not(mod(n,3)) + eq(mod(n,3),1)'",'-fps_mode','vfr']
    args+=['-c:v','libx264','-threads','1','-pix_fmt','yuv420p']
    if audio:args+=['-c:a','aac']
    if offset:args+=['-output_ts_offset','3']
    subprocess.run([*args,'-y',str(path)],check=True,timeout=30)


@pytest.mark.asyncio
@pytest.mark.parametrize('audio,offset,vfr',[(True,False,False),(False,False,False),(False,True,False),(False,False,True)])
async def test_original_pts_and_derivatives(tmp_path,audio,offset,vfr):
    """Preserve integer clocks and VFR gaps while creating mapped review derivatives."""
    source=tmp_path/'original.mp4';fixture(source,audio=audio,offset=offset,vfr=vfr)
    before=hashlib.sha256(source.read_bytes()).hexdigest()
    result=await probe(source,tmp_path/'output',trusted_ffmpeg=executable())
    frames=result['video']['frames']
    assert frames[0]['relative_us']==0 and len(frames)>1
    assert result['source']['sha256']==before==hashlib.sha256(source.read_bytes()).hexdigest()
    assert result['audio']['present']==audio
    assert (tmp_path/'output'/'proxy.mp4').is_file()
    assert result['proxy']['frame_mapping'] and result['keyframes']
    if audio:
        assert (tmp_path/'output'/'audio.wav').is_file()
        assert isinstance(result['audio']['tracks'][0]['first_decoded_pts'],int)
        assert isinstance(result['audio']['tracks'][0]['origin_relative_us'],int)
    if offset:assert frames[0]['pts']>0
    if vfr:
        gaps={frames[i]['relative_us']-frames[i-1]['relative_us'] for i in range(1,len(frames))}
        assert max(gaps)>min(gaps)*1.5
    for frame in frames:
        from fractions import Fraction
        first=frames[0]
        assert frame['relative_us']==round((Fraction(frame['pts']*frame['time_base_num'],frame['time_base_den'])-Fraction(first['pts']*first['time_base_num'],first['time_base_den']))*1000000)


@pytest.mark.asyncio
async def test_corrupt_and_long_explicitly_rejected(tmp_path):
    """Invalid input and declared timelines over 120 seconds fail rather than truncate."""
    corrupt=tmp_path/'corrupt.mp4';corrupt.write_bytes(b'not a video')
    with pytest.raises(ValueError):await probe(corrupt,tmp_path/'bad',trusted_ffmpeg=executable())
    long=tmp_path/'long.mp4';fixture(long,duration=121)
    with pytest.raises(ValueError,match='120'):await probe(long,tmp_path/'long-output',trusted_ffmpeg=executable())


@pytest.mark.asyncio
async def test_equal_luma_color_cut_and_bidirectional_proxy_mapping(tmp_path):
    """Equal-luma red/green hard cuts remain detectable and proxy pixels match mappings."""
    import av
    import numpy as np
    source=tmp_path/'colors.mp4'
    subprocess.run([str(executable()),'-nostdin','-v','error','-f','lavfi','-i','color=red:s=64x48:r=30:d=0.5','-f','lavfi','-i','color=green:s=64x48:r=30:d=0.5','-filter_complex','[0:v][1:v]concat=n=2:v=1:a=0[v]','-map','[v]','-c:v','libx264','-threads','1','-y',str(source)],check=True,timeout=30)
    result=await probe(source,tmp_path/'colors-output',trusted_ffmpeg=executable())
    assert len(result['cuts'])==1 and abs(result['cuts'][0]['relative_us']-500000)<1000
    with av.open(str(source)) as container:
        original=[frame.to_ndarray(format='rgb24').mean(axis=(0,1)) for frame in container.decode(video=0)]
    with av.open(str(tmp_path/'colors-output'/'proxy.mp4')) as container:
        derived=[frame.to_ndarray(format='rgb24').mean(axis=(0,1)) for frame in container.decode(video=0)]
    seen=set()
    for entry,pixels in zip(result['proxy']['frame_mapping'],derived):
        assert np.abs(pixels-original[entry['source_index']]).mean()/255<0.05
        seen.add(entry['source_index'])
    assert seen==set(range(len(original)))


def test_v2_motion_gradual_change_and_transient_occlusion():
    """Fixed generic v2 rules suppress continuous motion, fades and one-frame occlusion."""
    import numpy as np
    from app.services.reverse.video_probe_child import adaptive_candidates,discrete_candidates
    frames=[{'relative_us':i*33333} for i in range(60)]
    random=np.random.default_rng(7)
    texture=random.integers(0,256,size=(36,64,3),dtype=np.int16)
    motion=[np.roll(texture,i,axis=1) for i in range(60)]
    gradual=[np.full((36,64,3),i*4,dtype=np.int16) for i in range(60)]
    occlusion=[texture.copy() for _ in range(60)];occlusion[30]=np.zeros_like(texture)
    assert adaptive_candidates(motion,frames)==[]
    assert adaptive_candidates(gradual,frames)==[]
    assert adaptive_candidates(occlusion,frames)==[]
    assert discrete_candidates(motion,frames)==[]
    assert discrete_candidates(gradual,frames)==[]
    assert discrete_candidates(occlusion,frames)==[]
    assert discrete_candidates([texture.copy() for _ in frames],frames)==[]


@pytest.mark.asyncio
@pytest.mark.parametrize('local_occlusion',[True,False])
async def test_v4_local_occlusion_and_100ms_full_frame_edits(tmp_path,local_occlusion):
    """Independent encoded clips reject local masks but keep true edits 100ms apart."""
    import av
    import numpy as np
    from fractions import Fraction
    source=tmp_path/'spatial.mp4'
    arrays=[]
    if local_occlusion:
        background=np.full((48,64,3),80,dtype=np.uint8)
        for i in range(30):
            image=background.copy()
            if 10<=i<20:image[12:36,20:44]=[245,30,200]
            arrays.append(image)
        expected=[]
    else:
        colors=[[220,20,20],[20,220,20],[20,20,220],[220,220,20],[20,220,220]]
        arrays=[np.full((48,64,3),color,dtype=np.uint8) for color in colors for _ in range(3)]
        expected=[3,6,9,12]
    with av.open(str(source),'w') as container:
        stream=container.add_stream('libx264',rate=30);stream.width=64;stream.height=48;stream.pix_fmt='yuv420p';stream.codec_context.thread_count=1
        for i,image in enumerate(arrays):
            frame=av.VideoFrame.from_ndarray(image,format='rgb24');frame.pts=i;frame.time_base=Fraction(1,30)
            for packet in stream.encode(frame):container.mux(packet)
        for packet in stream.encode():container.mux(packet)
    result=await probe(source,tmp_path/'spatial-output',trusted_ffmpeg=executable())
    assert [cut['frame_index'] for cut in result['cuts']]==expected
    if local_occlusion:assert result['v3_cuts']
    assert result['candidate_algorithm']=='spatial_coverage_discrete_rgb_v4'
