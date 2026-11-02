"""Decode bounded inert MP4 bytes without inheriting application settings or opening a caller path."""
import sys


def open_reference_video(content, pixel_limit=2073600):
    """Apply the decoder's pixel ceiling during format probing and decoding, including changing in-band video headers."""
    from io import BytesIO
    import av
    if type(pixel_limit) is not int or not 0 < pixel_limit <= 2073600:
        raise ValueError()
    options = {'protocol_whitelist': 'file,pipe', 'enable_drefs': '0', 'err_detect': 'explode',
               'max_pixels': str(pixel_limit), 'threads': '1'}
    container = av.open(BytesIO(content), format='mov', options=options)
    for stream in container.streams.video:
        stream.codec_context.thread_count = 1
        stream.codec_context.options = {**stream.codec_context.options, 'max_pixels': str(pixel_limit)}
    return container


def main():
    """Accept only short, constant-frame-rate H264/HEVC and bounded optional AAC/MP3 audio under a root watchdog."""
    import os
    import threading
    timer = threading.Timer(20, lambda: os._exit(124))
    timer.daemon = True
    timer.start()
    try:
        import hashlib
        import json
        from fractions import Fraction
        content = sys.stdin.buffer.read(32 * 1024 * 1024 + 1)
        if not content or len(content) > 32 * 1024 * 1024:
            raise ValueError()
        stamps = []
        with open_reference_video(content) as container:
            if (len(container.streams.video) != 1 or len(container.streams.audio) > 1
                    or len(container.streams) != len(container.streams.video)+len(container.streams.audio)):
                raise ValueError()
            video = container.streams.video[0]
            video.codec_context.thread_count = 1
            width, height = video.width, video.height
            if (video.codec_context.name not in {'h264', 'hevc'}
                    or not 300 <= width <= 1920 or not 300 <= height <= 1920
                    or not 407696 <= width * height <= 2073600 or not 0.4 <= width/height <= 2.5
                    or video.sample_aspect_ratio not in (None, Fraction(1))
                    or str(video.metadata.get('rotate', '0')) not in ('0', '0.0')):
                raise ValueError()
            for frame in container.decode(video):
                if (frame.pts is None or frame.time_base is None or frame.time_base <= 0
                        or frame.width != width or frame.height != height
                        or any('DISPLAYMATRIX' in str(data.type).upper() for data in frame.side_data)):
                    raise ValueError()
                stamp = Fraction(frame.pts)*frame.time_base
                if stamp < 0 or stamp > 10 or (stamps and stamp <= stamps[-1][0]):
                    raise ValueError()
                stamps.append((stamp, int(getattr(frame, 'duration', 0) or 0), frame.time_base))
                if len(stamps) > 600:
                    raise ValueError()
        if len(stamps) < 2 or stamps[0][0] != 0 or stamps[-1][1] <= 0:
            raise ValueError()
        rate = 1/(stamps[1][0]-stamps[0][0])
        if rate not in (24, 25, 30) or any(stamp != Fraction(index, rate) for index, (stamp, _, _) in enumerate(stamps)):
            raise ValueError()
        terminal = Fraction(stamps[-1][1])*stamps[-1][2]
        if terminal != 1/rate:
            raise ValueError()
        duration = stamps[-1][0]+terminal
        if not 2 <= duration <= 10:
            raise ValueError()
        audio_samples = 0
        audio_channels = 0
        with open_reference_video(content) as container:
            if container.streams.audio:
                audio = container.streams.audio[0]
                audio.codec_context.thread_count = 1
                if audio.codec_context.name not in {'aac', 'mp3'}:
                    raise ValueError()
                previous_end = None
                frames = 0
                for frame in container.decode(audio):
                    frames += 1
                    channels = len(frame.layout.channels)
                    if (not 8000 <= frame.sample_rate <= 48000 or channels not in (1, 2)
                            or not 0 < frame.samples <= 8192 or frame.pts is None or frame.time_base is None
                            or frame.time_base <= 0 or frames > 1000):
                        raise ValueError()
                    stamp = Fraction(frame.pts)*frame.time_base
                    end = stamp+Fraction(frame.samples, frame.sample_rate)
                    if stamp < 0 or end > duration+Fraction(1, 10) or (previous_end is not None and stamp < previous_end):
                        raise ValueError()
                    audio_samples += frame.samples
                    if audio_samples > 48000*11:
                        raise ValueError()
                    previous_end = end
                    audio_channels = max(audio_channels, channels)
                if not frames:
                    raise ValueError()
        clocks = [[stamp.numerator, stamp.denominator, span, base.numerator, base.denominator] for stamp, span, base in stamps]
        result = {'sha256': hashlib.sha256(content).hexdigest(), 'byte_size': len(content), 'mime': 'video/mp4',
            'width': width, 'height': height, 'duration_us': int(duration*1_000_000), 'frames': len(stamps),
            'fps': int(rate), 'frame_clock_sha256': hashlib.sha256(json.dumps(clocks, separators=(',', ':')).encode()).hexdigest(),
            'has_audio': audio_samples > 0, 'audio_samples': audio_samples, 'audio_channels': audio_channels}
        sys.stdout.write(json.dumps(result))
        sys.stdout.flush()
    except Exception:
        raise SystemExit(2) from None
    finally:
        timer.cancel()


if __name__ == '__main__':
    main()
