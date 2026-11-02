"""Join a bounded credential-free PNG/JPEG decoder before accepting image input or output."""
import hashlib
import json
from pathlib import Path
import sys

from app.core.media_environment import media_environment
from app.core.owned_process import run_owned_process
from app.core.integrations.asset_image import AssetImageModelError, DecodedAssetImage


async def verify_image_bytes(content: bytes, *, reference=False) -> DecodedAssetImage:
    """Return proof for these exact in-memory bytes; the decoder never receives a filesystem or URL reference."""
    limit = (4 if reference else 8) * 1024 * 1024
    if not isinstance(content, bytes) or not content or len(content) > limit:
        raise AssetImageModelError('图片字节为空或超过本次协议的大小上限')
    try:
        output, code = await run_owned_process(
            [sys.executable, str(Path(__file__).with_name('verified_image_child.py'))], content,
            timeout=5, max_stdout_bytes=2048, environment=media_environment())
        proof = json.loads(output)
        if code != 0 or set(proof) != {'sha256', 'byte_size', 'width', 'height', 'mime'} or proof['sha256'] != hashlib.sha256(content).hexdigest() or proof['byte_size'] != len(content):
            raise ValueError()
        width, height = proof['width'], proof['height']
        if type(width) is not int or type(height) is not int or not 1 <= width <= 4096 or not 1 <= height <= 4096 or width * height > 16_000_000 or proof['mime'] not in {'image/png', 'image/jpeg'}:
            raise ValueError()
        if reference and (min(width, height) < 15 or not 1/16 <= width/height <= 16):
            raise ValueError()
        return DecodedAssetImage(content, width, height, proof['mime'], proof['sha256'])
    except Exception:
        raise AssetImageModelError('图片无法在限定资源内完整解码为本协议支持的单帧 PNG/JPEG') from None
