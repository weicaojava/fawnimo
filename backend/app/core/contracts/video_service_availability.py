"""Known official video-service availability, separate from retained compatibility drivers and private provider credentials."""
from urllib.parse import urlsplit
import httpx

OPENAI_VIDEO_SHUTDOWN='2026-09-24'
OPENAI_VIDEO_DEPRECATION_SOURCE='https://developers.openai.com/api/docs/deprecations'
OPENAI_VIDEO_UNAVAILABLE_MESSAGE='OpenAI 官方视频接口已于 2026-09-24 停止服务，请选择其他已支持的视频供应商。'


def require_available_openai_video_origin(base_url):
    """Refuse the retired official Videos API before credentials/client access; explicit other origins retain their existing compatibility contract."""
    value=(base_url or 'https://api.openai.com/v1').strip()
    try:
        # Preserve strict malformed-netloc rejection, then match the actual
        # transport's IDNA normalization, including Unicode dot separators.
        urlsplit(value)
        host=httpx.URL(value).host
    except (ValueError,TypeError,UnicodeError,httpx.InvalidURL):
        raise ValueError('视频服务地址无效，请检查供应商配置。') from None
    if not value or host is not None and host.rstrip('.').casefold()=='api.openai.com':
        raise ValueError(OPENAI_VIDEO_UNAVAILABLE_MESSAGE)
