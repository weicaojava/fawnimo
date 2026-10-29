"""Real gateway/two-worker resource isolation matrix, using only synthetic local accounts and media."""
from contextlib import closing
from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
from io import BytesIO
import os
from pathlib import Path
import shutil
import sqlite3
import subprocess
import zipfile

from fastapi.testclient import TestClient
from PIL import Image
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.controlplane.gateway import create_gateway
from app.models.agent_execution import AgentRun
from app.models.asset_image_generation import AssetImageJob
from app.models.editing import EditPublication
from app.models.editing_export import EditExportJob, EditExportProduct
from app.models.editing_v2 import TransitionDraft, TransitionPublication, TransitionExportJob, TransitionExportProduct
from app.models.production import ProductionMedia
from app.models.studio import FileItem
from app.models.task import GenerationTask
from app.services.editing.media import canonical_hash

PASSWORD='matrix-fixture-password-2026'
FAMILIES=('project','chapter','shot','detail','actor','character','costume','scene','prop','prompt','agent','provider','model','file')


@dataclass
class Resource:
    """Keep request metadata separate from fixture contents and session credentials."""
    path: str
    listing: str | None
    patch: dict
    identifier: str


class Matrix:
    """Drive actual authenticated proxy requests into permanently scoped worker databases."""

    def __init__(self,app,client):
        """Create independent trusted identities without sharing the browser login cookie jar."""
        self.app,self.client=app,client
        self.store=app.state.identity_store
        self.users,self.tokens,self.current,self.resources,self.extras={},{},{},{},{}
        self.export_bytes={}
        for side in ('a','b'):
            self.users[side]=self.store.provision('matrix.'+side,PASSWORD)
            self.tokens[side]=self.store.login('matrix.'+side,PASSWORD,'fixture-local')
            self.current[side]=self.store.current(self.tokens[side])
            self.resources[side]={}
            self.extras[side]={}
            self.seed(side)
            self.seed_retained_jobs(side)
            self.seed_retained_exports(side)

    def headers(self,side,**extra):
        """Select only a real current session; the gateway must derive role and worker itself."""
        identity=self.current[side]
        return {'Cookie':'studio_session='+self.tokens[side],'X-Identity-Id':identity['user']['id'],
                'X-Workspace-Id':identity['workspace_id'],'Origin':'http://matrix.local',**extra}

    def request(self,side,method,path,**kwargs):
        """Keep every mutation/read on the same public ASGI gateway contract."""
        headers={**self.headers(side),**kwargs.pop('headers',{})}
        return self.client.request(method,'/api/v1'+path,headers=headers,**kwargs)

    def ok(self,side,method,path,**kwargs):
        """Require a real successful response rather than treating a disabled route as coverage."""
        response=self.request(side,method,path,**kwargs)
        assert response.is_success,(method,path,response.status_code,response.text)
        return response.json()['data']

    def seed(self,side):
        """Persist private projects, nested entities, histories, model configs and actual PNG bytes."""
        records=self.resources[side]
        marker='private-'+side
        def add(family,listing,body,patch_field='name'):
            """Create once and retain the server's concrete identifier for negative/positive controls."""
            result=self.ok(side,'POST',listing,json=body)
            identity=result['id']
            records[family]=Resource(listing+'/'+identity,listing,{patch_field:marker+'-edited-'+family},identity)
            return identity
        project=add('project','/studio/projects',{'id':side+'-project','name':marker+'-project','style':'真人都市','visual_style':'现实'})
        chapter=add('chapter','/studio/chapters',{'id':side+'-chapter','project_id':project,'index':1,'title':marker+'-chapter'},'title')
        shot=add('shot','/studio/shots',{'id':side+'-shot','chapter_id':chapter,'index':1,'title':marker+'-shot','status':'pending'},'title')
        self.ok(side,'POST','/studio/shot-details',json={'id':shot,'camera_shot':'MS','angle':'EYE_LEVEL','movement':'STATIC','first_frame_prompt':marker+'-prompt'})
        records['detail']=Resource('/studio/shot-details/'+shot,'/studio/shot-details',{'atmosphere':marker+'-edited-detail'},shot)
        for family in ('actor','costume','scene','prop'):
            add(family,'/studio/entities/'+family,{'id':side+'-'+family,'name':marker+'-'+family,'project_id':project})
        add('character','/studio/entities/character',{'id':side+'-character','project_id':project,'name':marker+'-character',
            'actor_id':records['actor'].identifier,'costume_id':records['costume'].identifier})
        add('prompt','/studio/prompts',{'category':'video_prompt','name':marker+'-template','content':marker+'-template-content'})
        add('agent','/agents',{'name':marker+'-agent','type':'plot','prompt':marker+'-agent-prompt'})
        provider=add('provider','/llm/providers',{'id':side+'-provider','name':'OpenAI','description':marker+'-provider','base_url':'https://example.invalid/v1',
            'api_key':'fixture-'+side+'-credential','status':'testing'},'description')
        add('model','/llm/models',{'id':side+'-model','name':marker+'-model','category':'text','provider_id':provider,'params':{'model_name':'fixture-text'}})
        stream=BytesIO();Image.new('RGB',(16,16),(190,20,30) if side=='a' else (10,30,190)).save(stream,format='PNG')
        content=stream.getvalue()
        result=self.ok(side,'POST','/studio/files/upload',data={'project_id':project,'chapter_id':chapter,'shot_id':shot,'usage_kind':'upload'},files={'file':(marker+'.png',content,'image/png')})
        records['file']=Resource('/studio/files/'+result['id'],'/studio/files',{'name':marker+'-edited-file'},result['id'])
        history=self.ok(side,'GET','/studio/chapters/'+chapter+'/text-history')
        self.ok(side,'POST','/studio/chapters/'+chapter+'/text-history',json={'expected_revision':history['current_revision'],'raw_text':marker+'-historical-text'})
        self.ok(side,'PUT','/video-runtime/projects/'+project+'/budget',json={'expected_revision':0,'limit_minor':1000})
        extra=self.extras[side]
        production='/studio/production/projects/'+project
        extra['production_media']=self.ok(side,'POST',production+'/media',files={'file':(marker+'.png',content,'image/png')})[0]['id']
        asset=self.ok(side,'POST',production+'/assets',json={'kind':'prop','name':marker+'-versioned-prop','description':marker+'-version-one','media_ids':[extra['production_media']]})
        extra['production_asset']=asset['asset']['id']
        extra['production_version']=asset['version']['id']
        extra['edit_draft']=self.ok(side,'POST','/editing/projects/'+project+'/drafts',json={'chapter_id':chapter,'document':{'title':marker+'-assembly','clips':[]}})['id']
        graph=self.ok(side,'GET','/studio/shots/graph',params={'project_id':project,'chapter_id':chapter})
        mutation=self.ok(side,'POST','/studio/shots/graph/actions',json={'project_id':project,'chapter_id':chapter,'request_key':'initial-reorder',
            'expected_revision':graph['revision'],'expected_fingerprint':graph['fingerprint'],'action':'reorder','ordered_shot_ids':[shot]})
        extra['graph_receipt']=mutation['receipt']['id']

    def seed_retained_jobs(self,side):
        """Insert explicitly failed local history, never a queued job or an external provider call.

        Public admission correctly refuses paid tasks, so this fixture separately
        exercises read/control authorization for pre-existing task history.
        """
        records=self.resources[side]
        now=datetime.now(timezone.utc)
        marker='private-'+side
        engine=create_engine('sqlite:///'+self.database(side).as_posix())
        try:
            with engine.begin() as connection:
                connection.exec_driver_sql('PRAGMA foreign_keys=ON')
                with Session(bind=connection) as db:
                    for kind in ('workflow','connection_test'):
                        db.add(AgentRun(id=side+'-'+kind,kind=kind,scope_key=side+'-'+kind,request_key='retained-fixture',request_hash='a'*64,
                            agent_id=records['agent'].identifier if kind=='workflow' else None,agent_name=marker+'-agent',agent_version=1,
                            config_snapshot={'private':marker+'-config'},model_id=records['model'].identifier,model_name=marker+'-model',
                            model_binding={'secret':'fixture-'+side+'-credential'},status='failed',input_text=marker+'-input',output_text=marker+'-output',
                            error='Synthetic local history; no provider call',max_tokens=8,created_at=now,finished_at=now))
                    db.add(AssetImageJob(id=side+'-image-job',scope_key=side+'-image',request_key='retained-fixture',request_hash='a'*64,
                        asset_kind='actor',asset_id=records['actor'].identifier,slot_id=1,model_id=records['model'].identifier,model_name=marker+'-model',
                        model_binding={'secret':'fixture-'+side+'-credential'},prompt=marker+'-image-prompt',size='256x256',status='failed',
                        error='Synthetic local history; no provider call',created_at=now,finished_at=now))
                    db.flush()
        finally:
            engine.dispose()

    def database(self,side):
        """Inspect only this test's generated fixed root, never a user workspace."""
        return self.store.root/'workspaces'/self.current[side]['workspace_id']/'studio.db'

    def seed_retained_exports(self,side):
        """Make explicit synthetic history with real authored bytes, solely for read authorization.

        These SQL fixtures do not claim the editing freeze/render workflow passed.
        They exercise ready-history content checks without queueing a renderer.
        """
        resources=self.resources[side]
        project=resources['project'].identifier
        chapter=side+'-export-history-chapter'
        self.ok(side,'POST','/studio/chapters',json={'id':chapter,'project_id':project,'index':77,'title':'Synthetic export history'})
        draft=self.ok(side,'POST','/editing/projects/'+project+'/drafts',json={'chapter_id':chapter,'document':{'title':'Synthetic retained history','clips':[]}})['id']
        self.extras[side]['export_chapter']=chapter
        root=self.database(side).parent/'media'
        folder=root/('synthetic-export-'+side)
        folder.mkdir()
        video=folder/'authored.mp4'
        executable=os.environ.get('FAWNIMO_TEST_FFMPEG') or shutil.which('ffmpeg')
        subprocess.run([executable,'-nostdin','-v','error','-f','lavfi','-i',
            'color='+('red' if side=='a' else 'blue')+':s=32x48:r=30:d=0.2',
            '-c:v','libx264','-threads','1','-pix_fmt','yuv420p',str(video)],check=True,timeout=20)
        content={'video':video.read_bytes(),'subtitles_srt':('1\n00:00:00,000 --> 00:00:00,100\nSelf-authored '+side+'\n').encode(),
            'subtitles_vtt':('WEBVTT\n\n00:00:00.000 --> 00:00:00.100\nSelf-authored '+side+'\n').encode()}
        archive=BytesIO()
        with zipfile.ZipFile(archive,'w') as stream:
            stream.writestr('video.mp4',content['video']);stream.writestr('subtitles.srt',content['subtitles_srt'])
        content['package']=archive.getvalue()
        suffixes={'video':'.mp4','subtitles_srt':'.srt','subtitles_vtt':'.vtt','package':'.zip'}
        artifacts={}
        for kind,value in content.items():
            target=folder/(kind+suffixes[kind]);target.write_bytes(value)
            artifacts[kind]={'relative_path':target.relative_to(root).as_posix(),'size':len(value),'sha256':hashlib.sha256(value).hexdigest()}
        payload={'schema_version':1,'fixture_only':True,'project_id':project,'chapter_id':chapter,'document':{'title':'Synthetic retained history'},
            'clips':[],'duration_ms':200,'model_calls':0,'base':{'publication_id':side+'-retained-publication'}}
        engine=create_engine('sqlite:///'+self.database(side).as_posix())
        try:
            with engine.begin() as connection:
                connection.exec_driver_sql('PRAGMA foreign_keys=ON')
                with Session(bind=connection) as db:
                    base_id=side+'-retained-publication'
                    db.add(EditPublication(id=base_id,project_id=project,chapter_id=chapter,draft_id=draft,
                        draft_revision=1,payload=payload,content_hash=canonical_hash(payload)));db.flush()
                    transition_draft=side+'-retained-transition-draft'
                    db.add(TransitionDraft(id=transition_draft,project_id=project,chapter_id=chapter,base_publication_id=base_id,revision=1,
                        document={'title':'Synthetic retained transition','edges':[]}));db.flush()
                    transition_publication=side+'-retained-transition-publication'
                    db.add(TransitionPublication(id=transition_publication,project_id=project,chapter_id=chapter,draft_id=transition_draft,
                        draft_revision=1,payload=payload,content_hash=canonical_hash(payload)));db.flush()
                    for version,job_model,product_model,publication in [('v1',EditExportJob,EditExportProduct,base_id),('v2',TransitionExportJob,TransitionExportProduct,transition_publication)]:
                        identifier=side+'-export-'+version
                        media_id=identifier+'-media'
                        version_artifacts={}
                        for kind,value in content.items():
                            target=folder/(version+'-'+kind+suffixes[kind]);target.write_bytes(value)
                            version_artifacts[kind]={**artifacts[kind],'relative_path':target.relative_to(root).as_posix()}
                        db.add(FileItem(id=media_id,name='private-'+side+'-retained-'+version,type='video',storage_key='local-production/'+version_artifacts['video']['relative_path']))
                        db.flush()
                        db.add(ProductionMedia(id=media_id,project_id=project,name='Synthetic retained export',mime='video/mp4',
                            relative_path=version_artifacts['video']['relative_path'],size=version_artifacts['video']['size'],sha256=version_artifacts['video']['sha256']))
                        db.add(GenerationTask(id=identifier+'-task',mode='async_polling',task_kind='local-export-fixture',status='succeeded',progress=100,payload={'fixture_only':True}));db.flush()
                        db.add(job_model(id=identifier,project_id=project,chapter_id=chapter,publication_id=publication,
                            publication_hash=canonical_hash(payload),task_id=identifier+'-task',request_key=identifier,intent_hash='a'*64,state='ready',attempts=1));db.flush()
                        db.add(product_model(job_id=identifier,project_id=project,media_id=media_id,artifacts=version_artifacts,facts={'fixture_only':True}));db.flush()
                        self.export_bytes[(side,version)]=content
                        self.extras[side]['export_'+version]=identifier
        finally:
            engine.dispose()


