"""Authored Wan3.0 transport boundaries, original outcomes and cleanup; no actual cloud/model or bill qualification."""
import asyncio
from copy import deepcopy
from email.utils import formatdate
import json

import pytest

from app.core.contracts.execution_control import QueryBackoff,RejectedSubmission
from app.core.contracts.wan30 import CREATE_PATH,QUERY_PATH,REFERENCE_PROTOCOL
from app.core.contracts.seedance_references import VideoReferenceSelection
from app.core.integrations.trusted_http import HttpResult
from app.core.integrations.wan30 import Wan30DurableTransport,Wan30ProtocolError
from tests.test_wan30_protocol import configuration
from tests.test_wan30_payload import text_snapshot,image_proof,reference_snapshot

CDN='https://dashscope-result-bj.oss-cn-beijing.aliyuncs.com/authored.mp4?token=AUTHORED_PRIVATE_RESULT'


def reply(value,status=200,headers=None):
    """Return bounded authored bytes, never a vendor observation."""
    return HttpResult(status,headers or{},value if isinstance(value,bytes) else json.dumps(value).encode('utf8'))


def metadata(state='SUCCEEDED',**changes):
    """Declare only the documented output identity/state and an authored ephemeral URL."""
    return {'request_id':'authored-read-witness','output':{'task_id':'authored-task','task_status':state,'video_url':CDN,**changes}}


@pytest.mark.asyncio
async def test_one_create_original_queries_download_and_read_only_cancel_keep_boundaries():
    """Only the original create records a witness; reads/download cannot replay it or forward credentials to the result host."""
    calls=[];witnesses=[];states=iter(['PENDING','RUNNING','SUCCEEDED','SUCCEEDED','CANCELED'])
    async def request(url,**options):
        """Emulate exact original control and result endpoints while checking all transport authority."""
        calls.append((url,options))
        assert options['max_redirects']==(3 if url==CDN else 0)
        if url==CDN:
            assert options['headers']=={} and options['allowed_hosts']=={'dashscope-result-bj.oss-cn-beijing.aliyuncs.com'}
            return reply(b'AUTHORED_TRANSPORT_BYTES')
        assert options['allowed_hosts']=={'authored-space.cn-beijing.maas.aliyuncs.com'}
        assert options['headers']['Authorization']=='Bearer AUTHORED_WAN_CREDENTIAL'
        if options['method']=='POST':
            assert url.endswith(CREATE_PATH) and options['headers']['X-DashScope-Async']=='enable'
            assert json.loads(options['data'])['parameters']['duration']==4
            return reply({'request_id':'authored-create-witness','output':{'task_id':'authored-task','task_status':'PENDING'}})
        assert url.endswith(QUERY_PATH+'authored-task') and 'X-DashScope-Async' not in options['headers']
        return reply(metadata(next(states)))
    async def observe(identifier):
        """Capture the sanitized original response witness only."""
        witnesses.append(identifier)
    transport=Wan30DurableTransport(configuration(),requester=request,on_submission_response=observe)
    assert await transport.submit(text_snapshot(),'original-request')=='authored-task'
    assert await transport.reconcile('original-request') is None
    assert await transport.query('authored-task')=={'status':'running'}
    assert await transport.query('authored-task')=={'status':'running'}
    result=await transport.query('authored-task')
    assert result=={'status':'succeeded','locator':'authored-task','name':'wan30.mp4'} and 'token' not in json.dumps(result)
    artifact=await transport.download(result,'authored-media','authored-project')
    assert artifact.data==b'AUTHORED_TRANSPORT_BYTES' and artifact.name=='wan30.mp4'
    assert await transport.cancel('authored-task') is True
    assert witnesses==['authored-create-witness'] and sum(row[1]['method']=='POST' for row in calls)==1


@pytest.mark.parametrize('status',[400,401,403,429,500])
@pytest.mark.asyncio
async def test_ambiguous_create_errors_retain_one_original_response_without_retry(status):
    """Even error responses can retain their original witness; no response prose grants definite nonacceptance or a second create."""
    calls=[];witnesses=[]
    async def request(url,**options):
        """Supply authored remote-error data containing a marker that must never escape the fixed error."""
        calls.append(url);return reply({'request_id':'authored-error-witness','message':'AUTHORED_PRIVATE_ERROR'},status)
    async def observe(value):
        """Record only the public-protocol request identifier."""
        witnesses.append(value)
    transport=Wan30DurableTransport(configuration(),requester=request,on_submission_response=observe)
    with pytest.raises(Wan30ProtocolError) as caught:await transport.submit(text_snapshot(),'original-request')
    assert str(caught.value)=='Provider HTTP request not confirmed' and 'AUTHORED_PRIVATE' not in str(caught.value)
    assert witnesses==['authored-error-witness'] and len(calls)==1
    assert await transport.reconcile('original-request') is None and len(calls)==1


@pytest.mark.parametrize('value',[
    {'output':{'task_id':'other-task','task_status':'RUNNING'}},
    {'output':{'task_id':'authored-task','task_status':[]}},
    {'output':{'task_id':'authored-task','task_status':'UNKNOWN'}},
    {'output':{'task_id':'authored-task','task_status':'SUCCEEDED','video_url':'https://untrusted.example/private'}},
    {'model':'other-model','output':{'task_id':'authored-task','task_status':'RUNNING'}},
])
@pytest.mark.asyncio
async def test_wrong_unknown_or_untrusted_read_metadata_stays_unconfirmed(value):
    """Expired24-hour records and mismatched identities cannot masquerade as a failed/settled original job or result."""
    calls=[]
    async def request(url,**options):
        """Return one deliberately invalid original-task response."""
        calls.append(options['method']);return reply(value)
    transport=Wan30DurableTransport(configuration(),requester=request)
    with pytest.raises(Wan30ProtocolError):await transport.query('authored-task')
    assert calls==['GET']


