"""Authored local reference bytes and HTTP profiles; no real provider, price or model-quality qualification."""
import asyncio
import base64
from contextlib import asynccontextmanager
import hashlib
from io import BytesIO
import json
import os
from pathlib import Path
import shutil

from PIL import Image
import pytest
from sqlalchemy import func, select, text, update
from sqlalchemy.exc import IntegrityError
from pydantic import ValidationError

from app.config import settings
from app.core.contracts.asset_image_generation import AssetImageSubmit
from app.core.contracts.asset_image_protocols import (
    LEGACY_IMAGE_PROTOCOL, SEEDREAM_IMAGE_PROTOCOL, GPT_IMAGE_PROTOCOL, asset_image_protocol, result_host_policy,
)
from app.core.contracts.model_accounting import ModelPriceConfiguration, ModelPriceEnvelope, ModelPriceRule
from app.core.integrations import seedream_asset_image as adapter
from app.core.integrations.trusted_http import HttpResult
from app.models.asset_image_generation import AssetImageJob
from app.models.asset_image_inputs import AssetImageInputSet, AssetImageReference
from app.models.execution_budget import ExecutionBudget
from app.models.llm import Model, Provider
from app.models.model_charge import ModelCallCharge
from app.models.production import ProductionMedia
from app.models.provider_connection_version import ProviderConnectionVersion
from app.models.studio import FileItem
from app.services.agent_execution import digest
from app.services.asset_image_inputs import request_payload
from app.services.worker.model_prices import ModelPriceRegistry
from tests.test_asset_image_generation import fixture_client, model_server, submit, terminal

MODEL4 = 'doubao-seedream-4-5-251128'
MODEL5 = 'doubao-seedream-5-0-pro-260628'


def png(size=(256, 256), color=(70, 110, 160)):
    """Create small original deterministic fixture images instead of borrowing user or internet media."""
    stream = BytesIO()
    Image.new('RGB', size, color).save(stream, format='PNG')
    return stream.getvalue()


async def balance(factory, project='image-project'):
    """Read only the original fixture project's actual committed counters."""
    async with factory() as db:
        row = await db.get(ExecutionBudget, project)
        return row.reserved_minor, row.spent_minor


@asynccontextmanager
async def reference_client(tmp_path, monkeypatch, server, *, model=MODEL5, hosts=None,protocol=SEEDREAM_IMAGE_PROTOCOL,output_size=(2048,2048)):
    """Configure one isolated explicitly priced protocol without importing any existing profile or secret."""
    server['png'] = png(output_size)
    monkeypatch.setattr(settings, 'production_media_root', str(tmp_path / 'media'))
    async with fixture_client(tmp_path, monkeypatch, server) as (client, factory, slots, runtimes):
        runtime = runtimes[0]
        await runtime.stop()
        async with factory() as db:
            await db.execute(update(Provider).where(Provider.id == 'image-provider').values(name='openai' if protocol==GPT_IMAGE_PROTOCOL else 'volcengine'))
            await db.execute(update(Model).where(Model.id == 'image-model').values(name=model,
                params={'asset_image_protocol': protocol, 'asset_image_result_hosts': hosts or []}))
            await db.commit()
        async with factory() as db:
            revision = (await db.get(ProviderConnectionVersion, 'image-provider')).revision
        quote = ModelPriceEnvelope(model_id='image-model', model_name=model, provider_id='image-provider',
            provider_connection_version=revision, account_version='synthetic-reference-account', max_call_minor=15,
            source_note='Authored reference protocol fixture; not a supplier tariff')
        runtime.price_registry = ModelPriceRegistry(ModelPriceConfiguration(enabled=True,
            rules=[ModelPriceRule(quote=quote, kinds=['asset_image'])]))
        references, originals = [], []
        async with factory() as db:
            for index in range(2):
                content = png((128 + index * 16, 160), (40 + index * 90, 90, 140))
                identity = 'reference-' + str(index)
                relative = hashlib.sha256(b'image-project').hexdigest() + '/' + identity + '.png'
                target = Path(settings.production_media_root) / relative
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(content)
                digest_value = hashlib.sha256(content).hexdigest()
                db.add(FileItem(id=identity, type='image', name=identity + '.png', thumbnail='',
                    tags=['authored_reference_fixture'], storage_key='local-production/' + relative))
                await db.flush()
                db.add(ProductionMedia(id=identity, project_id='image-project', name=identity + '.png', mime='image/png',
                    sha256=digest_value, size=len(content), relative_path=relative))
                references.append({'media_id': identity, 'expected_sha256': digest_value,
                    'purpose': 'appearance' if index == 0 else 'composition'})
                originals.append((content, target))
            await db.commit()
        await runtime.start()
        yield client, factory, slots, runtime, quote, references, originals


