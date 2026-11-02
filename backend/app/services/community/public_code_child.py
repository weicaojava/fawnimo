"""Database-free gated public code reader; it has no Git, credentials, model or arbitrary-host/command operation."""
import base64
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))


def main():
    """Read trusted bounded parent input before any network access, then return only exact public source observations."""
    from app.core.contracts.community_requests import CommunityIntakeConfiguration
    from app.services.community.public_source import parse_public_json
    from app.services.community.public_code import inspect_public_code
    try:
        raw = sys.stdin.buffer.read(16385)
        if len(raw) > 16384:
            raise ValueError()
        value = parse_public_json(raw)
        if set(value) != {'configuration', 'feature_ids', 'paths'}:
            raise ValueError()
        config = CommunityIntakeConfiguration.model_validate(value['configuration'])
        result = inspect_public_code(config, value['feature_ids'], value['paths'])
        snapshot = result.snapshot
        wire = {'protocol': result.protocol, 'repository_id': snapshot.repository_id, 'repository_name': snapshot.repository_name,
            'commit': snapshot.commit, 'tree': snapshot.tree, 'checked_at_us': result.checked_at_us, 'response_sha256': list(result.response_sha256),
            'files_base64': {path: base64.b64encode(content).decode('ascii') for path, content in snapshot.files.items()}}
        encoded = json.dumps(wire, ensure_ascii=False, allow_nan=False).encode('utf8')
        if len(encoded) > 8388608:
            raise ValueError()
        sys.stdout.buffer.write(encoded)
    except Exception:
        sys.stdout.buffer.write(b'{"error":"public_code_unavailable"}')
        raise SystemExit(1)


if __name__ == '__main__':
    main()
