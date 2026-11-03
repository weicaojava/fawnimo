"""Prove exact scoped capability cryptography with authored keys and clocks, never real media or model credentials."""
import hashlib
from urllib.parse import parse_qs,urlsplit

import pytest

from app.core.contracts.workspace_reference_delivery import WorkspaceReferencePolicy,workspace_reference_key

A='ws_'+'a'*32
B='ws_'+'b'*32
MASTER=bytes(range(32))
IDENTIFIER='c'*32
TOKEN='d'*64


def policy(workspace=A,origin='https://studio.example.com'):
    """Derive one role-specific fixture key without putting a master in a worker policy."""
    return WorkspaceReferencePolicy(workspace,origin,workspace_reference_key(MASTER,workspace))


def facts(value):
    """Parse emitted public wire fields explicitly; no test guesses server state from an HTTP200."""
    query=parse_qs(urlsplit(value).query)
    return query['token'][0],int(query['expires'][0]),query['signature'][0]


def test_exact_signed_link_is_short_lived_and_secrets_are_not_public_profile_facts():
    """The worker receives only its derived secret; repr/profile facts carry neither key nor its digest."""
    current=policy();url=current.signed_url(IDENTIFIER,TOKEN,1000.9);token,expires,signature=facts(url)
    assert expires==4600 and current.verifies(IDENTIFIER,token,expires,signature,clock=lambda:1001)
    assert not current.verifies(IDENTIFIER,token,expires,signature,clock=lambda:4600)
    assert not current.verifies(IDENTIFIER,token,expires,signature,clock=lambda:999)
    assert '/ws_'+('a'*32)+'/'+IDENTIFIER in url
    assert current.signing_key!=MASTER and policy(B).signing_key!=current.signing_key
    public=repr(current)+repr(current.public_descriptor())
    assert current.signing_key.hex() not in public and hashlib.sha256(current.signing_key).hexdigest() not in public
    assert WorkspaceReferencePolicy.from_private_message(current.private_message(),A)==current


@pytest.mark.parametrize('change',['workspace','origin','identifier','token','expiry','signature'])
def test_changing_any_signed_authority_fact_cannot_select_another_target(change):
    """A valid role-A link cannot be repurposed with another scope, host, file, token, lifetime or signature."""
    current=policy();token,expires,signature=facts(current.signed_url(IDENTIFIER,TOKEN,1000))
    target=policy(B) if change=='workspace' else policy(origin='https://preview.example.com') if change=='origin' else current
    identifier='e'*32 if change=='identifier' else IDENTIFIER
    if change=='token':token='f'*64
    if change=='expiry':expires-=1
    if change=='signature':signature=('0' if signature[0]!='0' else '1')+signature[1:]
    assert not target.verifies(identifier,token,expires,signature,clock=lambda:1001)


@pytest.mark.parametrize('change',['different_scope','extra_fields','wrong_key','untrusted_http'])
def test_private_startup_policy_cannot_smuggle_another_scope_or_untrusted_origin(change):
    """Private structured input is strict before worker construction and does not use environment/header authority."""
    value=policy().private_message()
    if change=='different_scope':value['workspace_id']=B
    if change=='extra_fields':value['root']='untrusted-root'
    if change=='wrong_key':value['signing_key']=True
    if change=='untrusted_http':value['origin']='http://127.0.0.1:18004'
    with pytest.raises(ValueError):WorkspaceReferencePolicy.from_private_message(value,A)


def test_scoped_delivery_keeps_old_image_only_profile_and_never_hashes_signing_material():
    """An opt-in local gateway does not invalidate old image-only bindings; video bindings vary by scope, not private keys."""
    from app.core.contracts.model_accounting import ModelPriceEnvelope
    from app.core.contracts.seedance25 import MODEL_ID
    from app.core.contracts.seedance_references import REFERENCE_PROTOCOL
    from app.core.contracts.video_runtime_setup import VideoRuntimeConfiguration
    from app.services.worker.native_video_accounting import NativeVideoAccounting
    origin='http://127.0.0.1:18006'
    one=WorkspaceReferencePolicy(A,origin,bytes(range(32)),origin)
    rotated=WorkspaceReferencePolicy(A,origin,bytes(reversed(range(32))),origin)
    other=WorkspaceReferencePolicy(B,origin,bytes(range(32)),origin)
    quote=ModelPriceEnvelope(model_id='authored-model',model_name=MODEL_ID,provider_id='authored-provider',provider_connection_version=1,
        account_version='authored-account',input_protocol=REFERENCE_PROTOCOL,max_call_minor=25,source_note='Authored protocol profile only; no supplier tariff')
    connection={'protocol':REFERENCE_PROTOCOL,'base_url':'http://127.0.0.1:19123/api/v3'}
    def profile(scope,media_origin):
        """Construct only the typed inert profile; no credentials, network or database operation is executed."""
        config=VideoRuntimeConfiguration(enabled=True,model_id=quote.model_id,service_version='authored-scope-profile',reference_media_origin=media_origin)
        value=NativeVideoAccounting(None,config,None,trusted_local_host='127.0.0.1',trusted_local_port=19123,
            reference_delivery_available=scope is not None,reference_delivery_scope=scope)
        return value.transport_config(connection,quote,'authored-reference-model-key')
    assert profile(one,None).profile_hash==profile(None,None).profile_hash
    assert profile(one,origin).profile_hash==profile(rotated,origin).profile_hash
    assert profile(one,origin).profile_hash!=profile(other,origin).profile_hash
