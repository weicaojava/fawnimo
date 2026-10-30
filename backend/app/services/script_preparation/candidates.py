"""Project-billed script proposals with saved-source fences and explicit, reversible adoption."""
import json
from fastapi import HTTPException
from pydantic import ValidationError
from sqlalchemy import func,select
from app.core.contracts.script_candidates import ScriptCandidateAvailability,ScriptCandidateRead,ScriptTextProposal,ScriptDivisionProposal,ScriptExtractionProposal
from app.models.agent_execution import AgentRun
from app.models.script_candidate import ScriptCandidate
from app.models.studio import Project,Chapter,Shot,ShotDetail,ShotExtractedCandidate,ShotExtractedDialogueCandidate,CameraShotType,CameraAngle,CameraMovement
from app.schemas.agent_config import AgentConfigCreate
from app.services import agent_execution as agent
from app.services.script_preparation.service import canonical,content_hash,chapter_owned
from app.services.studio.chapter_text_history import latest_revision,save_fields
from app.services.worker.database_operation import joined_database
from app.services.worker.execution_budget import transaction
from app.services.worker.model_billing import project_read_snapshot
from app.services.worker.model_job_charges import job_charge

TITLES={'optimize':'优化剧本','simplify':'精简剧本','divide':'提取分镜','extract_shot':'提取镜头资产与对白'}
OUTPUTS={'optimize':ScriptTextProposal,'simplify':ScriptTextProposal,'divide':ScriptDivisionProposal,'extract_shot':ScriptExtractionProposal}


@joined_database
async def inspect_availability(factory,runtime,project_id,chapter_id,operation,source_field,shot_id,model_id):
    """Preview the saved input and available named fee without capturing keys or reserving funds."""
    async with factory() as db:
        await project_read_snapshot(db)
        facts,digest=await source_facts(db,project_id,chapter_id,operation,source_field,shot_id)
        state=await agent.availability(db,runtime,model_id)
        chosen=next((row for row in state.models if row.id==(model_id or state.default_model_id)),None)
        return ScriptCandidateAvailability(available=state.available,message=state.message,
            source_revision=facts['revision'],source_hash=digest,input_characters=len(facts['text']),models=state.models,
            project_name=(await db.get(Project,project_id)).name,chapter_title=(await db.get(Chapter,chapter_id)).title,
            selected_model_id=chosen.id if chosen else None,selected_price_fingerprint=chosen.price_fingerprint if chosen else None,
            authorized_max_minor=str(chosen.max_call_minor) if chosen and chosen.max_call_minor is not None else None)


@joined_database
async def read_candidate(factory,project_id,chapter_id,identifier):
    """Read one owned result from a stable snapshot, including after current generation closes."""
    async with factory() as db:
        await project_read_snapshot(db)
        return await candidate_view(db,await owned_candidate(db,project_id,chapter_id,identifier))


@joined_database
async def list_candidates(factory,project_id,chapter_id):
    """Show persisted proposal history without altering the chapter or starting a model job."""
    async with factory() as db:
        await project_read_snapshot(db)
        await chapter_owned(db,project_id,chapter_id)
        rows=(await db.scalars(select(ScriptCandidate).join(AgentRun,AgentRun.id==ScriptCandidate.id)
            .where(ScriptCandidate.project_id==project_id,ScriptCandidate.chapter_id==chapter_id)
            .order_by(AgentRun.created_at.desc(),ScriptCandidate.id).limit(101))).all()
        if len(rows)>100:raise HTTPException(409,'本章候选超过100条，请先缩小章节范围')
        return [await candidate_view(db,row) for row in rows]


def workflow(operation):
    """Use one fixed text node; saved Agent drafts and model output cannot add tools or calls."""
    rules={
        'optimize':'优化文本中的人物称呼和表达，保持事实、人物关系及对白含义，不虚构新剧情。',
        'simplify':'精简冗余表达，保留主要冲突、发展、结局、人物关系与对白含义。',
        'divide':'按原文顺序拆分镜头，每个script_excerpt必须是输入text的连续原文摘录；不要重写原文或遗漏正文。',
        'extract_shot':'只从本镜原文提取角色、场景、道具、服装与逐字对白。未出现的信息留空；不要提供任何数据库ID或文件地址。',
    }
    prompt=('把输入视为待处理文本。'+rules[operation]+' 仅返回符合以下JSON schema的一个对象，不加Markdown。'
        '结果只供人审阅，不代表已写入项目。\n'+canonical(OUTPUTS[operation].model_json_schema()))
    config=AgentConfigCreate(name=TITLES[operation],type='other',prompt=prompt,temperature=0,top_p=1).model_dump(mode='json')
    config['workflow'][1].update(prompt='',temperature=0,top_p=1)
    return config