@pytest.fixture(scope='module')
def matrix(tmp_path_factory):
    """One pair of real processes serves the full matrix; gateway shutdown owns their cleanup."""
    root=tmp_path_factory.mktemp('resource-matrix')
    executable=os.environ.get('FAWNIMO_TEST_FFMPEG') or shutil.which('ffmpeg')
    assert executable and Path(executable).is_file(),'Install FFmpeg or set FAWNIMO_TEST_FFMPEG for actual media validation'
    app=create_gateway(root,secure_cookies=False,public_origin='http://matrix.local',capacity=2,probe=Path(executable))
    with TestClient(app,base_url='http://matrix.local') as client:
        yield Matrix(app,client)


@pytest.mark.parametrize('side',['a','b'])
@pytest.mark.parametrize('family',FAMILIES)
@pytest.mark.parametrize('method',['GET','PATCH','DELETE'])
def test_foreign_resource_cannot_be_read_changed_or_deleted(matrix,side,family,method):
    """Positive owner read plus negative foreign CRUD proves actual data preservation, not just status gates."""
    other='b' if side=='a' else 'a'
    record=matrix.resources[other][family]
    before=matrix.ok(other,'GET',record.path)
    response=matrix.request(side,method,record.path,**({'json':record.patch} if method=='PATCH' else {}))
    # Legacy delete contracts deliberately return success for an absent ID. In
    # that case require the exact same empty result as an arbitrary absent ID.
    allowed={400,403,404,409} | ({200,204} if method=='DELETE' else set())
    assert response.status_code in allowed,(family,method,response.status_code,response.text)
    if method=='DELETE' and response.is_success:
        absent=matrix.request(side,'DELETE',record.path.rsplit('/',1)[0]+'/matrix-absent')
        assert (response.status_code,response.content)==(absent.status_code,absent.content)
    assert 'private-'+other not in response.text
    assert 'fixture-'+other+'-credential' not in response.text
    assert matrix.ok(other,'GET',record.path)==before


