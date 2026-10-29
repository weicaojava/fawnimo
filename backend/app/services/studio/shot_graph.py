"""Atomic structural editing, with exact graph CAS and no borrowed source/adoption identities."""
import copy
import hashlib
import json
import uuid
from datetime import date, datetime

from fastapi import HTTPException
from sqlalchemy import JSON, delete, select, text, update
from sqlalchemy.exc import IntegrityError, OperationalError

from app.core.db import Base
from app.models.shot_graph import ShotGraphCursor, ShotGraphReceipt
from app.models.studio import (Chapter, Shot, ShotDetail, ShotDialogLine, ShotFrameImage,
    ShotCharacterLink, ShotExtractedCandidate, ShotExtractedDialogueCandidate,
    ProjectActorLink, ProjectSceneLink, ProjectPropLink, ProjectCostumeLink, Character)
from app.models.studio_file_usages import FileUsage
from app.models.task import GenerationTask
from app.models.task_links import GenerationTaskLink
from app.models.shot_plan import PlanJobLink
from app.models.execution import ExecutionJob
from app.services.worker.execution_budget import transaction

MAX_SHOTS = 500
MAX_ROWS = 10000
MAX_BYTES = 2 * 1024 * 1024
LINKS = (ProjectActorLink, ProjectSceneLink, ProjectPropLink, ProjectCostumeLink)
GRAPH_MODELS = (ShotDetail, ShotDialogLine, ShotFrameImage, ShotCharacterLink,
                ShotExtractedCandidate, ShotExtractedDialogueCandidate, *LINKS, FileUsage)
RECOVERY_NOTE = '合并前镜头和完整准备资料保存在此收据。源镜头行已删除，视频文件不删除；新镜头为待确认，未继承生成任务、采用结果或原片时钟。收据用于核对和人工重建，不会自动恢复采用资格。'


def json_value(value):
    """Preserve all saved user fields in a deterministic JSON receipt without timezone guessing."""
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, dict):
        return {str(key): json_value(item) for key, item in value.items()}
    if isinstance(value, (tuple, list)):
        return [json_value(item) for item in value]
    return value


def canonical(value):
    """Hash exact bounded structure data; nonfinite JSON is never silently rewritten."""
    return json.dumps(json_value(value), ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False)


def fingerprint(value):
    """Compare every saved detail/link/candidate, not second-resolution updated_at alone."""
    return hashlib.sha256(canonical(value).encode()).hexdigest()


async def chapter_owned(db, project_id, chapter_id):
    """Resolve chapter ownership before reading structure data or request receipts."""
    chapter = await db.get(Chapter, chapter_id)
    if chapter is None or chapter.project_id != project_id:
        raise HTTPException(404, '本工程章节不存在')
    return chapter


async def rows(db, table, condition):
    """Read complete Core rows so bulk SQL writes cannot leave cached ORM timestamps in a CAS token."""
    result = list((await db.execute(select(table).where(condition).order_by(*table.primary_key.columns).limit(MAX_ROWS + 1))).mappings())
    if len(result) > MAX_ROWS:
        raise HTTPException(409, '章节关联记录超过结构编辑上限，请先拆分章节')
    return [json_value(dict(row)) for row in result]


async def snapshot(db, project_id, chapter_id):
    """Capture all source preparation and file-usage context; the snapshot does not mint a source clock."""
    await chapter_owned(db, project_id, chapter_id)
    shots = await rows(db, Shot.__table__, Shot.chapter_id == chapter_id)
    if len(shots) > MAX_SHOTS:
        raise HTTPException(409, '章节超过500镜，请先拆分章节')
    shots.sort(key=lambda item: (item['index'], item['id']))
    identifiers = [item['id'] for item in shots]
    data = {'project_id': project_id, 'chapter_id': chapter_id, 'shots': shots}
    for model in GRAPH_MODELS:
        key = 'id' if model is ShotDetail else ('shot_detail_id' if model in (ShotDialogLine, ShotFrameImage) else 'shot_id')
        data[model.__tablename__] = await rows(db, model.__table__, getattr(model, key).in_(identifiers)) if identifiers else []
    if len(canonical(data).encode()) > MAX_BYTES:
        raise HTTPException(409, '完整章节准备资料超过2MiB，结构操作未开始')
    return data


def receipt_summary(row):
    """Expose only a durable operation pointer in the normal chapter response."""
    return {key: getattr(row, key) for key in ('id', 'action', 'revision', 'result_shot_id', 'source_shot_ids')}