async def reference_submit(client, slot, quote, references, *, key='reference-1', **extra):
    """Send only closed public reference fields and the original selected project's exact price confirmation."""
    return await submit(client, 'actor', slot, key=key, size=extra.pop('size','2048x2048'), references=references,
        expected_price_fingerprint=quote.fingerprint, **extra)


@pytest.mark.asyncio
@pytest.mark.parametrize('model,profile', [(MODEL4, 'single_v4'), (MODEL5, 'single_v5')])
async def test_exact_profiles_send_actual_ordered_reference_bytes_and_keep_one_original_project_hold(tmp_path, monkeypatch, model, profile):
    """Native local HTTP receives the exact approved images, while a real decoded candidate remains separate from adoption."""
    with model_server() as server:
        async with reference_client(tmp_path, monkeypatch, server, model=model) as (client, factory, slots, runtime, quote, refs, originals):
            first = await reference_submit(client, slots['actor'], quote, refs)
            assert first.status_code == 201, first.text
            job = await terminal(client, first.json()['data']['id'])
            assert job['status'] == 'candidate', job
            assert len(server['calls']) == 1 and await balance(factory) == (15, 0) and await balance(factory, 'other-project') == (0, 0)
            wire = server['calls'][0]
            assert wire['authenticated'] and wire['path'] == '/v1/images/generations'
            payload = wire['payload']
            assert payload['model'] == model and payload['response_format'] == 'b64_json' and payload['watermark'] is True
            assert 'n' not in payload and len(payload['image']) == 2
            for position, value in enumerate(payload['image']):
                prefix, encoded = value.split(',', 1)
                assert prefix == 'data:image/png;base64' and base64.b64decode(encoded, validate=True) == originals[position][0]
            assert '参考图1用于外观与主体细节' in payload['prompt'] and '参考图2用于构图与空间关系' in payload['prompt']
            if profile == 'single_v4':
                assert payload['sequential_image_generation'] == 'disabled' and payload['stream'] is False
            else:
                assert 'sequential_image_generation' not in payload and 'stream' not in payload
            assert [(r['position'], r['width'], r['height'], r['sha256']) for r in job['references']] == [
                (i, 128+i*16, 160, refs[i]['expected_sha256']) for i in range(2)]
            assert job['width'] == job['height'] == 2048 and job['protocol'] == SEEDREAM_IMAGE_PROTOCOL
            assert (await client.get(job['content_url'])).content == server['png']
            assert job['file_id'] is None and 'relative_path' not in json.dumps(job)
            async with factory() as db:
                header = await db.get(AssetImageInputSet, job['id'])
                assert header.sealed and header.profile == profile and header.reference_count == 2
            # Recovery is historical: a missing current input or retired price
            # cannot turn the original key into a second model submission.
            runtime.price_registry = ModelPriceRegistry()
            originals[0][1].unlink()
            replay = await reference_submit(client, slots['actor'], quote, refs)
            assert replay.status_code == 201 and replay.json()['data']['id'] == job['id'] and len(server['calls']) == 1
            swapped = await reference_submit(client, slots['actor'], quote, list(reversed(refs)))
            assert swapped.status_code == 409 and await balance(factory) == (15, 0)