@pytest.mark.parametrize('side',['a','b'])
@pytest.mark.parametrize('family',tuple(name for name in FAMILIES if name!='detail'))
def test_lists_and_search_never_return_another_space(matrix,side,family):
    """Search and normal lists resolve the current database even when the foreign ID is supplied as text."""
    other='b' if side=='a' else 'a'
    record=matrix.resources[side][family]
    for query in ('','?q=private-'+side):
        positive=matrix.ok(side,'GET',record.listing+query)
        assert record.identifier in {row['id'] for row in positive['items']},(family,query)
    for query in ('','?q=private-'+other):
        response=matrix.request(side,'GET',record.listing+query)
        assert response.status_code==200,response.text
        assert 'private-'+other not in response.text
        assert matrix.resources[other][family].identifier not in response.text


@pytest.mark.parametrize('side',['a','b'])
def test_nested_references_reject_foreign_ids_without_partial_writes(matrix,side):
    """A valid body with a foreign parent/file/provider fails while the owned equivalent succeeds."""
    other='b' if side=='a' else 'a'
    own,foreign=matrix.resources[side],matrix.resources[other]
    rejected=[
        ('/studio/chapters',{'id':side+'-foreign-chapter','project_id':foreign['project'].identifier,'index':9,'title':'Rejected child'}),
        ('/studio/shots',{'id':side+'-foreign-shot','chapter_id':foreign['chapter'].identifier,'index':9,'title':'Rejected shot'}),
        ('/studio/entities/character',{'id':side+'-foreign-character','project_id':own['project'].identifier,'name':'Rejected character','actor_id':foreign['actor'].identifier}),
        ('/llm/models',{'id':side+'-foreign-model','name':'Rejected model','category':'text','provider_id':foreign['provider'].identifier}),
    ]
    for path,body in rejected:
        response=matrix.request(side,'POST',path,json=body)
        assert response.status_code in {400,403,404,409},(path,response.status_code,response.text)
        assert matrix.request(side,'GET',path+'/'+body['id']).status_code==404
    actor=own['actor'].path
    slot=matrix.ok(side,'POST',actor+'/images',json={'view_angle':'FRONT','quality_level':'LOW'})
    path=actor+'/images/'+str(slot['id'])
    positive=matrix.ok(side,'PATCH',path,json={'file_id':own['file'].identifier,'width':16,'height':16,'format':'png'})
    response=matrix.request(side,'PATCH',path,json={'file_id':foreign['file'].identifier,'width':16,'height':16,'format':'png'})
    assert response.status_code in {400,403,404,409},response.text
    current=matrix.ok(side,'GET',actor+'/images')
    assert next(row for row in current['items'] if row['id']==positive['id'])['file_id']==own['file'].identifier


