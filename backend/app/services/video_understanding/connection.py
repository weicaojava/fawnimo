"""Dedicated, opt-in model setup without consulting legacy provider credentials."""
import ipaddress
import os
from urllib.parse import urlsplit
from app.core.contracts.video_understanding import UnderstandingConnection
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


def credential(connection: dict, environment=None, *, audio=False) -> str:
    """Read only an explicitly selected new DNA_UNDERSTANDING_* reference, never existing model keys."""
    checked = UnderstandingConnection.model_validate(connection)
    reference = (checked.audio_credential_env or checked.credential_env) if audio else checked.credential_env
    if not reference:
        return ''
    mapping = os.environ if environment is None else environment
    value = mapping.get(reference, '')
    if not isinstance(value, str) or not value.strip():
        raise UnderstandingFailure('credential_missing', '该理解服务的专用授权尚未填写；请在本地启动配置中新增表单所示授权项，重启后刷新状态')
    return value.strip()


def setup_state(project_id: str, revision: int, connection: dict, worker) -> dict:
    """Explain missing setup in the UI without probing a provider or submitting any media."""
    value = UnderstandingConnection.model_validate(connection).model_dump()
    configured = bool(value['base_url'].strip() and value['vision_model'].strip() and (not value['transcribe_audio'] or value['audio_model'].strip()))
    enabled = bool(worker is not None and worker.enabled)
    remote = bool(worker is not None and worker.allow_remote)
    present, available, steps = not bool(value['credential_env']), False, []
    message = '尚未配置视频理解服务，可以继续手动拆解'
    if not configured:
        steps.append('填写本机或兼容服务的API地址和视觉模型名称。需要对白候选时，再填写支持逐段时间的语音转写模型。')
    if not value['enabled']:
        steps.append('保存信息后打开“启用此连接”；保存本身不会调用模型。')
        message = '此项目的视频理解连接尚未启用'
    elif not enabled:
        message = '视频理解运行器尚未启用，请先在本地服务运行配置中打开，再刷新状态'
        steps.append('由本地服务运行配置启用视频理解运行器；不会自动复用已保存的生成模型账号。')
    elif configured:
        try:
            endpoint_base(value['base_url'], allow_remote=remote)
            credential(value, worker.environment)
            if value['transcribe_audio']:
                endpoint_base(value['audio_base_url'] or value['base_url'], allow_remote=remote)
                credential(value, worker.environment, audio=True)
            present, available = True, True
            message = '配置可提交，服务能力尚未验证；开始理解会发送本次抽样原帧及启用时的音轨'
        except UnderstandingFailure as error:
            message = error.message
            steps.append(error.message)
    return {'project_id': project_id, 'revision': revision, 'connection': value, 'configured': configured,
        'available': available, 'credential_present': present, 'worker_enabled': enabled, 'remote_allowed': remote,
        'message': message, 'next_steps': steps}