@pytest.mark.asyncio
async def test_bad_hash_cross_project_unsupported_input_and_changed_queued_bytes_never_send(tmp_path, monkeypatch):
    """Admission and dispatch both reject changed input, preserving the actual project balance semantics."""
    with model_server() as server:
        async with reference_client(tmp_path, monkeypatch, server) as (client, factory, slots, runtime, quote, refs, originals):
            bad = [{**refs[0], 'expected_sha256': 'f' * 64}]
            assert (await reference_submit(client, slots['actor'], quote, bad)).status_code == 409
            assert (await reference_submit(client, slots['actor'], quote, refs, project_id='other-project')).status_code == 404
            assert (await reference_submit(client, slots['actor'], quote, refs, response_format='url')).status_code == 422
            assert await balance(factory) == (0, 0) and not server['calls']
            await runtime.stop()
            runtime.running = True
            admitted = await reference_submit(client, slots['actor'], quote, refs)
            assert admitted.status_code == 201, admitted.text
            originals[0][1].write_bytes(b'x' * len(originals[0][0]))
            runtime.running = False
            await runtime.start()
            row = await terminal(client, admitted.json()['data']['id'])
            assert row['status'] == 'failed' and not server['calls'] and await balance(factory) == (0, 0)


@pytest.mark.asyncio
async def test_sql_cannot_append_replace_or_reassign_a_sealed_reference_set(tmp_path, monkeypatch):
    """Typed FK/collection/immutability guards enforce the saved inputs even without the HTTP layer."""
    with model_server() as server:
        async with reference_client(tmp_path, monkeypatch, server) as (client, factory, slots, runtime, quote, refs, _):
            await runtime.stop()
            runtime.running = True
            row = (await reference_submit(client, slots['actor'], quote, refs)).json()['data']
            for sql in [
                "UPDATE asset_image_input_sets SET sealed=0 WHERE job_id=:id",
                "UPDATE asset_image_input_sets SET response_format='url' WHERE job_id=:id",
                "UPDATE asset_image_references SET purpose='style' WHERE job_id=:id",
                "UPDATE asset_image_references SET project_id='other-project' WHERE job_id=:id",
                "DELETE FROM asset_image_references WHERE job_id=:id",
                "INSERT OR REPLACE INTO asset_image_references SELECT * FROM asset_image_references WHERE job_id=:id",
                "INSERT OR REPLACE INTO asset_image_input_sets SELECT * FROM asset_image_input_sets WHERE job_id=:id",
            ]:
                async with factory() as db:
                    with pytest.raises(IntegrityError):
                        await db.execute(text(sql), {'id': row['id']})
                    await db.rollback()
            assert not server['calls'] and await balance(factory) == (15, 0)
            runtime.running = False


@pytest.mark.asyncio
async def test_real_post_evidence_precedes_invalid_url_failure_and_unknown_fee_is_retained(tmp_path, monkeypatch):
    """An actual model response cannot use the POST's loopback exception for a returned URL or erase its fee evidence."""
    with model_server() as server:
        server['mode'] = 'url'
        async with reference_client(tmp_path, monkeypatch, server, hosts=['cdn.fawnimo.example']) as (client, factory, slots, _, quote, refs, _):
            response = await reference_submit(client, slots['actor'], quote, refs, response_format='url')
            assert response.status_code == 201, response.text
            row = await terminal(client, response.json()['data']['id'])
            assert row['status'] == 'failed' and server['gets'] == 0 and await balance(factory) == (15, 0)
            assert '127.0.0.1' not in row['error'] and 'cdn.fawnimo.example' not in row['error']
            async with factory() as db:
                charge = await db.scalar(select(ModelCallCharge))
                assert charge.provider_request_id == 'fixture-image-1' and charge.dispatch_state == 'accepted'


@pytest.mark.asyncio
async def test_explicit_retry_copies_original_reference_proofs_and_reserves_only_original_project(tmp_path, monkeypatch):
    """Failed output keeps the first unknown fee; an acknowledged new attempt copies its sealed input set."""
    with model_server() as server:
        server['mode'] = 'badimage'
        async with reference_client(tmp_path, monkeypatch, server) as (client, factory, slots, runtime, quote, refs, _):
            response = await reference_submit(client, slots['actor'], quote, refs)
            original = await terminal(client, response.json()['data']['id'])
            assert original['status'] == 'failed' and await balance(factory) == (15, 0)
            server['mode'] = 'ok'
            body = {'request_key': 'explicit-reference-retry', 'expected_price_fingerprint': quote.fingerprint,
                    'acknowledge_duplicate_charge': True}
            retried = await client.post('/api/v1/asset-image-generation/jobs/' + original['id'] + '/retry', json=body)
            assert retried.status_code == 201, retried.text
            candidate = await terminal(client, retried.json()['data']['id'])
            assert candidate['status'] == 'candidate' and candidate['retry_of'] == original['id'] and candidate['attempt'] == 2
            assert candidate['references'] == original['references'] and await balance(factory) == (30, 0)
            assert await balance(factory, 'other-project') == (0, 0) and len(server['calls']) == 2
            runtime.price_registry = ModelPriceRegistry()
            replay = await client.post('/api/v1/asset-image-generation/jobs/' + original['id'] + '/retry', json=body)
            assert replay.status_code == 201 and replay.json()['data']['id'] == candidate['id'] and len(server['calls']) == 2


