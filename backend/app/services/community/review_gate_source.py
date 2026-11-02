"""Bounded anonymous fixed-host GitHub PR/review/workflow reads, with explicit-attempt jobs and end-of-collection race checks."""
from dataclasses import asdict
from datetime import datetime,timezone
import hashlib
import json
import os
from pathlib import Path
import re
import sys
import time

from app.core.contracts.community_review_gates import ExactPublicPRObservation,WORKFLOW_REQUIREMENTS
from app.services.community.events import unique_fields,invalid_number,finite_float
from app.services.community.public_source import github_public_get
from app.services.community.store import CommunityStoreConflict

PROTOCOL='github-public-pr-rest-2022-11-28-v1'
MAX_JSON=1048576


def positive(value):
    """GitHub IDs/counts cannot be accepted through boolean coercion."""
    return type(value) is int and 0<value<=9223372036854775807


def git_id(value):
    """API paths contain only validated immutable SHA1 Git identities, never a supplied URL/ref expression."""
    return isinstance(value,str) and re.fullmatch(r'[0-9a-f]{40}',value) is not None


def parse_pr_json(raw):
    """Reject oversized/nonfinite/ambiguous provider JSON, including nested duplicate fields before selecting any identity or status."""
    if not isinstance(raw,bytes) or not 1<=len(raw)<=MAX_JSON: raise CommunityStoreConflict('Public PR response exceeds fixed bounds')
    try:
        value=json.loads(raw.decode('utf8'),object_pairs_hook=unique_fields,parse_constant=invalid_number,parse_float=finite_float)
        if not isinstance(value,(dict,list)): raise ValueError()
        return value
    except (ValueError,TypeError,UnicodeError,RecursionError):
        raise CommunityStoreConflict('Public PR JSON could not be verified') from None


def timestamp_us(value):
    """Use GitHub's submitted UTC timestamp for decisive review order; no body/login text is interpreted."""
    if not isinstance(value,str) or len(value)>40: raise CommunityStoreConflict('Review submission timestamp required')
    try:
        parsed=datetime.fromisoformat(value.replace('Z','+00:00'))
        if parsed.tzinfo is None or parsed.utcoffset().total_seconds()!=0: raise ValueError()
        delta=parsed-datetime(1970,1,1,tzinfo=timezone.utc)
        result=delta.days*86400000000+delta.seconds*1000000+delta.microseconds
        if result<0: raise ValueError()
        return result
    except (ValueError,TypeError,OverflowError): raise CommunityStoreConflict('Invalid public review timestamp') from None


def project_pr(value,config,number):
    """Retain only bounded numeric/source/state facts; comments, user prose, URLs and arbitrary nested objects are discarded."""
    try:
        head=value['head']; base=value['base']; author=value['user']['id']
        if (type(value['number']) is not int or value['number']!=number or not positive(author) or not git_id(head['sha']) or not git_id(base['sha'])
                or value['state'] not in {'open','closed'} or any(type(value.get(name)) is not bool for name in ('draft','merged','locked'))
                or value.get('mergeable') is not None and type(value['mergeable']) is not bool
                or type(value.get('commits')) is not int or not 0<=value['commits']<=1000
                or not all(isinstance(item,str) and 1<=len(item)<=255 and not any(ord(char)<32 for char in item) for item in (head['ref'],base['ref']))): raise ValueError()
        for source in (head,base):
            if (type(source['repo']['id']) is not int or source['repo']['id']!=config.repository_id
                    or source['repo'].get('private') is not False or source['repo']['full_name'].casefold()!=config.repository_name.casefold()): raise ValueError()
        merge=value.get('merge_commit_sha')
        if merge is not None and not git_id(merge): raise ValueError()
        return {'number':number,'state':value['state'],'draft':value['draft'],'merged':value['merged'],'locked':value['locked'],'author_id':author,
            'head_sha':head['sha'],'head_ref':head['ref'],'head_repository_id':head['repo']['id'],
            'base_sha':base['sha'],'base_ref':base['ref'],'base_repository_id':base['repo']['id'],
            'commits':value['commits'],'mergeable':value.get('mergeable'),'merge_commit_sha':merge}
    except (ValueError,TypeError,KeyError,AttributeError): raise CommunityStoreConflict('Public PR source identity/state could not be verified') from None