async def state(db, project_id, chapter_id, data=None):
    """Return complete actual order plus recent receipts; this GET never creates a cursor."""
    data = data if data is not None else await snapshot(db, project_id, chapter_id)
    cursor = await db.get(ShotGraphCursor, chapter_id)
    receipts = (await db.execute(select(ShotGraphReceipt).where(ShotGraphReceipt.chapter_id == chapter_id, ShotGraphReceipt.project_id == project_id).order_by(ShotGraphReceipt.revision.desc()).limit(10))).scalars().all()
    return {'project_id': project_id, 'chapter_id': chapter_id, 'revision': cursor.revision if cursor else 0,
            'fingerprint': fingerprint(data), 'shots': [{key: shot[key] for key in ('id', 'index', 'title', 'status')} for shot in data['shots']],
            'receipts': [receipt_summary(row) for row in receipts]}


async def read_state(factory, project_id, chapter_id):
    """Read a full structural CAS token from an application-owned database factory."""
    async with factory() as db:
        return await state(db, project_id, chapter_id)


async def read_receipt(factory, project_id, receipt_id):
    """Return original graph backup data only for its actual owning project."""
    async with factory() as db:
        receipt = await db.get(ShotGraphReceipt, receipt_id)
        if receipt is None or receipt.project_id != project_id:
            raise HTTPException(404, '本工程结构操作收据不存在')
        await chapter_owned(db, project_id, receipt.chapter_id)
        return {'project_id': project_id, 'chapter_id': receipt.chapter_id, 'receipt': receipt_summary(receipt),
                'before': receipt.before, 'after_fingerprint': receipt.after_fingerprint, 'recovery_note': RECOVERY_NOTE}


async def quiescent(db, identifiers, chapter_id):
    """Do not change upstream structure while either current or legacy generation work is active."""
    scope = [chapter_id, *identifiers]
    active = await db.scalar(select(GenerationTask.id).join(GenerationTaskLink, GenerationTaskLink.task_id == GenerationTask.id).where(GenerationTaskLink.relation_entity_id.in_(scope), GenerationTask.status.in_(['pending', 'running', 'streaming'])).limit(1))
    planned = await db.scalar(select(ExecutionJob.id).join(PlanJobLink, PlanJobLink.job_id == ExecutionJob.id).where(PlanJobLink.shot_id.in_(identifiers), ExecutionJob.state.not_in(['ready', 'failed', 'cancelled'])).limit(1))
    parameters = {f'scope{index}': identity for index, identity in enumerate(scope)}
    placeholders = ','.join(':' + key for key in parameters)
    unlinked = False
    for table, column, predicate in (('generation_tasks', 'payload', "document.status IN ('pending','running','streaming')"), ('execution_jobs', 'snapshot', "document.state NOT IN ('ready','failed','cancelled')")):
        query = text(f'SELECT 1 FROM "{table}" AS document, json_tree(document."{column}") AS item WHERE {predicate} AND ((item.type=\'text\' AND item.atom IN ({placeholders})) OR item.key IN ({placeholders})) LIMIT 1')
        if (await db.execute(query, parameters)).first():
            unlinked = True
            break
    if active or planned or unlinked:
        raise HTTPException(409, '章节仍有生成或待核对任务；请完成或取消后再修改镜头结构')


async def merge_unreferenced(db, identifiers):
    """Protect every foreign-key and exact JSON reference, including immutable source-clock receipts."""
    mutable = {model.__tablename__ for model in GRAPH_MODELS}
    for table in Base.metadata.sorted_tables:
        if table.name in mutable:
            continue
        for column in table.columns:
            if any(foreign.target_fullname == 'shots.id' for foreign in column.foreign_keys):
                if (await db.execute(select(column).where(column.in_(identifiers)).limit(1))).first():
                    raise HTTPException(409, '合并源镜头已被采用、执行或历史记录引用，不能删除；请复制为新的待确认资料后合并')
    parameters = {f'shot{index}': identity for index, identity in enumerate(identifiers)}
    placeholders = ','.join(':' + key for key in parameters)
    # Scan application-owned JSON columns, including future source/plan documents. Audit snapshots
    # intentionally keep historical string IDs and must not make every subsequent edit self-lock.
    for table in Base.metadata.sorted_tables:
        if table.name in mutable or table.name in {'shots', 'shot_graph_receipts'}:
            continue
        for column in table.columns:
            if not isinstance(column.type, JSON):
                continue
            query = text(f'SELECT 1 FROM "{table.name}" AS document, json_tree(document."{column.name}") AS item WHERE (item.type = \'text\' AND item.atom IN ({placeholders})) OR item.key IN ({placeholders}) LIMIT 1')
            if (await db.execute(query, parameters)).first():
                raise HTTPException(409, '合并源镜头已在剧本、DNA原片资料、计划或执行/剪辑文档中引用，不能删除；请先复制为新的待确认资料')
    timeline = Base.metadata.tables.get('timeline_clips')
    if timeline is not None and (await db.execute(select(timeline.c.id).where(timeline.c.source_id.in_(identifiers)).limit(1))).first():
        raise HTTPException(409, '合并源镜头仍被旧剪辑时间线引用，不能删除')
    if await db.scalar(select(GenerationTaskLink.id).where(GenerationTaskLink.relation_entity_id.in_(identifiers)).limit(1)):
        raise HTTPException(409, '合并源镜头仍有历史生成记录，不能转移其生成身份')


