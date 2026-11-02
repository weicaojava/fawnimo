"""Database/credential-free gated fixed-host public PR reader; review bodies and source text are never executable instructions."""
from dataclasses import asdict
import json
from pathlib import Path
import sys

sys.path.insert(0,str(Path(__file__).resolve().parents[3]))


def main():
    """Read exact bounded parent input before GETs, project only safe metadata, then emit a bounded observation without secrets or side effects."""
    from app.core.contracts.community_requests import CommunityIntakeConfiguration
    from app.services.community.review_gate_source import parse_pr_json,inspect_public_pr
    try:
        raw=sys.stdin.buffer.read(16385)
        if len(raw)>16384: raise ValueError()
        value=parse_pr_json(raw)
        if set(value)!={'configuration','pr_number'}: raise ValueError()
        config=CommunityIntakeConfiguration.model_validate(value['configuration'])
        observed=inspect_public_pr(config,value['pr_number'])
        wire=json.dumps(asdict(observed),ensure_ascii=False,allow_nan=False).encode('utf8')
        if len(wire)>1048576: raise ValueError()
        sys.stdout.buffer.write(wire)
    except Exception:
        sys.stdout.buffer.write(b'{"error":"public_pr_unavailable"}')
        raise SystemExit(1)


if __name__=='__main__': main()
