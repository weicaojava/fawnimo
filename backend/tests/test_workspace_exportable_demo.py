"""Real scoped worker HTTP, lost control ACK, manual retention confirmation and actual local exports."""
import asyncio
import hashlib
import io
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import zipfile

import httpx
import pytest

from app.controlplane import demo
from app.controlplane.gateway import create_gateway
from app.controlplane.store import IdentityStore
from app.controlplane.workers import WorkspaceWorkers
from app.core.contracts.local_example import example_identity, EXAMPLE_TEMPLATE
from app.services.editing.media_child import probe

PASSWORD = 'original-example-disposable-fixture-password'


def account(store, name):
    """Provision only a private test account; no browser login or real user credentials are used."""
    store.provision(name,PASSWORD)
    token = store.login(name,PASSWORD,'fixture-local')
    return token, store.current(token)


def scope(current):
    """Use the actual logged-in identity, as the generated browser client does."""
    return {'X-Workspace-Id':current['workspace_id'],'X-Identity-Id':current['user']['id']}


@pytest.mark.asyncio
async def test_real_workspace_example_ack_recovery_isolation_and_all_export_artifacts(tmp_path, monkeypatch):
    """Recover one actual typed Ready and export confirmed original media without model calls or cross-account access."""
    executable = os.environ.get('FAWNIMO_TEST_FFMPEG') or os.environ.get('TEST_MEDIA_PROBE')
    if not executable or not Path(executable).is_file():
        pytest.skip('Trusted local FFmpeg required for actual export')
    store = IdentityStore(tmp_path/'control')
    token_a, a = account(store,'example.a')
    token_b, b = account(store,'example.b')
    pool = WorkspaceWorkers(tmp_path/'workspaces',probe=Path(executable),capacity=2)
    gateway = create_gateway(tmp_path/'control',secure_cookies=False,workers=pool)
    key = 'original-example-http-01'
    target = example_identity(a['workspace_id'],a['user']['id'],key)
    original_progress = demo._progress
    lost = False

    def lose_one_ack(store, row, completed, *, succeeded=False):
        """Lose the control acknowledgement after the real worker has already committed its complete new project."""
        nonlocal lost
        if succeeded and row['template_version']==EXAMPLE_TEMPLATE and not lost:
            lost = True
            raise OSError('Disposable fixture loses control ACK only')
        return original_progress(store,row,completed,succeeded=succeeded)

    monkeypatch.setattr(demo,'_progress',lose_one_ack)
    try:
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=gateway),base_url='http://fixture.test',
                cookies={'studio_session':token_a},headers=scope(a),timeout=180) as client:
            wrong = await client.post('/api/v1/identity/demo-exportable',json={'request_key':key,'output':'../outside'})
            assert wrong.status_code==422
            first = await client.post('/api/v1/identity/demo-exportable',json={'request_key':key})
            assert first.status_code==502 and lost
            with store.connect() as db:
                control = dict(db.execute('SELECT * FROM demo_requests WHERE request_key=?',(key,)).fetchone())
                assert control['state']=='failedunknown' and control['completed_steps']==0
            with sqlite3.connect(pool.root/a['workspace_id']/'studio.db') as db:
                assert db.execute('SELECT state,attempts FROM local_example_requests WHERE id=?',(target['id'],)).fetchone()==('ready',1)
                assert db.execute('SELECT count(*) FROM production_takes').fetchone()[0]==7
                assert db.execute('SELECT count(*) FROM execution_jobs').fetchone()[0]==0
            monkeypatch.setattr(demo,'_progress',original_progress)
            recovered = await client.post('/api/v1/identity/demo-exportable',json={'request_key':key})
            assert recovered.status_code==200, recovered.text
            result = recovered.json()['data']
            assert result['replayed'] and result['project_id']==target['project_id'] and result['external_model_calls']==0
            base = '/api/v1/editing/projects/'+result['project_id']
            draft_url = base+'/drafts/'+result['draft_id']
            read = await client.get(draft_url)
            assert read.status_code==200
            draft = read.json()['data']
            assert not draft['readiness']['can_freeze'] and len(draft['document']['anchors'])==7
            denied = await client.post(draft_url+'/freeze',json={'expected_revision':1})
            assert denied.status_code==409
            document = draft['document']
            document['title'] = 'fixture explicitly reviewed original geometry'
            for anchor in document['anchors']:
                anchor['confirmed'] = True
            saved = await client.patch(draft_url,json={'expected_revision':1,'document':document})
            assert saved.status_code==200 and saved.json()['data']['readiness']['can_freeze']
            frozen = await client.post(draft_url+'/freeze',json={'expected_revision':2})
            assert frozen.status_code==201, frozen.text
            publication = frozen.json()['data']
            assert publication['payload']['duration_ms']==28000
            admitted = await client.post(base+'/publications/'+publication['id']+'/exports',json={'request_key':'original-local-export-01'})
            assert admitted.status_code==201, admitted.text
            exported = admitted.json()['data']
            for _ in range(300):
                response = await client.get(base+'/exports/'+exported['id'])
                assert response.status_code==200
                exported = response.json()['data']
                if exported['state'] in {'ready','failed','cancelled'}:
                    break
                await asyncio.sleep(.2)
            assert exported['state']=='ready', exported['reason']
            assert exported['model_calls']==0 and len(exported['files'])==4
            downloaded = {}
            for item in exported['files']:
                content = await client.get(item['content_url'])
                assert content.status_code==200 and len(content.content)==item['size']
                assert hashlib.sha256(content.content).hexdigest()==item['sha256']
                downloaded[item['kind']] = content.content
            video = tmp_path/'downloaded-original-example.mp4'
            video.write_bytes(downloaded['video'])
            facts = probe(video,output=True)
            assert facts['video']['frames']==840 and facts['video']['coverage_us']==[[0,28000000]]
            assert (facts['video']['width'],facts['video']['height'])==(360,640)
            decoded = subprocess.run([executable,'-nostdin','-hide_banner','-v','error','-i',str(video),'-f','null','-'],
                capture_output=True,timeout=30,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
            assert decoded.returncode==0 and not decoded.stderr
            assert downloaded['subtitles_srt'].decode('utf8').strip()==''
            assert downloaded['subtitles_vtt'].decode('utf8').strip()=='WEBVTT'
            with zipfile.ZipFile(io.BytesIO(downloaded['package'])) as archive:
                assert set(archive.namelist())=={'video.mp4','subtitles.srt','subtitles.vtt','manifest.json'}
                manifest = json.loads(archive.read('manifest.json'))
                assert manifest['model_quality_qualified'] is False and manifest['human_declarations_only'] is True
                assert manifest['timeline']['duration_ms']==28000 and len(manifest['timeline']['clips'])==7
                assert b'relative_path' not in archive.read('manifest.json') and b'original-nonce-' not in archive.read('manifest.json')
            replay = await client.post('/api/v1/identity/demo-exportable',json={'request_key':key})
            assert replay.status_code==200 and replay.json()['data']['replayed']
            assert (await client.get(draft_url)).json()['data']['document']['title']==document['title']
            mixed = await client.post('/api/v1/identity/demo',json={'request_key':key})
            assert mixed.status_code==409
            # Both modes share the original20-intent ledger. Only control
            # reservations are added here; they create no tenant projects.
            for index in range(19):
                demo._claim(store,a['workspace_id'],a['user']['id'],f'text-quota-{index:02d}','f'*64,f'quota-only-{index}')
            full = await client.post('/api/v1/identity/demo-exportable',json={'request_key':'new-over-cap-request'})
            assert full.status_code==429
            assert (await client.post('/api/v1/identity/demo-exportable',json={'request_key':key})).status_code==200
            assert (await client.get('/__workspace/original-example')).status_code==404
            assert (await client.get('/api/v1/__workspace/original-example',params={'request_key':key})).status_code==404
            worker = await pool.get(a['workspace_id'])
            private_headers = {'X-Workspace-Token':worker.token,**scope(a),'X-Workspace-Role':'owner'}
            async with httpx.AsyncClient(base_url=f'http://127.0.0.1:{worker.port}',trust_env=False) as private:
                assert (await private.get('/__workspace/original-example',params={'request_key':key})).status_code==403
                for change in ({'X-Workspace-Id':b['workspace_id']},{'X-Workspace-Role':'viewer'},{'X-Identity-Id':'forged-actor'}):
                    refused = await private.get('/__workspace/original-example',params={'request_key':key},headers={**private_headers,**change})
                    assert refused.status_code==403
                schema = (await private.get('/openapi.json',headers=private_headers)).json()
                assert not any('/__workspace/' in path for path in schema['paths'])
                assert '/api/v1/identity/demo-exportable' in schema['paths']
            await pool.stop_worker(worker)
            pool.workers.pop(a['workspace_id'])
            # Restart creates a new token/process over this same owned database;
            # completed example recovery remains read-only with no new attempt.
            restarted = await client.post('/api/v1/identity/demo-exportable',json={'request_key':key})
            assert restarted.status_code==200 and restarted.json()['data']['replayed']
            assert (await client.get(draft_url)).json()['data']['document']['title']==document['title']
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=gateway),base_url='http://fixture.test',
                    cookies={'studio_session':token_b},headers=scope(b)) as other:
                assert (await other.get(draft_url)).status_code==404
                file_url = exported['files'][0]['content_url']
                assert (await other.get(file_url)).status_code==404
            with sqlite3.connect(pool.root/a['workspace_id']/'studio.db') as db:
                assert db.execute('SELECT state,attempts FROM local_example_requests').fetchall()==[('ready',1)]
                assert db.execute('SELECT count(*) FROM production_takes').fetchone()[0]==7
                assert db.execute('SELECT count(*) FROM projects').fetchone()[0]==1
                assert db.execute('SELECT count(*) FROM execution_jobs').fetchone()[0]==0
                assert db.execute('PRAGMA foreign_key_check').fetchall()==[]
    finally:
        await pool.close()
