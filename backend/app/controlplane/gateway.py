"""Same-origin identity gateway and authenticated streaming proxy for isolated workspaces."""
from __future__ import annotations

import asyncio
import json
from contextlib import asynccontextmanager
from pathlib import Path
import re

import httpx
from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse, Response, StreamingResponse
from starlette.datastructures import QueryParams

from app.controlplane.routes import COOKIE, check_origin, router
from app.controlplane.store import IdentityStore
from app.controlplane.workers import WorkspaceWorkers
from app.core.contracts.workspace_reference_delivery import WORKSPACE_REFERENCE_PREFIX
from app.core.contracts.workspace_image_admission import image_submission_path
from app.controlplane.image_admission import ImageAdmissionPolicy

WRITE_METHODS = {'POST', 'PUT', 'PATCH', 'DELETE'}
REQUEST_LIMIT = 70 * 1024 * 1024
LEGACY_PATHS = ('/api/v1/film/', '/api/v1/script-processing/', '/api/v1/studio/image-tasks')
BILLABLE = (
    r'/api/v1/agent-execution/(?:agents/[^/]+/runs|models/[^/]+/connection-tests|runs/[^/]+/retry)$',
    r'/api/v1/asset-image-generation/(?:assets/.+/jobs|jobs/[^/]+/retry)$',
    r'/api/v1/video-understanding/projects/[^/]+/jobs(?:/[^/]+/retry)?$',
    r'/api/v1/execution/projects/[^/]+/submissions$',
    r'/api/v1/shot-plans/projects/[^/]+/plans/[^/]+/shots/[^/]+/submissions$',
    r'/api/v1/script-preparation/projects/[^/]+/chapters/[^/]+/candidates$',
)
CLOSED_AVAILABILITY_PATHS = frozenset({'/api/v1/agent-execution/availability','/api/v1/asset-image-generation/availability'})


