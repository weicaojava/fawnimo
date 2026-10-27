"""Versioned whole-chapter assembly and fixed original story retention declarations."""
import uuid
from fastapi import HTTPException
from sqlalchemy import select,update
from app.core.contracts.editing import EditDocument
from app.models.editing import EditDraft,EditPublication,TakeOrigin
from app.models.production import TakeVersion,AssetVersion
from app.services.editing.common import owned_take,owned_shot,chapter_owned,owned_plan,plan_script,original_items,canonical,content_hash,issue,bounded,public_payload,cursor_state
from app.services.worker.execution_budget import transaction
from app.services.shot_plan.service import validate_current


async def owned_draft(db,project_id,draft_id):
    """Resolve one actual chapter-owned editing draft."""
    row=(await db.execute(select(EditDraft).where(EditDraft.id==draft_id,EditDraft.project_id==project_id))).scalar_one_or_none()
    if row is None:raise HTTPException(404,'本工程编辑草稿不存在')
    await chapter_owned(db,project_id,row.chapter_id)
    return row


async def owned_publication(db,project_id,publication_id):
    """Return the full private immutable payload only after ownership and hash validation."""
    row=(await db.execute(select(EditPublication).where(EditPublication.id==publication_id,EditPublication.project_id==project_id))).scalar_one_or_none()
    if row is None:raise HTTPException(404,'本工程时间线版本不存在')
    if content_hash(row.payload)!=row.content_hash:raise HTTPException(409,'时间线版本内容校验失败')
    await chapter_owned(db,project_id,row.chapter_id)
    return row


async def validate_document(db,project_id,chapter_id,document):
    """Reject broken identities while allowing incomplete coverage to remain a draft."""
    await chapter_owned(db,project_id,chapter_id)
    data=EditDocument.model_validate(document).model_dump()
    if len(canonical(data).encode())>1024*1024:raise HTTPException(422,'时间线文档超过1MiB')
    plan=await owned_plan(db,project_id,data['plan_id']) if data['plan_id'] else None
    if plan and plan.payload['chapter_id']!=chapter_id:raise HTTPException(422,'计划不属于本章')
    entries={e['shot_id']:e for e in plan.payload['entries']} if plan else {}
    shots=set();takes=set()
    for clip in data['clips']:
        shot=await owned_shot(db,project_id,clip['shot_id'])
        if shot.chapter_id!=chapter_id:raise HTTPException(422,'时间线不能引用其他章节镜头')
        if shot.id in shots or clip['take_id'] in takes:raise HTTPException(422,'镜头或采用片段不能重复')
        shots.add(shot.id);takes.add(clip['take_id'])
        await owned_take(db,project_id,clip['take_id'],shot.id)
        if plan and shot.id not in entries:raise HTTPException(422,'镜头不在固定整章计划内')
    known={(i['kind'],i['item_id']):i for i in original_items(plan,await plan_script(db,project_id,plan))} if plan else {}
    anchor_ids=set()
    for anchor in data['anchors']:
        key=(anchor['kind'],anchor['item_id'])
        if key in anchor_ids:raise HTTPException(422,'原剧情或对白只能保留一条时间标记')
        anchor_ids.add(key)
        if not plan or key not in known:raise HTTPException(422,'时间标记没有对应的原计划条目')
        if anchor['take_id'] not in takes:raise HTTPException(422,'时间标记必须引用已选片段')
        clip=next(c for c in data['clips'] if c['take_id']==anchor['take_id'])
        if clip['shot_id'] not in known[key]['shot_ids']:raise HTTPException(422,'时间标记片段不承载该原始剧情或对白')
    return data


