"""Materialize source-backed DNA as pending, editable assets and chapter preparation."""
import copy
import uuid
from fastapi import HTTPException
from sqlalchemy import func, select
from app.core.contracts.dna_production import DnaProductionWrite
from app.core.contracts.shot_plan import PlanDocument
from app.models.dna_production import DnaProductionImport
from app.models.production import ProductionMedia
from app.models.shot_plan import ShotPlanDraft
from app.models.studio import Actor, ActorImage, Chapter, Character, Costume, CostumeImage, Prop, PropImage, Scene, SceneImage, Shot, ShotCharacterLink, ShotDetail, ShotFrameImage, PromptTemplate, ProjectActorLink, ProjectSceneLink, ProjectPropLink, ProjectCostumeLink
from app.schemas.studio.production import AssetWrite
from app.services.shot_plan.compiler import validate_document as validate_plan
from app.services.studio.entity_crud import create_entity
from app.services.studio.production import create_asset, project_exists
from app.services.script_preparation.service import content_hash
from app.services.video_dna import service
from app.services.video_dna.document import validate_document
from app.services.video_dna.source import media_evidence, source_current, verify_source
from app.services.worker.execution_budget import transaction
from app.utils.project_links import upsert_project_link


def identity() -> str:
    """Allocate independent identities; no source identifier can alias another project's row."""
    return str(uuid.uuid4())


def claim_text(claim: dict) -> str:
    """Keep both languages and declared uncertainty visible instead of silently upgrading a guess."""
    status = {'observed': '观察记录', 'inferred': '复现推断', 'unknown': '未知'}[claim['status']]
    lines = [f'[{status}]', claim.get('zh') or '', claim.get('en') or '']
    if claim.get('reason'):
        lines.append('依据/边界：' + claim['reason'])
    if claim.get('evidence'):
        lines.append('原帧证据：' + ', '.join(f"[{e['start_frame']},{e['end_frame']})" for e in claim['evidence']))
    return '\n'.join(line for line in lines if line)


def asset_description(asset: dict) -> str:
    """Preserve fixed asset observations and inferred appearance in the editable description."""
    lines = ['原片拆解草稿，待确认；不是原作者参数。', claim_text(asset['lock_prompt'])]
    for key, claim in asset['fields'].items():
        if claim['status'] != 'unknown' or claim.get('zh') or claim.get('en'):
            lines.append(key + ': ' + claim_text(claim))
    value = '\n'.join(lines)
    if len(value) > 16000:
        raise HTTPException(422, '资产完整描述超过16000字，请先精简DNA；系统不会截断')
    return value


def counts(document: dict) -> dict:
    """Preview actual prospective records, excluding unknown clothing and unknown prompts."""
    kinds = [asset['kind'] for asset in document['assets']]
    costumes = sum(a['kind'] == 'character' and a['fields']['costume']['status'] != 'unknown' and bool(a['fields']['costume'].get('zh') or a['fields']['costume'].get('en')) for a in document['assets'])
    prompts = sum(bool(shot[key].get('zh') or shot[key].get('en')) for shot in document['shots'] for key in ('image_prompt', 'video_prompt'))
    return {'actors': kinds.count('character'), 'characters': kinds.count('character'), 'costumes': costumes, 'scenes': kinds.count('scene'), 'props': kinds.count('prop'), 'shots': len(document['shots']), 'prompts': prompts + len(document['assets']) + costumes}


def add_prompt(db, name: str, category: str, content: str) -> str:
    """Publish a visible editable prompt with declared draft provenance and no default override."""
    template_id = identity()
    db.add(PromptTemplate(id=template_id, category=category, name=name[:255], content=content, preview='原片拆解资料，待确认；不是原作者参数。', variables=[], is_default=False, is_system=False))
    return template_id


async def preview(factory, project_id: str, draft_id: str) -> dict:
    """Describe the pending import without writing a record or granting model eligibility."""
    async with factory() as db:
        draft = await service.owned_draft(db, project_id, draft_id)
        document = validate_document(draft.document, draft.source)
        previous = (await db.execute(select(DnaProductionImport).where(DnaProductionImport.project_id == project_id, DnaProductionImport.draft_id == draft_id))).scalar_one_or_none()
        return {'draft_revision': draft.revision, 'counts': counts(document), 'source_current': await source_current(db, project_id, draft.source), 'already_imported': previous.result if previous else None,
                'status': 'pending_confirmation', 'message': '仅导入为待确认资料，保留观察与推断。不会标记镜头已就绪、人工已签核或启动模型。'}


