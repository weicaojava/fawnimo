"""Exercise explicit condition modes and exact decoded bytes without a model account or public media host."""
import base64
from dataclasses import replace
import hashlib
from io import BytesIO

from PIL import Image
import pytest
from pydantic import ValidationError

from app.core.contracts.seedance25 import MODEL_ID
from app.core.contracts.seedance_references import PreparedReference, VideoReferenceSelection
from app.core.integrations.verified_image import verify_image_bytes
from app.core.integrations.volcengine.seedance25_payload import build_seedance25_body
from app.core.integrations.volcengine.seedance_reference_payload import build_seedance_reference_body


async def image_proof(identity='image-a', *, size=(360, 640), color='navy'):
    """Use authored geometry and the actual owned decoder, not a claimed hash/dimension fixture."""
    buffer = BytesIO()
    Image.new('RGB', size, color).save(buffer, format='PNG')
    decoded = await verify_image_bytes(buffer.getvalue(), reference=True)
    return PreparedReference(project_id='project-a', media_id=identity, sha256=decoded.sha256,
        mime=decoded.mime, byte_size=len(decoded.content), width=decoded.width, height=decoded.height, content=decoded.content)


def selection(proofs, roles, mode='reference'):
    """Construct only the public media choices; private paths and delivery URLs never appear in them."""
    return VideoReferenceSelection.model_validate({'mode': mode, 'references': [
        {'media_id': proof.media_id, 'expected_sha256': proof.sha256, 'role': role} for proof, role in zip(proofs, roles)]})


def body(selected, proofs, **options):
    """Keep all request tests on the same exact explicit project/model/time intent."""
    return build_seedance_reference_body(project_id='project-a', model_id=MODEL_ID, prompt='原创几何场景',
        duration=4, selection=selected, prepared=tuple(proofs), **options)


@pytest.mark.asyncio
async def test_actual_decoded_image_bytes_and_order_are_transmitted_without_implicit_inputs():
    """The outgoing condition payload decodes to the actual chosen bytes in their original order."""
    left, right = await image_proof(), await image_proof('image-b', color='coral')
    outgoing = body(selection([right, left], ['reference_image']*2), [right, left])
    assert outgoing['omni_reference_task_type'] == 'reference' and outgoing['ratio'] == '9:16'
    assert outgoing['generate_audio'] is False and outgoing['watermark'] is True
    assert len(outgoing['content']) == 3 and 'n' not in outgoing
    for item, proof in zip(outgoing['content'][1:], [right, left]):
        encoded = item['image_url']['url'].split(',', 1)[1]
        assert base64.b64decode(encoded, validate=True) == proof.content
        assert hashlib.sha256(base64.b64decode(encoded)).hexdigest() == proof.sha256
        assert item['role'] == 'reference_image'


@pytest.mark.asyncio
async def test_first_and_last_frames_are_explicit_adaptive_and_allow_the_same_actual_image():
    """First and last frame semantics differ from loose semantic references and preserve duplicate-role intent."""
    proof = await image_proof()
    selected = selection([proof, proof], ['first_frame', 'last_frame'], 'first_last_frames')
    outgoing = body(selected, [proof, proof])
    assert outgoing['ratio'] == 'adaptive' and 'omni_reference_task_type' not in outgoing
    assert [item['role'] for item in outgoing['content'][1:]] == ['first_frame', 'last_frame']
    assert outgoing['content'][1]['image_url'] == outgoing['content'][2]['image_url']
    first = body(selection([proof], ['first_frame'], 'first_frame'), [proof])
    assert len(first['content']) == 2 and first['ratio'] == 'adaptive'


@pytest.mark.parametrize('mode,roles', [
    ('first_frame', ['last_frame']), ('first_frame', ['first_frame', 'last_frame']),
    ('first_last_frames', ['last_frame', 'first_frame']), ('first_last_frames', ['first_frame']),
    ('reference', ['reference_image', 'first_frame']), ('reference', ['last_frame']),
    ('reference', ['reference_video', 'reference_video']), ('reference', []),
    ('reference', ['reference_image']*4)])
def test_unsupported_or_ambiguous_role_combinations_refuse_before_wire_build(mode, roles):
    """No mode converts a tail-only/mixed/over-limit input into a different operation."""
    with pytest.raises(ValidationError):
        VideoReferenceSelection.model_validate({'mode': mode, 'references': [
            {'media_id': 'input-'+str(index), 'expected_sha256': 'a'*64, 'role': role} for index, role in enumerate(roles)]})


