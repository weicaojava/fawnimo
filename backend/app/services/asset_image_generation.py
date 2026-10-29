"""Durable, fenced text-to-image candidates with separately authorized asset-slot adoption."""
import asyncio
from datetime import timedelta, timezone
import hashlib
import os
import re
import tempfile
from urllib.parse import urlsplit
import uuid

from fastapi import HTTPException
from sqlalchemy import String, cast, func, select, text, update
from sqlalchemy.dialects.sqlite import insert

from app.core import local_storage
from app.core.contracts.asset_image_generation import AssetImageAvailability, AssetImageJobRead, AssetImageSlotRead, ImageModelChoice
from app.core.integrations.asset_image import AssetImageModelError, invoke_asset_image
from app.models.asset_image_generation import AssetImageJob
from app.models.llm import Model, ModelSettings, Provider
from app.models.provider_connection_version import ProviderConnectionVersion
from app.models.execution_budget import ExecutionBudgetEntry
from app.models.studio import FileItem
from app.services.agent_execution import clock, digest
from app.services.studio.entity_specs import entity_spec
from app.services.worker.execution_budget import transaction
from app.core.contracts.model_accounting import QuotedModelCall,ModelPriceEnvelope
from app.services.worker.model_job_charges import begin_job_admission,bind_new_job,binding_digest,job_charge,charged_job_unit,release_job_unsent
from app.services.worker.model_charges import prepare_call,record_accepted_call
from app.services.worker.model_prices import ModelPriceRegistry

FINAL = {'candidate', 'adopted', 'failed', 'cancelled'}
PROTOCOL = 'openai_b64_v1'
UNKNOWN = '图片请求中断或结果未知；不会自动重发，显式重试可能重复计费'


async def migrate_asset_image_generation(engine):
    """Protect immutable request identity and successful candidate results in local SQLite itself."""
    if engine.dialect.name != 'sqlite':
        return
    identity = ['id', 'scope_key', 'request_key', 'request_hash', 'asset_kind', 'asset_id', 'slot_id', 'model_id', 'model_name', 'model_binding', 'prompt', 'size', 'attempt', 'retry_of', 'created_at']
    result = ['storage_key', 'sha256', 'byte_size', 'width', 'height', 'mime', 'finished_at']
    adoption = ['file_id', 'adopt_request_key', 'adopt_request_hash', 'adopted_at']
    async with engine.begin() as connection:
        for name, fields, condition in [('identity', identity, '1'), ('result', result, "OLD.status IN ('candidate','adopted')"), ('adoption', adoption, "OLD.status='adopted'")]:
            changed = ' OR '.join(f'NEW.{field} IS NOT OLD.{field}' for field in fields)
            await connection.execute(text(f"CREATE TRIGGER IF NOT EXISTS immutable_asset_image_{name} BEFORE UPDATE ON asset_image_generation_jobs WHEN ({condition}) AND ({changed}) BEGIN SELECT RAISE(ABORT,'immutable asset image {name}'); END"))
        await connection.execute(text("CREATE TRIGGER IF NOT EXISTS immutable_asset_image_terminal BEFORE UPDATE ON asset_image_generation_jobs WHEN OLD.status IN ('candidate','adopted','failed','cancelled') AND NEW.status IS NOT OLD.status AND NOT (OLD.status='candidate' AND NEW.status='adopted') BEGIN SELECT RAISE(ABORT,'immutable image terminal'); END"))
        await connection.execute(text("CREATE TRIGGER IF NOT EXISTS immutable_asset_image_success_delete BEFORE DELETE ON asset_image_generation_jobs WHEN OLD.status IN ('candidate','adopted') BEGIN SELECT RAISE(ABORT,'immutable image candidate'); END"))


