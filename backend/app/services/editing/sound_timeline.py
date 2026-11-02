"""Independent sound-v3 assembly over unchanged picture and original-dialogue rules."""
import copy
import asyncio
import uuid
from types import SimpleNamespace
from fastapi import HTTPException
from sqlalchemy import select, update
from app.core.contracts.sound_timeline import SoundDocument
from app.models.production import ProductionMedia
from app.models.sound_timeline import SoundCandidate, SoundDraft, SoundPublication
from app.services.editing.common import chapter_owned, canonical, issue, owned_plan, owned_take, public_payload
from app.services.editing.media import canonical_hash, probe_owned, recheck_media
from app.services.editing.sound_media import probe_wave
from app.services.editing.timeline import assemble as assemble_picture, validate_document, upstream_state
from app.services.editing.transitions import compose
from app.services.worker.execution_budget import transaction

PROFILE = 'portrait-360p30-sound-v3'


async def owned_candidate(db, project_id, identifier, chapter_id=None):
    """Resolve immutable candidate scope before exposing any media or sample facts."""
    row = await db.get(SoundCandidate, identifier)
    if row is None or row.project_id != project_id or chapter_id is not None and row.chapter_id != chapter_id:
        raise HTTPException(404, '本工程本章声音候选不存在')
    return row


def candidate_view(row):
    """Return only logical identities and actual audio facts, never server storage paths."""
    return {'id': row.id, 'project_id': row.project_id, 'chapter_id': row.chapter_id, 'media_id': row.media_id,
        'sha256': row.evidence['sha256'], 'name': row.name, 'kind': row.kind, 'origin': row.origin,
        'audio': row.evidence['audio'], 'model_quality_qualified': False}


async def import_candidate(factory, project_id, body):
    """Decode once outside the writer; replay precedes current file/runtime eligibility."""
    if not body.request_key.strip() or not body.name.strip(): raise HTTPException(422, '候选名称和请求标识不能为空')
    intent_hash = canonical_hash({'domain': 'sound-import-v1', **body.model_dump()})
    async with factory() as db:
        await chapter_owned(db, project_id, body.chapter_id)
        old = await db.scalar(select(SoundCandidate).where(SoundCandidate.project_id == project_id, SoundCandidate.request_key == body.request_key))
        if old:
            if old.intent_hash != intent_hash: raise HTTPException(409, '相同请求标识已用于另一声音候选')
            return candidate_view(old)
        facts = await probe_wave(db, project_id, body.media_id)
        facts['audio'].pop('interval_peaks_pcm16')
        if facts['sha256'] != body.expected_sha256: raise HTTPException(409, '声音素材已改变，请核对后重新选择')
    async with factory() as db:
        async with transaction(db):
            await chapter_owned(db, project_id, body.chapter_id)
            old = await db.scalar(select(SoundCandidate).where(SoundCandidate.project_id == project_id, SoundCandidate.request_key == body.request_key))
            if old:
                if old.intent_hash != intent_hash: raise HTTPException(409, '相同请求标识已用于另一声音候选')
                return candidate_view(old)
            count = len((await db.execute(select(SoundCandidate.id).where(SoundCandidate.project_id == project_id, SoundCandidate.chapter_id == body.chapter_id).limit(201))).all())
            if count >= 200: raise HTTPException(409, '本章声音候选已达200项上限')
            await recheck_media(db, project_id, facts)
            row = SoundCandidate(id=str(uuid.uuid4()), project_id=project_id, chapter_id=body.chapter_id, media_id=body.media_id,
                request_key=body.request_key, intent_hash=intent_hash, kind=body.kind, name=body.name,
                origin='user_import', evidence=facts)
            db.add(row)
            await db.flush()
            return candidate_view(row)


async def list_candidates(db, project_id, chapter_id):
    """List bounded actual candidates; missing sources remain visible as history."""
    await chapter_owned(db, project_id, chapter_id)
    rows = (await db.execute(select(SoundCandidate).where(SoundCandidate.project_id == project_id, SoundCandidate.chapter_id == chapter_id).order_by(SoundCandidate.id).limit(201))).scalars().all()
    if len(rows) > 200: raise HTTPException(409, '本章声音候选超过200项')
    return [candidate_view(row) for row in rows]


