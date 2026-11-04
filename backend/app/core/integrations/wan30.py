"""One-create Wan3.0 async protocol with original-account reads, bounded downloads and no implicit recreation or cancellation call."""
import asyncio
import json
import re
import time
from urllib.parse import quote,urlsplit

from app.core.owned_tasks import join_tasks
from app.core.contracts.execution_artifact import DownloadedArtifact
from app.core.contracts.execution_control import QueryBackoff,RejectedSubmission
from app.core.contracts.seedance_references import snapshot_reference_selection
from app.core.contracts.wan30 import Wan30Configuration,CREATE_PATH,QUERY_PATH,REFERENCE_PROTOCOL,MAX_REQUEST_BYTES,MODEL_ID,valid_remote_id
from app.core.integrations.trusted_http import HttpResult,request_isolated_async
from app.core.integrations.video_protocol_json import finite_video_json,video_retry_delay
from app.core.integrations.wan30_payload import build_wan30_body

_STATES=frozenset({'PENDING','RUNNING','SUCCEEDED','FAILED','CANCELED','UNKNOWN'})


class Wan30ProtocolError(RuntimeError):
    """A fixed non-sensitive error; upstream prose, signed URLs and keys are never reflected."""


class Wan30DurableTransport:
    """Bind the exact private profile; injected requesters belong only to application-owned tests."""
    def __init__(self,config,*,project_id=None,selection=None,references=None,requester=None,clock=time.time,on_submission_response=None):
        """Construct without network or file access; control-only original descriptors cannot create reference tasks."""
        if not isinstance(config,Wan30Configuration):raise TypeError('Trusted Wan3.0 configuration required')
        self.config=config;self._project_id=project_id;self._selection=selection;self._references=references
        self._requester=requester;self._clock=clock;self._on_submission_response=on_submission_response

    def _network(self,hosts):
        """Keep the independently trusted fixture port separate from exact production host authority."""
        return {'allowed_hosts':set(hosts),'trusted_local_host':self.config.trusted_local_host,
            'trusted_local_ports':{self.config.trusted_local_port} if self.config.trusted_local_port is not None else set()}

    async def _http(self,url,**options):
        """Use the owned killable HTTP transport and conceal raw errors while preserving cancellation cleanup."""
        try:
            value=await(self._requester or request_isolated_async)(url,**options)
            if not isinstance(value,HttpResult):raise TypeError()
            return value
        except asyncio.CancelledError:raise
        except Exception:raise Wan30ProtocolError('Provider request not confirmed') from None

    async def _record_response(self,value):
        """Join only the original sanitized JSON request witness through owner cancellation before accepting a task handle."""
        if self._on_submission_response is None:return
        request_id=value.get('request_id') if isinstance(value,dict) else None
        if (not isinstance(request_id,str) or re.fullmatch(r'[A-Za-z0-9_.:-]{1,128}',request_id) is None
                or self.config.api_key in request_id):request_id=None
        task=asyncio.create_task(self._on_submission_response(request_id))
        result=(await join_tasks(task))[0]
        if isinstance(result,BaseException):raise result

    async def _control(self,path,*,method='GET',body=None):
        """Create once or query an exact opaque task path; redirects and all other control operations are unavailable."""
        if not((method=='POST' and path==CREATE_PATH) or(method=='GET' and path.startswith(QUERY_PATH) and valid_remote_id(path[len(QUERY_PATH):]))):
            raise Wan30ProtocolError('Unsupported provider control operation')
        data=json.dumps(body,ensure_ascii=False,separators=(',',':'),allow_nan=False).encode('utf8') if body is not None else None
        if data is not None and len(data)>MAX_REQUEST_BYTES:raise Wan30ProtocolError('Provider request exceeds its limit')
        headers={'Authorization':'Bearer '+self.config.api_key,'Content-Type':'application/json'}
        if method=='POST':headers['X-DashScope-Async']='enable'
        response=await self._http(self.config.base_url+path,method=method,data=data,headers=headers,
            timeout=15,max_bytes=65536,max_redirects=0,**self._network({self.config.control_host}))
        if type(response.status) is not int:raise Wan30ProtocolError('Invalid provider HTTP status')
        if response.status==429 and method=='GET':raise QueryBackoff(video_retry_delay(response.headers,self._clock()))
        try:value=finite_video_json(response.body)
        except ValueError:
            if method=='POST':await self._record_response(None)
            raise Wan30ProtocolError('Invalid provider JSON') from None
        if method=='POST':await self._record_response(value)
        if response.status!=200:raise Wan30ProtocolError('Provider HTTP request not confirmed')
        return value

    def _media_url(self,metadata):
        """Keep signed result URLs transient and confined to the exact official result host or owned fixture."""
        output=metadata.get('output');url=output.get('video_url') if isinstance(output,dict) else None
        if not isinstance(url,str) or not url or len(url)>16384 or any(ord(char)<=32 or ord(char)==127 for char in url):
            raise Wan30ProtocolError('Missing or invalid provider artifact')
        try:
            target=urlsplit(url);local=self.config.trusted_local_host is not None
            port=target.port or(443 if target.scheme=='https' else 80)
            if (target.scheme!=('http' if local else'https') or target.hostname not in self.config.download_hosts
                    or port!=(self.config.trusted_local_port if local else 443) or target.username is not None
                    or target.password is not None or target.fragment or '%' in(target.hostname or'')):
                raise ValueError()
            url.encode('utf8')
        except (ValueError,TypeError,UnicodeError):raise Wan30ProtocolError('Untrusted provider artifact') from None
        return url

    async def _getmeta(self,remote_id):
        """Read only the original opaque task/account; a vanished24-hour record stays unconfirmed and never causes recreation."""
        if not valid_remote_id(remote_id) or self.config.api_key in remote_id:raise Wan30ProtocolError('Invalid provider identity')
        value=await self._control(QUERY_PATH+quote(remote_id,safe=''))
        output=value.get('output')
        if (not isinstance(output,dict) or output.get('task_id')!=remote_id or not isinstance(output.get('task_status'),str)
                or output.get('task_status') not in _STATES
                or value.get('model',MODEL_ID)!=MODEL_ID):
            raise Wan30ProtocolError('Provider metadata does not match the task')
        if output['task_status']=='UNKNOWN':raise Wan30ProtocolError('Original provider task is no longer confirmed')
        if output['task_status']=='SUCCEEDED':self._media_url(value)
        return value

    async def submit(self,snapshot,request_id):
        """Attempt exactly one create from an original frozen input; only local rejection is definitely unaccepted."""
        try:
            if self.config.protocol==REFERENCE_PROTOCOL and(self._selection is None or self._references is None
                    or snapshot_reference_selection(snapshot)!=self._selection):raise ValueError()
            body=build_wan30_body(snapshot,protocol=self.config.protocol,project_id=self._project_id,
                prepared=self._references if self.config.protocol==REFERENCE_PROTOCOL else())
        except (ValueError,TypeError):raise RejectedSubmission('Unsupported original Wan3.0 request') from None
        value=await self._control(CREATE_PATH,method='POST',body=body);output=value.get('output')
        remote_id=output.get('task_id') if isinstance(output,dict) else None
        if not valid_remote_id(remote_id) or self.config.api_key in remote_id:raise Wan30ProtocolError('Missing or invalid provider identity')
        return remote_id

    async def reconcile(self,request_id):
        """No documented original-request lookup exists; an uncertain create is never automatically submitted again."""
        return None

    async def query(self,remote_id):
        """Persist only normalized state and a task locator, leaving raw prose and signed URLs outside history."""
        value=await self._getmeta(remote_id);state=value['output']['task_status']
        if state in {'PENDING','RUNNING'}:return {'status':'running'}
        if state=='SUCCEEDED':return {'status':'succeeded','locator':remote_id,'name':'wan30.mp4'}
        return {'status':'cancelled' if state=='CANCELED' else'failed'}

    async def cancel(self,remote_id):
        """Confirm only a remotely observed cancelled state; this profile has no remote DELETE or undocumented cancellation call."""
        value=await self._getmeta(remote_id)
        return value['output']['task_status']=='CANCELED'

    async def download(self,result,media_id,project_id):
        """Refresh the original result and download bounded bytes without forwarding control credentials to the result host."""
        if (not isinstance(result,dict) or set(result)!={'status','locator','name'} or result.get('status')!='succeeded'
                or result.get('name')!='wan30.mp4' or not valid_remote_id(result.get('locator'))):
            raise Wan30ProtocolError('Invalid local artifact locator')
        from app.config import settings
        maximum=settings.production_upload_limit
        if type(maximum) is not int or maximum<1:raise Wan30ProtocolError('Invalid trusted artifact limit')
        try:
            async with asyncio.timeout(45):
                metadata=await self._getmeta(result['locator'])
                if metadata['output']['task_status']!='SUCCEEDED':raise Wan30ProtocolError('Provider artifact is not ready')
                response=await self._http(self._media_url(metadata),method='GET',headers={},timeout=30,
                    max_bytes=maximum,max_redirects=3,**self._network(self.config.download_hosts))
                if type(response.status) is int and response.status==429:raise QueryBackoff(video_retry_delay(response.headers,self._clock()))
                if (type(response.status) is not int or response.status!=200 or not isinstance(response.body,bytes)
                        or not response.body or len(response.body)>maximum):raise Wan30ProtocolError('Provider artifact download not confirmed')
                return DownloadedArtifact('wan30.mp4',response.body)
        except (QueryBackoff,asyncio.CancelledError):raise
        except Exception:raise Wan30ProtocolError('Provider artifact download not confirmed') from None
