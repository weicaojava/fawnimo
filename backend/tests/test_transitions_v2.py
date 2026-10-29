"""Independent synthetic pixels/audio and real SQLite v2 state; no AI/model/semantic claims."""
import asyncio
import copy
import hashlib
from fractions import Fraction
from pathlib import Path
import subprocess
from types import SimpleNamespace
import av
import numpy as np
import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
from pydantic import ValidationError
from sqlalchemy import event, func, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.core.contracts.editing_v2 import TransitionDocument, TransitionEdge
from app.core.db import Base
from app.config import settings
from app.models.studio import Project, Chapter, Shot, FileItem
from app.models.production import ProductionMedia, ProductionTake, ShotSelection
from app.models.editing import EditDraft, EditPublication
from app.models.editing_v2 import TransitionDraft, TransitionPublication, TransitionExportJob
from app.models.task import GenerationTask
from app.services.editing.schema import migrate_editing
from app.services.editing.transition_schema import migrate_transitions
from app.services.editing.transition_exports import TransitionExportWorker
from app.api.editing_v2 import router, get_transition_factory
from app.services.editing.media import run_child as probe_child, canonical_hash, stat_identity
from app.services.editing.transition_media import run_child as render_child
from app.services.editing.transitions import compose

ROOT = Path(__file__).resolve().parents[5]
FFMPEG = ROOT / 'work' / 'ffmpeg.exe'


def digest(path):
    """Hash actual bytes before and after rendering so input mutation cannot pass unnoticed."""
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


async def source_fixture(folder):
    """Author red/440Hz and blue/880Hz 24fps MP4, explicitly as independent software fixtures."""
    folder.mkdir(parents=True, exist_ok=True)
    clips = []
    for index, (color, frequency) in enumerate((('red', 440), ('blue', 880))):
        target = folder / f'{color}.mp4'
        subprocess.run([str(FFMPEG), '-nostdin', '-v', 'error', '-f', 'lavfi', '-i', f'color={color}:s=64x96:r=24:d=2',
            '-f', 'lavfi', '-i', f'sine=frequency={frequency}:sample_rate=48000:duration=2', '-c:v', 'libx264', '-threads', '1', '-pix_fmt', 'yuv420p', '-c:a', 'aac', str(target)], check=True, timeout=20)
        facts = await probe_child({'mode': 'probe', 'path': str(target), 'expected_hash': digest(target)})
        clips.append({'shot_id': f's{index + 1}', 'take_id': f't{index + 1}', 'source_in_ms': 0, 'source_out_ms': 2000, 'source_audio': True,
            'timeline_start_ms': index * 2000, 'timeline_end_ms': (index + 1) * 2000, 'origin': {'kind': 'user_import', 'fixture_only': True},
            'source': {'id': color, 'mime': 'video/mp4', 'sha256': digest(target), 'size': target.stat().st_size, 'relative_path': target.name, 'stat': stat_identity(target), **facts}})
    anchors = [{'kind': 'dialogue', 'item_id': 'line1', 'take_id': 't1', 'start_ms': 500, 'end_ms': 1000, 'confirmed': True},
               {'kind': 'dialogue', 'item_id': 'line2', 'take_id': 't2', 'start_ms': 800, 'end_ms': 1200, 'confirmed': True}]
    payload = {'schema_version': 1, 'profile': 'portrait-360p30-v1', 'project_id': 'p', 'chapter_id': 'c', 'duration_ms': 4000,
        'clips': clips, 'document': {'anchors': anchors}, 'coverage': {'original_items': []},
        'subtitles': [{'dialogue_id': 'line1', 'text': 'First original fixture line', 'start_ms': 500, 'end_ms': 1000},
                      {'dialogue_id': 'line2', 'text': 'Second original fixture line', 'start_ms': 2800, 'end_ms': 3200}]}
    return SimpleNamespace(id='base-v1', project_id='p', chapter_id='c', content_hash=canonical_hash(payload), payload=payload)


def transition_document(duration=500):
    """Declare only the one actual fixed-order boundary, without source-clock or media overrides."""
    return TransitionDocument(title='Independent synthetic crossfade test', edges=[{'left_shot_id': 's1', 'right_shot_id': 's2', 'kind': 'crossfade' if duration else 'hard_cut', 'duration_ms': duration}])


def audio_samples(path):
    """Independently decode rendered mono float PCM for actual tone-mixture measurements."""
    samples = []
    with av.open(str(path)) as container:
        resampler = av.AudioResampler(format='fltp', layout='mono', rate=48000)
        for frame in container.decode(audio=0):
            for converted in resampler.resample(frame): samples.append(converted.to_ndarray().reshape(-1))
        for converted in resampler.resample(None): samples.append(converted.to_ndarray().reshape(-1))
    return np.concatenate(samples)


