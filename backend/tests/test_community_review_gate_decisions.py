"""Authored numeric actors/CI/collector facts test gate software only; they never claim a real independent review or actual GitHub observation."""
from copy import deepcopy
from dataclasses import replace

import pytest
from pydantic import ValidationError

from app.core.contracts.community_review_gates import (CommunityReviewGatePolicy,CommunityPRGateInput,TrustedWorkflowRule,
    ExactPublicPRObservation,WORKFLOW_REQUIREMENTS)
from app.services.community.candidate_git import CandidateCommit
from app.services.community.change_bundles import build_change_bundle
from app.services.community.review_gate_decisions import LocalPRGateFacts,decide_pr_gate
from tests.test_community_change_bundles import prepared_change
from tests.test_community_queue_leases import factory
from tests.test_community_evaluations import NOW


@pytest.fixture
def gate(prepared_change):
    """Build a valid immutable inert artifact and explicitly authored source/identity/check receipts, without network, Docker or credentials."""
    original,prepared,snapshot,registry=prepared_change; artifact=build_change_bundle(original,prepared,snapshot,registry)
    candidate=CandidateCommit(artifact['bundle_id'],artifact['branch'],'c'*40,'d'*40,snapshot.commit,17,'authored/fawnimo')
    rules=tuple(TrustedWorkflowRule(path=path,blob_sha=str(index+1)*40) for index,path in enumerate(WORKFLOW_REQUIREMENTS))
    policy=CommunityReviewGatePolicy(repository_id=17,repository_name='Authored/Fawnimo',enabled=True,independent_reviewer_ids=(300,),
        developer_actor_ids=(200,),trusted_workflows=rules,trusted_check_image_ids=('sha256:'+'e'*64,),trusted_runner_sha256='1'*64)
    body=CommunityPRGateInput(request_key='authored-gate-001',bundle_id=artifact['bundle_id'],
        check_attempt_id='11111111-1111-4111-8111-111111111111',pr_number=7)
    check={'id':body.check_attempt_id,'bundle_id':body.bundle_id,'candidate_commit':candidate.commit,'candidate_tree':candidate.tree,
        'candidate_parent':candidate.parent,'outcome':'passed','current_phase':'terminal','active_reservation':False,'image_id':policy.trusted_check_image_ids[0],
        'container_id':'f'*64,'current_receipt':{'details':{'cleanup_verified':True,'outcome':'passed','container_id':'f'*64}},
        'history':[{'phase':'observed','receipt':{'details':{'outcome':'passed','process_exit_code':0,'supervisor_sha256':'1'*64,
            'output_sha256':'2'*64,'output_bytes':20,'kernel_limits_verified':True,'uid_separation_verified':True,'namespace_isolation_verified':True}}}]}
    facts=LocalPRGateFacts(candidate,check,'1'*64,{rule.path:{'base':rule.blob_sha,'candidate':rule.blob_sha} for rule in rules})
    pr={'number':7,'state':'open','draft':False,'merged':False,'locked':False,'author_id':200,'head_sha':candidate.commit,'head_ref':candidate.branch,
        'head_repository_id':17,'base_sha':candidate.parent,'base_ref':'main','base_repository_id':17,'commits':1,'mergeable':True,'merge_commit_sha':'e'*40}
    reviews=({'id':1,'user_id':300,'user_type':'User','state':'APPROVED','submitted_at_us':NOW-10,
        'author_association':'COLLABORATOR','commit_id':candidate.commit},)
    runs=[]
    for index,(path,jobs) in enumerate(WORKFLOW_REQUIREMENTS.items()):
        run_id=100+index; names=[]
        for job_index,(name,steps) in enumerate(jobs.items()):
            names.append({'id':1000+index*10+job_index,'name':name,'run_id':run_id,'run_attempt':1,'attempt_binding':'explicit_run_attempt_endpoint','status':'completed','conclusion':'success',
                'steps':[{'number':step_index+1,'name':step,'status':'completed','conclusion':'success'} for step_index,step in enumerate(steps)]})
        runs.append({'id':run_id,'workflow_id':10+index,'run_number':1,'run_attempt':1,'path':path,'repository_id':17,'head_repository_id':17,
            'head_sha':candidate.commit,'event':'pull_request','workflow_state':'active','status':'completed','conclusion':'success','jobs':names,
            'pull_request':{'number':7,'head_sha':candidate.commit,'base_sha':candidate.parent,'repository_id':17}})
    observed=ExactPublicPRObservation(17,'authored/fawnimo',NOW,candidate.parent,pr,
        {'sha':'e'*40,'tree':candidate.tree,'parents':[candidate.parent,candidate.commit]},reviews,tuple(runs),('a'*64,))
    return policy,body,artifact,facts,observed


