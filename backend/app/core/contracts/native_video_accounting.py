"""Explicit native generation confirmation never accepts client-supplied amounts or connection identities."""
from pydantic import BaseModel,Field,StrictBool


class NativeVideoConfirmation(BaseModel):
    """Require exact displayed whole-call pricing in managed mode, while old inert request histories remain parseable."""
    confirm_project_charges: StrictBool=False
    expected_price_fingerprint: str | None=Field(default=None,pattern=r'^[a-f0-9]{64}$')
