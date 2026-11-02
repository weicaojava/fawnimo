"""A capability-only media read for an actual original video attempt; no user session or model credential is forwarded."""
from urllib.parse import parse_qs

from fastapi import APIRouter, HTTPException, Request

router = APIRouter(prefix='/execution-reference-media')


async def hide_reference_delivery_tokens(request: Request, call_next):
    """Remove capability queries from the ASGI access-log scope before routing or identity-boundary rejection."""
    if request.scope.get('path', '').startswith('/api/v1/execution-reference-media/'):
        request.state.reference_delivery_query = request.scope.get('query_string', b'')
        request.scope['query_string'] = b''
    return await call_next(request)


@router.api_route('/{delivery_id}', methods=['GET', 'HEAD'], include_in_schema=False)
async def read_execution_reference_media(delivery_id: str, request: Request):
    """Read exactly one original fixed video by its private bounded capability, without opening a project or selecting a workspace."""
    raw = getattr(request.state, 'reference_delivery_query', request.scope.get('query_string', b''))
    request.scope['query_string'] = b''
    runtime = getattr(request.app.state, 'execution_runtime', None)
    worker = getattr(runtime, 'worker', None)
    accountant = getattr(worker, 'native_accounting', None)
    service = getattr(accountant, 'reference_delivery', None)
    if service is None or not getattr(accountant, 'reference_delivery_available', False) or len(raw) > 256:
        raise HTTPException(404, '参考素材不可用')
    try:
        values = parse_qs(raw.decode('ascii'), strict_parsing=True, keep_blank_values=True)
        if set(values) != {'token'} or len(values['token']) != 1:
            raise ValueError()
        token = values['token'][0]
    except (UnicodeError, ValueError, KeyError):
        raise HTTPException(404, '参考素材不可用') from None
    return await service.response(delivery_id, token, method=request.method, range_header=request.headers.get('range'))
