"""Prepare exact reference bytes outside writer ownership and seal them with the original attempt."""
import asyncio
from dataclasses import dataclass
import hashlib
import os
from pathlib import Path, PurePosixPath
import stat

from fastapi import HTTPException
from sqlalchemy import select

from app.config import settings
from app.core.contracts.asset_image_protocols import asset_image_protocol, LEGACY_IMAGE_PROTOCOL, SEEDREAM_IMAGE_PROTOCOL, GPT_IMAGE_PROTOCOL
from app.core.integrations.asset_image import AssetImageModelError
from app.core.integrations.verified_image import verify_image_bytes
from app.core.owned_tasks import join_tasks
from app.models.asset_image_inputs import AssetImageInputSet, AssetImageReference
from app.models.gpt_image_inputs import GptImageInputSet,GptImageReference
from app.models.llm import Model, Provider
from app.models.production import ProductionMedia
from app.services.agent_execution import digest
from app.services.worker.model_job_charges import binding_digest


@dataclass(frozen=True)
class VerifiedReference:
    """Retain these actual decoded bytes and a cheap disk witness; never reread a path to send them."""
    fields: dict
    image: object
    file_stat: tuple


@dataclass(frozen=True)
class PreparedImageInputs:
    """Server-only transient proofs cannot be supplied by the HTTP request."""
    protocol: object
    response_format: str
    references: tuple[VerifiedReference, ...]


def request_payload(body, *, protocol_key):
    """Preserve historical text/b64 fingerprints exactly; nondefault new inputs always remain in the fingerprint."""
    value = body.model_dump(mode='json')
    if protocol_key == LEGACY_IMAGE_PROTOCOL and 'references' in value and not value['references'] and value['response_format'] == 'b64_json':
        value.pop('references')
        value.pop('response_format')
    return value


async def configured_protocol(db, model_id):
    """Resolve model-owned capability fields without selecting or decrypting any provider secret."""
    row = (await db.execute(select(Model.name, Model.params, Provider.name.label('provider_name'))
        .join(Provider, Provider.id == Model.provider_id).where(Model.id == model_id, Model.category == 'image'))).first()
    try:
        if row is None or not isinstance(row.params, dict):
            raise ValueError()
        from app.bootstrap import bootstrap_all_registries
        from app.services.llm.provider_registry import resolve_provider_key_from_name
        bootstrap_all_registries()
        return asset_image_protocol(provider=resolve_provider_key_from_name(row.provider_name), model=row.name, params=row.params)
    except (TypeError, ValueError, AttributeError):
        raise HTTPException(503, '此图片模型没有启用受支持的明确协议或下载范围配置') from None


def validate_requested_inputs(protocol, *, size, response_format, reference_count):
    """Reject unsupported options before reserving funds or reading reference files."""
    if size not in protocol.sizes or response_format not in protocol.response_formats or reference_count > protocol.max_references:
        raise HTTPException(422, '所选图片协议不支持这些参考输入、尺寸或返回方式，未发起请求')


def public_reference_fields(row):
    """Describe one complete saved proof in a deterministic order without ORM state or workspace bookkeeping."""
    return {name: getattr(row, name) for name in ('project_id', 'media_id', 'position', 'purpose', 'sha256', 'byte_size', 'width', 'height', 'mime', 'relative_path')}


