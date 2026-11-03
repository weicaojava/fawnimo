"""Explicit local-model code proposals from current prepared public source, with original request recovery and no automatic candidate execution."""
from contextlib import closing
import hashlib
import json
import os
from pathlib import Path
import re
import sys
import time
from app.core.contracts.community_model_code import CommunityCodeModelConfiguration,CommunityCodeModelResult
from app.core.owned_process import run_owned_process
from app.services.community.advice_child import strict_json
from app.services.community.advice_store import digest,require_separate_roots
from app.services.community.change_bundles import protected_code_path,bytes_hash
from app.services.community.change_preparation import current_prepared_work,assemble_current_change
from app.services.community.development_model_child import validate_proposal
from app.services.community.development_model_protocol import change_input
from app.services.community.evaluations import source_row,validate_proof
from app.services.community.public_source import verify_public_issue,parse_public_json
from app.services.community.public_code import verify_public_code,CODE_PROTOCOL


def check_roots(preparation,changes,journal):
    """Bind all three independent roots to the same original public repository before any inference or new proposal write."""
    if preparation is None or changes is None:raise ValueError('Fresh proposal creation requires the original prepared source and change roots')
    intake=preparation.intake
    for left,right in ((intake.root,changes.root),(intake.root,journal.root),(changes.root,journal.root)):
        require_separate_roots(left,right)
    if (journal.identity['repository_id']!=intake.config.repository_id or journal.identity['repository_name']!=intake.config.repository_name.casefold()
            or changes.config.repository_id!=intake.config.repository_id or changes.config.repository_name.casefold()!=intake.config.repository_name.casefold()):
        raise ValueError('Local model, public preparation and change roots belong to different sources')


def code_context(request,prepared,source,observation):
    """Copy only complete selected regular public Python files, original hashes and the bounded original requirement/acceptance data."""
    snapshot=observation.snapshot
    files=[];total=0
    for path in request.paths:
        if protected_code_path(path):raise ValueError('Local model cannot propose controller changes')
        raw=snapshot.files.get(path)
        if path in request.test_files:
            if raw is not None:raise ValueError('Candidate tests must be explicitly new original paths')
            files.append({'path':path,'text':None,'before_sha256':None})
            continue
        if raw is None or len(raw)>32768:raise ValueError('Selected complete public source exceeds the small local-model profile')
        text=raw.decode('utf8');total+=len(raw)
        if '\x00' in text or total>32768:raise ValueError('Selected source is binary or exceeds the small local-model profile')
        files.append({'path':path,'text':text,'before_sha256':bytes_hash(raw)})
    entry=prepared['work']['entry']
    return {'scope':{'work_id':request.work_id,'version_id':entry['version_id'],'feature_ids':entry['feature_ids'],
        'acceptance':entry['acceptance'],'prepared_work_hash':digest(prepared['work']),'base_commit':snapshot.commit,'base_tree':snapshot.tree},
        'source':{'title':source['title'],'body_prefix':source['body'][:1024],'body_truncated':len(source['body'])>1024},
        'files':files,'paths':request.paths,'test_files':request.test_files}


def recover_original_bundle(changes,journal,request):
    """Link an already committed original inert bundle before source/current-policy gates, without creating another proposal or model request."""
    original=journal.replay(request)
    if original is None:raise ValueError('Original local code-model request is missing')
    if original['bundle'] is not None or original['model_result'] is None or original['model_result']['state']!='generated':return original
    if changes is None:return None
    if changes.config.repository_id!=journal.identity['repository_id'] or changes.config.repository_name.casefold()!=journal.identity['repository_name']:raise ValueError('Original change/model roots belong to different sources')
    require_separate_roots(changes.root,journal.root)
    saved=changes.replay(change_input(original['intent'],original['model_result']))
    return journal.attach_bundle(request,saved['artifact']) if saved is not None else None


async def materialize_original(preparation,changes,journal,request):
    """Recover generated text without another model POST, then save only a fresh-source bundle on the exact original public base/work."""
    original=journal.replay(request)
    if original is None:raise ValueError('Original local code-model request is missing')
    if original['bundle'] is not None or original['model_result'] is None or original['model_result']['state']!='generated':return original
    recovered=recover_original_bundle(changes,journal,request)
    if recovered is not None:return recovered
    check_roots(preparation,changes,journal)
    if any(protected_code_path(path) for path in request.paths):raise ValueError('Original generated text now targets a protected controller')
    body=change_input(original['intent'],original['model_result'])
    saved=await assemble_current_change(preparation,changes,body,
        expected_base_commit=original['intent']['provenance']['base_commit'],
        expected_work_hash=original['intent']['provenance']['prepared_work_hash'])
    return journal.attach_bundle(request,saved['artifact'])


