"""Private bounded decoder/assembler: accepts server paths, never database configuration."""
import hashlib
import html
import json
import math
import os
from pathlib import Path
import subprocess
import sys
import threading
import time
import zipfile
from fractions import Fraction

ACTIVE = None
STOP = threading.Event()
QUOTA = 768 * 1024 * 1024
LIMIT = 256 * 1024 * 1024


def digest(value):
    """Match the parent canonical clock hash without importing application settings."""
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=False, allow_nan=False).encode()).hexdigest()


def checksum(path):
    """Bound memory while hashing every immutable source and generated artifact."""
    hasher = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            hasher.update(block)
    return hasher.hexdigest()


def intervals_union(intervals):
    """Merge actual intervals, allowing only a one-microsecond rational rounding seam."""
    result = []
    for start, end in sorted(intervals):
        start = math.ceil(start * 1000000)
        end = math.floor(end * 1000000)
        if end <= start:
            raise ValueError('Invalid media interval')
        if result and start <= result[-1][1] + 1:
            result[-1][1] = max(result[-1][1], end)
        else:
            result.append([start, end])
    return result


def open_video(path):
    """Restrict untrusted MP4 demuxing to local bytes without external data references."""
    import av
    return av.open(str(path), format='mov', options={'protocol_whitelist': 'file,pipe', 'enable_drefs': '0', 'err_detect': 'explode'})