def decide(values):
    """Use a fixed authored clock; test results say nothing about whether these fabricated actor/CI facts ever existed on GitHub."""
    return decide_pr_gate(*values,NOW)


def test_exact_current_independent_review_and_required_ci_are_observation_only(gate):
    """Even a complete eligible fixture does not return an executable merge/deploy capability or treat candidate tests as independent."""
    result=decide(gate)
    assert result['eligible_at_observation'] and result['blocking_reasons']==[]
    assert result['independent_reviews']['approvals']==[{'reviewer_id':300,'review_id':1,'commit':'c'*40}]
    assert all(item['passed'] for item in result['required_checks']['workflows'])
    assert not any(result[name] for name in ('promotion_authorized','merge_authorized','deployment_authorized','candidate_tests_are_independent'))


def test_default_disabled_policy_does_not_need_or_trust_a_candidate_supplied_observation(gate):
    """No configured independent identities means no eligibility; default disabled state requires no public network call."""
    _policy,body,artifact,facts,_observed=gate
    policy=CommunityReviewGatePolicy(repository_id=17,repository_name='authored/fawnimo')
    result=decide_pr_gate(policy,body,artifact,facts,None,NOW)
    assert not result['eligible_at_observation'] and result['blocking_reasons']==['review_gate_disabled']


@pytest.mark.parametrize('bad',['pr_author','developer','bot','unknown','outsider','old_commit','dismissed','changes_requested','pending','future_time','duplicate_review'])
def test_self_stale_untrusted_dismissed_or_blocking_reviews_never_count(gate,bad):
    """Numeric identity and current commit, not a login/comment/success string, determine whether an independent current approval exists."""
    policy,body,artifact,facts,observed=gate; reviews=deepcopy(list(observed.reviews)); pr=dict(observed.pull_request)
    if bad=='pr_author': pr['author_id']=300
    elif bad=='developer': reviews[0]['user_id']=200
    elif bad=='bot': reviews[0]['user_type']='Bot'
    elif bad=='unknown': reviews[0]['user_id']=400
    elif bad=='outsider': reviews[0]['author_association']='CONTRIBUTOR'
    elif bad=='old_commit': reviews[0]['commit_id']='b'*40
    elif bad=='dismissed': reviews[0]['state']='DISMISSED'
    elif bad=='changes_requested': reviews.append({**reviews[0],'id':2,'submitted_at_us':NOW-1,'state':'CHANGES_REQUESTED'})
    elif bad=='pending': reviews.append({**reviews[0],'id':2,'submitted_at_us':None,'state':'PENDING'})
    elif bad=='future_time': reviews[0]['submitted_at_us']=NOW+1
    else: reviews.append(dict(reviews[0]))
    assert not decide((policy,body,artifact,facts,replace(observed,pull_request=pr,reviews=tuple(reviews))))['eligible_at_observation']


def test_later_comment_does_not_erase_valid_approval_but_a_new_decision_does(gate):
    """A neutral comment is not an approval and does not reset a decisive review; a later dismissal/changes request removes eligibility."""
    policy,body,artifact,facts,observed=gate
    for state,expected in [('COMMENTED',True),('DISMISSED',False),('CHANGES_REQUESTED',False)]:
        reviews=observed.reviews+({**observed.reviews[0],'id':2,'submitted_at_us':NOW-1,'state':state},)
        assert decide((policy,body,artifact,facts,replace(observed,reviews=reviews)))['eligible_at_observation'] is expected


