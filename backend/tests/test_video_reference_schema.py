"""Verify sealed conditions against actual native job/hold objects in a disposable SQLite database."""
from contextlib import asynccontextmanager
import sqlite3
import hashlib
from pathlib import Path

from fastapi import HTTPException
import pytest
from sqlalchemy import func, select, text
from sqlalchemy.exc import IntegrityError

from app.core.contracts.execution_binding import binding_fingerprint
from app.core.contracts.execution_capabilities import Capability
from app.core.contracts.model_accounting import ModelPriceEnvelope
from app.core.contracts.seedance25 import ARK_BASE_URL, MODEL_ID
from app.core.contracts.seedance_references import REFERENCE_PROTOCOL
from app.core.contracts.seedance_references import VideoReferenceSelection
from app.config import settings
from app.models.production import ProductionMedia
from app.models.llm import Model, Provider
from app.models.provider_connection_version import ProviderConnectionVersion
from app.models.studio import FileItem
from app.models.execution import ExecutionJob
from app.models.execution_budget import ExecutionBudget
from app.models.video_reference_inputs import VideoReferenceInputSet, VideoReferenceInput
from app.services.worker.execution_admission import AdmissionPolicy, _insert_reserved_job, freeze_request
from app.services.worker.execution_budget import configure, transaction
from app.services.worker.execution_schema import migrate_execution
from app.services.worker.model_charge_schema import migrate_model_charges
from app.services.worker.model_billing_schema import migrate_model_billing
from app.services.worker.model_job_charges import binding_digest
from app.services.worker.native_video_accounting import identity
from app.services.worker.native_video_charges import bind_new_jobs
from app.services.worker.native_video_charge_schema import migrate_native_video_charges
from app.services.worker.video_reference_inputs import (
    install_video_input_set, prepare_saved_video_inputs, prepare_video_inputs, recheck_video_inputs, saved_video_input_set,
)
from app.services.worker.video_reference_schema import migrate_video_reference_inputs
from tests.test_video_reference_inputs import owned_media


@asynccontextmanager
async def actual_reference_job(tmp_path, monkeypatch, *, install=True, protocol=REFERENCE_PROTOCOL, video_content=None):
    """Use real admission/reservation/billing helpers with authored trusted configuration, never a running transport."""
    async with owned_media(tmp_path, monkeypatch) as (factory, chosen, path, content):
        engine = factory.kw['bind']
        for migrate in (migrate_execution, migrate_model_charges, migrate_model_billing,
                        migrate_native_video_charges, migrate_video_reference_inputs):
            await migrate(engine)
        async with factory() as db:
            await configure(db, 'project-a', 'CNY', 100)
        async with factory() as db:
            # Common dispatch guards require current model/clock metadata. The
            # authored connection has no key or enabled protocol and points at
            # an unreachable loopback port; it cannot create a real model call.
            db.add(Provider(id='authored-provider', name='volcengine', base_url='http://127.0.0.1:1/api/v3', api_key=''))
            await db.flush()
            db.add(Model(id='authored-model', name=MODEL_ID, category='video', provider_id='authored-provider', params={}))
            db.add(ProviderConnectionVersion(provider_id='authored-provider', revision=1))
            await db.commit()
        if video_content is not None:
            path = Path(settings.production_media_root)/'authored-video.mp4'
            path.write_bytes(video_content)
            content = video_content
            checksum = hashlib.sha256(video_content).hexdigest()
            async with factory() as db:
                db.add(FileItem(id='video-a', type='video', name='Authored video', storage_key='local-production/authored-video.mp4'))
                await db.flush()
                db.add(ProductionMedia(id='video-a', project_id='project-a', name='Authored video', mime='video/mp4',
                    sha256=checksum, size=len(video_content), relative_path='authored-video.mp4'))
                await db.commit()
            chosen = VideoReferenceSelection.model_validate({'mode': 'reference', 'references': [
                {'media_id': 'video-a', 'expected_sha256': checksum, 'role': 'reference_video'}]})
        prepared = await prepare_video_inputs(factory, 'project-a', chosen)
        quote = ModelPriceEnvelope(model_id='authored-model', model_name=MODEL_ID, provider_id='authored-provider',
            input_protocol=REFERENCE_PROTOCOL,
            provider_connection_version=1, account_version='authored-account', max_call_minor=12,
            source_note='Authored header/ledger test only; not a supplier tariff')
        connection = {'model_id': quote.model_id, 'model_name': MODEL_ID, 'category': 'video', 'protocol': protocol,
            'provider_id': quote.provider_id, 'provider_name': 'volcengine', 'provider_connection_version': 1, 'base_url': ARK_BASE_URL}
        policy = AdmissionPolicy(Capability(MODEL_ID, frozenset({4}), frozenset({'image', 'video'}), 4,
            reference_protocol=REFERENCE_PROTOCOL), 12, 'CNY', 3, 'authored-reference-header-fixture')
        original = {'model_id': MODEL_ID, 'duration': 4, 'prompt': '自作几何条件',
            'references': [{'media_id': selected.media_id, 'kind': selected.kind} for selected in chosen.references],
            'video_reference': chosen.model_dump(mode='json')}
        frozen, request_hash = freeze_request(original, policy)
        selection = {'kind': 'direct', 'snapshot': frozen}
        profile = {'schema': 'native-managed-v1', 'service_version': 'authored-service', 'account_version': quote.account_version,
            'profile_hash': 'd'*64, 'iteration_limit': 3, 'quote_fingerprint': quote.fingerprint,
            'execution_binding_hash': binding_fingerprint(policy)}

        async def validate(db, _project, _snapshot):
            """The actual reservation writer rechecks prior owned bytes without decoding under its lock."""
            await recheck_video_inputs(db, prepared)
        async with factory() as db:
            async with transaction(db):
                key = 'authored-header-key'
                job_id = await _insert_reserved_job(db, 'project-a', 'native:'+binding_digest({'key': key, 'unit': 'video'}),
                    frozen, request_hash, policy, reference_validator=validate)
                await bind_new_jobs(db, project_id='project-a', submitted_by='local_operator', request_key=key,
                    request_hash=identity('project-a', 'local_operator', selection, quote.fingerprint),
                    connection=connection, profile=profile, selection=selection, jobs=[(job_id, 'video')], quote=quote)
                if install:
                    await install_video_input_set(db, await db.get(ExecutionJob, job_id), prepared)
        yield factory, job_id, prepared, path, content


