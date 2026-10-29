"""Resolve explicitly opted-in vision/ASR models using the same saved provider revision as project accounting."""
from urllib.parse import urlsplit
from types import SimpleNamespace

from fastapi import HTTPException
from sqlalchemy import case,func,select

from app.bootstrap import bootstrap_all_registries
from app.core.contracts.video_understanding import UnderstandingModelChoice
from app.models.llm import Model,ModelCategoryKey,Provider
from app.models.provider_connection_version import ProviderConnectionVersion
from app.services.llm.connection_versions import model_connection_snapshot
from app.services.llm.provider_resolver import resolve_effective_base_url
from app.services.llm.provider_registry import get_provider_spec,resolve_provider_key_from_name,is_provider_category_supported
from app.services.worker.model_prices import ModelPriceRegistry

PROTOCOLS={
    'video_vision':('text','understanding_vision_protocol','openai_chat_image_v1'),
    'video_asr':('audio','understanding_asr_protocol','openai_verbose_segments_v1'),
}


def compatible(kind,category,params,provider_name):
    """An explicit protocol flag and matching model category prevent generation models from masquerading as understanding."""
    if kind not in PROTOCOLS:
        return False
    expected,field,protocol=PROTOCOLS[kind]
    if category!=expected or not isinstance(params,dict) or params.get(field)!=protocol:
        return False
    try:
        bootstrap_all_registries()
        provider_key=resolve_provider_key_from_name(provider_name)
        return is_provider_category_supported(provider_key,ModelCategoryKey(expected)) and not get_provider_spec(provider_key).requires_api_secret
    except (HTTPException,ValueError):
        return False


def checked_base(provider,kind):
    """Validate inert server model metadata without DNS, key reflection, routing headers or arbitrary request parameters."""
    category=ModelCategoryKey(PROTOCOLS[kind][0])
    base=resolve_effective_base_url(provider=provider,category=category) or ''
    try:
        parsed=urlsplit(base)
        if parsed.scheme not in {'http','https'} or not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment or any(part in {'.','..'} for part in parsed.path.split('/')):
            raise ValueError()
        if parsed.port is not None and not 1<=parsed.port<=65535:
            raise ValueError()
        return base.rstrip('/')
    except (ValueError,TypeError):
        raise HTTPException(503,'理解模型服务地址无效；未接受模型调用') from None


async def material(db,model_id,kind):
    """Capture model, provider clock and transient key in one statement; only the returned safe binding may be persisted."""
    model,provider,version=await model_connection_snapshot(db,model_id)
    if provider.status=='disabled' or not provider.api_key.strip() or not compatible(kind,model.category,model.params,provider.name):
        raise HTTPException(503,'所选模型尚未明确配置本理解协议与授权；未接受调用')
    category,_field,protocol=PROTOCOLS[kind]
    binding={'model_id':model.id,'model_name':model.name,'provider_id':provider.id,'provider_name':provider.name,
        'provider_connection_version':version,'base_url':checked_base(provider,kind),'category':category,'protocol':protocol}
    return binding,provider.api_key.strip()


async def safe_binding(db,model_id,kind):
    """Inspect one coherent connection without bringing its key into Python during read-only setup views."""
    row=(await db.execute(select(Model.id,Model.name,Model.category,Model.params,Provider.id.label('provider_id'),
        Provider.name.label('provider_name'),Provider.status,Provider.base_url,Provider.image_base_url,Provider.video_base_url,
        func.coalesce(ProviderConnectionVersion.revision,0).label('version'),
        case((func.length(func.trim(Provider.api_key))>0,True),else_=False).label('has_key'))
        .join(Provider,Provider.id==Model.provider_id)
        .outerjoin(ProviderConnectionVersion,ProviderConnectionVersion.provider_id==Provider.id)
        .where(Model.id==model_id))).one_or_none()
    if row is None or row.status=='disabled' or not row.has_key or not compatible(kind,row.category,row.params,row.provider_name):
        raise HTTPException(503,'所选模型尚未明确配置本理解协议与授权；未接受调用')
    if type(row.version) is not int or not 0<=row.version<=9223372036854775806:
        raise HTTPException(503,'供应商连接版本无效，未接受理解调用')
    provider=SimpleNamespace(name=row.provider_name,base_url=row.base_url,image_base_url=row.image_base_url,video_base_url=row.video_base_url)
    return {'model_id':row.id,'model_name':row.name,'provider_id':row.provider_id,'provider_name':row.provider_name,
        'provider_connection_version':row.version,'base_url':checked_base(provider,kind),'category':PROTOCOLS[kind][0],'protocol':PROTOCOLS[kind][2]}


async def choices(db,registry=None):
    """Project safe opt-in choices and trusted per-protocol quotes; the SQL projection returns key presence only."""
    bootstrap_all_registries()
    registry=registry or ModelPriceRegistry()
    rows=(await db.execute(select(Model.id,Model.name,Model.category,Model.params,Provider.name.label('provider_name'),
        Provider.id.label('provider_id'),Provider.status,func.coalesce(ProviderConnectionVersion.revision,0).label('version'),
        case((func.length(func.trim(Provider.api_key))>0,True),else_=False).label('has_key'))
        .join(Provider,Provider.id==Model.provider_id)
        .outerjoin(ProviderConnectionVersion,ProviderConnectionVersion.provider_id==Provider.id)
        .where(Model.category.in_(['text','audio'])).order_by(Model.name,Model.id))).all()
    result=[]
    for row in rows:
        kind='video_asr' if row.category=='audio' else 'video_vision'
        if not compatible(kind,row.category,row.params,row.provider_name):
            continue
        quote=None
        try:
            candidate=registry.required(kind,row.id)
            if candidate.provider_id==row.provider_id and candidate.provider_connection_version==row.version and candidate.model_name==row.name:
                quote=candidate
        except HTTPException:
            pass
        result.append(UnderstandingModelChoice(id=row.id,name=row.name,provider_name=row.provider_name,kind=kind,
            configured=bool(row.has_key and row.status!='disabled'),priced=quote is not None,
            max_call_minor=quote.max_call_minor if quote else None,price_fingerprint=quote.fingerprint if quote else None))
    return result
