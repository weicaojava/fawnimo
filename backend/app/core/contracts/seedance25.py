"""Fixed S25A software profile and trusted, non-importable transport configuration."""
from __future__ import annotations

from dataclasses import dataclass, field
import hashlib
import ipaddress
import json
import re
from types import MappingProxyType
from urllib.parse import urlsplit


MODEL_ID = 'doubao-seedance-2-5-260628'
SUPPORTED_DURATIONS = frozenset(range(4, 31))
OUTPUT_PROFILE = MappingProxyType({
    'ratio': '9:16',
    'resolution': '480p',
    'output_format': 'mp4',
    'generate_audio': False,
    'watermark': True,
})
ARK_BASE_URL = 'https://ark.cn-beijing.volces.com/api/v3'
CREATE_PATH = '/contents/generations/tasks'
DOWNLOAD_HOSTS = frozenset({'ark-acg-cn-beijing.tos-cn-beijing.volces.com'})
PREPARATION_ONLY = 'PREPARATION_ONLY'
REMOTE_ID_PATTERN = re.compile(r'[A-Za-z0-9_-]{1,128}', re.ASCII)
_VERSION_PATTERN = re.compile(r'[A-Za-z0-9_-]{1,128}', re.ASCII)


def _profile_hash(*, service_version: str, account_version: str, base_url: str,
                  download_hosts: frozenset[str], local_host: str | None,
                  local_port: int | None) -> str:
    """Bind the full fixed protocol/output profile and version references, never a key."""
    profile = {
        'schema': 's25a-text-only-v1',
        'model_id': MODEL_ID,
        'durations': sorted(SUPPORTED_DURATIONS),
        'references': [],
        'output': dict(OUTPUT_PROFILE),
        'service_version': service_version,
        'account_version': account_version,
        'base_url': base_url,
        'create_path': CREATE_PATH,
        'download_hosts': sorted(download_hosts),
        'trusted_local_host': local_host,
        'trusted_local_port': local_port,
        'control': {'timeout': 15, 'max_bytes': 65536, 'redirects': 0},
        'download': {'timeout': 30, 'total_timeout': 45, 'redirects': 3},
        'reconciliation': 'unsupported-no-resubmission',
        'cancellation': 'query-only',
    }
    encoded = json.dumps(profile, ensure_ascii=False, sort_keys=True,
                         separators=(',', ':'), allow_nan=False).encode('utf-8')
    return hashlib.sha256(encoded).hexdigest()


PREPARATION_PROFILE_HASH = _profile_hash(
    service_version=PREPARATION_ONLY, account_version=PREPARATION_ONLY,
    base_url=ARK_BASE_URL, download_hosts=DOWNLOAD_HOSTS,
    local_host=None, local_port=None,
)


def valid_remote_id(value: object) -> bool:
    """Accept only one bounded ASCII path segment, never a URL or escaped path."""
    return isinstance(value, str) and REMOTE_ID_PATTERN.fullmatch(value) is not None


@dataclass(frozen=True, slots=True)
class Seedance25Configuration:
    """Application-owned credentials and exact origins, separate from project JSON.

    A loopback fixture must independently name its one host/port and download
    host. Production supports only the documented Ark origin and one exact CDN.
    Configuration construction does not read files, environment variables or DNS.
    """

    api_key: str = field(repr=False)
    service_version: str
    account_version: str
    base_url: str = ARK_BASE_URL
    download_hosts: frozenset[str] = DOWNLOAD_HOSTS
    trusted_local_host: str | None = None
    trusted_local_port: int | None = None

    def __post_init__(self) -> None:
        """Reject routing/profile overrides and freeze the independently trusted hosts."""
        if (not isinstance(self.api_key, str) or not 1 <= len(self.api_key) <= 8192
                or not self.api_key.isascii()
                or any(ord(char) <= 32 or ord(char) >= 127 for char in self.api_key)):
            raise ValueError('Invalid trusted credential')
        for version in (self.service_version, self.account_version):
            if (not isinstance(version, str) or _VERSION_PATTERN.fullmatch(version) is None
                    or version == PREPARATION_ONLY):
                raise ValueError('Independent executable service/account version required')
        if not isinstance(self.download_hosts, (set, frozenset)):
            raise ValueError('Exact download host set required')
        hosts = frozenset(self.download_hosts)
        if any(not isinstance(host, str) for host in hosts):
            raise ValueError('Invalid download host')
        object.__setattr__(self, 'download_hosts', hosts)
        if self.trusted_local_host is None:
            if (self.base_url != ARK_BASE_URL or self.trusted_local_port is not None
                    or hosts != DOWNLOAD_HOSTS):
                raise ValueError('Untrusted production origin or download hosts')
            return
        host = self.trusted_local_host
        if not isinstance(host, str) or host != host.lower():
            raise ValueError('Invalid fixture host')
        try:
            loopback = host == 'localhost' or ipaddress.ip_address(host).is_loopback
        except ValueError:
            loopback = False
        port = self.trusted_local_port
        if not loopback or type(port) is not int or not 1 <= port <= 65535:
            raise ValueError('Explicit fixture loopback host and port required')
        origin_host = '[' + host + ']' if ':' in host else host
        expected = f'http://{origin_host}:{port}/api/v3'
        if self.base_url != expected or hosts != frozenset({host}):
            raise ValueError('Fixture origin and download hosts must be exact')

    @property
    def control_host(self) -> str:
        """Return the single constructor-validated control hostname."""
        return urlsplit(self.base_url).hostname or ''

    @property
    def profile_hash(self) -> str:
        """Identify the complete immutable transport profile without credential values."""
        return _profile_hash(
            service_version=self.service_version, account_version=self.account_version,
            base_url=self.base_url, download_hosts=self.download_hosts,
            local_host=self.trusted_local_host, local_port=self.trusted_local_port,
        )

    @property
    def binding_id(self) -> str:
        """Supply the profile hash to the existing durable policy fingerprint."""
        return 's25a:' + self.profile_hash