async def normalize_order(db, identifiers):
    """Vacate unique indices before assigning contiguous order, all inside the same writer transaction."""
    existing = list((await db.execute(select(Shot.index).where(Shot.id.in_(identifiers)))).scalars())
    minimum = min([0, *existing]) - len(identifiers) - 10
    if minimum < -(2**62):
        raise HTTPException(409, '旧镜头序号超过安全范围，未执行结构修改')
    for index, identity in enumerate(identifiers):
        await db.execute(update(Shot).where(Shot.id == identity).values(index=minimum - index).execution_options(synchronize_session=False))
    for index, identity in enumerate(identifiers, 1):
        await db.execute(update(Shot).where(Shot.id == identity).values(index=index).execution_options(synchronize_session=False))


def stripped(row, *extra):
    """Copy saved preparation fields without old identity, timestamps or confirmation pointers."""
    excluded = {'id', 'created_at', 'updated_at', *extra}
    return {key: copy.deepcopy(value) for key, value in row.items() if key not in excluded}


def combined(sources, details, field):
    """Keep shot boundaries explicit when combining human-editable prose and prompts."""
    return '\n\n'.join(f"[{shot['title']}]\n{details.get(shot['id'], {}).get(field, '')}" for shot in sources if details.get(shot['id'], {}).get(field))


