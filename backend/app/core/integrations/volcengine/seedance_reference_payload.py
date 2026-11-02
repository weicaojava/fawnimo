"""Build a separate fixed reference wire profile from already decoded original-project bytes."""
import base64
import hashlib
import re
from urllib.parse import urlsplit

from app.core.contracts.seedance25 import MODEL_ID, SUPPORTED_DURATIONS
from app.core.contracts.seedance_reference_profile import checked_delivery_origin
from app.core.contracts.seedance_references import PreparedReference, VideoReferenceSelection

MAX_IMAGE_BYTES = 4 * 1024 * 1024
MAX_VIDEO_BYTES = 32 * 1024 * 1024
MAX_REQUEST_BYTES = 18 * 1024 * 1024


def _image_url(proof: PreparedReference, *, frames: bool) -> str:
    """Serialize exactly the verified bytes, with a deliberately narrower frame profile than the provider's maximum."""
    if (proof.mime not in {'image/png', 'image/jpeg'} or not isinstance(proof.content, bytes)
            or proof.delivery_url is not None or proof.duration_ms is not None
            or proof.fps is not None or proof.frame_clock_sha256 is not None or proof.has_audio is not None
            or type(proof.byte_size) is not int or not 0 < proof.byte_size <= MAX_IMAGE_BYTES
            or len(proof.content) != proof.byte_size or hashlib.sha256(proof.content).hexdigest() != proof.sha256
            or type(proof.width) is not int or type(proof.height) is not int
            or not 300 <= proof.width <= 4096 or not 300 <= proof.height <= 4096
            or proof.width * proof.height > 16_000_000 or not 0.4 <= proof.width / proof.height <= 2.5):
        raise ValueError('参考图片证明不符合本协议')
    if frames and proof.width * 16 != proof.height * 9:
        raise ValueError('本版首尾帧只接受实际9:16图片，不自动裁剪或拉伸')
    return 'data:'+proof.mime+';base64,'+base64.b64encode(proof.content).decode('ascii')


def _video_url(proof: PreparedReference, delivery_origin: str | None, trusted_local_origin: str | None) -> str:
    """Accept only an application-owned scoped delivery URL, never a user URL, asset handle or inline video."""
    if (proof.mime != 'video/mp4' or proof.content is not None or not isinstance(proof.delivery_url, str)
            or type(proof.byte_size) is not int or not 0 < proof.byte_size <= MAX_VIDEO_BYTES
            or type(proof.width) is not int or type(proof.height) is not int
            or not 300 <= proof.width <= 1920 or not 300 <= proof.height <= 1920
            or not 407696 <= proof.width * proof.height <= 2073600
            or not 0.4 <= proof.width / proof.height <= 2.5
            or type(proof.duration_ms) is not int or not 2000 <= proof.duration_ms <= 10000
            or type(proof.fps) is not int or proof.fps not in (24, 25, 30)
            or type(proof.has_audio) is not bool or not isinstance(proof.frame_clock_sha256, str)
            or re.fullmatch(r'[a-f0-9]{64}', proof.frame_clock_sha256) is None
            or not isinstance(delivery_origin, str)):
        raise ValueError('参考视频证明或独立素材交付配置缺失')
    checked_delivery_origin(delivery_origin, trusted_local_origin=trusted_local_origin)
    origin, target = urlsplit(delivery_origin), urlsplit(proof.delivery_url)
    if (target.scheme != origin.scheme or target.netloc != origin.netloc
            or target.username or target.password or target.fragment
            or re.fullmatch(r'/api/v1/execution-reference-media/[a-f0-9]{32}', target.path) is None
            or re.fullmatch(r'token=[a-f0-9]{64}', target.query) is None):
        raise ValueError('参考视频交付地址与原服务不一致')
    return proof.delivery_url


def build_seedance_reference_body(*, project_id: str, model_id: str, prompt: str, duration: int,
                                  selection: VideoReferenceSelection, prepared: tuple[PreparedReference, ...],
                                  delivery_origin: str | None = None, trusted_local_delivery_origin: str | None = None) -> dict:
    """Keep ordered explicit media in the outgoing body; validation has no database, file or network side effects."""
    if (not isinstance(project_id, str) or not project_id or model_id != MODEL_ID
            or type(duration) is not int or duration not in SUPPORTED_DURATIONS
            or not isinstance(prompt, str) or not prompt.strip() or len(prompt) > 32000
            or not isinstance(selection, VideoReferenceSelection) or not isinstance(prepared, tuple)
            or len(prepared) != len(selection.references)):
        raise ValueError('参考视频请求与固定协议不一致')
    try:
        prompt.encode('utf8')
    except UnicodeError:
        raise ValueError('参考视频提示词编码无效') from None
    content = [{'type': 'text', 'text': prompt}]
    frames = selection.mode != 'reference'
    total_images = 0
    for chosen, proof in zip(selection.references, prepared):
        if (not isinstance(proof, PreparedReference) or proof.project_id != project_id
                or proof.media_id != chosen.media_id or proof.sha256 != chosen.expected_sha256):
            raise ValueError('参考素材与原项目、顺序或SHA证明不一致')
        if chosen.kind == 'image':
            url = _image_url(proof, frames=frames)
            total_images += proof.byte_size
            content.append({'type': 'image_url', 'image_url': {'url': url}, 'role': chosen.role})
        else:
            content.append({'type': 'video_url', 'video_url': {'url': _video_url(proof, delivery_origin, trusted_local_delivery_origin)}, 'role': chosen.role})
    if total_images > 12 * 1024 * 1024:
        raise ValueError('参考图片总大小超过本协议上限')
    body = {'model': MODEL_ID, 'content': content, 'duration': duration,
        'ratio': 'adaptive' if frames else '9:16', 'resolution': '480p',
        'output_format': 'mp4', 'generate_audio': False, 'watermark': True}
    if not frames:
        body['omni_reference_task_type'] = 'reference'
    import json
    if len(json.dumps(body, ensure_ascii=False, separators=(',', ':'), allow_nan=False).encode('utf8')) > MAX_REQUEST_BYTES:
        raise ValueError('参考视频请求正文超过本协议上限')
    return body