async def validate_form(db, project_id, chapter_id, document):
    """Validate all logical identities without decoding inside a draft writer transaction."""
    data = SoundDocument.model_validate(document).model_dump()
    if len(canonical(data).encode()) > 1024 * 1024: raise HTTPException(422, '声音文档超过1MiB')
    await validate_document(db, project_id, chapter_id, data['picture'])
    track_ids = set()
    for track in data['tracks']:
        if track['id'] in track_ids: raise HTTPException(422, '声音轨道标识不能重复')
        track_ids.add(track['id'])
        candidate = await owned_candidate(db, project_id, track['candidate_id'], chapter_id)
        if track['dialogue_id'] is not None and candidate.kind != 'voice': raise HTTPException(422, '只有明确采用的配音候选可以对应原对白')
    return data


async def prepare_proof(db, project_id, chapter_id, data):
    """Bound aggregate work before decoding each unique project source exactly once."""
    video_ids, video_bytes = set(), 0
    for clip in data['picture']['clips']:
        take = await owned_take(db, project_id, clip['take_id'], clip['shot_id'])
        if take.media_id in video_ids: continue
        row = await db.get(ProductionMedia, take.media_id)
        if row is None or row.project_id != project_id: raise HTTPException(404, '本工程视频不存在')
        video_ids.add(row.id); video_bytes += row.size
    intervals, wave_bytes = {}, 0
    for track in data['tracks']:
        candidate = await owned_candidate(db, project_id, track['candidate_id'], chapter_id)
        if candidate.media_id not in intervals: wave_bytes += candidate.evidence['size']
        intervals.setdefault(candidate.media_id, []).append([track['source_in_ms'], track['source_out_ms']])
    if video_bytes > 512 * 1024 * 1024 or wave_bytes > 128 * 1024 * 1024 or len(intervals) > 32:
        raise HTTPException(422, '声音版本最多引用512MiB视频和32个、合计128MiB声音素材')
    proof = {'videos': {}, 'waves': {}, 'intervals': {}, 'peaks': {}}
    for identifier in sorted(video_ids): proof['videos'][identifier] = await probe_owned(db, project_id, identifier)
    return proof


async def sound_intervals(db, project_id, chapter_id, data, cues):
    """Map every adopted trim and covered original subtitle interval to its actual WAV sample clock."""
    intervals, positions = {}, {}
    for track in data['tracks']:
        candidate = await owned_candidate(db, project_id, track['candidate_id'], chapter_id)
        selected = intervals.setdefault(candidate.media_id, [])
        position = {'selected': len(selected), 'dialogue': None}
        selected.append([track['source_in_ms'], track['source_out_ms']])
        cue = cues.get(track['dialogue_id'])
        end = track['timeline_start_ms'] + track['source_out_ms'] - track['source_in_ms']
        if cue is not None and track['timeline_start_ms'] <= cue['start_ms'] < cue['end_ms'] <= end:
            position['dialogue'] = len(selected)
            selected.append([track['source_in_ms'] + cue['start_ms'] - track['timeline_start_ms'],
                track['source_in_ms'] + cue['end_ms'] - track['timeline_start_ms']])
        positions[track['id']] = position
    return intervals, positions


