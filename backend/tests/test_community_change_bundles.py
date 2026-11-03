"""Inert bundle integrity checks using real authored preparation records; no candidate code or issue instruction is executed."""
from copy import deepcopy
from dataclasses import replace
import json
from pathlib import Path
import subprocess
import uuid

import pytest
from pydantic import ValidationError

from app.core.contracts.community_changes import CommunityChangeInput, CommunityFileChange
from app.core.contracts.community_queue import QueueFinishInput
from app.services.community.change_bundles import (PublicCodeSnapshot, build_change_bundle, verify_change_bundle,
    bytes_hash, original_change_hash, PROTECTED)
from app.services.community.evaluations import canonical, digest
from app.services.community.store import CommunityStoreConflict
from tests.test_community_evaluations import proof
from tests.test_community_queue_leases import factory, claim_body, lease_body

REPO = Path(__file__).resolve().parents[2]
TEST_PATH = 'backend/tests/test_community_candidate_register.py'


@pytest.fixture
def prepared_change(factory):
    """Use actual preparation storage/proof, while marking the immutable public Git IDs as authored test identities."""
    _intake, queue, store, _moment, values, _plan, ids, _body = factory()
    claim = store.claim(claim_body(ids[0]), proof(values[0]))
    store.finish(lease_body(claim, contract=QueueFinishInput, outcome='prepared', note='仅完成准备，变更仍须独立检查。'), proof(values[0]))
    prepared = store.get_work(ids[0])
    files = {'scripts/features.py': (REPO / 'scripts/features.py').read_bytes(),
             'capabilities/features.json': (REPO / 'capabilities/features.json').read_bytes()}
    snapshot = PublicCodeSnapshot(17, 'authored/fawnimo', 'a' * 40, 'b' * 40, files)
    body = CommunityChangeInput.model_validate({'request_key': 'change-' + uuid.uuid4().hex, 'work_id': ids[0], 'title': '可审阅的功能登记变更',
        'changes': [{'path': 'scripts/features.py', 'before_sha256': bytes_hash(files['scripts/features.py']), 'action': 'write',
                     'text': files['scripts/features.py'].decode('utf8').replace('\r\n', '\n') + '\n# Authored candidate remains unexecuted bundle data.\n'},
                    {'path': TEST_PATH, 'before_sha256': None, 'action': 'write',
                     'text': 'def test_candidate_register():\n    raise AssertionError("This is inert source data, never executed by the bundle builder")\n'}],
        'test_files': [TEST_PATH]})
    return body, prepared, snapshot, queue.catalog()


def rehash(value):
    """Model an adversary recomputing public hashes; derived invariants must still reject inconsistent source or approvals."""
    value['bundle_id'] = value['content_hash'] = digest(value['bundle'])
    value['branch'] = 'community/change-' + value['bundle_id'][:24]
    return value


def test_bundle_is_reproducible_and_inert_with_exact_prepared_provenance(prepared_change, monkeypatch):
    """Commands inside candidate tests remain bytes, and identical original input/snapshot produces the same reviewable artifact."""
    body, prepared, snapshot, registry = prepared_change
    def forbidden(*_args, **_kwargs):
        """Building a bundle cannot import or execute proposed code, start a shell or contact a source."""
        raise AssertionError('Unexpected candidate execution')
    monkeypatch.setattr(subprocess, 'Popen', forbidden)
    first = build_change_bundle(body, prepared, snapshot, registry)
    assert first == build_change_bundle(body, prepared, snapshot, registry)
    saved = verify_change_bundle(first)
    assert saved['scope']['work_id'] == prepared['id'] and saved['scope']['evaluation_id'] == prepared['work']['entry']['evaluation_id']
    assert saved['base_code_manifest_hash'] != saved['candidate_code_manifest_hash']
    assert saved['scope']['base_commit'] == 'a' * 40 and saved['scope']['base_tree'] == 'b' * 40
    assert not saved['execution_authorized'] and not saved['development_complete'] and not saved['candidate_tests_are_independent']
    assert saved['scope']['feature_ids'] == ['F-015.01'] and 'AssertionError' in saved['patch']


