"""Fail closed for new unplanned legacy calls into registered story projects.

Scope is read from real identities and ALL their associations, never a client's
preferred project or a first-project shortcut. Known isolated global assets keep
their legacy semantics; absent/unknown identities cannot authorize an external call.
"""
from fastapi import HTTPException
from sqlalchemy import select
from app.models.production import ProductionAsset
from app.models.script_preparation import ScriptDraft
from app.models.studio import (Actor,ActorImage,Chapter,Character,CharacterImage,CharacterPropLink,Costume,CostumeImage,Project,ProjectActorLink,ProjectCostumeLink,ProjectPropLink,ProjectSceneLink,Prop,PropImage,Scene,SceneImage,Shot,ShotCharacterLink,ShotDetail,ShotFrameImage)
from app.models.task_links import GenerationTaskLink

MESSAGE='本工程已启用结构化剧本，请前往工作室使用绑定计划；拒绝新的未绑定计划调用，既往受理需对账'


def projects_for_subject(db, kind: str, identity: str) -> set[str]:
    """Resolve one actual subject, including every project reached by shared assets."""
    models={'project':Project,'chapter':Chapter,'shot':Shot,'character':Character,'actor':Actor,'scene':Scene,'prop':Prop,'costume':Costume}
    images={'actor_image':(ActorImage,'actor','actor_id'),'character_image':(CharacterImage,'character','character_id'),'scene_image':(SceneImage,'scene','scene_id'),'prop_image':(PropImage,'prop','prop_id'),'costume_image':(CostumeImage,'costume','costume_id'),'shot_frame_image':(ShotFrameImage,'shot','shot_detail_id')}
    if not isinstance(identity,str) or not identity:raise HTTPException(409,'旧调用主体缺失，不能核实工程归属；既往受理需对账')
    if kind in images:
        model,target,field=images[kind]
        try:key=int(identity)
        except ValueError:raise HTTPException(409,'旧调用主体无效，不能核实工程归属') from None
        row=db.get(model,key)
        if row is None:raise HTTPException(409,'旧调用主体不存在，不能核实工程归属')
        return projects_for_subject(db,target,str(getattr(row,field)))
    model=models.get(kind)
    row=db.get(model,identity) if model else None
    if row is None:raise HTTPException(409,'旧调用主体不存在或类型未知，不能核实工程归属')
    if kind=='project':return {row.id}
    if kind=='chapter':return projects_for_subject(db,'project',row.project_id)
    if kind=='shot':return projects_for_subject(db,'chapter',row.chapter_id)
    if kind=='character':return projects_for_subject(db,'project',row.project_id)
    projects=set()
    link_model,field={'actor':(ProjectActorLink,'actor_id'),'scene':(ProjectSceneLink,'scene_id'),'prop':(ProjectPropLink,'prop_id'),'costume':(ProjectCostumeLink,'costume_id')}[kind]
    links=db.execute(select(link_model).where(getattr(link_model,field)==identity)).scalars()
    for link in links:
        projects.update(projects_for_subject(db,'project',link.project_id))
        if link.chapter_id:projects.update(projects_for_subject(db,'chapter',link.chapter_id))
        if link.shot_id:projects.update(projects_for_subject(db,'shot',link.shot_id))
    asset_kinds={'actor':{'actor'},'scene':{'scene'},'prop':{'prop'},'costume':{'costume','appearance'}}[kind]
    projects.update(db.execute(select(ProductionAsset.project_id).where(ProductionAsset.entity_id==identity,ProductionAsset.kind.in_(asset_kinds))).scalars())
    if kind in {'actor','costume','prop'}:
        chars=select(Character.id,Character.project_id)
        if kind=='actor':chars=chars.where(Character.actor_id==identity)
        elif kind=='costume':chars=chars.where(Character.costume_id==identity)
        else:chars=chars.join(CharacterPropLink,CharacterPropLink.character_id==Character.id).where(CharacterPropLink.prop_id==identity)
        for char_id,project in db.execute(chars):
            projects.add(project)
            for shot_id in db.execute(select(ShotCharacterLink.shot_id).where(ShotCharacterLink.character_id==char_id)).scalars():projects.update(projects_for_subject(db,'shot',shot_id))
    if kind=='scene':
        for shot_id in db.execute(select(ShotDetail.id).where(ShotDetail.scene_id==identity)).scalars():projects.update(projects_for_subject(db,'shot',shot_id))
    return projects


def assert_legacy_allowed_sync(db, kind: str, identity: str) -> None:
    """Deny any registered scope without changing tasks, frames, results or budgets."""
    projects=projects_for_subject(db,kind,identity)
    if projects and db.execute(select(ScriptDraft.id).where(ScriptDraft.project_id.in_(projects)).limit(1)).first():raise HTTPException(409,MESSAGE)


async def assert_legacy_allowed(db, kind: str, identity: str) -> None:
    """Use the same real-DB scope rules from asynchronous HTTP and workers."""
    await db.run_sync(lambda sync:assert_legacy_allowed_sync(sync,kind,identity))


def script_scope_sync(db, values: dict, *, relation_kind=None, require_scope=False) -> None:
    """Validate every supplied real scope; a chosen unregistered project cannot hide another."""
    found=False
    for kind in ['project','chapter','shot']:
        identity=values.get(kind+'_id')
        if identity is not None:
            assert_legacy_allowed_sync(db,kind,identity);found=True
    identity=values.get('relation_entity_id')
    if identity:
        assert_relation_allowed_sync(db,identity);found=True
    if require_scope and not found:raise HTTPException(409,'旧任务缺少可验证主体，不重新外呼；既往受理需对账')


async def assert_script_request_allowed(db, values: dict, *, relation_kind=None) -> None:
    """Guard project-aware legacy processing while preserving truly unscoped tools."""
    await db.run_sync(lambda sync:script_scope_sync(sync,values,relation_kind=relation_kind))


def assert_relation_allowed_sync(db, identity: str) -> None:
    """Resolve real raw or UI kind:id relations, checking every matching namespace."""
    models={'project':Project,'chapter':Chapter,'shot':Shot,'character':Character,'actor':Actor,'scene':Scene,'prop':Prop,'costume':Costume}
    if not isinstance(identity,str) or not identity:raise HTTPException(409,'旧调用缺少可验证关联主体')
    if ':' in identity:
        kind,key=identity.split(':',1)
        assert_legacy_allowed_sync(db,kind,key);return
    kinds=[kind for kind,model in models.items() if db.get(model,identity) is not None]
    if not kinds:raise HTTPException(409,'旧调用关联主体无法核实，不重新外呼；既往受理需对账')
    for kind in kinds:assert_legacy_allowed_sync(db,kind,identity)


def assert_script_task_allowed_sync(db, task_id: str, values: dict) -> None:
    """Resolve persisted task links as well as payload scope before constructing an LLM."""
    script_scope_sync(db,values)
    found=any(values.get(kind+'_id') for kind in ['project','chapter','shot'])
    for link in db.execute(select(GenerationTaskLink).where(GenerationTaskLink.task_id==task_id)).scalars():
        assert_relation_allowed_sync(db,link.relation_entity_id);found=True
    if not found:raise HTTPException(409,'旧任务主体无法核实，不重新外呼；既往受理需对账')


async def assert_script_task_allowed(db, task_id: str, values: dict) -> None:
    """Guard async legacy merge/variant workers before resolving any provider."""
    await db.run_sync(lambda sync:assert_script_task_allowed_sync(sync,task_id,values))
