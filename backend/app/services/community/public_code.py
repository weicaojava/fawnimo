"""Fixed-host unauthenticated public Git snapshot reads; never use a private worktree, Git credentials or issue-supplied URLs."""
import base64
from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
import re
import sys
import time

from app.core.contracts.community_changes import code_path
from app.services.community.change_bundles import PublicCodeSnapshot, PROTECTED, CANDIDATE_TEST, verify_public_snapshot
from app.services.community.events import unique_fields, invalid_number, finite_float
from app.services.community.public_source import github_public_get, parse_public_json
from app.services.community.queue_planning import catalog_features
from app.services.community.store import CommunityStoreConflict

CODE_PROTOCOL = 'github-public-code-rest-2022-11-28-v1'


@dataclass(frozen=True)
class PublicCodeObservation:
    """A short-lived read of immutable public objects and the then-current main ref, not code-execution authority."""
    snapshot: PublicCodeSnapshot
    checked_at_us: int
    response_sha256: tuple[str, ...]
    protocol: str = CODE_PROTOCOL

    def receipt(self):
        """Keep only public identity/hash witnesses in an audit receipt; file content belongs to the inert change bundle."""
        return {'protocol': self.protocol, 'repository_id': self.snapshot.repository_id,
            'repository_name': self.snapshot.repository_name.casefold(), 'commit': self.snapshot.commit, 'tree': self.snapshot.tree,
            'checked_at_us': self.checked_at_us, 'response_sha256': list(self.response_sha256),
            'selected_public_files': verify_public_snapshot(self.snapshot)}


def git_blob_id(raw):
    """Verify exact SHA1 Git blob bytes for this explicitly SHA1-only protocol, independently of a JSON filename or hash claim."""
    return hashlib.sha1(b'blob ' + str(len(raw)).encode('ascii') + b'\0' + raw).hexdigest()


def inspect_public_code(config, feature_ids, paths, *, reader=None, clock_us=None):
    """Read fixed public repo/main/commit/tree/blobs and recheck main, with no redirects, credentials, writes or automatic retries."""
    if (config.repository_id is None or not isinstance(feature_ids, list) or not 1 <= len(feature_ids) <= 32
            or any(not isinstance(item, str) or re.fullmatch(r'F-\d{3}\.\d{2}', item) is None for item in feature_ids)
            or len(set(feature_ids)) != len(feature_ids) or not isinstance(paths, list) or not 1 <= len(paths) <= 16 or any(not isinstance(path, str) for path in paths)
            or len(set(paths)) != len(paths)):
        raise CommunityStoreConflict('Invalid bounded public code request')
    for path in paths:
        code_path(path)
        if not path.endswith('.py') or path.startswith(PROTECTED):
            raise CommunityStoreConflict('Public code request targets a protected controller')
    get = reader or github_public_get
    prefix = '/repos/' + config.repository_name
    hashes = []

    def read(path):
        """All caller-independent REST paths share the existing strict1MiB public JSON and fixed-host transport."""
        raw = get(path)
        value = parse_public_json(raw)
        hashes.append(hashlib.sha256(raw).hexdigest())
        return value

    repository = read(prefix)
    if (type(repository.get('id')) is not int or repository['id'] != config.repository_id
            or not isinstance(repository.get('full_name'), str) or repository['full_name'].casefold() != config.repository_name.casefold()
            or repository.get('private') is not False or repository.get('visibility') != 'public'
            or repository.get('archived') is not False or repository.get('disabled') is not False):
        raise CommunityStoreConflict('Public code repository identity or availability changed')
    ref = read(prefix + '/git/ref/heads/main')
    if ref.get('ref') != 'refs/heads/main' or ref.get('object', {}).get('type') != 'commit' or re.fullmatch(r'[0-9a-f]{40}', ref['object'].get('sha', '')) is None:
        raise CommunityStoreConflict('Public main ref is not an exact supported commit')
    head = ref['object']['sha']
    commit = read(prefix + '/git/commits/' + head)
    if commit.get('sha') != head or re.fullmatch(r'[0-9a-f]{40}', commit.get('tree', {}).get('sha', '')) is None:
        raise CommunityStoreConflict('Public code commit identity changed')
    tree = commit['tree']['sha']
    listing = read(prefix + '/git/trees/' + tree + '?recursive=1')
    if listing.get('sha') != tree or listing.get('truncated') is not False or not isinstance(listing.get('tree'), list) or not 1 <= len(listing['tree']) <= 4096:
        raise CommunityStoreConflict('Public code tree is incomplete or unsupported')
    entries = {}
    for item in listing['tree']:
        if not isinstance(item, dict) or not isinstance(item.get('path'), str) or item['path'] in entries:
            raise CommunityStoreConflict('Ambiguous public tree entry')
        entries[item['path']] = item

    def blob(path, maximum):
        """Only exact regular100644 files enter this first edit profile; links, submodules and executable mode changes are excluded."""
        entry = entries.get(path)
        if (entry is None or entry.get('type') != 'blob' or entry.get('mode') != '100644'
                or re.fullmatch(r'[0-9a-f]{40}', entry.get('sha', '')) is None
                or type(entry.get('size')) is not int or not 0 <= entry['size'] <= maximum):
            raise CommunityStoreConflict('Public edit file is absent, redirected or outside the regular-file profile')
        result = read(prefix + '/git/blobs/' + entry['sha'])
        if (result.get('sha') != entry['sha'] or result.get('encoding') != 'base64'
                or type(result.get('size')) is not int or result['size'] != entry['size'] or not isinstance(result.get('content'), str)):
            raise CommunityStoreConflict('Public blob identity or encoding changed')
        try:
            raw = base64.b64decode(result['content'].replace('\n', ''), validate=True)
        except (ValueError, UnicodeError):
            raise CommunityStoreConflict('Public blob bytes are invalid') from None
        if len(raw) != entry['size'] or git_blob_id(raw) != entry['sha']:
            raise CommunityStoreConflict('Public blob content does not match its exact Git object')
        return raw

    files = {'capabilities/features.json': blob('capabilities/features.json', 700000)}
    registry = parse_public_json(files['capabilities/features.json'])
    features = catalog_features(registry)
    registered = set()
    for identifier in feature_ids:
        if identifier not in features or not isinstance(features[identifier].get('code'), list) or any(not isinstance(path, str) for path in features[identifier]['code']):
            raise CommunityStoreConflict('Unknown original registered change feature')
        registered.update(features[identifier]['code'])
    for path in paths:
        if path not in registered and CANDIDATE_TEST.fullmatch(path) is None:
            raise CommunityStoreConflict('Unregistered public source target')
        if path in entries:
            files[path] = blob(path, 131072)
        elif CANDIDATE_TEST.fullmatch(path) is None:
            raise CommunityStoreConflict('Registered public source file is absent')
    again = read(prefix + '/git/ref/heads/main')
    if again.get('ref') != 'refs/heads/main' or again.get('object', {}).get('type') != 'commit' or again['object'].get('sha') != head:
        raise CommunityStoreConflict('Public main ref changed during source collection')
    now = time.time_ns() // 1000 if clock_us is None else clock_us
    if type(now) is not int or now < 0:
        raise CommunityStoreConflict('Invalid public code observation clock')
    snapshot = PublicCodeSnapshot(config.repository_id, config.repository_name.casefold(), head, tree, files)
    verify_public_snapshot(snapshot)
    return PublicCodeObservation(snapshot, now, tuple(hashes))