@pytest.mark.parametrize('side',['a','b'])
def test_files_ranges_native_scope_and_all_or_nothing_zip(matrix,side):
    """Actual PNG/range/ZIP bytes cannot be read through foreign IDs or stale media scope."""
    other='b' if side=='a' else 'a'
    own=matrix.resources[side]['file'];foreign=matrix.resources[other]['file']
    response=matrix.request(side,'GET',own.path+'/download',headers={'Range':'bytes=0-7'})
    assert response.status_code==206 and response.content==b'\x89PNG\r\n\x1a\n'
    assert matrix.request(side,'GET',foreign.path+'/download').status_code==404
    assert matrix.request(side,'GET',foreign.path+'/storage-info').status_code==404
    scope='?__studio_workspace='+matrix.current[side]['workspace_id']+'&__studio_user='+matrix.current[side]['user']['id']
    response=matrix.client.get('/api/v1'+own.path+'/download'+scope,headers={'Cookie':'studio_session='+matrix.tokens[side]})
    assert response.status_code==200 and response.content.startswith(b'\x89PNG')
    assert matrix.client.get('/api/v1'+own.path+'/download'+scope,headers={'Cookie':'studio_session='+matrix.tokens[other]}).status_code==409
    positive=matrix.request(side,'GET','/studio/files/export-bundle',params={'file_ids':[own.identifier]})
    assert positive.status_code==200,positive.text
    with zipfile.ZipFile(BytesIO(positive.content)) as archive:
        assert any(archive.read(name).startswith(b'\x89PNG') for name in archive.namelist() if not name.endswith('.json'))
    rejected=matrix.request(side,'GET','/studio/files/export-bundle',params={'file_ids':[own.identifier,foreign.identifier]})
    assert rejected.status_code in {400,403,404,409} and 'application/zip' not in rejected.headers.get('content-type','')