def _inspect_public_pr(config,number,*,reader=None,clock_us=None):
    """Observe one same-repository public PR with finite complete pagination; repeat reviews/runs/PR/main to reject changes during collection."""
    if config.repository_id is None or not positive(number) or number>2147483647: raise CommunityStoreConflict('Exact public repository/PR number required')
    get=reader or github_public_get; prefix='/repos/'+config.repository_name; hashes=[]

    def read(path):
        """Only internally constructed fixed-host paths are read; a complete collection cannot exceed32 bounded requests."""
        if len(hashes)>=32: raise CommunityStoreConflict('Public PR observation request bound reached')
        raw=get(path); result=parse_pr_json(raw); hashes.append(hashlib.sha256(raw).hexdigest()); return result

    def main_ref():
        """Require an exact main commit ref, without following a provider/user-supplied URL."""
        value=read(prefix+'/git/ref/heads/main')
        if value.get('ref')!='refs/heads/main' or value.get('object',{}).get('type')!='commit' or not git_id(value['object'].get('sha')):
            raise CommunityStoreConflict('Exact public main reference required')
        return value['object']['sha']

    repository=read(prefix)
    if (type(repository.get('id')) is not int or repository['id']!=config.repository_id or not isinstance(repository.get('full_name'),str)
            or repository['full_name'].casefold()!=config.repository_name.casefold() or repository.get('private') is not False
            or repository.get('visibility')!='public' or repository.get('archived') is not False or repository.get('disabled') is not False):
        raise CommunityStoreConflict('Public repository identity/visibility changed')
    main=main_ref(); pr=project_pr(read(prefix+'/pulls/'+str(number)),config,number); head=pr['head_sha']

    def reviews():
        """Collect at most300 reviews plus an empty completion page, ignoring all review bodies and validating distinct provider identities."""
        result=[]; seen=set()
        for page in range(1,5):
            values=read(prefix+'/pulls/'+str(number)+'/reviews?per_page=100&page='+str(page))
            if not isinstance(values,list) or len(values)>100: raise CommunityStoreConflict('Complete public review page required')
            if len(result)+len(values)>300: raise CommunityStoreConflict('Public review set exceeds fixed bound')
            for value in values:
                user=value.get('user',{}); identifier=value.get('id'); state=value.get('state')
                if (not positive(identifier) or identifier in seen or not positive(user.get('id')) or user.get('type') not in {'User','Bot','Organization'}
                        or state not in {'APPROVED','CHANGES_REQUESTED','COMMENTED','DISMISSED','PENDING'} or not git_id(value.get('commit_id'))
                        or value.get('author_association') not in {'OWNER','MEMBER','COLLABORATOR','CONTRIBUTOR','FIRST_TIME_CONTRIBUTOR','FIRST_TIMER','NONE','MANNEQUIN'}):
                    raise CommunityStoreConflict('Ambiguous public review identity/state')
                seen.add(identifier)
                result.append({'id':identifier,'user_id':user['id'],'user_type':user['type'],'state':state,'commit_id':value['commit_id'],
                    'author_association':value['author_association'],'submitted_at_us':None if state=='PENDING' else timestamp_us(value.get('submitted_at'))})
            if len(values)<100: return tuple(sorted(result,key=lambda item:item['id']))
        raise CommunityStoreConflict('Public review pagination completion is unknown')

    def runs():
        """Fetch all bounded pull-request runs for this exact head, retaining required workflow identities and no log/artifact URLs."""
        result=[]; seen=set(); total=None
        for page in range(1,3):
            values=read(prefix+'/actions/runs?event=pull_request&head_sha='+head+'&per_page=100&page='+str(page))
            count=values.get('total_count'); batch=values.get('workflow_runs')
            if (type(count) is not int or not 0<=count<=200 or total is not None and count!=total or not isinstance(batch,list)
                    or len(batch)!=min(100,count-len(result))): raise CommunityStoreConflict('Complete bounded workflow list required')
            total=count
            for value in batch:
                identifier=value.get('id')
                if not positive(identifier) or identifier in seen: raise CommunityStoreConflict('Ambiguous public workflow run identity')
                seen.add(identifier)
                # Count every returned run for pagination completeness, even
                # when the server-owned gate does not require that workflow.
                result.append(value)
            if len(result)==total: break
        if len(result)!=total: raise CommunityStoreConflict('Incomplete public workflow pagination')
        projected=[]; workflow_ids={}
        for value in result:
            path=value.get('path')
            if path not in WORKFLOW_REQUIREMENTS: continue
            if path in workflow_ids and workflow_ids[path]!=value.get('workflow_id'):
                raise CommunityStoreConflict('Pinned workflow path has mixed provider identities')
            workflow_ids[path]=value.get('workflow_id')
            prs=value.get('pull_requests')
            if (not all(positive(value.get(name)) for name in ('id','workflow_id','run_number','run_attempt'))
                    or value.get('event')!='pull_request' or value.get('head_sha')!=head or not isinstance(prs,list) or len(prs)!=1
                    or value.get('repository',{}).get('id')!=config.repository_id or type(value.get('repository',{}).get('id')) is not int
                    or value.get('head_repository',{}).get('id')!=config.repository_id or type(value.get('head_repository',{}).get('id')) is not int):
                raise CommunityStoreConflict('Workflow source/event differs from exact public PR')
            pull=prs[0]
            if (type(pull.get('number')) is not int or pull['number']!=number or pull.get('head',{}).get('sha')!=head
                    or pull.get('base',{}).get('sha')!=pr['base_sha'] or pull.get('head',{}).get('repo',{}).get('id')!=config.repository_id
                    or pull.get('base',{}).get('repo',{}).get('id')!=config.repository_id
                    or type(pull.get('head',{}).get('repo',{}).get('id')) is not int or type(pull.get('base',{}).get('repo',{}).get('id')) is not int):
                raise CommunityStoreConflict('Workflow belongs to another PR/base/head source')
            projected.append({name:value.get(name) for name in ('id','workflow_id','run_number','run_attempt','path','head_sha','event','status','conclusion')}|
                {'repository_id':config.repository_id,'head_repository_id':config.repository_id,
                 'pull_request':{'number':number,'head_sha':head,'base_sha':pr['base_sha'],'repository_id':config.repository_id}})
        return tuple(sorted(projected,key=lambda item:(item['path'],item['run_number'],item['run_attempt'],item['id'])))

    review_values=reviews(); run_values=runs(); latest={}
    for value in run_values:
        path=value['path']; order=(value['run_number'],value['run_attempt'],value['id'])
        if path not in latest or order>latest[path][0]: latest[path]=(order,value)
    workflow_states={}; workflow_identity={}; jobs_by_run={}
    for path,(_order,run) in latest.items():
        workflow=read(prefix+'/actions/workflows/'+str(run['workflow_id']))
        if workflow.get('id')!=run['workflow_id'] or type(workflow.get('id')) is not int or workflow.get('path')!=path:
            raise CommunityStoreConflict('Trusted workflow identity/path changed')
        state=workflow.get('state')
        if not isinstance(state,str) or len(state)>64: raise CommunityStoreConflict('Unknown workflow availability')
        workflow_states[path]=state
        workflow_identity[path]=(workflow['id'],path,state)
        # Job JSON does not consistently contain run_attempt. Bind the jobs
        # by the explicit documented attempt endpoint, never infer latest=1.
        values=read(prefix+'/actions/runs/'+str(run['id'])+'/attempts/'+str(run['run_attempt'])+'/jobs?per_page=100&page=1')
        batch=values.get('jobs'); count=values.get('total_count')
        if type(count) is not int or not 0<=count<=100 or not isinstance(batch,list) or len(batch)!=count:
            raise CommunityStoreConflict('Complete exact-attempt jobs required')
        projected=[]; seen_jobs=set()
        for job in batch:
            if not positive(job.get('id')) or job['id'] in seen_jobs or type(job.get('run_id')) is not int or job['run_id']!=run['id']:
                raise CommunityStoreConflict('Ambiguous job/source attempt identity')
            if 'run_attempt' in job and (type(job['run_attempt']) is not int or job['run_attempt']!=run['run_attempt']):
                raise CommunityStoreConflict('Explicit job attempt contradicts the requested endpoint')
            seen_jobs.add(job['id']); name=job.get('name'); steps=job.get('steps')
            if not isinstance(name,str) or not 1<=len(name)<=255 or not isinstance(steps,list) or len(steps)>64:
                raise CommunityStoreConflict('Bounded actual job/step list required')
            clean_steps=[]; step_ids=set()
            for step in steps:
                if (not positive(step.get('number')) or step['number'] in step_ids or not isinstance(step.get('name'),str)
                        or not 1<=len(step['name'])<=300): raise CommunityStoreConflict('Ambiguous bounded job step')
                step_ids.add(step['number']); clean_steps.append({name:step.get(name) for name in ('number','name','status','conclusion')})
            projected.append({'id':job['id'],'name':name,'run_id':run['id'],'run_attempt':run['run_attempt'],
                'attempt_binding':'explicit_run_attempt_endpoint','status':job.get('status'),'conclusion':job.get('conclusion'),'steps':clean_steps})
        jobs_by_run[(run['id'],run['run_attempt'])]=tuple(projected)
    merge={}
    if pr['merge_commit_sha'] is not None:
        value=read(prefix+'/git/commits/'+pr['merge_commit_sha']); parents=value.get('parents')
        if value.get('sha')!=pr['merge_commit_sha'] or not git_id(value.get('tree',{}).get('sha')) or not isinstance(parents,list) or len(parents)>2:
            raise CommunityStoreConflict('Public tested merge object is not supported')
        if any(not isinstance(parent,dict) or not git_id(parent.get('sha')) for parent in parents): raise CommunityStoreConflict('Incomplete merge parents')
        merge={'sha':value['sha'],'tree':value['tree']['sha'],'parents':[parent['sha'] for parent in parents]}
    if reviews()!=review_values or runs()!=run_values:
        raise CommunityStoreConflict('Public PR/review/workflow/main changed during observation')
    for path,(_order,run) in latest.items():
        workflow=read(prefix+'/actions/workflows/'+str(run['workflow_id']))
        if (workflow.get('id'),workflow.get('path'),workflow.get('state'))!=workflow_identity[path]:
            raise CommunityStoreConflict('Workflow availability changed during observation')
    if project_pr(read(prefix+'/pulls/'+str(number)),config,number)!=pr or main_ref()!=main:
        raise CommunityStoreConflict('Public PR/review/workflow/main changed during observation')
    completed=tuple({**value,'workflow_state':workflow_states[value['path']],
        'jobs':jobs_by_run.get((value['id'],value['run_attempt']),())} for value in run_values)
    now=time.time_ns()//1000 if clock_us is None else clock_us
    if type(now) is not int or now<0: raise CommunityStoreConflict('Invalid public PR observation clock')
    return ExactPublicPRObservation(config.repository_id,config.repository_name.casefold(),now,main,pr,merge,review_values,completed,tuple(hashes))


