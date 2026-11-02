"""Deterministic bounded dependency/value/cost/capacity planning; a roadmap never authorizes development or deployment."""
from fractions import Fraction
import hashlib
import heapq
import re
from app.core.contracts.community_queue import CommunityQueuePlanInput,CommunityQueuePolicy
from app.services.community.evaluations import canonical
from app.services.community.store import CommunityStoreConflict


def catalog_features(registry):
    """Validate a frozen feature graph before using it as prerequisite evidence, without changing any feature's status."""
    if not isinstance(registry,dict) or not isinstance(registry.get('features'),list) or not 1<=len(registry['features'])<=512:
        raise CommunityStoreConflict('Invalid frozen feature catalog')
    for item in registry['features']:
        if (not isinstance(item,dict) or not isinstance(item.get('id'),str) or re.fullmatch(r'F-\d{3}\.\d{2}',item['id']) is None
            or item.get('implementation') not in {'implemented','partial','planned'} or not isinstance(item.get('validation'),dict)
            or item['validation'].get('software') not in {'verified','baseline_verified','not_verified'}
            or not isinstance(item.get('depends_on'),list) or any(not isinstance(parent,str) for parent in item['depends_on'])
            or len(set(item['depends_on']))!=len(item['depends_on'])):
            raise CommunityStoreConflict('Invalid frozen feature definition')
    features={item['id']:item for item in registry['features']}
    if len(features)!=len(registry['features']):raise CommunityStoreConflict('Repeated catalog feature identity')
    active,complete=set(),set()

    def visit(identifier):
        """Detect cycles even in prerequisites not directly targeted by this plan."""
        if identifier in active:raise CommunityStoreConflict('Feature prerequisite cycle')
        if identifier in complete:return
        if identifier not in features:raise CommunityStoreConflict('Unknown catalog prerequisite')
        item=features[identifier]
        if not isinstance(item.get('depends_on'),list) or len(item['depends_on'])>512:raise CommunityStoreConflict('Invalid catalog prerequisites')
        active.add(identifier)
        for parent in item['depends_on']:visit(parent)
        active.remove(identifier);complete.add(identifier)

    for identifier in features:visit(identifier)
    return features


def missing_feature_prerequisites(features,identifiers):
    """A completed queue attempt cannot silently mark a partial feature implemented or erase its independent/model gates."""
    missing,seen=set(),set()

    def visit(identifier):
        """Traverse only actual prerequisite IDs; work improving an existing feature is not its own prerequisite."""
        if identifier in seen:return
        seen.add(identifier)
        for parent in features[identifier]['depends_on']:
            item=features[parent]
            if item['implementation']!='implemented' or item['validation']['software'] not in {'verified','baseline_verified'}:missing.add(parent)
            visit(parent)

    for identifier in identifiers:
        if identifier not in features:raise CommunityStoreConflict('Evaluation references an unknown feature')
        visit(identifier)
    return sorted(missing)


