"""Private fixed-worker example RPC; it is outside the browser proxy and generated public OpenAPI."""
import asyncio
import hmac
import re

from fastapi import APIRouter, HTTPException, Query, Request, Response

from app.config import settings
from app.core.contracts.local_example import LocalExampleInput
from app.core.db import async_session_maker
from app.core.owned_tasks import join_tasks
from app.schemas.common import success_response
from app.services.studio.local_example import LocalExampleService

router = APIRouter(prefix='/__workspace/original-example',include_in_schema=False)


def private_service(request):
    """Require one private token plus matching fixed workspace and gateway-attested editor identity."""
    if settings.workspace_id is None or not settings.workspace_internal_token:
        raise HTTPException(503,'此本地入口不接受独立空间示例请求')
    headers = {}
    for name in ('x-workspace-token','x-workspace-id','x-identity-id','x-workspace-role'):
        values = request.headers.getlist(name)
        if len(values)!=1:
            raise HTTPException(403,'私有示例请求身份不完整')
        headers[name] = values[0]
    if not hmac.compare_digest(headers['x-workspace-token'].encode('utf8'),settings.workspace_internal_token.encode('utf8')) or headers['x-workspace-id']!=settings.workspace_id:
        raise HTTPException(403,'私有示例请求归属不匹配')
    if not re.fullmatch(r'usr_[a-f0-9]{32}',headers['x-identity-id']) or headers['x-workspace-role'] not in {'owner','editor'}:
        raise HTTPException(403,'当前身份没有建立示例的权限')
    service = getattr(request.app.state,'local_example_service',None)
    if service is None:
        service = LocalExampleService(async_session_maker,settings.workspace_id)
        request.app.state.local_example_service = service
    return service, headers['x-identity-id']


async def while_connected(request, operation):
    """Cancel private render work on actual disconnect and join its root/files/database before returning."""
    task = asyncio.create_task(operation)

    async def disconnected():
        """Watch only this already-parsed request, without inspecting any user's other session or browser."""
        while not await request.is_disconnected():
            await asyncio.sleep(.25)

    watch = asyncio.create_task(disconnected())
    try:
        done, _pending = await asyncio.wait((task,watch),return_when=asyncio.FIRST_COMPLETED)
        if task in done:
            return await task
        raise HTTPException(409,'示例请求连接已中断，请使用原请求标识核对')
    finally:
        await join_tasks(task,watch,cancel=True)


@router.post('')
async def create_original_example(body: LocalExampleInput, request: Request, response: Response):
    """Create only a server-derived new target under the fixed worker's single render lane."""
    service, actor = private_service(request)
    response.headers['Cache-Control'] = 'no-store'
    return success_response(await while_connected(request,service.create(actor,body.request_key)))


@router.get('')
async def original_example_status(request: Request, response: Response,
        request_key: str = Query(pattern=r'^[A-Za-z0-9_-]{8,128}$')):
    """Recover typed history read-only; this GET can never render or repair missing media."""
    service, actor = private_service(request)
    response.headers['Cache-Control'] = 'no-store'
    return success_response(await service.status(actor,request_key))