async def assemble(db, project_id, chapter_id, document, verified=None):
    """Enforce every old picture rule; resolve only missing dialogue audio with verified explicit dubs."""
    data = await validate_form(db, project_id, chapter_id, document)
    deadline = asyncio.get_running_loop().time() + 120
    try:
        proof = verified if verified is not None else await asyncio.wait_for(prepare_proof(db, project_id, chapter_id, data), timeout=120)
    except asyncio.TimeoutError as error:
        raise HTTPException(409, '声音版本完整校验超过120秒，请减少素材或分章处理') from error
    picture, problems, _video_proof, changes = await assemble_picture(db, project_id, chapter_id, data['picture'], verified=proof['videos'])
    if picture is None: return None, problems, proof, changes
    base = SimpleNamespace(payload=picture, id='sound-picture-v3', content_hash=canonical_hash(picture), project_id=project_id, chapter_id=chapter_id)
    composed, transition_issues = compose(base, {'schema_version': 2, 'title': data['title'], 'edges': data['edges']})
    problems.extend(transition_issues)
    if composed['overlap_ms']: changes.append('声音版本的交叉淡化使输出时长缩短'+str(composed['overlap_ms'])+'毫秒')
    cues = {cue['dialogue_id']: cue for cue in composed['subtitles']}
    intervals, positions = await sound_intervals(db, project_id, chapter_id, data, cues)
    if verified is None:
        proof['intervals'] = intervals
        for identifier, selections in intervals.items():
            remaining = deadline - asyncio.get_running_loop().time()
            if remaining <= 0: raise HTTPException(409, '声音版本完整校验超过120秒，请减少素材或分章处理')
            try: facts = await asyncio.wait_for(probe_wave(db, project_id, identifier, selections), timeout=remaining)
            except asyncio.TimeoutError as error: raise HTTPException(409, '声音版本完整校验超过120秒，请减少素材或分章处理') from error
            proof['peaks'][identifier] = facts['audio'].pop('interval_peaks_pcm16')
            proof['waves'][identifier] = facts
    elif intervals != proof['intervals']: raise HTTPException(409, '声音采用或原对白区间在校验期间改变')
    tracks, used_dialogues, resolved = [], set(), set()
    for track in data['tracks']:
        candidate = await owned_candidate(db, project_id, track['candidate_id'], chapter_id)
        source = proof['waves'].get(candidate.media_id)
        if source is None: raise HTTPException(409, '声音来源证明不完整')
        await recheck_media(db, project_id, source)
        for key in ('id', 'sha256', 'size', 'mime', 'audio'):
            if source[key] != candidate.evidence[key]: raise HTTPException(409, '声音候选的实际素材或样本时钟已改变')
        end = track['timeline_start_ms'] + track['source_out_ms'] - track['source_in_ms']
        if end > composed['duration_ms']: problems.append(issue('sound_outside', '音轨超出最终输出时长，请明确裁剪，不能隐式截断'))
        peak = proof['peaks'][candidate.media_id][positions[track['id']]['selected']]
        dialogue_index = positions[track['id']]['dialogue']
        dialogue_peak = proof['peaks'][candidate.media_id][dialogue_index] if dialogue_index is not None else None
        cue_id = track['dialogue_id']
        if cue_id is not None:
            if cue_id not in cues: raise HTTPException(422, '配音只能对应本计划已标记的原对白')
            if cue_id in used_dialogues: raise HTTPException(422, '同一原对白只能采用一条明确配音')
            used_dialogues.add(cue_id)
            cue = cues[cue_id]
            if candidate.origin == 'local_speech':
                speech = candidate.evidence.get('local_speech', {})
                if any(speech.get(key) != value for key, value in {'plan_id': picture['plan']['id'], 'plan_hash': picture['plan']['content_hash'],
                    'dialogue_id': cue_id, 'text': cue['text'], 'speaker_id': cue['speaker_id']}.items()):
                    raise HTTPException(409, '系统配音只能关联其原始计划、原对白和说话人；请为新对白生成新的候选')
            if not track['content_confirmed']:
                problems.append(issue('voice_unconfirmed', '请人工试听并确认配音保留原对白文字和说话人', item_id=cue_id))
            elif track['muted'] or track['gain_db'] < -18 or dialogue_peak is not None and dialogue_peak <= 32:
                problems.append(issue('voice_inaudible', '静音、过低音量或无有效信号的音轨不能提供对白覆盖', item_id=cue_id))
            elif track['timeline_start_ms'] > cue['start_ms'] or end < cue['end_ms']:
                problems.append(issue('voice_coverage', '配音的真实采用区间须完整覆盖对应字幕区间', item_id=cue_id))
            else: resolved.add(cue_id)
        candidate_facts = {'id': candidate.id, 'kind': candidate.kind, 'origin': candidate.origin, 'name': candidate.name, 'intent_hash': candidate.intent_hash}
        if candidate.origin == 'local_speech': candidate_facts['local_speech'] = copy.deepcopy(candidate.evidence['local_speech'])
        tracks.append({**track, 'timeline_end_ms': end, 'candidate': candidate_facts,
            'source': copy.deepcopy(source), 'selected_peak_pcm16': peak, 'dialogue_peak_pcm16': dialogue_peak,
            'dialogue': copy.deepcopy(cues[cue_id]) if cue_id is not None else None})
    if intervals != proof['intervals']: raise HTTPException(409, '声音采用区间在校验期间改变')
    problems = [problem for problem in problems if not (problem['code'] == 'dialogue_audio' and problem['item_id'] in resolved)]
    payload = {'schema_version': 3, 'profile': PROFILE, 'project_id': project_id, 'chapter_id': chapter_id, 'document': data,
        'plan': picture['plan'], 'clips': composed['clips'], 'transitions': composed['transitions'], 'tracks': tracks,
        'subtitles': composed['subtitles'], 'duration_ms': composed['duration_ms'], 'overlap_ms': composed['overlap_ms'],
        'coverage': composed['coverage'], 'human_declarations_only': True, 'model_quality_qualified': False,
        'mix_policy': {'audio': '48kHz stereo, explicit integer dB gain, additive mix and sample saturation; no automatic ducking, stretching or loop',
            'dialogue': 'original source coverage or one explicitly confirmed voiced candidate; signal presence never proves spoken words or speaker identity',
            'subtitles': 'original immutable text/speaker and picture-anchor output clock, separate SRT/VTT'}}
    if len(canonical(payload).encode()) > 2 * 1024 * 1024: raise HTTPException(409, '完整声音证据超过2MiB')
    return payload, problems, proof, changes