def build_queue_plan(body,policy,registry,contexts):
    """Freeze a reproducible roadmap from trusted saved evaluation contexts; client JSON cannot supply its own approval/source facts."""
    if not isinstance(body,CommunityQueuePlanInput) or not isinstance(policy,CommunityQueuePolicy):
        raise CommunityStoreConflict('Queue planning requires strict contracts')
    features=catalog_features(registry)
    entries={entry.key:entry for entry in body.proposals};parents={};children={key:[] for key in entries};seen_requests=set()
    for key,entry in entries.items():
        if key in entry.after or not set(entry.after)<=set(entries):raise CommunityStoreConflict('Unknown or self-referencing predecessor')
        context=contexts.get(entry.evaluation_id)
        if (not isinstance(context,dict) or context.get('decision')!='reasonable' or not context.get('source_current')
            or not context.get('feature_ids') or not context.get('acceptance') or not context.get('request_id')
            or not context.get('version_id') or not context.get('evaluation_hash') or not context.get('fingerprint')):
            raise CommunityStoreConflict('A current original reasonable evaluation is required')
        if type(context.get('evaluation_sequence')) is not int or context['evaluation_sequence']<1 or context.get('evaluation_order_origin') not in {'legacy_rowid_snapshot_v1','commit_trigger_v1'}:
            raise CommunityStoreConflict('A stable original evaluation sequence is required')
        if context['request_id'] in seen_requests:raise CommunityStoreConflict('One logical requirement cannot enter a plan twice')
        seen_requests.add(context['request_id'])
        parents[key]=set(entry.after)
        for parent in entry.after:children[parent].append(key)
    pending={key:len(value) for key,value in parents.items()};ready=[]

    def rank(key):
        """Exact rational value/effort ranking with stable ties avoids float drift and source-order priority."""
        entry=entries[key]
        return (-Fraction(entry.value_score,entry.effort_minutes),entry.effort_minutes,key)

    for key,count in pending.items():
        if count==0:heapq.heappush(ready,rank(key))
    ordered=[]
    while ready:
        _ratio,_effort,key=heapq.heappop(ready);ordered.append(key)
        for child in children[key]:
            pending[child]-=1
            if pending[child]==0:heapq.heappush(ready,rank(child))
    if len(ordered)!=len(entries):raise CommunityStoreConflict('Queue predecessor cycle')
    selected=set();used=0;result=[]
    for key in ordered:
        entry=entries[key];context=contexts[entry.evaluation_id];blockers=[]
        missing=missing_feature_prerequisites(features,context['feature_ids'])
        if entry.estimated_external_fee_fen>policy.external_fee_limit_fen:
            blockers.append({'code':'external_cost_not_authorized','estimated_fen':entry.estimated_external_fee_fen,'authorized_fen':0})
        if missing:blockers.append({'code':'feature_prerequisites_incomplete','feature_ids':missing})
        if entry.effort_minutes>policy.max_active_effort_minutes:
            blockers.append({'code':'single_attempt_effort_capacity','maximum_minutes':policy.max_active_effort_minutes})
        not_selected=sorted(parents[key]-selected)
        if not_selected:blockers.append({'code':'predecessor_not_selected','keys':not_selected})
        if not blockers and used+entry.effort_minutes>policy.max_plan_effort_minutes:
            blockers.append({'code':'planning_capacity','remaining_minutes':policy.max_plan_effort_minutes-used})
        if not blockers:selected.add(key);used+=entry.effort_minutes
        status='blocked' if blockers else 'waiting_predecessor' if parents[key] else 'ready'
        result.append({'key':key,'evaluation_id':entry.evaluation_id,'evaluation_hash':context['evaluation_hash'],
            'request_id':context['request_id'],'version_id':context['version_id'],'source_fingerprint':context['fingerprint'],
            'evaluation_sequence':context['evaluation_sequence'],'evaluation_order_origin':context['evaluation_order_origin'],
            'feature_ids':sorted(context['feature_ids']),'acceptance':list(context['acceptance']),
            'value_score':entry.value_score,'value_reason':entry.value_reason,'effort_minutes':entry.effort_minutes,
            'estimated_external_fee_fen':entry.estimated_external_fee_fen,'after':sorted(parents[key]),
            'rank':len(result)+1,'capacity_selected':not blockers,'snapshot_status':status,'blockers':blockers})
    return {'schema_version':1,'title':body.title,'input':body.model_dump(),'policy':policy.model_dump(),
        'feature_catalog_hash':hashlib.sha256(canonical(registry).encode('utf8')).hexdigest(),
        'entries':result,'selected_effort_minutes':used,'ranking':'topological, exact value/effort descending, effort/key stable ties',
        'source_observations_required_before_claim':True,'execution_authorized':False,'implementation_or_quality_qualified':False,
        'public_deployment_verified':False,'external_model_calls':0,'paid_authorization':0}
