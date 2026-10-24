"""Canonical trusted configuration fingerprints; never hash or store secret values."""
import hashlib
import json
from typing import TYPE_CHECKING
if TYPE_CHECKING:
    from app.services.worker.execution_admission import AdmissionPolicy


def binding_fingerprint(policy: 'AdmissionPolicy') -> str:
    """Bind jobs to a trusted service/account version and complete capability/quote."""
    if not isinstance(policy.binding_id,str) or not policy.binding_id.strip() or len(policy.binding_id)>255:
        raise ValueError('Trusted binding version identifier required')
    cap=policy.capability
    config={'binding_id':policy.binding_id,'model_id':cap.model_id,'durations':sorted(cap.durations),'reference_kinds':sorted(cap.reference_kinds),'max_references':cap.max_references,'quote_minor':policy.quote_minor,'currency':policy.currency,'iteration_limit':policy.iteration_limit}
    return hashlib.sha256(json.dumps(config,sort_keys=True,separators=(',',':'),ensure_ascii=False,allow_nan=False).encode('utf8')).hexdigest()