def tone_amplitude(samples, start, frequency):
    """Measure a 100ms frequency component independently of the renderer/filter graph."""
    window = samples[round(start * 48000):round((start + .1) * 48000)]
    phase = np.arange(len(window)) / 48000
    return float(abs(np.sum(window * np.exp(-2j * np.pi * frequency * phase))) * 2 / len(window))


@pytest.mark.asyncio
async def test_real_crossfade_render_has_exact_duration_mixed_pixels_and_two_tone_audio(tmp_path):
    base = await source_fixture(tmp_path / 'media')
    original = copy.deepcopy(base.payload)
    payload, issues = compose(base, transition_document())
    assert not issues and payload['duration_ms'] == 3500 and payload['overlap_ms'] == 500
    assert payload['subtitles'][1]['start_ms'] == 2300 and payload['subtitles'][1]['end_ms'] == 2700
    assert base.payload == original
    for clip in payload['clips']: clip['path'] = str(tmp_path / 'media' / clip['source']['relative_path'])
    folder = tmp_path / 'render'; folder.mkdir()
    result = await render_child({'payload': payload, 'folder': str(folder), 'ffmpeg': str(FFMPEG), 'publication_id': 'fixture-v2', 'publication_hash': canonical_hash(payload)})
    assert result['facts']['video']['frames'] == 105
    assert result['facts']['video']['end_us'] == 3500000
    with av.open(str(folder / 'video.mp4')) as container:
        frames = list(container.decode(video=0))
    assert [Fraction(frame.pts) * frame.time_base for frame in frames] == [Fraction(index, 30) for index in range(105)]
    red, mixed, blue = [frame.to_ndarray(format='rgb24')[300:340, 160:200].mean(axis=(0, 1)) for frame in (frames[15], frames[52], frames[85])]
    assert red[0] > 220 and red[2] < 20
    assert blue[2] > 220 and blue[0] < 20
    assert mixed[0] > 80 and mixed[2] > 80 and mixed[1] < 30
    expected = red * (1 - 7 / 15) + blue * (7 / 15)
    assert np.max(abs(mixed - expected)) < 25
    samples = audio_samples(folder / 'video.mp4')
    pure_a, pure_b = tone_amplitude(samples, .5, 440), tone_amplitude(samples, 2.7, 880)
    blend_a, blend_b = tone_amplitude(samples, 1.7, 440), tone_amplitude(samples, 1.7, 880)
    assert pure_a > .03 and pure_b > .03
    assert .25 < blend_a / pure_a < .75 and .25 < blend_b / pure_b < .75
    assert '00:00:02,300 --> 00:00:02,700' in (folder / 'subtitles.srt').read_text('utf8')
    assert all(digest(tmp_path / 'media' / clip['source']['relative_path']) == clip['source']['sha256'] for clip in base.payload['clips'])


@pytest.mark.asyncio
async def test_transition_protects_all_declared_anchor_and_audio_coverage(tmp_path):
    base = await source_fixture(tmp_path / 'sources')
    base.payload['document']['anchors'][0]['end_ms'] = 1600
    _, issues = compose(base, transition_document())
    assert any(issue['code'] == 'anchor_in_transition' and issue['item_id'] == 'line1' for issue in issues)
    base.payload['document']['anchors'][0]['end_ms'] = 1000
    base.payload['clips'][0]['source']['audio']['coverage_us'] = [[0, 1000000], [1200000, 2000000]]
    assert any(issue['code'] == 'audio_gap' for issue in compose(base, transition_document())[1])
    base.payload['clips'][0]['source_audio'] = False
    assert not compose(base, transition_document())[1]


@pytest.mark.parametrize('value', [-100, 50, 1100, True, 100.0])
def test_crossfade_duration_is_strict_and_bounded(value):
    with pytest.raises(ValidationError): TransitionEdge(left_shot_id='s1', right_shot_id='s2', kind='crossfade', duration_ms=value)


