"""Owned source-bound Video DNA drafts, immutable snapshots and inert JSON imports."""
import hashlib
import json
import uuid
from fastapi import HTTPException
from pydantic import ValidationError
from sqlalchemy import select,update
from app.core.contracts.video_dna import DnaCreate,DnaImport,DnaSnapshotPayload,safe_json,SAFE
from app.models.video_dna import DnaDraft,DnaSnapshot
from app.models.reverse import ReverseAnalysis
from app.models.production import ProductionMedia,ProductionAsset,AssetVersion
from app.services.worker.execution_budget import transaction
from app.services.video_dna.source import owned_analysis,source_from_analysis,verify_source,source_current,issue
from app.services.video_dna.document import template,validate_document,readiness,asset_versions
from app.services.script_preparation.service import canonical,content_hash


MAX_PORTABLE_BYTES=2*1024*1024


def portable_snapshot(identifier,payload,digest=None):
    """Apply one exact byte budget to the complete downloadable/importable envelope."""
    envelope={'id':identifier,'content_hash':digest if digest is not None else content_hash(payload),'payload':payload}
    if len(canonical(envelope).encode('utf8'))>MAX_PORTABLE_BYTES:raise HTTPException(409,'完整DNA快照JSON封包超过2MiB，不能锁定或下载')
    return envelope


def request_hash(body,domain):
    """Bind the original normalized user intent, never a later draft or dynamic template."""
    if not body.request_key.strip():raise HTTPException(422,'请求标识不能为空')
    return content_hash({'domain':domain,'body':body.model_dump()})


async def replay(db,project_id,key,digest):
    """Resolve exact idempotency before checking current bytes, cuts or CAS eligibility."""
    row=(await db.execute(select(DnaDraft).where(DnaDraft.project_id==project_id,DnaDraft.request_key==key))).scalar_one_or_none()
    if row and row.initial_hash!=digest:raise HTTPException(409,'请求标识已用于另一DNA创建或导入意图')
    return row


async def owned_draft(db,project_id,draft_id):
    """Historical drafts remain readable even when current source cuts or files change."""
    row=(await db.execute(select(DnaDraft).where(DnaDraft.id==draft_id,DnaDraft.project_id==project_id))).scalar_one_or_none()
    if row is None:raise HTTPException(404,'本工程DNA草稿不存在')
    return row


async def owned_snapshot(db,project_id,snapshot_id):
    """Validate complete immutable canonical content without following new source revisions."""
    row=(await db.execute(select(DnaSnapshot).where(DnaSnapshot.id==snapshot_id,DnaSnapshot.project_id==project_id))).scalar_one_or_none()
    if row is None:raise HTTPException(404,'本工程DNA快照不存在')
    try:
        safe_json(row.payload)
        if content_hash(row.payload)!=row.content_hash:raise ValueError('Snapshot hash mismatch')
        DnaSnapshotPayload.model_validate(row.payload)
    except (ValueError,ValidationError):raise HTTPException(409,'DNA快照内容或整数版本校验失败') from None
    return row


async def bounded(db,query,limit,label):
    """Return complete options or an explicit limit error, never a hidden first-page subset."""
    rows=list((await db.execute(query.limit(limit+1))).scalars())
    if len(rows)>limit:raise HTTPException(409,label+'超过本版上限，请缩小工程范围')
    return rows


def import_document(content,source):
    """Extract inert v1 content, recompute embedded clocks and discard all claimed qualifications."""
    try:
        if len(canonical(content).encode())>2*1024*1024:raise HTTPException(413,'导入JSON超过2MiB')
        payload=content.get('payload',content)
        if not isinstance(payload,dict):raise ValueError('Invalid envelope')
        if 'document' in payload:
            if 'schema_version' in payload and (type(payload['schema_version']) is not int or payload['schema_version']!=1):raise ValueError('Unsupported envelope schema')
            document=payload['document'];imported=payload.get('source')
        else:document=payload;imported=None
        if imported is not None:
            if not isinstance(imported,dict):raise ValueError('Invalid source')
            frames=imported.get('frames')
            if not isinstance(frames,list):raise ValueError('Missing imported clock')
            safe_json(frames)
            cuts=imported.get('confirmed_cut_indices')
            if not isinstance(cuts,list) or not cuts or len(cuts)>256 or any(type(index) is not int or abs(index)>SAFE or index<0 or index>=len(frames) for index in cuts) or cuts[0]!=0 or any(left>=right for left,right in zip(cuts,cuts[1:])):raise ValueError('Invalid imported cut integers')
            clock=content_hash({'schema_version':1,'frames':frames})
            if clock!=imported.get('frame_clock_hash') or clock!=source['frame_clock_hash'] or imported.get('sha256')!=source['sha256'] or cuts!=source['confirmed_cut_indices']:raise HTTPException(409,'导入原片、实际帧时钟或确认切点与目标分析不一致')
        return validate_document(document,source)
    except (ValueError,TypeError,KeyError,ValidationError):raise HTTPException(422,'仅支持明确的v1 DNA文档或快照JSON；未来版本不自动迁移') from None


