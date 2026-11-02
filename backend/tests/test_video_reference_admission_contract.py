"""Protect original request/binding identities while requiring a separately declared reference admission domain."""
from dataclasses import replace
from decimal import Decimal

from fastapi import HTTPException
import pytest

from app.core.contracts.execution_binding import binding_fingerprint
from app.core.contracts.execution_capabilities import Capability, preflight
from app.core.contracts.seedance25 import MODEL_ID
from app.core.contracts.seedance_references import REFERENCE_PROTOCOL
from app.services.shot_plan.compiler import public_policy
from app.services.worker.execution_admission import AdmissionPolicy, freeze_request


def old_policy():
    """Use the exact authored inputs sampled against frozen pre-change sourced75edd8c."""
    return AdmissionPolicy(Capability(MODEL_ID, frozenset({4, 8}), frozenset(), 0), 12,
        'CNY', 3, 'baseline-old-text-profile')


def reference_policy():
    """Only this explicit server-owned profile grants the extended input contract."""
    return AdmissionPolicy(Capability(MODEL_ID, frozenset({4, 8}), frozenset({'image', 'video'}), 4,
        reference_protocol=REFERENCE_PROTOCOL), 12, 'CNY', 3, 'reference-profile')


def request():
    """Describe the permitted same-image first/last intent without a transport address or claimed decoded metadata."""
    return {'model_id': MODEL_ID, 'duration': 4, 'prompt': 'original geometry',
        'references': [{'media_id': 'frame-a', 'kind': 'image'}]*2,
        'video_reference': {'schema_version': 1, 'mode': 'first_last_frames', 'references': [
            {'media_id': 'frame-a', 'expected_sha256': 'a'*64, 'role': role} for role in ('first_frame', 'last_frame')]}}


def check(snapshot, policy):
    """Run the real pure admission contract, leaving reservation and HTTP to later integration tests."""
    preflight(snapshot, policy.capability, quoted_cost=Decimal(12), remaining_budget=Decimal(12), iteration=0, iteration_limit=3)


def test_old_binding_and_request_hashes_remain_exact_frozen_parent_goldens():
    """Actual parent source produced these fixed values before any new profile was added."""
    policy = old_policy()
    original = {'model_id': MODEL_ID, 'duration': 4, 'references': [],
        'prompt': 'historical intent', 'prompt_revision': 'old-revision'}
    assert binding_fingerprint(policy) == 'e0809affb3af537347e366b7def0e8fa9a1e0b082b3a072c86c909e0e801cf8a'
    assert freeze_request(original, policy) == (original, '90eb739a973b7b8a9601106360579b37d147a837c9b41de7ee37162e189dd373')
    assert 'reference_protocol' not in public_policy(policy)
    check(original, policy)


def test_new_profile_keeps_ordered_duplicate_frame_roles_and_detaches_caller_objects():
    """Duplicate media has explicit frame meaning; frozen inputs cannot later mutate with a caller-owned list."""
    policy, original = reference_policy(), request()
    check(original, policy)
    frozen, digest = freeze_request(original, policy)
    assert frozen == original and len(digest) == 64
    original['video_reference']['references'][1]['role'] = 'reference_image'
    assert frozen['video_reference']['references'][1]['role'] == 'last_frame'
    assert public_policy(policy)['reference_protocol'] == REFERENCE_PROTOCOL
    generic = replace(policy, capability=replace(policy.capability, reference_protocol=None))
    assert binding_fingerprint(generic) != binding_fingerprint(policy)


@pytest.mark.parametrize('profile', ['old-text', 'generic-image'])
def test_old_or_undeclared_capability_cannot_accept_new_reference_contract(profile):
    """A reference-kind count alone cannot promote a legacy model to the new role/hash-aware protocol."""
    policy = old_policy() if profile == 'old-text' else replace(reference_policy(),
        capability=replace(reference_policy().capability, reference_protocol=None))
    with pytest.raises(HTTPException):
        freeze_request(request(), policy)
    with pytest.raises(HTTPException):
        check(request(), policy)


@pytest.mark.parametrize('fault', ['missing-mode', 'index-order', 'mixed-role', 'url-field'])
def test_incomplete_or_mismatched_reference_intent_refuses_before_reservation(fault):
    """No path silently removes explicit conditions or reinterprets first/last ordering."""
    original = request()
    if fault == 'missing-mode': original.pop('video_reference')
    if fault == 'index-order': original['references'][1] = {'media_id': 'other-frame', 'kind': 'image'}
    if fault == 'mixed-role': original['video_reference']['references'][1]['role'] = 'reference_image'
    if fault == 'url-field': original['video_reference']['references'][0]['url'] = 'https://example.com/private.png'
    with pytest.raises(HTTPException):
        freeze_request(original, reference_policy())
    with pytest.raises(HTTPException):
        check(original, reference_policy())
