"""Static-address admission and narrowly scoped empty address-reservation cleanup; actual packet behavior is separately qualified on Docker."""
from types import SimpleNamespace
import pytest

from app.services.community.runtime_release_docker import RuntimeDocker
from app.services.community.runtime_release_store import RuntimeReleaseError


def boundary():
    """Return an authored metadata transport that cannot contact a daemon or mutate real resources."""
    return RuntimeDocker(SimpleNamespace(command=lambda *args: (_ for _ in ()).throw(AssertionError('No daemon mutation expected'))),'a'*64)


def test_static_address_requires_explicitly_configured_owned_subnet(monkeypatch):
    """An auto-allocated network alone is not Docker admission for --ip, even though inspect shows an apparent subnet."""
    docker=boundary()
    network=docker.spec('network','b'*64,'private',internal=True)
    monkeypatch.setattr(docker,'inspect',lambda value:{'IPAM':{'Config':[{'Subnet':'172.24.0.0/24'}]}})
    with pytest.raises(RuntimeReleaseError,match='explicitly'):docker.fixed_app_address(network)
    assert docker.fixed_app_address({**network,'subnet':'172.24.0.0/24'})=='172.24.0.2'


@pytest.mark.parametrize('subnet',['8.8.8.0/24','172.24.0.0/31','fd00::/64'])
def test_engine_allocation_must_be_supported_private_ipv4(monkeypatch,subnet):
    """The controller cannot turn public, IPv6 or too-small allocator output into an arbitrary app address."""
    docker=boundary();network=docker.spec('network','b'*64,'address-reservation',internal=True)
    monkeypatch.setattr(docker,'inspect',lambda value:{'IPAM':{'Config':[{'Subnet':subnet}]}})
    with pytest.raises(RuntimeReleaseError):docker.allocated_subnet(network)


def test_final_app_network_is_not_a_removable_address_reservation(monkeypatch):
    """The narrow cleanup method cannot delete a live/final network simply because its owner label matches."""
    docker=boundary();network=docker.spec('network','b'*64,'private',internal=True)
    monkeypatch.setattr(docker,'inspect',lambda value:(_ for _ in ()).throw(AssertionError('Wrong role must fail before daemon inspection')))
    with pytest.raises(RuntimeReleaseError,match='reservation'):docker.remove_address_reservation(network)


def test_address_reservation_with_any_endpoint_is_retained(monkeypatch):
    """A new unexpected member prevents removal; the method never force-disconnects another workload."""
    docker=boundary();network=docker.spec('network','b'*64,'address-reservation',internal=True,id='c'*64)
    monkeypatch.setattr(docker,'inspect',lambda value:{'Containers':{'d'*64:{'Name':'unexpected'}}})
    with pytest.raises(RuntimeReleaseError,match='endpoint'):docker.remove_address_reservation(network)
