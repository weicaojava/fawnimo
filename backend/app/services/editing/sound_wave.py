"""Bounded real PCM-WAV inspection shared by the sound service and its renderer child."""
import array
import hashlib
import math
import os
from pathlib import Path
import struct
import sys

LIMIT = 32 * 1024 * 1024


def inspect_wave(path, expected_hash, intervals=(), limit=LIMIT):
    """Read one exact bounded descriptor and decode every signed PCM16 sample.

    RIFF sizes, chunk alignment, format, sample count and the actual file identity
    must agree. The supported profile has no codec, external references or PTS
    synthesis. Interval peaks describe signal existence, never speech correctness.
    """
    target = Path(path)
    before = target.stat()
    if limit not in (LIMIT, 64 * 1024 * 1024) or not 44 <= before.st_size <= limit:
        raise ValueError('WAV exceeds admitted size')
    with target.open('rb') as stream:
        opened = os.fstat(stream.fileno())
        raw = stream.read(limit + 1)
        after_open = os.fstat(stream.fileno())
    after_path = target.stat()
    # Windows path stat and descriptor fstat expose different ctime meanings.
    # Compare common fields across APIs; compare ctime only within the same API.
    def identity(item):
        """Use fields with the same meaning for both path and descriptor witnesses."""
        return (item.st_dev, item.st_ino, item.st_size, item.st_mtime_ns)
    if identity(before) != identity(opened) or identity(opened) != identity(after_open) or identity(after_open) != identity(after_path) or before.st_ctime_ns != after_path.st_ctime_ns or opened.st_ctime_ns != after_open.st_ctime_ns:
        raise ValueError('WAV changed while reading')
    if len(raw) != before.st_size or hashlib.sha256(raw).hexdigest() != expected_hash:
        raise ValueError('WAV hash or length changed')
    if raw[:4] != b'RIFF' or raw[8:12] != b'WAVE' or struct.unpack_from('<I', raw, 4)[0] != len(raw) - 8:
        raise ValueError('Invalid exact RIFF-WAVE size')
    position, fmt, samples, metadata = 12, None, None, 0
    while position < len(raw):
        if position + 8 > len(raw): raise ValueError('Truncated WAV chunk')
        kind, size = struct.unpack_from('<4sI', raw, position)
        start, end = position + 8, position + 8 + size
        if end + (size & 1) > len(raw): raise ValueError('WAV chunk exceeds actual bytes')
        value = raw[start:end]
        if kind == b'fmt ':
            if fmt is not None or size not in (16, 18) or size == 18 and value[16:] != b'\0\0': raise ValueError('Unsupported WAV format chunk')
            fmt = struct.unpack_from('<HHIIHH', value)
        elif kind == b'data':
            if samples is not None or fmt is None or not size: raise ValueError('Duplicate, empty or unordered WAV data')
            samples = value
        elif kind in (b'LIST', b'JUNK', b'fact'):
            metadata += size
            if metadata > 65536: raise ValueError('Excessive WAV metadata')
        else:
            raise ValueError('Unsupported WAV chunk')
        position = end + (size & 1)
    if fmt is None or samples is None: raise ValueError('Incomplete WAV')
    codec, channels, rate, byte_rate, alignment, bits = fmt
    if codec != 1 or channels not in (1, 2) or rate not in (24000, 48000) or bits != 16 or alignment != channels * 2 or byte_rate != rate * alignment or len(samples) % alignment:
        raise ValueError('WAV requires PCM16, 24/48kHz and one or two channels')
    frames = len(samples) // alignment
    if not rate // 100 <= frames <= rate * 300: raise ValueError('WAV duration outside 10ms..300s')
    pcm = array.array('h'); pcm.frombytes(samples)
    if sys.byteorder != 'little': pcm.byteswap()
    peak = max(abs(value) for value in pcm)
    interval_peaks = []
    if len(intervals) > 128: raise ValueError('Too many sound intervals')
    for begin, end in intervals:
        if type(begin) is not int or type(end) is not int or begin < 0 or end <= begin or end * rate > frames * 1000:
            raise ValueError('Sound interval outside actual sample coverage')
        interval_peaks.append(max(abs(value) for value in pcm[begin * rate * channels // 1000:end * rate * channels // 1000]))
    step = max(1, (len(pcm) + 255) // 256)
    envelope = [max(abs(value) for value in pcm[index:index + step]) for index in range(0, len(pcm), step)]
    return {'sample_rate': rate, 'channels': channels, 'sample_frames': frames, 'duration_us': frames * 1000000 // rate,
        'coverage_us': [[0, frames * 1000000 // rate]], 'peak_pcm16': peak,
        'rms_pcm16': int(math.isqrt(sum(value * value for value in pcm) // len(pcm))),
        'envelope_pcm16': envelope, 'interval_peaks_pcm16': interval_peaks}