async def owned_draft(db, project_id, identifier):
    """Resolve a sound draft only in its actual project and chapter."""
    row = await db.get(SoundDraft, identifier)
    if row is None or row.project_id != project_id: raise HTTPException(404, '本工程声音草稿不存在')
    await chapter_owned(db, project_id, row.chapter_id)
    return row


async def owned_publication(db, project_id, identifier):
    """Validate historical hash and ownership without consulting mutable current pointers."""
    row = await db.get(SoundPublication, identifier)
    if row is None or row.project_id != project_id: raise HTTPException(404, '本工程声音版本不存在')
    await chapter_owned(db, project_id, row.chapter_id)
    if canonical_hash(row.payload) != row.content_hash: raise HTTPException(409, '声音版本内容校验失败')
    return row


async def create_draft(factory, project_id, body):
    """Copy an explicitly chosen picture form into an independent sound revision."""
    async with factory() as db:
        async with transaction(db):
            data = await validate_form(db, project_id, body.chapter_id, body.document)
            if await db.scalar(select(SoundDraft.id).where(SoundDraft.project_id == project_id, SoundDraft.chapter_id == body.chapter_id)):
                raise HTTPException(409, '本章已有声音草稿，请打开已有草稿')
            identifier = str(uuid.uuid4())
            db.add(SoundDraft(id=identifier, project_id=project_id, chapter_id=body.chapter_id, revision=1, document=data))
    return identifier


async def patch_draft(factory, project_id, identifier, body):
    """Persist the complete picture/audio form using one CAS revision."""
    async with factory() as db:
        async with transaction(db):
            row = await owned_draft(db, project_id, identifier)
            data = await validate_form(db, project_id, row.chapter_id, body.document)
            result = await db.execute(update(SoundDraft).where(SoundDraft.id == identifier, SoundDraft.revision == body.expected_revision).values(document=data, revision=SoundDraft.revision + 1))
            if result.rowcount != 1: raise HTTPException(409, '声音草稿已被修改，请保留输入并刷新核对')


