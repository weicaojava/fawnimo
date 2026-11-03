"""Bounded fixed-host HTTP protocol fixtures only; no test connects to GitHub or reads a user credential."""
import json
import pytest
from app.core.contracts.community_publication import CommunityPublicationPolicy
from app.services.community.publication_transport import GitHubPublicationTransport,PublicationRemoteError
from app.services.community.store import CommunityStoreConflict
from tests.test_community_publication import publication
from tests.test_community_review_gate_decisions import gate
from tests.test_community_change_bundles import prepared_change
from tests.test_community_queue_leases import factory


@pytest.fixture
def transport(publication,tmp_path,monkeypatch):
    """An authored fake credential and fake HTTPS connection prove destination/header/bounds without a real secret or network."""
    store,_body,_artifact,facts=publication; token=tmp_path/'fake-credential.txt'; token.write_text('authored_fake_github_credential_12345\n')
    requests=[]; state={'status':200,'raw':b'{}'}
    class Connection:
        """Capture only the fixed controller transport used by this test."""
        def __init__(self,host,timeout):
            """Every credential-bearing connection must use the literal trusted GitHub host."""
            assert host=='api.github.com' and 0<timeout<=12
        def request(self,method,path,body,headers):
            """Record internal request construction; never display the authorization header."""
            requests.append((method,path,body,headers))
        def getresponse(self):
            """Reuse this object as a bounded response fixture."""
            self.status=state['status']; return self
        def read(self,limit):
            """The controller must always enforce one fixed response-size boundary."""
            assert limit==1048577; return state['raw'][:limit]
        def close(self):
            """All success/failure paths close the one owned connection."""
            state['closed']=True
    monkeypatch.setattr('app.services.community.publication_transport.http.client.HTTPSConnection',Connection)
    return GitHubPublicationTransport(store.policy,token),requests,state,facts.candidate


def test_fixed_new_ref_and_draft_payload_never_mutate_main_or_accept_candidate_prose(transport):
    """Authenticated writes are limited to object creation/new reserved branch/fixed draft; there is no arbitrary URL, force or merge."""
    client,calls,state,candidate=transport; state['status']=201
    client.create_ref(candidate.branch,candidate.commit)
    assert calls[-1][0]=='POST' and calls[-1][1].endswith('/git/refs') and json.loads(calls[-1][2])=={'ref':'refs/heads/'+candidate.branch,'sha':candidate.commit}
    with pytest.raises(CommunityStoreConflict): client.create_ref('main',candidate.commit)
    client.create_pull(candidate.branch,type('Input',(),{'bundle_id':candidate.bundle_id,'check_attempt_id':'11111111-1111-4111-8111-111111111111'})())
    body=json.loads(calls[-1][2]); assert body['draft'] is True and body['base']=='main' and body['maintainer_can_modify'] is False
    assert state['closed'] and calls[-1][3]['Authorization'].startswith('Bearer authored_fake_')


@pytest.mark.parametrize('status,raw',[(302,b'{}'),(403,b'private-provider-prose'),(200,b'{"a":1,"a":2}'),(200,b'{"x":NaN}'),(200,b'x'*1048577)],ids=['redirect','forbidden','duplicate-json','nonfinite-json','response-overflow'])
def test_redirect_provider_errors_duplicate_or_oversized_responses_are_redacted(transport,status,raw):
    """No redirect is followed and no provider response/token/body becomes an error string."""
    client,calls,state,_candidate=transport; state.update(status=status,raw=raw)
    with pytest.raises(PublicationRemoteError) as error: client.repository()
    assert len(calls)==1 and 'private-provider' not in str(error.value) and 'authored_fake' not in str(error.value) and state['closed']


def test_exact_missing_ref_is_not_missing_permission_and_pagination_cannot_hide_original(transport):
    """Only a 404 ref GET means absence; error or full PR page cannot become permission for a duplicate create."""
    client,calls,state,candidate=transport; state['status']=404
    assert client.ref(candidate.branch) is None
    state.update(status=200,raw=json.dumps([{}]*100).encode())
    with pytest.raises(PublicationRemoteError): client.pulls(candidate.branch)
    state['status']=403
    with pytest.raises(PublicationRemoteError): client.ref(candidate.branch)


def test_disabled_policy_invalid_targets_and_request_deadline_do_not_load_or_send_credentials(transport,tmp_path):
    """Default closed transport refuses before opening a file; malformed fixed paths and exhausted request budgets do not connect."""
    policy=CommunityPublicationPolicy(repository_id=17,repository_name='authored/fawnimo')
    with pytest.raises(CommunityStoreConflict): GitHubPublicationTransport(policy,tmp_path/'not-created')
    client,calls,_state,_candidate=transport
    with pytest.raises(PublicationRemoteError): client._request('GET','https://untrusted.invalid/steal')
    client.requests=48
    with pytest.raises(PublicationRemoteError): client.repository()
    client.requests=0; client.deadline=0
    with pytest.raises(PublicationRemoteError): client.repository()
    assert calls==[]
