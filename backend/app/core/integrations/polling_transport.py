"""Explicit polling protocol adapter, configured by trusted application code only."""
import json
from dataclasses import dataclass
from urllib.parse import quote
from app.core.integrations.trusted_http import request_isolated_async
from app.core.contracts.execution_artifact import DownloadedArtifact


@dataclass(frozen=True)
class PollingConfiguration:
    """Endpoint and host trust are not loaded from prompts, project imports or DNA."""
    base_url: str
    allowed_hosts: frozenset[str]
    trusted_local_host: str | None=None
    trusted_local_ports: frozenset[int]=frozenset()


class PollingTransport:
    """Minimal documented jobs protocol; no claim of compatibility with a provider."""
    def __init__(self,config: PollingConfiguration):
        self.config=config

    async def _request(self,path: str, *, method='GET', body=None):
        """Use killable bounded HTTP for each configured control-plane call."""
        response=await request_isolated_async(self.config.base_url.rstrip('/')+path,method=method,data=json.dumps(body).encode() if body is not None else None,headers={'Content-Type':'application/json'},allowed_hosts=set(self.config.allowed_hosts),trusted_local_host=self.config.trusted_local_host,trusted_local_ports=set(self.config.trusted_local_ports),max_bytes=65536,timeout=15)
        if response.status!=200:raise RuntimeError('Control request not confirmed')
        result=json.loads(response.body)
        if not isinstance(result,dict):raise ValueError('Invalid control response')
        return result

    async def submit(self,snapshot: dict,request_id: str) -> str:
        """Send the stable request identity; ambiguous errors remain unknown."""
        result=await self._request('/jobs',method='POST',body={'request_id':request_id,'snapshot':snapshot})
        remote=result.get('id')
        if not isinstance(remote,str) or not remote or len(remote)>255:raise ValueError('Missing job identity')
        return remote

    async def reconcile(self,request_id: str) -> str | None:
        """Look up the original accepted request rather than creating a replacement."""
        result=await self._request('/requests/'+quote(request_id,safe=''))
        remote=result.get('id')
        if remote is not None and (not isinstance(remote,str) or not remote or len(remote)>255):raise ValueError('Invalid reconciled identity')
        return remote

    async def query(self,remote_id: str) -> dict:
        """Only allow defined protocol fields into the durable result journal."""
        result=await self._request('/jobs/'+quote(remote_id,safe=''))
        if set(result)-{'status','artifact_url','name','retry_after'} or result.get('status') not in {'running','succeeded','failed','cancelled'}:raise ValueError('Invalid query response')
        return result

    async def cancel(self,remote_id: str) -> bool:
        """A request acknowledgement alone is insufficient to confirm cancellation."""
        result=await self._request('/jobs/'+quote(remote_id,safe='')+'/cancel',method='POST',body={})
        return result.get('status')=='cancelled'

    async def download(self,result: dict,media_id: str,project_id: str) -> DownloadedArtifact:
        """Retrieve bounded bytes without forwarding any control-plane credentials."""
        from app.config import settings
        url=result.get('artifact_url');name=result.get('name')
        if not isinstance(url,str) or not isinstance(name,str):raise ValueError('Missing artifact metadata')
        response=await request_isolated_async(url,allowed_hosts=set(self.config.allowed_hosts),trusted_local_host=self.config.trusted_local_host,trusted_local_ports=set(self.config.trusted_local_ports),max_bytes=settings.production_upload_limit,timeout=30)
        if response.status!=200:raise RuntimeError('Artifact download not confirmed')
        return DownloadedArtifact(name,response.body)
