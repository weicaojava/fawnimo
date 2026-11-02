"""Read-only fresh eligibility from independent GitHub identity and exact PR-head checks; no result authorizes merge, fees or deployment."""
from dataclasses import dataclass
import re

from app.core.contracts.community_review_gates import CommunityReviewGatePolicy,CommunityPRGateInput,ExactPublicPRObservation,WORKFLOW_REQUIREMENTS
from app.services.community.candidate_git import CandidateCommit
from app.services.community.change_bundles import verify_change_bundle
from app.services.community.evaluations import digest
from app.services.community.store import CommunityStoreConflict


@dataclass(frozen=True)
class LocalPRGateFacts:
    """Trusted local reads of the exact inert candidate, durable stopped/cleaned check, installed supervisor and committed workflow blobs."""
    candidate:CandidateCommit
    check:dict
    runner_sha256:str
    workflow_blobs:dict


def positive(value):
    """A JSON bool cannot impersonate a GitHub numeric actor, run, job or review identity."""
    return type(value) is int and 0 < value <= 9223372036854775807


def hex_id(value,length=40):
    """Only immutable lower-case Git/hash identities can participate in exact-version comparisons."""
    return isinstance(value,str) and re.fullmatch(r'[0-9a-f]{'+str(length)+'}',value) is not None


def gate_flags():
    """An observed eligible state is diagnostic only; cached receipts and candidate tests do not become continuing review/merge authority."""
    return {'profile':'github_exact_pr_review_v1','promotion_authorized':False,'merge_authorized':False,'deployment_authorized':False,
        'candidate_tests_are_independent':False,'external_model_calls':0,'paid_authorization':0}


def validate_local_check(body,artifact,facts,policy):
    """Require exact candidate and kernel-verified completed check using a configured trusted image/current supervisor, with cleanup already confirmed."""
    payload=verify_change_bundle(artifact); scope=payload['scope']; candidate=facts.candidate; check=facts.check
    if (not isinstance(candidate,CandidateCommit) or candidate.bundle_id!=body.bundle_id or artifact['bundle_id']!=body.bundle_id
            or candidate.branch!=artifact['branch'] or candidate.parent!=scope['base_commit'] or not hex_id(candidate.commit) or not hex_id(candidate.tree)
            or candidate.repository_id!=policy.repository_id or candidate.repository_name.casefold()!=policy.repository_name
            or scope['repository_id']!=policy.repository_id or scope['repository_name']!=policy.repository_name):
        raise CommunityStoreConflict('Gate candidate differs from its original public bundle')
    expected={'id':body.check_attempt_id,'bundle_id':body.bundle_id,'candidate_commit':candidate.commit,'candidate_tree':candidate.tree,
        'candidate_parent':candidate.parent,'outcome':'passed','current_phase':'terminal','active_reservation':False}
    if not isinstance(check,dict) or any(check.get(name)!=value for name,value in expected.items()) or check.get('active_reservation') is not False:
        raise CommunityStoreConflict('Exact passed candidate check and terminal cleanup required')
    if check.get('image_id') not in policy.trusted_check_image_ids or facts.runner_sha256!=policy.trusted_runner_sha256:
        raise CommunityStoreConflict('Check image/supervisor is outside configured trusted policy')
    terminal=check.get('current_receipt',{}).get('details',{})
    if terminal.get('cleanup_verified') is not True or terminal.get('outcome')!='passed' or not hex_id(check.get('container_id'),64) or terminal.get('container_id')!=check.get('container_id'):
        raise CommunityStoreConflict('Owned stopped resource cleanup was not confirmed')
    observed=[item['receipt']['details'] for item in check.get('history',[]) if item.get('phase')=='observed']
    if len(observed)!=1: raise CommunityStoreConflict('One original trusted supervisor observation required')
    value=observed[0]
    if (value.get('outcome')!='passed' or type(value.get('process_exit_code')) is not int or value['process_exit_code']!=0
            or value.get('supervisor_sha256')!=facts.runner_sha256 or not hex_id(value.get('output_sha256'),64)
            or type(value.get('output_bytes')) is not int or not 0<=value['output_bytes']<=65536
            or any(value.get(name) is not True for name in ('kernel_limits_verified','uid_separation_verified','namespace_isolation_verified'))):
        raise CommunityStoreConflict('Current trusted supervisor/kernel check required')
    if set(facts.workflow_blobs)!=set(WORKFLOW_REQUIREMENTS): raise CommunityStoreConflict('Both complete trusted workflow blobs required')
    for rule in policy.trusted_workflows:
        pair=facts.workflow_blobs[rule.path]
        if not isinstance(pair,dict) or set(pair)!={'base','candidate'} or pair['base']!=rule.blob_sha or pair['candidate']!=rule.blob_sha:
            raise CommunityStoreConflict('A candidate cannot change or replace configured CI rules')
    return scope


