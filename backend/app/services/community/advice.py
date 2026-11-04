"""Source-bound local-model advice orchestration, separate from maintainer decisions, user projects and paid generation."""
from contextlib import closing
import hashlib
import json
import os
from pathlib import Path
import re
import sys
import time

from app.core.contracts.community_advice import CommunityAdviceConfiguration
from app.core.owned_process import run_owned_process
from app.services.community.advice_child import strict_json, validate_fresh_advice
from app.services.community.advice_store import require_separate_roots
from app.services.community.evaluations import source_row, validate_proof, duplicate_suggestions
from app.services.community.public_source import verify_public_issue


def original_context(intake, version_id):
    """Read only the selected public requirement, bounded duplicate suggestions and registered feature titles."""
    with closing(intake.connect()) as db:
        row = source_row(db,version_id)
        suggestions = duplicate_suggestions(db,row)
    path = Path(__file__).resolve().parents[4]/'capabilities/features.json'
    raw = path.read_bytes()
    if len(raw) > 2*1024*1024:
        raise ValueError('Feature registry exceeds the local advice profile')
    registry = json.loads(raw)
    features = [{'id':feature['id'],'title':feature['title']} for feature in registry['features']]
    if not 1 <= len(features) <= 256 or len({feature['id'] for feature in features}) != len(features):
        raise ValueError('Invalid feature registry for local advice')
    context = {'version_id':row['version_id'],'fingerprint':row['fingerprint'],'title':row['title'],
        'body_prefix':row['body'][:4096],'body_truncated':len(row['body']) > 4096,
        'feature_catalog':features,'duplicates':suggestions['matches']}
    return row,context,hashlib.sha256(raw).hexdigest()


def current_source(intake, request):
    """Report local source currency separately from immutable model advice and its original public observation."""
    try:
        with closing(intake.connect()) as db:
            return source_row(db,request.version_id,eligible=False)['source_current']
    except (ValueError,KeyError):
        return False


def response_receipt(value, code, context, config):
    """Revalidate the owned child's fixed response instead of trusting a model or process exit code as application authority."""
    if not isinstance(value,dict) or value.get('state') not in {'ready','not_submitted','uncertain'} or type(value.get('submitted')) is not bool:
        raise ValueError('Invalid local advice child response')
    if value['state'] == 'ready':
        if code != 0 or not value['submitted'] or set(value) != {'state','submitted','advice','usage','model_digest','response_sha256'}:
            raise ValueError('Invalid completed local advice response')
        advice = validate_fresh_advice(value['advice'],context)
        if value['model_digest'] != config.model_digest or re.fullmatch(r'[a-f0-9]{64}',value['response_sha256']) is None:
            raise ValueError('Original local model response identity changed')
        if set(value['usage']) != {'prompt_eval_count','eval_count'} or any(type(count) is not int or not 0 <= count <= 1000000 for count in value['usage'].values()):
            raise ValueError('Invalid local model usage counts')
        return {**value,'advice':advice.model_dump()}
    if code == 0 or set(value) != {'state','submitted','error'} or value['error'] != 'local_advice_not_completed' or (value['state'] == 'uncertain') != value['submitted']:
        raise ValueError('Invalid failed local advice response')
    return value


async def create_advice(intake, journal, request, configuration_file, *, source_verifier=None, runner=None, clock_us=None):
    """Persist one original intent before one inference; replay never rechecks configuration or resends an ambiguous local POST."""
    if journal.identity['repository_id'] != intake.config.repository_id or journal.identity['repository_name'] != intake.config.repository_name.casefold():
        raise ValueError('Advice journal belongs to another original repository')
    require_separate_roots(intake.root,journal.root)
    previous = journal.replay(request)
    if previous is not None:
        return {**previous,'source_current':current_source(intake,request)}
    path = Path(configuration_file)
    if path.is_symlink() or not path.is_file() or path.stat().st_size > 8192:
        raise ValueError('Explicit private local-model configuration is required')
    config = CommunityAdviceConfiguration.model_validate(strict_json(path.read_bytes()))
    if not config.enabled or config.max_requests < 1:
        raise ValueError('Local model advice has not been explicitly enabled')
    row,_,_ = original_context(intake,request.version_id)
    proof = await (source_verifier or verify_public_issue)(intake.config,row['issue_number'])
    row,context,registry_sha = original_context(intake,request.version_id)
    now = (clock_us or (lambda:time.time_ns()//1000))()
    validate_proof(intake.config,row,proof,now)
    frame = {'configuration':config.model_dump(),'context':context}
    content = json.dumps(frame,ensure_ascii=False,allow_nan=False).encode('utf8')
    if len(content) > 65536:
        raise ValueError('Original advice frame exceeds its bounded profile')
    observation = {**proof.receipt(),'version_id':request.version_id,'feature_registry_sha256':registry_sha}
    with closing(intake.connect()) as db:
        # Keep the final source validation and the short journal reservation under
        # one original-source write lock. No network or inference runs here.
        db.execute('BEGIN IMMEDIATE')
        current = source_row(db,request.version_id)
        validate_proof(intake.config,current,proof,(clock_us or (lambda:time.time_ns()//1000))())
        if current['fingerprint'] != context['fingerprint']:
            raise ValueError('Original public requirement changed before advice reservation')
        claimed,original = journal.reserve(request,config,context,observation)
        db.commit()
    if not claimed:
        return {**original,'source_current':current_source(intake,request)}
    allowed = {'SYSTEMROOT','WINDIR','COMSPEC','PATH','PATHEXT','TEMP','TMP','TMPDIR','LANG','LC_ALL','TZ'}
    environment = {key:value for key,value in os.environ.items() if key.upper() in allowed}
    environment.update(PYTHONUTF8='1',PYTHONDONTWRITEBYTECODE='1')
    try:
        raw,code = await (runner or run_owned_process)([sys.executable,str(Path(__file__).with_name('advice_child.py'))],
            content,timeout=40,max_stdout_bytes=32768,environment=environment)
        receipt = response_receipt(strict_json(raw),code,context,config)
    except Exception:
        receipt = {'state':'uncertain','submitted':True,'error':'local_advice_outcome_not_confirmed'}
    receipt.update(execution_authorized=False,external_fee_fen=0,model_quality_qualified=False,
        independent_review_completed=False,public_feedback_sent=False,
        source_current_at_completion=current_source(intake,request),requires_fresh_source_for_maintainer_decision=True)
    result = journal.finish(request,receipt)
    return {**result,'source_current':current_source(intake,request)}