async def availability(db, runtime, model_id=None):
    """Check protocol opt-in and credential presence without loading saved key values."""
    rows = (await db.execute(select(Model.id, Model.name, Model.params['asset_image_protocol'].as_string().label('protocol'), Provider.name.label('provider_name'), Provider.status,
        Provider.id.label('provider_id'),func.coalesce(ProviderConnectionVersion.revision,0).label('revision'),(func.length(func.trim(Provider.api_key)) > 0).label('has_key'))
        .join(Provider, Provider.id == Model.provider_id).outerjoin(ProviderConnectionVersion,ProviderConnectionVersion.provider_id==Provider.id)
        .where(Model.category == 'image').order_by(Model.name, Model.id).limit(1000))).all()
    choices = []
    for row in rows:
        supported = row.provider_name.strip().lower() == 'openai' and row.protocol == PROTOCOL
        configured = supported and row.has_key and row.status != 'disabled'
        message = '单张文本生图已配置；尚未验证真实模型' if configured else '请在模型管理启用“用于本机单张图片生成”，并配置受支持的 OpenAI-compatible 供应商；不支持人物参考图或仅返回网址的响应'
        quote=None
        if configured:
            try:
                quote=(runtime.price_registry if runtime else ModelPriceRegistry()).required('asset_image',row.id)
                if quote.provider_id!=row.provider_id or quote.provider_connection_version!=row.revision:
                    quote=None;message='图片模型连接已改变，请核对原连接的受信费用上界'
            except HTTPException:
                message='此图片模型尚未登记单次调用的费用上界'
        choices.append(ImageModelChoice(id=row.id, name=row.name, configured=bool(configured), message=message,priced=quote is not None,
            max_call_minor=quote.max_call_minor if quote else None,price_fingerprint=quote.fingerprint if quote else None))
    setting = await db.get(ModelSettings, 1)
    default_id = setting.default_image_model_id if setting else None
    selected = next((row for row in choices if row.id == (model_id or default_id)), None)
    available = bool(runtime and runtime.running and selected and selected.configured and selected.priced)
    message = '可显式提交一张文本生图候选，可能产生费用；生成后需另外采用' if available else selected.message if selected else '请选择图片模型，并在模型管理启用“用于本机单张图片生成”'
    if not runtime or not runtime.running:
        message = '独立图片执行服务尚未启动；手工编辑与上传仍可使用'
    return AssetImageAvailability(available=available, message=message, default_model_id=default_id, models=choices)


async def image_connection_material(db, model_id):
    """Capture protocol, endpoint, key and clock from one coherent model/provider snapshot."""
    from app.services.llm.connection_versions import model_connection_snapshot
    from app.services.llm.provider_resolver import resolve_provider_config_from_provider
    model,provider,revision=await model_connection_snapshot(db,model_id)
    if model.category != 'image' or (model.params or {}).get('asset_image_protocol') != PROTOCOL:
        raise HTTPException(503, '模型没有启用受支持的单张 base64 文生图协议')
    config = resolve_provider_config_from_provider(provider=provider,category=model.category)
    if config.provider_key != 'openai':
        raise HTTPException(503, '此入口仅支持声明的 OpenAI-compatible base64 图片协议')
    parsed = urlsplit(config.base_url or '')
    if not parsed.hostname or parsed.query or parsed.fragment or parsed.username or parsed.password:
        raise HTTPException(422, '图片供应商基础地址无效')
    return {'model_id': model.id, 'model_name': model.name, 'provider_id': model.provider_id, 'provider_connection_version': revision, 'base_url': config.base_url, 'protocol': PROTOCOL},config.api_key


async def binding(db, model_id):
    """Keep the saved image connection snapshot free of credential values or key hashes."""
    resolved,_secret=await image_connection_material(db,model_id)
    return resolved


async def slot(db, kind, asset_id, slot_id):
    """Require the exact global asset and its own actual image row, rejecting cross-asset adoption."""
    spec = entity_spec(kind)
    if kind not in {'actor', 'costume', 'scene', 'prop'} or await db.get(spec.model, asset_id) is None:
        raise HTTPException(404, '资产不存在或类型不受支持')
    image = await db.get(spec.image_model, slot_id)
    if image is None or getattr(image, spec.id_field) != asset_id:
        raise HTTPException(404, '图片槽位不属于此资产')
    return spec, image


async def slots(db, kind, asset_id):
    """Return current compare-and-swap snapshots without creating or changing any slot."""
    spec = entity_spec(kind)
    if kind not in {'actor', 'costume', 'scene', 'prop'} or await db.get(spec.model, asset_id) is None:
        raise HTTPException(404, '资产不存在或类型不受支持')
    rows = (await db.scalars(select(spec.image_model).where(getattr(spec.image_model, spec.id_field) == asset_id).order_by(spec.image_model.id))).all()
    return [AssetImageSlotRead(id=row.id, file_id=row.file_id, updated_at=row.updated_at, view_angle=row.view_angle, quality_level=row.quality_level) for row in rows]


