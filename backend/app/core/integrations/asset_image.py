"""Bounded OpenAI-compatible n=1 base64 image adapter; URL-only and reference inputs are unsupported."""
import base64
from dataclasses import dataclass,replace
import hashlib
from io import BytesIO
import json
import re
from urllib.parse import urlsplit
import warnings
from PIL import Image
from app.core.integrations.trusted_http import request_isolated_async


class AssetImageModelError(RuntimeError):
    """A sanitized request or decode failure, never an upstream body or credential value."""

    def __init__(self,message,*,provider_request_id=None):
        """Attach optional private response evidence without exposing it in the displayed failure."""
        super().__init__(message)
        self.provider_request_id=provider_request_id


@dataclass(frozen=True)
class DecodedAssetImage:
    """Actual decoded image bytes plus integrity metadata, rather than provider-declared dimensions."""
    content: bytes
    width: int
    height: int
    mime: str
    sha256: str
    provider_request_id: str | None = None


def decode_image(content: bytes) -> DecodedAssetImage:
    """Fully decode a bounded single PNG/JPEG frame and reject bombs or mislabeled bytes."""
    if not content or len(content) > 8 * 1024 * 1024:
        raise AssetImageModelError('图片字节为空或超过 8 MiB 限制')
    try:
        with warnings.catch_warnings():
            warnings.simplefilter('error', Image.DecompressionBombWarning)
            with Image.open(BytesIO(content)) as image:
                if image.format not in {'PNG', 'JPEG'} or getattr(image, 'n_frames', 1) != 1 or not 1 <= image.width <= 4096 or not 1 <= image.height <= 4096 or image.width * image.height > 16_000_000:
                    raise ValueError()
                image.load()
                return DecodedAssetImage(content, image.width, image.height, 'image/png' if image.format == 'PNG' else 'image/jpeg', hashlib.sha256(content).hexdigest())
    except (Exception, Image.DecompressionBombError):
        raise AssetImageModelError('返回内容不是有效的单帧 PNG/JPEG 图片，未创建候选') from None


async def invoke_asset_image(*, base_url: str, api_key: str, model: str, prompt: str, size: str, trusted_local_host=None, trusted_local_ports=frozenset()) -> DecodedAssetImage:
    """Send one real POST with fixed safe protocol fields; never fetch an upstream image URL."""
    parsed = urlsplit(base_url)
    if not parsed.hostname or parsed.query or parsed.fragment or parsed.username or parsed.password:
        raise AssetImageModelError('图片模型地址无效，未发起请求')
    payload = {'model': model, 'prompt': prompt, 'size': size, 'n': 1, 'response_format': 'b64_json'}
    try:
        response = await request_isolated_async(base_url.rstrip('/') + '/images/generations', allowed_hosts={parsed.hostname.lower()}, trusted_local_host=trusted_local_host, trusted_local_ports=set(trusted_local_ports), method='POST', data=json.dumps(payload, ensure_ascii=False).encode(), headers={'Authorization': f'Bearer {api_key}', 'Content-Type': 'application/json', 'Accept': 'application/json'}, timeout=60, max_bytes=12 * 1024 * 1024, max_redirects=0)
    except Exception:
        raise AssetImageModelError('图片请求失败或结果未知；不会自动重发，重试可能重复计费') from None
    request_id=response.headers.get('x-request-id')
    if not isinstance(request_id,str) or not re.fullmatch(r'[A-Za-z0-9_.:-]{1,128}',request_id) or request_id==api_key:
        request_id=None
    if not 200 <= response.status < 300:
        raise AssetImageModelError(f'图片模型返回 HTTP {response.status}；未创建候选，重试可能重复计费',provider_request_id=request_id)
    try:
        data = json.loads(response.body)
        items = data['data']
        if not isinstance(items, list) or len(items) != 1 or not isinstance(items[0], dict) or 'url' in items[0] or not isinstance(items[0].get('b64_json'), str) or len(items[0]['b64_json']) > 12 * 1024 * 1024:
            raise ValueError()
        content = base64.b64decode(items[0]['b64_json'], validate=True)
    except (ValueError, TypeError, KeyError, IndexError):
        raise AssetImageModelError('仅接受单张 b64_json 图片响应；URL-only/多图/无效 base64 不受支持，未下载外部图片',provider_request_id=request_id) from None
    try:
        return replace(decode_image(content),provider_request_id=request_id)
    except AssetImageModelError as failure:
        raise AssetImageModelError(str(failure),provider_request_id=request_id) from None