@pytest.mark.parametrize('bad',['head','base','fork','draft','closed','merged','merge_not_ready','merge_tree','merge_parents','main_changed',
    'too_old','future_observation','source_repo','response_hash','branch','commits'])
def test_wrong_or_stale_pr_source_and_tested_merge_cannot_reuse_success(gate,bad):
    """Only the real current same-repository PR and exact tested candidate tree/original parents may use its approval/CI receipts."""
    policy,body,artifact,facts,observed=gate; pr=dict(observed.pull_request); merge=deepcopy(observed.tested_merge)
    if bad=='head': pr['head_sha']='b'*40
    elif bad=='base': pr['base_sha']='b'*40
    elif bad=='fork': pr['head_repository_id']=18
    elif bad=='draft': pr['draft']=True
    elif bad=='closed': pr['state']='closed'
    elif bad=='merged': pr['merged']=True
    elif bad=='merge_not_ready': pr['mergeable']=None
    elif bad=='merge_tree': merge['tree']='b'*40
    elif bad=='merge_parents': merge['parents'].reverse()
    elif bad=='main_changed': observed=replace(observed,main_commit='b'*40)
    elif bad=='too_old': observed=replace(observed,checked_at_us=NOW-60000001)
    elif bad=='future_observation': observed=replace(observed,checked_at_us=NOW+1)
    elif bad=='source_repo': observed=replace(observed,repository_id=18)
    elif bad=='response_hash': observed=replace(observed,response_sha256=())
    elif bad=='branch': pr['head_ref']='unrelated'
    else: pr['commits']=2
    assert not decide((policy,body,artifact,facts,replace(observed,pull_request=pr,tested_merge=merge)))['eligible_at_observation']


@pytest.mark.parametrize('bad',['missing_workflow','old_head','push_event','wrong_pr','disabled','failed','skipped','pending','job_missing',
    'wrong_job_attempt','missing_attempt_binding','required_step_missing','required_step_skipped','duplicate_job','job_failed','newer_pending','rerun_failed'])
def test_old_failed_missing_or_newer_unresolved_ci_blocks_even_after_old_success(gate,bad):
    """A successful older result, job name or PR creation alone cannot satisfy the latest exact workflow/attempt and required executed steps."""
    policy,body,artifact,facts,observed=gate; runs=deepcopy(list(observed.workflows)); run=runs[0]
    if bad=='missing_workflow': runs.pop()
    elif bad=='old_head': run['head_sha']='b'*40
    elif bad=='push_event': run['event']='push'
    elif bad=='wrong_pr': run['pull_request']['number']=8
    elif bad=='disabled': run['workflow_state']='disabled_manually'
    elif bad=='failed': run['conclusion']='failure'
    elif bad=='skipped': run['conclusion']='skipped'
    elif bad=='pending': run.update(status='in_progress',conclusion=None)
    elif bad=='job_missing': run['jobs'].pop()
    elif bad=='wrong_job_attempt': run['jobs'][0]['run_attempt']=2
    elif bad=='missing_attempt_binding': run['jobs'][0].pop('attempt_binding')
    elif bad=='required_step_missing': run['jobs'][0]['steps'].pop()
    elif bad=='required_step_skipped': run['jobs'][0]['steps'][0]['conclusion']='skipped'
    elif bad=='duplicate_job': run['jobs'].append(deepcopy(run['jobs'][0]))
    elif bad=='job_failed': run['jobs'][0]['conclusion']='failure'
    else:
        newer=deepcopy(run)
        if bad=='newer_pending': newer.update(id=999,run_number=2,status='queued',conclusion=None,jobs=[])
        else:
            newer.update(run_attempt=2,conclusion='failure')
            for job in newer['jobs']: job['run_attempt']=2
        runs.append(newer)
    assert not decide((policy,body,artifact,facts,replace(observed,workflows=tuple(runs))))['eligible_at_observation']


