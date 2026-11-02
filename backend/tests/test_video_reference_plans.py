"""Exercise actual chapter compilation, explicit source provenance and read-only metadata previews in disposable SQLite."""
from copy import deepcopy
from dataclasses import replace
import hashlib
from io import BytesIO

from fastapi import HTTPException
from PIL import Image
import pytest
from sqlalchemy import update

from app.core.contracts.seedance25 import MODEL_ID
from app.core.contracts.seedance_references import REFERENCE_PROTOCOL
from app.core.contracts.shot_plan import PlanCompile,PlanPatch
from app.config import settings
from app.models.production import AssetVersion,ProductionAsset,ProductionMedia
from app.models.studio import FileItem
from app.models.shot_plan import ShotPlan
from app.schemas.studio.production import VersionWrite
from app.services.shot_plan import compiler
from app.services.shot_plan.service import compile_draft,patch_draft,plan_view
from app.services.studio.production import add_version
from app.services.worker.workspace_video_runtime import preview_executable_plan
from tests.test_shot_plan import fixture
from tests.test_native_video_planned_accounting import planned_setup


async def images(factory,tmp_path):
    """Store two distinct authored images with actual project-owned file records and checksums."""
    rows=[]
    async with factory() as db:
        for identity,color in [('picked','navy'),('provenance','green')]:
            buffer=BytesIO();Image.new('RGB',(360,640),color).save(buffer,format='PNG');raw=buffer.getvalue()
            (tmp_path/(identity+'.png')).write_bytes(raw)
            db.add(FileItem(id=identity,type='image',name=identity,storage_key='local-production/'+identity+'.png'))
            await db.flush()
            row=ProductionMedia(id=identity,project_id='a',name=identity,mime='image/png',size=len(raw),sha256=hashlib.sha256(raw).hexdigest(),relative_path=identity+'.png')
            db.add(row);rows.append(row)
        await db.commit()
    return rows


def choice(row):
    """Explicitly choose the same original image for both independently named frame roles."""
    return {'schema_version':1,'mode':'first_last_frames','references':[{'media_id':row.id,'expected_sha256':row.sha256,'role':role} for role in ['first_frame','last_frame']]}


@pytest.mark.asyncio
async def test_legacy_documents_keep_hashes_when_new_optional_field_is_absent_or_null(tmp_path,monkeypatch):
    """A generated client adding the new null field cannot invalidate an already frozen old compilation."""
    engine,factory,_draft,value,runtime,_=await fixture(tmp_path,monkeypatch)
    try:
        async with factory() as db:
            original,_=await compiler.build_payload(db,'a','c1',value,runtime)
            added=deepcopy(value);added['shots'][0]['video_reference']=None
            current,_=await compiler.build_payload(db,'a','c1',added,runtime)
            assert current==original and current['compiler_version']=='m4b-v1'
            assert 'video_reference' not in current['document']['shots'][0] and 'reference_protocol' not in current['binding']
    finally:await engine.dispose()


@pytest.mark.asyncio
async def test_explicit_frames_do_not_send_other_version_media_and_preview_has_no_wire_secrets(tmp_path,monkeypatch):
    """Actual provenance includes an extra asset image, while ordered wire inputs contain only the two explicit roles."""
    engine,factory,draft,value,runtime,_=await fixture(tmp_path,monkeypatch)
    try:
        picked,source=await images(factory,tmp_path)
        async with factory() as db:
            old=await db.get(AssetVersion,value['shots'][0]['version_ids'][0]);asset=await db.get(ProductionAsset,old.asset_id)
            version=await add_version(db,asset,VersionWrite(expected_revision=1,description='authored provenance',media_ids=[source.id]));await db.commit()
        value['shots'][0]['version_ids'][0]=version.id
        value['shots'][0]['video_reference']=choice(picked);value['model_id']=MODEL_ID
        policy=next(iter(runtime.policies.values()))
        policy=replace(policy,capability=replace(policy.capability,model_id=MODEL_ID,reference_protocol=REFERENCE_PROTOCOL,reference_kinds=frozenset({'image'}),max_references=3))
        runtime.policies={MODEL_ID:policy}
        await patch_draft(factory,'a',draft,PlanPatch(expected_revision=1,document=value))
        plan_id=await compile_draft(factory,'a',draft,PlanCompile(expected_revision=2),runtime)
        async with factory() as db:
            stored=(await plan_view(db,'a',plan_id))['payload']
            assert stored['compiler_version']=='m4b-reference-v1'
            entry=stored['entries'][0]
            assert {row['id'] for row in entry['media']}=={picked.id,source.id}
            assert entry['request']['references']==[{'media_id':picked.id,'kind':'image'}]*2
            assert [item['role'] for item in entry['request']['video_reference']['references']]==['first_frame','last_frame']
            preview=await preview_executable_plan(db,'a',plan_id,runtime)
            assert preview['can_prepare'] and preview['wire_hash'] is None and len(preview['summary_hash'])==64
            assert preview['entries'][0]['body'] is None and preview['entries'][0]['input_validation']=='metadata_only'
            assert preview['entries'][0]['request_summary']['video_reference']==choice(picked)
            assert 'data:image/' not in str(preview) and '?token=' not in str(preview)
    finally:await engine.dispose()


