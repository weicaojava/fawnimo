"""Real decoded media through project HTTP endpoints; no model calls or response mocks."""
import asyncio
from pathlib import Path
import subprocess
from types import SimpleNamespace
import pytest
from httpx import ASGITransport,AsyncClient
from app.main import app
from app.api.reverse import get_reverse_factory
from app.config import settings
from app.dependencies import get_db
from app.models.production import ProductionMedia
from app.services.reverse.ingestion import run_analysis
from app.services.reverse.schema import migrate_reverse
from app.services.studio.production_media import media_path
from tests.test_durable_execution import setup


@pytest.mark.asyncio
async def test_video_import_decode_manual_cuts_and_safe_assets(tmp_path,monkeypatch):
    """HTTP creation, actual decode and manual editing preserve original evidence."""
    ffmpeg=Path(__file__).resolve().parents[4]/'ffmpeg.exe'
    source=tmp_path/'fixture.mp4'
    subprocess.run([str(ffmpeg),'-nostdin','-v','error','-f','lavfi','-i','testsrc2=s=64x48:r=24:d=2','-c:v','libx264','-pix_fmt','yuv420p',str(source)],check=True)
    monkeypatch.setattr(settings,'production_media_root',str(tmp_path/'media'))
    monkeypatch.setattr(settings,'production_probe_executable',str(ffmpeg))
    monkeypatch.setattr(settings,'reverse_media_allowed_hosts','')
    engine,factory,_,_,_,_=await setup(tmp_path)
    await migrate_reverse(engine)
    async def dependency():
        """Give each real HTTP request its own SQLite session and transaction."""
        async with factory() as db:
            try:yield db;await db.commit()
            except BaseException:await db.rollback();raise
    app.dependency_overrides[get_db]=dependency
    app.dependency_overrides[get_reverse_factory]=lambda:factory
    previous=getattr(app.state,'reverse_scanner',None)
    enabled=asyncio.create_task(asyncio.sleep(3600))
    app.state.reverse_scanner=SimpleNamespace(task=enabled)
    base='/api/v1/reverse/projects/p'
    try:
        async with AsyncClient(transport=ASGITransport(app=app),base_url='http://test',trust_env=False) as client:
            upload=await client.post('/api/v1/studio/production/projects/p/media',files={'file':('fixture.mp4',source.read_bytes(),'video/mp4')})
            assert upload.status_code==201,upload.text
            media_id=upload.json()['data'][0]['id']
            response=await client.post(base+'/analyses',json={'media_id':media_id,'request_key':'same'})
            assert response.status_code==201,response.text
            identity=response.json()['data']['id']
            repeat=await client.post(base+'/analyses',json={'media_id':media_id,'request_key':'same'})
            assert repeat.json()['data']['id']==identity
            path=base+'/analyses/'+identity
            assert await run_analysis(factory,identity,str(ffmpeg)) is True
            report=(await client.get(path)).json()['data']
            assert report['state'] in {'ready','degraded'} and len(report['manifest']['video']['frames'])==48
            assert report['confirmed_cuts'] is None and report['manifest']['cuts_are_ground_truth'] is False
            assert 'artifact_dir' not in report and 'lease_token' not in report
            for asset,mime in [('/proxy','video/mp4'),('/frames/0','image/jpeg'),('/frames/10','image/jpeg')]:
                result=await client.get(path+asset)
                assert result.status_code==200,result.text[:100]
                assert mime in result.headers['content-type'] and result.content
            assert (await client.get(path+'/audio')).status_code==404
            assert (await client.get(path+'/frames/-1')).status_code==404
            assert (await client.get('/api/v1/reverse/projects/foreign/analyses/'+identity)).status_code==404
            assert (await client.get('/api/v1/reverse/projects/foreign/analyses/'+identity+'/proxy')).status_code==404
            for invalid in [[1],[0,0],[0,48],[0,True]]:
                assert (await client.patch(path+'/cuts',json={'frame_indices':invalid,'expected_revision':1})).status_code==422
            edited=await client.patch(path+'/cuts',json={'frame_indices':[0,24],'expected_revision':1})
            assert edited.status_code==200,edited.text
            assert edited.json()['data']['revision']==2
            assert edited.json()['data']['confirmed_cuts']==[{'frame_index':0,'relative_us':0},{'frame_index':24,'relative_us':1000000}]
            assert edited.json()['data']['manifest']==report['manifest']
            assert (await client.patch(path+'/cuts',json={'frame_indices':[0],'expected_revision':1})).status_code==409
            assert (await client.post(base+'/import-url',json={'url':'https://unconfigured.invalid/video.mp4'})).status_code==503
            assert (await client.post(base+'/import-url',json={'url':'file:///secret','allowed_hosts':['*']})).status_code==422
            async with factory() as db:
                media=await db.get(ProductionMedia,media_id)
                stored=media_path(media.relative_path)
            assert stored.read_bytes()==source.read_bytes()
            stored.write_bytes(b'corrupt owned fixture')
            assert (await client.get(path)).status_code==409
            assert (await client.get(path+'/proxy')).status_code==409
    finally:
        enabled.cancel();await asyncio.gather(enabled,return_exceptions=True)
        app.state.reverse_scanner=previous
        app.dependency_overrides.clear()
        await engine.dispose()