def test_successful_new_attempt_requires_its_own_complete_jobs(gate):
    """A later fully successful rerun can resolve earlier failure, using its own exact attempt rather than borrowing old jobs."""
    policy,body,artifact,facts,observed=gate; runs=deepcopy(list(observed.workflows)); runs[0]['conclusion']='failure'
    newer=deepcopy(runs[0]); newer.update(run_attempt=2,conclusion='success')
    for job in newer['jobs']: job['run_attempt']=2
    runs.append(newer)
    assert decide((policy,body,artifact,facts,replace(observed,workflows=tuple(runs))))['eligible_at_observation']


def test_recreated_workflow_identity_cannot_hide_new_failure_behind_old_run_number(gate):
    """Workflow-local run numbers cannot order distinct workflow identities; an ambiguous recreated path must block rather than reuse old success."""
    policy,body,artifact,facts,observed=gate; runs=deepcopy(list(observed.workflows)); runs[0]['run_number']=50
    newer=deepcopy(runs[0]); newer.update(id=999,workflow_id=9001,run_number=1,conclusion='failure')
    for job in newer['jobs']: job['run_id']=999
    runs.append(newer)
    assert not decide((policy,body,artifact,facts,replace(observed,workflows=tuple(runs))))['eligible_at_observation']


@pytest.mark.parametrize('bad',['active','cleanup','unknown_container','image','runner','wrong_candidate','no_observation','output_overflow',
    'kernel','process_failure','workflow_changed'])
def test_current_trusted_local_check_and_immutable_workflow_policy_are_required(gate,bad):
    """A stopped/cleaned kernel check and pinned rules remain separate requirements, even if a reviewer and GitHub say success."""
    policy,body,artifact,facts,observed=gate; check=deepcopy(facts.check); blobs=deepcopy(facts.workflow_blobs); runner=facts.runner_sha256
    if bad=='active': check['active_reservation']=True
    elif bad=='cleanup': check['current_receipt']['details']['cleanup_verified']=False
    elif bad=='unknown_container': check['container_id']=None
    elif bad=='image': check['image_id']='sha256:'+'a'*64
    elif bad=='runner': runner='a'*64
    elif bad=='wrong_candidate': check['candidate_commit']='b'*40
    elif bad=='no_observation': check['history']=[]
    elif bad=='output_overflow': check['history'][0]['receipt']['details']['output_bytes']=65537
    elif bad=='kernel': check['history'][0]['receipt']['details']['kernel_limits_verified']=False
    elif bad=='process_failure': check['history'][0]['receipt']['details']['process_exit_code']=1
    else: next(iter(blobs.values()))['candidate']='a'*40
    assert not decide((policy,body,artifact,replace(facts,check=check,runner_sha256=runner,workflow_blobs=blobs),observed))['eligible_at_observation']


@pytest.mark.parametrize('extra',['reviewer_ids','approved','merge','workflow','url','token','command'])
def test_request_cannot_inject_reviewer_or_execution_authority(gate,extra):
    """Issue/candidate JSON can only name an original bundle/check/PR, never prescribe approval identities, network targets or commands."""
    value=gate[1].model_dump(); value[extra]=True
    with pytest.raises(ValidationError): CommunityPRGateInput.model_validate(value)


@pytest.mark.parametrize('bad',['self_policy','duplicate_reviewer','unknown_workflow','missing_image','missing_runner','bool_identity','expanded_limit'])
def test_enabled_policy_cannot_be_half_configured_or_self_approved(gate,bad):
    """An explicit immutable local policy needs concrete distinct identities and pinned check/CI authority; default absence never grants it."""
    value=gate[0].model_dump()
    if bad=='self_policy': value['independent_reviewer_ids']=[200]
    elif bad=='duplicate_reviewer': value['independent_reviewer_ids']=[300,300]
    elif bad=='unknown_workflow': value['trusted_workflows'][0]['path']='.github/workflows/untrusted.yml'
    elif bad=='missing_image': value['trusted_check_image_ids']=[]
    elif bad=='missing_runner': value['trusted_runner_sha256']=None
    elif bad=='bool_identity': value['independent_reviewer_ids']=[True]
    else: value['max_workflow_runs']=1000
    with pytest.raises(ValidationError): CommunityReviewGatePolicy.model_validate(value)
