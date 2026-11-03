"""Actual SQLite, concurrent reservations, native Git and CLI with authored remote facts; no real GitHub token, model or independent approval."""
import asyncio
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import datetime
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import pytest
from app.core.contracts.community_publication import CommunityPublicationInput,CommunityPublicationPolicy
from app.services.community.publication_objects import publication_objects
from app.services.community.publication_runtime import publish_candidate,reconcile_publication
from app.services.community.publication_store import CommunityPublicationStore
from app.services.community.publication_transport import PublicationRemoteError
from app.services.community.store import CommunityStoreConflict
from tests.test_community_candidate_git import candidate_repo,assert_original
from tests.test_community_change_bundles import prepared_change
from tests.test_community_queue_leases import factory
from tests.test_community_review_gate_decisions import gate


@pytest.fixture
def publication(gate,tmp_path):
    """Reuse explicit fabricated checked facts; these are not proof of actual GitHub, CI or independent review."""
    seed,request,artifact,facts,_observed=gate
    policy=CommunityPublicationPolicy(repository_id=seed.repository_id,repository_name=seed.repository_name,enabled=True,
        trusted_workflows=seed.trusted_workflows,trusted_check_image_ids=seed.trusted_check_image_ids,trusted_runner_sha256=seed.trusted_runner_sha256)
    body=CommunityPublicationInput(**request.model_dump(exclude={'pr_number'}))
    store=CommunityPublicationStore(tmp_path/'publication',policy,initialize=True)
    return store,body,artifact,facts


class AuthoredRemote:
    """Deterministic in-memory transport with explicit accepted-but-lost responses and foreign mutable refs, never real service qualification."""
    def __init__(self,policy,candidate,*,failure=None):
        """Remember each mutation and simulate only fixed service endpoints."""
        self.policy=policy; self.candidate=candidate; self.failure=failure; self.calls=[]; self.branch=None; self.pr=[]; self.main=candidate.parent

    def repository(self):
        """Return authored public repository facts only."""
        return {'id':self.policy.repository_id,'full_name':self.policy.repository_name,'private':False,'visibility':'public','archived':False,'disabled':False}

    def ref(self,branch):
        """A missing reserved ref is distinct from a successful creation whose response was lost."""
        sha=self.main if branch=='main' else self.branch
        return None if sha is None else {'ref':'refs/heads/'+branch,'object':{'type':'commit','sha':sha}}

    def pulls(self,branch):
        """No test claims these fake PR facts existed on GitHub."""
        assert branch==self.candidate.branch; return self.pr

    def create_object(self,kind,body):
        """Return exact expected fixture object identity, or an authored mismatching server result."""
        self.calls.append(kind)
        if self.failure==kind: return {'sha':'0'*40}
        return {'sha':{'trees':self.candidate.tree,'commits':self.candidate.commit,'blobs':'a'*40}[kind]}

    def create_ref(self,branch,commit):
        """Simulate a branch accepted before a timeout; the controller must retain the original intent."""
        self.calls.append('ref'); assert branch==self.candidate.branch and commit==self.candidate.commit
        if self.failure=='ref-rejected': raise PublicationRemoteError(422)
        self.branch=commit
        if self.failure=='ref-lost': raise PublicationRemoteError()
        if self.failure=='ref-crash': raise SystemExit('Authored controller interruption')
        return self.ref(branch)

    def create_pull(self,branch,body):
        """Create a fixture draft once before a possible lost response, using fixed exact original head/base."""
        self.calls.append('pr'); repo={'id':self.policy.repository_id,'full_name':self.policy.repository_name}
        self.pr=[{'number':7,'state':'open','draft':True,'merged_at':None,'head':{'ref':branch,'sha':self.candidate.commit,'repo':repo},
            'base':{'ref':'main','sha':self.candidate.parent,'repo':repo}}]
        if self.failure=='pr-lost': raise PublicationRemoteError()
        if self.failure=='pr-crash': raise SystemExit('Authored controller interruption')
        return self.pr[0]