async def merge_graph(db, data, sources, title):
    """Create a new unqualified preparation graph; originals are removed only after full cloning succeeds."""
    identifiers = [shot['id'] for shot in sources]
    await merge_unreferenced(db, identifiers)
    project_id, chapter_id = data['project_id'], data['chapter_id']
    identity = str(uuid.uuid4())
    details = {row['id']: row for row in data['shot_details'] if row['id'] in identifiers}
    for model in LINKS:
        for row in data[model.__tablename__]:
            if row['shot_id'] in identifiers and (row['project_id'] != project_id or row['chapter_id'] not in (None, chapter_id)):
                raise HTTPException(409, '源镜头资产关联归属不一致，未执行合并')
    characters = {row['character_id'] for row in data['shot_character_links'] if row['shot_id'] in identifiers}
    for line in data['shot_dialog_lines']:
        if line['shot_detail_id'] in identifiers:
            characters.update(value for value in (line['speaker_character_id'], line['target_character_id']) if value)
    if characters:
        owned = set((await db.execute(select(Character.id).where(Character.id.in_(characters), Character.project_id == project_id))).scalars())
        if owned != characters:
            raise HTTPException(409, '源镜头含其他工程角色，未执行合并')
    excerpt = '\n\n'.join(f"[{shot['title']}]\n{shot['script_excerpt']}" for shot in sources)
    temporary = max([0, *[shot['index'] for shot in data['shots']]]) + 1
    if temporary >= 2**62:
        raise HTTPException(409, '旧镜头序号超过安全范围')
    db.add(Shot(id=identity, chapter_id=chapter_id, index=temporary, title=title or '合并分镜', script_excerpt=excerpt,
                status='pending', skip_extraction=False, last_extracted_at=None, generated_video_file_id=None, thumbnail=''))
    await db.flush()
    first = next((details[shot['id']] for shot in sources if shot['id'] in details), None)
    preparation = stripped(first or {}, 'scene_id')
    preparation.update(camera_shot=preparation.get('camera_shot', 'MS'), angle=preparation.get('angle', 'EYE_LEVEL'), movement=preparation.get('movement', 'STATIC'))
    scenes = {detail.get('scene_id') for detail in details.values()}
    preparation['scene_id'] = scenes.pop() if len(scenes) == 1 else None
    preparation['duration'] = sum(detail.get('duration', 0) for detail in details.values())
    if not 0 <= preparation['duration'] <= 300:
        raise HTTPException(422, '合并后的计划时长须在0至300秒内；不会截断或伪造源时钟')
    for field in ('description', 'atmosphere', 'vfx_note', 'first_frame_prompt', 'last_frame_prompt', 'key_frame_prompt'):
        preparation[field] = combined(sources, details, field)
    preparation['description'] = '合并准备资料，待重新确认。摄影参数沿用首个有细节的源镜头；计划时长相加不代表原片时钟拼接。\n' + preparation['description']
    preparation['action_beats'] = [beat for shot in sources for beat in details.get(shot['id'], {}).get('action_beats', [])]
    preparation['mood_tags'] = list(dict.fromkeys(tag for shot in sources for tag in details.get(shot['id'], {}).get('mood_tags', [])))
    preparation['has_bgm'] = any(detail.get('has_bgm') for detail in details.values())
    db.add(ShotDetail(id=identity, **preparation))
    await db.flush()
    dialogue_index = 0
    candidate_index = 0
    seen_characters, seen_candidates = set(), set()
    for shot in sources:
        for line in sorted((row for row in data['shot_dialog_lines'] if row['shot_detail_id'] == shot['id']), key=lambda row: (row['index'], row['id'])):
            db.add(ShotDialogLine(shot_detail_id=identity, index=dialogue_index, **stripped(line, 'shot_detail_id', 'index')))
            dialogue_index += 1
        for link in sorted((row for row in data['shot_character_links'] if row['shot_id'] == shot['id']), key=lambda row: (row['index'], row['id'])):
            if link['character_id'] not in seen_characters:
                db.add(ShotCharacterLink(shot_id=identity, index=len(seen_characters), **stripped(link, 'shot_id', 'index')))
                seen_characters.add(link['character_id'])
        for row in (row for row in data['shot_extracted_candidates'] if row['shot_id'] == shot['id']):
            key = (row['candidate_type'], row['candidate_name'])
            if key not in seen_candidates:
                db.add(ShotExtractedCandidate(shot_id=identity, **{**stripped(row, 'shot_id'), 'candidate_status': 'pending', 'linked_entity_id': None, 'confirmed_at': None}))
                seen_candidates.add(key)
        for row in sorted((row for row in data['shot_extracted_dialogue_candidates'] if row['shot_id'] == shot['id']), key=lambda row: (row['index'], row['id'])):
            db.add(ShotExtractedDialogueCandidate(shot_id=identity, index=candidate_index, **{**stripped(row, 'shot_id', 'index'), 'candidate_status': 'pending', 'linked_dialog_line_id': None, 'confirmed_at': None}))
            candidate_index += 1
    for model in LINKS:
        asset_key = {'project_actor_links': 'actor_id', 'project_scene_links': 'scene_id', 'project_prop_links': 'prop_id', 'project_costume_links': 'costume_id'}[model.__tablename__]
        identities = dict.fromkeys(row[asset_key] for row in data[model.__tablename__] if row['shot_id'] in identifiers)
        for asset_id in identities:
            db.add(model(project_id=project_id, chapter_id=chapter_id, shot_id=identity, **{asset_key: asset_id}))
    # Three editable slots choose deterministic original references; every alternative stays in the receipt.
    frames = [row for row in data['shot_frame_images'] if row['shot_detail_id'] in identifiers]
    for kind in ('first', 'key', 'last'):
        order = list(reversed(identifiers)) if kind == 'last' else identifiers
        chosen = next((row for source in order for row in frames if row['shot_detail_id'] == source and row['frame_type'] == kind), None)
        if chosen:
            db.add(ShotFrameImage(shot_detail_id=identity, **stripped(chosen, 'shot_detail_id')))
    await db.flush()
    for model in LINKS:
        await db.execute(delete(model).where(model.shot_id.in_(identifiers)))
    await db.execute(delete(Shot).where(Shot.id.in_(identifiers)))
    await db.flush()
    return identity