def inspect_public_pr(config,number,*,reader=None,clock_us=None):
    """Normalize malformed nested provider data without reflecting review bodies, raw API errors, URLs or private controller context."""
    try: return _inspect_public_pr(config,number,reader=reader,clock_us=clock_us)
    except CommunityStoreConflict: raise
    except (ValueError,TypeError,KeyError,AttributeError,IndexError,RecursionError):
        raise CommunityStoreConflict('Public PR source metadata could not be verified') from None


def parse_pr_observation(raw,config,*,now=None):
    """Accept only the owned child's exact finite wire shape with fresh configured source identity; this is not a caller approval endpoint."""
    value=parse_pr_json(raw); keys={'repository_id','repository_name','checked_at_us','main_commit','pull_request','tested_merge','reviews','workflows','response_sha256','protocol'}
    try:
        moment=time.time_ns()//1000 if now is None else now
        if (not isinstance(value,dict) or set(value)!=keys or value['protocol']!=PROTOCOL or type(value['repository_id']) is not int
                or value['repository_id']!=config.repository_id or value['repository_name'].casefold()!=config.repository_name.casefold()
                or not git_id(value['main_commit']) or type(value['checked_at_us']) is not int or type(moment) is not int
                or not 0<=moment-value['checked_at_us']<=60000000 or not isinstance(value['reviews'],list) or len(value['reviews'])>300
                or not isinstance(value['workflows'],list) or len(value['workflows'])>200 or not isinstance(value['pull_request'],dict)
                or not isinstance(value['tested_merge'],dict) or not isinstance(value['response_sha256'],list) or not 1<=len(value['response_sha256'])<=32
                or any(not isinstance(item,str) or re.fullmatch(r'[0-9a-f]{64}',item) is None for item in value['response_sha256'])): raise ValueError()
        workflows=[]
        for run in value['workflows']:
            if not isinstance(run,dict) or not isinstance(run.get('jobs'),list) or len(run['jobs'])>100 or any(not isinstance(job,dict) for job in run['jobs']):
                raise ValueError()
            workflows.append({**run,'jobs':tuple(run['jobs'])})
        return ExactPublicPRObservation(value['repository_id'],value['repository_name'].casefold(),value['checked_at_us'],value['main_commit'],
            value['pull_request'],value['tested_merge'],tuple(value['reviews']),tuple(workflows),tuple(value['response_sha256']),value['protocol'])
    except (ValueError,TypeError,KeyError,AttributeError,RecursionError): raise CommunityStoreConflict('Owned public PR observation could not be verified') from None


async def verify_public_pr(config,number):
    """Own the fixed credential-free GET helper for45 seconds; never inherit Git/model/API auth, personal proxy config or a caller-selected executable/URL."""
    from app.core.owned_process import run_owned_process
    allowed={'SYSTEMROOT','WINDIR','COMSPEC','PATH','PATHEXT','TEMP','TMP','TMPDIR','LANG','LC_ALL','TZ'}
    environment={name:value for name,value in os.environ.items() if name.upper() in allowed}; environment.update(PYTHONUTF8='1',PYTHONDONTWRITEBYTECODE='1')
    data=json.dumps({'configuration':config.model_dump(),'pr_number':number}).encode('utf8')
    if not positive(number) or number>2147483647 or len(data)>16384: raise CommunityStoreConflict('Bounded original public PR input required')
    raw,code=await run_owned_process([sys.executable,str(Path(__file__).with_name('review_gate_source_child.py'))],data,
        timeout=45,max_stdout_bytes=1048576,environment=environment)
    if code: raise CommunityStoreConflict('Public PR observation unavailable; no automatic retry')
    result=parse_pr_observation(raw,config)
    if result.pull_request.get('number')!=number: raise CommunityStoreConflict('Public PR helper returned another request')
    return result
