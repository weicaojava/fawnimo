"""Prepare explicitly chosen original-project media outside writer ownership for the new video profile."""
import asyncio
from dataclasses import dataclass, field
import hashlib
import os
from pathlib import Path, PurePosixPath
import stat

from fastapi import HTTPException
from sqlalchemy import select

from app.config import settings
from app.core.contracts.seedance_references import (
    REFERENCE_PROTOCOL, PreparedReference, VideoReferenceSelection, snapshot_reference_selection,
)
from app.core.integrations.verified_image import verify_image_bytes
from app.core.integrations.verified_video import verify_video_bytes
from app.core.owned_tasks import join_tasks
from app.models.production import ProductionMedia
from app.models.model_charge import ModelCallCharge
from app.models.native_video_charge import NativeVideoJobCharge
from app.models.video_reference_inputs import VideoReferenceInputSet, VideoReferenceInput as StoredVideoReferenceInput
from app.services.worker.model_job_charges import binding_digest

SAVED_FIELDS = ('project_id', 'media_id', 'position', 'role', 'sha256', 'byte_size', 'mime', 'relative_path',
    'width', 'height', 'duration_us', 'frames', 'fps', 'frame_clock_sha256', 'has_audio', 'audio_samples', 'audio_channels')


class NativeReferenceInputError(HTTPException):
    """A queued job's original conditions are unavailable; it cannot prepare or transmit a chargeable request."""
    def __init__(self):
        """Keep source failures fixed and path-free for durable cancellation and release of a known unsent hold."""
        super().__init__(409, '原任务的参考素材或完整证明已变化，未发起模型请求')


@dataclass(frozen=True, slots=True)
class VerifiedVideoInput:
    """Keep exact decoded bytes private until the original job acquires authority to transmit or deliver them."""
    fields: dict
    wire_proof: PreparedReference
    file_stat: tuple
    content: bytes = field(repr=False)


@dataclass(frozen=True, slots=True)
class PreparedVideoInputs:
    """A server-created complete ordered set; no HTTP caller can provide its decoded or file witnesses."""
    project_id: str
    selection: VideoReferenceSelection
    references: tuple[VerifiedVideoInput, ...]


def owned_reference_path(relative: str) -> Path:
    """Accept only generated project-media paths below the configured root, with no redirection."""
    if (not isinstance(relative, str) or not relative or '\\' in relative or ':' in relative
            or PurePosixPath(relative).is_absolute() or any(part in {'', '.', '..'} for part in relative.split('/'))):
        raise HTTPException(409, '参考素材存储证明无效')
    root = Path(settings.production_media_root).resolve()
    path = root/relative
    if path.resolve() != path or not path.is_relative_to(root):
        raise HTTPException(409, '参考素材存储证明无效')
    return path


def file_witness(item) -> tuple:
    """Use file identity and clocks as an extra replacement fence, never as a substitute for the expected SHA."""
    return item.st_dev, item.st_ino, item.st_size, item.st_mtime_ns, item.st_ctime_ns


def read_owned_reference(fields: dict) -> tuple[bytes, tuple]:
    """Read exact bounded regular-file bytes once, comparing path and opened descriptor identities."""
    try:
        path = owned_reference_path(fields['relative_path'])
        before = path.stat(follow_symlinks=False)
        maximum = (32 if fields['role'] == 'reference_video' else 4)*1024*1024
        if not stat.S_ISREG(before.st_mode) or before.st_size != fields['byte_size'] or not 0 < before.st_size <= maximum:
            raise ValueError()
        with path.open('rb') as stream:
            opened = os.fstat(stream.fileno())
            content = stream.read(maximum+1)
            after = os.fstat(stream.fileno())
        witness = file_witness(before)
        # Windows descriptor/path ctime can have different meanings. Preserve
        # the full path witness, while comparing descriptor identity/size/mtime.
        if (not stat.S_ISREG(opened.st_mode) or witness[:4] != file_witness(opened)[:4]
                or witness[:4] != file_witness(after)[:4] or witness != file_witness(path.stat(follow_symlinks=False))
                or len(content) != fields['byte_size'] or hashlib.sha256(content).hexdigest() != fields['sha256']):
            raise ValueError()
        return content, witness
    except (OSError, ValueError):
        raise HTTPException(409, '参考素材已缺失、替换或改变，未发起模型请求') from None


