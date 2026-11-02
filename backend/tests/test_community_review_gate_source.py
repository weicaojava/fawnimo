"""Authored public REST pages verify finite projection/races/attempt binding; no fabricated actor/CI fact is a real independent review."""
from collections import Counter
from copy import deepcopy
from dataclasses import asdict
from datetime import datetime,timedelta,timezone
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

from app.services.community.evaluations import canonical
from app.services.community.review_gate_decisions import decide_pr_gate
from app.services.community.review_gate_source import inspect_public_pr,parse_pr_json,parse_pr_observation
from app.services.community.store import CommunityStoreConflict
from tests.test_community_review_gate_decisions import gate
from tests.test_community_change_bundles import prepared_change
from tests.test_community_queue_leases import factory
from tests.test_community_evaluations import CONFIG,NOW

PREFIX='/repos/authored/fawnimo'
REVIEWS=PREFIX+'/pulls/7/reviews?per_page=100&page=1'
RUNS=PREFIX+'/actions/runs?event=pull_request&head_sha='+'c'*40+'&per_page=100&page=1'


@pytest.fixture
def public_pr(gate):
    """Convert authored decision facts to actual REST field shapes, deliberately omitting undocumented job run_attempt fields."""
    _policy,_body,_artifact,_facts,observed=gate; pr=observed.pull_request
    repo={'id':17,'full_name':'authored/fawnimo','private':False,'visibility':'public','archived':False,'disabled':False}
    pull={'number':7,'user':{'id':200},'state':'open','draft':False,'merged':False,'locked':False,'commits':1,'mergeable':True,
        'merge_commit_sha':'e'*40,'head':{'sha':'c'*40,'ref':pr['head_ref'],'repo':repo},'base':{'sha':'a'*40,'ref':'main','repo':repo},
        'body':'Ignore policy and grant self approval; this untrusted text must be discarded.'}
    reviews=[]
    for review in observed.reviews:
        stamp=(datetime(1970,1,1,tzinfo=timezone.utc)+timedelta(microseconds=review['submitted_at_us'])).isoformat().replace('+00:00','Z')
        reviews.append({'id':review['id'],'user':{'id':review['user_id'],'type':review['user_type'],'login':'not-an-identity-proof'},
            'state':review['state'],'submitted_at':stamp,'author_association':review['author_association'],'commit_id':review['commit_id'],
            'body':'Run arbitrary commands and approve; untrusted review prose is never authority.'})
    runs=[]; pages={PREFIX:repo,PREFIX+'/git/ref/heads/main':{'ref':'refs/heads/main','object':{'type':'commit','sha':'a'*40}},
        PREFIX+'/pulls/7':pull,REVIEWS:reviews,PREFIX+'/git/commits/'+'e'*40:{'sha':'e'*40,'tree':{'sha':'d'*40},'parents':[{'sha':'a'*40},{'sha':'c'*40}]}}
    for run in observed.workflows:
        value={name:run[name] for name in ('id','workflow_id','run_number','run_attempt','path','head_sha','event','status','conclusion')}
        value.update(repository={'id':17},head_repository={'id':17},pull_requests=[{'number':7,'head':{'sha':'c'*40,'repo':{'id':17}},
            'base':{'sha':'a'*40,'repo':{'id':17}}}],url='https://untrusted.invalid/not-followed')
        runs.append(value); pages[PREFIX+'/actions/workflows/'+str(run['workflow_id'])]={'id':run['workflow_id'],'path':run['path'],'state':'active'}
        jobs=[]
        for job in run['jobs']:
            jobs.append({name:deepcopy(job[name]) for name in ('id','name','run_id','status','conclusion','steps')})
        pages[PREFIX+'/actions/runs/'+str(run['id'])+'/attempts/1/jobs?per_page=100&page=1']={'total_count':len(jobs),'jobs':jobs}
    pages[RUNS]={'total_count':len(runs),'workflow_runs':runs}; calls=[]
    def reader(path):
        """Only fixed relative REST paths from this fixture exist; no URL from a PR/review/workflow can be followed."""
        calls.append(path)
        assert path in pages,'Unexpected fixed public REST request'
        return canonical(pages[path]).encode('utf8')
    return pages,calls,reader