def review_summary(policy,observation,head,author_id):
    """Resolve each allowed human's latest decisive review, rejecting self/developer, stale, dismissed or in-progress approvals."""
    allowed=set(policy.independent_reviewer_ids)-set(policy.developer_actor_ids)-{author_id}
    latest={}; pending=set(); seen=set(); valid_states={'APPROVED','CHANGES_REQUESTED','COMMENTED','DISMISSED','PENDING'}
    if len(observation.reviews)>policy.max_reviews: raise CommunityStoreConflict('Complete bounded review set required')
    for review in observation.reviews:
        identifier=review.get('id'); user=review.get('user_id'); state=review.get('state'); submitted=review.get('submitted_at_us')
        if (not positive(identifier) or identifier in seen or not positive(user) or state not in valid_states
                or review.get('user_type') not in {'User','Bot','Organization'} or not hex_id(review.get('commit_id'))):
            raise CommunityStoreConflict('Ambiguous public review metadata')
        seen.add(identifier)
        if state=='PENDING':
            if user in allowed: pending.add(user)
            continue
        if type(submitted) is not int or not 0<=submitted<=observation.checked_at_us: raise CommunityStoreConflict('Review submission clock is not verified')
        if (user not in allowed or review['user_type']!='User' or review.get('author_association') not in {'OWNER','MEMBER','COLLABORATOR'}): continue
        if state=='COMMENTED': continue
        order=(submitted,identifier)
        if user not in latest or order>latest[user][0]: latest[user]=(order,review)
    approvals=[]; blockers=[]
    for user,(_order,review) in sorted(latest.items()):
        if review['state']=='CHANGES_REQUESTED': blockers.append({'reviewer_id':user,'review_id':review['id'],'reason':'changes_requested'})
        elif review['state']=='APPROVED' and review['commit_id']==head and user not in pending:
            approvals.append({'reviewer_id':user,'review_id':review['id'],'commit':head})
    if pending: blockers.append({'reason':'independent_review_pending'})
    return {'approvals':approvals,'blocking_reviews':blockers,'approved':len(approvals)>=policy.required_approvals and not blockers}


def workflow_summary(policy,observation,body,head,base):
    """Use the latest run/attempt per pinned workflow; skipped/pending/failed/newer runs, stale heads and incomplete jobs all block eligibility."""
    if len(observation.workflows)>policy.max_workflow_runs: raise CommunityStoreConflict('Complete bounded workflow set required')
    latest={}; seen=set(); workflow_ids={}
    for run in observation.workflows:
        path=run.get('path')
        if path not in WORKFLOW_REQUIREMENTS: continue
        if path in workflow_ids and workflow_ids[path]!=run.get('workflow_id'):
            raise CommunityStoreConflict('Pinned workflow path has mixed identities; workflow-local run numbers cannot order them')
        workflow_ids[path]=run.get('workflow_id')
        identity=(run.get('id'),run.get('run_attempt'))
        if (not all(positive(run.get(name)) for name in ('id','workflow_id','run_number','run_attempt')) or identity in seen
                or run.get('repository_id')!=policy.repository_id or type(run.get('repository_id')) is not int
                or run.get('head_repository_id')!=policy.repository_id or type(run.get('head_repository_id')) is not int
                or run.get('head_sha')!=head or run.get('event')!='pull_request' or run.get('workflow_state')!='active'
                or run.get('pull_request')!={'number':body.pr_number,'head_sha':head,'base_sha':base,'repository_id':policy.repository_id}):
            raise CommunityStoreConflict('Workflow event/source/PR identity differs from exact candidate')
        seen.add(identity); order=(run['run_number'],run['run_attempt'],run['id'])
        if path not in latest or order>latest[path][0]: latest[path]=(order,run)
    results=[]
    for path,requirements in WORKFLOW_REQUIREMENTS.items():
        selected=latest.get(path)
        if selected is None:
            results.append({'path':path,'passed':False,'reason':'required_workflow_missing'}); continue
        run=selected[1]; passed=run.get('status')=='completed' and run.get('conclusion')=='success'; jobs=run.get('jobs')
        if not isinstance(jobs,(list,tuple)) or not 1<=len(jobs)<=policy.max_jobs: raise CommunityStoreConflict('Complete latest-attempt jobs required')
        names={}; job_ids=set()
        for job in jobs:
            if (not positive(job.get('id')) or job['id'] in job_ids or not isinstance(job.get('name'),str) or job['name'] in names
                    or job.get('run_id')!=run['id'] or type(job.get('run_id')) is not int or job.get('run_attempt')!=run['run_attempt']
                    or type(job.get('run_attempt')) is not int or job.get('attempt_binding')!='explicit_run_attempt_endpoint'):
                raise CommunityStoreConflict('Job belongs to another workflow attempt or is ambiguous')
            names[job['name']]=job; job_ids.add(job['id'])
            if job.get('status')!='completed' or job.get('conclusion')!='success': passed=False
        for name,required_steps in requirements.items():
            job=names.get(name)
            if job is None: passed=False; continue
            steps=job.get('steps')
            if not isinstance(steps,(list,tuple)) or len(steps)>64: raise CommunityStoreConflict('Bounded actual job steps required')
            step_names={}; numbers=set()
            for step in steps:
                if (not positive(step.get('number')) or step['number'] in numbers or not isinstance(step.get('name'),str)
                        or step['name'] in step_names): raise CommunityStoreConflict('Ambiguous workflow step identity')
                numbers.add(step['number']); step_names[step['name']]=step
            if any(step not in step_names or step_names[step].get('status')!='completed' or step_names[step].get('conclusion')!='success' for step in required_steps): passed=False
        results.append({'path':path,'run_id':run['id'],'run_attempt':run['run_attempt'],'head_sha':head,'passed':passed,
            'reason':'exact_required_checks_passed' if passed else 'required_workflow_not_successful'})
    return {'workflows':results,'passed':all(item['passed'] for item in results)}