async def input_candidates(db, project_id: str, selection: VideoReferenceSelection) -> list[dict]:
    """Project only metadata for the explicit selected inputs, never all pictures attached to an asset version."""
    result = []
    for position, selected in enumerate(selection.references):
        row = await db.get(ProductionMedia, selected.media_id)
        if row is None or row.project_id != project_id:
            raise HTTPException(404, '原项目的参考素材不存在')
        if row.sha256 != selected.expected_sha256:
            raise HTTPException(409, '参考素材内容版本已变化')
        allowed = {'video/mp4'} if selected.kind == 'video' else {'image/png', 'image/jpeg'}
        maximum = (32 if selected.kind == 'video' else 4)*1024*1024
        if row.mime not in allowed or not 0 < row.size <= maximum:
            raise HTTPException(422, '参考素材类型或大小超出本协议范围')
        result.append({'project_id': project_id, 'media_id': row.id, 'position': position, 'role': selected.role,
            'sha256': row.sha256, 'byte_size': row.size, 'mime': row.mime, 'relative_path': row.relative_path})
    return result


async def prepare_video_inputs(factory, project_id: str, selection: VideoReferenceSelection) -> PreparedVideoInputs:
    """Close the read session before decoding; the caller owns a bounded runtime preparation lane."""
    if not isinstance(selection, VideoReferenceSelection):
        raise HTTPException(422, '请明确选择视频参考模式')
    async with factory() as db:
        candidates = await input_candidates(db, project_id, selection)
    cache, references = {}, []
    for fields in candidates:
        key = (fields['media_id'], fields['sha256'])
        if key not in cache:
            task = asyncio.create_task(asyncio.to_thread(read_owned_reference, fields))
            result = (await join_tasks(task))[0]
            if isinstance(result, BaseException):
                raise result
            content, witness = result
            try:
                if fields['role'] == 'reference_video':
                    actual = await verify_video_bytes(content)
                    proof = PreparedReference(project_id, fields['media_id'], actual.sha256, actual.mime, actual.byte_size,
                        actual.width, actual.height, duration_ms=actual.duration_us//1000, fps=actual.fps,
                        frame_clock_sha256=actual.frame_clock_sha256, has_audio=actual.has_audio)
                    facts = {'width': actual.width, 'height': actual.height, 'duration_us': actual.duration_us,
                        'frames': actual.frames, 'fps': actual.fps, 'frame_clock_sha256': actual.frame_clock_sha256,
                        'has_audio': actual.has_audio, 'audio_samples': actual.audio_samples, 'audio_channels': actual.audio_channels}
                else:
                    actual = await verify_image_bytes(content, reference=True)
                    if not 300 <= actual.width <= 4096 or not 300 <= actual.height <= 4096 or not 0.4 <= actual.width/actual.height <= 2.5:
                        raise ValueError()
                    if selection.mode != 'reference' and actual.width*16 != actual.height*9:
                        raise ValueError()
                    proof = PreparedReference(project_id, fields['media_id'], actual.sha256, actual.mime, len(content),
                        actual.width, actual.height, content=content)
                    facts = {'width': actual.width, 'height': actual.height}
                if actual.sha256 != fields['sha256'] or actual.mime != fields['mime']:
                    raise ValueError()
            except Exception:
                raise HTTPException(422, '参考素材的实际内容、时长或画幅不符合所选模式') from None
            cache[key] = content, witness, proof, facts
        content, witness, proof, facts = cache[key]
        references.append(VerifiedVideoInput({**fields, **facts}, proof, witness, content))
    return PreparedVideoInputs(project_id, selection, tuple(references))


async def recheck_video_inputs(db, prepared: PreparedVideoInputs) -> None:
    """Check only current metadata and stat in writer ownership; never reread or decode under the project reservation lock."""
    for reference in prepared.references:
        fields = reference.fields
        row = await db.get(ProductionMedia, fields['media_id'], populate_existing=True)
        if (row is None or row.project_id != prepared.project_id or fields['project_id'] != prepared.project_id
                or any(getattr(row, column) != fields[key] for column, key in [
                    ('sha256', 'sha256'), ('size', 'byte_size'), ('mime', 'mime'), ('relative_path', 'relative_path')])):
            raise HTTPException(409, '原项目参考素材的元数据已变化')
        try:
            if file_witness(owned_reference_path(row.relative_path).stat(follow_symlinks=False)) != reference.file_stat:
                raise ValueError()
        except (OSError, ValueError):
            raise HTTPException(409, '参考素材在准备期间已变化，未发起模型请求') from None


def saved_fields(value) -> dict:
    """Use the same complete nullable field domain when hashing prepared data and persisted typed rows."""
    return {name: value.get(name) if isinstance(value, dict) else getattr(value, name) for name in SAVED_FIELDS}


