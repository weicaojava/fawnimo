"""Bind one saved local-model/check chain to separately enabled draft publication and read-only review; never infer, approve, merge or deploy."""
from app.core.contracts.community_code_checks import CommunityCodeCheckInput
from app.core.contracts.community_publication import CommunityPublicationInput
from app.core.contracts.community_review_gates import CommunityPRGateInput
from app.services.community.advice_store import require_separate_roots
from app.services.community.development_model_flow import linked_artifact
from app.services.community.publication_admission import publish_current_candidate
from app.services.community.review_gate_admission import observe_current_pr_gate


def publication_input(changes,journal,request,checks,publications,check_key,image_id,publication_key):
    """Derive server-owned bundle/check association from exact saved originals, checking repository and separate model/publication roots before remote work."""
    _original,artifact=linked_artifact(changes,journal,request)
    if checks.changes.root!=changes.root:raise ValueError('Original model and isolated-check change roots differ')
    require_separate_roots(journal.root,publications.root)
    require_separate_roots(changes.root,publications.root)
    if (publications.policy.repository_id!=journal.identity['repository_id']
            or publications.policy.repository_name.casefold()!=journal.identity['repository_name']):
        raise ValueError('Original model and draft publication repositories differ')
    check=checks.replay(CommunityCodeCheckInput(request_key=check_key,bundle_id=artifact['bundle_id']),image_id)
    if check is None:raise ValueError('Exact original isolated check required before draft publication')
    body=CommunityPublicationInput(request_key=publication_key,bundle_id=artifact['bundle_id'],check_attempt_id=check['id'])
    return artifact,body


def review_input(changes,journal,request,checks,publications,gates,check_key,image_id,publication_key,observation_key,*,allow_history=False):
    """Use a positively recorded original PR identity; allow old identity lookup solely for replay before applying latest-confirmation gates to a new read."""
    artifact,body=publication_input(changes,journal,request,checks,publications,check_key,image_id,publication_key)
    require_separate_roots(journal.root,gates.root)
    require_separate_roots(changes.root,gates.root)
    require_separate_roots(publications.root,gates.root)
    if (gates.policy.repository_id!=journal.identity['repository_id']
            or gates.policy.repository_name.casefold()!=journal.identity['repository_name']):
        raise ValueError('Original model and review repositories differ')
    saved=publications.replay(body)
    if saved is None:raise ValueError('Original draft publication required before observing review')
    confirmed=[event for event in saved['history'] if (event['phase']=='published'
        or event['phase']=='reconciled' and event['details'].get('reason')=='original_exact_draft_observed')
        and type(event['details'].get('pr_number')) is int and 1<=event['details']['pr_number']<=2147483647]
    if not confirmed or not allow_history and confirmed[-1]!=saved['history'][-1]:
        raise ValueError('Original draft publication remains unconfirmed; reconcile its exact record before review')
    number=confirmed[-1]['details']['pr_number']
    return artifact,CommunityPRGateInput(request_key=observation_key,bundle_id=body.bundle_id,check_attempt_id=body.check_attempt_id,pr_number=number)


def receipt(request,artifact,stage,result,*,replayed):
    """Describe a narrowly scoped original operation; remote records and eligibility observations never establish continuous approval or release authority."""
    return {'profile':'local_model_delivery_flow_v1','model_request_key':request.request_key,'work_id':request.work_id,
        'bundle_id':artifact['bundle_id'],'stage':stage,'result':result,'replayed':replayed,'new_model_requests':0,
        'model_quality_qualified':False,'independent_review_completed':False,'merge_authorized':False,
        'deployment_authorized':False,'public_deployment_verified':False,'overall_goal_complete':False,
        'external_model_calls':0,'paid_authorization':0}


async def publish_model_candidate(preparation,changes,journal,request,checks,publications,check_key,image_id,publication_key,*,repository=None,remote_factory=None):
    """Recover an exact original publication before source/Git/credentials, or explicitly call the existing default-closed current-source draft publisher."""
    artifact,body=publication_input(changes,journal,request,checks,publications,check_key,image_id,publication_key)
    old=publications.replay(body)
    if old is not None:return receipt(request,artifact,'draft_publication',old,replayed=True)
    if not publications.policy.enabled:raise ValueError('Remote publication is disabled')
    if repository is None or remote_factory is None:raise ValueError('Explicit public clone and dedicated trusted transport required')
    if preparation is not None:require_separate_roots(journal.root,preparation.intake.root)
    result=await publish_current_candidate(preparation,checks,publications,body,repository=repository,remote_factory=remote_factory)
    return receipt(request,artifact,'draft_publication',result,replayed=False)


async def observe_model_review(preparation,changes,journal,request,checks,publications,gates,check_key,image_id,publication_key,observation_key,*,repository=None):
    """Recover original review history without fresh reads, or observe the saved exact draft PR under the existing separate independent-review/CI policy."""
    artifact,body=review_input(changes,journal,request,checks,publications,gates,check_key,image_id,publication_key,observation_key,allow_history=True)
    old=gates.replay(body)
    if old is not None:return receipt(request,artifact,'review_observation',old,replayed=True)
    review_input(changes,journal,request,checks,publications,gates,check_key,image_id,publication_key,observation_key)
    if gates.policy.enabled and repository is None:raise ValueError('Explicit original public clone required for a new enabled review observation')
    if preparation is not None:
        require_separate_roots(journal.root,preparation.intake.root)
        require_separate_roots(publications.root,preparation.intake.root)
    result=await observe_current_pr_gate(preparation,checks,gates,body,repository=repository)
    return receipt(request,artifact,'review_observation',result,replayed=False)