@pytest.mark.parametrize('side',['a','b'])
def test_histories_budget_and_exports_require_the_owned_parent(matrix,side):
    """Historical versions, amounts and export lists cannot be reached by guessing another parent ID."""
    other='b' if side=='a' else 'a'
    for owner in (side,other):
        resources=matrix.resources[owner]
        project,chapter=resources['project'].identifier,resources['chapter'].identifier
        paths=[resources['agent'].path+'/revisions',resources['agent'].path+'/export',
               '/studio/chapters/'+chapter+'/text-history','/video-runtime/projects/'+project+'/budget',
               '/video-runtime/projects/'+project+'/budget/entries',
               '/editing/projects/'+project+'/exports?chapter_id='+chapter,
               '/editing-v2/projects/'+project+'/exports?chapter_id='+chapter]
        for path in paths:
            response=matrix.request(side,'GET',path)
            assert response.status_code in ({200} if owner==side else {404,422}),(path,response.status_code,response.text)
    foreign=matrix.resources[other]['chapter'].identifier
    response=matrix.request(side,'POST','/studio/chapters/'+foreign+'/text-history/1/restore',json={'expected_revision':1})
    assert response.status_code==404


def test_same_identifier_in_different_spaces_keeps_distinct_content(matrix):
    """Scope comes from authenticated membership, not from a globally unique business identifier."""
    for side in ('a','b'):
        matrix.ok(side,'POST','/studio/projects',json={'id':'coincident-id','name':'private-'+side+'-coincident','style':'真人都市'})
    for side in ('a','b'):
        assert matrix.ok(side,'GET','/studio/projects/coincident-id')['name']=='private-'+side+'-coincident'


@pytest.mark.parametrize('method',['POST','PATCH','DELETE'])
def test_viewer_cannot_forge_a_write_role(matrix,method):
    """The current membership controls writes even if an incoming role header says owner."""
    user=matrix.current['a']['user']['id']
    with matrix.store.connect() as db:
        db.execute("UPDATE memberships SET role='viewer' WHERE user_id=?",(user,))
    try:
        path='/studio/projects' if method=='POST' else matrix.resources['a']['project'].path
        response=matrix.request('a',method,path,headers={'X-Workspace-Role':'owner'},json={'name':'Not authorized','id':'not-authorized','style':'真人都市'})
        assert response.status_code==403
        assert matrix.request('a','GET',matrix.resources['a']['project'].path).status_code==200
    finally:
        with matrix.store.connect() as db:
            db.execute("UPDATE memberships SET role='owner' WHERE user_id=?",(user,))