@pytest.mark.parametrize('state', ['queued', 'leased', 'cancelled', 'failed'])
def test_preparation_must_be_completed_and_does_not_grant_execution(prepared_change, state):
    """A pending, cancelled or failed reservation cannot fabricate prepared provenance for a new source bundle."""
    body, prepared, snapshot, registry = prepared_change
    wrong = deepcopy(prepared); wrong['current']['state'] = state
    with pytest.raises(CommunityStoreConflict):
        build_change_bundle(body, wrong, snapshot, registry)


@pytest.mark.parametrize('field', ['execution_authorized', 'development_complete'])
def test_preparation_authority_claim_is_rejected(prepared_change, field):
    """A copied JSON flag cannot upgrade the preparation protocol into a code-execution capability."""
    body, prepared, snapshot, registry = prepared_change
    wrong = deepcopy(prepared); wrong[field] = True
    with pytest.raises(CommunityStoreConflict):
        build_change_bundle(body, wrong, snapshot, registry)


def test_public_snapshot_and_registered_targets_must_match_exactly(prepared_change):
    """Changed before bytes, another feature register or unregistered production file all stop candidate assembly."""
    body, prepared, snapshot, registry = prepared_change
    altered = dict(snapshot.files); altered['scripts/features.py'] += b'# newer committed bytes\n'
    with pytest.raises(CommunityStoreConflict):
        build_change_bundle(body, prepared, replace(snapshot, files=altered), registry)
    changed_registry = deepcopy(registry); changed_registry['register_version'] = 'another-version'
    with pytest.raises(CommunityStoreConflict):
        build_change_bundle(body, prepared, snapshot, changed_registry)
    changed = body.model_dump(); changed['changes'][0] = {'path': 'backend/unregistered.py', 'action': 'write', 'before_sha256': None, 'text': 'value = 1\n'}
    with pytest.raises(CommunityStoreConflict):
        build_change_bundle(CommunityChangeInput.model_validate(changed), prepared, snapshot, registry)


@pytest.mark.parametrize('path', ['../private.py', '/absolute.py', 'backend//x.py', 'backend/./x.py', 'backend/x/../x.py',
    'backend\\private.py', 'backend/A.py', 'backend/con.py', 'backend/lpt1.py', 'backend/x.py:stream', 'backend/x .py'])
def test_paths_reject_traversal_windows_aliases_and_option_like_names(path):
    """The same JSON cannot select a different file when handled on Windows versus Linux."""
    with pytest.raises(ValidationError):
        CommunityFileChange(path=path, before_sha256=None, action='write', text='value = 1\n')


@pytest.mark.parametrize('path', ['backend/app/core/owned_process.py', 'backend/app/services/community/queue_leases.py',
    'backend/app/services/community/change_bundles.py', 'backend/app/services/community/public_source.py',
    'backend/app/services/community/candidate_git.py', 'backend/app/services/community/public_code_child.py', 'backend/develop_community.py',
    'backend/check_community.py', 'backend/queue_community.py', 'backend/assess_community.py', 'backend/review_community.py', 'backend/unseen_control_entry.py',
    'backend/app/services/community/advice.py','backend/app/services/community/advice_store.py',
    'backend/app/services/community/advice_child.py','backend/app/services/community/advice_future_worker.py'])
def test_protected_controller_cannot_be_selected_even_if_registry_lists_it(prepared_change, path):
    """A registry association cannot let a candidate rewrite its execution, source, lease or approval boundary."""
    body, prepared, snapshot, registry = prepared_change
    altered = deepcopy(registry)
    next(item for item in altered['features'] if item['id'] == 'F-015.01')['code'].append(path)
    files = dict(snapshot.files); files['capabilities/features.json'] = canonical(altered).encode('utf8')
    changed = body.model_dump(); changed['changes'][0] = {'path': path, 'before_sha256': None, 'action': 'write', 'text': 'value = 1\n'}
    with pytest.raises(CommunityStoreConflict):
        build_change_bundle(CommunityChangeInput.model_validate(changed), prepared, replace(snapshot, files=files), altered)


