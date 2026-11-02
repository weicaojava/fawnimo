"""Decode inert image bytes only after the owned-process stdin gate opens."""
import sys


def main():
    """Bound parser lifetime, bytes, pixels and frames without opening any supplied path or network target."""
    import os
    import threading
    timer = threading.Timer(6, lambda: os._exit(124))
    timer.daemon = True
    timer.start()
    content = sys.stdin.buffer.read(8 * 1024 * 1024 + 1)
    try:
        import hashlib
        from io import BytesIO
        import json
        import warnings
        from PIL import Image
        if not content or len(content) > 8 * 1024 * 1024:
            raise ValueError()
        with warnings.catch_warnings():
            warnings.simplefilter('error', Image.DecompressionBombWarning)
            with Image.open(BytesIO(content)) as image:
                if image.format not in {'PNG', 'JPEG'} or getattr(image, 'n_frames', 1) != 1 or not 1 <= image.width <= 4096 or not 1 <= image.height <= 4096 or image.width * image.height > 16_000_000:
                    raise ValueError()
                image.load()
                sys.stdout.write(json.dumps({'sha256': hashlib.sha256(content).hexdigest(),
                    'byte_size': len(content), 'width': image.width, 'height': image.height,
                    'mime': 'image/png' if image.format == 'PNG' else 'image/jpeg'}))
                sys.stdout.flush()
    except Exception:
        raise SystemExit(2) from None
    finally:
        timer.cancel()


if __name__ == '__main__':
    main()