def create_gateway(root: Path, *, frontend: Path | None = None, probe: Path | None = None,
                   secure_cookies: bool = True, public_origin: str | None = None,
                   allow_registration: bool = False, capacity: int = 4, workers=None,trial_registration_policy=None,reference_media_origin=None) -> FastAPI:
    """Build an independent control plane; never import or open the legacy studio database."""
    from app.controlplane.maintenance import validate_gateway_paths
    validate_gateway_paths(root,frontend)
    if reference_media_origin is not None:
        from app.controlplane.reference_delivery import validate_reference_origin
        validate_reference_origin(reference_media_origin,public_origin,secure_cookies)
    store = IdentityStore(root)
    image_admission_policy=ImageAdmissionPolicy(root)
    pool = workers or WorkspaceWorkers(root / 'workspaces', probe=probe, capacity=capacity)
    reference_authority=None;reference_proxy=None
    if reference_media_origin is not None:
        from app.controlplane.reference_delivery import GatewayReferenceAuthority
        from app.controlplane.reference_proxy import GatewayReferenceProxy
        reference_authority=GatewayReferenceAuthority(root,reference_media_origin,public_origin=public_origin,secure_cookies=secure_cookies)
        reference_proxy=GatewayReferenceProxy(reference_authority,store,pool,capacity=capacity)
    from app.core.contracts.identity_trials import TrialRegistrationPolicy
    from app.controlplane.trials import IdentityBodyLimit
    registration_policy=TrialRegistrationPolicy() if trial_registration_policy is None else TrialRegistrationPolicy.model_validate(trial_registration_policy)
    if isinstance(pool,WorkspaceWorkers):
        pool.trial_policy_provider=store.workspace_trial_policy
        pool.reference_policy_provider=reference_authority.policy_for_workspace if reference_authority is not None else None
        pool.image_admission_provider=image_admission_policy.startup_ticket

    @asynccontextmanager
    async def lifespan(application):
        """Keep worker process ownership bound to this gateway lifetime."""
        try:
            yield
        finally:
            await pool.close()

    app = FastAPI(title='Fawnimo workspace identity gateway', lifespan=lifespan)
    app.state.identity_store = store
    app.state.allow_registration = allow_registration
    app.state.trial_registration_policy=registration_policy
    app.state.secure_cookies = secure_cookies
    app.state.public_origin = public_origin
    app.state.auth_slots = asyncio.Semaphore(4)
    app.state.workers = pool
    app.state.reference_delivery_authority=reference_authority
    app.add_middleware(IdentityBodyLimit)
    app.include_router(router, prefix='/api/v1')

    @app.exception_handler(RequestValidationError)
    async def invalid_input(request: Request, error: RequestValidationError):
        """Do not echo passwords or private payloads through framework validation errors."""
        return JSONResponse(status_code=422, content={'code': 422, 'message': '输入字段无效，请检查账号、密码或设置格式', 'data': None}, headers={'Cache-Control': 'no-store'})

    @app.exception_handler(HTTPException)
    async def error_response(request: Request, error: HTTPException):
        """Return safe user-facing errors without SQL, paths or upstream connection details."""
        return JSONResponse(status_code=error.status_code, content={'code': error.status_code, 'message': str(error.detail), 'data': None}, headers={'Cache-Control': 'no-store'})

    @app.middleware('http')
    async def private_cache_headers(request: Request, call_next):
        """Never cache private media/identity responses or allow active uploaded HTML to run inline."""
        reference_path=request.scope.get('path','').startswith((WORKSPACE_REFERENCE_PREFIX,'/api/v1/execution-reference-media/'))
        if reference_path:
            request.state.reference_delivery_query=request.scope.get('query_string',b'')
            request.scope['query_string']=b''
        response = await call_next(request)
        if reference_path:response.headers['Referrer-Policy']='no-referrer'
        if request.url.path.startswith('/api/'):
            response.headers['Cache-Control'] = 'no-store'
            response.headers['Vary'] = 'Cookie, X-Workspace-Id, X-Identity-Id'
            response.headers['X-Content-Type-Options'] = 'nosniff'
            if 'text/html' in response.headers.get('content-type', '') or 'image/svg+xml' in response.headers.get('content-type', ''):
                response.headers['Content-Disposition'] = 'attachment'
                response.headers['Content-Security-Policy'] = "sandbox; default-src 'none'"
        return response

    @app.get('/health')
    async def health():
        """Expose only public process health, never workspace counts, paths or identities."""
        return {'status': 'ok'}

    @app.api_route(WORKSPACE_REFERENCE_PREFIX+'{workspace_id}/{delivery_id}',methods=['GET','HEAD'],include_in_schema=False)
    async def reference_media(workspace_id:str,delivery_id:str,request:Request):
        """Permit one signed read only; this route cannot browse projects, submit work or borrow a cookie's workspace."""
        if reference_proxy is None or b'%' in request.scope.get('raw_path',b''):
            raise HTTPException(404,'参考素材不可用')
        return await reference_proxy.response(workspace_id,delivery_id,request)

    @app.api_route('/api/v1/{path:path}', methods=['GET', 'HEAD', 'POST', 'PUT', 'PATCH', 'DELETE'])
    async def proxy(path: str, request: Request):
        """Authenticate every download/task/config request before selecting a private fixed worker."""
        # The ASGI path has already been decoded once. A residual percent escape
        # could be decoded again by the proxy target and change policy routing.
        if '%' in path or '\\' in path or any(ord(value) < 32 or ord(value) == 127 for value in path) or any(segment in {'.', '..'} for segment in path.split('/')):
            raise HTTPException(400, '接口路径无效')
        if path.startswith('workspace-reference-media'):
            raise HTTPException(404,'参考素材不可用')
        identity = await asyncio.to_thread(store.current, request.cookies.get(COOKIE))
        workspace = identity['workspace_id']
        supplied_workspace, supplied_user = request.headers.get('x-workspace-id'), request.headers.get('x-identity-id')
        saved_reference_query=getattr(request.state,'reference_delivery_query',None)
        query_params=QueryParams(saved_reference_query) if saved_reference_query is not None else request.query_params
        media_workspaces, media_users = query_params.getlist('__studio_workspace'), query_params.getlist('__studio_user')
        if len(media_workspaces) > 1 or len(media_users) > 1:
            raise HTTPException(400, '媒体范围无效')
        if media_workspaces and media_workspaces[0] != workspace or media_users and media_users[0] != identity['user']['id']:
            raise HTTPException(409, '媒体所属身份或空间已变化，请刷新页面')
        if (supplied_workspace and supplied_workspace != workspace) or (supplied_user and supplied_user != identity['user']['id']):
            raise HTTPException(409, '登录身份或工作空间已变化，请刷新后再操作')
        if (supplied_workspace or (media_workspaces[0] if media_workspaces else None)) != workspace or (supplied_user or (media_users[0] if media_users else None)) != identity['user']['id']:
            raise HTTPException(409, '请先刷新页面，确认当前身份与工作空间')
        write = request.method in WRITE_METHODS
        if write:
            check_origin(request)
            if supplied_workspace != workspace or supplied_user != identity['user']['id']:
                raise HTTPException(409, '请先刷新页面，确认当前工作空间')
            if identity['role'] == 'viewer':
                raise HTTPException(403, '此工作空间只有查看权限')
            from app.controlplane.trials import require_trial_write
            require_trial_write(identity)
        route = '/api/v1/' + path
        policy_route = route.rstrip('/')
        if route.rstrip('/').startswith('/api/v1/video-runtime/workspace-budget'):
            raise HTTPException(410, '预算已改为按项目管理，请进入项目设置')
        if write and re.fullmatch(r'/api/v1/video-runtime/projects/[^/]+/budget/?', route) and identity['role'] != 'owner':
            raise HTTPException(403, '只有所有者可以调整项目预算')
        if write and (route.startswith(LEGACY_PATHS) or path in {'film', 'script-processing'}):
            raise HTTPException(503, '旧共享执行入口尚未通过空间隔离验收')
        image_ticket=None
        if write and (any(re.fullmatch(pattern, policy_route) for pattern in BILLABLE) or policy_route.endswith('/callbacks')):
            image_ticket=image_admission_policy.current_ticket(workspace) if image_submission_path(request.method,policy_route) else None
            if image_ticket is None:raise HTTPException(503, '收费模型尚未启用：项目费用与专用服务配置仍待验收，当前可编辑与本地制作')
        body = bytearray()
        async for chunk in request.stream():
            body.extend(chunk)
            if len(body) > REQUEST_LIMIT:
                raise HTTPException(413, '上传内容超过体验空间上限')
        worker = await pool.get(workspace)
        if image_ticket is not None:
            if (getattr(worker,'image_admission',None)!=image_ticket or image_admission_policy.current_ticket(workspace)!=image_ticket):
                raise HTTPException(503,'图片执行授权尚未确认或已变化，请联系维护者核对；原任务和项目费用保留')
        receipt=None
        if write:
            if image_ticket is not None:receipt=await asyncio.to_thread(store.audit_start,identity,request.method,route,bytes(body),image_admission=image_ticket)
            else:receipt=await asyncio.to_thread(store.audit_start,identity,request.method,route,bytes(body))
        headers = {key: value for key, value in request.headers.items() if key.lower() in {
            'content-type', 'range', 'if-range', 'accept', 'accept-language',
        }}
        headers.update({'X-Workspace-Token': worker.token, 'X-Workspace-Id': workspace, 'X-Identity-Id': identity['user']['id'], 'X-Workspace-Role': identity['role'], 'Accept-Encoding': 'identity'})
        if receipt:
            headers['X-Submission-Receipt'] = receipt
        if image_ticket is not None:headers['X-Image-Admission-Policy']=image_ticket.grant.policy_sha256
        client = httpx.AsyncClient(trust_env=False, follow_redirects=False, timeout=httpx.Timeout(180, connect=5))
        upstream = None
        try:
            target = f'http://127.0.0.1:{worker.port}' + route
            query=saved_reference_query.decode('ascii') if saved_reference_query is not None else request.url.query
            if query:
                target += '?' + query
            upstream = await client.send(client.build_request(request.method, target, content=bytes(body), headers=headers), stream=True)
        except BaseException as error:
            if upstream:
                await upstream.aclose()
            await client.aclose()
            # Receipt remains unresolved: a lost response must not be mistaken for a rejected write.
            if isinstance(error, httpx.HTTPError):
                raise HTTPException(502, '工作空间响应中断；请用原请求标识核对结果') from None
            raise
        try:
            if receipt:
                await asyncio.to_thread(store.audit_finish, receipt, upstream.status_code)
        except BaseException:
            await upstream.aclose()
            await client.aclose()
            raise
        opened_image_availability=(route.rstrip('/')=='/api/v1/asset-image-generation/availability'
            and image_admission_policy.current_ticket(workspace) is not None
            and getattr(worker,'image_admission',None)==image_admission_policy.current_ticket(workspace))
        if request.method=='GET' and (route.rstrip('/') in CLOSED_AVAILABILITY_PATHS or re.fullmatch(r'/api/v1/script-preparation/projects/[^/]+/chapters/[^/]+/candidates/availability/?',route)) and upstream.status_code==200 and not opened_image_availability:
            # Read-only quotes never open unapproved execution paths. Only the
            # explicit current image policy can expose that worker's readiness.
            try:
                content=bytearray()
                async for chunk in upstream.aiter_raw():
                    if len(content)+len(chunk)>2*1024*1024:
                        raise ValueError('Bounded availability response required')
                    content.extend(chunk)
                envelope=json.loads(content)
                if not isinstance(envelope,dict) or not isinstance(envelope.get('data'),dict):
                    raise ValueError('Availability response required')
                envelope['data'].update(available=False,message='当前身份空间的收费执行尚未开放；可查看原模型费用上界与项目预算')
                return JSONResponse(envelope,headers={'Cache-Control':'no-store'})
            except (ValueError,TypeError):
                raise HTTPException(502,'模型状态响应尚未确认，请刷新核对；没有提交模型调用') from None
            finally:
                await upstream.aclose()
                await client.aclose()
        response_headers = {key: value for key, value in upstream.headers.items() if key.lower() in {
            'content-type', 'content-disposition', 'content-length', 'content-range', 'accept-ranges', 'etag', 'last-modified',
        }}
        if receipt:
            response_headers['X-Submission-Receipt'] = receipt

        async def stream():
            """Hold upstream ownership until the browser finishes or disconnects, including video ranges."""
            try:
                async for chunk in upstream.aiter_raw():
                    yield chunk
            finally:
                await upstream.aclose()
                await client.aclose()
        return StreamingResponse(stream(), status_code=upstream.status_code, headers=response_headers)

    @app.get('/{path:path}', include_in_schema=False)
    async def spa(path: str):
        """Serve only built frontend files; authenticated private bytes always go through the API."""
        if path.startswith(('api/', '__workspace/')) or not frontend:
            raise HTTPException(404, '页面不存在')
        public = frontend.resolve()
        candidate = (public / path).resolve()
        if not candidate.is_relative_to(public):
            raise HTTPException(404, '页面不存在')
        if path == 'env.js':
            return Response("window.__ENV = { BACKEND_URL: '' };", media_type='application/javascript', headers={'Cache-Control': 'no-store'})
        if candidate.is_file():
            return FileResponse(candidate)
        if '.' in Path(path).name:
            raise HTTPException(404, '文件不存在')
        index = public / 'index.html'
        if not index.is_file():
            raise HTTPException(503, '前端构建尚未准备好')
        return FileResponse(index, headers={'Cache-Control': 'no-store'})

    return app