@pytest.mark.asyncio
async def test_queued_and_actual_inflight_cancel_have_distinct_original_hold_outcomes(tmp_path, monkeypatch):
    """Cancellation before dispatch releases the planned hold; actual possible POST acceptance retains it."""
    with model_server() as server:
        async with reference_client(tmp_path, monkeypatch, server) as (client, factory, slots, runtime, quote, refs, _):
            await runtime.stop()
            runtime.running = True
            queued = (await reference_submit(client, slots['actor'], quote, refs)).json()['data']
            cancelled = await client.post('/api/v1/asset-image-generation/jobs/' + queued['id'] + '/cancel')
            assert cancelled.status_code == 200 and cancelled.json()['data']['status'] == 'cancelled'
            assert await balance(factory) == (0, 0) and not server['calls']
            server['mode'] = 'block'
            runtime.running = False
            await runtime.start()
            live_response = await reference_submit(client, slots['actor'], quote, refs, key='cancel-after-post')
            assert live_response.status_code == 201, live_response.text
            live = live_response.json()['data']
            for _ in range(300):
                if server['received'].is_set():
                    break
                await asyncio.sleep(.02)
            assert server['received'].is_set()
            assert (await client.post('/api/v1/asset-image-generation/jobs/' + live['id'] + '/cancel')).status_code == 200
            await runtime.stop()
            server['release'].set()
            assert await balance(factory) == (15, 0) and len(server['calls']) == 1
            row = (await client.get('/api/v1/asset-image-generation/jobs/' + live['id'])).json()['data']
            assert row['status'] == 'cancelled' and row['content_url'] is None


@pytest.mark.asyncio
async def test_changed_model_download_policy_and_missing_complete_header_refuse_dispatch(tmp_path, monkeypatch):
    """Neither a mutable model field nor a partial manual job can expand the already reserved input authority."""
    from app.services import asset_image_generation as service
    with model_server() as server:
        async with reference_client(tmp_path, monkeypatch, server) as (client, factory, slots, runtime, quote, refs, _):
            await runtime.stop()
            runtime.running = True
            queued = (await reference_submit(client, slots['actor'], quote, refs)).json()['data']
            async with factory() as db:
                await db.execute(update(Model).where(Model.id == 'image-model').values(params={
                    'asset_image_protocol': SEEDREAM_IMAGE_PROTOCOL, 'asset_image_result_hosts': ['changed.fawnimo.example']}))
                await db.commit()
            runtime.running = False
            await runtime.start()
            assert (await terminal(client, queued['id']))['status'] == 'failed'
            assert not server['calls'] and await balance(factory) == (0, 0)
            await runtime.stop()
            runtime.running = True

            async def omit_header(*_args, **_kwargs):
                """Deliberately simulate an incorrect trusted maintenance insert, not an HTTP option."""
            monkeypatch.setattr(service, 'install_input_set', omit_header)
            partial_response = await reference_submit(client, slots['actor'], quote, refs, key='partial-input-set')
            assert partial_response.status_code == 201, partial_response.text
            partial = partial_response.json()['data']
            runtime.running = False
            await runtime.start()
            assert (await terminal(client, partial['id']))['status'] == 'failed'
            assert not server['calls'] and await balance(factory) == (0, 0)