async def get_job(db, job_id):
    """Load an existing persistent attempt without exposing its storage or credentials."""
    row = await db.get(AssetImageJob, job_id)
    if row is None:
        raise HTTPException(404, '图片生成任务不存在')
    return row


async def job_read(db,job):
    """Publish only a verified candidate content endpoint, never a provider URL or disk path."""
    financial=await job_charge(db,'image',job.id)
    charge=await charged_job_unit(db,financial,'candidate') if financial else None
    entry=await db.get(ExecutionBudgetEntry,charge.id) if charge else None
    return AssetImageJobRead.model_validate(job).model_copy(update={'content_url': f'/api/v1/asset-image-generation/jobs/{job.id}/content' if job.status in {'candidate', 'adopted'} else None,
        'project_id':financial.project_id if financial else None,'authorized_max_minor':str(entry.quote_minor) if entry else None})


async def admit(db, runtime, kind, asset_id, slot_id, body, *,submitted_by='local_operator'):
    """Open the project's writer transaction before resolving a new image request."""
    await begin_job_admission(db)
    return await _admit_locked(db,runtime,kind,asset_id,slot_id,body,submitted_by=submitted_by)


async def retry(db,runtime,job_id,body,*,submitted_by='local_operator'):
    """Keep the original project and connection while reserving a distinct explicitly confirmed attempt."""
    await begin_job_admission(db)
    original=await get_job(db,job_id)
    if original.status not in {'failed','cancelled'}:
        raise HTTPException(409,'只有失败或取消的图片任务可显式重试')
    return await _admit_locked(db,runtime,original.asset_kind,original.asset_id,original.slot_id,body,original,submitted_by=submitted_by)


async def _admit_locked(db, runtime, kind, asset_id, slot_id, body, original=None, *,submitted_by):
    """Admit a single immutable attempt idempotently before any potentially billable HTTP."""
    if db.get_bind().dialect.name != 'sqlite':
        raise HTTPException(503, '本地图片执行当前支持带不可变触发器的 SQLite')
    scope = 'retry:' + original.id if original else f'{kind}:{asset_id}:{slot_id}'
    fingerprint = digest({**body.model_dump(mode='json'),'submitted_by':submitted_by})
    prior = await db.scalar(select(AssetImageJob).where(AssetImageJob.scope_key == scope, AssetImageJob.request_key == body.request_key))
    if prior:
        if prior.request_hash != fingerprint:
            raise HTTPException(409, '请求标识已用于不同图片参数')
        return prior
    financial=await job_charge(db,'image',original.id,required=True) if original else None
    project_id=financial.project_id if financial else body.project_id
    await slot(db, kind, asset_id, slot_id)
    model_id = original.model_id if original else body.model_id
    state = await availability(db, runtime, model_id)
    if not state.available:
        raise HTTPException(503, state.message)
    resolved = await binding(db, model_id)
    if original and resolved != original.model_binding:
        raise HTTPException(409, '模型配置已改变，请新建生成；旧请求不会改用新连接重发')
    quote=runtime.price_registry.confirmed('asset_image',model_id,body.expected_price_fingerprint)
    if await db.scalar(select(func.count()).select_from(AssetImageJob).where(AssetImageJob.status.in_(['queued', 'running']))) >= 20:
        raise HTTPException(409, '待处理图片任务过多，请先完成或取消')
    job_id = uuid.uuid4().hex
    values = dict(id=job_id, scope_key=scope, request_key=body.request_key, request_hash=fingerprint, asset_kind=kind, asset_id=asset_id, slot_id=slot_id, model_id=model_id, model_name=resolved['model_name'], model_binding=resolved, prompt=original.prompt if original else body.prompt, size=original.size if original else body.size, status='queued', error='', attempt=original.attempt + 1 if original else 1, retry_of=original.id if original else None, created_at=clock())
    inserted = await db.scalar(insert(AssetImageJob).values(**values).on_conflict_do_nothing(index_elements=['scope_key', 'request_key']).returning(AssetImageJob.id))
    if inserted is None:
        prior = await db.scalar(select(AssetImageJob).where(AssetImageJob.scope_key == scope, AssetImageJob.request_key == body.request_key))
        if prior and prior.request_hash == fingerprint:
            return prior
        raise HTTPException(409, '请求参数并发冲突，请使用原标识查询')
    await bind_new_job(db,kind='image',job_id=job_id,project_id=project_id,submitted_by=submitted_by,request_hash=fingerprint,
        calls=[QuotedModelCall(unit_key='candidate',kind='asset_image',binding_hash=binding_digest(resolved),quote=quote)])
    return await db.get(AssetImageJob, job_id)


