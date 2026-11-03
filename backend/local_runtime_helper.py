"""Fixed bounded stdin/stdout protocol for trusted runtime helpers; raw failures and credentials never leave the container."""
import json
import os
from pathlib import Path
import sys


def main():
    """Execute once and persist a private outcome before stdout, allowing read-only reconciliation of lost attachments."""
    os.environ['JELLYFISH_SKIP_ENV_FILE'] = '1'
    from app.services.community.events import unique_fields, invalid_number
    from app.services.community.runtime_release_helper import execute
    from app.services.community.local_release_files import write_json
    raw = sys.stdin.buffer.read(65537)
    if len(raw) > 65536: raise ValueError('Runtime helper frame exceeds its bound')
    value = json.loads(raw, object_pairs_hook=unique_fields, parse_constant=invalid_number)
    try: result = execute(value)
    except Exception:
        result = {'profile': 'local_runtime_helper_v1', 'operation_id': value.get('operation_id'),
                  'action': value.get('action'), 'ok': False, 'reason': 'owned_runtime_helper_refused',
                  'external_model_calls': 0, 'paid_authorization': 0}
    # Every result must survive container exit, including readiness. Probe
    # mounts a separate own result volume and keeps business data read-only.
    destination = Path('/target/result.json')
    write_json(destination, result)
    sys.stdout.buffer.write(json.dumps(result, sort_keys=True, separators=(',', ':')).encode() + b'\n')
    sys.stdout.buffer.flush()
    if not result['ok']: raise SystemExit(1)


if __name__ == '__main__': main()