def probe(path, expected_hash=None, output=False):
    """Decode all frames and samples, never substitute container duration for actual clocks."""
    path = Path(path)
    if not path.is_file() or path.stat().st_size > (LIMIT if output else 64 * 1024 * 1024):
        raise ValueError('Media missing or over size limit')
    if expected_hash and checksum(path) != expected_hash:
        raise ValueError('Media hash mismatch')
    clocks = []
    with open_video(path) as container:
        if len(container.streams.video) != 1 or len(container.streams.audio) > 1 or len(container.streams) > 2:
            raise ValueError('Only one video and at most one audio track supported')
        stream = container.streams.video[0]
        stream.codec_context.thread_count = 1
        if stream.sample_aspect_ratio not in (None, Fraction(1, 1)) or str(stream.metadata.get('rotate', '0')) not in ('0', '0.0'):
            raise ValueError('Non-square sample aspect or rotation unsupported')
        width, height = stream.width, stream.height
        if min(width, height) <= 0 or max(width, height) > 1920 or min(width, height) > 1080 or width * height > 2073600:
            raise ValueError('Video dimensions exceed profile')
        for frame in container.decode(stream):
            if frame.pts is None or frame.time_base is None or frame.time_base <= 0 or frame.width != width or frame.height != height:
                raise ValueError('Invalid video clock or changing dimensions')
            if any('DISPLAYMATRIX' in str(data.type).upper() for data in frame.side_data):
                raise ValueError('Display rotation unsupported')
            stamp = Fraction(frame.pts) * frame.time_base
            if abs(stamp) > 86400 or (clocks and stamp <= clocks[-1][0]):
                raise ValueError('Video PTS must strictly increase')
            duration = int(getattr(frame, 'duration', 0) or 0)
            clocks.append((stamp, duration, int(frame.pts), frame.time_base.numerator, frame.time_base.denominator))
            if len(clocks) > 9000:
                raise ValueError('Video frame limit exceeded')
    if not clocks or clocks[-1][1] <= 0:
        raise ValueError('Actual terminal video duration required')
    origin = clocks[0][0]
    if output and (stream.codec_context.name != 'h264' or origin != 0 or any(stamp != Fraction(index, 30) for index, (stamp, *_rest) in enumerate(clocks))):
        raise ValueError('Output must decode on the exact H264 30fps zero-origin grid')
    intervals = []
    for i, (stamp, duration, _, num, den) in enumerate(clocks):
        following = clocks[i + 1][0] if i + 1 < len(clocks) else None
        end = stamp + Fraction(duration * num, den) if duration > 0 else following
        if following is not None:
            end = min(end, following)
        if end is None or end <= stamp:
            raise ValueError('Invalid video display interval')
        intervals.append((stamp - origin, end - origin))
    coverage = intervals_union(intervals)
    end_us = coverage[-1][1]
    if end_us > 300000000 or end_us <= 0:
        raise ValueError('Video exceeds 300 seconds')
    video = {'first_pts_us': round(origin * 1000000), 'first_pts_num': origin.numerator, 'first_pts_den': origin.denominator, 'duration_ms': end_us // 1000, 'end_us': end_us, 'frames': len(clocks), 'width': width, 'height': height, 'frame_clock_hash': digest([[pts, num, den, duration] for _, duration, pts, num, den in clocks]), 'coverage_us': coverage}
    audio = None
    with open_video(path) as container:
        if container.streams.audio:
            stream = container.streams.audio[0]
            stream.codec_context.thread_count = 1
            segments, audio_clocks = [], []
            rate = channels = None
            previous = None
            for frame in container.decode(stream):
                if frame.pts is None or frame.time_base is None or frame.time_base <= 0 or frame.samples <= 0:
                    raise ValueError('Invalid audio clock')
                stamp = Fraction(frame.pts) * frame.time_base
                if previous is not None and stamp <= previous:
                    raise ValueError('Audio PTS must strictly increase')
                previous = stamp
                if rate is None:
                    rate, channels = frame.sample_rate, len(frame.layout.channels)
                if not 8000 <= rate <= 192000 or not 1 <= channels <= 8 or frame.sample_rate != rate or len(frame.layout.channels) != channels:
                    raise ValueError('Unsupported changing audio format')
                start, end = stamp - origin, stamp - origin + Fraction(frame.samples, rate)
                if start < -310 or end > 310:
                    raise ValueError('Audio time exceeds limit')
                segments.append((start, end))
                audio_clocks.append([frame.pts, frame.time_base.numerator, frame.time_base.denominator, frame.samples, rate])
                if len(audio_clocks) > 20000:
                    raise ValueError('Audio frame limit exceeded')
            if not segments:
                raise ValueError('Declared audio stream has no decoded samples')
            coverage_audio = intervals_union(segments)
            audio = {'start_us': coverage_audio[0][0], 'end_us': coverage_audio[-1][1], 'start_ms': coverage_audio[0][0] // 1000, 'end_ms': coverage_audio[-1][1] // 1000, 'sample_rate': rate, 'channels': channels, 'frame_clock_hash': digest(audio_clocks), 'coverage_us': coverage_audio}
            if output and (stream.codec_context.name != 'aac' or rate != 48000 or channels != 2):
                raise ValueError('Output must decode as AAC48k stereo')
    return {'video': video, 'audio': audio}


def terminate():
    """Stop an active trusted encoder before exiting on the independent watchdog."""
    global ACTIVE
    process = ACTIVE
    if process is not None and process.poll() is None:
        process.kill()
        try:
            process.wait(timeout=3)
        except subprocess.TimeoutExpired:
            pass


def watchdog(deadline, folder=None):
    """Bound runtime and all attempt bytes even after the application parent disappears."""
    until = time.monotonic() + deadline
    while not STOP.wait(0.5):
        exceeded = time.monotonic() >= until
        if folder and not exceeded:
            try:
                exceeded = sum(p.stat().st_size for p in Path(folder).rglob('*') if p.is_file()) > QUOTA
            except OSError:
                exceeded = True
        if exceeded:
            terminate()
            os._exit(70)


def run_encoder(command, log):
    """Execute only internally built argument arrays; bound diagnostics on private disk."""
    global ACTIVE
    with Path(log).open('xb') as stream:
        ACTIVE = subprocess.Popen(command, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=stream, creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        result = ACTIVE.wait()
        ACTIVE = None
    if result:
        raise ValueError('Trusted encoder rejected the input')


def resized(frame):
    """Scale square pixels proportionally and pad the fixed portrait canvas."""
    import av
    from PIL import Image
    width, height = frame.width, frame.height
    factor = min(Fraction(360, width), Fraction(640, height))
    target = (max(1, int(width * factor)), max(1, int(height * factor)))
    image = frame.to_image().resize(target, Image.Resampling.BILINEAR)
    canvas = Image.new('RGB', (360, 640))
    canvas.paste(image, ((360 - target[0]) // 2, (640 - target[1]) // 2))
    return av.VideoFrame.from_image(canvas)


def write_picture(output, encoder, picture, index):
    """Assign only the deterministic output clock; repeated source frames remain explicit."""
    picture.pts = index
    picture.time_base = Fraction(1, 30)
    for packet in encoder.encode(picture):
        output.mux(packet)


def encode_video(clips, target):
    """Sample each clip at in+k/30 from actual source display intervals, without tail fill."""
    import av
    output_index = 0
    with av.open(str(target), 'w', format='mp4') as output:
        encoder = output.add_stream('libx264', rate=30)
        encoder.width, encoder.height, encoder.pix_fmt = 360, 640, 'yuv420p'
        encoder.codec_context.thread_count = 1
        encoder.codec_context.time_base = Fraction(1, 30)
        encoder.options = {'preset': 'veryfast', 'crf': '23', 'threads': '1'}
        for clip in clips:
            source = clip['source']
            origin = Fraction(source['video']['first_pts_num'], source['video']['first_pts_den'])
            count = (clip['source_out_ms'] - clip['source_in_ms']) * 3 // 100
            selected = 0
            previous = None
            previous_stamp = None
            previous_duration = 0
            cache = None
            with open_video(clip['path']) as container:
                stream = container.streams.video[0]
                stream.codec_context.thread_count = 1
                for frame in container.decode(stream):
                    stamp = Fraction(frame.pts) * frame.time_base - origin
                    if previous is not None:
                        end = previous_stamp + Fraction(previous_duration) * previous.time_base if previous_duration > 0 else stamp
                        end = min(end, stamp)
                        while selected < count:
                            timepoint = Fraction(clip['source_in_ms'], 1000) + Fraction(selected, 30)
                            if timepoint >= stamp:
                                break
                            if timepoint < previous_stamp or timepoint >= end:
                                raise ValueError('Sampling outside actual source interval')
                            cache = cache or resized(previous)
                            write_picture(output, encoder, cache, output_index)
                            selected += 1
                            output_index += 1
                    if selected == count:
                        break
                    previous, previous_stamp = frame, stamp
                    previous_duration = int(getattr(frame, 'duration', 0) or 0)
                    cache = None
                while selected < count:
                    timepoint = Fraction(clip['source_in_ms'], 1000) + Fraction(selected, 30)
                    if previous is None or previous_duration <= 0 or timepoint < previous_stamp or timepoint >= previous_stamp + Fraction(previous_duration) * previous.time_base:
                        raise ValueError('Short source, no terminal frame padding allowed')
                    cache = cache or resized(previous)
                    write_picture(output, encoder, cache, output_index)
                    selected += 1
                    output_index += 1
        for packet in encoder.encode():
            output.mux(packet)
    return output_index


def timestamp(milliseconds, vtt=False):
    """Serialize integer chapter milliseconds without another rounding step."""
    hours, rest = divmod(milliseconds, 3600000)
    minutes, rest = divmod(rest, 60000)
    seconds, millis = divmod(rest, 1000)
    return f'{hours:02d}:{minutes:02d}:{seconds:02d}{"." if vtt else ","}{millis:03d}'


def captions(subtitles, vtt=False):
    """Produce inert UTF8 cues; original exact text and speaker remain in the manifest."""
    result = ['WEBVTT\n'] if vtt else []
    for index, cue in enumerate(sorted(subtitles, key=lambda item: (item['start_ms'], item['end_ms'], item['dialogue_id'])), 1):
        text = ''.join(' ' if ord(char) < 32 or ord(char) == 127 else char for char in cue['text'])
        text = html.escape(text, quote=False)
        result.append(f'{index}\n{timestamp(cue["start_ms"], vtt)} --> {timestamp(cue["end_ms"], vtt)}\n{text}\n')
    return '\n'.join(result) + '\n'


def assemble(parameters):
    """Verify every source, encode every clip, mux aligned audio, and fully validate output."""
    folder = Path(parameters['folder']).resolve()
    ffmpeg = Path(parameters['ffmpeg']).resolve()
    payload = parameters['payload']
    if not ffmpeg.is_file() or not folder.is_dir() or any(folder.iterdir()):
        raise ValueError('Invalid private editing attempt')
    clips = []
    checked = {}
    for clip in payload['clips']:
        source = clip['source']
        path = Path(clip['path']).resolve()
        actual = checked.get(source['id'])
        if actual is None:
            actual = probe(path, source['sha256'])
            checked[source['id']] = actual
        if actual['video'] != source['video'] or actual['audio'] != source['audio']:
            raise ValueError('Source clocks differ from frozen timeline')
        if not any(start <= clip['source_in_ms'] * 1000 and end >= clip['source_out_ms'] * 1000 for start, end in actual['video']['coverage_us']):
            raise ValueError('Source crop outside actual coverage')
        clips.append(clip)
    if not clips or len(clips) > 100 or payload['profile'] != 'portrait-360p30-v1':
        raise ValueError('Invalid frozen timeline')
    target_frames = payload['duration_ms'] * 3 // 100
    if encode_video(clips, folder / 'pictures.mp4') != target_frames:
        raise ValueError('Not all planned frames encoded')
    command = [str(ffmpeg), '-nostdin', '-v', 'error', '-y', '-threads', '1', '-copyts', '-protocol_whitelist', 'file,pipe', '-f', 'mov', '-i', str(folder / 'pictures.mp4')]
    filters, labels = [], []
    audio_input = 1
    for index, clip in enumerate(clips):
        samples = (clip['source_out_ms'] - clip['source_in_ms']) * 48
        label = f'a{index}'
        if clip['source_audio'] and clip['source']['audio'] is not None:
            command.extend(['-protocol_whitelist', 'file,pipe', '-enable_drefs', '0', '-f', 'mov', '-i', str(clip['path'])])
            video = clip['source']['video']
            expression = f'PTS-({video["first_pts_num"]}/{video["first_pts_den"]})/TB'
            filters.append(f'[{audio_input}:a:0]asetpts={expression},aresample=48000:async=1:first_pts=0,aformat=sample_fmts=fltp:channel_layouts=stereo,atrim=start_sample={clip["source_in_ms"] * 48}:end_sample={clip["source_out_ms"] * 48},asetpts=N/SR/TB,apad=whole_len={samples},atrim=end_sample={samples}[{label}]')
            audio_input += 1
        else:
            filters.append(f'anullsrc=r=48000:cl=stereo,atrim=end_sample={samples},asetpts=N/SR/TB[{label}]')
        labels.append(f'[{label}]')
    filters.append(''.join(labels) + f'concat=n={len(labels)}:v=0:a=1[audio]')
    command.extend(['-filter_complex_threads', '1', '-filter_complex', ';'.join(filters), '-map', '0:v:0', '-map', '[audio]', '-c:v', 'copy', '-c:a', 'aac', '-b:a', '128k', '-ar', '48000', '-ac', '2', '-movflags', '+faststart', '-fs', str(LIMIT), str(folder / 'video.mp4')])
    run_encoder(command, folder / 'encoder.log')
    final = folder / 'video.mp4'
    facts = probe(final, output=True)
    if facts['video']['frames'] != target_frames or facts['video']['width'] != 360 or facts['video']['height'] != 640 or abs(facts['video']['end_us'] - payload['duration_ms'] * 1000) > 33334:
        raise ValueError('Output frame count/duration invalid')
    if facts['audio'] is None or abs(facts['audio']['end_us'] - facts['video']['end_us']) > 100000 or abs(facts['audio']['start_us']) > 100000:
        raise ValueError('Output audio endpoint invalid')
    # Rehash after all reads/encoding, so a changed input cannot gain a receipt.
    for clip in clips:
        if checksum(clip['path']) != clip['source']['sha256']:
            raise ValueError('Input changed during export')
    (folder / 'subtitles.srt').write_text(captions(payload['subtitles']), 'utf-8')
    (folder / 'subtitles.vtt').write_text(captions(payload['subtitles'], True), 'utf-8')
    portable = json.loads(json.dumps(payload))
    for clip in portable['clips']:
        clip.pop('path', None)
        clip['source'].pop('relative_path', None)
        clip['source'].pop('stat', None)
    manifest = {'schema_version': 1, 'timeline_id': parameters['publication_id'], 'timeline_hash': parameters['publication_hash'], 'portable_view_only': True, 'human_declarations_only': True, 'model_quality_qualified': False, 'timeline': portable, 'files': {name: {'sha256': checksum(folder / name), 'size': (folder / name).stat().st_size} for name in ('video.mp4', 'subtitles.srt', 'subtitles.vtt')}}
    (folder / 'manifest.json').write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + '\n', 'utf-8')
    with zipfile.ZipFile(folder / 'export.zip', 'x', compression=zipfile.ZIP_STORED) as archive:
        for name in ('video.mp4', 'subtitles.srt', 'subtitles.vtt', 'manifest.json'):
            archive.write(folder / name, name)
    if sum(path.stat().st_size for path in folder.iterdir() if path.is_file()) > QUOTA:
        raise ValueError('Attempt quota exceeded')
    return {'facts': facts, 'files': {name: {'sha256': checksum(folder / name), 'size': (folder / name).stat().st_size} for name in ('video.mp4', 'subtitles.srt', 'subtitles.vtt', 'export.zip')}}


def main():
    """Read bounded trusted protocol JSON and return generic non-path diagnostics."""
    try:
        parameters = json.loads(sys.stdin.buffer.read(2 * 1024 * 1024 + 1))
        deadline = parameters['deadline']
        if deadline not in (45, 120):
            raise ValueError('Invalid deadline')
        threading.Thread(target=watchdog, args=(deadline, parameters.get('folder')), daemon=True).start()
        result = probe(parameters['path'], parameters['expected_hash']) if parameters['mode'] == 'probe' else assemble(parameters)
        print(json.dumps(result, ensure_ascii=False, allow_nan=False))
    except Exception as exc:
        print(json.dumps({'error': 'Bounded local media processing rejected input', 'error_type': type(exc).__name__}))
        sys.exit(1)
    finally:
        STOP.set()
        terminate()


if __name__ == '__main__':
    main()