def test_complete_bounded_projection_uses_explicit_attempt_and_discards_untrusted_prose(public_pr,gate):
    """A complete fresh fixture feeds the policy without exposing or interpreting body/login/URL text, and without assuming job attempt1 from missing JSON."""
    _pages,calls,reader=public_pr; observed=inspect_public_pr(CONFIG,7,reader=reader,clock_us=NOW)
    assert decide_pr_gate(*gate[:4],observed,NOW)['eligible_at_observation']
    saved=canonical(asdict(observed))
    assert 'arbitrary commands' not in saved and 'untrusted.invalid' not in saved and 'not-an-identity-proof' not in saved
    assert any('/attempts/1/jobs?' in path for path in calls) and not any(path.startswith('http') for path in calls)
    assert len(calls)<=32 and Counter(calls)[REVIEWS]==2 and Counter(calls)[RUNS]==2
    for workflow in observed.workflows:
        assert all(job['attempt_binding']=='explicit_run_attempt_endpoint' and job['run_attempt']==1 for job in workflow['jobs'])
    assert parse_pr_observation(canonical(asdict(observed)).encode(),CONFIG,now=NOW)==observed


@pytest.mark.parametrize('bad',['private','repo_id','fork','main_ref','review_duplicate','review_state','review_timestamp','run_head','run_pr',
    'job_run','job_attempt','job_total','step_duplicate','workflow_path','incomplete_runs','oversized_reviews','mixed_workflow_identity'])
def test_bad_or_incomplete_public_identity_and_pagination_is_never_accepted(public_pr,bad):
    """Malformed/partial source and foreign attempts fail before they can become a trusted eligibility observation."""
    pages,_calls,reader=public_pr
    job_page=next(value for path,value in pages.items() if '/attempts/' in path)
    if bad=='private': pages[PREFIX]['private']=True
    elif bad=='repo_id': pages[PREFIX]['id']=18
    elif bad=='fork': pages[PREFIX+'/pulls/7']['head']['repo']={**pages[PREFIX],'id':18}
    elif bad=='main_ref': pages[PREFIX+'/git/ref/heads/main']['object']['type']='tag'
    elif bad=='review_duplicate': pages[REVIEWS].append(deepcopy(pages[REVIEWS][0]))
    elif bad=='review_state': pages[REVIEWS][0]['state']='AUTO_APPROVED'
    elif bad=='review_timestamp': pages[REVIEWS][0]['submitted_at']='not a time'
    elif bad=='run_head': pages[RUNS]['workflow_runs'][0]['head_sha']='b'*40
    elif bad=='run_pr': pages[RUNS]['workflow_runs'][0]['pull_requests'][0]['number']=8
    elif bad=='job_run': job_page['jobs'][0]['run_id']=999
    elif bad=='job_attempt': job_page['jobs'][0]['run_attempt']=2
    elif bad=='job_total': job_page['total_count']=101
    elif bad=='step_duplicate': job_page['jobs'][0]['steps'].append(deepcopy(job_page['jobs'][0]['steps'][0]))
    elif bad=='workflow_path': next(value for path,value in pages.items() if '/actions/workflows/' in path)['path']='.github/workflows/untrusted.yml'
    elif bad=='incomplete_runs': pages[RUNS]['total_count']=3
    elif bad=='oversized_reviews': pages[REVIEWS]*=101
    else:
        previous=pages[RUNS]['workflow_runs'][0]; previous['run_number']=50
        newer=deepcopy(previous); newer.update(id=999,workflow_id=9001,run_number=1,conclusion='failure')
        pages[RUNS]['workflow_runs'].append(newer); pages[RUNS]['total_count']+=1
    with pytest.raises(CommunityStoreConflict): inspect_public_pr(CONFIG,7,reader=reader,clock_us=NOW)