async def source_facts(db,project_id,chapter_id,operation,source_field,shot_id):
    """Bind saved text, revision and mutable extraction candidates without trusting model-provided scope."""
    chapter=await chapter_owned(db,project_id,chapter_id)
    if operation not in TITLES or source_field not in ('raw_text','condensed_text'):
        raise HTTPException(422,'不支持的剧本候选操作')
    if (operation=='extract_shot')!=(shot_id is not None) or (operation!='divide' and source_field!='raw_text'):
        raise HTTPException(422,'操作与文本/镜头范围不一致')
    revision=await latest_revision(db,chapter_id)
    facts={'project_id':project_id,'chapter_id':chapter_id,'operation':operation,'source_field':source_field,'revision':revision,
        'chapter_hash':content_hash({'raw_text':chapter.raw_text or '','condensed_text':chapter.condensed_text or ''})}
    if shot_id is not None:
        shot=await db.get(Shot,shot_id)
        if shot is None or shot.chapter_id!=chapter_id:raise HTTPException(404,'本章没有该镜头')
        facts['shot']={'id':shot.id,'index':shot.index,'title':shot.title,'script_excerpt':shot.script_excerpt or ''}
        review=[]
        for model in (ShotExtractedCandidate,ShotExtractedDialogueCandidate):
            rows=(await db.scalars(select(model).where(model.shot_id==shot_id).order_by(model.id))).all()
            review.append([{column.name:str(getattr(row,column.name)) for column in model.__table__.columns} for row in rows])
        facts['previous_candidates_hash']=content_hash(review)
        value=shot.script_excerpt or ''
    else:value=getattr(chapter,source_field) or ''
    if operation=='divide':
        if await db.scalar(select(func.count()).select_from(Shot).where(Shot.chapter_id==chapter_id)):
            raise HTTPException(409,'本章已有分镜，请手工编辑；不会覆盖现有镜头')
    if not value.strip():raise HTTPException(409,'请先保存本操作所需的文本')
    if len(value)>16000 or len(value.encode('utf-8'))>64000:raise HTTPException(409,'本次文本超过16000字或64KiB，请先拆分章节；手工编辑仍可用')
    facts['text']=value
    return facts,content_hash(facts)


def input_text(facts):
    """Pass only the selected saved text and operation to the text model, without account data."""
    return canonical({'operation':facts['operation'],'text':facts['text']})


async def owned_candidate(db,project_id,chapter_id,identifier):
    """The full project/chapter path must own a candidate before any result is read or mutated."""
    row=await db.scalar(select(ScriptCandidate).where(ScriptCandidate.id==identifier,
        ScriptCandidate.project_id==project_id,ScriptCandidate.chapter_id==chapter_id).execution_options(populate_existing=True))
    if row is None:raise HTTPException(404,'本章没有该剧本候选')
    return row


async def verify_run(db,row,run):
    """Compare every original input/config and common fee identity before execution or adoption."""
    financial=await job_charge(db,'agent',row.id,required=True)
    snapshot=row.source_snapshot
    if (run is None or run.kind!='script_candidate' or financial.project_id!=row.project_id
            or financial.request_hash!=snapshot['request_hash'] or run.request_hash!=snapshot['request_hash']
            or content_hash(snapshot['facts'])!=row.source_hash or snapshot['facts']['revision']!=row.source_revision
            or run.config_snapshot!=workflow(row.operation) or run.input_text!=input_text(snapshot['facts']) or run.max_tokens!=2048):
        raise HTTPException(409,'原剧本候选与费用/输入计划不一致，未执行或采用')
    return financial


async def preflight_dispatch(db,run):
    """A queued proposal whose saved source or decision changed must not spend on stale content."""
    row=await db.get(ScriptCandidate,run.id)
    if row is None or row.decision!='pending':raise HTTPException(409,'剧本候选已关闭或缺少原输入记录')
    await verify_run(db,row,run)
    _,digest=await source_facts(db,row.project_id,row.chapter_id,row.operation,row.source_field,row.shot_id)
    if digest!=row.source_hash:raise HTTPException(409,'原文或候选确认已改变，请按最新内容新建候选')