def run_publication(values,remote,*,revalidate=None):
    """Supply only an internal object plan; native exact serialization is separately verified against real Git."""
    store,body,artifact,facts=values
    async def current():
        """The pure runtime fixture has no source network; actual source admission is tested separately."""
        if revalidate is not None: revalidate()
    objects={'candidate':facts.candidate,'blobs':[{'sha':'a'*40,'body':{'content':'fixture','encoding':'base64'}}],
        'tree':{'base_tree':'b'*40,'tree':[]},'commit':{'tree':facts.candidate.tree}}
    return asyncio.run(publish_candidate(store,body,artifact,facts,objects,remote,revalidate=current))


def test_exact_draft_publication_and_original_recovery_never_repeat_writes(publication):
    """One owner publishes once; changing/withdrawing current state cannot turn original replay into another mutation."""
    store,body,_artifact,facts=publication; remote=AuthoredRemote(store.policy,facts.candidate)
    saved=run_publication(publication,remote); before=list(remote.calls)
    assert saved['phase']=='published' and saved['history'][-1]['details']['pr_number']==7
    assert before==['blobs','trees','commits','ref','pr']
    remote.main='9'*40; recovered=run_publication(publication,remote)
    assert recovered==saved and remote.calls==before
    assert not any(saved[name] for name in ('new_write_authorized','independent_review_completed','ci_qualified','merge_authorized','deployment_authorized'))


@pytest.mark.parametrize('failure',['blobs','trees','commits','ref-rejected','ref-lost','pr-lost'])
def test_bad_or_unknown_result_is_preserved_without_blind_retry(publication,failure):
    """Mismatched objects never acquire an active ref; accepted-but-lost ref/PR operations are read-only recoverable."""
    store,body,_artifact,facts=publication; remote=AuthoredRemote(store.policy,facts.candidate,failure=failure)
    record=run_publication(publication,remote); calls=list(remote.calls)
    assert record['phase']=='attention' and run_publication(publication,remote)==record and remote.calls==calls
    if failure in {'blobs','trees','commits'}: assert remote.branch is None and 'pr' not in calls
    observed=reconcile_publication(store,record['id'],remote)
    assert remote.calls==calls and observed['new_write_authorized'] is False
    if failure=='pr-lost': assert observed['history'][-1]['details']['pr_number']==7
    if failure in {'ref-rejected','ref-lost'}: assert observed['history'][-1]['details']['reason']=='original_write_not_confirmed'


@pytest.mark.parametrize('failure',['ref-crash','pr-crash'])
def test_controller_crash_after_accepted_write_retains_prior_durable_intent(publication,failure):
    """BaseException imitates abrupt controller death; a fresh store instance sees the attempted phase before doing any read-only reconcile."""
    store,body,_artifact,facts=publication; remote=AuthoredRemote(store.policy,facts.candidate,failure=failure)
    with pytest.raises(SystemExit): run_publication(publication,remote)
    reopened=CommunityPublicationStore(store.root); saved=reopened.replay(body)
    assert saved['phase']==('ref_intent' if failure=='ref-crash' else 'pr_intent')
    calls=list(remote.calls); reconcile_publication(reopened,saved['id'],remote)
    assert remote.calls==calls and run_publication(publication,remote)['new_write_authorized'] is False


@pytest.mark.parametrize('bad',['foreign-ref','moved-main','closed-pr','wrong-pr-sha','wrong-pr-base','ready-pr','duplicate-pr'])
def test_existing_foreign_or_changed_remote_state_never_overwritten(publication,bad):
    """Changes or duplicates are diagnosed rather than updating branches, reopening PRs, creating replacements or silently retargeting main."""
    store,_body,_artifact,facts=publication; remote=AuthoredRemote(store.policy,facts.candidate)
    if bad=='foreign-ref': remote.branch='9'*40
    elif bad=='moved-main': remote.main='9'*40
    else:
        remote.branch=facts.candidate.commit; remote.create_pull(facts.candidate.branch,publication[1]); remote.calls=[]
        if bad=='closed-pr': remote.pr[0]['state']='closed'
        elif bad=='wrong-pr-sha': remote.pr[0]['head']['sha']='9'*40
        elif bad=='wrong-pr-base': remote.pr[0]['base']['sha']='9'*40
        elif bad=='ready-pr': remote.pr[0]['draft']=False
        elif bad=='duplicate-pr': remote.pr*=2
    saved=run_publication(publication,remote)
    assert saved['phase']=='attention' and remote.calls==[]