@pytest.fixture(params=['backend/check_community.py','backend/app/services/community/advice_store.py'])
def legacy_controller_bundle(prepared_change,monkeypatch,request):
    """Reproduce only the former fixed-name guard to create inert historical bytes; restore current policy before any store/read/cleanup test."""
    body,prepared,snapshot,registry=prepared_change; altered=deepcopy(registry); path=request.param
    next(item for item in altered['features'] if item['id']=='F-015.01')['code'].append(path)
    files=dict(snapshot.files); files['capabilities/features.json']=canonical(altered).encode('utf8'); selected=replace(snapshot,files=files)
    changed=body.model_dump(); changed['changes'][0]={'path':path,'before_sha256':None,'action':'write','text':'legacy_entry = True\n'}
    intent=CommunityChangeInput.model_validate(changed)
    with monkeypatch.context() as prior:
        prior.setattr('app.services.community.change_bundles.protected_code_path',lambda value:value.startswith(PROTECTED))
        artifact=build_change_bundle(intent,prepared,selected,altered)
    return intent,prepared,selected,altered,artifact


def test_legacy_inert_controller_history_remains_readable_but_cannot_create_new_change(legacy_controller_bundle):
    """Narrow new authority without erasing old immutable bytes or preventing the old profile's read-only/cleanup compatibility."""
    body,prepared,snapshot,registry,artifact=legacy_controller_bundle
    assert verify_change_bundle(artifact)==artifact['bundle']
    with pytest.raises(CommunityStoreConflict): build_change_bundle(body,prepared,snapshot,registry)


@pytest.mark.parametrize('tamper', ['file_text', 'before_text', 'manifest', 'patch', 'test_files', 'scope_work', 'approval'])
def test_recomputed_outer_hash_does_not_hide_inconsistent_inner_data(prepared_change, tamper):
    """Public hashes identify bytes, but never independently approve them; all reproducible source relations are checked separately."""
    body, prepared, snapshot, registry = prepared_change
    value = build_change_bundle(body, prepared, snapshot, registry); payload = value['bundle']
    if tamper == 'file_text':
        payload['files'][0]['text'] = 'substituted = True\n'
    elif tamper == 'before_text':
        payload['files'][0]['before_text'] = 'different_original = True\n'
    elif tamper == 'manifest':
        payload['candidate_code_manifest']['scripts/features.py'] = '0' * 64
        payload['candidate_code_manifest_hash'] = digest(payload['candidate_code_manifest'])
    elif tamper == 'patch':
        payload['patch'] += '+hidden_command = True\n'
    elif tamper == 'test_files':
        payload['candidate_test_files'] = ['backend/tests/test_another.py']
    elif tamper == 'scope_work':
        payload['scope']['work_id'] = 'another-work'; payload['scope_hash'] = digest(payload['scope'])
    else:
        payload['independent_review_completed'] = True
    with pytest.raises(CommunityStoreConflict):
        verify_change_bundle(rehash(value))


def test_original_change_key_hash_and_artifact_bind_every_new_intent(prepared_change):
    """An original key can only recover identical input; a new title, test or public base produces a different artifact identity."""
    body, prepared, snapshot, registry = prepared_change
    original = build_change_bundle(body, prepared, snapshot, registry)
    changed = body.model_copy(update={'title': '明确不同的新意图'})
    assert original_change_hash(changed) != original_change_hash(body)
    assert build_change_bundle(changed, prepared, snapshot, registry)['bundle_id'] != original['bundle_id']
    assert build_change_bundle(body, prepared, replace(snapshot, commit='c' * 40), registry)['bundle_id'] != original['bundle_id']


def test_unknown_retained_authority_field_is_rejected_even_with_recomputed_hash(prepared_change):
    """A retained artifact has one exact protocol shape, so extra self-approval metadata cannot masquerade as a new policy."""
    body, prepared, snapshot, registry = prepared_change
    value = build_change_bundle(body, prepared, snapshot, registry)
    value['bundle']['new_execution_policy'] = {'approved': True}
    with pytest.raises(CommunityStoreConflict): verify_change_bundle(rehash(value))


@pytest.mark.parametrize('extra', ['command', 'lease_token', 'image', 'repository', 'execution_authorized', 'independent_review_completed'])
def test_original_json_cannot_select_executor_credentials_or_claim_approval(prepared_change, extra):
    """Preparation tokens, arbitrary images/commands and review flags are absent from the proposed-change input."""
    body, _prepared, _snapshot, _registry = prepared_change
    value = body.model_dump(); value[extra] = 'untrusted'
    with pytest.raises(ValidationError):
        CommunityChangeInput.model_validate(value)