@pytest.mark.asyncio
@pytest.mark.parametrize('first_fade', [False, True])
async def test_actual_mixed_hardcut_crossfade_chains_keep_exact_frames(tmp_path, first_fade):
    """A hard cut must not erase rate metadata before a later fade or change accumulated offsets."""
    base = await source_fixture(tmp_path / 'media')
    third = copy.deepcopy(base.payload['clips'][0]); third.update(shot_id='s3', take_id='t3', timeline_start_ms=4000, timeline_end_ms=6000)
    base.payload['clips'].append(third); base.payload['duration_ms'] = 6000
    document = TransitionDocument(title='Mixed boundary chain', edges=[
        {'left_shot_id': 's1', 'right_shot_id': 's2', 'kind': 'crossfade' if first_fade else 'hard_cut', 'duration_ms': 500 if first_fade else 0},
        {'left_shot_id': 's2', 'right_shot_id': 's3', 'kind': 'hard_cut' if first_fade else 'crossfade', 'duration_ms': 0 if first_fade else 500}])
    payload, issues = compose(base, document)
    assert not issues and payload['duration_ms'] == 5500
    for clip in payload['clips']: clip['path'] = str(tmp_path / 'media' / clip['source']['relative_path'])
    folder = tmp_path / 'render'; folder.mkdir()
    result = await render_child({'payload': payload, 'folder': str(folder), 'ffmpeg': str(FFMPEG), 'publication_id': 'fixture-chain', 'publication_hash': canonical_hash(payload)})
    assert result['facts']['video']['frames'] == 165 and result['facts']['video']['end_us'] == 5500000


async def seed_base(factory, folder):
    """Seed an explicitly synthetic historical v1 fixture, never a real production qualification."""
    base = await source_fixture(folder)
    async with factory() as db:
        db.add(Project(id='p', name='独立转场软件验收（合成素材，不是模型结果）', style='真人都市'))
        await db.flush()
        db.add(Chapter(id='c', project_id='p', index=1, title='合成色块与音调转场验收'))
        await db.flush()
        for index, clip in enumerate(base.payload['clips']):
            source = clip['source']
            db.add(Shot(id=clip['shot_id'], chapter_id='c', index=index + 1, title=f'软件验收镜头{index + 1}', status='pending'))
            db.add(FileItem(id=source['id'], type='video', name='Explicit synthetic source ' + source['id'], storage_key='local-production/' + source['relative_path']))
        await db.flush()
        for clip in base.payload['clips']:
            source = clip['source']
            db.add(ProductionMedia(id=source['id'], project_id='p', name='Explicit synthetic source', mime='video/mp4', size=source['size'], sha256=source['sha256'], relative_path=source['relative_path']))
        await db.flush()
        for clip in base.payload['clips']:
            db.add(ProductionTake(id=clip['take_id'], project_id='p', shot_id=clip['shot_id'], media_id=clip['source']['id'], provenance='user_import', version_ids=[]))
        document = {'schema_version': 1, 'plan_id': None, 'title': 'Explicit synthetic v1 historical fixture',
            'clips': [{key: clip[key] for key in ('shot_id', 'take_id', 'source_in_ms', 'source_out_ms', 'source_audio')} for clip in base.payload['clips']], 'anchors': base.payload['document']['anchors']}
        base.payload['document'] = document
        base.content_hash = canonical_hash(base.payload)
        db.add(EditDraft(id='base-draft', project_id='p', chapter_id='c', revision=1, document=document))
        await db.flush()
        db.add(EditPublication(id=base.id, project_id='p', chapter_id='c', draft_id='base-draft', draft_revision=1, content_hash=base.content_hash, payload=base.payload))
        await db.commit()
    return base


@pytest.fixture
def transition_api(tmp_path, monkeypatch):
    """Use real source files, real SQLite/FKs, full routes and independent v2 migrations."""
    folder = tmp_path / 'media'
    monkeypatch.setattr(settings, 'production_media_root', str(folder))
    monkeypatch.setattr(settings, 'production_probe_executable', str(FFMPEG))
    monkeypatch.setattr(settings, 'editing_export_enabled', True)
    engine = create_async_engine('sqlite+aiosqlite:///' + (tmp_path / 'v2.db').as_posix(), poolclass=NullPool)

    @event.listens_for(engine.sync_engine, 'connect')
    def integrity(connection, _record):
        """Apply the actual local database reference and contention rules."""
        connection.execute('PRAGMA foreign_keys=ON'); connection.execute('PRAGMA busy_timeout=5000')
    factory = async_sessionmaker(engine, expire_on_commit=False, autoflush=False)

    async def prepare():
        """Never use current or historical user database files as test setup."""
        await migrate_editing(engine)
        await migrate_transitions(engine)
        return await seed_base(factory, folder)
    base = asyncio.run(prepare())
    app = FastAPI(); app.include_router(router)
    app.dependency_overrides[get_transition_factory] = lambda: factory
    client = TestClient(app)
    try: yield client, factory, base, engine
    finally:
        client.close(); asyncio.run(engine.dispose())