@pytest.mark.parametrize('header,expected',[('0',5),('45',45),('bad',30),('9'*30,30)])
@pytest.mark.asyncio
async def test_rate_limited_original_reads_pause_without_recreating(header,expected):
    """Retry-After applies to a read deadline rather than granting a new submission."""
    calls=[]
    async def request(url,**options):
        """Return an authored429 for the original read."""
        calls.append(options['method']);return reply({},429,{'Retry-After':header})
    transport=Wan30DurableTransport(configuration(),requester=request)
    with pytest.raises(QueryBackoff) as caught:await transport.query('authored-task')
    assert caught.value.retry_after_seconds==expected and calls==['GET']


@pytest.mark.parametrize('offset,expected',[(35,35),(-10,5)])
@pytest.mark.asyncio
async def test_http_date_query_pause_uses_the_original_owned_clock(offset,expected):
    """HTTP-date deadlines and already elapsed hints cannot create a negative or unbound query delay."""
    now=1700000000
    async def request(url,**options):
        """Return a deterministic authored HTTP-date hint without any wall-clock dependency."""
        return reply({},429,{'Retry-After':formatdate(now+offset,usegmt=True)})
    transport=Wan30DurableTransport(configuration(),requester=request,clock=lambda:now)
    with pytest.raises(QueryBackoff) as caught:await transport.query('authored-task')
    assert caught.value.retry_after_seconds==expected


@pytest.mark.asyncio
async def test_local_rejection_and_control_only_reference_descriptor_never_make_a_create():
    """Restoring an original-account read descriptor does not restore its absent original image authority."""
    calls=[]
    async def request(url,**options):
        """Only a control read is expected from this incomplete original reference descriptor."""
        calls.append(options['method']);return reply(metadata('RUNNING'))
    transport=Wan30DurableTransport(configuration(protocol=REFERENCE_PROTOCOL),requester=request)
    assert await transport.query('authored-task')=={'status':'running'}
    proof=image_proof();snapshot=reference_snapshot((proof,),('first_frame',),'first_frame')
    with pytest.raises(RejectedSubmission):await transport.submit(snapshot,'original-request')
    assert calls==['GET']


@pytest.mark.asyncio
async def test_reference_creation_uses_only_exact_prepared_original_selection():
    """One explicitly selected first frame is serialized without a text fallback or changed project/content."""
    proof=image_proof();snapshot=reference_snapshot((proof,),('first_frame',),'first_frame');bodies=[]
    async def request(url,**options):
        """Capture the exact authored wire body while supplying a valid opaque task handle."""
        bodies.append(json.loads(options['data']));return reply({'output':{'task_id':'authored-task'}})
    transport=Wan30DurableTransport(configuration(protocol=REFERENCE_PROTOCOL),project_id='authored-project',
        selection=VideoReferenceSelection.model_validate(snapshot['video_reference']),references=(proof,),requester=request)
    assert await transport.submit(snapshot,'original-request')=='authored-task'
    changed=deepcopy(snapshot);changed['video_reference']['references'][0]['expected_sha256']='a'*64
    with pytest.raises(RejectedSubmission):await transport.submit(changed,'other-request')
    assert len(bodies)==1 and bodies[0]['input']['media'][0]['type']=='first_frame'


@pytest.mark.asyncio
async def test_owner_cancellation_joins_the_original_response_witness():
    """A late create-response witness settles its own private journal before the caller observes completed cancellation."""
    entered=asyncio.Event();release=asyncio.Event();witnesses=[];posts=[]
    async def request(url,**options):
        """Return one immediate authored response and never issue any outside request."""
        posts.append(options['method']);return reply({'request_id':'authored-create-witness','output':{'task_id':'authored-task'}})
    async def observe(value):
        """Block the original witness so parent cancellation races its durable completion."""
        entered.set();await release.wait();witnesses.append(value)
    transport=Wan30DurableTransport(configuration(),requester=request,on_submission_response=observe)
    task=asyncio.create_task(transport.submit(text_snapshot(),'original-request'))
    await asyncio.wait_for(entered.wait(),2);task.cancel();await asyncio.sleep(0)
    assert not task.done();release.set()
    result=(await asyncio.gather(task,return_exceptions=True))[0]
    assert isinstance(result,asyncio.CancelledError) and witnesses==['authored-create-witness'] and posts==['POST']


@pytest.mark.parametrize('raw',[b'{"output":{},"output":{}}',b'{"x":NaN}',b'\xff',b' '*65537],
    ids=['duplicate-fields','nonfinite','invalid-utf8','oversized-frame'])
@pytest.mark.asyncio
async def test_ambiguous_json_never_becomes_a_task_or_echoed_error(raw):
    """Duplicate fields, nonfinite values and invalid/oversized data retain an unconfirmed create with a null witness."""
    witnesses=[]
    async def request(url,**options):
        """Return only the authored invalid frame."""
        return reply(raw)
    async def observe(value):
        """A malformed body supplies no verified request witness."""
        witnesses.append(value)
    transport=Wan30DurableTransport(configuration(),requester=request,on_submission_response=observe)
    with pytest.raises(Wan30ProtocolError,match='Invalid provider JSON'):await transport.submit(text_snapshot(),'original-request')
    assert witnesses==[None]
