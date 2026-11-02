"""Build reproducible inert changes from a trusted public snapshot; a proposal and candidate tests grant no execution or review authority."""
from dataclasses import dataclass
import difflib
import hashlib
import json
import re

from app.core.contracts.community_changes import CommunityChangeInput
from app.services.community.evaluations import canonical, digest
from app.services.community.queue_planning import catalog_features
from app.services.community.store import CommunityStoreConflict

CHANGE_PROFILE = 'public_python_change_v1'
MAX_BUNDLE_BYTES = 8388608
PROTECTED = ('backend/app/core/', 'backend/app/controlplane/', 'backend/app/api/',
    'backend/app/services/community/change_', 'backend/app/services/community/development_',
    'backend/app/services/community/candidate_', 'backend/app/services/community/isolated_', 'backend/app/services/community/code_check',
    'backend/app/services/community/review_gate', 'backend/app/services/community/public_source',
    'backend/app/services/community/public_code', 'backend/develop_community.py', 'backend/community_isolated_runner.py',
    'backend/app/services/community/source_check_', 'backend/app/services/community/store.py',
    'backend/app/services/community/queue_store.py', 'backend/app/services/community/queue_schema.py',
    'backend/app/services/community/queue_lease', 'backend/app/services/community/rowid_',
    'backend/app/services/community/evaluations.py', 'backend/app/services/community/events.py')
CANDIDATE_TEST = re.compile(r'backend/tests/test_community_candidate_[a-z0-9_]+\.py')


def protected_code_path(path):
    """Protect every backend top-level entry point, including future controllers, even if a feature register associates it with a candidate task."""
    return path.startswith(PROTECTED) or path.startswith('backend/') and path.count('/') == 1


@dataclass(frozen=True)
class PublicCodeSnapshot:
    """Server-owned public Git identity and regular file bytes, collected from committed objects rather than a dirty checkout."""
    repository_id: int
    repository_name: str
    commit: str
    tree: str
    files: dict[str, bytes]


def bytes_hash(raw):
    """Bind exact committed/source bytes, without treating text as commands or normalizing a mismatched old file."""
    return hashlib.sha256(raw).hexdigest()


def original_change_hash(body):
    """A stable original request can recover only the exact same scope, replacement bytes and test list."""
    if type(body) is not CommunityChangeInput:
        raise CommunityStoreConflict('Strict original change input required')
    typed = CommunityChangeInput.model_validate(body.model_dump())
    return digest({'domain': CHANGE_PROFILE, 'input': typed.model_dump()})


def verify_public_snapshot(snapshot):
    """Reject unknown object formats, excessive copies and non-regular/binary values before any candidate assembly."""
    if (not isinstance(snapshot, PublicCodeSnapshot) or type(snapshot.repository_id) is not int or snapshot.repository_id < 1
            or re.fullmatch(r'[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+', snapshot.repository_name) is None
            or re.fullmatch(r'[0-9a-f]{40}', snapshot.commit) is None or re.fullmatch(r'[0-9a-f]{40}', snapshot.tree) is None
            or not isinstance(snapshot.files, dict) or not 1 <= len(snapshot.files) <= 4096
            or any(not isinstance(path, str) or type(raw) is not bytes for path, raw in snapshot.files.items())
            or sum(len(raw) for raw in snapshot.files.values()) > 33554432):
        raise CommunityStoreConflict('Invalid trusted public code snapshot')
    return {path: bytes_hash(raw) for path, raw in sorted(snapshot.files.items())}


