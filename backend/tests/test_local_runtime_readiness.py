"""Additional actual native-worker readiness/row-preservation check using only new private fixtures; no Docker or image qualification."""
import asyncio
import json
import os
from pathlib import Path
import time
import httpx
import pytest

import local_runtime_bootstrap as bootstrap
from app.controlplane.gateway import create_gateway
from app.controlplane.maintenance import ProcessLease
from app.services.community.runtime_release_helper import initialize_data,snapshot_data,restore_data
from app.services.community.local_release_files import read_json


def test_actual_private_ready_starts_each_worker_and_preserves_original_business_columns(tmp_path,monkeypatch):
    """Map only the fixed /data prefix to this new owned fixture, then exercise actual workers before/after a real full-root restore."""
    config={'profile':'local_runtime_release_v1','slot':'stable','origin':'http://127.0.0.1:18080','ready_token':'a'*64}
    data=tmp_path/'fresh-data';data.mkdir(mode=0o700)
    initialize_data(data,config,'b'*64,time.monotonic()+120)
    selected=[data]
    def own_path(value):
        """Trusted test embedding changes no product input/CLI and cannot address any pre-existing host /data directory."""
        if isinstance(value,str) and (value=='/data' or value.startswith('/data/')):
            return selected[0]/value.removeprefix('/data').lstrip('/')
        return Path(value)
    monkeypatch.setattr(bootstrap,'Path',own_path)
    async def exercise(root,create=False,edit=False,expected_name='preserved-own-business'):
        """Run the installed readiness class and join every actual native process before returning to offline maintenance."""
        probe=os.environ.get('TEST_MEDIA_PROBE')
        with ProcessLease(root/'studio'/'.maintenance.lock'):
            app=create_gateway(root/'studio',probe=Path(probe) if probe else None,public_origin=config['origin'],secure_cookies=False)
            wrapper=bootstrap.PrivateReady(app,config)
            try:
                await wrapper.prepare()
                assert wrapper.prepared and len(app.state.workers.workers)==2
                claims=read_json(root/'claims.json')['a'];store=app.state.identity_store
                token=store.login(claims['account'],claims['password'],'new-authored-private-peer')
                identity=store.current(token)
                headers={'Origin':config['origin'],'X-Identity-Id':identity['user']['id'],'X-Workspace-Id':identity['workspace_id']}
                async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url=config['origin'],headers=headers,
                                             cookies={'studio_session':token}) as client:
                    if create:
                        response=await client.post('/api/v1/studio/projects',json={'id':'p','name':'preserved-own-business','style':'真人都市','visual_style':'现实'})
                        assert response.status_code==201
                    else:
                        response=await client.get('/api/v1/studio/projects/p')
                        assert response.status_code==200 and response.json()['data']['name']==expected_name
                    if edit:
                        response=await client.put('/api/v1/video-runtime/projects/p/budget',json={'expected_revision':0,'limit_minor':200})
                        assert response.status_code==200
                        response=await client.patch('/api/v1/studio/projects/p',json={'name':'legitimate-later-edit'})
                        assert response.status_code==200
                    elif expected_name=='legitimate-later-edit':
                        response=await client.get('/api/v1/video-runtime/projects/p/budget')
                        assert response.status_code==200 and response.json()['data']['limit_minor']==200
            finally:await app.state.workers.close()
    asyncio.run(exercise(data,True))
    archive=tmp_path/'archive';archive.mkdir(mode=0o700)
    snapshot_data(data,archive,time.monotonic()+120)
    restored=tmp_path/'restored';restored.mkdir(mode=0o700)
    restore_data(archive,data,restored,config,time.monotonic()+120)
    assert not (restored/'runtime-prepared.json').exists(), 'A new data generation must perform its own original-row verification'
    selected[0]=restored
    asyncio.run(exercise(restored,edit=True))
    assert (restored/'runtime-prepared.json').is_file()
    asyncio.run(exercise(restored,expected_name='legitimate-later-edit'))
    receipt=read_json(restored/'runtime-prepared.json')
    receipt['config_sha256']='f'*64
    (restored/'runtime-prepared.json').write_text(json.dumps(receipt))
    with pytest.raises(ValueError,match='receipt changed'):
        asyncio.run(exercise(restored,expected_name='legitimate-later-edit'))