def parse_code_observation(raw, config, *, now=None):
    """Decode only the owned child's exact bounded wire shape; supplied URLs/commands or stale authority are never accepted."""
    if type(raw) is not bytes or not 1 <= len(raw) <= 8388608:
        raise CommunityStoreConflict('Invalid public code observation size')
    try:
        value = json.loads(raw.decode('utf8'), object_pairs_hook=unique_fields, parse_constant=invalid_number, parse_float=finite_float)
        if set(value) != {'protocol', 'repository_id', 'repository_name', 'commit', 'tree', 'files_base64', 'checked_at_us', 'response_sha256'}:
            raise ValueError()
        if not isinstance(value['files_base64'], dict) or not 1 <= len(value['files_base64']) <= 17:
            raise ValueError()
        snapshot = PublicCodeSnapshot(value['repository_id'], value['repository_name'], value['commit'], value['tree'],
            {path: base64.b64decode(encoded, validate=True) for path, encoded in value['files_base64'].items()})
        verify_public_snapshot(snapshot)
        moment = time.time_ns() // 1000 if now is None else now
        if (value['protocol'] != CODE_PROTOCOL or snapshot.repository_id != config.repository_id or snapshot.repository_name.casefold() != config.repository_name.casefold()
                or type(value['checked_at_us']) is not int or type(moment) is not int or not 0 <= moment - value['checked_at_us'] <= 60000000
                or not isinstance(value['response_sha256'], list) or not 6 <= len(value['response_sha256']) <= 23
                or any(not isinstance(item, str) or re.fullmatch(r'[0-9a-f]{64}', item) is None for item in value['response_sha256'])):
            raise ValueError()
        return PublicCodeObservation(snapshot, value['checked_at_us'], tuple(value['response_sha256']))
    except (ValueError, TypeError, KeyError, AttributeError, UnicodeError, RecursionError):
        raise CommunityStoreConflict('Public code could not be verified') from None


async def verify_public_code(config, feature_ids, paths):
    """Own the fixed read-only helper for45 seconds with an OS-only environment and finite input/output."""
    from app.core.owned_process import run_owned_process
    allowed = {'SYSTEMROOT', 'WINDIR', 'COMSPEC', 'PATH', 'PATHEXT', 'TEMP', 'TMP', 'TMPDIR', 'LANG', 'LC_ALL', 'TZ'}
    environment = {name: value for name, value in os.environ.items() if name.upper() in allowed}
    environment.update(PYTHONUTF8='1', PYTHONDONTWRITEBYTECODE='1')
    data = json.dumps({'configuration': config.model_dump(), 'feature_ids': feature_ids, 'paths': paths}).encode('utf8')
    if len(data) > 16384:
        raise CommunityStoreConflict('Public source helper input exceeds bound')
    raw, code = await run_owned_process([sys.executable, str(Path(__file__).with_name('public_code_child.py'))], data,
        timeout=45, max_stdout_bytes=8388608, environment=environment)
    if code:
        raise CommunityStoreConflict('Public code is unavailable; no automatic retry')
    result = parse_code_observation(raw, config)
    if not set(result.snapshot.files) <= set(paths) | {'capabilities/features.json'} or 'capabilities/features.json' not in result.snapshot.files:
        raise CommunityStoreConflict('Public code helper returned unrequested files')
    return result