def build_change_bundle(body, prepared, snapshot, registry, *, observations=None):
    """Bind an inert source bundle to exact prepared provenance and registered edit paths; trusted callers establish current source first."""
    intent = original_change_hash(body)
    before = verify_public_snapshot(snapshot)
    features = catalog_features(registry)
    try:
        stored_registry = json.loads(snapshot.files['capabilities/features.json'].decode('utf8'))
    except (KeyError, ValueError, UnicodeError):
        raise CommunityStoreConflict('Trusted public snapshot lacks its exact feature register') from None
    if digest(stored_registry) != digest(registry):
        raise CommunityStoreConflict('Public code and feature register snapshot differ')
    if (not isinstance(prepared, dict) or prepared.get('id') != body.work_id
            or prepared.get('current', {}).get('state') != 'prepared'
            or prepared.get('profile') != 'preparation_lease_v1'
            or prepared.get('execution_authorized') is not False or prepared.get('development_complete') is not False):
        raise CommunityStoreConflict('Exact completed preparation provenance required; it is not execution permission')
    work = prepared.get('work', {})
    entry = work.get('entry', {})
    if (not isinstance(entry.get('feature_ids'), list) or not entry['feature_ids'] or not entry.get('acceptance')
            or not entry.get('request_id') or not entry.get('version_id') or not entry.get('evaluation_id')
            or re.fullmatch(r'[0-9a-f]{64}', entry.get('source_fingerprint', '')) is None
            or re.fullmatch(r'[0-9a-f]{64}', work.get('plan_hash', '')) is None
            or re.fullmatch(r'[0-9a-f]{64}', entry.get('evaluation_hash', '')) is None
            or type(entry.get('evaluation_sequence')) is not int or entry['evaluation_sequence'] < 1
            or not work.get('plan_id') or type(work.get('selection_revision')) is not int or work['selection_revision'] < 1):
        raise CommunityStoreConflict('Incomplete original preparation scope')
    registered = set()
    for identifier in entry['feature_ids']:
        if identifier not in features or not isinstance(features[identifier].get('code'), list):
            raise CommunityStoreConflict('Unknown original change feature')
        if any(not isinstance(path, str) for path in features[identifier]['code']):
            raise CommunityStoreConflict('Invalid registered edit path')
        registered.update(features[identifier]['code'])
    replacements = {}; records = []; patch = []
    for change in body.changes:
        test = CANDIDATE_TEST.fullmatch(change.path) is not None
        if (protected_code_path(change.path) or not (test or change.path in registered)
                or (test and (change.action != 'write' or change.before_sha256 is not None or change.path in before))):
            raise CommunityStoreConflict('Change target is outside the registered bounded edit profile')
        if before.get(change.path) != change.before_sha256:
            raise CommunityStoreConflict('Original public file hash changed or new-file intent is incorrect')
        old = snapshot.files.get(change.path, b'')
        if len(old) > 131072:
            raise CommunityStoreConflict('Original file exceeds the bounded edit profile')
        try:
            old_text = old.decode('utf8')
        except UnicodeError:
            raise CommunityStoreConflict('Original edit target is not supported UTF8 text') from None
        if '\x00' in old_text or (old_text and not old_text.endswith('\n')):
            raise CommunityStoreConflict('Original edit target is outside the supported text profile')
        raw = None if change.action == 'delete' else change.text.encode('utf8')
        if raw == old:
            raise CommunityStoreConflict('Proposed file has no change')
        replacements[change.path] = raw
        records.append({'path': change.path, 'before_sha256': change.before_sha256,
            'before_text': old_text if change.before_sha256 is not None else None,
            'after_sha256': None if raw is None else bytes_hash(raw), 'action': change.action, 'text': change.text})
        patch.extend(difflib.unified_diff(old_text.splitlines(keepends=True), (change.text or '').splitlines(keepends=True),
            fromfile='a/' + change.path if change.before_sha256 is not None else '/dev/null',
            tofile='b/' + change.path if raw is not None else '/dev/null'))
    if any(CANDIDATE_TEST.fullmatch(path) is None or path not in replacements or replacements[path] is None for path in body.test_files):
        raise CommunityStoreConflict('Candidate tests must be explicit newly supplied bounded test files')
    after = dict(before)
    for path, raw in replacements.items():
        if raw is None:
            after.pop(path)
        else:
            after[path] = bytes_hash(raw)
    scope = {'repository_id': snapshot.repository_id, 'repository_name': snapshot.repository_name.casefold(),
        'base_commit': snapshot.commit, 'base_tree': snapshot.tree, 'work_id': body.work_id,
        'request_id': entry['request_id'], 'version_id': entry['version_id'], 'source_fingerprint': entry['source_fingerprint'],
        'evaluation_id': entry['evaluation_id'], 'evaluation_hash': entry['evaluation_hash'],
        'evaluation_sequence': entry['evaluation_sequence'], 'plan_id': work['plan_id'], 'plan_hash': work['plan_hash'],
        'selection_revision': work['selection_revision'], 'feature_ids': sorted(entry['feature_ids']), 'acceptance': entry['acceptance']}
    scope['feature_catalog_hash'] = digest(registry)
    scope['registered_code_paths'] = sorted(registered)
    payload = {'schema_version': 1, 'profile': CHANGE_PROFILE, 'input': body.model_dump(), 'intent_hash': intent,
        'scope': scope, 'scope_hash': digest(scope), 'title': body.title, 'files': records,
        'source_observations': observations,
        'base_code_manifest': before, 'base_code_manifest_hash': digest(before),
        'candidate_code_manifest': after, 'candidate_code_manifest_hash': digest(after),
        'candidate_test_files': body.test_files, 'candidate_tests_are_independent': False,
        'patch': ''.join(patch), 'execution_authorized': False, 'development_complete': False,
        'independent_review_completed': False, 'ci_qualified': False, 'public_deployment_verified': False,
        'external_model_calls': 0, 'paid_authorization': 0}
    checksum = digest(payload)
    if len(canonical(payload).encode('utf8')) > MAX_BUNDLE_BYTES:
        raise CommunityStoreConflict('Complete inert bundle exceeds8MiB')
    return {'bundle_id': checksum, 'content_hash': checksum, 'branch': 'community/change-' + checksum[:24], 'bundle': payload}


