"""Database-free WAV probe; the parent owns and joins this bounded process."""
import json
import sys
from sound_wave import inspect_wave


def main():
    """Only decode a server-selected path; input and output are finite JSON records."""
    try:
        raw = sys.stdin.buffer.read(16385)
        if len(raw) > 16384: raise ValueError('Probe request exceeds bound')
        value = json.loads(raw)
        print(json.dumps(inspect_wave(value['path'], value['expected_hash'], value.get('intervals', [])), allow_nan=False))
    except Exception:
        print(json.dumps({'error': 'Actual PCM-WAV inspection failed'}))
        sys.exit(1)


if __name__ == '__main__':
    main()