async def replay(db, project_id: str, draft_id: str, body: DnaProductionWrite, digest: str):
    """Replay an identical original intent before mutable source checks; reject key/revision reuse."""
    rows = (await db.execute(select(DnaProductionImport).where(DnaProductionImport.project_id == project_id, (DnaProductionImport.request_key == body.request_key) | (DnaProductionImport.draft_id == draft_id)))).scalars().all()
    if not rows:
        return None
    row = rows[0]
    if len(rows) != 1 or row.draft_id != draft_id or row.request_hash != digest:
        raise HTTPException(409, f'此草稿或请求标识已导入，收据 {row.id}；新修订不会覆盖旧导入结果，请在已创建章节中编辑或另建草稿。')
    return row.result


async def unique_name(db, model, name: str, suffix: str) -> str:
    """Avoid reusing an unrelated global library identity merely because its name matches."""
    if not name.strip() or len(name) > 120:
        raise HTTPException(422, '导入资产名称须为1至120字，请先编辑DNA')
    if (await db.execute(select(model.id).where(model.name == name).limit(1))).first():
        return name[:103] + ' (' + suffix[:8] + ')'
    return name


async def attach_image(db, model, field: str, entity_id: str, media_id: str | None) -> None:
    """Use the explicitly chosen original reference file, without claiming a generated portrait."""
    if not media_id:
        return
    row = (await db.execute(select(model).where(getattr(model, field) == entity_id).order_by(model.id).limit(1))).scalar_one_or_none()
    if row is None:
        row = model(**{field: entity_id, 'view_angle': 'front', 'quality_level': 'low'})
        db.add(row)
    row.file_id = media_id