def verify_change_bundle(value):
    """Expose one bounded validation failure for malformed retained data without echoing candidate source or paths."""
    try:
        return _verify_change_bundle(value)
    except (ValueError, TypeError, KeyError, AttributeError, UnicodeError, RecursionError):
        raise CommunityStoreConflict('Retained change bundle is invalid or changed') from None


def _verify_change_bundle(value):
    """Verify retained original bytes and every derived hash without accepting any embedded approval as authority."""
    if not isinstance(value, dict) or set(value) != {'bundle_id', 'content_hash', 'branch', 'bundle'}:
        raise CommunityStoreConflict('Invalid retained change bundle')
    payload = value['bundle']; checksum = digest(payload)
    if not isinstance(payload, dict) or len(canonical(payload).encode('utf8')) > MAX_BUNDLE_BYTES:
        raise CommunityStoreConflict('Invalid bounded change bundle payload')
    expected_keys = {'schema_version', 'profile', 'input', 'intent_hash', 'scope', 'scope_hash', 'title', 'files', 'source_observations',
        'base_code_manifest', 'base_code_manifest_hash', 'candidate_code_manifest', 'candidate_code_manifest_hash',
        'candidate_test_files', 'candidate_tests_are_independent', 'patch', 'execution_authorized', 'development_complete',
        'independent_review_completed', 'ci_qualified', 'public_deployment_verified', 'external_model_calls', 'paid_authorization'}
    if set(payload) != expected_keys:
        raise CommunityStoreConflict('Unknown or missing retained change profile field')
    if (checksum != value['bundle_id'] or checksum != value['content_hash'] or value['branch'] != 'community/change-' + checksum[:24]
            or payload.get('profile') != CHANGE_PROFILE or payload.get('schema_version') != 1
            or any(payload.get(name) is not False for name in ('execution_authorized', 'development_complete', 'independent_review_completed', 'ci_qualified', 'public_deployment_verified'))
            or type(payload.get('paid_authorization')) is not int or payload['paid_authorization'] != 0
            or type(payload.get('external_model_calls')) is not int or payload['external_model_calls'] != 0
            or original_change_hash(CommunityChangeInput.model_validate(payload['input'])) != payload.get('intent_hash')
            or digest(payload['scope']) != payload.get('scope_hash')
            or digest(payload['base_code_manifest']) != payload.get('base_code_manifest_hash')
            or digest(payload['candidate_code_manifest']) != payload.get('candidate_code_manifest_hash')):
        raise CommunityStoreConflict('Retained change bundle changed or claims unauthorized qualification')
    body = CommunityChangeInput.model_validate(payload['input'])
    if (payload.get('title') != body.title or payload.get('candidate_tests_are_independent') is not False
            or payload.get('candidate_test_files') != body.test_files or payload['scope'].get('work_id') != body.work_id
            or not isinstance(payload.get('files'), list) or len(payload['files']) != len(body.changes)):
        raise CommunityStoreConflict('Retained change inputs and derived scope differ')
    derived = dict(payload['base_code_manifest']); patch = []
    for change, saved in zip(body.changes, payload['files']):
        test = CANDIDATE_TEST.fullmatch(change.path) is not None
        old = saved.get('before_text')
        raw = None if change.action == 'delete' else change.text.encode('utf8')
        expected = {'path': change.path, 'action': change.action, 'before_sha256': change.before_sha256,
            'before_text': old, 'text': change.text, 'after_sha256': None if raw is None else bytes_hash(raw)}
        # Preserve the original v1 integrity rules for inert historical reads
        # and cleanup. New creation/fresh admission uses protected_code_path;
        # the trusted supervisor independently rejects every root controller.
        if (saved != expected or change.path.startswith(PROTECTED) or (not test and change.path not in payload['scope'].get('registered_code_paths', []))
                or derived.get(change.path) != change.before_sha256
                or (old is None) != (change.before_sha256 is None)
                or (old is not None and (not isinstance(old, str) or len(old.encode('utf8')) > 131072
                    or bytes_hash(old.encode('utf8')) != change.before_sha256 or '\x00' in old or (old and not old.endswith('\n'))))
                or (test and (change.action != 'write' or change.before_sha256 is not None))):
            raise CommunityStoreConflict('Retained source replacement differs from original intent or old bytes')
        if raw is None:
            derived.pop(change.path)
        else:
            derived[change.path] = bytes_hash(raw)
        patch.extend(difflib.unified_diff((old or '').splitlines(keepends=True), (change.text or '').splitlines(keepends=True),
            fromfile='a/' + change.path if old is not None else '/dev/null', tofile='b/' + change.path if raw is not None else '/dev/null'))
    if (derived != payload['candidate_code_manifest'] or ''.join(patch) != payload.get('patch')
            or any(CANDIDATE_TEST.fullmatch(path) is None or not any(item.path == path and item.action == 'write' for item in body.changes) for path in body.test_files)):
        raise CommunityStoreConflict('Retained candidate manifest, patch or test profile changed')
    return payload
