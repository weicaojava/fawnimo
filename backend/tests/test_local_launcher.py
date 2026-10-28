"""Local launcher owns the HTTP drain bound; workers retain their independent cleanup contracts."""
import pytest
from run_local import build_server


def test_local_launcher_is_loopback_with_finite_shutdown():
    """A closed HTTP listener must not leave application lifespan cleanup waiting without a deadline."""
    server = build_server(18841)
    assert server.config.host == '127.0.0.1'
    assert server.config.timeout_graceful_shutdown == 5
    assert server.config.timeout_keep_alive == 2
    assert server.config.port == 18841


@pytest.mark.parametrize('port', [True, 0, 65536, 8000.0])
def test_local_launcher_rejects_ambiguous_ports(port):
    """Runtime arguments do not silently turn booleans or floats into network configuration."""
    with pytest.raises(ValueError):
        build_server(port)