def test_source_withdrawal_before_active_ref_stops_publication(publication):
    """Content-addressed objects may already exist, but a changed requirement stops before any active branch or PR."""
    store,_body,_artifact,facts=publication; remote=AuthoredRemote(store.policy,facts.candidate); count=[]
    def validate():
        """Withdraw the source on the second fresh admission."""
        count.append(1)
        if len(count)==2: raise CommunityStoreConflict('Authored withdrawn source')
    saved=run_publication(publication,remote,revalidate=validate)
    assert saved['phase']=='attention' and remote.calls==['blobs','trees','commits'] and remote.branch is None


def test_two_actual_sqlite_reservations_have_one_owner_and_conflicting_key_rejected(publication):
    """Concurrent controllers share a single durable reservation; a different bundle/check cannot reuse its key."""
    store,body,_artifact,facts=publication
    with ThreadPoolExecutor(max_workers=2) as pool:
        results=list(pool.map(lambda _value:store.reserve(body,facts.candidate),range(2)))
    assert sum(owned for _value,owned in results)==1 and len({value['id'] for value,_owned in results})==1
    with pytest.raises(CommunityStoreConflict): store.replay(body.model_copy(update={'check_attempt_id':'22222222-2222-4222-8222-222222222222'}))
    with pytest.raises(CommunityStoreConflict): store.reserve(body.model_copy(update={'request_key':'another-original-key'}),facts.candidate)


@pytest.mark.parametrize('table',['community_publication_policy','community_publication_intents','community_publication_events'])
@pytest.mark.parametrize('action',['UPDATE','DELETE','REPLACE_ROWID'])
def test_real_sql_history_cannot_be_rewritten_by_natural_or_physical_key(publication,table,action):
    """Actual SQLite defaults cannot bypass immutable guards with UPDATE/DELETE/INSERT OR REPLACE and a physical rowid collision."""
    store,body,_artifact,facts=publication; store.reserve(body,facts.candidate)
    with store.connect() as db:
        db.execute('PRAGMA recursive_triggers=OFF')
        row=db.execute('SELECT rowid,* FROM '+table).fetchone()
        with pytest.raises(sqlite3.IntegrityError):
            if action=='UPDATE': db.execute('UPDATE '+table+' SET '+('payload=payload' if table.endswith('policy') else 'created_at_us=created_at_us'))
            elif action=='DELETE': db.execute('DELETE FROM '+table)
            else:
                names=[name[1] for name in db.execute('PRAGMA table_info('+table+')')]
                values=[row[name] for name in names]
                if 'id' in names: values[names.index('id')]='another-id'; values[names.index('request_key')]='another-request'; values[names.index('bundle_id')]='9'*64
                db.execute('INSERT OR REPLACE INTO '+table+'(rowid,'+','.join(names)+') VALUES('+','.join('?' for _ in range(len(names)+1))+')',[row[0],*values])


def test_policy_schema_paths_and_capacity_preserve_original_recovery(publication,tmp_path):
    """Refuse changed immutable policy, mixed roots and nested source state; backward clocks affect new work only."""
    store,body,_artifact,facts=publication; record,_=store.reserve(body,facts.candidate)
    with pytest.raises(CommunityStoreConflict): CommunityPublicationStore(store.root,store.policy.model_copy(update={'enabled':False}))
    with pytest.raises(CommunityStoreConflict): CommunityPublicationStore(store.root,isolated_from=(store.root.parent,))
    store.clock_us=lambda:0
    assert store.replay(body)==record
    with pytest.raises(CommunityStoreConflict): store.append(record['id'],'attention',{'reason':'clock_backwards'})
    (store.root/'unrelated.txt').write_text('Authored unrelated data')
    with pytest.raises(CommunityStoreConflict): CommunityPublicationStore(store.root)