async def _create(factory,project_id,body,domain,imported=False):
    """Hash privately, then atomically recheck source/stat and register one initial intent."""
    digest=request_hash(body,domain)
    async with factory() as db:
        old=await replay(db,project_id,body.request_key,digest)
        if old:return old.id
        analysis=await owned_analysis(db,project_id,body.analysis_id)
        if analysis.revision!=body.expected_analysis_revision:raise HTTPException(409,'原分析修订已改变，请重新选择确认切点')
        source=source_from_analysis(analysis)
        data=import_document(body.content,source) if imported else validate_document(body.document.model_dump() if body.document is not None else template(source),source)
        proof=await verify_source(db,project_id,source)
        _,version_proof=await asset_versions(db,project_id,data)
    async with factory() as db:
        async with transaction(db):
            old=await replay(db,project_id,body.request_key,digest)
            if old:return old.id
            analysis=await owned_analysis(db,project_id,body.analysis_id)
            if source_from_analysis(analysis)!=source:raise HTTPException(409,'原分析、确认切点或来源已改变，请刷新')
            await verify_source(db,project_id,source,proof)
            await asset_versions(db,project_id,data,version_proof)
            identifier=str(uuid.uuid4());db.add(DnaDraft(id=identifier,project_id=project_id,analysis_id=analysis.id,source=source,request_key=body.request_key,initial_hash=digest,revision=1,document=data));return identifier


async def create_draft(factory,project_id,body):
    """Create an editable hypothesis; no source, production entity or execution state is modified."""
    return await _create(factory,project_id,body,'r2a-create-v1')


async def import_draft(factory,project_id,body):
    """Only a new draft is created; imported scores, identities and execution claims grant nothing."""
    return await _create(factory,project_id,body,'r2a-import-v1',imported=True)


async def patch_draft(factory,project_id,draft_id,body):
    """Preserve fixed source while applying full-document CAS and owned version validation."""
    async with factory() as db:
        draft=await owned_draft(db,project_id,draft_id)
        if draft.revision!=body.expected_revision:raise HTTPException(409,'DNA修订冲突，请刷新')
        data=validate_document(body.document,draft.source)
        _,proof=await asset_versions(db,project_id,data)
    async with factory() as db:
        async with transaction(db):
            draft=await owned_draft(db,project_id,draft_id)
            data=validate_document(data,draft.source);await asset_versions(db,project_id,data,proof)
            result=await db.execute(update(DnaDraft).where(DnaDraft.id==draft_id,DnaDraft.revision==body.expected_revision).values(document=data,revision=DnaDraft.revision+1))
            if not result.rowcount:raise HTTPException(409,'DNA修订冲突，请刷新')


async def freeze_draft(factory,project_id,draft_id,body):
    """Freeze exact source/current cuts and full version bytes, allowing explicit unknown claims."""
    async with factory() as db:
        draft=await owned_draft(db,project_id,draft_id)
        if draft.revision!=body.expected_revision:raise HTTPException(409,'DNA修订冲突，请刷新')
        old=(await db.execute(select(DnaSnapshot).where(DnaSnapshot.draft_id==draft.id,DnaSnapshot.draft_revision==draft.revision))).scalar_one_or_none()
        if old:return (await owned_snapshot(db,project_id,old.id)).id
        if not await source_current(db,project_id,draft.source):raise HTTPException(409,'当前确认切点或原分析已改变，请从当前分析新建DNA草稿')
        data=validate_document(draft.document,draft.source);problems=readiness(data,draft.source)
        if problems:raise HTTPException(409,problems[0]['message'])
        source_proof=await verify_source(db,project_id,draft.source)
        versions,version_proof=await asset_versions(db,project_id,data)
        payload=DnaSnapshotPayload(source=draft.source,document=data,asset_versions=versions).model_dump()
        digest=content_hash(payload)
        identifier=str(uuid.uuid4())
        portable_snapshot(identifier,payload,digest)
    async with factory() as db:
        async with transaction(db):
            draft=await owned_draft(db,project_id,draft_id)
            if draft.revision!=body.expected_revision:raise HTTPException(409,'DNA修订冲突，请刷新')
            old=(await db.execute(select(DnaSnapshot).where(DnaSnapshot.draft_id==draft.id,DnaSnapshot.draft_revision==draft.revision))).scalar_one_or_none()
            if old:return (await owned_snapshot(db,project_id,old.id)).id
            if not await source_current(db,project_id,draft.source):raise HTTPException(409,'锁定期间原确认切点已改变')
            await verify_source(db,project_id,draft.source,source_proof)
            current_versions,_=await asset_versions(db,project_id,draft.document,version_proof)
            current=DnaSnapshotPayload(source=draft.source,document=draft.document,asset_versions=current_versions).model_dump()
            if content_hash(current)!=digest:raise HTTPException(409,'DNA来源或版本在锁定期间改变，请重新核对')
            portable_snapshot(identifier,current,digest)
            db.add(DnaSnapshot(id=identifier,project_id=project_id,draft_id=draft.id,draft_revision=draft.revision,payload=current,content_hash=digest));return identifier