@pytest.mark.asyncio
async def test_actual_hold_and_complete_input_set_are_committed_together_and_survive_reopen(tmp_path, monkeypatch):
    """Typed inputs retain the original project, two frame positions and one real12-minor-unit fixture hold."""
    async with actual_reference_job(tmp_path, monkeypatch) as (factory, job_id, prepared, _path, content):
        async with factory() as db:
            header = await db.get(VideoReferenceInputSet, job_id)
            assert header.sealed and header.reference_count == 2 and header.mode == 'first_last_frames'
            job = await db.get(ExecutionJob, job_id)
            selected, fields = await saved_video_input_set(db, job)
            assert selected == prepared.selection and [row['role'] for row in fields] == ['first_frame', 'last_frame']
            assert all(row['project_id'] == 'project-a' for row in fields)
            assert (await db.get(ExecutionBudget, 'project-a')).reserved_minor == 12
            assert not (await db.execute(text('PRAGMA foreign_key_check'))).first()
        reopened = await prepare_saved_video_inputs(factory, job)
        assert reopened.selection == selected and all(item.content == content for item in reopened.references)


@pytest.mark.asyncio
@pytest.mark.parametrize('operation', ['update-header', 'delete-header', 'replace-header', 'update-input', 'delete-input', 'append-input', 'replace-input'])
async def test_sql_cannot_change_replace_delete_or_append_sealed_original_inputs(tmp_path, monkeypatch, operation):
    """Direct SQL cannot bypass the application to acquire new conditions after financial admission."""
    async with actual_reference_job(tmp_path, monkeypatch) as (factory, job_id, _prepared, _path, _content):
        statements = {
            'update-header': "UPDATE video_reference_input_sets SET connection_hash='"+'a'*64+"' WHERE job_id=:job",
            'delete-header': 'DELETE FROM video_reference_input_sets WHERE job_id=:job',
            'replace-header': 'INSERT OR REPLACE INTO video_reference_input_sets SELECT * FROM video_reference_input_sets WHERE job_id=:job',
            'update-input': "UPDATE video_reference_inputs SET role='reference_image' WHERE job_id=:job AND position=0",
            'delete-input': 'DELETE FROM video_reference_inputs WHERE job_id=:job AND position=0',
            'append-input': "INSERT INTO video_reference_inputs(job_id,position,project_id,media_id,role,sha256,byte_size,mime,relative_path,width,height) SELECT job_id,2,project_id,media_id,role,sha256,byte_size,mime,relative_path,width,height FROM video_reference_inputs WHERE job_id=:job AND position=0",
            'replace-input': 'INSERT OR REPLACE INTO video_reference_inputs SELECT * FROM video_reference_inputs WHERE job_id=:job AND position=0',
        }
        async with factory() as db:
            with pytest.raises(IntegrityError):
                await db.execute(text(statements[operation]), {'job': job_id})
            await db.rollback()
        async with factory() as db:
            assert await db.scalar(select(func.count()).select_from(VideoReferenceInput).where(VideoReferenceInput.job_id == job_id)) == 2
            await saved_video_input_set(db, await db.get(ExecutionJob, job_id))


@pytest.mark.asyncio
async def test_missing_complete_header_never_yields_reference_send_authority(tmp_path, monkeypatch):
    """A financial hold or queued snapshot alone cannot stand in for complete saved conditions."""
    async with actual_reference_job(tmp_path, monkeypatch, install=False) as (factory, job_id, _prepared, _path, _content):
        async with factory() as db:
            job = await db.get(ExecutionJob, job_id)
        with pytest.raises(HTTPException) as error:
            await prepare_saved_video_inputs(factory, job)
        assert error.value.status_code == 409


@pytest.mark.asyncio
async def test_old_text_connection_cannot_gain_new_header_and_failed_admission_rolls_back_hold(tmp_path, monkeypatch):
    """Even an internal caller cannot attach role-aware conditions to a financial context sealed as the old text protocol."""
    with pytest.raises(HTTPException):
        async with actual_reference_job(tmp_path, monkeypatch, protocol='seedance25_text_v1'):
            raise AssertionError('Old connection must not gain new conditions')
    with sqlite3.connect(tmp_path/'references.sqlite3') as db:
        assert db.execute("SELECT reserved_minor,spent_minor FROM execution_budgets WHERE project_id='project-a'").fetchone() == (0, 0)
        assert db.execute('SELECT count(*) FROM execution_jobs').fetchone()[0] == 0
        assert db.execute('SELECT count(*) FROM video_reference_input_sets').fetchone()[0] == 0