async def import_draft(factory, project_id: str, draft_id: str, body: DnaProductionWrite) -> dict:
    """Commit all mappings, chapter rows and an immutable provenance receipt in one transaction."""
    intent = {'project_id': project_id, 'draft_id': draft_id, **body.model_dump(exclude={'request_key'})}
    digest = content_hash(intent)
    async with factory() as db:
        previous = await replay(db, project_id, draft_id, body, digest)
        if previous:
            return previous
        draft = await service.owned_draft(db, project_id, draft_id)
        if draft.revision != body.expected_revision:
            raise HTTPException(409, 'DNA草稿已更新，请刷新后导入')
        source = copy.deepcopy(draft.source)
        document = validate_document(draft.document, source)
        if not document['shots'] or len(document['shots']) > 200:
            raise HTTPException(422, '导入须有1至200个镜头')
        if not await source_current(db, project_id, source):
            raise HTTPException(409, '原片确认切点已变化，请先刷新DNA来源')
        proof = await verify_source(db, project_id, source)
        media_proofs = {}
        media_ids = set(body.asset_media_ids.values()) | set(body.shot_media_ids.values())
        if len(media_ids) > 64:
            raise HTTPException(413, '一次拆解导入最多引用64张图片，请分批准备')
        media_rows = list((await db.execute(select(ProductionMedia).where(ProductionMedia.project_id == project_id, ProductionMedia.id.in_(media_ids)))).scalars()) if media_ids else []
        if sum(row.size for row in media_rows) > 32 * 1024 * 1024:
            raise HTTPException(413, '拆解导入的参考图总量超过32MiB')
        for media_id in media_ids:
            public, private = await media_evidence(db, project_id, media_id)
            if not public['mime'].startswith('image/'):
                raise HTTPException(422, '拆解参考须为本工程图片')
            media_proofs[media_id] = private
        if set(body.asset_media_ids) - {a['id'] for a in document['assets']} or set(body.shot_media_ids) - {s['id'] for s in document['shots']}:
            raise HTTPException(422, '参考图片映射包含不存在的DNA资产或镜头')
        document_hash = content_hash(document)

    async with factory() as db:
        async with transaction(db):
            previous = await replay(db, project_id, draft_id, body, digest)
            if previous:
                return previous
            draft = await service.owned_draft(db, project_id, draft_id)
            if draft.revision != body.expected_revision or content_hash(draft.document) != document_hash or draft.source != source:
                raise HTTPException(409, '导入前DNA内容已变化，请刷新')
            if not await source_current(db, project_id, source):
                raise HTTPException(409, '导入前原片确认切点已变化')
            await verify_source(db, project_id, source, proof)
            for media_id, media_proof in media_proofs.items():
                await media_evidence(db, project_id, media_id, media_proof)
            project = await project_exists(db, project_id)
            receipt_id = identity()
            mapping = {}
            asset_templates = []
            for asset in document['assets']:
                name = asset['name_zh'] or asset['name_en'] or asset['id']
                description = asset_description(asset)
                kind = asset['kind']
                if kind in ('scene', 'prop'):
                    name = await unique_name(db, Scene if kind == 'scene' else Prop, name, receipt_id)
                media_id = body.asset_media_ids.get(asset['id'])
                created = await create_asset(db, project_id, AssetWrite(kind=kind, name=name, description=description, media_ids=[media_id] if media_id else []))
                binding = {'kind': kind, 'entity_id': created['asset']['entity_id'], 'asset_id': created['asset']['id'], 'version_id': created['version']['id']}
                if kind == 'character':
                    actor_id = identity()
                    actor_name = await unique_name(db, Actor, name, receipt_id)
                    await create_entity(db, entity_type='actor', body={'id': actor_id, 'project_id': project_id, 'name': actor_name, 'description': description, 'tags': ['原片拆解', '待确认'], 'style': project.style, 'visual_style': project.visual_style})
                    character = await db.get(Character, binding['entity_id'])
                    character.actor_id = actor_id
                    binding['actor_id'] = actor_id
                    actor = await db.get(Actor, actor_id)
                    actor.prompt_template_id = add_prompt(db, name + ' / 原片人物提示词草稿', 'actor_image_front', description)
                    asset_templates.append(actor.prompt_template_id)
                    await attach_image(db, ActorImage, 'actor_id', actor_id, media_id)
                    costume = asset['fields']['costume']
                    if costume['status'] != 'unknown' and (costume.get('zh') or costume.get('en')):
                        costume_id = identity()
                        costume_name = await unique_name(db, Costume, name[:100] + '服装', receipt_id)
                        costume_description = claim_text(costume)
                        await create_entity(db, entity_type='costume', body={'id': costume_id, 'project_id': project_id, 'name': costume_name, 'description': costume_description, 'tags': ['原片拆解', '待确认'], 'style': project.style, 'visual_style': project.visual_style})
                        character.costume_id = costume_id
                        costume_row = await db.get(Costume, costume_id)
                        costume_row.prompt_template_id = add_prompt(db, costume_name + ' / 原片服装提示词草稿', 'costume_image_front', costume_description)
                        asset_templates.append(costume_row.prompt_template_id)
                        await attach_image(db, CostumeImage, 'costume_id', costume_id, media_id)
                        appearance = await create_asset(db, project_id, AssetWrite(kind='appearance', name=costume_name, subject_id=character.id, description=costume_description, media_ids=[media_id] if media_id else []))
                        binding.update(costume_id=costume_id, appearance_asset_id=appearance['asset']['id'], appearance_version_id=appearance['version']['id'])
                else:
                    entity = await db.get(Scene if kind == 'scene' else Prop, binding['entity_id'])
                    entity.prompt_template_id = add_prompt(db, name + ' / 原片资产提示词草稿', 'scene_image_front' if kind == 'scene' else 'prop_image_front', description)
                    asset_templates.append(entity.prompt_template_id)
                    await attach_image(db, SceneImage if kind == 'scene' else PropImage, 'scene_id' if kind == 'scene' else 'prop_id', binding['entity_id'], media_id)
                mapping[asset['id']] = binding
            chapter_id = identity()
            chapter_index = int((await db.execute(select(func.max(Chapter.index)).where(Chapter.project_id == project_id))).scalar() or 0) + 1
            db.add(Chapter(id=chapter_id, project_id=project_id, index=chapter_index, title=body.chapter_title or document['title'][:120] or '原片拆解草稿', summary='从原帧证据导入，观察与推断均待确认；不是原作者剧本。', raw_text='\n\n'.join(claim_text(s['action']) for s in document['shots']), storyboard_count=len(document['shots']), status='draft'))
            await db.flush()
            shot_ids, templates, choices, frame_bindings = [], list(asset_templates), [], []
            for index, shot in enumerate(document['shots'], 1):
                shot_id = identity()
                shot_ids.append(shot_id)
                start = source['frames'][shot['start_frame']]['relative_us']
                end = source['frames'][shot['end_frame']]['relative_us'] if shot['end_frame'] < len(source['frames']) else None
                planned_duration = (end - start + 999999) // 1000000 if end is not None else 0
                image_prompt = shot['image_prompt'].get('zh') or shot['image_prompt'].get('en') or ''
                video_prompt = shot['video_prompt'].get('zh') or shot['video_prompt'].get('en') or ''
                template_ids = {}
                for key, category in [('image_prompt', 'frame_head_image'), ('video_prompt', 'video_prompt')]:
                    if shot[key].get('zh') or shot[key].get('en'):
                        template_id = identity()
                        db.add(PromptTemplate(id=template_id, category=category, name=f'{project.name[:80]} / 镜头{index} / {"图像" if key == "image_prompt" else "视频"}草稿', content=claim_text(shot[key]), preview='原片拆解推断，待确认；不是原作者参数。', variables=[], is_default=False, is_system=False))
                        templates.append(template_id)
                        template_ids[key] = template_id
                media_id = body.shot_media_ids.get(shot['id'])
                thumbnail = f'/api/v1/studio/files/{media_id}/download' if media_id else f"/api/v1/reverse/projects/{project_id}/analyses/{source['analysis_id']}/frames/{shot['start_frame']}"
                db.add(Shot(id=shot_id, chapter_id=chapter_id, index=index, title=f'镜头{index} / {shot["id"]}', thumbnail=thumbnail, status='pending', skip_extraction=False, script_excerpt=claim_text(shot['action'])))
                await db.flush()
                db.add(ShotDetail(id=shot_id, camera_shot='MS', angle='EYE_LEVEL', movement='STATIC', scene_id=mapping[shot['scene_id']]['entity_id'] if shot['scene_id'] else None, duration=planned_duration,
                    description='原片拆解待确认。景别/机位/运镜为可编辑占位，准确声明见下文；整数时长仅为制作计划，末镜端点未知。\n' + '\n'.join(claim_text(shot[k]) for k in ('composition', 'camera', 'lighting', 'continuity')), first_frame_prompt=image_prompt, prompt_template_id=template_ids.get('video_prompt'), action_beats=[shot['action'].get('zh') or shot['action'].get('en') or '动作待确认']))
                await db.flush()
                version_ids = []
                for character_index, character_local_id in enumerate(shot['character_ids']):
                    binding = mapping[character_local_id]
                    db.add(ShotCharacterLink(shot_id=shot_id, character_id=binding['entity_id'], index=character_index, note='原片拆解草稿，待确认'))
                    await upsert_project_link(db, model=ProjectActorLink, asset_field='actor_id', asset_id=binding['actor_id'], project_id=project_id, chapter_id=chapter_id, shot_id=shot_id)
                    version_ids.append(binding['version_id'])
                    if binding.get('appearance_version_id'):
                        version_ids.append(binding['appearance_version_id'])
                        await upsert_project_link(db, model=ProjectCostumeLink, asset_field='costume_id', asset_id=binding['costume_id'], project_id=project_id, chapter_id=chapter_id, shot_id=shot_id)
                for local_id in ([shot['scene_id']] if shot['scene_id'] else []) + shot['prop_ids']:
                    binding = mapping[local_id]
                    version_ids.append(binding['version_id'])
                    is_scene = binding['kind'] == 'scene'
                    await upsert_project_link(db, model=ProjectSceneLink if is_scene else ProjectPropLink, asset_field='scene_id' if is_scene else 'prop_id', asset_id=binding['entity_id'], project_id=project_id, chapter_id=chapter_id, shot_id=shot_id)
                if media_id:
                    db.add(ShotFrameImage(shot_detail_id=shot_id, frame_type='key', file_id=media_id))
                choices.append({'shot_id': shot_id, 'version_ids': version_ids, 'reference_media_ids': [media_id] if media_id else [], 'prompt_addition': video_prompt})
                frame_bindings.append({'local_id': shot['id'], 'shot_id': shot_id, 'start_frame': shot['start_frame'], 'end_frame': shot['end_frame'], 'start_us': start, 'end_us': end, 'planned_duration_seconds': planned_duration, 'prompt_templates': template_ids})
            await db.flush()
            plan_id = identity()
            plan = await validate_plan(db, project_id, chapter_id, PlanDocument(shots=choices))
            db.add(ShotPlanDraft(id=plan_id, project_id=project_id, chapter_id=chapter_id, revision=1, document=plan))
            result = {'id': receipt_id, 'project_id': project_id, 'draft_id': draft_id, 'draft_revision': draft.revision, 'chapter_id': chapter_id, 'shot_ids': shot_ids, 'plan_draft_id': plan_id, 'asset_bindings': mapping, 'prompt_template_ids': templates, 'counts': counts(document), 'status': 'pending_confirmation', 'message': '已创建待确认资产、分镜与镜头计划；尚未人工签核或提交生成。'}
            db.add(DnaProductionImport(id=receipt_id, project_id=project_id, draft_id=draft_id, draft_revision=draft.revision, request_key=body.request_key, request_hash=digest, document_hash=document_hash, source=source, document=document,
                request={**body.model_dump(), 'shot_frame_bindings': frame_bindings}, result=result))
            await db.flush()
            return result