def decide_pr_gate(policy,body,artifact,facts,observation,now_us):
    """Produce bounded observation-time eligibility only; future publishers must gather a new source/head/review/CI observation before any action."""
    if type(policy) is not CommunityReviewGatePolicy or type(body) is not CommunityPRGateInput:
        raise CommunityStoreConflict('Strict server policy, request and trusted local facts required')
    policy=CommunityReviewGatePolicy.model_validate(policy.model_dump()); body=CommunityPRGateInput.model_validate(body.model_dump())
    result={'request_key':body.request_key,'bundle_id':body.bundle_id,'check_attempt_id':body.check_attempt_id,'pr_number':body.pr_number,
        'policy_hash':digest(policy.model_dump()),'eligible_at_observation':False,'blocking_reasons':[],**gate_flags()}
    if not policy.enabled:
        result['blocking_reasons']=['review_gate_disabled']; return result
    if not isinstance(facts,LocalPRGateFacts): raise CommunityStoreConflict('Trusted exact local gate facts required')
    try:
        scope=validate_local_check(body,artifact,facts,policy)
        if (not isinstance(observation,ExactPublicPRObservation) or observation.protocol!='github-public-pr-rest-2022-11-28-v1'
                or type(observation.repository_id) is not int or observation.repository_id!=policy.repository_id
                or observation.repository_name.casefold()!=policy.repository_name or type(observation.checked_at_us) is not int
                or type(now_us) is not int or not 0<=now_us-observation.checked_at_us<=policy.observation_seconds*1000000
                or not 1<=len(observation.response_sha256)<=32 or any(not hex_id(value,64) for value in observation.response_sha256)):
            raise CommunityStoreConflict('Fresh complete fixed public PR observation required')
        pr=observation.pull_request; head=facts.candidate.commit; base=scope['base_commit']; tree=facts.candidate.tree
        if (pr.get('number')!=body.pr_number or type(pr.get('number')) is not int or pr.get('state')!='open' or pr.get('draft') is not False
                or pr.get('merged') is not False or pr.get('locked') is not False or not positive(pr.get('author_id'))
                or pr.get('head_sha')!=head or pr.get('head_ref')!=facts.candidate.branch or pr.get('head_repository_id')!=policy.repository_id
                or type(pr.get('head_repository_id')) is not int or pr.get('base_sha')!=base or pr.get('base_ref')!='main'
                or pr.get('base_repository_id')!=policy.repository_id or type(pr.get('base_repository_id')) is not int
                or pr.get('commits')!=1 or type(pr.get('commits')) is not int or observation.main_commit!=base
                or pr.get('mergeable') is not True): raise CommunityStoreConflict('PR is draft/closed/stale/forked or differs from original exact candidate')
        merge=observation.tested_merge
        if (not isinstance(merge,dict) or not hex_id(merge.get('sha')) or pr.get('merge_commit_sha')!=merge['sha']
                or merge.get('tree')!=tree or merge.get('parents')!=[base,head]):
            raise CommunityStoreConflict('Pinned pull-request checkout must test the exact candidate tree with original base/head parents')
        reviews=review_summary(policy,observation,head,pr['author_id'])
        workflows=workflow_summary(policy,observation,body,head,base)
        result.update(candidate_commit=head,candidate_tree=tree,base_commit=base,tested_merge_commit=merge['sha'],
            checked_at_us=observation.checked_at_us,source_response_sha256=list(observation.response_sha256),
            independent_reviews=reviews,required_checks=workflows)
        if not reviews['approved']: result['blocking_reasons'].append('current_independent_approval_missing_or_blocked')
        if not workflows['passed']: result['blocking_reasons'].append('exact_latest_required_checks_not_passed')
        result['eligible_at_observation']=not result['blocking_reasons']
        return result
    except (CommunityStoreConflict,ValueError,TypeError,KeyError,AttributeError,IndexError):
        result['blocking_reasons']=['exact_source_review_or_ci_proof_not_verified']; return result