async def saved_input_set(db, job, project_id):
    """Refuse absent, partial or mismatched new manifests; old text-only jobs do not gain a new protocol."""
    gpt=job.model_binding.get('protocol')==GPT_IMAGE_PROTOCOL
    header_type,reference_type=(GptImageInputSet,GptImageReference) if gpt else (AssetImageInputSet,AssetImageReference)
    other_header,other_reference=(AssetImageInputSet,AssetImageReference) if gpt else (GptImageInputSet,GptImageReference)
    if await db.get(other_header,job.id) is not None or (await db.scalar(select(other_reference.job_id).where(other_reference.job_id==job.id).limit(1))) is not None:
        raise HTTPException(409,'图片任务混用了其他协议的参考输入证明')
    header = await db.get(header_type, job.id)
    references = list((await db.scalars(select(reference_type).where(reference_type.job_id == job.id).order_by(reference_type.position))).all())
    if job.model_binding.get('protocol') == LEGACY_IMAGE_PROTOCOL:
        if header is not None or references:
            raise HTTPException(409, '历史文本图片任务不能附加新的参考输入')
        return None, []
    fields = [public_reference_fields(row) for row in references]
    if (job.model_binding.get('protocol') not in {SEEDREAM_IMAGE_PROTOCOL,GPT_IMAGE_PROTOCOL} or header is None or not header.sealed
            or header.project_id != project_id or header.protocol != job.model_binding['protocol']
            or header.profile != job.model_binding.get('gpt_image_profile' if gpt else 'seedream_profile') or header.model_name != job.model_name
            or header.size != job.size or header.request_hash != job.request_hash
            or header.binding_hash != binding_digest(job.model_binding)
            or header.reference_count != len(references) or [row.position for row in references] != list(range(header.reference_count))
            or any(row.project_id != project_id for row in references) or header.reference_hash != digest(fields)):
        raise HTTPException(409, '图片任务的完整原项目参考输入证明不一致，未发起请求')
    return header.response_format, fields


async def reference_candidates(db, project_id, inputs):
    """Load at most three original-project PNG/JPEG metadata rows before closing the read transaction."""
    result = []
    for position, item in enumerate(inputs):
        row = await db.get(ProductionMedia, item.media_id)
        if row is None or row.project_id != project_id:
            raise HTTPException(404, '原项目的参考图片不存在')
        if row.sha256 != item.expected_sha256:
            raise HTTPException(409, '参考图片的内容版本已变化，未发起模型请求')
        if row.mime not in {'image/png', 'image/jpeg'} or not 1 <= row.size <= 4 * 1024 * 1024:
            raise HTTPException(422, '参考图片只支持不超过4 MiB的单帧 PNG/JPEG')
        result.append(dict(project_id=project_id, media_id=row.id, position=position, purpose=item.purpose,
            sha256=row.sha256, byte_size=row.size, mime=row.mime, relative_path=row.relative_path))
    return result


def reference_path(relative):
    """Resolve only the original generated media path and reject redirection before reading private bytes."""
    if not isinstance(relative, str) or not relative or '\\' in relative or ':' in relative or any(p in {'', '.', '..'} for p in relative.split('/')) or PurePosixPath(relative).is_absolute():
        raise HTTPException(409, '参考图片的存储证明无效')
    root = Path(settings.production_media_root).resolve()
    target = root / relative
    if target.resolve() != target or not target.is_relative_to(root):
        raise HTTPException(409, '参考图片的存储证明无效')
    return target


def file_witness(value):
    """Use file identity, size and nanosecond clocks as an additional post-hash replacement check."""
    return value.st_dev, value.st_ino, value.st_size, value.st_mtime_ns, value.st_ctime_ns


def read_reference(fields):
    """Read one bounded file once and compare opened-descriptor/path identity around that read."""
    try:
        path = reference_path(fields['relative_path'])
        before = path.stat(follow_symlinks=False)
        if not stat.S_ISREG(before.st_mode) or before.st_size != fields['byte_size'] or before.st_size > 4 * 1024 * 1024:
            raise ValueError()
        with path.open('rb') as stream:
            opened = os.fstat(stream.fileno())
            content = stream.read(4 * 1024 * 1024 + 1)
            after = os.fstat(stream.fileno())
        witness = file_witness(before)
        # Windows can expose different ctime meanings through path stat and
        # descriptor fstat. Compare identity/size/mtime across the descriptor,
        # while retaining the complete path witness (including ctime) around
        # the read and again inside admission/dispatch writer ownership.
        if not stat.S_ISREG(opened.st_mode) or witness[:4] != file_witness(opened)[:4] or witness[:4] != file_witness(after)[:4] or witness != file_witness(path.stat(follow_symlinks=False)) or len(content) != fields['byte_size'] or hashlib.sha256(content).hexdigest() != fields['sha256']:
            raise ValueError()
        return content, witness
    except (OSError, ValueError):
        raise HTTPException(409, '参考图片缺失或内容已改变，未发起模型请求') from None


