"""Explicit additive local migration and database guards for production assets."""
from sqlalchemy import text
from app.core.db import engine, Base
from app.models import production  # noqa: F401


async def migrate_production() -> None:
    """Add v1 tables to the fixed baseline and install local integrity constraints.

    This is additive only. Recovery uses a consistent backup into a new database;
    destructive in-place downgrades and unknown legacy schemas are unsupported.
    """
    import app.models.studio  # noqa: F401
    import app.models.task  # noqa: F401
    import app.models.task_links  # noqa: F401
    if engine.dialect.name != 'sqlite':
        raise RuntimeError('Production v1 currently requires local SQLite')
    async with engine.begin() as conn:
        await conn.execute(text('CREATE TABLE IF NOT EXISTS production_schema (version INTEGER PRIMARY KEY)'))
        versions = (await conn.execute(text('SELECT version FROM production_schema'))).scalars().all()
        if versions and versions != [1]:
            raise RuntimeError('Unsupported production schema; restore a supported backup')
        await conn.run_sync(lambda sync: Base.metadata.create_all(sync, tables=[t for t in Base.metadata.sorted_tables if t.name.startswith('production_')]))
        for table in [t for t in Base.metadata.sorted_tables if t.name.startswith('production_')]:
            columns=(await conn.execute(text(f'PRAGMA table_info({table.name})'))).all()
            if {row[1] for row in columns}!={c.name for c in table.columns}:
                raise RuntimeError('Unsupported partial production schema; restore a supported backup')
        if (await conn.execute(text('PRAGMA foreign_key_check'))).first() is not None:
            raise RuntimeError('Existing orphan references require repair before migration')
        for table in ['production_asset_versions','production_version_media','production_takes','production_take_versions','production_media']:
            for action in ['UPDATE','DELETE']:
                await conn.execute(text(f"CREATE TRIGGER IF NOT EXISTS {table}_{action.lower()}_immutable BEFORE {action} ON {table} BEGIN SELECT RAISE(ABORT, 'Immutable production record'); END"))
        for table, field in [('chapters','project_id'),('characters','project_id'),('shots','chapter_id')]:
            await conn.execute(text(f"CREATE TRIGGER IF NOT EXISTS {table}_fixed_parent BEFORE UPDATE OF {field} ON {table} WHEN OLD.{field} IS NOT NEW.{field} BEGIN SELECT RAISE(ABORT, 'Parent scope is immutable; copy explicitly'); END"))
        conditions = {
            'shot_dialog_lines': "(NEW.speaker_character_id IS NOT NULL AND NOT EXISTS(SELECT 1 FROM characters c JOIN shots s ON s.id=NEW.shot_detail_id JOIN chapters ch ON ch.id=s.chapter_id WHERE c.id=NEW.speaker_character_id AND c.project_id=ch.project_id)) OR (NEW.target_character_id IS NOT NULL AND NOT EXISTS(SELECT 1 FROM characters c JOIN shots s ON s.id=NEW.shot_detail_id JOIN chapters ch ON ch.id=s.chapter_id WHERE c.id=NEW.target_character_id AND c.project_id=ch.project_id))",
            'file_usages': "(NEW.chapter_id IS NOT NULL AND NOT EXISTS(SELECT 1 FROM chapters WHERE id=NEW.chapter_id AND project_id=NEW.project_id)) OR (NEW.shot_id IS NOT NULL AND NOT EXISTS(SELECT 1 FROM shots s JOIN chapters c ON c.id=s.chapter_id WHERE s.id=NEW.shot_id AND c.project_id=NEW.project_id AND (NEW.chapter_id IS NULL OR NEW.chapter_id=c.id))) OR EXISTS(SELECT 1 FROM production_media WHERE id=NEW.file_id AND project_id!=NEW.project_id)",
            'production_assets': "NEW.subject_id IS NOT NULL AND NOT EXISTS(SELECT 1 FROM characters WHERE id=NEW.subject_id AND project_id=NEW.project_id)",
            'production_takes': "NOT EXISTS(SELECT 1 FROM shots s JOIN chapters c ON c.id=s.chapter_id WHERE s.id=NEW.shot_id AND c.project_id=NEW.project_id)",
            'production_shot_selections': "NOT EXISTS(SELECT 1 FROM production_takes WHERE id=NEW.take_id AND shot_id=NEW.shot_id AND project_id=NEW.project_id)",
            'shots': "EXISTS(SELECT 1 FROM production_media WHERE id=NEW.generated_video_file_id)",
            'shot_frame_images': "EXISTS(SELECT 1 FROM production_media m WHERE m.id=NEW.file_id AND NOT EXISTS(SELECT 1 FROM shots s JOIN chapters c ON c.id=s.chapter_id WHERE s.id=NEW.shot_detail_id AND c.project_id=m.project_id))",
            'shot_details': "EXISTS(SELECT 1 FROM production_assets a WHERE a.entity_id=NEW.scene_id AND a.project_id!=(SELECT c.project_id FROM shots s JOIN chapters c ON c.id=s.chapter_id WHERE s.id=NEW.id))",
            'character_images': "EXISTS(SELECT 1 FROM production_media m WHERE m.id=NEW.file_id AND NOT EXISTS(SELECT 1 FROM characters WHERE id=NEW.character_id AND project_id=m.project_id))",
        }
        for table in ['project_actor_links','project_scene_links','project_prop_links','project_costume_links']:
            conditions[table] = "(NEW.chapter_id IS NOT NULL AND NOT EXISTS(SELECT 1 FROM chapters WHERE id=NEW.chapter_id AND project_id=NEW.project_id)) OR (NEW.shot_id IS NOT NULL AND NOT EXISTS(SELECT 1 FROM shots s JOIN chapters c ON c.id=s.chapter_id WHERE s.id=NEW.shot_id AND c.project_id=NEW.project_id AND (NEW.chapter_id IS NULL OR NEW.chapter_id=c.id)))"
        for table,field in [('actor_images','actor_id'),('scene_images','scene_id'),('prop_images','prop_id'),('costume_images','costume_id')]:
            # Actors and costumes are global legacy identities. Their project links,
            # rather than character/appearance version entity IDs, own the image scope.
            owned=f"EXISTS(SELECT 1 FROM production_assets a WHERE a.entity_id=NEW.{field} AND a.project_id=m.project_id)"
            if table in {'actor_images','costume_images'}:
                links='project_actor_links' if table=='actor_images' else 'project_costume_links'
                owned+=f" OR EXISTS(SELECT 1 FROM {links} l WHERE l.{field}=NEW.{field} AND l.project_id=m.project_id)"
            conditions[table]=f"EXISTS(SELECT 1 FROM production_media m WHERE m.id=NEW.file_id AND NOT ({owned}))"
        for table, condition in conditions.items():
            if (await conn.execute(text(f"SELECT 1 FROM {table} candidate WHERE {condition.replace('NEW.','candidate.')} LIMIT 1"))).first() is not None:
                raise RuntimeError(f'Existing inconsistent project scope in {table}; migration refused')
            for action in ['INSERT','UPDATE']:
                if table in {'actor_images','costume_images'}:
                    await conn.execute(text(f'DROP TRIGGER IF EXISTS {table}_scope_{action.lower()}'))
                await conn.execute(text(f"CREATE TRIGGER IF NOT EXISTS {table}_scope_{action.lower()} BEFORE {action} ON {table} WHEN {condition} BEGIN SELECT RAISE(ABORT, 'Cross-project reference rejected'); END"))
        await conn.execute(text("CREATE TRIGGER IF NOT EXISTS take_versions_frozen_set BEFORE INSERT ON production_take_versions WHEN NOT EXISTS(SELECT 1 FROM production_takes t,json_each(t.version_ids) v WHERE t.id=NEW.take_id AND v.value=NEW.version_id) BEGIN SELECT RAISE(ABORT, 'Take version set is frozen'); END"))
        await conn.execute(text("CREATE TRIGGER IF NOT EXISTS version_media_frozen_set BEFORE INSERT ON production_version_media WHEN NOT EXISTS(SELECT 1 FROM production_asset_versions v,json_each(v.payload,'$.media_ids') m WHERE v.id=NEW.version_id AND m.value=NEW.media_id) BEGIN SELECT RAISE(ABORT, 'Version media set is frozen'); END"))
        for table in ['characters','scenes','props']:
            await conn.execute(text(f"CREATE TRIGGER IF NOT EXISTS {table}_production_restrict_delete BEFORE DELETE ON {table} WHEN EXISTS(SELECT 1 FROM production_assets WHERE entity_id=OLD.id) BEGIN SELECT RAISE(ABORT, 'Archive versioned assets instead of deleting'); END"))
        await conn.execute(text("CREATE TRIGGER IF NOT EXISTS file_usages_fixed_owner BEFORE UPDATE OF project_id ON file_usages WHEN OLD.project_id!=NEW.project_id BEGIN SELECT RAISE(ABORT, 'File usage ownership is immutable'); END"))
        await conn.execute(text("CREATE TRIGGER IF NOT EXISTS production_assets_fixed_identity BEFORE UPDATE OF id,project_id,kind,subject_id,entity_id ON production_assets BEGIN SELECT RAISE(ABORT, 'Asset identity is immutable'); END"))
        await conn.execute(text("CREATE TRIGGER IF NOT EXISTS files_fixed_content BEFORE UPDATE OF storage_key ON files WHEN EXISTS(SELECT 1 FROM production_media WHERE id=OLD.id) BEGIN SELECT RAISE(ABORT, 'Media content is immutable'); END"))
        await conn.execute(text('INSERT OR IGNORE INTO production_schema(version) VALUES (1)'))