async def generate_original(preparation,changes,journal,request,configuration_file,*,source_verifier=None,code_verifier=None,runner=None,clock_us=None):
    """Persist the exact original scope before one local model request; retry/reload resumes stored text/bundle and never regenerates an uncertain operation."""
    original=journal.replay(request)
    if original is not None:
        if original['bundle'] is not None or original['model_result'] is None or original['model_result']['state']!='generated':return original
        return await materialize_original(preparation,changes,journal,request)
    check_roots(preparation,changes,journal)
    if any(protected_code_path(path) for path in request.paths):raise ValueError('Local model cannot propose controller changes')
    config_path=Path(configuration_file)
    if config_path.is_symlink() or not config_path.is_file() or config_path.stat().st_size>8192:raise ValueError('Explicit separate local code-model configuration required')
    config=CommunityCodeModelConfiguration.model_validate(strict_json(config_path.read_bytes()))
    if not config.enabled or config.max_requests<1:raise ValueError('Local coding model is disabled or has no explicit request quota')
    intake=preparation.intake
    with closing(intake.connect()) as db:
        prepared=current_prepared_work(preparation,db,request.work_id)
        source=source_row(db,prepared['work']['entry']['version_id'])
    issue=await (source_verifier or verify_public_issue)(intake.config,source['issue_number'])
    code=await (code_verifier or verify_public_code)(intake.config,prepared['work']['entry']['feature_ids'],request.paths)
    now=(clock_us or (lambda:time.time_ns()//1000))()
    validate_proof(intake.config,source,issue,now)
    if (code.protocol!=CODE_PROTOCOL or type(code.checked_at_us) is not int or not 0<=now-code.checked_at_us<=60000000
            or code.snapshot.repository_id!=intake.config.repository_id or code.snapshot.repository_name.casefold()!=intake.config.repository_name.casefold()
            or re.fullmatch(r'[a-f0-9]{40}',code.snapshot.commit) is None):raise ValueError('Fresh original public code observation required')
    registry=parse_public_json(code.snapshot.files['capabilities/features.json'])
    if digest(registry)!=digest(preparation.queue.catalog()):raise ValueError('Public main and installed feature-register versions differ')
    context=code_context(request,prepared,source,code)
    frame={'configuration':config.model_dump(),'context':context}
    raw_input=json.dumps(frame,ensure_ascii=False,allow_nan=False).encode('utf8')
    if len(raw_input)>65536:raise ValueError('Complete local code-model frame exceeds its bounded profile')
    provenance={'base_commit':code.snapshot.commit,'base_tree':code.snapshot.tree,'prepared_work_hash':digest(prepared['work']),
        'issue':issue.receipt(),'code':code.receipt(),'feature_registry_sha256':hashlib.sha256(code.snapshot.files['capabilities/features.json']).hexdigest()}
    with closing(intake.connect()) as db:
        db.execute('BEGIN IMMEDIATE')
        current=current_prepared_work(preparation,db,request.work_id)
        validate_proof(intake.config,source_row(db,current['work']['entry']['version_id']),issue,(clock_us or (lambda:time.time_ns()//1000))())
        if digest(current['work'])!=provenance['prepared_work_hash']:raise ValueError('Original prepared work changed before local-model reservation')
        claimed,previous=journal.reserve(request,config,context,provenance)
        db.commit()
    if not claimed:return await materialize_original(preparation,changes,journal,request)
    allowed={'SYSTEMROOT','WINDIR','COMSPEC','PATH','PATHEXT','TEMP','TMP','TMPDIR','LANG','LC_ALL','TZ'}
    environment={key:value for key,value in os.environ.items() if key.upper() in allowed}
    environment.update(PYTHONUTF8='1',PYTHONDONTWRITEBYTECODE='1')
    try:
        raw,code_status=await (runner or run_owned_process)([sys.executable,str(Path(__file__).with_name('development_model_child.py'))],
            raw_input,timeout=40,max_stdout_bytes=65536,environment=environment)
        result=CommunityCodeModelResult.model_validate(strict_json(raw))
        if (code_status==0)!=(result.state=='generated'):raise ValueError('Local coding-model process/outcome mismatch')
        if result.state=='generated':
            validate_proposal(result.proposal.model_dump(),context)
            if result.model_digest!=config.model_digest:raise ValueError('Original local model digest changed')
    except Exception:
        result=CommunityCodeModelResult(state='uncertain',submitted=True,error='local_code_model_outcome_not_confirmed')
    journal.record_result(request,result.model_dump())
    return await materialize_original(preparation,changes,journal,request)