async def assemble(db,project_id,chapter_id,document,verified=None):
    """Build a complete fixed payload with real source evidence outside writer locks."""
    from app.services.editing.media import probe_owned,recheck_media,covers
    data=await validate_document(db,project_id,chapter_id,document)
    problems=[];proof={};clips=[];subtitles=[];changes=[]
    if not data['title'].strip():problems.append(issue('title_missing','请填写时间线标题'))
    if not data['plan_id']:return None,[issue('plan_missing','请选择本章固定计划')]+problems,proof,changes
    plan=await owned_plan(db,project_id,data['plan_id']);entries=plan.payload['entries']
    if len(entries)>100:raise HTTPException(409,'整章计划超过100镜，本版不能截取部分镜头导出')
    expected=[e['shot_id'] for e in entries]
    if [c['shot_id'] for c in data['clips']]!=expected:problems.append(issue('shot_coverage','请按原计划顺序完整选择全部镜头，不能缺镜或重排'))
    items=original_items(plan,await plan_script(db,project_id,plan));by_item={(i['kind'],i['item_id']):i for i in items}
    clock=0
    for clip in data['clips']:
        take=await owned_take(db,project_id,clip['take_id'],clip['shot_id']);origin=await db.get(TakeOrigin,take.id)
        entry=next(e for e in entries if e['shot_id']==take.shot_id)
        if origin is None or origin.project_id!=project_id or origin.shot_id!=take.shot_id or origin.plan_id!=plan.id or origin.entry_hash!=entry['entry_hash']:
            problems.append(issue('take_origin','片段没有当前计划镜头的可信来源，请明确导入同媒体重新关联',take.shot_id));continue
        versions=list((await db.execute(select(TakeVersion.version_id).where(TakeVersion.take_id==take.id,TakeVersion.project_id==project_id))).scalars())
        expected_versions=[v['version_id'] for v in entry['versions']]
        if sorted(versions)!=sorted(expected_versions) or sorted(take.version_ids)!=sorted(expected_versions):raise HTTPException(409,'采用资产版本与原计划不一致')
        for version in entry['versions']:
            actual=await db.get(AssetVersion,version['version_id'])
            if actual is None or actual.project_id!=project_id or actual.content_hash!=version['content_hash'] or content_hash(actual.payload)!=actual.content_hash:raise HTTPException(409,'采用的不可变资产版本校验失败')
        if verified is None:evidence=await probe_owned(db,project_id,take.media_id)
        else:
            evidence=verified.get(take.media_id)
            if evidence is None:raise HTTPException(409,'来源证据不完整')
            await recheck_media(db,project_id,evidence)
        proof[take.media_id]=evidence
        if evidence['mime']!='video/mp4' or evidence['sha256']!=origin.evidence['sha256']:raise HTTPException(409,'采用视频与原始记录不符')
        if not covers(evidence['video']['coverage_us'],clip['source_in_ms'],clip['source_out_ms']):problems.append(issue('video_coverage','裁剪超出视频实际完整覆盖区间，不能补帧延长',take.shot_id))
        duration=clip['source_out_ms']-clip['source_in_ms']
        clips.append({**clip,'origin':{'kind':origin.origin_kind,'plan_id':origin.plan_id,'entry_hash':origin.entry_hash,'job_id':origin.job_id,'version_ids':expected_versions},'source':evidence,'timeline_start_ms':clock,'timeline_end_ms':clock+duration})
        clock+=duration
        if clip['source_in_ms']!=0 or clip['source_out_ms']!=entry['duration_ms']:changes.append('镜头'+take.shot_id+'裁剪或时长不同于原计划')
    if clock>300000:problems.append(issue('duration_limit','时间线总时长不能超过300秒'))
    if clock<100:problems.append(issue('duration_empty','请选择有效视频区间'))
    anchors={(a['kind'],a['item_id']):a for a in data['anchors']}
    for item in items:
        anchor=anchors.get((item['kind'],item['item_id']))
        if anchor is None:
            if item['required']:problems.append(issue('required_anchor','请为每条必需原剧情及对白标注并确认保留区间',item_id=item['item_id']))
            continue
        clip=next((c for c in clips if c['take_id']==anchor['take_id']),None)
        if not anchor['confirmed']:problems.append(issue('anchor_unconfirmed','请人工确认此原始条目仍被保留',item_id=item['item_id']))
        if clip is None:continue
        if not (clip['source_in_ms']<=anchor['start_ms']<anchor['end_ms']<=clip['source_out_ms']) or not covers(clip['source']['video']['coverage_us'],anchor['start_ms'],anchor['end_ms']):problems.append(issue('anchor_trimmed','原始条目时间标记位于裁剪范围之外',clip['shot_id'],item['item_id']))
        if item['kind']=='dialogue':
            audio=clip['source']['audio']
            if not clip['source_audio'] or audio is None or not covers(audio['coverage_us'],anchor['start_ms'],anchor['end_ms']):problems.append(issue('dialogue_audio','对白必须保留实际原声覆盖区间；静音或补静音不能通过',clip['shot_id'],item['item_id']))
            subtitles.append({'dialogue_id':item['item_id'],'speaker_id':item['speaker_id'],'text':item['text'],'start_ms':clip['timeline_start_ms']+anchor['start_ms']-clip['source_in_ms'],'end_ms':clip['timeline_start_ms']+anchor['end_ms']-clip['source_in_ms']})
    duration_plan=sum(e['duration_ms'] for e in entries)
    if clock!=duration_plan:changes.append('全章总时长不同于原计划')
    payload={'schema_version':1,'project_id':project_id,'chapter_id':chapter_id,'document':data,'plan':{'id':plan.id,'content_hash':plan.content_hash},'clips':clips,'subtitles':sorted(subtitles,key=lambda x:(x['start_ms'],x['dialogue_id'])),'duration_ms':clock,'profile':'portrait-360p30-v1','coverage':{'required_beats':sum(i['required'] for i in items if i['kind']=='beat'),'required_dialogues':sum(i['required'] for i in items if i['kind']=='dialogue'),'declared_items':len(anchors),'shot_count':len(expected),'original_items':items},'human_declarations_only':True}
    if len(canonical(payload).encode())>1024*1024:raise HTTPException(409,'完整时间线证据超过1MiB')
    return payload,problems,proof,changes


