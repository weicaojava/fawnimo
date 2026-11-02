"""Finite queue planning tests use explicit authored evaluation/catalog facts, never AI or an execution grant."""
from copy import deepcopy
import pytest
from pydantic import ValidationError
from app.core.contracts.community_queue import CommunityQueuePlanInput,CommunityQueuePolicy
from app.services.community.queue_planning import build_queue_plan
from app.services.community.store import CommunityStoreConflict


def fixture():
    """Two registered features and three distinct reasonable original requirements permit deterministic ranking checks."""
    registry={'features':[{'id':'F-002.01','implementation':'implemented','validation':{'software':'verified'},'depends_on':[]},
        {'id':'F-002.02','implementation':'planned','validation':{'software':'not_verified'},'depends_on':['F-002.01']}]}
    contexts={key:{'decision':'reasonable','source_current':True,'feature_ids':['F-002.02'],'acceptance':['保留可核对的原请求。'],
        'request_id':'request-'+key,'version_id':'version-'+key,'evaluation_hash':'a'*64,'fingerprint':'b'*64,
        'evaluation_sequence':index+1,'evaluation_order_origin':'commit_trigger_v1'} for index,key in enumerate(('a','b','c'))}
    proposals=[{'key':key,'evaluation_id':key,'value_score':score,'value_reason':'明确的本地维护价值。','effort_minutes':effort} for key,score,effort in [('a',20,10),('b',30,10),('c',40,10)]]
    body={'request_key':'authored-plan-001','title':'本机路线图','proposals':proposals}
    return registry,contexts,body


def build(registry,contexts,body,**policy):
    """Run the actual strict-contract planner with bounded server-owned policy."""
    return build_queue_plan(CommunityQueuePlanInput.model_validate(body),CommunityQueuePolicy(**policy),registry,contexts)


def test_exact_value_effort_ranking_is_stable_across_input_order_and_never_grants_execution():
    """Value/effort order is reproducible and separate from both reasonableness and implementation/deployment status."""
    registry,contexts,body=fixture();plan=build(registry,contexts,body)
    assert [entry['key'] for entry in plan['entries']]==['c','b','a']
    body['proposals'].reverse();again=build(registry,contexts,body)
    assert plan['entries']==again['entries'] and plan['feature_catalog_hash']==again['feature_catalog_hash']
    assert not plan['execution_authorized'] and not plan['implementation_or_quality_qualified'] and plan['paid_authorization']==0


def test_topology_precedes_value_and_capacity_is_a_visible_blocker():
    """A valuable child waits behind its explicit parent; capacity deferral is not silently mistaken for acceptance."""
    registry,contexts,body=fixture();body['proposals'][2]['after']=['a']
    plan=build(registry,contexts,body,max_plan_effort_minutes=20)
    assert [entry['key'] for entry in plan['entries']]==['b','a','c']
    assert plan['selected_effort_minutes']==20 and plan['entries'][2]['blockers'][0]['code']=='planning_capacity'
    plan=build(registry,contexts,body)
    assert plan['entries'][2]['snapshot_status']=='waiting_predecessor'


@pytest.mark.parametrize('fault',['cycle','unknown','self'])
def test_invalid_predecessors_never_produce_an_executable_or_partial_queue(fault):
    """Invalid dependency graphs are rejected as a whole before any persistent queue write."""
    registry,contexts,body=fixture()
    if fault=='cycle':body['proposals'][0]['after']=['b'];body['proposals'][1]['after']=['a']
    elif fault=='unknown':body['proposals'][0]['after']=['unknown']
    else:body['proposals'][0]['after']=['a']
    with pytest.raises(CommunityStoreConflict):build(registry,contexts,body)


def test_positive_cost_and_unimplemented_feature_prerequisites_stay_blocked_and_propagate():
    """Zero authorization cannot be converted into a spending permission or satisfied by relabelling another queue result."""
    registry,contexts,body=fixture();registry['features'][0]['implementation']='partial'
    body['proposals'][0]['estimated_external_fee_fen']=1;body['proposals'][1]['after']=['a']
    plan=build(registry,contexts,body)
    assert plan['selected_effort_minutes']==0
    first=next(item for item in plan['entries'] if item['key']=='a')
    assert {item['code'] for item in first['blockers']}=={'external_cost_not_authorized','feature_prerequisites_incomplete'}
    assert any(item['code']=='predecessor_not_selected' for item in next(item for item in plan['entries'] if item['key']=='b')['blockers'])


def test_one_entry_cannot_hide_an_impossible_active_effort_estimate_inside_a_larger_plan_capacity():
    """A plan-level allowance must not mark a job ready when it exceeds the per-active-attempt operational capacity."""
    registry,contexts,body=fixture();body['proposals'][0]['effort_minutes']=61
    plan=build(registry,contexts,body,max_plan_effort_minutes=480,max_active_effort_minutes=60)
    item=next(row for row in plan['entries'] if row['key']=='a')
    assert not item['capacity_selected'] and item['blockers'][0]['code']=='single_attempt_effort_capacity'


def test_malformed_catalog_status_does_not_silently_qualify_a_dependency():
    """Unknown status or malformed feature data fails the whole proposed snapshot before persistence."""
    registry,contexts,body=fixture();registry['features'][0]['validation']['software']='looks_good'
    with pytest.raises(CommunityStoreConflict):build(registry,contexts,body)


@pytest.mark.parametrize('fault',['stale','not_reasonable','same_issue','unknown_feature','feature_cycle'])
def test_only_current_reasonable_distinct_original_requirements_and_a_valid_catalog_enter_plans(fault):
    """Neither unreviewed/stale input nor duplicate logical issues or forged feature dependencies can enter the roadmap."""
    registry,contexts,body=fixture()
    if fault=='stale':contexts['a']['source_current']=False
    elif fault=='not_reasonable':contexts['a']['decision']='needs_clarification'
    elif fault=='same_issue':contexts['a']['request_id']=contexts['b']['request_id']
    elif fault=='unknown_feature':contexts['a']['feature_ids']=['F-999.99']
    else:registry['features'][0]['depends_on']=['F-002.02']
    with pytest.raises(CommunityStoreConflict):build(registry,contexts,body)


@pytest.mark.parametrize('change',[{'max_concurrent':True},{'max_attempts':4},{'external_fee_limit_fen':1},
    {'lease_seconds':60,'attempt_timeout_seconds':10},{'command':'run'},{'max_active_effort_minutes':0}])
def test_policy_rejects_bool_coercion_over_limits_and_command_or_spending_expansion(change):
    """The policy has finite operational knobs and no arbitrary command, endpoint, model or positive-fee authorization."""
    with pytest.raises(ValidationError):CommunityQueuePolicy(**change)
