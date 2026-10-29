"""Inert understanding endpoint policy; execution credentials come only from managed connection snapshots."""
import ipaddress
from urllib.parse import urlsplit
from app.services.video_understanding.errors import UnderstandingFailure


def endpoint_base(value: str, *, allow_remote: bool) -> str:
    """Allow explicit loopback services by default; remote transmission needs server-side opt-in."""
    try:
        parsed = urlsplit(value.strip())
        if parsed.scheme not in {'http', 'https'} or not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment:
            raise ValueError('Invalid API address')
        if any(part in {'.', '..'} for part in parsed.path.split('/')):
            raise ValueError('Invalid API path')
        host = parsed.hostname.lower()
        local = host in {'localhost', '127.0.0.1', '::1'}
        try:
            address = ipaddress.ip_address(host)
            if not local and not address.is_global:
                raise ValueError('Non-global remote address')
        except ValueError as error:
            if host.replace('.', '').isdigit() or ':' in host:
                raise error
        if not local and (not allow_remote or parsed.scheme != 'https'):
            raise UnderstandingFailure('remote_disabled', '当前运行器只允许本机模型服务；远程服务需要先在服务器运行配置中单独启用，尚未发送素材')
        if parsed.port is not None and not 1 <= parsed.port <= 65535:
            raise ValueError('Invalid API port')
        return value.strip().rstrip('/')
    except UnderstandingFailure:
        raise
    except (ValueError, TypeError):
        raise UnderstandingFailure('endpoint_invalid', '请填写有效模型服务地址，例如 http://127.0.0.1:端口/v1；不要在地址中填写密钥、查询参数或密码') from None



