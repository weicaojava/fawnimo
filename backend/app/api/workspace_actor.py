"""Extract only the gateway-attested actor, preserving the standalone operator identity."""
from app.config import settings
from app.services.worker.model_charges import actor


def model_submitted_by(request):
    """Ignore public-looking actor headers in legacy mode; fixed workers already require the internal token."""
    return actor(request.headers.get('x-identity-id')) if settings.workspace_id is not None else 'local_operator'