async def preflight_snapshot(db,project_id,snapshot_id):
    """Report byte availability separately from current-cut drift and content correctness."""
    row=await owned_snapshot(db,project_id,snapshot_id);source=row.payload['source'];problems=[];source_ok=True;versions_ok=True
    try:await verify_source(db,project_id,source)
    except HTTPException:source_ok=False;problems.append(issue('source_unavailable','原片缺失、改变或完整字节不可用','source'))
    try:
        versions,_=await asset_versions(db,project_id,row.payload['document'])
        if versions!=row.payload['asset_versions']:raise HTTPException(409,'生产版本字节事实改变')
    except HTTPException:versions_ok=False;problems.append(issue('version_unavailable','固定生产版本或参考媒体缺失、改变','asset_versions'))
    current=await source_current(db,project_id,source)
    if not current:problems.append(issue('source_changed','当前分析或确认切点已改变；历史内容仍保持原来源','source'))
    return {'usable':source_ok and versions_ok,'source_available':source_ok,'asset_versions_available':versions_ok,'source_current':current,'issues':problems,'human_declarations_only':True}


async def snapshot_view(db,project_id,snapshot_id):
    """Return the exact portable immutable JSON even if later analysis cuts changed."""
    row=await owned_snapshot(db,project_id,snapshot_id)
    return {'id':row.id,'content_hash':row.content_hash,'payload':row.payload}


async def draft_view(db,project_id,draft_id):
    """Return every controlled field and a truthful structural/current-source checklist."""
    row=await owned_draft(db,project_id,draft_id);problems=readiness(row.document,row.source)
    current=await source_current(db,project_id,row.source)
    if not current:problems.append(issue('source_changed','原确认切点或分析已改变，请新建草稿迁移内容','source'))
    try:
        await verify_source(db,project_id,row.source)
        await asset_versions(db,project_id,row.document)
    except HTTPException:problems.append(issue('bytes_unavailable','原片或生产版本参考媒体不可用'))
    snapshots=await bounded(db,select(DnaSnapshot).where(DnaSnapshot.draft_id==row.id).order_by(DnaSnapshot.draft_revision.desc()),200,'DNA版本')
    return {'id':row.id,'project_id':project_id,'analysis_id':row.analysis_id,'revision':row.revision,'source':row.source,'document':row.document,'readiness':{'can_freeze':not problems,'issues':problems,'source_current':current,'human_declarations_only':True},'snapshots':[{'id':s.id,'content_hash':s.content_hash,'draft_revision':s.draft_revision} for s in snapshots]}


async def catalog(db,project_id):
    """Read bounded real options without repeated media hashing or implicit source selection."""
    from app.services.studio.production import project_exists
    await project_exists(db,project_id)
    analyses=await bounded(db,select(ReverseAnalysis).where(ReverseAnalysis.project_id==project_id).order_by(ReverseAnalysis.id),200,'视频分析')
    drafts=await bounded(db,select(DnaDraft).where(DnaDraft.project_id==project_id).order_by(DnaDraft.id),200,'DNA草稿')
    rows=list((await db.execute(select(AssetVersion,ProductionAsset).join(ProductionAsset,ProductionAsset.id==AssetVersion.asset_id).where(AssetVersion.project_id==project_id,ProductionAsset.project_id==project_id,ProductionAsset.kind.in_(('character','scene','prop'))).order_by(AssetVersion.asset_id,AssetVersion.revision).limit(501))).all())
    if len(rows)>500:raise HTTPException(409,'生产版本超过500项，请缩小工程范围')
    options=[]
    for row in analyses:
        problems=[];source=None
        try:source=source_from_analysis(row)
        except HTTPException as exc:problems.append(issue('analysis_unavailable',str(exc.detail)))
        media=await db.get(ProductionMedia,row.media_id)
        if media is None or media.project_id!=project_id or media.sha256!=row.source_hash:problems.append(issue('source_scope','原片归属或哈希元数据不符'))
        options.append({'id':row.id,'media_id':row.media_id,'name':media.name if media else row.media_id,'state':row.state,'revision':row.revision,'source_hash':row.source_hash,'frame_count':len((row.manifest or {}).get('video',{}).get('frames',[])),'confirmed_cut_indices':source['confirmed_cut_indices'] if source else [],'can_create':not problems,'issues':problems})
    return {'analyses':options,'drafts':[{'id':d.id,'analysis_id':d.analysis_id,'title':d.document['title'],'revision':d.revision} for d in drafts],'versions':[{'version_id':v.id,'asset_id':a.id,'kind':a.kind,'name':v.payload.get('name',a.name),'revision':v.revision,'content_hash':v.content_hash} for v,a in rows]}