async def freeze_draft(factory, project_id, identifier, body):
    """Decode outside writers, then compare revision, facts and full payload before locking."""
    async with factory() as db:
        row = await owned_draft(db, project_id, identifier)
        if row.revision != body.expected_revision: raise HTTPException(409, '声音草稿修订已改变')
        previous = await db.scalar(select(SoundPublication.id).where(SoundPublication.draft_id == identifier, SoundPublication.draft_revision == row.revision))
        if previous: return (await owned_publication(db, project_id, previous)).id
        payload, problems, proof, changes = await assemble(db, project_id, row.chapter_id, row.document)
        if problems: raise HTTPException(409, problems[0]['message'])
        plan = await owned_plan(db, project_id, payload['plan']['id'])
        stale, plan_proof = await upstream_state(db, plan)
        if stale: changes.append('上游计划已是历史版本')
        if changes and (not body.acknowledge_changes or not body.change_note.strip()): raise HTTPException(409, '请明确确认裁剪、重叠或历史计划变化，并填写说明')
        payload['change_acknowledgment'] = {'acknowledged': body.acknowledge_changes, 'note': body.change_note, 'changes': changes}
        expected_hash = canonical_hash(payload)
    async with factory() as db:
        async with transaction(db):
            row = await owned_draft(db, project_id, identifier)
            if row.revision != body.expected_revision: raise HTTPException(409, '解码期间声音草稿修订已改变')
            previous = await db.scalar(select(SoundPublication.id).where(SoundPublication.draft_id == identifier, SoundPublication.draft_revision == row.revision))
            if previous: return (await owned_publication(db, project_id, previous)).id
            current, problems, _, changes = await assemble(db, project_id, row.chapter_id, row.document, verified=proof)
            if problems: raise HTTPException(409, problems[0]['message'])
            if plan_proof is not None:
                now_stale, _ = await upstream_state(db, await owned_plan(db, project_id, current['plan']['id']), plan_proof)
                if now_stale != stale: raise HTTPException(409, '上游计划事实在锁定期间改变')
            if stale: changes.append('上游计划已是历史版本')
            current['change_acknowledgment'] = {'acknowledged': body.acknowledge_changes, 'note': body.change_note, 'changes': changes}
            if canonical_hash(current) != expected_hash: raise HTTPException(409, '声音版本来源在锁定期间改变')
            publication_id = str(uuid.uuid4())
            db.add(SoundPublication(id=publication_id, project_id=project_id, chapter_id=row.chapter_id, draft_id=row.id,
                draft_revision=row.revision, content_hash=expected_hash, payload=current))
    return publication_id


async def draft_view(db, project_id, identifier):
    """Show genuine completeness while keeping incomplete or missing media forms editable."""
    row = await owned_draft(db, project_id, identifier)
    try:
        payload, problems, _, changes = await assemble(db, project_id, row.chapter_id, row.document)
    except HTTPException as error:
        payload, problems, changes = None, [issue('source_unavailable', str(error.detail))], []
    publications = (await db.execute(select(SoundPublication).where(SoundPublication.draft_id == row.id).order_by(SoundPublication.draft_revision.desc()).limit(201))).scalars().all()
    if len(publications) > 200: raise HTTPException(409, '声音历史版本超过200份')
    return {'id': row.id, 'project_id': project_id, 'chapter_id': row.chapter_id, 'revision': row.revision, 'document': row.document,
        'duration_ms': payload['duration_ms'] if payload else 0, 'can_freeze': not problems, 'issues': problems, 'changes': changes,
        'publications': [{'id': value.id, 'content_hash': value.content_hash, 'draft_revision': value.draft_revision, 'duration_ms': value.payload['duration_ms']} for value in publications]}


async def publication_view(db, project_id, identifier):
    """Strip all source filesystem witnesses, including each independent sound track."""
    row = await owned_publication(db, project_id, identifier)
    payload = public_payload(row.payload)
    for track in payload['tracks']:
        for key in ('relative_path', 'stat'): track['source'].pop(key, None)
    return {'id': row.id, 'project_id': project_id, 'chapter_id': row.chapter_id, 'draft_revision': row.draft_revision,
        'content_hash': row.content_hash, 'payload': payload, 'model_calls': 0}
