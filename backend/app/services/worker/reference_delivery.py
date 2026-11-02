"""Deliver only the fixed bytes of an original prepared video reference, without a session or provider credential."""
import asyncio
from dataclasses import dataclass, field, replace
import hashlib
import hmac
import re
import secrets
import time
import uuid

from fastapi import HTTPException
from sqlalchemy import update
from starlette.responses import Response

from app.core.contracts.seedance_reference_profile import checked_delivery_origin
from app.core.owned_tasks import join_tasks
from app.models.execution import ExecutionJob
from app.models.video_reference_inputs import ExecutionReferenceDelivery
from app.services.worker.database_operation import joined_database
from app.services.worker.execution_budget import transaction
from app.services.worker.video_reference_inputs import read_owned_reference, saved_video_input_set


@dataclass(frozen=True, slots=True)
class ReferenceDeliveryIntent:
    """Ephemeral raw tokens reach only the original request body; the database stores their digest and exact attempt scope."""
    id: str
    job_id: str
    position: int
    project_id: str
    created_at: float
    token: str = field(repr=False)


def prepare_reference_deliveries(prepared, job_id: str, origin: str | None, *, trusted_local_origin=None, clock=time.time):
    """Prepare bounded wire URLs in memory before writer ownership, without publishing or granting any read capability yet."""
    checked_delivery_origin(origin, trusted_local_origin=trusted_local_origin)
    references, intents = [], []
    for item in prepared.references:
        if item.fields['role'] != 'reference_video':
            references.append(item.wire_proof)
            continue
        if origin is None:
            raise HTTPException(503, '参考视频需要独立配置的素材交付服务，未接受生成')
        token, identifier = secrets.token_hex(32), uuid.uuid4().hex
        intent = ReferenceDeliveryIntent(identifier, job_id, item.fields['position'], prepared.project_id, clock(), token)
        references.append(replace(item.wire_proof, delivery_url=origin+'/api/v1/execution-reference-media/'+identifier+'?token='+token))
        intents.append(intent)
    return tuple(references), tuple(intents)


async def install_reference_deliveries(db, job, attempt_id: str, intents) -> None:
    """Join capabilities to this actual newly prepared attempt inside its existing unknown-charge transaction."""
    for intent in intents:
        if intent.job_id != job.id or intent.project_id != job.project_id:
            raise HTTPException(409, '参考素材交付与原任务不符')
        db.add(ExecutionReferenceDelivery(id=intent.id, job_id=job.id, position=intent.position, project_id=job.project_id,
            attempt_id=attempt_id, token_sha256=hashlib.sha256(intent.token.encode('ascii')).hexdigest(),
            created_at=intent.created_at, expires_at=intent.created_at+3600, access_count=0))
    await db.flush()


class ScopedReferenceResponse(Response):
    """Hold the one-media response lane through real ASGI send/disconnect cleanup, not just file reading."""
    def __init__(self, content, *, release, **options):
        """The service transfers one acquired lock to this response after every failure path has been guarded."""
        super().__init__(content, **options)
        self.release = release

    async def __call__(self, scope, receive, send):
        """Release ownership even when the downstream client disconnects or ASGI send raises."""
        try:
            await super().__call__(scope, receive, send)
        finally:
            self.release()


def single_range(value: str | None, size: int):
    """Interpret exactly one standard byte range over already authorized fixed bytes."""
    if value is None:
        return 0, size-1, 200
    match = re.fullmatch(r'bytes=(\d*)-(\d*)', value) if isinstance(value, str) and len(value) <= 128 else None
    if not match or not any(match.groups()):
        raise HTTPException(416, '参考素材范围无效', headers={'Content-Range': f'bytes */{size}'})
    left, right = match.groups()
    if left:
        start = int(left)
        end = min(int(right), size-1) if right else size-1
    else:
        suffix = int(right)
        start, end = max(0, size-suffix), size-1
        if suffix == 0:
            start = size
    if start >= size or end < start:
        raise HTTPException(416, '参考素材范围无效', headers={'Content-Range': f'bytes */{size}'})
    return start, end, 206


class ReferenceMediaDelivery:
    """Read from one configured worker database only; unguessable capabilities grant one fixed video, never project browsing."""
    def __init__(self, factory, *, clock=time.time):
        """Construction does no database/file/network I/O or automatic media publication."""
        self.factory, self.clock = factory, clock
        self.lane = asyncio.Lock()

    @joined_database
    async def claim(self, delivery_id, token):
        """Authorize and count one original-attempt access in a short transaction before touching private file bytes."""
        unavailable = HTTPException(404, '参考素材不可用')
        async with self.factory() as db:
            async with transaction(db):
                row = await db.get(ExecutionReferenceDelivery, delivery_id)
                now = self.clock()
                if (row is None or not hmac.compare_digest(row.token_sha256, hashlib.sha256(token.encode('ascii')).hexdigest())
                        or not row.created_at <= now < row.expires_at or row.access_count >= 16):
                    raise unavailable
                job = await db.get(ExecutionJob, row.job_id)
                if (job is None or job.project_id != row.project_id or job.cancel_requested
                        or job.state not in {'submitting', 'submission_unknown', 'running', 'downloading'}):
                    raise unavailable
                _selected, fields = await saved_video_input_set(db, job)
                if row.position >= len(fields) or fields[row.position]['role'] != 'reference_video':
                    raise unavailable
                change = await db.execute(update(ExecutionReferenceDelivery).where(
                    ExecutionReferenceDelivery.id == row.id, ExecutionReferenceDelivery.access_count == row.access_count,
                    ExecutionReferenceDelivery.access_count < 16).values(access_count=row.access_count+1))
                if change.rowcount != 1:
                    raise unavailable
                return fields[row.position]

    async def response(self, delivery_id, token, *, method='GET', range_header=None):
        """Join the actual file read, verify original bytes and preserve lane ownership until the complete response is sent."""
        if (not isinstance(delivery_id, str) or re.fullmatch(r'[a-f0-9]{32}', delivery_id) is None
                or not isinstance(token, str) or re.fullmatch(r'[a-f0-9]{64}', token) is None or method not in {'GET', 'HEAD'}):
            raise HTTPException(404, '参考素材不可用')
        if self.lane.locked():
            raise HTTPException(429, '参考素材读取繁忙', headers={'Retry-After': '1'})
        await self.lane.acquire()
        try:
            fields = await self.claim(delivery_id, token)
            start, end, status = single_range(range_header, fields['byte_size'])
            task = asyncio.create_task(asyncio.to_thread(read_owned_reference, fields))
            result = (await join_tasks(task))[0]
            if isinstance(result, BaseException):
                raise result
            content, _witness = result
            headers = {'Cache-Control': 'no-store', 'Referrer-Policy': 'no-referrer', 'Accept-Ranges': 'bytes',
                'X-Content-Type-Options': 'nosniff', 'Content-Length': str(end-start+1)}
            if status == 206:
                headers['Content-Range'] = f"bytes {start}-{end}/{len(content)}"
            data = b'' if method == 'HEAD' else content if status == 200 else content[start:end+1]
            return ScopedReferenceResponse(data, status_code=status, media_type='video/mp4', headers=headers, release=self.lane.release)
        except BaseException:
            self.lane.release()
            raise
