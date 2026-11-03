"""Real public original-request failure/retry/cancellation and connection controls against fixed owned workers; all model behavior is authored local-only data."""
import time
import pytest
from tests.test_workspace_execution_gateway import execution_spaces
from tests.test_workspace_local_model_execution import PROJECT


def completed(spaces,side,identifier,status):
    """Read the original account's exact durable task until the expected terminal state, rather than fabricating a row or treating a refusal as success."""
    for _ in range(200):
        row=spaces.ok(side,'GET','/agent-execution/runs/'+identifier)
        if row['status'] not in {'queued','running'}:break
        time.sleep(.05)
    assert row['status']==status,row
    return row


@pytest.mark.parametrize('side',['a','b'])
def test_public_failed_original_requires_acknowledged_new_retry_and_replays_only_that_attempt(execution_spaces,side):
    """A real authored500 retains the original project hold; another account and missing confirmation cannot retry, while one explicit own retry reuses its durable key."""
    spaces=execution_spaces;other='b' if side=='a' else 'a';path,original=spaces.intents[side]['connection']
    before=spaces.ok(side,'GET',f'/video-runtime/projects/{PROJECT}/budget')
    spaces.origins[side]['mode']='fail_once'
    failed=spaces.ok(side,'POST',path,json={**original,'request_key':'action-failed-'+side})
    completed(spaces,side,failed['id'],'failed')
    reserved=spaces.ok(side,'GET',f'/video-runtime/projects/{PROJECT}/budget')
    assert reserved['reserved_minor']==before['reserved_minor']+10 and reserved['spent_minor']==0
    retry_path='/agent-execution/runs/'+failed['id']+'/retry'
    body={'request_key':'action-retry-'+side,'expected_price_fingerprint':spaces.quotes[side]['text'].fingerprint,'acknowledge_duplicate_charge':True}
    counts=[len(spaces.origins[key]['calls']) for key in ('a','b')]
    assert spaces.request(other,'POST',retry_path,json=body).status_code==404
    assert spaces.request(side,'POST',retry_path,json={key:value for key,value in body.items() if key!='acknowledge_duplicate_charge'}).status_code==422
    assert [len(spaces.origins[key]['calls']) for key in ('a','b')]==counts
    retry=spaces.ok(side,'POST',retry_path,json=body)
    assert retry['retry_of']==failed['id'] and retry['attempt']==failed['attempt']+1
    completed(spaces,side,retry['id'],'succeeded')
    assert spaces.ok(side,'POST',retry_path,json=body)['id']==retry['id']
    assert [len(spaces.origins[key]['calls']) for key in ('a','b')]==[count+(1 if key==side else 0) for key,count in zip(('a','b'),counts)]
    after=spaces.ok(side,'GET',f'/video-runtime/projects/{PROJECT}/budget')
    assert after['reserved_minor']==before['reserved_minor']+20 and after['spent_minor']==0


@pytest.mark.parametrize('side',['a','b'])
def test_public_inflight_cancel_preserves_unknown_hold_and_cannot_cancel_other_identity(execution_spaces,side):
    """Hold one real owned HTTP response, reject cross-account cancel, then cancel the actual original task before releasing late bytes and replay its key without another POST."""
    spaces=execution_spaces;other='b' if side=='a' else 'a';path,original=spaces.intents[side]['connection']
    before=spaces.ok(side,'GET',f'/video-runtime/projects/{PROJECT}/budget')
    origin=spaces.origins[side];origin['blocked'].clear();origin['release'].clear();origin['mode']='block_once'
    counts=[len(spaces.origins[key]['calls']) for key in ('a','b')]
    body={**original,'request_key':'action-cancel-'+side}
    job=spaces.ok(side,'POST',path,json=body);cancel='/agent-execution/runs/'+job['id']+'/cancel'
    try:
        assert origin['blocked'].wait(timeout=4),'Owned HTTP request did not reach the controlled origin'
        assert spaces.request(other,'POST',cancel,json={}).status_code==404
        assert spaces.request(side,'POST',cancel,json={}).status_code==200
    finally:origin['release'].set()
    completed(spaces,side,job['id'],'cancelled')
    assert spaces.ok(side,'POST',path,json=body)['id']==job['id']
    assert [len(spaces.origins[key]['calls']) for key in ('a','b')]==[count+(1 if key==side else 0) for key,count in zip(('a','b'),counts)]
    after=spaces.ok(side,'GET',f'/video-runtime/projects/{PROJECT}/budget')
    assert after['reserved_minor']==before['reserved_minor']+10 and after['spent_minor']==0


@pytest.mark.parametrize('execution_spaces',['native'],indirect=True)
@pytest.mark.parametrize('side',['a','b'])
def test_public_native_pause_resume_and_cancel_never_resubmit_original_model(execution_spaces,side):
    """Control the actual accepted/querying native task in its original account, denying every foreign control and retaining its uncertain hold without another generation POST."""
    spaces=execution_spaces;other='b' if side=='a' else 'a';identifier=spaces.native_jobs[side]
    path=f'/execution/projects/{PROJECT}/jobs/'+identifier
    before=spaces.ok(side,'GET',f'/video-runtime/projects/{PROJECT}/budget')
    counts=[len(spaces.origins[key]['calls']) for key in ('a','b')]
    for action in ('pause','resume','cancel'):
        assert spaces.request(other,'POST',path+'/'+action,json={}).status_code==404
    for _ in range(100):
        response=spaces.request(side,'POST',path+'/pause',json={})
        if response.status_code==200:break
        assert response.status_code==409 and '正在处理' in response.json()['message'],response.text
        time.sleep(.05)
    assert response.status_code==200,response.text
    paused=response.json()['data']
    assert paused['state']=='paused' and paused['can_resume']
    resumed=spaces.ok(side,'POST',path+'/resume',json={})
    assert resumed['state'] in {'running','downloading'} and not resumed['can_resume']
    cancelled=spaces.ok(side,'POST',path+'/cancel',json={})
    assert cancelled['cancel_requested']
    assert [len(spaces.origins[key]['calls']) for key in ('a','b')]==counts
    after=spaces.ok(side,'GET',f'/video-runtime/projects/{PROJECT}/budget')
    assert after['reserved_minor']==before['reserved_minor'] and after['spent_minor']==0


@pytest.mark.parametrize('side',['a','b'])
def test_changed_connection_keeps_original_recovery_and_refuses_new_use_of_stale_quote(execution_spaces,side):
    """Mutate only the owned provider URL through its public API; an accepted original key still recovers without decrypting/sending another model request, and a new key cannot borrow the stale price binding."""
    spaces=execution_spaces;path,body=spaces.intents[side]['connection']
    counts=[len(spaces.origins[key]['calls']) for key in ('a','b')]
    before=spaces.ok(side,'GET',f'/video-runtime/projects/{PROJECT}/budget')
    spaces.ok(side,'PATCH','/llm/providers/same-provider',json={'base_url':spaces.origins[side]['origin']+'/changed-by-own-operator'})
    recovered=spaces.ok(side,'POST',path,json=body)
    assert recovered['id']==spaces.jobs[side]['connection']['id'] and recovered['status']=='succeeded'
    rejected=spaces.request(side,'POST',path,json={**body,'request_key':'action-stale-connection-'+side})
    assert rejected.status_code in {409,503},rejected.text
    assert [len(spaces.origins[key]['calls']) for key in ('a','b')]==counts
    after=spaces.ok(side,'GET',f'/video-runtime/projects/{PROJECT}/budget')
    assert after['reserved_minor']==before['reserved_minor'] and after['spent_minor']==0
