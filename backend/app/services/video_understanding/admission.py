"""Resolve complete source-specific understanding plans without network probes or credential reads."""
from urllib.parse import urlsplit

from fastapi import HTTPException

from app.core.contracts.video_understanding import UnderstandingConnection
from app.services.video_understanding.charges import units_for
from app.services.video_understanding.connection import endpoint_base
from app.services.video_understanding.errors import UnderstandingFailure
from app.services.video_understanding.model_connections import choices, safe_binding
from app.services.worker.model_prices import ModelPriceRegistry


def registry_for(worker):
    """An absent/disabled runtime never borrows pricing from process-wide or another workspace's configuration."""
    return getattr(worker, 'price_registry', None) or ModelPriceRegistry()


def check_network_policy(binding, worker):
    """Enforce operator remote/local settings without deriving a local exception from a project-selected URL."""
    try:
        base = endpoint_base(binding['base_url'], allow_remote=bool(worker and worker.allow_remote))
        parsed = urlsplit(base)
        if parsed.hostname in {'localhost', '127.0.0.1', '::1'}:
            port = parsed.port or (443 if parsed.scheme == 'https' else 80)
            if parsed.hostname != getattr(worker, 'trusted_local_host', None) or port not in getattr(worker, 'trusted_local_ports', set()):
                raise HTTPException(503, '本机模型服务地址尚未由运行配置明确允许，未发送素材')
    except UnderstandingFailure as error:
        raise HTTPException(503, error.message) from None


async def complete_plan(db, connection, source, worker, *, expected=None, original_bindings=None):
    """Check every required unit and named quote before any unit can reserve funds or send source media."""
    value = UnderstandingConnection.model_validate(connection)
    kinds = set(units_for(source, connection).values())
    if not value.enabled or not value.vision_model_id or ('video_asr' in kinds and not value.audio_model_id):
        raise HTTPException(503, '请先在本项目选择模型管理中的画面理解和语音转写模型；旧地址配置不能发起新调用')
    if worker is None or not worker.enabled:
        raise HTTPException(503, '原片理解运行器尚未启用，未接受模型调用')
    if expected is not None and set(expected) != kinds:
        raise HTTPException(409, '确认的费用没有覆盖本原片实际需要的完整调用计划，请刷新后重新确认')
    bindings, quotes = {}, {}
    registry = registry_for(worker)
    for kind in sorted(kinds):
        model_id = value.audio_model_id if kind == 'video_asr' else value.vision_model_id
        binding = await safe_binding(db, model_id, kind)
        check_network_policy(binding, worker)
        quote = registry.confirmed(kind, model_id, expected[kind]) if expected is not None else registry.required(kind, model_id)
        if (quote.model_name, quote.provider_id, quote.provider_connection_version) != (binding['model_name'], binding['provider_id'], binding['provider_connection_version']):
            raise HTTPException(503, '理解模型名称或连接版本与受信报价不一致，请更新费用配置')
        if original_bindings is not None and binding != original_bindings.get(kind):
            raise HTTPException(409, '原理解模型连接已变化，未改用新连接；请按当前项目配置新建任务')
        bindings[kind], quotes[kind] = binding, quote
    if original_bindings is not None and set(original_bindings) != kinds:
        raise HTTPException(409, '原理解费用计划不完整，不能重新发送')
    return bindings, quotes


async def setup_view(db, project_id, revision, connection, worker, source=None):
    """Show source-aware costs and explicit model choices using only inert metadata projections."""
    value = UnderstandingConnection.model_validate(connection).model_dump()
    source = source if source is not None else {'audio_present': True}
    kinds = list(units_for(source, value).values())
    models = await choices(db, registry_for(worker))
    configured = bool(value['vision_model_id'] and ('video_asr' not in kinds or value['audio_model_id']))
    result = {'project_id': project_id, 'revision': revision, 'connection': value, 'configured': configured,
              'available': False, 'credential_present': False, 'worker_enabled': bool(worker and worker.enabled),
              'remote_allowed': bool(worker and worker.allow_remote), 'models': models,
              'planned_calls': kinds, 'price_fingerprints': {}, 'max_reservation_minor': None,
              'message': '请在本项目选择明确支持原片画面理解与语音转写的模型', 'next_steps': []}
    try:
        bindings, quotes = await complete_plan(db, value, source, worker)
        result.update(available=True, credential_present=True,
                      price_fingerprints={kind: quote.fingerprint for kind, quote in quotes.items()},
                      max_reservation_minor=sum(quote.max_call_minor for quote in quotes.values()),
                      message='开始前将按本项目确认完整费用预留；发送抽样原帧及已启用的原片音轨，服务能力仍需验证')
    except HTTPException as error:
        result['message'] = str(error.detail)
        result['next_steps'] = [str(error.detail)]
    return result