async def mutate(factory, body):
    """Commit one graph change and receipt atomically; exact replay precedes stale-state checks."""
    digest = fingerprint(body.model_dump(exclude={'request_key'}))
    try:
        async with factory() as db:
            if db.bind.dialect.name != 'sqlite':
                raise HTTPException(501, '结构原子编辑当前仅支持本地SQLite数据库')
            async with transaction(db):
                await chapter_owned(db, body.project_id, body.chapter_id)
                previous = (await db.execute(select(ShotGraphReceipt).where(ShotGraphReceipt.project_id == body.project_id, ShotGraphReceipt.request_key == body.request_key))).scalar_one_or_none()
                if previous:
                    if previous.request_hash != digest or previous.chapter_id != body.chapter_id:
                        raise HTTPException(409, '请求标识已用于其他镜头结构操作')
                    return {'receipt': receipt_summary(previous), 'replayed': True, 'graph': await state(db, body.project_id, body.chapter_id)}
                before = await snapshot(db, body.project_id, body.chapter_id)
                cursor = await db.get(ShotGraphCursor, body.chapter_id)
                revision = cursor.revision if cursor else 0
                if revision != body.expected_revision or fingerprint(before) != body.expected_fingerprint:
                    raise HTTPException(409, '镜头顺序或准备资料已被更新；未执行操作，请刷新后重新确认')
                identifiers = [shot['id'] for shot in before['shots']]
                await quiescent(db, identifiers, body.chapter_id)
                result_id = None
                if body.action == 'reorder':
                    if len(body.ordered_shot_ids) != len(identifiers) or set(body.ordered_shot_ids) != set(identifiers):
                        raise HTTPException(422, '重排必须精确包含本章全部镜头，不能遗漏或混入其他章节')
                    order = body.ordered_shot_ids
                elif body.action == 'insert':
                    if len(identifiers) >= MAX_SHOTS:
                        raise HTTPException(409, '本章已达到500镜上限')
                    if body.after_shot_id is not None and body.after_shot_id not in identifiers:
                        raise HTTPException(422, '插入位置不属于本章')
                    temporary = max([0, *[shot['index'] for shot in before['shots']]]) + 1
                    if temporary >= 2**62:
                        raise HTTPException(409, '旧镜头序号超过安全范围')
                    result_id = str(uuid.uuid4())
                    db.add(Shot(id=result_id, chapter_id=body.chapter_id, index=temporary, title=body.title or '新分镜', status='pending', skip_extraction=False, generated_video_file_id=None))
                    await db.flush()
                    db.add(ShotDetail(id=result_id, camera_shot='MS', angle='EYE_LEVEL', movement='STATIC', duration=0))
                    position = identifiers.index(body.after_shot_id) + 1 if body.after_shot_id else 0
                    order = identifiers[:position] + [result_id] + identifiers[position:]
                else:
                    selected = set(body.source_shot_ids)
                    sources = [shot for shot in before['shots'] if shot['id'] in selected]
                    positions = [identifiers.index(shot['id']) for shot in sources]
                    if len(sources) != len(selected) or positions != list(range(min(positions, default=-1), max(positions, default=-1) + 1)):
                        raise HTTPException(422, '只能合并本章至少两个连续相邻的镜头')
                    result_id = await merge_graph(db, before, sources, body.title)
                    order = identifiers[:positions[0]] + [result_id] + identifiers[positions[-1] + 1:]
                await db.flush()
                await normalize_order(db, order)
                if cursor:
                    changed = await db.execute(update(ShotGraphCursor).where(ShotGraphCursor.chapter_id == body.chapter_id, ShotGraphCursor.revision == revision).values(revision=revision + 1))
                    if changed.rowcount != 1:
                        raise HTTPException(409, '镜头结构修订冲突，操作已回滚')
                else:
                    db.add(ShotGraphCursor(chapter_id=body.chapter_id, project_id=body.project_id, revision=1))
                await db.flush()
                after = await snapshot(db, body.project_id, body.chapter_id)
                receipt = ShotGraphReceipt(id=str(uuid.uuid4()), project_id=body.project_id, chapter_id=body.chapter_id,
                    request_key=body.request_key, request_hash=digest, revision=revision + 1, action=body.action,
                    source_shot_ids=body.source_shot_ids, result_shot_id=result_id, before=before, after_fingerprint=fingerprint(after))
                db.add(receipt)
                await db.flush()
                return {'receipt': receipt_summary(receipt), 'replayed': False, 'graph': await state(db, body.project_id, body.chapter_id, after)}
    except IntegrityError:
        raise HTTPException(409, '镜头结构存在新的关联或序号冲突，整个操作已回滚') from None
    except OperationalError as error:
        if 'locked' in str(error).lower() or 'busy' in str(error).lower():
            raise HTTPException(409, '另一项保存正在执行，未重复操作；请刷新或核对原请求') from None
        raise