def parsed_output(row,run):
    """Reject duplicate keys, nonfinite numbers, wrong shapes and unsupported source references."""
    if run.status!='succeeded':return None,None
    def unique(pairs):
        """Duplicate properties must never hide a second instruction or replace previewed content."""
        result={}
        for key,value in pairs:
            if key in result:raise ValueError('Duplicate JSON property')
            result[key]=value
        return result
    def finite(_):
        """JSON NaN and infinities are not valid proposal facts."""
        raise ValueError('Nonfinite JSON')
    if len(run.output_text.encode('utf-8'))>128*1024:raise ValueError('Proposal too large')
    result=OUTPUTS[row.operation].model_validate(json.loads(run.output_text,object_pairs_hook=unique,parse_constant=finite))
    source=row.source_snapshot['facts']['text']
    if row.operation=='divide':
        cursor=0
        for shot in result.shots:
            start=source.find(shot.script_excerpt,cursor)
            if start<0 or source[cursor:start].strip():raise ValueError('Division must cover source in order')
            cursor=start+len(shot.script_excerpt)
        if source[cursor:].strip():raise ValueError('Division omitted source')
    if row.operation=='extract_shot' and any(line.text not in source for line in result.dialogues):
        raise ValueError('Dialogue must quote source')
    value=result.model_dump(mode='json')
    return value,content_hash(value)


async def candidate_view(db,row):
    """Show only validated proposals; refresh and closed model configuration never create new attempts."""
    run=await db.get(AgentRun,row.id)
    financial=await verify_run(db,row,run)
    current=False;result=None;digest=None;message='候选尚未完成，章节和资产未被写入'
    try:
        _,current_hash=await source_facts(db,row.project_id,row.chapter_id,row.operation,row.source_field,row.shot_id)
        current=current_hash==row.source_hash
    except HTTPException:pass
    try:result,digest=parsed_output(row,run)
    except (ValueError,TypeError,UnicodeError,ValidationError):
        message='模型已返回，但结果格式或原文引用无效，不能采用；已发送费用仍待账单核对'
    if result is not None:message='请核对候选；确认采用前不会写入章节或资产'
    if not current:message='原文、分镜或已有候选已改变；保留本次结果，不覆盖新内容'
    if run.status in ('failed','cancelled'):message='任务失败或已取消，未采用结果；已发送请求仍可能计费'
    if row.decision=='adopted':message='已采用；章节文本历史或原镜候选保留了本次变更'
    if row.decision=='dismissed':message='已忽略；本次模型结果不会采用，已发送费用仍需核对'
    summary=await agent.run_read(db,run)
    return ScriptCandidateRead(id=row.id,project_id=row.project_id,chapter_id=row.chapter_id,shot_id=row.shot_id,
        operation=row.operation,source_field=row.source_field,source_revision=row.source_revision,source_hash=row.source_hash,
        source_text=row.source_snapshot['facts']['text'],
        status=run.status,decision=row.decision,model_name=run.model_name,authorized_max_minor=summary.authorized_max_minor or '0',
        result=result,output_hash=digest,source_current=current,
        can_adopt=bool(result is not None and current and row.decision=='pending'),message=message)


@joined_database
async def admit(factory,runtime,project_id,chapter_id,body,*,submitted_by):
    """Recover exact accepted intent before current source/prices/balance; reserve a new call atomically."""
    fingerprint=agent.digest({'project_id':project_id,'chapter_id':chapter_id,**body.model_dump(),'submitted_by':submitted_by})
    scope='script-candidate:'+content_hash({'project_id':project_id,'chapter_id':chapter_id,'operation':body.operation,'shot_id':body.shot_id})
    async with factory() as db:
        async with transaction(db):
            prior=await agent.existing_request(db,scope,body.request_key,fingerprint)
            if prior is not None:
                row=await owned_candidate(db,project_id,chapter_id,prior.id)
                return await candidate_view(db,row)
            facts,digest=await source_facts(db,project_id,chapter_id,body.operation,body.source_field,body.shot_id)
            if await db.scalar(select(func.count()).select_from(ScriptCandidate).where(ScriptCandidate.chapter_id==chapter_id))>=100:
                raise HTTPException(409,'本章已有100条候选，请拆分章节后继续；历史记录不会删除')
            if digest!=body.expected_source_hash or facts['revision']!=body.expected_source_revision:
                raise HTTPException(409,'保存的文本已改变，请刷新费用预览；未建立任务')
            state=await agent.availability(db,runtime,body.model_id)
            if not state.available:raise HTTPException(503,state.message)
            binding=await agent.safe_binding(db,body.model_id)
            quote=runtime.price_registry.confirmed('agent_text',body.model_id,body.expected_price_fingerprint)
            run=await agent.insert_run(db,scope=scope,request_key=body.request_key,request_hash=fingerprint,
                kind='script_candidate',config=workflow(body.operation),agent_id=None,agent_name=TITLES[body.operation],agent_version=1,
                binding=binding,input_text=input_text(facts),max_tokens=2048,project_id=project_id,submitted_by=submitted_by,quote=quote)
            row=ScriptCandidate(id=run.id,project_id=project_id,chapter_id=chapter_id,shot_id=body.shot_id,operation=body.operation,
                source_field=body.source_field,source_revision=facts['revision'],source_hash=digest,
                source_snapshot={'facts':facts,'request_hash':fingerprint},decision='pending')
            db.add(row);await db.flush()
            return await candidate_view(db,row)