async def create_draft(factory,project_id,body):
    """Register one editable assembly without changing any selected Take."""
    async with factory() as db:
        async with transaction(db):
            data=await validate_document(db,project_id,body.chapter_id,body.document)
            if (await db.execute(select(EditDraft.id).where(EditDraft.chapter_id==body.chapter_id))).first():raise HTTPException(409,'本章已有编辑草稿')
            identifier=str(uuid.uuid4());db.add(EditDraft(id=identifier,project_id=project_id,chapter_id=body.chapter_id,revision=1,document=data));return identifier


async def patch_draft(factory,project_id,draft_id,body):
    """Save complete form content as a new revision, retaining every old publication."""
    async with factory() as db:
        async with transaction(db):
            draft=await owned_draft(db,project_id,draft_id);data=await validate_document(db,project_id,draft.chapter_id,body.document)
            result=await db.execute(update(EditDraft).where(EditDraft.id==draft_id,EditDraft.revision==body.expected_revision).values(document=data,revision=EditDraft.revision+1))
            if not result.rowcount:raise HTTPException(409,'编辑修订冲突，请刷新后重试')


async def upstream_state(db,plan,verified=None):
    """Only plan content drift, not missing model configuration, requires an acknowledgment."""
    try:
        _,proof=await validate_current(db,plan,verified=verified)
        return False,proof
    except HTTPException:return True,None


