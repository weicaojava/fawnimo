"""Trusted price-file admission remains inert without explicit positive, protocol-specific setup."""
import json

from fastapi import HTTPException
import pytest

from app.services.worker.model_prices import load_model_prices
from tests.test_model_charges import calls


def configuration():
    """Create an explicit synthetic operator fixture, never a real supplier price."""
    return {'schema_version':1,'enabled':True,'rules':[{'quote':calls(count=1)[0].quote.model_dump(),'kinds':['agent_text']}]}


def test_only_the_configured_model_and_protocol_receive_a_quote(tmp_path):
    """An enabled config is still a quote registry and never starts or invokes a transport."""
    path=tmp_path/'operator-prices.json'
    path.write_text(json.dumps(configuration()),'utf-8')
    registry=load_model_prices(path)
    assert registry.issue=='configured'
    assert registry.required('agent_text','m').max_call_minor==25
    for kind,model in [('asset_image','m'),('agent_text','different')]:
        with pytest.raises(HTTPException) as failure:
            registry.required(kind,model)
        assert failure.value.status_code==503


@pytest.mark.parametrize('mode',['missing','disabled','unknown_quote','zero','duplicate','secret_field','oversized','malformed','empty_enabled'])
def test_missing_or_ambiguous_operator_setup_fails_closed_without_echoing_contents(tmp_path,mode):
    """Client-like extra fields, invalid prices and duplicate rules cannot enable an implicit free call."""
    path=tmp_path/'operator-prices.json'
    value=configuration()
    if mode=='disabled':value['enabled']=False
    if mode=='unknown_quote':value['rules'][0]['quote']['max_call_minor']=None
    if mode=='zero':value['rules'][0]['quote']['max_call_minor']=0
    if mode=='duplicate':value['rules'].append(value['rules'][0])
    if mode=='secret_field':value['api_key']='SYNTHETIC_PRIVATE_INPUT'
    if mode=='empty_enabled':value['rules']=[]
    if mode!='missing':
        path.write_text('SYNTHETIC_PRIVATE_INPUT' if mode=='malformed' else 'x'*524289 if mode=='oversized' else json.dumps(value),'utf-8')
    registry=load_model_prices(None if mode=='missing' else path)
    assert registry.issue in {'not_configured','disabled','invalid_configuration'}
    with pytest.raises(HTTPException) as failure:
        registry.required('agent_text','m')
    assert failure.value.status_code==503 and 'SYNTHETIC_PRIVATE_INPUT' not in str(failure.value.detail)
