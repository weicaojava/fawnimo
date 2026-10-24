"""Human benchmark preparation with owned evidence, CAS edits and immutable freeze."""
import hashlib
import json
import uuid
from fractions import Fraction
from fastapi import HTTPException
from sqlalchemy import select,update
from app.core.contracts.benchmark import BenchmarkDocument
from app.models.benchmark import BenchmarkDraft,BenchmarkSnapshot
from app.services.reverse.views import owned_analysis,artifact_path
from app.services.worker.execution_budget import transaction

RULES={'weights':{'character':20,'scene':20,'composition':15,'lighting':15,'action':10,'continuity':10,'lipsync':5,'editing':5},'hardcut_precision_min':0.90,'hardcut_recall_min':0.90,'hardcut_tolerance':'one_actual_original_frame_interval','proxy_mapping_tolerance':'one_actual_original_frame_interval','automatic_cer_max':0.10,'cer_normalization':'remove whitespace and Unicode punctuation; preserve all other characters; case unchanged','confirmed_dialogue_coverage':1,'speaker_duration_accuracy_min':0.95,'subtitle_endpoint_error_ms_max':150,'lipsync_median_ms_max':80,'lipsync_p95_ms_max':160,'event_coverage_min':1,'event_order_accuracy_min':1,'event_time_median':'max(200ms,shot_duration*0.10)','event_time_p95':'max(400ms,shot_duration*0.20)','export_shot_coverage_min':1,'export_duration_tolerance':'one_output_frame_interval','export_av_endpoint_error_ms_max':100,'unplanned_black_frames_allowed':False,'missing_audio_allowed':False,'decode_required':True,'dna_roundtrip_fidelity':1,'quality_A_min':85,'quality_B_min':85,'critical_character_scene_action_min':80,'serious_defect_allowed':False,'visual_anchors':[0,25,50,70,85,100],'independent_reviewers_min':2,'review_difference_requires_recheck_over':10,'no_pixel_determinism_claim':True}


def canonical(value):
    """Stable Unicode JSON excludes random snapshot identity and timestamps."""
    return json.dumps(value,ensure_ascii=False,sort_keys=True,separators=(',',':'),allow_nan=False)


def validate_document(document,analysis):
    """Reject malformed references and bounds while permitting incomplete draft coverage."""
    data=BenchmarkDocument.model_validate(document).model_dump()
    if len(canonical(data).encode('utf8'))>128*1024:raise HTTPException(422,'基准草稿超过128KiB')
    frames=(analysis.manifest or {}).get('video',{}).get('frames',[])
    ids=[r['id'] for r in data['roles']]
    if len(set(ids))!=len(ids):raise HTTPException(422,'角色标识重复')
    role_ids=set(ids)
    for group in ['dialogues','events']:
        identifiers=[item['id'] for item in data[group]]
        if len(set(identifiers))!=len(identifiers):raise HTTPException(422,'标注标识重复')
        for item in data[group]:
            if item['start_frame']>item['end_frame'] or item['end_frame']>=len(frames):raise HTTPException(422,'标注区间超出原始解码帧范围')
    for item in data['dialogues']+data['mouth_anchors']:
        if item['role_id'] not in role_ids:raise HTTPException(422,'标注引用未知角色')
    anchors=[(a['role_id'],a['frame_index']) for a in data['mouth_anchors']]
    if len(set(anchors))!=len(anchors):raise HTTPException(422,'同一角色口型帧重复')
    if any(a['frame_index']>=len(frames) for a in data['mouth_anchors']):raise HTTPException(422,'口型锚点超出原始帧范围')
    cuts=data['cut_frame_indices']
    if cuts and (cuts[0]!=0 or cuts[-1]>=len(frames) or any(a>=b for a,b in zip(cuts,cuts[1:]))):raise HTTPException(422,'切点须从第0帧严格递增且不越界')
    return data


