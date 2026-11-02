"""Database-free owned public-source helper; only fixed-host read requests are available."""
from dataclasses import asdict
import json
from pathlib import Path
import sys

sys.path.insert(0,str(Path(__file__).resolve().parents[3]))


def main():
    """Read bounded trusted-parent JSON and return a source observation, never a raw error, secret or command output."""
    from app.core.contracts.community_requests import CommunityIntakeConfiguration
    from app.services.community.public_source import inspect_public_issue, parse_public_json
    try:
        raw=sys.stdin.buffer.read(4097)
        if len(raw)>4096:raise ValueError()
        value=parse_public_json(raw)
        if set(value)!={'configuration','issue_number'}:raise ValueError()
        config=CommunityIntakeConfiguration.model_validate(value['configuration'])
        if config.repository_id is None:raise ValueError()
        proof=inspect_public_issue(config,value['issue_number'])
        sys.stdout.buffer.write(json.dumps(asdict(proof),ensure_ascii=False).encode('utf8'))
    except Exception:
        sys.stdout.buffer.write(b'{"error":"public_source_unavailable"}')
        raise SystemExit(1)


if __name__=='__main__':main()
