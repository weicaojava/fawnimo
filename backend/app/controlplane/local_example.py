"""Reserve the shared example quota, then ask only the authenticated space's private fixed worker."""
import asyncio

import httpx
from fastapi import HTTPException
from pydantic import ValidationError

from app.controlplane import demo
from app.core.contracts.local_example import LocalExampleInput, LocalExampleRead, LocalExampleStatus, example_identity
from app.core.owned_tasks import join_tasks
from app.services.worker.database_operation import joined_database


@joined_database
async def ledger(operation, *arguments, **options):
    """Join each short control-ledger thread; no tenant database is opened by the gateway."""
    return await asyncio.to_thread(operation,*arguments,**options)


def role(store, workspace, actor):
    """Attest the current real membership role instead of accepting it from a browser or stale session view."""
    with store.connect() as db:
        return demo._member(db,workspace,actor)


@joined_database
async def claim_control(store, workspace, actor, request_key, identity, witness):
    """Remember only this fully committed/closed control claim even if cancellation hides its return value."""
    row = await asyncio.to_thread(demo._claim,store,workspace,actor,request_key,identity['request_hash'],identity['project_id'],
        template_version=identity['template_version'])
    witness['row'] = row
    return row


def body(response):
    """Bound private responses and keep all upstream diagnostics out of public errors."""
    if response.status_code!=200:
        code = response.status_code if response.status_code in {403,409,429,503} else 502
        messages = {403:'当前身份没有建立示例的权限',409:'原示例尚未完整确认或内容冲突，请保留原请求核对',
            429:'本空间正在处理示例或已达到上限，请继续使用原请求',503:'当前空间尚未开放本地可导出示例',502:'示例响应尚未确认，请保留原请求核对'}
        raise HTTPException(code,messages[code])
    try:
        if len(response.content)>65536:
            raise ValueError('Bounded private response required')
        return response.json()['data']
    except (ValueError,KeyError,TypeError):
        raise HTTPException(502,'示例响应格式尚未确认，请保留原请求核对') from None


def checked_result(value, identity):
    """Only reflect strict safe fields for the exact server-derived target, never paths or arbitrary navigation."""
    try:
        result = LocalExampleRead.model_validate(value)
        if (result.project_id,result.chapter_id)!=(identity['project_id'],identity['chapter_id']) or result.entry!=f"/projects/{identity['project_id']}/editor?chapter={identity['chapter_id']}":
            raise ValueError('Private example target mismatch')
        return result.model_dump()
    except (ValueError,ValidationError,TypeError):
        raise HTTPException(502,'示例归属尚未确认，未继续建立或覆盖工程') from None


async def read_or_create(client, identity, *, read_only):
    """Read a committed typed Ready first; a completed control receipt can never re-enter rendering."""
    response = await client.get('/__workspace/original-example',params={'request_key':identity['request_key']})
    try:
        status = LocalExampleStatus.model_validate(body(response))
    except ValidationError:
        raise HTTPException(502,'示例历史响应尚未确认，请保留原请求') from None
    if status.state=='ready' and status.result is not None:
        return checked_result(status.result,identity)
    if read_only:
        raise HTTPException(409,'原示例完成记录对应的内容已缺失，未重新渲染或修复')
    response = await client.post('/__workspace/original-example',json={'request_key':identity['request_key']})
    return checked_result(body(response),identity)


async def create_exportable_demo(store, pool, current, request_key):
    """Persist one shared-quota intent before RPC; resolve lost acknowledgements from the worker's typed receipt."""
    try:
        LocalExampleInput(request_key=request_key)
    except (ValueError,ValidationError,TypeError):
        raise HTTPException(422,'示例请求标识需为8至128位字母、数字、下划线或短横线') from None
    try:
        workspace, actor = current['workspace_id'],current['user']['id']
    except (KeyError,TypeError):
        raise HTTPException(401,'请先登录') from None
    identity = example_identity(workspace,actor,request_key)
    await ledger(demo._schema,store)
    # Both modes intentionally use the same raw request-key namespace. Reusing
    # an old text-example key here conflicts, rather than claiming a second kind.
    row, claim_witness = None, {}
    try:
        row = await claim_control(store,workspace,actor,request_key,identity,claim_witness)
        succeeded = row['state']=='succeeded'
        worker = await pool.get(workspace)
        if worker.workspace_id!=workspace:
            raise HTTPException(503,'工作空间执行器身份不匹配，示例未继续保存')
        trusted_role = await ledger(role,store,workspace,actor)
        headers = {'X-Workspace-Token':worker.token,'X-Workspace-Id':workspace,'X-Identity-Id':actor,'X-Workspace-Role':trusted_role}
        async with httpx.AsyncClient(base_url=f'http://127.0.0.1:{worker.port}',headers=headers,
                timeout=httpx.Timeout(180,connect=3),trust_env=False,follow_redirects=False) as client:
            if succeeded:
                return await read_or_create(client,identity,read_only=True)
            stop = asyncio.Event()

            async def renew():
                """Fence long private rendering by the current real membership and control intent lease."""
                while True:
                    try:
                        await asyncio.wait_for(stop.wait(),timeout=10)
                        return
                    except asyncio.TimeoutError:
                        await ledger(demo._progress,store,row,0)

            rpc = asyncio.create_task(read_or_create(client,identity,read_only=False))
            heartbeat = asyncio.create_task(renew())
            try:
                done, _pending = await asyncio.wait((rpc,heartbeat),return_when=asyncio.FIRST_COMPLETED)
                if heartbeat in done:
                    await heartbeat
                    raise HTTPException(409,'示例控制请求归属已失效')
                result = await rpc
            finally:
                stop.set()
                await join_tasks(rpc,heartbeat,cancel=True)
        await ledger(demo._progress,store,row,1,succeeded=True)
        return result
    except BaseException as error:
        row = row or claim_witness.get('row')
        if row is not None and row['state']!='succeeded':
            await ledger(demo._uncertain,store,row)
        if isinstance(error,(HTTPException,asyncio.CancelledError)):
            raise
        raise HTTPException(502,'示例响应中断，已保留原请求；请核对原请求，避免重复新建') from None