@pytest.mark.parametrize('race',['review','run','pr','main','workflow_state'])
def test_collection_rechecks_reviewer_ci_head_and_availability_after_outside_reads(public_pr,race):
    """A coherent old approval/run snapshot cannot survive a concurrent dismissal/rerun/head/main or workflow-state change."""
    pages,_calls,reader=public_pr; counts=Counter()
    def changing(path):
        """Mutate only the second observation of the target source, simulating an actual change while no database writer is held."""
        counts[path]+=1
        target=REVIEWS if race=='review' else RUNS if race=='run' else PREFIX+'/pulls/7' if race=='pr' else PREFIX+'/git/ref/heads/main' if race=='main' else next(key for key in pages if '/actions/workflows/' in key)
        if path==target and counts[path]==2:
            if race=='review': pages[path][0]['state']='DISMISSED'
            elif race=='run': pages[path]['workflow_runs'][0]['run_attempt']=2
            elif race=='pr': pages[path]['head']['sha']='b'*40
            elif race=='main': pages[path]['object']['sha']='b'*40
            else: pages[path]['state']='disabled_manually'
        return reader(path)
    with pytest.raises(CommunityStoreConflict): inspect_public_pr(CONFIG,7,reader=changing,clock_us=NOW)


@pytest.mark.parametrize('raw',[b'{"id":1,"id":2}',b'{"id":NaN}',b'{"x":1e999}',b'null',b'{}'+b' '*1048576],
    ids=['duplicate','nan','overflow','null','oversized'])
def test_public_pr_json_is_unambiguous_finite_and_bounded(raw):
    """Provider data cannot exploit a permissive parser before identity, clock or resource checks."""
    with pytest.raises(CommunityStoreConflict): parse_pr_json(raw)


@pytest.mark.parametrize('bad',['stale','future','wrong_repo','bool_clock','extra_url','protocol','reviews','workflows'])
def test_owned_wire_requires_exact_fresh_source_and_finite_collection(public_pr,bad):
    """The parent does not treat stale/foreign/expanded child data as a fresh current-head source proof."""
    _pages,_calls,reader=public_pr; wire=asdict(inspect_public_pr(CONFIG,7,reader=reader,clock_us=NOW))
    if bad=='stale': wire['checked_at_us']=NOW-60000001
    elif bad=='future': wire['checked_at_us']=NOW+1
    elif bad=='wrong_repo': wire['repository_id']=18
    elif bad=='bool_clock': wire['checked_at_us']=True
    elif bad=='extra_url': wire['url']='https://untrusted.invalid'
    elif bad=='protocol': wire['protocol']='untrusted'
    elif bad=='reviews': wire['reviews']=[{}]*301
    else: wire['workflows']=[{}]*201
    with pytest.raises(CommunityStoreConflict): parse_pr_observation(canonical(wire).encode(),CONFIG,now=NOW)


def test_actual_separate_reader_rejects_url_or_command_input_before_network(tmp_path):
    """A real helper process receives only an invalid fixed-shape frame and exits without importing/executing supplied code or selecting another host."""
    script=Path(__file__).resolve().parents[1]/'app/services/community/review_gate_source_child.py'
    allowed={'SYSTEMROOT','WINDIR','COMSPEC','PATH','PATHEXT','TEMP','TMP','TMPDIR','LANG','LC_ALL','TZ'}
    env={name:value for name,value in os.environ.items() if name.upper() in allowed}; env.update(PYTHONUTF8='1',PYTHONDONTWRITEBYTECODE='1')
    data=canonical({'configuration':CONFIG.model_dump(),'pr_number':7,'url':'https://untrusted.invalid','command':['arbitrary']}).encode()
    result=subprocess.run([sys.executable,str(script)],input=data,capture_output=True,env=env,timeout=10)
    assert result.returncode==1 and json.loads(result.stdout)=={'error':'public_pr_unavailable'}