async def freeze_draft(factory,project_id,draft_id,body):
    """Freeze decoded facts with CAS; replay the same revision before probing again."""
    async with factory() as db:
        draft=await owned_draft(db,project_id,draft_id)
        if draft.revision!=body.expected_revision:raise HTTPException(409,'编辑修订冲突，请刷新')
        old=(await db.execute(select(EditPublication).where(EditPublication.draft_id==draft.id,EditPublication.draft_revision==draft.revision))).scalar_one_or_none()
        if old:return (await owned_publication(db,project_id,old.id)).id
        payload,problems,proof,changes=await assemble(db,project_id,draft.chapter_id,draft.document)
        if problems:raise HTTPException(409,problems[0]['message'])
        plan=await owned_plan(db,project_id,payload['plan']['id']);stale,plan_proof=await upstream_state(db,plan)
        if stale:changes.append('上游计划已是历史版本')
        if changes and (not body.acknowledge_changes or not body.change_note.strip()):raise HTTPException(409,'裁剪、时长或上游发生改变，请明确确认并填写说明')
        payload['change_acknowledgment']={'acknowledged':body.acknowledge_changes,'note':body.change_note,'changes':changes}
        checksum=content_hash(payload)
    async with factory() as db:
        async with transaction(db):
            draft=await owned_draft(db,project_id,draft_id)
            if draft.revision!=body.expected_revision:raise HTTPException(409,'编辑修订冲突，请刷新')
            old=(await db.execute(select(EditPublication).where(EditPublication.draft_id==draft.id,EditPublication.draft_revision==draft.revision))).scalar_one_or_none()
            if old:return (await owned_publication(db,project_id,old.id)).id
            current,problems,_,new_changes=await assemble(db,project_id,draft.chapter_id,draft.document,verified=proof)
            if problems:raise HTTPException(409,problems[0]['message'])
            plan=await owned_plan(db,project_id,current['plan']['id'])
            if plan_proof is not None:
                now_stale,_=await upstream_state(db,plan,plan_proof)
                if now_stale!=stale:raise HTTPException(409,'上游事实已改变，请重新核对')
            if stale:new_changes.append('上游计划已是历史版本')
            current['change_acknowledgment']={'acknowledged':body.acknowledge_changes,'note':body.change_note,'changes':new_changes}
            if content_hash(current)!=checksum:raise HTTPException(409,'时间线来源在锁定期间改变，请重试')
            identifier=str(uuid.uuid4());db.add(EditPublication(id=identifier,project_id=project_id,chapter_id=draft.chapter_id,draft_id=draft.id,draft_revision=draft.revision,payload=current,content_hash=checksum));return identifier


async def publication_view(db,project_id,publication_id):
    """Serve the portable display view while retaining the opaque full-evidence hash."""
    row=await owned_publication(db,project_id,publication_id)
    return {'id':row.id,'project_id':row.project_id,'chapter_id':row.chapter_id,'draft_id':row.draft_id,'draft_revision':row.draft_revision,'content_hash':row.content_hash,'payload':public_payload(row.payload)}


async def preflight_publication(db,project_id,publication_id):
    """Check historical media independently of mutable current selections or script edits."""
    from app.services.editing.media import probe_owned
    row=await owned_publication(db,project_id,publication_id);problems=[];differences=[]
    for clip in row.payload['clips']:
        try:
            evidence=await probe_owned(db,project_id,clip['source']['id'])
            for key in ('sha256','size','video','audio'):
                if evidence[key]!=clip['source'][key]:raise HTTPException(409,'来源媒体事实不符')
        except HTTPException:problems.append(issue('source_unavailable','锁定视频来源缺失、损坏或无法完整解码',clip['shot_id']))
        state=await cursor_state(db,project_id,clip['shot_id'])
        if state['take_id']!=clip['take_id']:differences.append({'shot_id':clip['shot_id'],'publication_take_id':clip['take_id'],'current_take_id':state['take_id']})
    return {'usable':not problems,'issues':problems,'current_selection_differences':differences,'human_declarations_only':True}


async def draft_view(db,project_id,draft_id):
    """Read controlled form content with truthful incomplete-source readiness."""
    draft=await owned_draft(db,project_id,draft_id)
    try:
        _,problems,_,_=await assemble(db,project_id,draft.chapter_id,draft.document)
    except HTTPException as exc:problems=[issue('source_unavailable',str(exc.detail))]
    publications=await bounded(db,select(EditPublication).where(EditPublication.draft_id==draft.id).order_by(EditPublication.draft_revision.desc()),200,'时间线版本')
    if publications and publications[0].draft_revision==draft.revision:
        check=await preflight_publication(db,project_id,publications[0].id)
        if not check['usable']:problems.append(issue('save_new_revision','本修订历史版本来源不可用，请修复来源并保存新修订再锁定'))
    return {'id':draft.id,'project_id':project_id,'chapter_id':draft.chapter_id,'revision':draft.revision,'document':draft.document,'readiness':{'can_freeze':not problems,'issues':problems,'human_declarations_only':True},'publications':[{'id':p.id,'content_hash':p.content_hash,'draft_revision':p.draft_revision} for p in publications]}