def test_workspace_header_role_and_audit_are_derived_from_session(matrix):
    """A stale tab cannot replace identity; write audit stores actor/scope and digest instead of secrets."""
    path=matrix.resources['a']['project'].path
    assert matrix.request('a','GET',path,headers={'X-Workspace-Id':matrix.current['b']['workspace_id']}).status_code==409
    assert matrix.request('a','PATCH',path,headers={'X-Identity-Id':matrix.current['b']['user']['id']},json={'name':'stale'}).status_code==409
    assert matrix.request('a','POST','/identity/switch',json={'workspace_id':matrix.current['b']['workspace_id']}).status_code==404
    matrix.ok('a','PATCH',path,json={'description':'matrix-private-description'})
    with matrix.store.connect() as db:
        row=db.execute('SELECT workspace_id,submitted_by,body_sha256 FROM request_audit WHERE path=? ORDER BY created_at DESC,rowid DESC LIMIT 1',('/api/v1'+path,)).fetchone()
        assert row['workspace_id']==matrix.current['a']['workspace_id'] and row['submitted_by']==matrix.current['a']['user']['id'] and len(row['body_sha256'])==64
        assert 'matrix-private-description' not in str(dict(row))
    with closing(sqlite3.connect(matrix.database('a'))) as db:
        assert db.execute('SELECT DISTINCT workspace_id FROM projects').fetchall()==[(matrix.current['a']['workspace_id'],)]


@pytest.mark.parametrize('side',['a','b'])
@pytest.mark.parametrize('family',FAMILIES)
def test_owned_patch_positive_control(matrix,side,family):
    """Every negative PATCH uses an actually supported edit on its corresponding owned resource."""
    record=matrix.resources[side][family]
    updated=matrix.ok(side,'PATCH',record.path,json=record.patch)
    persisted=matrix.ok(side,'GET',record.path)
    for key,value in record.patch.items():
        assert updated[key]==value
        assert persisted[key]==value


@pytest.mark.parametrize('side',['a','b'])
def test_nested_updates_and_uploads_rollback_without_orphans(matrix,side):
    """Foreign parents, entities, templates and usages cannot attach to an owned row or leave files behind."""
    other='b' if side=='a' else 'a'
    own,foreign=matrix.resources[side],matrix.resources[other]
    mutations=[
        (own['chapter'],{'project_id':foreign['project'].identifier}),
        (own['shot'],{'chapter_id':foreign['chapter'].identifier}),
        (own['detail'],{'scene_id':foreign['scene'].identifier}),
        (own['character'],{'actor_id':foreign['actor'].identifier}),
        (own['character'],{'costume_id':foreign['costume'].identifier}),
        (own['character'],{'project_id':foreign['project'].identifier}),
        (own['actor'],{'prompt_template_id':foreign['prompt'].identifier}),
        (own['scene'],{'prompt_template_id':foreign['prompt'].identifier}),
        (own['model'],{'provider_id':foreign['provider'].identifier}),
        (own['file'],{'name':'must-roll-back','usage':{'project_id':foreign['project'].identifier,'usage_kind':'upload'}}),
    ]
    for record,body in mutations:
        before=matrix.ok(side,'GET',record.path)
        response=matrix.request(side,'PATCH',record.path,json=body)
        assert response.status_code in {400,403,404,409},(record.path,response.status_code,response.text)
        assert matrix.ok(side,'GET',record.path)==before
        replacements={'project_id':own['project'].identifier,'chapter_id':own['chapter'].identifier,
            'scene_id':own['scene'].identifier,'actor_id':own['actor'].identifier,'costume_id':own['costume'].identifier,
            'prompt_template_id':own['prompt'].identifier,'provider_id':own['provider'].identifier,
            'usage':{'project_id':own['project'].identifier,'usage_kind':'upload','source_ref':'matrix-positive-usage'}}
        positive={key:replacements[key] if key in replacements else before[key] for key in body}
        matrix.ok(side,'PATCH',record.path,json=positive)
    before=matrix.ok(side,'GET','/studio/files?page_size=100')
    directory=matrix.database(side).parent/'files'
    paths=sorted(path.relative_to(directory).as_posix() for path in directory.rglob('*') if path.is_file())
    content=matrix.request(side,'GET',own['file'].path+'/download').content
    response=matrix.request(side,'POST','/studio/files/upload',data={'project_id':foreign['project'].identifier,'usage_kind':'upload'},files={'file':('rejected.png',content,'image/png')})
    assert response.status_code in {400,403,404,409},response.text
    assert matrix.ok(side,'GET','/studio/files?page_size=100')==before
    assert sorted(path.relative_to(directory).as_posix() for path in directory.rglob('*') if path.is_file())==paths
    # Test both a foreign child and a foreign parent on the actual association API.
    valid={'shot_id':own['shot'].identifier,'character_id':own['character'].identifier,'index':0}
    matrix.ok(side,'POST','/studio/shot-character-links',json=valid)
    for change in ({'character_id':foreign['character'].identifier},{'shot_id':foreign['shot'].identifier}):
        assert matrix.request(side,'POST','/studio/shot-character-links',json={**valid,**change}).status_code in {400,403,404,409}