def create_and_freeze(client):
    """Drive actual HTTP saving and locking with explicit duration acknowledgement."""
    created = client.post('/editing-v2/projects/p/drafts', json={'base_publication_id': 'base-v1', 'document': transition_document().model_dump()})
    assert created.status_code == 201, created.text
    draft = created.json()['data']
    locked = client.post(f"/editing-v2/projects/p/drafts/{draft['id']}/freeze", json={'expected_revision': 1, 'accept_overlap_change': True})
    assert locked.status_code == 200, locked.text
    return draft, locked.json()['data']


def test_actual_v2_api_cas_freeze_export_replay_and_v1_unchanged(transition_api):
    client, factory, base, _engine = transition_api
    draft, locked = create_and_freeze(client)
    assert locked['payload']['duration_ms'] == 3500 and locked['payload']['base']['content_hash'] == base.content_hash
    assert all('relative_path' not in clip['source'] and 'stat' not in clip['source'] for clip in locked['payload']['clips'])
    replay = client.post(f"/editing-v2/projects/p/drafts/{draft['id']}/freeze", json={'expected_revision': 1, 'accept_overlap_change': True})
    assert replay.json()['data']['id'] == locked['id']
    saved = client.patch(f"/editing-v2/projects/p/drafts/{draft['id']}", json={'expected_revision': 1, 'document': transition_document(0).model_dump()})
    assert saved.status_code == 200 and saved.json()['data']['revision'] == 2
    stale = client.patch(f"/editing-v2/projects/p/drafts/{draft['id']}", json={'expected_revision': 1, 'document': transition_document(100).model_dump()})
    assert stale.status_code == 409
    response = client.post(f"/editing-v2/projects/p/publications/{locked['id']}/exports", json={'request_key': 'actual-render'})
    assert response.status_code == 201, response.text
    job = response.json()['data']
    same = client.post(f"/editing-v2/projects/p/publications/{locked['id']}/exports", json={'request_key': 'actual-render'})
    assert same.json()['data']['id'] == job['id']
    asyncio.run(TransitionExportWorker(factory, str(FFMPEG)).step())
    ready = client.get('/editing-v2/projects/p/exports/' + job['id'])
    assert ready.status_code == 200 and ready.json()['data']['state'] == 'ready', ready.text
    assert len(ready.json()['data']['files']) == 4
    for item in ready.json()['data']['files']:
        response = client.get(item['content_url'].replace('/api/v1', ''))
        assert response.status_code == 200 and hashlib.sha256(response.content).hexdigest() == item['sha256']

    async def unchanged():
        """V2 must not edit v1 evidence or mint any source adoption/qualification."""
        async with factory() as db:
            assert (await db.get(EditPublication, base.id)).payload == base.payload
            assert await db.scalar(select(func.count()).select_from(ProductionTake)) == 2
            assert await db.scalar(select(func.count()).select_from(ShotSelection)) == 0
            assert (await db.get(TransitionExportJob, job['id'])).state == 'ready'
            with pytest.raises(IntegrityError): await db.execute(update(TransitionPublication).where(TransitionPublication.id == locked['id']).values(content_hash='forged'))
            await db.rollback()
    asyncio.run(unchanged())


def test_real_task_center_http_cancels_v2_queue_without_celery(transition_api, monkeypatch):
    client, factory, _base, _engine = transition_api
    from app.api.v1.routes.film import task_status
    from app.dependencies import get_db
    draft, locked = create_and_freeze(client)
    created = client.post(f"/editing-v2/projects/p/publications/{locked['id']}/exports", json={'request_key': 'cancel-through-task-center'})
    assert created.status_code == 201
    job = created.json()['data']
    called = []
    monkeypatch.setattr(task_status, 'revoke_task_execution', lambda identity: called.append(identity))

    async def database():
        """Give the legacy HTTP task entrypoint this same isolated real queue."""
        async with factory() as db:
            try: yield db; await db.commit()
            except BaseException: await db.rollback(); raise
    app = FastAPI(); app.include_router(task_status.router); app.dependency_overrides[get_db] = database
    with TestClient(app) as tasks:
        response = tasks.post('/tasks/' + job['task_id'] + '/cancel', json={'reason': 'cancel actual v2 local queue'})
    assert response.status_code == 200, response.text
    assert response.json()['data']['effective_immediately'] is True

    async def cancelled():
        """Both durable owner and task projection must reflect real cancellation."""
        async with factory() as db:
            actual = await db.get(TransitionExportJob, job['id'])
            task = await db.get(GenerationTask, job['task_id'])
            assert actual.state == 'cancelled' and actual.cancel_requested
            assert task.status == 'cancelled' and task.cancel_requested
    asyncio.run(cancelled())
    assert not called
