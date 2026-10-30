"""Capture explicit managed Seedance input identities without inferring execution or borrowing a host credential."""
import ipaddress
from types import SimpleNamespace

from fastapi import HTTPException
from sqlalchemy import case, func, select

from app.bootstrap import bootstrap_all_registries
from app.core.contracts.seedance25 import ARK_BASE_URL, MODEL_ID
from app.core.contracts.video_runtime_setup import NativeVideoModelChoice
from app.models.llm import Model, ModelCategoryKey, Provider
from app.models.provider_connection_version import ProviderConnectionVersion
from app.services.llm.connection_versions import model_connection_snapshot
from app.services.llm.provider_registry import resolve_provider_key_from_name
from app.services.llm.provider_resolver import resolve_effective_base_url
from app.services.worker.model_prices import ModelPriceRegistry

PROTOCOL = 'seedance25_text_v1'


def compatible(category, params, provider_name, model_name):
    """Only the explicitly selected fixed native protocol can use this adapter; generation categories never imply it."""
    if category!='video' or model_name!=MODEL_ID or not isinstance(params,dict) or params.get('native_video_protocol')!=PROTOCOL:
        return False
    try:
        bootstrap_all_registries()
        return resolve_provider_key_from_name(provider_name)=='volcengine'
    except (HTTPException,ValueError):
        return False


def checked_base(provider, *, trusted_local_host=None, trusted_local_port=None):
    """Keep the production Ark origin fixed; any test/local exception must independently name its exact host and port."""
    value=resolve_effective_base_url(provider=provider,category=ModelCategoryKey.video) or ''
    base=value.strip().rstrip('/') if isinstance(value,str) else ''
    if trusted_local_host is None:
        if trusted_local_port is None and base==ARK_BASE_URL:
            return ARK_BASE_URL
    else:
        try:
            local=trusted_local_host=='localhost' or ipaddress.ip_address(trusted_local_host).is_loopback
        except (TypeError,ValueError):
            local=False
        if local and type(trusted_local_port) is int and 1<=trusted_local_port<=65535:
            host='['+trusted_local_host+']' if ':' in trusted_local_host else trusted_local_host
            expected=f'http://{host}:{trusted_local_port}/api/v3'
            if base==expected:
                return expected
    raise HTTPException(503,'原生视频模型地址与受信执行服务不符，未接受调用')


def binding(model,provider,version,**network):
    """Describe only the material actually used by the fixed native adapter, excluding credentials and ignored model parameters."""
    if (type(version) is not int or not 0<=version<=9223372036854775806 or provider.status=='disabled'
            or not compatible(model.category,model.params,provider.name,model.name)):
        raise HTTPException(503,'所选模型没有明确配置受支持的原生视频协议，未接受调用')
    return {'model_id':model.id,'model_name':model.name,'category':'video','protocol':PROTOCOL,
            'provider_id':provider.id,'provider_name':provider.name,'provider_connection_version':version,
            'base_url':checked_base(provider,**network)}


async def safe_binding(db, model_id, *, trusted_local_host=None, trusted_local_port=None):
    """Project one coherent saved model/clock/key-presence snapshot without bringing a key into Python."""
    row=(await db.execute(select(Model.id,Model.name,Model.category,Model.params,Provider.id.label('provider_id'),
        Provider.name.label('provider_name'),Provider.status,Provider.base_url,Provider.image_base_url,Provider.video_base_url,
        func.coalesce(ProviderConnectionVersion.revision,0).label('version'),
        case((func.length(func.trim(Provider.api_key))>0,True),else_=False).label('has_key'))
        .join(Provider,Provider.id==Model.provider_id)
        .outerjoin(ProviderConnectionVersion,ProviderConnectionVersion.provider_id==Provider.id)
        .where(Model.id==model_id))).one_or_none()
    if row is None or not row.has_key:
        raise HTTPException(503,'原生视频模型连接尚未配置授权，未接受调用')
    model=SimpleNamespace(id=row.id,name=row.name,category=row.category,params=row.params)
    provider=SimpleNamespace(id=row.provider_id,name=row.provider_name,status=row.status,
        base_url=row.base_url,image_base_url=row.image_base_url,video_base_url=row.video_base_url)
    return binding(model,provider,row.version,trusted_local_host=trusted_local_host,trusted_local_port=trusted_local_port)


async def material(db, model_id, *, trusted_local_host=None, trusted_local_port=None):
    """Capture a transient native key and the same exact provider clock in one fresh database query."""
    model,provider,version=await model_connection_snapshot(db,model_id)
    safe=binding(model,provider,version,trusted_local_host=trusted_local_host,trusted_local_port=trusted_local_port)
    return safe,credential(provider)


def credential(provider):
    """Validate only the transient captured key, without reflecting it in any projection or validation error."""
    key=provider.api_key.strip() if isinstance(provider.api_key,str) else ''
    if not 1<=len(key)<=8192 or not key.isascii() or any(ord(char)<=32 or ord(char)>=127 for char in key):
        raise HTTPException(503,'原生视频供应商授权无效，未接受调用')
    return key


async def original_material(db,original,*,trusted_local_host=None,trusted_local_port=None):
    """Removing a new-generation flag cannot reprice or disable an already sealed original-account read."""
    if not isinstance(original,dict) or original.get('protocol')!=PROTOCOL or original.get('category')!='video':
        raise HTTPException(409,'原生视频原协议授权缺失，未读取账号凭据')
    model,provider,version=await model_connection_snapshot(db,original.get('model_id'))
    captured=SimpleNamespace(id=model.id,name=model.name,category=model.category,params={'native_video_protocol':PROTOCOL})
    safe=binding(captured,provider,version,trusted_local_host=trusted_local_host,trusted_local_port=trusted_local_port)
    if safe!=original:raise HTTPException(409,'原生视频原模型或账号连接已经变化，未向新账号查询')
    return safe,credential(provider)


async def choices(db,registry=None,*,trusted_local_host=None,trusted_local_port=None):
    """Read explicitly supported native model choices and named prices without testing or enabling a transport."""
    registry=registry or ModelPriceRegistry()
    rows=(await db.execute(select(Model.id,Model.name,Model.category,Model.params,Provider.id.label('provider_id'),
        Provider.name.label('provider_name')).join(Provider,Provider.id==Model.provider_id)
        .where(Model.category=='video').order_by(Model.name,Model.id))).all()
    result=[]
    for row in rows:
        if not compatible(row.category,row.params,row.provider_name,row.name):
            continue
        configured=False;quote=None;message='原生视频连接或价格尚未登记'
        try:
            safe=await safe_binding(db,row.id,trusted_local_host=trusted_local_host,trusted_local_port=trusted_local_port)
            configured=True
            candidate=registry.required('native_video',row.id)
            if (candidate.model_name,candidate.provider_id,candidate.provider_connection_version)!=(safe['model_name'],safe['provider_id'],safe['provider_connection_version']):
                raise HTTPException(503,'原生视频实际模型名称或连接版本与受信报价不一致')
            quote=candidate;message='模型连接与原生视频费用已登记；此读取不授予执行或真实模型资格'
        except HTTPException as error:
            message=str(error.detail)
        result.append(NativeVideoModelChoice(id=row.id,name=row.name,provider_name=row.provider_name,configured=configured,
            message=message,priced=quote is not None,max_call_minor=quote.max_call_minor if quote else None,
            price_fingerprint=quote.fingerprint if quote else None))
    return result
