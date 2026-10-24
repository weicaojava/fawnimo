"""Versioned production assets reuse existing projects, characters, scenes and shots."""
import hashlib
import json
import uuid
from fastapi import HTTPException
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from app.models.production import AssetVersion, ProductionAsset, ProductionTake, ShotSelection, TakeVersion, VersionMedia
from app.models.studio import Character, Chapter, Project, Shot
from app.schemas.studio.production import AssetWrite, TakeWrite, VersionWrite
from app.services.studio.entity_crud import create_entity
from app.services.studio.production_media import scoped_media


def serialize(row) -> dict:
    """Return mapped scalar columns without lazy relationship IO."""
    return {column.name:getattr(row,column.name) for column in row.__table__.columns}


async def project_exists(db: AsyncSession, project_id: str) -> Project:
    """Require an existing project instead of silently creating unowned data."""
    project=await db.get(Project,project_id)
    if project is None:raise HTTPException(404,'工程不存在')
    return project


async def asset_in_project(db: AsyncSession, project_id: str, asset_id: str) -> ProductionAsset:
    """Never resolve an asset by a foreign project's ID."""
    asset=await db.get(ProductionAsset,asset_id,with_for_update=True)
    if asset is None or asset.project_id!=project_id:raise HTTPException(404,'本工程资产不存在')
    return asset


async def add_version(db: AsyncSession, asset: ProductionAsset, body: VersionWrite) -> AssetVersion:
    """Publish once under optimistic concurrency; older versions are never modified."""
    if asset.archived:raise HTTPException(409,'请先恢复归档资产')
    current=int((await db.execute(select(func.max(AssetVersion.revision)).where(AssetVersion.asset_id==asset.id))).scalar() or 0)
    if body.expected_revision!=current:raise HTTPException(409,'资产已更新，请刷新后重新提交')
    ids=list(dict.fromkeys(body.media_ids))
    for media_id in ids:await scoped_media(db,asset.project_id,media_id)
    payload={'name':asset.name,'kind':asset.kind,'subject_id':asset.subject_id,'entity_id':asset.entity_id,'description':body.description,'media_ids':ids}
    canonical=json.dumps(payload,ensure_ascii=False,sort_keys=True,separators=(',',':'))
    version=AssetVersion(id=str(uuid.uuid4()),asset_id=asset.id,project_id=asset.project_id,revision=current+1,payload=payload,content_hash=hashlib.sha256(canonical.encode()).hexdigest())
    db.add(version);await db.flush()
    for media_id in ids:db.add(VersionMedia(version_id=version.id,media_id=media_id,project_id=asset.project_id))
    await db.flush()
    return version


async def create_asset(db: AsyncSession, project_id: str, body: AssetWrite) -> dict:
    """Create the legacy library identity and its initial immutable snapshot together."""
    project=await project_exists(db,project_id)
    if body.expected_revision!=0:raise HTTPException(409,'新资产的预期版本必须为0')
    if body.kind in {'appearance','voice'}:
        subject=await db.get(Character,body.subject_id) if body.subject_id else None
        if subject is None or subject.project_id!=project_id:raise HTTPException(400,'服装与声线必须关联本工程角色')
    elif body.subject_id is not None:raise HTTPException(400,'此资产类别不能指定角色归属')
    asset_id=str(uuid.uuid4());entity_id=None
    if body.kind in {'character','scene','prop'}:
        entity_id=asset_id
        await create_entity(db,entity_type=body.kind,body={'id':entity_id,'project_id':project_id,'name':body.name,'description':body.description,'style':project.style,'visual_style':project.visual_style})
    asset=ProductionAsset(id=asset_id,project_id=project_id,kind=body.kind,name=body.name,subject_id=body.subject_id,entity_id=entity_id)
    db.add(asset);await db.flush()
    version=await add_version(db,asset,body)
    return {'asset':serialize(asset),'version':serialize(version)}