@pytest.mark.parametrize('side',['a','b'])
def test_versioned_assets_media_drafts_and_structure_receipts(matrix,side):
    """History objects and their nested media require both the authenticated space and matching project."""
    other='b' if side=='a' else 'a'
    own,foreign=matrix.resources[side],matrix.resources[other]
    extra,other_extra=matrix.extras[side],matrix.extras[other]
    project=own['project'].identifier
    base='/studio/production/projects/'+project
    asset=base+'/assets/'+extra['production_asset']
    before=matrix.ok(side,'GET',asset)
    assert before['versions'][0]['payload']['media_ids']==[extra['production_media']]
    response=matrix.request(side,'POST',asset+'/versions',json={'expected_revision':1,'description':'rejected','media_ids':[other_extra['production_media']]})
    assert response.status_code==404,response.text
    assert matrix.ok(side,'GET',asset)==before
    matrix.ok(side,'POST',asset+'/versions',json={'expected_revision':1,'description':'second local revision','media_ids':[extra['production_media']]})
    for owner in (side,other):
        owner_project=matrix.resources[owner]['project'].identifier
        data=matrix.extras[owner]
        paths=['/studio/production/projects/'+owner_project+'/assets/'+data['production_asset'],
            '/studio/production/projects/'+owner_project+'/media/'+data['production_media']+'/content',
            '/editing/projects/'+owner_project+'/drafts/'+data['edit_draft'],
            '/studio/shots/graph/receipts/'+data['graph_receipt']+'?project_id='+owner_project]
        for path in paths:
            result=matrix.request(side,'GET',path)
            assert result.status_code==(200 if owner==side else 404),(path,result.status_code,result.text)
    for method,path,body in [
        ('PATCH',base+'/assets/'+other_extra['production_asset']+'/archive',{'archived':True}),
        ('POST',base+'/assets/'+other_extra['production_asset']+'/versions',{'expected_revision':1,'description':'rejected'}),
        ('PATCH','/editing/projects/'+project+'/drafts/'+other_extra['edit_draft'],{'expected_revision':1,'document':{'title':'rejected','clips':[]}}),
    ]:
        assert matrix.request(side,method,path,json=body).status_code==404
    assert matrix.request(side,'GET',base+'/media/'+other_extra['production_media']+'/content').status_code==404
    assert matrix.request(side,'GET','/studio/shots/graph/receipts/'+other_extra['graph_receipt']+'?project_id='+project).status_code==404


@pytest.mark.parametrize('side',['a','b'])
def test_task_history_controls_and_credentials_stay_in_the_original_space(matrix,side):
    """Use retained synthetic failed tasks for positive detail/cancel controls and secret-safe summaries."""
    other='b' if side=='a' else 'a'
    own,foreign=matrix.resources[side],matrix.resources[other]
    paths=['/agent-execution/runs/'+side+'-workflow','/agent-execution/runs/'+side+'-connection_test',
           '/asset-image-generation/jobs/'+side+'-image-job']
    foreign_paths=['/agent-execution/runs/'+other+'-workflow','/agent-execution/runs/'+other+'-connection_test',
                   '/asset-image-generation/jobs/'+other+'-image-job']
    for path,foreign_path in zip(paths,foreign_paths):
        row=matrix.ok(side,'GET',path)
        assert row['status']=='failed' and 'fixture-'+side+'-credential' not in str(row)
        assert matrix.ok(side,'POST',path+'/cancel')['status']=='failed'
        before=matrix.ok(other,'GET',foreign_path)
        for method,append in [('GET',''),('POST','/cancel')]:
            response=matrix.request(side,method,foreign_path+append)
            assert response.status_code==404,response.text
            assert 'private-'+other not in response.text
        assert matrix.ok(other,'GET',foreign_path)==before
    summary=matrix.ok(side,'GET','/workspace-tasks/recent')
    assert summary['complete'] and len(summary['items'])==3
    assert all(item['id'].split(':')[-1].startswith(side+'-') for item in summary['items'])
    assert 'fixture-' not in str(summary) and 'private-' not in str(summary) and 'example.invalid' not in str(summary)
    queries=[('/agent-execution/agents/'+own['agent'].identifier+'/runs','/agent-execution/agents/'+foreign['agent'].identifier+'/runs'),
        ('/agent-execution/models/'+own['model'].identifier+'/connection-tests','/agent-execution/models/'+foreign['model'].identifier+'/connection-tests')]
    for positive,negative in queries:
        assert len(matrix.ok(side,'GET',positive)['items'])==1
        assert matrix.ok(side,'GET',negative)['items']==[]
    # Gateway refusal here is asserted as a closed paid gate, not as successful execution isolation.
    for path in [foreign_paths[0]+'/retry',foreign_paths[2]+'/retry','/execution/callbacks']:
        assert matrix.request(side,'POST',path,json={}).status_code==503
    provider=matrix.ok(side,'GET',own['provider'].path)
    assert not {'api_key','api_secret'} & set(provider)
    with closing(sqlite3.connect(matrix.database(side))) as db:
        assert db.execute('SELECT api_key FROM providers WHERE id=?',(own['provider'].identifier,)).fetchone()==('fixture-'+side+'-credential',)


