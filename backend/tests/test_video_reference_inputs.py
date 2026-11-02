"""Use disposable projects/files to verify reference ownership, exact bytes and short writer rechecks."""
from contextlib import asynccontextmanager
import hashlib
from io import BytesIO

from fastapi import HTTPException
from PIL import Image
import pytest
from sqlalchemy import event, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.config import settings
from app.core.db import Base
from app.core.contracts.seedance_references import VideoReferenceSelection
from app.core.integrations.volcengine.seedance_reference_payload import build_seedance_reference_body
from app.core.contracts.seedance25 import MODEL_ID
from app.models.production import ProductionMedia
from app.models.studio import FileItem, Project
from app.services.worker import video_reference_inputs as inputs


@asynccontextmanager
async def owned_media(tmp_path, monkeypatch):
    """Create two authored project identities and a single original image, without reading any studio source data."""
    root = tmp_path/'owned-media'
    root.mkdir()
    monkeypatch.setattr(settings, 'production_media_root', str(root))
    engine = create_async_engine('sqlite+aiosqlite:///'+(tmp_path/'references.sqlite3').as_posix())

    @event.listens_for(engine.sync_engine, 'connect')
    def configure(db, _):
        """Use actual FK enforcement and a short writer-contention bound in this independent SQLite fixture."""
        db.execute('PRAGMA foreign_keys=ON')
        db.execute('PRAGMA busy_timeout=250')
    async with engine.begin() as db:
        await db.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False, autoflush=False)
    buffer = BytesIO()
    Image.new('RGB', (360, 640), 'navy').save(buffer, format='PNG')
    content = buffer.getvalue()
    path = root/'authored.png'
    path.write_bytes(content)
    sha = hashlib.sha256(content).hexdigest()
    async with factory() as db:
        db.add_all([Project(id=identity, name=identity, style='真人都市') for identity in ('project-a', 'project-b')])
        db.add(FileItem(id='image-a', type='image', name='Authored reference', storage_key='local-production/authored.png'))
        await db.flush()
        db.add(ProductionMedia(id='image-a', project_id='project-a', name='Authored reference', mime='image/png',
            sha256=sha, size=len(content), relative_path='authored.png'))
        await db.commit()
    chosen = VideoReferenceSelection.model_validate({'mode': 'first_last_frames', 'references': [
        {'media_id': 'image-a', 'expected_sha256': sha, 'role': role} for role in ('first_frame', 'last_frame')]})
    try:
        yield factory, chosen, path, content
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_original_owned_media_and_exact_duplicate_frame_intent_survive_preparation(tmp_path, monkeypatch):
    """One actual image can serve both frame roles, retaining the same bytes and separate ordered positions."""
    async with owned_media(tmp_path, monkeypatch) as (factory, chosen, path, original):
        prepared = await inputs.prepare_video_inputs(factory, 'project-a', chosen)
        assert [item.fields['position'] for item in prepared.references] == [0, 1]
        assert [item.fields['role'] for item in prepared.references] == ['first_frame', 'last_frame']
        assert all(item.content == original and item.wire_proof.content == original for item in prepared.references)
        assert prepared.references[0].content is prepared.references[1].content
        async with factory() as db:
            await inputs.recheck_video_inputs(db, prepared)
        outgoing = build_seedance_reference_body(project_id='project-a', model_id=MODEL_ID, prompt='自作几何', duration=4,
            selection=chosen, prepared=tuple(item.wire_proof for item in prepared.references))
        assert len(outgoing['content']) == 3 and outgoing['ratio'] == 'adaptive'
        assert str(path) not in repr(prepared.references[0].wire_proof)


@pytest.mark.asyncio
@pytest.mark.parametrize('wrong', ['project', 'sha'])
async def test_foreign_project_or_wrong_hash_refuses_before_reading_any_file(tmp_path, monkeypatch, wrong):
    """A guessed media ID or stale content hash cannot trigger private file reads or decoder work."""
    async with owned_media(tmp_path, monkeypatch) as (factory, chosen, _path, _original):
        def unexpected_read(_fields):
            """Make any premature file access visible as an actual test failure."""
            raise AssertionError('Ownership must be checked before file access')
        monkeypatch.setattr(inputs, 'read_owned_reference', unexpected_read)
        if wrong == 'sha':
            value = chosen.model_dump(mode='json')
            for reference in value['references']:
                reference['expected_sha256'] = 'a'*64
            chosen = VideoReferenceSelection.model_validate(value)
        with pytest.raises(HTTPException) as error:
            await inputs.prepare_video_inputs(factory, 'project-b' if wrong == 'project' else 'project-a', chosen)
        assert error.value.status_code == (404 if wrong == 'project' else 409)


@pytest.mark.asyncio
async def test_decode_runs_after_read_session_closes_and_recheck_does_not_decode_under_writer(tmp_path, monkeypatch):
    """A competing real SQLite writer stays usable during CPU work; final writer checks use metadata/stat only."""
    async with owned_media(tmp_path, monkeypatch) as (factory, chosen, _path, _original):
        actual = inputs.verify_image_bytes
        writer_calls = []

        async def decode(content, **options):
            """Exercise a real independent writer before performing the actual owned image decode."""
            async with factory() as db:
                await db.execute(text('BEGIN IMMEDIATE'))
                await db.execute(text("UPDATE projects SET name='CPU decode writer proved' WHERE id='project-b'"))
                await db.commit()
            writer_calls.append(True)
            return await actual(content, **options)
        monkeypatch.setattr(inputs, 'verify_image_bytes', decode)
        prepared = await inputs.prepare_video_inputs(factory, 'project-a', chosen)
        assert writer_calls == [True]

        def no_reread(_fields):
            """The final transaction may inspect file identity but must not read bytes or invoke a decoder."""
            raise AssertionError('Writer recheck must not reread')
        monkeypatch.setattr(inputs, 'read_owned_reference', no_reread)
        async with factory() as db:
            await db.execute(text('BEGIN IMMEDIATE'))
            await inputs.recheck_video_inputs(db, prepared)
            await db.rollback()


@pytest.mark.asyncio
@pytest.mark.parametrize('change', ['file', 'metadata'])
async def test_changed_file_or_metadata_rejects_prepared_authority(tmp_path, monkeypatch, change):
    """Preserved in-memory bytes do not authorize a changed source row or file in a new reservation transaction."""
    async with owned_media(tmp_path, monkeypatch) as (factory, chosen, path, original):
        prepared = await inputs.prepare_video_inputs(factory, 'project-a', chosen)
        if change == 'file':
            path.write_bytes(original[:-1]+b'X')
        else:
            async with factory() as db:
                await db.execute(text("UPDATE production_media SET sha256=:sha WHERE id='image-a'"), {'sha': 'b'*64})
                await db.commit()
        async with factory() as db:
            with pytest.raises(HTTPException) as error:
                await inputs.recheck_video_inputs(db, prepared)
            assert error.value.status_code == 409
        assert all(reference.content == original for reference in prepared.references)