async def asset_history(db: AsyncSession, project_id: str, asset_id: str) -> dict:
    """Expose history and affected shot IDs without replacing adopted takes."""
    asset=await asset_in_project(db,project_id,asset_id)
    versions=list((await db.execute(select(AssetVersion).where(AssetVersion.asset_id==asset.id).order_by(AssetVersion.revision.desc()))).scalars())
    affected=(await db.execute(select(ProductionTake.shot_id).join(TakeVersion,TakeVersion.take_id==ProductionTake.id).join(AssetVersion,AssetVersion.id==TakeVersion.version_id).where(AssetVersion.asset_id==asset_id))).scalars().all()
    return {'asset':serialize(asset),'versions':[serialize(v) for v in versions],'affected_shot_ids':sorted(set(affected))}


async def adopt_take(db: AsyncSession, project_id: str, body: TakeWrite) -> dict:
    """Persist an imported candidate pinned to exact project-owned revisions."""
    shot=await db.get(Shot,body.shot_id)
    chapter=await db.get(Chapter,shot.chapter_id) if shot else None
    if chapter is None or chapter.project_id!=project_id:raise HTTPException(400,'镜头不属于本工程')
    media=await scoped_media(db,project_id,body.media_id)
    if not media.mime.startswith('video/'):raise HTTPException(400,'采用片段必须是视频')
    versions=[];assets=set()
    for version_id in body.version_ids:
        version=await db.get(AssetVersion,version_id)
        if version is None or version.project_id!=project_id:raise HTTPException(400,'版本不属于本工程')
        asset=await asset_in_project(db,project_id,version.asset_id)
        if asset.archived or asset.id in assets:raise HTTPException(400,'资产已归档或同一资产引用了多个版本')
        for item in version.payload['media_ids']:await scoped_media(db,project_id,item)
        assets.add(asset.id);versions.append(version)
    take=ProductionTake(id=str(uuid.uuid4()),project_id=project_id,shot_id=shot.id,media_id=media.id,provenance='user_import',version_ids=[v.id for v in versions])
    db.add(take);await db.flush()
    for version in versions:db.add(TakeVersion(take_id=take.id,version_id=version.id,project_id=project_id))
    await db.flush()
    await select_take(db,project_id,take.id)
    return {**serialize(take),'version_ids':[v.id for v in versions]}


async def select_take(db: AsyncSession, project_id: str, take_id: str) -> dict:
    """Switch current adoption after verifying all referenced media, retaining history."""
    take=await db.get(ProductionTake,take_id)
    if take is None or take.project_id!=project_id:raise HTTPException(404,'本工程采用记录不存在')
    await scoped_media(db,project_id,take.media_id)
    versions=(await db.execute(select(AssetVersion).join(TakeVersion,TakeVersion.version_id==AssetVersion.id).where(TakeVersion.take_id==take_id))).scalars().all()
    for version in versions:
        for media_id in version.payload['media_ids']:await scoped_media(db,project_id,media_id)
    selection=await db.get(ShotSelection,take.shot_id)
    if selection:selection.take_id=take.id
    else:db.add(ShotSelection(shot_id=take.shot_id,project_id=project_id,take_id=take.id))
    await db.flush()
    return await read_take(db,project_id,take_id)


async def read_take(db: AsyncSession, project_id: str, take_id: str) -> dict:
    """Return immutable bindings and their snapshots for history review."""
    take=await db.get(ProductionTake,take_id)
    if take is None or take.project_id!=project_id:raise HTTPException(404,'采用记录不存在')
    versions=(await db.execute(select(AssetVersion).join(TakeVersion,TakeVersion.version_id==AssetVersion.id).where(TakeVersion.take_id==take_id))).scalars().all()
    selected=await db.get(ShotSelection,take.shot_id)
    return {**serialize(take),'selected':bool(selected and selected.take_id==take.id),'versions':[serialize(v) for v in versions]}