@pytest.mark.asyncio
async def test_url_protocol_transport_seam_is_credential_free_and_records_post_before_get(monkeypatch):
    """Exercise output selection and decoder with a transport seam; this is not a live external CDN download."""
    content = png((2048, 2048))
    witnessed, calls = [], []

    async def witness(identity):
        """Represent a joined private original-charge write before any output work."""
        witnessed.append(identity)

    async def transport(url, **options):
        """Inspect exact transport boundaries independently of actual loopback positive POST tests above."""
        calls.append((url, options))
        if options['method'] == 'POST':
            return HttpResult(200, {'content-type': 'application/json', 'x-request-id': 'authored-url-post'},
                json.dumps({'model': MODEL5, 'data': [{'url': 'https://cdn.fawnimo.example/result.png?signature=inert', 'size': '2048x2048'}]}).encode())
        assert witnessed == ['authored-url-post']
        assert options['headers'] == {'Accept': 'image/png, image/jpeg'}
        assert options['allowed_hosts'] == {'cdn.fawnimo.example'} and options['max_redirects'] == 0
        assert 'trusted_local_host' not in options and 'trusted_local_ports' not in options
        return HttpResult(200, {'content-type': 'image/png'}, content)

    monkeypatch.setattr(adapter, 'request_isolated_async', transport)
    protocol = asset_image_protocol(provider='volcengine', model=MODEL5,
        params={'asset_image_protocol': SEEDREAM_IMAGE_PROTOCOL, 'asset_image_result_hosts': ['cdn.fawnimo.example']})
    result = await adapter.invoke_seedream_image(base_url='http://127.0.0.1:12345/v3', api_key='synthetic', model=MODEL5,
        prompt='original fixture', size='2048x2048', protocol=protocol, response_format='url',
        trusted_local_host='127.0.0.1', trusted_local_ports={12345}, on_response=witness)
    assert result.content == content and result.provider_request_id == 'authored-url-post' and len(calls) == 2


def test_old_fingerprint_domain_stays_exact_and_new_fields_are_never_silently_dropped():
    """Freeze the actual prior request serialization, including defaults, rather than regenerating a golden from new code."""
    legacy = {'request_key': 'old-intent', 'project_id': 'project', 'expected_price_fingerprint': 'a'*64,
        'model_id': 'model', 'prompt': 'original prompt', 'size': '1024x1024', 'confirm_model_call': True}
    body = AssetImageSubmit.model_validate(legacy)
    assert request_payload(body, protocol_key=LEGACY_IMAGE_PROTOCOL) == legacy
    assert request_payload(body, protocol_key=SEEDREAM_IMAGE_PROTOCOL) == {**legacy, 'references': [], 'response_format': 'b64_json'}
    actual_refs = [{'media_id': 'media', 'expected_sha256': 'b'*64, 'purpose': 'appearance'}]
    changed = AssetImageSubmit.model_validate({**legacy, 'references': actual_refs})
    assert request_payload(changed, protocol_key=LEGACY_IMAGE_PROTOCOL)['references'] == actual_refs


@pytest.mark.parametrize('confirmation', [1, 'true', '1', False, None])
def test_model_confirmation_requires_actual_json_true(confirmation):
    """A new network authorization cannot be supplied by a coercible integer, string or missing confirmation."""
    with pytest.raises(ValidationError):
        AssetImageSubmit.model_validate({'request_key': 'strict-intent', 'project_id': 'project',
            'expected_price_fingerprint': 'a'*64, 'model_id': 'model', 'prompt': 'original prompt',
            'confirm_model_call': confirmation})


@pytest.mark.asyncio
@pytest.mark.parametrize('fault', ['tiny', 'wide', 'animated', 'corrupt'])
async def test_reference_actual_decoder_rejects_unsupported_bytes_before_any_reservation(tmp_path, monkeypatch, fault):
    """Valid hashes and image labels cannot substitute for actual bounded single-frame decoding."""
    content = png((8, 8)) if fault == 'tiny' else png((4097, 15)) if fault == 'wide' else b'not an image'
    if fault == 'animated':
        stream = BytesIO()
        Image.new('RGB', (64, 64), 'red').save(stream, format='PNG', save_all=True,
            append_images=[Image.new('RGB', (64, 64), 'blue')], duration=100, loop=0)
        content = stream.getvalue()
    with model_server() as server:
        async with reference_client(tmp_path, monkeypatch, server) as (client, factory, slots, _, quote, refs, originals):
            originals[0][1].write_bytes(content)
            expected = hashlib.sha256(content).hexdigest()
            async with factory() as db:
                await db.execute(update(ProductionMedia).where(ProductionMedia.id == refs[0]['media_id']).values(sha256=expected, size=len(content)))
                await db.commit()
            response = await reference_submit(client, slots['actor'], quote, [{**refs[0], 'expected_sha256': expected}])
            assert response.status_code == 422 and not server['calls'] and await balance(factory) == (0, 0)
            async with factory() as db:
                assert await db.scalar(select(func.count()).select_from(AssetImageJob)) == 0