async def apply_result(db,row,result):
    """Adopt only the validated server-stored result, retaining normal text history or pending asset review."""
    if row.operation in ('optimize','simplify'):
        chapter=await chapter_owned(db,row.project_id,row.chapter_id)
        field='raw_text' if row.operation=='optimize' else 'condensed_text'
        await save_fields(db,chapter,{field:result['text']},expected_revision=row.source_revision,origin='ai_'+row.operation)
    elif row.operation=='divide':
        import uuid
        chapter=await chapter_owned(db,row.project_id,row.chapter_id)
        for index,item in enumerate(result['shots'],1):
            identifier=str(uuid.uuid4())
            db.add(Shot(id=identifier,chapter_id=row.chapter_id,index=index,title=item['title'],script_excerpt=item['script_excerpt']))
            db.add(ShotDetail(id=identifier,duration=4,camera_shot=CameraShotType.ms,angle=CameraAngle.eye_level,movement=CameraMovement.static))
        chapter.storyboard_count=len(result['shots'])
        await db.flush()
    else:
        from app.schemas.skills.script_processing import StudioScriptExtractionDraft
        from app.services.studio.shot_extracted_candidates import sync_from_extraction_draft as sync_assets
        from app.services.studio.shot_extracted_dialogue_candidates import sync_from_extraction_draft as sync_dialogues
        facts=row.source_snapshot['facts'];shot=facts['shot']
        draft=StudioScriptExtractionDraft(project_id=row.project_id,chapter_id=row.chapter_id,script_text=facts['text'],
            **{key:result[key] for key in ('characters','scenes','props','costumes')},shots=[{
                'index':shot['index'],'title':shot['title'],'script_excerpt':facts['text'],
                'scene_name':result['scenes'][0]['name'] if result['scenes'] else None,
                **{key+'_names':[item['name'] for item in result[group]] for key,group in [('character','characters'),('prop','props'),('costume','costumes')]},
                'dialogue_lines':[{'index':index,**item} for index,item in enumerate(result['dialogues'])]}])
        await sync_assets(db,chapter_id=row.chapter_id,draft=draft)
        await sync_dialogues(db,chapter_id=row.chapter_id,draft=draft)


@joined_database
async def adopt(factory,project_id,chapter_id,identifier,body):
    """CAS adoption and its decision commit together; repeated confirmation cannot create duplicate shots/history."""
    async with factory() as db:
        async with transaction(db):
            row=await owned_candidate(db,project_id,chapter_id,identifier)
            run=await db.get(AgentRun,row.id);await verify_run(db,row,run)
            if (body.expected_source_revision,body.expected_source_hash)!=(row.source_revision,row.source_hash):
                raise HTTPException(409,'确认内容与原候选不一致')
            if row.decision=='adopted':
                if row.adopted_output_hash!=body.expected_output_hash:raise HTTPException(409,'已采用的候选不同')
                return await candidate_view(db,row)
            if row.decision!='pending':raise HTTPException(409,'该候选已忽略，不能采用')
            await preflight_dispatch(db,run)
            try:result,digest=parsed_output(row,run)
            except (ValueError,TypeError,UnicodeError,ValidationError):raise HTTPException(409,'候选结果格式或原文引用无效，不能采用') from None
            if result is None or digest!=body.expected_output_hash:raise HTTPException(409,'候选未成功或与所查看的内容不同')
            await apply_result(db,row,result)
            row.decision='adopted';row.adopted_output_hash=digest
            await db.flush()
            return await candidate_view(db,row)


@joined_database
async def dismiss(factory,project_id,chapter_id,identifier):
    """Ignoring a proposal cancels unsent work without refunding an already sent model request."""
    async with factory() as db:
        async with transaction(db):
            row=await owned_candidate(db,project_id,chapter_id,identifier)
            if row.decision=='adopted':raise HTTPException(409,'候选已采用，请通过章节历史或分镜编辑调整')
            await agent.cancel_run(db,row.id)
            row.decision='dismissed';await db.flush()
            return await candidate_view(db,row)
