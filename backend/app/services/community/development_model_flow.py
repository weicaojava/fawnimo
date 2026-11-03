"""Coordinate exact saved local-model proposals with existing local Git candidates and separately enabled isolated checks; never infer, publish or self-approve."""
import re
from app.core.contracts.community_code_checks import CommunityCodeCheckInput
from app.services.community.advice_store import require_separate_roots
from app.services.community.development_model_protocol import validate_bundle_binding
from app.services.community.change_preparation import commit_current_change
from app.services.community.code_check_runtime import run_current_check


def linked_artifact(changes,journal,request):
    """Resolve one original immutable model-to-bundle link and independently revalidate scope before any Git or executor access."""
    require_separate_roots(changes.root,journal.root)
    if (changes.config.repository_id!=journal.identity['repository_id']
            or changes.config.repository_name.casefold()!=journal.identity['repository_name']):
        raise ValueError('Original model and change roots belong to another repository')
    original=journal.replay(request)
    if original is None or original['bundle'] is None or original['model_result'] is None or original['model_result']['state']!='generated':
        raise ValueError('Exact saved model proposal/bundle link required before advancing')
    artifact=changes.get(original['bundle']['bundle_id'])
    validate_bundle_binding(original['intent'],original['model_result'],artifact)
    return original,artifact


def flow_receipt(request,artifact,stage,**values):
    """Expose exact provenance and narrow phase results without turning a check into AI quality, independent review or release approval."""
    return {**values,'profile':'local_model_candidate_flow_v1','model_request_key':request.request_key,'work_id':request.work_id,
        'bundle_id':artifact['bundle_id'],'bundle_scope_hash':artifact['bundle']['scope_hash'],'stage':stage,
        'new_model_requests':0,'github_writes_performed':False,'development_complete':False,
        'model_quality_qualified':False,'independent_review_completed':False,'public_deployment_verified':False,'external_fee_fen':0}


async def prepare_candidate(preparation,changes,journal,request,*,repository):
    """Recover or create only the original local candidate using the existing fresh-source/private-index/CAS controls."""
    _original,artifact=linked_artifact(changes,journal,request)
    if repository is None:raise ValueError('Explicit public Git repository required for a new local candidate')
    saved=repository.recover(artifact)
    if saved is not None:return flow_receipt(request,artifact,'local_candidate',candidate=saved.receipt(),replayed=True)
    result=await commit_current_change(preparation,changes,artifact['bundle_id'],repository=repository)
    return flow_receipt(request,artifact,'local_candidate',candidate=result['candidate'],replayed=result['replayed'])


async def check_candidate(preparation,changes,journal,request,checks,check_key,image_id,*,repository=None,boundary=None):
    """Reuse an exact original check before Git/source/daemon gates, or explicitly run the separately enabled existing Linux profile."""
    _original,artifact=linked_artifact(changes,journal,request)
    if checks.changes.root!=changes.root:raise ValueError('Original isolated-check and change roots differ')
    body=CommunityCodeCheckInput(request_key=check_key,bundle_id=artifact['bundle_id'])
    original=checks.replay(body,image_id)
    if original is not None:return flow_receipt(request,artifact,'isolated_check',check=original,replayed=True)
    if not isinstance(image_id,str) or re.fullmatch(r'sha256:[a-f0-9]{64}',image_id) is None:raise ValueError('Explicit immutable local image ID required before candidate advancement')
    await prepare_candidate(preparation,changes,journal,request,repository=repository)
    result=await run_current_check(preparation,checks,body,image_id,repository=repository,boundary=boundary)
    return flow_receipt(request,artifact,'isolated_check',check=result,replayed=bool(result.get('replayed',False)))