@pytest.mark.asyncio
async def test_media_selection_and_real_upload_have_generated_typed_metadata_without_storage_paths(tmp_path, monkeypatch):
    """Actual scoped routes list/upload/decode original media and keep private paths outside their typed response."""
    from app.api.v1.routes.studio.production import router as media_router
    executable = os.environ.get('FAWNIMO_TEST_FFMPEG') or os.environ.get('TEST_MEDIA_PROBE') or shutil.which('ffmpeg')
    if not executable or not Path(executable).is_file():
        pytest.skip('Trusted media decoder is not configured for actual upload acceptance')
    monkeypatch.setattr(settings, 'production_probe_executable', executable)
    with model_server() as server:
        async with reference_client(tmp_path, monkeypatch, server) as (client, factory, _, _, _, _, _):
            # This fixture owns the app and database dependency; adding the real
            # route here does not run the application's lifespan or user data.
            client._transport.app.include_router(media_router, prefix='/api/v1/studio')
            response = await client.get('/api/v1/studio/production/projects/image-project/media')
            assert response.status_code == 200 and len(response.json()['data']) == 2
            assert set(response.json()['data'][0]) == {'id', 'project_id', 'name', 'mime', 'sha256', 'size', 'created_at', 'updated_at'}
            content = png((128, 128))
            uploaded = await client.post('/api/v1/studio/production/projects/image-project/media',
                files={'file': ('authored-reference.png', content, 'image/png')})
            assert uploaded.status_code == 201, uploaded.text
            row = uploaded.json()['data'][0]
            assert row['project_id'] == 'image-project' and row['sha256'] == hashlib.sha256(content).hexdigest()
            assert 'relative_path' not in row and 'storage_key' not in row
            assert (await client.get('/api/v1/studio/production/projects/image-project/media/' + row['id'] + '/content')).content == content
            assert (await client.get('/api/v1/studio/production/projects/other-project/media/' + row['id'] + '/content')).status_code == 404
            assert not server['calls'] and await balance(factory) == (0, 0)


@pytest.mark.parametrize('value', [['*.example.com'], ['https://cdn.example.com'], ['127.0.0.1'], ['cdn.example.com:443'], ['cdn.localhost'], ['CDN.example.com'], ['cdn.example.com', 'cdn.example.com']])
def test_result_host_policy_cannot_be_a_wildcard_url_port_ip_or_local_alias(value):
    """Only the explicit administrator's normalized exact DNS host set can authorize later public GET targets."""
    with pytest.raises(ValueError):
        result_host_policy(value)


@pytest.mark.parametrize('url', ['http://cdn.fawnimo.example/a.png', 'https://cdn.fawnimo.example:444/a.png',
    'https://user:secret@cdn.fawnimo.example/a.png', 'https://cdn.fawnimo.example/a.png#fragment',
    'https://other.example/a.png', 'https://127.0.0.1/a.png', 'data:image/png;base64,AA'])
def test_returned_url_cannot_broaden_sealed_https_target(url):
    """Reject routing traps before transport and keep signed result URLs out of public errors."""
    with pytest.raises(ValueError):
        adapter.result_url(url, ('cdn.fawnimo.example',))


@pytest.mark.parametrize('raw', [b'{"usage":1e999}', b'{"usage":-1e999}', b'{"usage":NaN}', b'{"usage":Infinity}', b'{"usage":1,"usage":2}'])
def test_strict_provider_response_rejects_nonfinite_exponents_and_ambiguous_fields(raw):
    """Strict response parsing includes exponent overflow, without claiming these values previously authorized money."""
    with pytest.raises(ValueError):
        adapter.strict_json(raw)