async def verify_references(candidates):
    """Join bounded reads and owned CPU decoders outside SQLite writer transactions, retaining the exact bytes."""
    if len(candidates) > 3 or sum(row['byte_size'] for row in candidates) > 12 * 1024 * 1024:
        raise HTTPException(422, '参考图片超过本协议的数量或总字节限制')
    result = []
    for fields in candidates:
        read = asyncio.create_task(asyncio.to_thread(read_reference, fields))
        outcome = (await join_tasks(read))[0]
        if isinstance(outcome, BaseException):
            raise outcome
        content, witness = outcome
        try:
            image = await verify_image_bytes(content, reference=True)
        except AssetImageModelError:
            raise HTTPException(422, '参考图片不能在限定资源内解码为支持的单帧 PNG/JPEG') from None
        if image.mime != fields['mime'] or image.sha256 != fields['sha256'] or ('width' in fields and (image.width != fields['width'] or image.height != fields['height'])):
            raise HTTPException(409, '参考图片的实际内容与保存证明不一致')
        result.append(VerifiedReference({**fields, 'width': image.width, 'height': image.height}, image, witness))
    return tuple(result)


async def recheck_prepared(db, project_id, prepared):
    """Repeat only metadata and stat checks under writer ownership; sending later uses the already verified bytes."""
    for reference in prepared.references:
        fields = reference.fields
        row = await db.get(ProductionMedia, fields['media_id'], populate_existing=True)
        if row is None or row.project_id != project_id or fields['project_id'] != project_id or any(getattr(row, column) != fields[key] for column, key in [('sha256', 'sha256'), ('size', 'byte_size'), ('mime', 'mime'), ('relative_path', 'relative_path')]):
            raise HTTPException(409, '原项目参考图片的保存证明已变化')
        try:
            if reference.file_stat != file_witness(reference_path(row.relative_path).stat(follow_symlinks=False)):
                raise ValueError()
        except (OSError, ValueError):
            raise HTTPException(409, '参考图片在准备期间已改变，未发起模型请求') from None


async def install_input_set(db, job, project_id, prepared):
    """Seal a complete typed set in the same transaction as the actual attempt and its single project reservation."""
    if prepared.protocol.protocol == LEGACY_IMAGE_PROTOCOL:
        if prepared.references or prepared.response_format != 'b64_json':
            raise HTTPException(422, '文本图片协议不支持参考图或网址返回')
        return
    fields = [reference.fields for reference in prepared.references]
    if prepared.protocol.protocol not in {SEEDREAM_IMAGE_PROTOCOL,GPT_IMAGE_PROTOCOL}:
        raise HTTPException(422,'图片输入证明不支持未知协议')
    header_type,reference_type=(GptImageInputSet,GptImageReference) if prepared.protocol.protocol==GPT_IMAGE_PROTOCOL else (AssetImageInputSet,AssetImageReference)
    header = header_type(job_id=job.id, project_id=project_id,
        protocol=prepared.protocol.protocol, profile=prepared.protocol.profile, model_name=job.model_name,
        size=job.size, response_format=prepared.response_format, reference_count=len(fields), reference_hash=digest(fields),
        request_hash=job.request_hash, binding_hash=binding_digest(job.model_binding), sealed=False)
    db.add(header)
    await db.flush()
    db.add_all([reference_type(job_id=job.id, **value) for value in fields])
    await db.flush()
    header.sealed = True
    await db.flush()