def readiness(data,analysis):
    """Report declaration completeness without certifying license, semantics or Q0."""
    issues=[]
    def require(condition,code,message):
        """Append a stable issue only when a freeze prerequisite is absent."""
        if not condition:issues.append({'code':code,'message':message})
    require(analysis.state=='ready','analysis_ready','需要完成且非降级的视频分析')
    frames=(analysis.manifest or {}).get('video',{}).get('frames',[])
    duration=None
    if len(frames)>=2:
        def stamp(frame):
            """Read exact source clock, without using nominal FPS or container duration."""
            return Fraction(frame['pts']*frame['time_base_num'],frame['time_base_den'])
        duration=stamp(frames[-1])-stamp(frames[0])+stamp(frames[-1])-stamp(frames[-2])
    require(duration is not None and 30<=duration<=60,'duration_30_60','连续素材解码时长须为30至60秒')
    audio=(analysis.manifest or {}).get('audio',{})
    available=False
    if audio.get('present') and audio.get('wav_path'):
        try:available=artifact_path(analysis,audio['wav_path']).stat().st_size>44
        except HTTPException:pass
    require(available,'audio_available','需要实际可用的提取音轨')
    require(bool(data['title'].strip()) and bool(data['source_note'].strip()),'source','请补充标题与来源')
    require(data['license_basis']!='unknown' and data['license_confirmed'] and bool(data['license_reference'].strip()),'license','请提供并确认许可依据')
    require(data['independent_from_target'],'independent','需要独立于目标素材的基准声明')
    require(data['continuous_take'],'continuous','需要连续素材声明，不能拼接不同来源')
    require(data['human_reviewed'] and bool(data['reviewer'].strip()),'human_review','请填写人工复核者并确认复核')
    require(len(data['roles'])>=2,'roles','至少需要两个稳定角色')
    for dialogue in data['dialogues']:
        require(bool(dialogue['text'].strip()),'dialogue_text_'+dialogue['id'],'对白 '+dialogue['id']+' 的逐字文本不能为空')
    for event in data['events']:
        require(bool(event['description'].strip()),'event_description_'+event['id'],'必需事件 '+event['id']+' 的描述不能为空')
    for role in data['roles']:
        identifier=role['id'];anchors=[a for a in data['mouth_anchors'] if a['role_id']==identifier]
        require(bool(role['name'].strip()),'role_name_'+identifier,'角色名称不能为空')
        require(any(d['role_id']==identifier and d['text'].strip() for d in data['dialogues']),'dialogue_'+identifier,'每个角色需要清晰对白标注')
        require(len(anchors)>=10 and {a['state'] for a in anchors}=={'speaking','closed'},'mouth_'+identifier,'每个角色需要至少10个独立发声及闭口锚点')
    for kind in ['interaction','action','camera_change']:
        require(any(e['kind']==kind and e['description'].strip() for e in data['events']),'event_'+kind,'请补齐互动、动作与机位事件')
    require(len(data['cut_frame_indices'])>=2,'cuts','至少需要含第0帧的两个切点')
    require(all(value.strip() for value in data['rubric_notes'].values()),'rubric','八个评分维度均需具体检查点')
    return {'can_freeze':not issues,'issues':issues,'semantic_verification':'human_declaration_only'}


async def owned_draft(db,project_id,draft_id):
    """Validate project ownership and original bytes on every draft operation."""
    draft=(await db.execute(select(BenchmarkDraft).where(BenchmarkDraft.id==draft_id,BenchmarkDraft.project_id==project_id))).scalar_one_or_none()
    if draft is None:raise HTTPException(404,'本工程基准草稿不存在')
    return draft,await owned_analysis(db,project_id,draft.analysis_id)


async def draft_view(db,project_id,draft_id):
    """Project safe typed content, declaration issues and immutable snapshot identities."""
    draft,analysis=await owned_draft(db,project_id,draft_id)
    rows=(await db.execute(select(BenchmarkSnapshot).where(BenchmarkSnapshot.draft_id==draft_id).order_by(BenchmarkSnapshot.draft_revision))).scalars()
    return {'id':draft.id,'analysis_id':draft.analysis_id,'revision':draft.revision,'document':draft.document,'readiness':readiness(draft.document,analysis),'snapshots':[{'id':r.id,'content_hash':r.content_hash,'draft_revision':r.draft_revision} for r in rows]}