@pytest.mark.asyncio
@pytest.mark.parametrize('wrong',['foreign','hash','mixed'])
async def test_reference_plan_rejects_foreign_stale_or_mixed_inputs_before_hashing(tmp_path,monkeypatch,wrong):
    """Scope, content and mutually exclusive old/new selections are checked before file access."""
    engine,factory,_draft,value,_runtime,_=await fixture(tmp_path,monkeypatch)
    try:
        picked,_=await images(factory,tmp_path);value['shots'][0]['video_reference']=choice(picked)
        if wrong=='foreign':
            async with factory() as db:await db.execute(update(ProductionMedia).where(ProductionMedia.id==picked.id).values(project_id='b'));await db.commit()
        elif wrong=='hash':
            for reference in value['shots'][0]['video_reference']['references']:reference['expected_sha256']='f'*64
        else:value['shots'][0]['reference_media_ids']=[picked.id]
        async with factory() as db:
            with pytest.raises(HTTPException) as error:await compiler.validate_document(db,'a','c1',value)
        assert error.value.status_code==422
    finally:await engine.dispose()


@pytest.mark.asyncio
async def test_reference_plan_bounds_asset_provenance_before_hashing_any_files(tmp_path,monkeypatch):
    """Even non-wire version media participates in the total source-byte ceiling before the first hash worker."""
    engine,factory,_draft,value,_runtime,_=await fixture(tmp_path,monkeypatch)
    try:
        picked,source=await images(factory,tmp_path);value['shots'][0]['video_reference']=choice(picked)
        async with factory() as db:
            old=await db.get(AssetVersion,value['shots'][0]['version_ids'][0]);asset=await db.get(ProductionAsset,old.asset_id)
            version=await add_version(db,asset,VersionWrite(expected_revision=1,description='bound all sources',media_ids=[source.id]))
            await db.execute(update(ProductionMedia).where(ProductionMedia.id==source.id).values(size=64*1024*1024));await db.commit()
        value['shots'][0]['version_ids'][0]=version.id
        async def forbidden_hash(*_args,**_kwargs):
            """Fail visibly if the compiler reaches byte hashing before its aggregate size check."""
            raise AssertionError('Byte hashes must follow aggregate bounds')
        monkeypatch.setattr(compiler,'media_evidence',forbidden_hash)
        async with factory() as db:
            with pytest.raises(HTTPException) as error:await compiler.build_payload(db,'a','c1',value)
        assert error.value.status_code==409 and '64MiB' in str(error.value.detail)
    finally:await engine.dispose()


@pytest.mark.asyncio
async def test_shared_reference_in_two_actual_shots_is_hashed_once_then_stat_rechecked(tmp_path,monkeypatch):
    """Repeated chapter references cannot multiply the bounded total source bytes into hundreds of full-file reads."""
    monkeypatch.setattr(settings,'production_media_root',str(tmp_path))
    values,selection=await planned_setup(tmp_path,count=2)
    engine,factory,_quote,_accountant,_worker,runtime,*_rest=values
    try:
        picked,_source=await images(factory,tmp_path)
        async with factory() as db:document=deepcopy((await db.get(ShotPlan,selection['plan_id'])).payload['document'])
        for shot in document['shots']:shot['video_reference']=choice(picked)
        policy=runtime.policies[MODEL_ID]
        runtime.policies={MODEL_ID:replace(policy,capability=replace(policy.capability,reference_protocol=REFERENCE_PROTOCOL,reference_kinds=frozenset({'image'}),max_references=3))}
        reads=[];actual_thread=compiler.asyncio.to_thread
        async def observe_read(operation,*args,**kwargs):
            """Count the actual byte-hash worker boundary without replacing file contents or media validation."""
            reads.append(True);return await actual_thread(operation,*args,**kwargs)
        monkeypatch.setattr(compiler.asyncio,'to_thread',observe_read)
        async with factory() as db:payload,proof=await compiler.build_payload(db,'a','c1',document,runtime)
        assert len(payload['entries'])==2 and list(proof)==[picked.id] and len(reads)==1
    finally:await engine.dispose()