async def cancel(db, job_id):
    """Durably reject any late result; an already sent provider request may still cost money."""
    job = await get_job(db, job_id)
    await db.execute(update(AssetImageJob).execution_options(synchronize_session=False).where(AssetImageJob.id == job_id, AssetImageJob.status.in_(['queued', 'running'])).values(status='cancelled', error='已取消，未采用迟到图片；已发出的请求仍可能计费', lease_token=None, lease_expires_at=None, finished_at=clock()))
    await release_job_unsent(db,'image',job_id)
    await db.refresh(job)
    return job


async def verified_bytes(job):
    """Resolve only the fenced immutable job path and verify size/SHA before preview or adoption."""
    expected = r'asset-image-candidates/([0-9a-f]{32})/[0-9a-f]{32}/([0-9a-f]{64})\.(png|jpg)'
    match = re.fullmatch(expected, job.storage_key or '')
    if job.status not in {'candidate', 'adopted'} or not match or match[1] != job.id or match[2] != job.sha256:
        raise HTTPException(409, '候选图片存储身份无效')
    def read_bounded():
        """Read at most the candidate limit even if local bytes were later replaced or expanded."""
        target = local_storage.storage_path(job.storage_key)
        try:
            with target.open('rb') as stream:
                return stream.read(8 * 1024 * 1024 + 1)
        except FileNotFoundError:
            raise HTTPException(404, '候选图片文件不存在') from None
    content = await asyncio.to_thread(read_bounded)
    if len(content) != job.byte_size or hashlib.sha256(content).hexdigest() != job.sha256 or len(content) > 8 * 1024 * 1024:
        raise HTTPException(409, '候选图片校验失败，未采用损坏内容')
    return content


async def read_adopted_file(db, file_item):
    """Serve local generated files independently of any configured S3 backend, with immutable linkage."""
    job = await db.scalar(select(AssetImageJob).where(AssetImageJob.file_id == file_item.id, AssetImageJob.status == 'adopted'))
    extension = 'png' if job and job.mime == 'image/png' else 'jpg'
    if job is None or file_item.storage_key != f'local-asset-image/{job.id}.{extension}':
        raise HTTPException(409, '已采用图片与文件记录不匹配')
    return await verified_bytes(job)


async def adopt(factory, job_id, body):
    """CAS one exact slot only after a separate explicit intent; original files and primary images survive."""
    async with factory() as db:
        async with transaction(db):
            job = await get_job(db, job_id)
            fingerprint = digest(body.model_dump(mode='json'))
            if job.status == 'adopted':
                if job.adopt_request_key == body.request_key and job.adopt_request_hash == fingerprint:
                    return await job_read(db,job)
                raise HTTPException(409, '此候选已采用；不会重复覆盖槽位')
            if job.status != 'candidate':
                raise HTTPException(409, '只有已完成并校验的候选图片可以采用')
            await verified_bytes(job)
            spec, current = await slot(db, job.asset_kind, job.asset_id, job.slot_id)
            expected_time = body.expected_updated_at.astimezone(timezone.utc).replace(tzinfo=None) if body.expected_updated_at.tzinfo else body.expected_updated_at
            if current.file_id != body.expected_file_id or current.updated_at != expected_time:
                raise HTTPException(409, '槽位已改变，请刷新并确认当前图片后重新采用')
            # CURRENT_TIMESTAMP may store whole seconds while bound DateTime renders
            # .000000. Compare the exact persisted token after validating client time.
            stored_clock = await db.scalar(select(cast(spec.image_model.updated_at, String)).where(spec.image_model.id == job.slot_id))
            file_id = uuid.uuid4().hex
            extension = 'png' if job.mime == 'image/png' else 'jpg'
            db.add(FileItem(id=file_id, type='image', name=f'生成候选-{job.id[:10]}.{extension}', thumbnail=f'/api/v1/asset-image-generation/jobs/{job.id}/content', tags=['generated_candidate_unqualified'], storage_key=f'local-asset-image/{job.id}.{extension}'))
            await db.flush()
            changed = await db.execute(update(spec.image_model).execution_options(synchronize_session=False).where(spec.image_model.id == job.slot_id, getattr(spec.image_model, spec.id_field) == job.asset_id, spec.image_model.file_id == body.expected_file_id, cast(spec.image_model.updated_at, String) == stored_clock).values(file_id=file_id, width=job.width, height=job.height, format=extension, updated_at=clock()))
            if changed.rowcount != 1:
                raise HTTPException(409, '槽位发生并发改变，原图片保持不变')
            job.status, job.file_id, job.adopted_at = 'adopted', file_id, clock()
            job.adopt_request_key, job.adopt_request_hash = body.request_key, fingerprint
            await db.flush()
            return await job_read(db,job)


