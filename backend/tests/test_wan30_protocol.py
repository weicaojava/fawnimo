"""Authored optional Wan3.0 contract/protocol checks; no actual vendor, credential, model-quality or deployment claim."""
from dataclasses import FrozenInstanceError,replace

import pytest

from app.core.contracts.wan30 import Wan30Configuration,checked_base_url,TEXT_PROTOCOL,REFERENCE_PROTOCOL

BASE='https://authored-space.cn-beijing.maas.aliyuncs.com/api/v1'


def configuration(**changes):
    """Use one clearly authored independent account without reading application settings or a real secret."""
    values={'api_key':'AUTHORED_WAN_CREDENTIAL','service_version':'authored-wan-service-v1',
        'account_version':'authored-wan-account-v1','base_url':BASE}
    return Wan30Configuration(**{**values,**changes})


def test_explicit_beijing_configuration_is_frozen_keyless_and_protocol_specific():
    """Changing account/protocol/origin authority changes the binding; replacing only the secret never exposes it as public evidence."""
    text=configuration();reference=configuration(protocol=REFERENCE_PROTOCOL)
    assert text.control_host=='authored-space.cn-beijing.maas.aliyuncs.com'
    assert text.binding_id.startswith('wan30:') and text.profile_hash!=reference.profile_hash
    assert text.profile_hash==configuration(api_key='OTHER_AUTHORED_KEY').profile_hash
    assert text.profile_hash!=configuration(account_version='authored-wan-account-v2').profile_hash
    assert text.api_key not in repr(text) and text.api_key not in text.profile_hash
    with pytest.raises(FrozenInstanceError):text.protocol=REFERENCE_PROTOCOL


@pytest.mark.parametrize('changes',[
    {'base_url':'https://dashscope.aliyuncs.com/api/v1'},
    {'base_url':'https://authored-space.ap-southeast-1.maas.aliyuncs.com/api/v1'},
    {'base_url':'https://authored-space.cn-beijing.maas.aliyuncs.com/api/v1?token=authored'},
    {'base_url':'https://operator:AUTHORED_PRIVATE@authored-space.cn-beijing.maas.aliyuncs.com/api/v1'},
    {'base_url':'https://authored-space.cn-beijing.maas.aliyuncs.com.evil.example/api/v1'},
    {'base_url':'https://authored-space.cn-beijing.maas.aliyuncs.com/api/v3'},
    {'base_url':'http://127.0.0.1:18999/api/v1'},
    {'download_hosts':frozenset({'other.example'})},
    {'protocol':'seedance25_text_v1'}, {'protocol':[]},
    {'service_version':'PREPARATION_ONLY'}, {'account_version':''}, {'api_key':'not a valid header'},
])
def test_unsupported_authority_never_becomes_a_wan_protocol(changes):
    """No default/shared/regional/lookalike endpoint or inherited Seedance flag can activate this separately declared profile."""
    with pytest.raises(ValueError):configuration(**changes)


def test_exact_loopback_requires_its_own_port_and_download_host():
    """An application-owned HTTP fixture needs all three independent local authority facts; no global localhost switch exists."""
    local=configuration(base_url='http://127.0.0.1:18999/api/v1',trusted_local_host='127.0.0.1',
        trusted_local_port=18999,download_hosts=frozenset({'127.0.0.1'}))
    assert local.control_host=='127.0.0.1' and local.profile_hash!=configuration().profile_hash
    for changes in ({'trusted_local_port':True},{'download_hosts':frozenset({'localhost'})},
        {'base_url':'http://127.0.0.1:19000/api/v1'},{'base_url':'http://127.0.0.1:18999/api/v3'}):
        with pytest.raises(ValueError):replace(local,**changes)


def test_bad_origin_errors_never_echo_authored_private_url_text():
    """A malformed URL cannot leak its netloc through a configuration failure."""
    with pytest.raises(ValueError) as caught:
        checked_base_url('https://operator:AUTHORED_PRIVATE@authored-space.cn-beijing.maas.aliyuncs.com\uff0fprivate')
    assert str(caught.value)=='Untrusted Wan3.0 control origin' and 'AUTHORED_PRIVATE' not in str(caught.value)
