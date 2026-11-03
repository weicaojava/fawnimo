"""Forward one authenticated media capability to its fixed worker, with no user/model credentials or arbitrary target."""
import asyncio
import re

import httpx
from fastapi import HTTPException
from starlette.responses import JSONResponse,Response,StreamingResponse

from app.controlplane.reference_delivery import active_reference_workspace
from app.core.owned_tasks import join_tasks

MAX_REFERENCE_BYTES=32*1024*1024


async def close_reference_transport(client,upstream):
    """Join sequential owned response/client cleanup through repeated caller cancellation before releasing admission."""
    async def close():
        """Close the client even if its individual response reports an error."""
        try:
            if upstream is not None:await upstream.aclose()
        finally:
            if client is not None:await client.aclose()
    result=(await join_tasks(asyncio.create_task(close())))[0]
    if isinstance(result,BaseException):
        raise RuntimeError('Reference transport cleanup failed') from None


class ScopedReferenceProxyResponse(StreamingResponse):
    """Keep one gateway admission lane until ASGI send/disconnect and all owned upstream cleanup complete."""

    def __init__(self,content,*,client,upstream,release,**options):
        """Accept ownership only after every header, target and size has been checked."""
        super().__init__(content,**options)
        self.client,self.upstream,self.release=client,upstream,release

    async def __call__(self,scope,receive,send):
        """Release the lane after joined cleanup on success, failed sends, cancellation or a client disconnect."""
        try:
            await super().__call__(scope,receive,send)
        finally:
            try:
                await close_reference_transport(self.client,self.upstream)
            finally:
                self.release()


def reference_busy():
    """Report finite retry advice without exposing a workspace, file, path or private upstream error."""
    return JSONResponse(status_code=429,content={'code':429,'message':'参考素材读取繁忙','data':None},headers={'Retry-After':'1','Cache-Control':'no-store'})


class GatewayReferenceProxy:
    """Allow only signed fixed-space GET/HEAD; the original worker grant still checks exact bytes/attempt/state/count."""

    def __init__(self,authority,store,pool,*,capacity=4):
        """Keep a finite number of live response owners, without evicting workspaces or queueing unbounded readers."""
        if type(capacity) is not int or not 1<=capacity<=16:
            raise ValueError('Reference proxy capacity must be between1 and16')
        self.authority,self.store,self.pool=authority,store,pool
        self.lanes=asyncio.Semaphore(capacity)

    async def response(self,workspace_id,delivery_id,request):
        """Verify before control lookup/process selection; rewrite only this capability to one private loopback route."""
        raw=getattr(request.state,'reference_delivery_query',request.scope.get('query_string',b''))
        token=self.authority.authorize(workspace_id,delivery_id,raw)
        if request.method not in {'GET','HEAD'}:
            raise HTTPException(404,'参考素材不可用')
        if self.lanes.locked():return reference_busy()
        await self.lanes.acquire()
        client=None;upstream=None;transferred=False
        try:
            if not await asyncio.to_thread(active_reference_workspace,self.store,workspace_id):
                raise HTTPException(404,'参考素材不可用')
            worker=await self.pool.get(workspace_id)
            if getattr(worker,'workspace_id',None)!=workspace_id or type(worker.port) is not int or not 1024<=worker.port<=65535:
                raise HTTPException(503,'参考素材暂时不可用')
            headers={'X-Workspace-Token':worker.token,'X-Workspace-Id':workspace_id,'Accept':'video/mp4','Accept-Encoding':'identity'}
            value=request.headers.get('range')
            if value is not None:
                if len(value)>128:raise HTTPException(404,'参考素材不可用')
                headers['Range']=value
            client=httpx.AsyncClient(trust_env=False,follow_redirects=False,timeout=httpx.Timeout(30,connect=2,pool=1),
                limits=httpx.Limits(max_connections=1,max_keepalive_connections=0))
            target=f'http://127.0.0.1:{worker.port}/api/v1/execution-reference-media/{delivery_id}?token={token}'
            try:
                upstream=await client.send(client.build_request(request.method,target,headers=headers),stream=True)
            except httpx.HTTPError:
                raise HTTPException(503,'参考素材暂时不可用') from None
            if upstream.status_code==404:raise HTTPException(404,'参考素材不可用')
            if upstream.status_code==429:return reference_busy()
            if upstream.status_code==416:
                value=upstream.headers.get('content-range','')
                return Response(status_code=416,headers={'Content-Range':value} if re.fullmatch(r'bytes \*/[0-9]{1,8}',value) else {})
            length=upstream.headers.get('content-length','')
            if (upstream.status_code not in {200,206} or upstream.headers.get('content-type','').lower()!='video/mp4'
                    or upstream.headers.get('content-encoding','identity')!='identity' or re.fullmatch(r'[0-9]{1,8}',length) is None
                    or not 0<int(length)<=MAX_REFERENCE_BYTES):
                raise HTTPException(503,'参考素材暂时不可用')
            headers={'Content-Length':length,'Accept-Ranges':'bytes','Cache-Control':'no-store','Referrer-Policy':'no-referrer','X-Content-Type-Options':'nosniff'}
            if upstream.status_code==206:
                content_range=upstream.headers.get('content-range','');match=re.fullmatch(r'bytes ([0-9]{1,8})-([0-9]{1,8})/([0-9]{1,8})',content_range)
                if match is None:raise HTTPException(503,'参考素材暂时不可用')
                start,end,total=map(int,match.groups())
                if not 0<=start<=end<total<=MAX_REFERENCE_BYTES or end-start+1!=int(length):raise HTTPException(503,'参考素材暂时不可用')
                headers['Content-Range']=content_range
            elif 'content-range' in upstream.headers:
                raise HTTPException(503,'参考素材暂时不可用')
            async def chunks():
                """Bound actual forwarded bytes as well as declared size; suppress private transport exception context."""
                if request.method=='HEAD':return
                count=0
                try:
                    async for chunk in upstream.aiter_raw(chunk_size=64*1024):
                        count+=len(chunk)
                        if count>int(length):raise RuntimeError('Reference response exceeded its fixed size')
                        yield chunk
                except httpx.HTTPError:
                    raise RuntimeError('Reference response interrupted') from None
                if count!=int(length):raise RuntimeError('Reference response size mismatch')
            result=ScopedReferenceProxyResponse(chunks(),client=client,upstream=upstream,release=self.lanes.release,
                status_code=upstream.status_code,headers=headers,media_type='video/mp4')
            transferred=True
            return result
        finally:
            if not transferred:
                try:
                    await close_reference_transport(client,upstream)
                finally:
                    self.lanes.release()