def save_exclusive(key, content):
    """Atomically publish complete bytes into a server-generated, exclusive lease-specific path."""
    target = local_storage.storage_path(key)
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(dir=target.parent, prefix='.candidate-', delete=False) as stream:
            temporary = stream.name
            stream.write(content); stream.flush(); os.fsync(stream.fileno())
        os.link(temporary, target)
    finally:
        if temporary:
            os.unlink(temporary)


class AssetImageGenerationRuntime:
    """CAS-leased persistent image worker; unknown requests are never automatically replayed."""
    def __init__(self, factory, *, poll_interval=.25, trusted_local_host=None, trusted_local_ports=frozenset(),price_registry=None):
        """Inject exact fixture trust separately from any client model or asset data."""
        self.factory, self.poll_interval = factory, poll_interval
        self.price_registry=price_registry or ModelPriceRegistry()
        self.trusted_local_host, self.trusted_local_ports = trusted_local_host, trusted_local_ports
        self.running, self.task = False, None

    async def start(self):
        """Start only a scanner; no model or credential is read until an explicit admitted job exists."""
        if not self.running:
            self.running = True; self.task = asyncio.create_task(self._loop())

    async def stop(self):
        """Join HTTP cancellation and database outcome handling before shutdown completes."""
        self.running = False
        if self.task:
            self.task.cancel(); await asyncio.gather(self.task, return_exceptions=True); self.task = None

    async def _loop(self):
        """Keep scanning after database errors without treating them as permission to resend HTTP."""
        while self.running:
            try:
                await self.tick()
            except asyncio.CancelledError:
                raise
            except Exception:
                pass
            await asyncio.sleep(self.poll_interval)

    async def _owned(self, db, job_id, token):
        """A stale or cancelled owner cannot publish bytes or a successful job state."""
        return await db.scalar(select(AssetImageJob).where(AssetImageJob.id == job_id, AssetImageJob.status == 'running', AssetImageJob.lease_token == token, AssetImageJob.lease_expires_at > clock()))

    async def tick(self):
        """Fail expired requests as unknown and atomically claim one queued job across workers."""
        async with self.factory() as db:
            expired=(await db.scalars(update(AssetImageJob).execution_options(synchronize_session=False).where(AssetImageJob.status == 'running', AssetImageJob.lease_expires_at < clock())
                .values(status='failed', error=UNKNOWN, finished_at=clock(), lease_token=None, lease_expires_at=None).returning(AssetImageJob.id))).all()
            for expired_id in expired:
                await release_job_unsent(db,'image',expired_id)
            job_id = await db.scalar(select(AssetImageJob.id).where(AssetImageJob.status == 'queued').order_by(AssetImageJob.created_at, AssetImageJob.id).limit(1))
            token = uuid.uuid4().hex
            claimed = False
            if job_id:
                result = await db.execute(update(AssetImageJob).execution_options(synchronize_session=False).where(AssetImageJob.id == job_id, AssetImageJob.status == 'queued').values(status='running', lease_token=token, lease_expires_at=clock() + timedelta(seconds=75)))
                claimed = bool(result.rowcount)
            await db.commit()
        if claimed:
            await self._execute(job_id, token)

    async def _fail(self, job_id, token, error):
        """Record only a still-owned failure, never modifying immutable candidate results."""
        async with self.factory() as db:
            result=await db.execute(update(AssetImageJob).execution_options(synchronize_session=False).where(AssetImageJob.id == job_id, AssetImageJob.status == 'running', AssetImageJob.lease_token == token, AssetImageJob.lease_expires_at > clock()).values(status='failed', error=error, finished_at=clock(), lease_token=None, lease_expires_at=None))
            if result.rowcount:
                await release_job_unsent(db,'image',job_id)
            await db.commit()

    async def _record_response(self,project_id,charge_id,provider_request_id):
        """Save late private response evidence against the original hold without publishing a cancelled candidate."""
        if provider_request_id is not None:
            async with self.factory() as db:
                async with transaction(db):
                    await record_accepted_call(db,project_id=project_id,charge_id=charge_id,provider_request_id=provider_request_id)

    async def _execute(self, job_id, token):
        """Make one real request, verify image bytes, fence publication, and leave adoption separate."""
        call, saved_key, published = None, None, False
        current_charge,current_project,secret=None,None,''
        try:
            async with self.factory() as db:
                async with transaction(db):
                    job = await self._owned(db, job_id, token)
                    if job is None:
                        return
                    financial=await job_charge(db,'image',job_id,required=True)
                    if financial.request_hash!=job.request_hash:
                        raise AssetImageModelError('原图片任务与项目费用授权不一致，未发起模型请求')
                    await slot(db, job.asset_kind, job.asset_id, job.slot_id)
                    resolved,secret = await image_connection_material(db, job.model_id)
                    if resolved != job.model_binding:
                        raise AssetImageModelError('图片模型配置已改变，请新建执行；未转向其他连接')
                    charge=await charged_job_unit(db,financial,'candidate')
                    stored=ModelPriceEnvelope.model_validate(charge.price_snapshot)
                    self.price_registry.confirmed('asset_image',job.model_id,stored.fingerprint)
                    if charge.kind!='asset_image':
                        raise AssetImageModelError('原图片调用费用类型无效，未发起模型请求')
                    await prepare_call(db,project_id=financial.project_id,charge_id=charge.id,binding_hash=binding_digest(resolved))
                    current_charge,current_project=charge.id,financial.project_id
                    prompt, size = job.prompt, job.size
            call = asyncio.create_task(invoke_asset_image(base_url=resolved['base_url'], api_key=secret, model=resolved['model_name'], prompt=prompt, size=size, trusted_local_host=self.trusted_local_host, trusted_local_ports=self.trusted_local_ports))
            secret=''
            while True:
                done, _ = await asyncio.wait({call}, timeout=.1)
                if done:
                    result = await call
                    break
                async with self.factory() as db:
                    if await self._owned(db, job_id, token) is None:
                        raise asyncio.CancelledError()
            await self._record_response(current_project,current_charge,result.provider_request_id)
            async with self.factory() as db:
                if await self._owned(db, job_id, token) is None:
                    return
            extension = 'png' if result.mime == 'image/png' else 'jpg'
            saved_key = f'asset-image-candidates/{job_id}/{token}/{result.sha256}.{extension}'
            # Complete file publication is bounded by the decoded 8 MiB limit. Its private
            # path is unreachable through HTTP until the following fenced DB commit.
            save_exclusive(saved_key, result.content)
            async with self.factory() as db:
                changed = await db.execute(update(AssetImageJob).execution_options(synchronize_session=False).where(AssetImageJob.id == job_id, AssetImageJob.status == 'running', AssetImageJob.lease_token == token, AssetImageJob.lease_expires_at > clock()).values(status='candidate', storage_key=saved_key, sha256=result.sha256, byte_size=len(result.content), width=result.width, height=result.height, mime=result.mime, finished_at=clock(), lease_token=None, lease_expires_at=None))
                await db.commit(); published = bool(changed.rowcount)
        except asyncio.CancelledError:
            await self._fail(job_id, token, UNKNOWN)
            if not self.running:
                raise
        except AssetImageModelError as failure:
            if current_charge is not None:
                await self._record_response(current_project,current_charge,failure.provider_request_id)
            await self._fail(job_id, token, str(failure))
        except HTTPException:
            await self._fail(job_id, token, '资产、槽位或图片模型配置不可用，未生成模拟图片')
        except Exception:
            await self._fail(job_id, token, '图片执行失败或结果未知；不会自动重发，重试可能重复计费')
        finally:
            secret=''
            if call and not call.done():
                call.cancel(); await asyncio.gather(call, return_exceptions=True)
            if saved_key and not published:
                local_storage.storage_path(saved_key).unlink(missing_ok=True)