async def create_draft(factory,project_id,body):
    """Atomically import an inert draft, deduplicating its original request content."""
    if not body.request_key.strip():raise HTTPException(422,'请求标识不能为空白')
    async with factory() as db:
        async with transaction(db):
            analysis=await owned_analysis(db,project_id,body.analysis_id)
            data=validate_document(body.document,analysis);digest=hashlib.sha256(canonical(data).encode()).hexdigest()
            old=(await db.execute(select(BenchmarkDraft).where(BenchmarkDraft.project_id==project_id,BenchmarkDraft.request_key==body.request_key))).scalar_one_or_none()
            if old:
                if old.analysis_id!=body.analysis_id or old.initial_hash!=digest:raise HTTPException(409,'请求标识已用于其他草稿')
                return old.id
            draft_id=str(uuid.uuid4())
            db.add(BenchmarkDraft(id=draft_id,project_id=project_id,analysis_id=body.analysis_id,request_key=body.request_key,initial_hash=digest,revision=1,document=data))
            return draft_id


async def patch_draft(factory,project_id,draft_id,body):
    """Replace only the expected revision under a writer lock; history is preserved."""
    async with factory() as db:
        async with transaction(db):
            draft,analysis=await owned_draft(db,project_id,draft_id)
            data=validate_document(body.document,analysis)
            changed=await db.execute(update(BenchmarkDraft).where(BenchmarkDraft.id==draft_id,BenchmarkDraft.project_id==project_id,BenchmarkDraft.revision==body.expected_revision).values(document=data,revision=BenchmarkDraft.revision+1))
            if not changed.rowcount:raise HTTPException(409,'草稿版本已改变，请刷新')


async def freeze_draft(factory,project_id,draft_id,expected_revision):
    """Freeze one complete declared document; never emit a formal gate-pass status."""
    async with factory() as db:
        async with transaction(db):
            draft,analysis=await owned_draft(db,project_id,draft_id)
            if draft.revision!=expected_revision:raise HTTPException(409,'草稿版本已改变，请刷新')
            old=(await db.execute(select(BenchmarkSnapshot).where(BenchmarkSnapshot.draft_id==draft_id,BenchmarkSnapshot.draft_revision==expected_revision))).scalar_one_or_none()
            if old:return old.id
            data=validate_document(draft.document,analysis);check=readiness(data,analysis)
            if not check['can_freeze']:raise HTTPException(409,{'message':'软件准备检查尚有缺项，不能冻结','readiness':check})
            payload={'schema_version':1,'source_hash':analysis.source_hash,'analysis_id':analysis.id,'machine_manifest_hash':hashlib.sha256(canonical(analysis.manifest).encode()).hexdigest(),'confirmed_cuts':analysis.confirmed_cuts,'reverse_revision':analysis.revision,'document':data,'rules':RULES,'frame_time_evidence':analysis.manifest['video'],'semantic_verification':'human_declaration_only'}
            snapshot=BenchmarkSnapshot(id=str(uuid.uuid4()),project_id=project_id,draft_id=draft_id,draft_revision=expected_revision,content_hash=hashlib.sha256(canonical(payload).encode()).hexdigest(),payload=payload)
            db.add(snapshot);return snapshot.id


async def snapshot_view(db,project_id,snapshot_id):
    """Verify current owned source before returning a prior immutable snapshot."""
    row=(await db.execute(select(BenchmarkSnapshot).where(BenchmarkSnapshot.id==snapshot_id,BenchmarkSnapshot.project_id==project_id))).scalar_one_or_none()
    if row is None:raise HTTPException(404,'本工程基准快照不存在')
    await owned_draft(db,project_id,row.draft_id)
    return {'id':row.id,'content_hash':row.content_hash,'payload':row.payload}
