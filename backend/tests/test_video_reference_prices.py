"""Prevent a text-only quote from authorizing reference input while preserving exact old quote/manifest identities."""
from fastapi import HTTPException
import pytest
from pydantic import ValidationError

from app.core.contracts.model_accounting import ModelPriceConfiguration, ModelPriceEnvelope, ModelPriceRule, QuotedModelCall
from app.core.contracts.seedance_references import REFERENCE_PROTOCOL
from app.services.worker.model_job_charges import binding_digest
from app.services.worker.model_prices import ModelPriceRegistry


def quote(**values):
    """Match authored values actually sampled on frozen pre-change sourced75edd8c, never a supplier tariff."""
    return ModelPriceEnvelope(model_id='quote-golden', model_name='doubao-seedance-2-5-260628',
        provider_id='quote-provider', provider_connection_version=1, account_version='golden-account',
        max_call_minor=12, source_note='Authored compatibility golden', **values)


def registry(value):
    """Resolve only this explicit server-owned protocol/model price rule."""
    return ModelPriceRegistry(ModelPriceConfiguration(enabled=True, rules=[ModelPriceRule(quote=value, kinds=['native_video'])]))


def test_old_named_legacy_and_nested_quote_fingerprints_remain_exact_parent_goldens():
    """The new default field is absent from every serialized old shape, including nested call snapshots."""
    named = quote()
    assert named.fingerprint == 'c9458e96001846e40cdb1f06e1f013d92f73910e13a62174a240fcc8b0b8c78e'
    raw = named.model_dump()
    assert 'input_protocol' not in raw
    raw.pop('model_name')
    legacy = ModelPriceEnvelope.model_validate(raw)
    assert legacy.fingerprint == '53807ea25a926afcf71f5c874bc66200a500cb633cf29571c157eb88dd3df7ba'
    call = QuotedModelCall(unit_key='video', kind='native_video', binding_hash='a'*64, quote=named)
    assert binding_digest(call.model_dump()) == '4d3a338bc931ae9f7a3d1b2e4e0318441e9074cb402cefdf6cffdb9cc9e14716'
    assert ModelPriceEnvelope.model_validate_json(named.model_dump_json()).fingerprint == named.fingerprint


def test_reference_price_is_explicit_and_cannot_be_reused_for_text_or_other_protocols():
    """The same exact model name/account does not make old and reference-input pricing interchangeable."""
    old, reference = quote(), quote(input_protocol=REFERENCE_PROTOCOL)
    assert old.fingerprint != reference.fingerprint
    with pytest.raises(HTTPException) as error:
        registry(old).required('native_video', old.model_id, input_protocol=REFERENCE_PROTOCOL)
    assert error.value.status_code == 503
    with pytest.raises(HTTPException) as error:
        registry(reference).required('native_video', reference.model_id)
    assert error.value.status_code == 503
    assert registry(reference).required('native_video', reference.model_id, input_protocol=REFERENCE_PROTOCOL) == reference
    assert registry(old).required('native_video', old.model_id) == old


def test_confirmed_reference_price_requires_its_own_complete_displayed_fingerprint():
    """An old text confirmation cannot borrow a new quote merely because the nominal amount is unchanged."""
    old, reference = quote(), quote(input_protocol=REFERENCE_PROTOCOL)
    with pytest.raises(HTTPException) as error:
        registry(reference).confirmed('native_video', reference.model_id, old.fingerprint, input_protocol=REFERENCE_PROTOCOL)
    assert error.value.status_code == 409
    assert registry(reference).confirmed('native_video', reference.model_id, reference.fingerprint,
        input_protocol=REFERENCE_PROTOCOL) == reference


@pytest.mark.parametrize('kinds', [['asset_image'], ['agent_text'], ['native_video', 'asset_image']])
def test_reference_quote_cannot_implicitly_authorize_unrelated_kinds(kinds):
    """A native input quote is not a newly shared text/image allowance."""
    with pytest.raises(ValidationError):
        ModelPriceRule(quote=quote(input_protocol=REFERENCE_PROTOCOL), kinds=kinds)


@pytest.mark.parametrize('value', [True, 1, '', 'seedance25_text_v1', 'unknown-protocol'])
def test_unknown_or_coerced_input_pricing_protocol_is_rejected(value):
    """Only the separately declared reference protocol can select the new optional pricing domain."""
    with pytest.raises(ValidationError):
        quote(input_protocol=value)