def test_native_git_objects_roundtrip_preserves_all_commit_bytes_and_dirty_checkout(candidate_repo):
    """Reconstruct the exact raw commit from the REST projection, including timestamp/timezone/newline, against an actual temporary Git repository."""
    repo,_body,prepared,artifact,observation,protected=candidate_repo
    candidate=repo.create(artifact,prepared,observation); projected=publication_objects(repo,artifact); message=projected['commit']
    headers=['tree '+message['tree'],'parent '+message['parents'][0]]
    for name in ('author','committer'):
        identity=message[name]; stamp=datetime.fromisoformat(identity['date']); offset=stamp.strftime('%z')
        headers.append(name+' '+identity['name']+' <'+identity['email']+'> '+str(int(stamp.timestamp()))+' '+offset)
    raw=('\n'.join(headers)+'\n\n'+message['message']).encode('utf8')
    assert hashlib.sha1(b'commit '+str(len(raw)).encode()+b'\0'+raw).hexdigest()==candidate.commit
    assert raw==repo._git('cat-file','commit',candidate.commit).stdout
    assert_original(repo,protected)


@pytest.mark.parametrize('field,value',[('enabled',1),('repository_id',True),('repository_name','https://elsewhere.invalid/repo'),('token','never-input'),('merge_authorized',True)])
def test_policy_rejects_coercion_urls_tokens_and_invented_authority(field,value):
    """Only strict maintainer policy fields are accepted; booleans/numbers cannot widen a disabled or mismatched destination."""
    with pytest.raises(ValueError): CommunityPublicationPolicy.model_validate({'repository_id':17,'repository_name':'authored/fawnimo',field:value})


def test_actual_capacity_and_vacuum_keep_original_order_and_recovery(publication):
    """Fill the actual finite store, reject one new intent and prove old-key recovery plus declared event order survives VACUUM."""
    store,body,_artifact,facts=publication; original,_owned=store.reserve(body,facts.candidate)
    for index in range(1,64):
        bundle=f'{index:064x}'; request=body.model_copy(update={'request_key':f'capacity-original-{index}','bundle_id':bundle})
        candidate=replace(facts.candidate,bundle_id=bundle,branch='community/change-'+bundle[:24])
        store.reserve(request,candidate)
    extra=body.model_copy(update={'request_key':'capacity-extra-intent','bundle_id':'9'*64})
    with pytest.raises(sqlite3.IntegrityError): store.reserve(extra,replace(facts.candidate,bundle_id=extra.bundle_id,branch='community/change-'+extra.bundle_id[:24]))
    with store.connect() as db: db.execute('VACUUM')
    assert store.replay(body)==original
    store.append(original['id'],'attention',{'reason':'authored_read_only_diagnostic'})
    history=store.get(original['id'])['history']; assert [event['sequence'] for event in history]==[1,65]


@pytest.mark.parametrize('flag',['independent_review_completed','ci_qualified','merge_authorized','deployment_authorized','external_model_calls','paid_authorization'])
def test_raw_sql_cannot_forge_audit_authority_flags(publication,flag):
    """A fresh physical/natural event key still cannot smuggle an approval, successful CI, fee or deploy authorization into history."""
    store,body,_artifact,facts=publication; record,_=store.reserve(body,facts.candidate)
    details=dict(record['history'][0]['details']); details[flag]=1
    with store.connect() as db:
        with pytest.raises(sqlite3.IntegrityError): db.execute('INSERT INTO community_publication_events(publication_id,phase,payload,created_at_us) VALUES(?,?,?,?)',
            (record['id'],'reconciled',json.dumps(details),record['history'][0]['created_at_us']))