async def install_video_input_set(db, job, prepared: PreparedVideoInputs) -> None:
    """Seal original inputs in the caller's actual native-job/financial transaction; never commit or reserve separately."""
    from app.services.worker.native_video_charges import verify_plan
    if not db.in_transaction() or db.bind.dialect.name != 'sqlite':
        raise HTTPException(409, '视频参考输入需要同一SQLite事务')
    try:
        selected = snapshot_reference_selection(job.snapshot)
    except (TypeError, ValueError):
        raise HTTPException(409, '参考输入请求证明无效') from None
    link = await db.get(NativeVideoJobCharge, job.id)
    if link is None or link.project_id != job.project_id:
        raise HTTPException(409, '参考输入缺少原任务费用绑定')
    batch, _calls = await verify_plan(db, link.batch_id)
    charge = await db.get(ModelCallCharge, job.id)
    if (charge is None or job.project_id != prepared.project_id or selected != prepared.selection or job.state != 'queued'
            or batch.context['connection'].get('protocol') != REFERENCE_PROTOCOL or charge.dispatch_state != 'planned'
            or charge.price_snapshot.get('input_protocol') != REFERENCE_PROTOCOL):
        raise HTTPException(409, '参考输入与原任务、协议或项目不符')
    await recheck_video_inputs(db, prepared)
    fields = [saved_fields(reference.fields) for reference in prepared.references]
    header = VideoReferenceInputSet(job_id=job.id, project_id=job.project_id, protocol=REFERENCE_PROTOCOL,
        mode=selected.mode, reference_count=len(fields), reference_hash=binding_digest(fields),
        selection_hash=binding_digest(selected.model_dump(mode='json')), request_hash=job.request_hash,
        snapshot_hash=binding_digest(job.snapshot), binding_hash=link.execution_binding_hash,
        connection_hash=charge.binding_hash, profile_hash=batch.context['profile']['profile_hash'], sealed=False)
    db.add(header)
    await db.flush()
    db.add_all([StoredVideoReferenceInput(job_id=job.id, **item) for item in fields])
    await db.flush()
    header.sealed = True
    await db.flush()


async def saved_video_input_set(db, job):
    """Refuse partial/missing/corrupt new sets and prevent old text jobs from acquiring extra input authority."""
    from app.services.worker.native_video_charges import verify_plan
    invalid = HTTPException(409, '原任务完整参考素材证明不一致，未发起请求')
    header = await db.get(VideoReferenceInputSet, job.id, populate_existing=True)
    rows = list((await db.scalars(select(StoredVideoReferenceInput)
        .where(StoredVideoReferenceInput.job_id == job.id).order_by(StoredVideoReferenceInput.position))).all())
    if 'video_reference' not in job.snapshot:
        if header is not None or rows:
            raise invalid
        return None, []
    try:
        selected = snapshot_reference_selection(job.snapshot)
    except (TypeError, ValueError):
        raise invalid from None
    link = await db.get(NativeVideoJobCharge, job.id)
    if link is None or header is None or not header.sealed or link.project_id != job.project_id:
        raise invalid
    batch, _calls = await verify_plan(db, link.batch_id)
    charge = await db.get(ModelCallCharge, job.id)
    fields = [saved_fields(row) for row in rows]
    if (charge is None or batch.context['connection'].get('protocol') != REFERENCE_PROTOCOL or header.protocol != REFERENCE_PROTOCOL
            or charge.price_snapshot.get('input_protocol') != REFERENCE_PROTOCOL
            or header.project_id != job.project_id or header.mode != selected.mode
            or header.reference_count != len(rows) or len(rows) != len(selected.references)
            or [row.position for row in rows] != list(range(len(rows)))
            or header.reference_hash != binding_digest(fields)
            or header.selection_hash != binding_digest(selected.model_dump(mode='json'))
            or header.request_hash != job.request_hash or header.snapshot_hash != binding_digest(job.snapshot)
            or header.binding_hash != link.execution_binding_hash or header.connection_hash != charge.binding_hash
            or header.profile_hash != batch.context['profile'].get('profile_hash')):
        raise invalid
    for row, expected in zip(rows, selected.references):
        if (row.project_id, row.media_id, row.sha256, row.role) != (job.project_id, expected.media_id, expected.expected_sha256, expected.role):
            raise invalid
    return selected, fields


async def prepare_saved_video_inputs(factory, job) -> PreparedVideoInputs | None:
    """Reload typed original authority, close its DB session, and decode only matching current original-project bytes."""
    async with factory() as db:
        selected, fields = await saved_video_input_set(db, job)
    if selected is None:
        return None
    prepared = await prepare_video_inputs(factory, job.project_id, selected)
    if [saved_fields(reference.fields) for reference in prepared.references] != fields:
        raise HTTPException(409, '视频参考素材的实际内容与原保存证明不一致')
    return prepared
