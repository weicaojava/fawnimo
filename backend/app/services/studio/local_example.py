"""Bounded model-free example orchestration for one permanently scoped worker."""
import asyncio
from pathlib import Path
import secrets
import time

from fastapi import HTTPException
from sqlalchemy import func, select, update
from sqlalchemy.exc import IntegrityError

from app.config import settings
from app.core.contracts.local_example import example_identity
from app.core.owned_tasks import join_tasks
from app.models.editing import EditDraft, TakeOrigin
from app.models.local_example import LocalExampleRequest, LocalExampleReady, LocalExampleMedia, LocalExampleTake
from app.models.production import ProductionTake
from app.models.studio import Project, Chapter
from app.services.editing.common import plan_script
from app.services.shot_plan.compiler import media_evidence
from app.services.shot_plan.service import owned_plan
from app.services.studio.local_example_install import install, result_view
from app.services.studio.local_example_media import prepare_media
from app.services.worker.execution_budget import transaction
from app.services.worker.database_operation import joined_database

LEASE = 60


async def join_task(task, *, cancel=False):
    """Join a single owned task without confusing its outcome with cancellation of the caller."""
    return await join_tasks(task,cancel=cancel)


class LocalExampleService:
    """Keep one render lane for the whole worker; no unbounded queue or client-owned concurrency setting."""

    def __init__(self, factory, workspace_id):
        """Bind the service once to the worker's database and fixed server identity."""
        self.factory, self.workspace_id = factory, workspace_id
        self.lane = asyncio.Lock()

    def scope(self, actor, request_key):
        """Refuse legacy mode or a mismatching worker before deriving any target identity."""
        if not self.workspace_id or settings.workspace_id != self.workspace_id:
            raise HTTPException(503,'原创可导出示例需要已登录的独立工作空间')
        return example_identity(self.workspace_id,actor,request_key)

    async def request_row(self, db, identity):
        """Match the complete stored intent before every recovery, never only a submitted project ID."""
        row = await db.get(LocalExampleRequest,identity['id'])
        if row is not None and any(getattr(row,key)!=value for key,value in identity.items()):
            raise HTTPException(409,'原示例请求身份或版本不一致，请核对原请求')
        return row

    async def replay(self, db, row):
        """Verify retained original history and bytes, preserving every later selection and editable document."""
        ready = await db.get(LocalExampleReady,row.id)
        if ready is None or (ready.project_id,ready.chapter_id)!=(row.project_id,row.chapter_id):
            raise HTTPException(409,'原示例回执缺失或归属改变，未修复或覆盖已有内容')
        project, chapter, draft = await db.get(Project,row.project_id), await db.get(Chapter,row.chapter_id), await db.get(EditDraft,ready.draft_id)
        if project is None or chapter is None or chapter.project_id!=row.project_id or draft is None or (draft.project_id,draft.chapter_id)!=(row.project_id,row.chapter_id):
            raise HTTPException(409,'原示例工程或编辑草稿已缺失，未重新建立')
        plan = await owned_plan(db,row.project_id,ready.plan_id)
        await plan_script(db,row.project_id,plan)
        if plan.payload['script_snapshot_id']!=ready.script_snapshot_id or plan.payload['chapter_id']!=row.chapter_id or plan.payload['binding'] is not None:
            raise HTTPException(409,'原示例计划身份不一致，未继续修改')
        references = (await db.execute(select(LocalExampleMedia).where(LocalExampleMedia.request_id==row.id))).scalars().all()
        takes = (await db.execute(select(LocalExampleTake).where(LocalExampleTake.request_id==row.id))).scalars().all()
        if len(references)!=14 or len(takes)!=7:
            raise HTTPException(409,'原示例历史引用不完整，未自动修复')
        frozen = {item['id']:item for item in plan.payload['media']}
        entries = {entry['shot_id']:entry for entry in plan.payload['entries']}
        for item in takes:
            take, origin = await db.get(ProductionTake,item.take_id), await db.get(TakeOrigin,item.take_id)
            if take is None or origin is None or take.project_id!=row.project_id or origin.project_id!=row.project_id or origin.plan_id!=ready.plan_id or origin.origin_kind!='user_import' or origin.job_id is not None or origin.shot_id!=take.shot_id or take.shot_id not in entries or origin.entry_hash!=entries[take.shot_id]['entry_hash']:
                raise HTTPException(409,'原示例采用来源不完整，未重新采用')
            reference = next((ref for ref in references if ref.kind=='video' and ref.clip_index==item.clip_index),None)
            if reference is None or reference.media_id!=take.media_id:
                raise HTTPException(409,'原示例片段与媒体引用不一致')
            frozen[take.media_id] = origin.evidence
        for reference in references:
            actual, _proof = await media_evidence(db,row.project_id,reference.media_id)
            expected = frozen.get(reference.media_id)
            if expected is None or any(actual[key]!=expected[key] for key in ('id','sha256','size','mime')):
                raise HTTPException(409,'原示例媒体缺失或改变，未替换已有素材')
        return result_view(ready,replayed=True)

    @joined_database
    async def ready_result(self, identity):
        """Close the read session before delivering cancellation or admitting new render work."""
        async with self.factory() as db:
            row = await self.request_row(db,identity)
            return await self.replay(db,row) if row is not None and row.state=='ready' else None

    @joined_database
    async def claim(self, identity, *, witness=None):
        """Consume an attempt only after the worker lane is held; retain exactly one absent reserved target."""
        now, token = int(time.time()), secrets.token_hex(32)
        async with self.factory() as db:
            async with transaction(db):
                row = await self.request_row(db,identity)
                if row is not None:
                    if row.state=='ready':
                        return None
                    if row.state=='running' and row.lease_until>now:
                        raise HTTPException(409,'此示例正在建立，请稍后用原请求核对')
                    if row.attempts>=3:
                        raise HTTPException(409,'此示例已达到3次本地尝试上限，已保留原请求与所有内容')
                    if await db.get(Project,row.project_id) is not None or await db.get(Chapter,row.chapter_id) is not None:
                        raise HTTPException(409,'示例保留目标已有内容，未覆盖或修复已有工程')
                    row.state, row.attempts, row.lease_token, row.lease_until = 'running',row.attempts+1,token,now+LEASE
                    row.reason, row.updated_at = '',now
                else:
                    if await db.scalar(select(func.count()).select_from(LocalExampleRequest))>=20:
                        raise HTTPException(429,'此空间已达到本地示例请求上限，请继续编辑已有示例')
                    if await db.get(Project,identity['project_id']) is not None or await db.get(Chapter,identity['chapter_id']) is not None:
                        raise HTTPException(409,'示例目标已存在，未修改任何工程')
                    db.add(LocalExampleRequest(**identity,state='running',attempts=1,lease_token=token,lease_until=now+LEASE,
                        reason='',created_at=now,updated_at=now))
        # The caller may be cancelled after COMMIT but before this decorated
        # operation returns. Publish ownership only after the Session has closed.
        if witness is not None:
            witness['token'] = token
        return token

    async def heartbeat(self, identifier, token, stop):
        """Renew only this live owner while its renderer/probes run outside writer locks."""
        while True:
            try:
                await asyncio.wait_for(stop.wait(),timeout=10)
                return
            except asyncio.TimeoutError:
                pass
            await self.renew(identifier,token)

    @joined_database
    async def renew(self, identifier, token):
        """Join the short heartbeat transaction and session close before reporting lease loss."""
        now = int(time.time())
        async with self.factory() as db:
            async with transaction(db):
                changed = await db.execute(update(LocalExampleRequest).where(LocalExampleRequest.id==identifier,
                    LocalExampleRequest.state=='running',LocalExampleRequest.lease_token==token,LocalExampleRequest.lease_until>now)
                    .values(lease_until=now+LEASE,updated_at=now))
                if changed.rowcount!=1:
                    raise HTTPException(409,'示例处理归属已失效，已停止本次任务')

    async def prepare(self, identifier, token):
        """Race preparation against lease loss, joining both owned tasks on cancellation or failure."""
        stop = asyncio.Event()
        render = asyncio.create_task(asyncio.wait_for(prepare_media(identifier,token),timeout=165))
        heartbeat = asyncio.create_task(self.heartbeat(identifier,token,stop))
        try:
            done, _pending = await asyncio.wait((render,heartbeat),return_when=asyncio.FIRST_COMPLETED)
            if heartbeat in done:
                await heartbeat
                raise HTTPException(409,'示例任务归属已失效')
            return await render
        finally:
            stop.set()
            if not render.done() and not render.cancelling():
                render.cancel()
            await join_tasks(render,heartbeat)

    @joined_database
    async def uncertain(self, identifier, token):
        """Release this attempt only; a stale owner cannot change a winner's receipt or lease."""
        async with self.factory() as db:
            async with transaction(db):
                await db.execute(update(LocalExampleRequest).where(LocalExampleRequest.id==identifier,
                    LocalExampleRequest.state=='running',LocalExampleRequest.lease_token==token)
                    .values(state='failedunknown',lease_token=None,lease_until=None,updated_at=int(time.time()),
                        reason='本次本地处理未能完整确认；已保留请求及私有素材，未修改已有工程'))

    @joined_database
    async def finish(self, identity, token, resources, proofs, videos):
        """Join final commit/rollback and physical close even if cancellation arrives at the commit boundary."""
        async with self.factory() as db:
            async with transaction(db):
                row = await self.request_row(db,identity)
                return await install(db,row,token,resources,proofs,videos)

    async def create(self, actor, request_key):
        """Replay first; otherwise render once and publish the new project plus typed receipt atomically."""
        identity = self.scope(actor,request_key)
        previous = await self.ready_result(identity)
        if previous is not None:
            return previous
        if settings.workspace_runtime_locked or not settings.editing_export_enabled or not Path(settings.production_probe_executable).is_file():
            raise HTTPException(503,'当前空间的本地媒体处理尚未开放；已有文字示例仍可继续编辑')
        if self.lane.locked():
            raise HTTPException(429,'本空间正在建立另一个原创示例，请稍后重试原请求')
        await self.lane.acquire()
        token = None
        claim_witness = {}
        try:
            token = await self.claim(identity,witness=claim_witness)
            if token is None:
                return await self.ready_result(identity)
            resources, proofs, videos = await self.prepare(identity['id'],token)
            return await self.finish(identity,token,resources,proofs,videos)
        except BaseException as error:
            owned_token = token or claim_witness.get('token')
            if owned_token is not None:
                cleanup = asyncio.create_task(self.uncertain(identity['id'],owned_token))
                await join_task(cleanup)
            if isinstance(error,IntegrityError):
                raise HTTPException(409,'示例保存遇到身份或内容冲突，未保存部分工程；请保留原请求核对') from None
            raise
        finally:
            self.lane.release()

    @joined_database
    async def status(self, actor, request_key):
        """Inspect existing state without rendering, consuming an attempt or repairing a restored/deleted example."""
        identity = self.scope(actor,request_key)
        async with self.factory() as db:
            row = await self.request_row(db,identity)
            available = not settings.workspace_runtime_locked and settings.editing_export_enabled and Path(settings.production_probe_executable).is_file()
            return {'available':available,'message':'原创示例可在本地建立，无模型调用' if available else '当前本地媒体处理未开放',
                'state':row.state if row else None,'attempts':row.attempts if row else 0,
                'result':await self.replay(db,row) if row is not None and row.state=='ready' else None}