@pytest.mark.parametrize('extra', [{'url': 'https://example.com/private.mp4'}, {'path': 'C:/private.png'},
                                  {'api_key': 'authored-not-a-key'}, {'kind': 'video'}])
def test_public_choice_cannot_supply_transport_targets_paths_or_conflicting_types(extra):
    """The user-facing choice has no transport or credential authority."""
    with pytest.raises(ValidationError):
        VideoReferenceSelection.model_validate({'mode': 'reference', 'references': [
            {'media_id': 'image-a', 'expected_sha256': 'a'*64, 'role': 'reference_image', **extra}]})


@pytest.mark.parametrize('version', [True, '1', 1.0, 2])
def test_contract_version_requires_actual_integer_one(version):
    """Strict versions keep booleans and numbers from selecting an unintended contract."""
    with pytest.raises(ValidationError):
        VideoReferenceSelection.model_validate({'schema_version': version, 'mode': 'reference', 'references': [
            {'media_id': 'image-a', 'expected_sha256': 'a'*64, 'role': 'reference_image'}]})


@pytest.mark.asyncio
@pytest.mark.parametrize('change', ['project', 'hash', 'bytes', 'dimensions', 'order', 'missing'])
async def test_changed_or_wrong_scope_proofs_never_serialize_conditions(change):
    """A different project/file/order cannot be accepted using a prior media choice."""
    first, second = await image_proof(), await image_proof('image-b', color='olive')
    selected = selection([first, second], ['reference_image']*2)
    proofs = [first, second]
    if change == 'project': proofs[0] = replace(first, project_id='project-b')
    if change == 'hash': proofs[0] = replace(first, sha256='b'*64)
    if change == 'bytes': proofs[0] = replace(first, content=first.content[:-1]+b'X')
    if change == 'dimensions': proofs[0] = replace(first, width=True)
    if change == 'order': proofs.reverse()
    if change == 'missing': proofs.pop()
    with pytest.raises(ValueError):
        body(selected, proofs)


@pytest.mark.asyncio
async def test_frame_profile_rejects_nonportrait_instead_of_changing_the_source():
    """This bounded first-frame slice refuses unsupported ratios before a provider can crop or stretch them."""
    square = await image_proof(size=(640, 640))
    with pytest.raises(ValueError, match='9:16'):
        body(selection([square], ['first_frame'], 'first_frame'), [square])
    assert body(selection([square], ['reference_image']), [square])['ratio'] == '9:16'


def test_video_url_is_an_explicit_owned_delivery_boundary_not_a_claim_of_completed_delivery():
    """This is a pure wire test; actual video decoding, ownership and delivery will be verified by the service stage."""
    target = 'https://media.example.org/api/v1/execution-reference-media/'+'a'*32+'?token='+'b'*64
    proof = PreparedReference(project_id='project-a', media_id='video-a', sha256='c'*64,
        mime='video/mp4', byte_size=8192, width=720, height=1280, duration_ms=4000, delivery_url=target,
        fps=24, frame_clock_sha256='d'*64, has_audio=False)
    outgoing = body(selection([proof], ['reference_video']), [proof], delivery_origin='https://media.example.org')
    assert outgoing['content'][1] == {'type': 'video_url', 'video_url': {'url': target}, 'role': 'reference_video'}
    for changed in ['http://media.example.org', 'https://other.example.org', 'https://user:secret@media.example.org']:
        with pytest.raises(ValueError):
            body(selection([proof], ['reference_video']), [proof], delivery_origin=changed)
    with pytest.raises(ValueError):
        body(selection([proof], ['reference_video']), [proof])
    assert target not in repr(proof)


def test_existing_text_profile_stays_text_only_and_keeps_its_original_wire_body():
    """Adding a separate builder does not silently extend the old immutable protocol."""
    snapshot = {'model_id': MODEL_ID, 'duration': 4, 'references': [], 'prompt': '旧文本意图'}
    outgoing = build_seedance25_body(snapshot)
    assert outgoing == {'model': MODEL_ID, 'content': [{'type': 'text', 'text': '旧文本意图'}],
        'ratio': '9:16', 'resolution': '480p', 'output_format': 'mp4', 'generate_audio': False, 'watermark': True, 'duration': 4}
    with pytest.raises(ValueError):
        build_seedance25_body({**snapshot, 'references': [{'media_id': 'image-a', 'kind': 'image'}]})