@pytest.mark.parametrize('side',['a','b'])
def test_owned_deletes_remove_only_new_positive_control_rows(matrix,side):
    """Create a new dependency tree, delete every resource family, and preserve the original two fixtures."""
    own=matrix.resources[side]
    prefix=side+'-disposable-'
    payloads=[
        ('project','/studio/projects',{'id':prefix+'project','name':'Disposable','style':'真人都市'}),
        ('chapter','/studio/chapters',{'id':prefix+'chapter','project_id':prefix+'project','index':1,'title':'Disposable'}),
        ('shot','/studio/shots',{'id':prefix+'shot','chapter_id':prefix+'chapter','index':1,'title':'Disposable'}),
        ('detail','/studio/shot-details',{'id':prefix+'shot','camera_shot':'MS','angle':'EYE_LEVEL','movement':'STATIC'}),
        *[(kind,'/studio/entities/'+kind,{'id':prefix+kind,'name':'Disposable','project_id':prefix+'project'}) for kind in ('actor','costume','scene','prop')],
        ('character','/studio/entities/character',{'id':prefix+'character','project_id':prefix+'project','name':'Disposable'}),
        ('prompt','/studio/prompts',{'category':'video_prompt','name':'Disposable','content':'Self-authored deletion control'}),
        ('agent','/agents',{'name':'Disposable','type':'plot'}),
        ('provider','/llm/providers',{'id':prefix+'provider','name':'OpenAI','base_url':'https://example.invalid/v1','status':'disabled'}),
        ('model','/llm/models',{'id':prefix+'model','name':'Disposable','provider_id':prefix+'provider','category':'text'}),
    ]
    created=[]
    baseline={name:matrix.ok(side,'GET',record.path) for name,record in own.items()}
    for family,path,body in payloads:
        record=matrix.ok(side,'POST',path,json=body)
        created.append(path+'/'+record['id'])
    png=matrix.request(side,'GET',own['file'].path+'/download').content
    uploaded=matrix.ok(side,'POST','/studio/files/upload',files={'file':('disposable.png',png,'image/png')})
    created.append('/studio/files/'+uploaded['id'])
    for path in reversed(created):
        assert matrix.request(side,'GET',path).status_code==200
        assert matrix.request(side,'DELETE',path).is_success
        assert matrix.request(side,'GET',path).status_code==404
    for name,record in own.items():
        assert matrix.ok(side,'GET',record.path)==baseline[name]


@pytest.mark.parametrize('side',['a','b'])
@pytest.mark.parametrize('version,prefix',[('v1','editing'),('v2','editing-v2')])
def test_retained_export_history_content_is_owned_and_verified(matrix,side,version,prefix):
    """Every ready artifact returns its exact real fixture bytes only within the correct space/project."""
    other='b' if side=='a' else 'a'
    own=matrix.resources[side]['project'].identifier
    foreign=matrix.resources[other]['project'].identifier
    identifier=matrix.extras[side]['export_'+version]
    other_id=matrix.extras[other]['export_'+version]
    path='/'+prefix+'/projects/'+own+'/exports/'+identifier
    row=matrix.ok(side,'GET',path)
    assert row['state']=='ready' and len(row['files'])==4
    catalog=matrix.ok(side,'GET','/'+prefix+'/projects/'+own+'/exports',params={'chapter_id':matrix.extras[side]['export_chapter']})
    assert [item['id'] for item in catalog['exports']]==[identifier]
    assert matrix.ok(side,'POST',path+'/cancel',json={})['state']=='ready'
    for artifact in row['files']:
        kind=artifact['kind']
        positive=matrix.request(side,'GET',path+'/content/'+kind)
        assert positive.status_code==200 and positive.content==matrix.export_bytes[(side,version)][kind]
        assert hashlib.sha256(positive.content).hexdigest()==artifact['sha256']
        for project,job in ((foreign,other_id),(own,other_id)):
            result=matrix.request(side,'GET','/'+prefix+'/projects/'+project+'/exports/'+job+'/content/'+kind)
            assert result.status_code==404 and 'Self-authored' not in result.text
    for project,job in ((foreign,other_id),(own,other_id)):
        url='/'+prefix+'/projects/'+project+'/exports/'+job
        assert matrix.request(side,'GET',url).status_code==404
        assert matrix.request(side,'POST',url+'/cancel',json={}).status_code==404
    assert matrix.ok(other,'GET','/'+prefix+'/projects/'+foreign+'/exports/'+other_id)['state']=='ready'
