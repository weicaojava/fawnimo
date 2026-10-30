"""Construct a default-closed maintainer intake app without opening personal identity/workspace data."""
from pathlib import Path
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse

from app.api.community_intake import router
from app.core.contracts.community_requests import CommunityIntakeConfiguration
from app.services.community.store import CommunityRequestStore


def create_community_app(root: Path, *, configuration=None, secret: bytes | None = None):
    """Bind one explicit public source and independent secret; disabled construction performs no directory or DB writes."""
    config = configuration or CommunityIntakeConfiguration()
    if config.enabled and (not isinstance(secret, bytes) or not 32 <= len(secret) <= 512):
        raise ValueError('Enabled community intake requires an explicit private webhook secret')
    app = FastAPI(title='Fawnimo public requirement intake')
    app.state.community_configuration = config
    app.state.community_webhook_secret = secret if config.enabled else None
    app.state.community_store = CommunityRequestStore(root, config) if config.enabled else None
    app.include_router(router)

    @app.exception_handler(HTTPException)
    async def request_error(_request: Request, error: HTTPException):
        """Return only deliberate source-safe errors, never SQL, raw event contents or private operator configuration."""
        return JSONResponse(status_code=error.status_code,
            content={'code': error.status_code, 'message': error.detail, 'data': None}, headers={'Cache-Control': 'no-store'})

    @app.middleware('http')
    async def no_private_cache(request: Request, call_next):
        """Keep signed acknowledgements and setup status out of shared caches."""
        response = await call_next(request)
        response.headers.update({'Cache-Control': 'no-store', 'X-Content-Type-Options': 'nosniff'})
        return response

    @app.get('/health')
    async def health():
        """Expose process readiness only, with no source counts, raw requirements or configuration secrets."""
        return {'status': 'ok'}

    return app
