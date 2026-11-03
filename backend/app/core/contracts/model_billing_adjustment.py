"""Strict local operator decisions; no request can infer an account, refund or increased budget."""
from pydantic import BaseModel,ConfigDict,Field,StrictInt,field_validator


class BillingCorrectionWrite(BaseModel):
    """Bind one replacement invoice total to an explicit original receipt and observed chain revision."""

    model_config=ConfigDict(extra='forbid',strict=True)
    line_id: str=Field(min_length=1,max_length=64)
    reference_receipt_id: str=Field(min_length=1,max_length=64)
    expected_revision: StrictInt=Field(ge=0,le=2**62-2)
    reason: str=Field(min_length=4,max_length=500)

    @field_validator('reason')
    @classmethod
    def reason_line(cls,value):
        """Retain a deliberate bounded single-line explanation without control characters."""
        value=value.strip()
        if len(value)<4 or any(ord(char)<32 or ord(char)==127 for char in value):
            raise ValueError('A single-line operator reason is required')
        return value
