"""Independent v2 boundary revisions over unchanged immutable v1 sources and adoptions."""
import copy
import uuid
from fastapi import HTTPException
from sqlalchemy import select, update
from app.core.contracts.editing_v2 import TransitionDocument
from app.models.editing_v2 import TransitionDraft, TransitionPublication
from app.services.editing.timeline import owned_publication as base_publication
from app.services.editing.common import public_payload, chapter_owned
from app.services.editing.media import canonical_hash, covers, verify_publication_sources, recheck_media
from app.services.worker.execution_budget import transaction

PROFILE = 'portrait-360p30-linear-crossfade-v2'


def problem(code, message, shot_id=None, item_id=None):
    """Return actionable content constraints without private storage paths."""
    return {'code': code, 'message': message, 'shot_id': shot_id, 'item_id': item_id}


def compose(base, document):
    """Derive output overlap time while keeping every input interval, PTS and origin unchanged."""
    data = TransitionDocument.model_validate(document).model_dump()
    original = base.payload
    if original.get('schema_version') != 1 or original.get('profile') != 'portrait-360p30-v1':
        raise HTTPException(422, '转场v2只能基于已有v1硬切锁定版本')
    source_clips = original.get('clips', [])
    if not 1 <= len(source_clips) <= 100:
        raise HTTPException(409, '原始时间线片段数量无效')
    expected = [(left['shot_id'], right['shot_id']) for left, right in zip(source_clips, source_clips[1:])]
    if [(edge['left_shot_id'], edge['right_shot_id']) for edge in data['edges']] != expected:
        raise HTTPException(422, '请按v1原顺序完整声明每个相邻边界，不能遗漏、重排或重复')
    durations = [clip['source_out_ms'] - clip['source_in_ms'] for clip in source_clips]
    if any(duration <= 0 or duration % 100 for duration in durations) or sum(durations) != original['duration_ms']:
        raise HTTPException(409, '原始时间线时长或100毫秒裁剪网格无效')
    issues = []
    if not data['title'].strip(): issues.append(problem('title_missing', '请填写转场版本标题'))
    fades = [edge['duration_ms'] for edge in data['edges']]
    clips, clock, transitions = [], 0, []
    by_take = {}
    for index, source in enumerate(source_clips):
        incoming = fades[index - 1] if index else 0
        outgoing = fades[index] if index < len(fades) else 0
        if incoming + outgoing >= durations[index]:
            issues.append(problem('triple_overlap', '同一片段的入/出重叠之和必须小于其保留时长，不能三镜混合', source['shot_id']))
        if source['source_audio'] and source['source']['audio'] is not None and not covers(source['source']['audio']['coverage_us'], source['source_in_ms'], source['source_out_ms']):
            issues.append(problem('audio_gap', 'v2保留原声须覆盖整个裁剪区间；不能用补静音参与转场', source['shot_id']))
        clip = copy.deepcopy(source)
        clip['timeline_start_ms'], clip['timeline_end_ms'] = clock, clock + durations[index]
        clip['transition_in_ms'], clip['transition_out_ms'] = incoming, outgoing
        clips.append(clip); by_take[clip['take_id']] = clip
        if index < len(fades):
            transitions.append({**data['edges'][index], 'start_ms': clip['timeline_end_ms'] - outgoing, 'end_ms': clip['timeline_end_ms'], 'video_frames': outgoing * 3 // 100, 'audio_samples': outgoing * 48})
        clock += durations[index] - outgoing
    if clock < 100 or clock > 300000:
        issues.append(problem('duration_invalid', '转场输出总时长须在100毫秒至300秒内'))
    for anchor in original['document'].get('anchors', []):
        clip = by_take.get(anchor['take_id'])
        if clip is None:
            raise HTTPException(409, 'v1时间标记与采用片段失配')
        clear_start = clip['source_in_ms'] + clip['transition_in_ms']
        clear_end = clip['source_out_ms'] - clip['transition_out_ms']
        if anchor['start_ms'] < clear_start or anchor['end_ms'] > clear_end:
            issues.append(problem('anchor_in_transition', '已声明保留的剧情/对白进入混合区；请缩短转场或在新v1版本中重新裁剪和标记', clip['shot_id'], anchor['item_id']))
    subtitles = copy.deepcopy(original.get('subtitles', []))
    anchors = {item['item_id']: item for item in original['document'].get('anchors', []) if item['kind'] == 'dialogue'}
    old_by_take = {clip['take_id']: clip for clip in source_clips}
    for cue in subtitles:
        anchor = anchors.get(cue['dialogue_id'])
        if anchor is None: raise HTTPException(409, 'v1字幕缺少对应源对白标记')
        offset = by_take[anchor['take_id']]['timeline_start_ms'] - old_by_take[anchor['take_id']]['timeline_start_ms']
        cue['start_ms'] += offset; cue['end_ms'] += offset
    payload = {'schema_version': 2, 'profile': PROFILE, 'project_id': base.project_id, 'chapter_id': base.chapter_id,
        'base': {'publication_id': base.id, 'content_hash': base.content_hash, 'duration_ms': original['duration_ms']},
        'document': data, 'clips': clips, 'transitions': transitions, 'subtitles': subtitles,
        'duration_ms': clock, 'overlap_ms': sum(fades), 'coverage': copy.deepcopy(original['coverage']),
        'human_declarations_only': True, 'model_quality_qualified': False,
        'mix_policy': {'video': 'linear encoded YUV frame blend on exact 30fps output grid', 'audio': 'linear sample crossfade at 48000Hz stereo; explicit mute or absent track is zero', 'anchors': 'all declared story/dialogue intervals must be outside every mixed source window', 'subtitles': 'separate SRT/VTT shifted by each clip output start; never burned in'}}
    if len(__import__('json').dumps(payload, ensure_ascii=False).encode()) > 1024 * 1024:
        raise HTTPException(409, '完整转场时间线超过1MiB')
    return payload, issues


async def owned_draft(db, project_id, identifier):
    """Resolve a project-owned independent v2 form without following mutable selections."""
    row = await db.get(TransitionDraft, identifier)
    if row is None or row.project_id != project_id: raise HTTPException(404, '本工程转场草稿不存在')
    await chapter_owned(db, project_id, row.chapter_id)
    return row


async def owned_publication(db, project_id, identifier):
    """Verify immutable v2 content before reusing it for historical local rendering."""
    row = await db.get(TransitionPublication, identifier)
    if row is None or row.project_id != project_id: raise HTTPException(404, '本工程转场锁定版本不存在')
    if row.content_hash != canonical_hash(row.payload): raise HTTPException(409, '转场锁定版本校验失败')
    await chapter_owned(db, project_id, row.chapter_id)
    return row


async def draft_view(db, project_id, identifier):
    """Expose exact output shortening and any protected anchor/audio constraints before freezing."""
    row = await owned_draft(db, project_id, identifier)
    base = await base_publication(db, project_id, row.base_publication_id)
    payload, issues = compose(base, row.document)
    versions = (await db.execute(select(TransitionPublication).where(TransitionPublication.draft_id == row.id).order_by(TransitionPublication.draft_revision.desc()))).scalars().all()
    return {'id': row.id, 'project_id': project_id, 'chapter_id': row.chapter_id, 'base_publication_id': base.id, 'base_publication_hash': base.content_hash,
        'revision': row.revision, 'document': row.document, 'base_duration_ms': base.payload['duration_ms'], 'overlap_ms': payload['overlap_ms'], 'duration_ms': payload['duration_ms'],
        'can_freeze': not issues, 'issues': issues, 'publications': [{'id': item.id, 'draft_revision': item.draft_revision, 'content_hash': item.content_hash, 'duration_ms': item.payload['duration_ms']} for item in versions]}


async def create_draft(factory, project_id, body):
    """Register one variant per immutable base without replacing any existing v1/v2 data."""
    async with factory() as db:
        async with transaction(db):
            base = await base_publication(db, project_id, body.base_publication_id)
            compose(base, body.document)
            if await db.scalar(select(TransitionDraft.id).where(TransitionDraft.project_id == project_id, TransitionDraft.base_publication_id == base.id)):
                raise HTTPException(409, '此v1版本已有转场草稿，请打开已有草稿')
            identifier = str(uuid.uuid4())
            db.add(TransitionDraft(id=identifier, project_id=project_id, chapter_id=base.chapter_id, base_publication_id=base.id, revision=1, document=body.document.model_dump()))
    return identifier


async def patch_draft(factory, project_id, identifier, body):
    """Replace only boundary choices under strict CAS, keeping the base publication pinned."""
    async with factory() as db:
        async with transaction(db):
            row = await owned_draft(db, project_id, identifier)
            compose(await base_publication(db, project_id, row.base_publication_id), body.document)
            result = await db.execute(update(TransitionDraft).where(TransitionDraft.id == identifier, TransitionDraft.revision == body.expected_revision).values(revision=TransitionDraft.revision + 1, document=body.document.model_dump()))
            if result.rowcount != 1: raise HTTPException(409, '转场草稿已被更新，请刷新后核对')


async def freeze_draft(factory, project_id, identifier, body):
    """Decode actual original bytes outside the writer lock, then pin one immutable v2 revision."""
    async with factory() as db:
        row = await owned_draft(db, project_id, identifier)
        if row.revision != body.expected_revision: raise HTTPException(409, '转场修订已变化')
        previous = await db.scalar(select(TransitionPublication.id).where(TransitionPublication.draft_id == identifier, TransitionPublication.draft_revision == row.revision))
        if previous: return previous
        base = await base_publication(db, project_id, row.base_publication_id)
        payload, issues = compose(base, row.document)
        if issues: raise HTTPException(409, issues[0]['message'])
        if payload['overlap_ms'] and not body.accept_overlap_change: raise HTTPException(409, '请明确确认重叠导致输出时长缩短')
        expected_hash = canonical_hash(payload)
    proof = await verify_publication_sources(factory, project_id, payload)
    async with factory() as db:
        async with transaction(db):
            row = await owned_draft(db, project_id, identifier)
            if row.revision != body.expected_revision: raise HTTPException(409, '媒体校验期间转场修订已变化')
            previous = await db.scalar(select(TransitionPublication.id).where(TransitionPublication.draft_id == identifier, TransitionPublication.draft_revision == row.revision))
            if previous: return previous
            base = await base_publication(db, project_id, row.base_publication_id)
            current, issues = compose(base, row.document)
            if issues or canonical_hash(current) != expected_hash: raise HTTPException(409, '媒体校验期间转场资料已变化')
            for source in proof.values(): await recheck_media(db, project_id, source)
            identifier = str(uuid.uuid4())
            db.add(TransitionPublication(id=identifier, project_id=project_id, chapter_id=row.chapter_id, draft_id=row.id, draft_revision=row.revision, content_hash=canonical_hash(current), payload=current))
    return identifier


async def publication_view(db, project_id, identifier):
    """Return safe v2 evidence with immutable original source PTS and explicit output overlap."""
    row = await owned_publication(db, project_id, identifier)
    return {'id': row.id, 'project_id': project_id, 'chapter_id': row.chapter_id, 'draft_revision': row.draft_revision, 'content_hash': row.content_hash, 'payload': public_payload(row.payload), 'model_calls': 0}
