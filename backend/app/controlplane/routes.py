"""Identity HTTP inputs and cookies; account policy lives in the isolated store."""
import asyncio
from fastapi import APIRouter, HTTPException, Request, Response

from app.core.contracts.identity import IdentityRead, IdentityStatus, LoginRequest, RegisterRequest, ProfileRequest, WorkspaceSwitch
from app.core.contracts.identity import DemoCreate, DemoRead
from app.schemas.common import ApiResponse, success_response

router = APIRouter(prefix='/identity', tags=['identity'])
COOKIE = 'studio_session'


def store_for(request: Request):
    """Fail closed when identity endpoints are called on an unmanaged local worker."""
    store = getattr(request.app.state, 'identity_store', None)
    if store is None:
        raise HTTPException(503, '此入口未运行身份服务')
    return store


def check_origin(request: Request) -> None:
    """Reject browser cross-origin writes; self-hosted clients use one public origin."""
    if request.headers.get('sec-fetch-site') == 'cross-site':
        raise HTTPException(403, '跨站请求已拒绝')
    origin = request.headers.get('origin')
    expected = getattr(request.app.state, 'public_origin', None) or str(request.base_url).rstrip('/')
    if origin and origin != expected:
        raise HTTPException(403, '请求来源不匹配')


def set_cookie(request: Request, response: Response, token: str) -> None:
    """Only browsers hold opaque HttpOnly cookies; HTTPS is the deployment default."""
    response.set_cookie(COOKIE, token, max_age=12 * 3600, httponly=True,
                        secure=getattr(request.app.state, 'secure_cookies', True), samesite='strict', path='/')
    response.headers['Cache-Control'] = 'no-store'


def current_context(request: Request, *, required: bool = True) -> dict:
    """Fence stale tabs even on identity routes that do not pass through the studio proxy."""
    current = store_for(request).current(request.cookies.get(COOKIE))
    user, workspace = request.headers.get('x-identity-id'), request.headers.get('x-workspace-id')
    if (required or user) and user != current['user']['id']:
        raise HTTPException(409, '登录身份已变化，请刷新后再操作')
    if (required or workspace) and workspace != current['workspace_id']:
        raise HTTPException(409, '工作空间已变化，请刷新后再操作')
    return current


@router.get('/status', response_model=ApiResponse[IdentityStatus])
def identity_status(request: Request):
    """Let the same built studio choose local entry or the real login flow."""
    enabled = hasattr(request.app.state, 'identity_store')
    return success_response(IdentityStatus(mode='workspace' if enabled else 'local', registration_enabled=enabled and getattr(request.app.state, 'allow_registration', False)))


@router.post('/login', response_model=ApiResponse[IdentityRead])
async def login(body: LoginRequest, request: Request, response: Response):
    """Authenticate one bounded attempt and start a new session without exposing its token."""
    check_origin(request)
    store = store_for(request)
    async with request.app.state.auth_slots:
        token = await asyncio.to_thread(store.login, body.account, body.password, request.client.host if request.client else 'unknown')
    # Session fixation is prevented and a login in this browser revokes the old session.
    await asyncio.to_thread(store.logout, request.cookies.get(COOKIE))
    set_cookie(request, response, token)
    return success_response(await asyncio.to_thread(store.current, token))


@router.post('/register', response_model=ApiResponse[IdentityRead])
async def register(body: RegisterRequest, request: Request, response: Response):
    """Provision only when the operator explicitly enables public account registration."""
    check_origin(request)
    store = store_for(request)
    if not request.app.state.allow_registration:
        raise HTTPException(403, '请向维护者领取独立体验账号')
    async with request.app.state.auth_slots:
        await asyncio.to_thread(store.provision, body.account, body.password, body.display_name)
        token = await asyncio.to_thread(store.login, body.account, body.password, request.client.host if request.client else 'unknown')
    await asyncio.to_thread(store.logout, request.cookies.get(COOKIE))
    set_cookie(request, response, token)
    return success_response(await asyncio.to_thread(store.current, token))


@router.get('/me', response_model=ApiResponse[IdentityRead])
def me(request: Request, response: Response):
    """Recheck membership and return only the current user's safe identity fields."""
    response.headers['Cache-Control'] = 'no-store'
    return success_response(current_context(request, required=False))


@router.patch('/me', response_model=ApiResponse[IdentityRead])
def profile(body: ProfileRequest, request: Request):
    """Update personal preferences without accepting role, ownership or identity changes."""
    check_origin(request)
    current_context(request)
    return success_response(store_for(request).profile(request.cookies.get(COOKIE), body.model_dump(exclude_none=True)))


@router.post('/switch', response_model=ApiResponse[IdentityRead])
def switch(body: WorkspaceSwitch, request: Request):
    """Switch only to a current membership; the client reloads before accessing new data."""
    check_origin(request)
    current_context(request)
    return success_response(store_for(request).switch(request.cookies.get(COOKIE), body.workspace_id))


@router.post('/logout', response_model=ApiResponse[dict])
def logout(request: Request, response: Response):
    """Persistently revoke the session and remove the browser's cookie."""
    check_origin(request)
    current_context(request)
    store_for(request).logout(request.cookies.get(COOKIE))
    response.delete_cookie(COOKIE, path='/', secure=getattr(request.app.state, 'secure_cookies', True), httponly=True, samesite='strict')
    response.headers['Cache-Control'] = 'no-store'
    return success_response({})


@router.post('/demo', response_model=ApiResponse[DemoRead])
async def create_example(body: DemoCreate, request: Request):
    """Create a fresh editable example, preserving all previous examples and user edits."""
    check_origin(request)
    identity = current_context(request)
    from app.controlplane.demo import create_demo
    return success_response(await create_demo(store_for(request), request.app.state.workers, identity, body.request_key))
