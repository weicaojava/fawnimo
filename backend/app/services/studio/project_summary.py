"""Read actual project totals instead of displaying stale client-supplied statistics."""
from sqlalchemy import func, select, inspect
from app.models.studio import Chapter, Character, ProjectActorLink, ProjectCostumeLink, ProjectPropLink, ProjectSceneLink
from app.schemas.studio.projects import ProjectRead


async def project_summaries(db, projects):
    """Aggregate a whole page with bounded grouped queries and distinct scoped asset identities."""
    if not projects:
        return []
    identifiers = [project.id for project in projects]
    totals = {identifier: {} for identifier in identifiers}
    for key, model, identity in (
        ('chapters', Chapter, Chapter.id), ('roles', Character, Character.id),
        ('actors', ProjectActorLink, ProjectActorLink.actor_id), ('costumes', ProjectCostumeLink, ProjectCostumeLink.costume_id),
        ('scenes', ProjectSceneLink, ProjectSceneLink.scene_id), ('props', ProjectPropLink, ProjectPropLink.prop_id),
    ):
        rows = (await db.execute(select(model.project_id, func.count(func.distinct(identity))).where(model.project_id.in_(identifiers)).group_by(model.project_id))).all()
        found = dict(rows)
        for identifier in identifiers:
            totals[identifier][key] = int(found.get(identifier, 0))
    # Count queries may autoflush edited projects and expire server-side clocks.
    # Resolve those values after all queries, before synchronous serialization.
    for project in projects:
        missing = inspect(project).unloaded.intersection({'created_at', 'updated_at'})
        if missing:
            await db.refresh(project, attribute_names=sorted(missing))
    return [ProjectRead.model_validate(project).model_copy(update={'stats': {**(project.stats or {}), **totals[project.id]}}) for project in projects]
