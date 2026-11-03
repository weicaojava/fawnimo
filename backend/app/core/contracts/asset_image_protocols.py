"""Explicit single-image wire profiles; model parameters cannot invent capabilities."""
from dataclasses import dataclass
import ipaddress
import re

LEGACY_IMAGE_PROTOCOL = 'openai_b64_v1'
SEEDREAM_IMAGE_PROTOCOL = 'volcengine_seedream_json_v1'
GPT_IMAGE_PROTOCOL = 'openai_gpt_image_json_v1'
GPT_IMAGE_PROFILE = 'gpt25_single_medium_png_v1'
GPT_IMAGE_MODELS = frozenset({'gpt-image-2.5-sunburst-2026-09-08', 'gpt-image-2.5-flare-2026-09-08'})
GPT_IMAGE_SIZES = ('1024x1024', '1536x1024', '1024x1536')
SEEDREAM_MODELS = {
    'doubao-seedream-5-0-pro-260628': 'single_v5',
    'doubao-seedream-5-0-flash-260915': 'single_v5',
    'doubao-seedream-4-5-251128': 'single_v4',
    'doubao-seedream-4-0-250828': 'single_v4',
}
SEEDREAM_SIZES = ('2048x2048', '2848x1600', '1600x2848')


@dataclass(frozen=True)
class AssetImageProtocol:
    """Bind supported inputs/outputs and a server-selected exact result-host policy."""
    protocol: str
    profile: str
    sizes: tuple[str, ...]
    result_hosts: tuple[str, ...] = ()
    max_references: int = 0

    @property
    def response_formats(self):
        """URL output needs an explicit download policy; reference support never implies it."""
        return ('b64_json', 'url') if self.result_hosts else ('b64_json',)


def result_host_policy(value):
    """Reject schemes, wildcards, ports, IP literals and local aliases in administrator-declared CDN hosts."""
    if value is None:
        return ()
    if not isinstance(value, list) or len(value) > 8:
        raise ValueError('Invalid image result host policy')
    hosts = []
    for host in value:
        if not isinstance(host, str) or host != host.strip().lower() or len(host) > 253 or not re.fullmatch(r'[a-z0-9](?:[a-z0-9.-]*[a-z0-9])?', host):
            raise ValueError('Invalid image result host policy')
        labels = host.split('.')
        if len(labels) < 2 or any(not label or len(label) > 63 or label.startswith('-') or label.endswith('-') for label in labels) or host.endswith(('.localhost', '.local', '.internal')):
            raise ValueError('Invalid image result host policy')
        try:
            ipaddress.ip_address(host)
        except ValueError:
            pass
        else:
            raise ValueError('IP image result hosts are unsupported')
        hosts.append(host)
    if len(set(hosts)) != len(hosts):
        raise ValueError('Duplicate image result hosts')
    return tuple(sorted(hosts))


def asset_image_protocol(*, provider: str, model: str, params: dict) -> AssetImageProtocol:
    """Resolve only declared profiles; unknown model names and unsupported input modes fail closed."""
    protocol = params.get('asset_image_protocol')
    if provider == 'openai' and protocol == LEGACY_IMAGE_PROTOCOL:
        return AssetImageProtocol(protocol, 'legacy_text_b64', ('256x256', '512x512', '1024x1024'))
    if provider == 'openai' and protocol == GPT_IMAGE_PROTOCOL and model in GPT_IMAGE_MODELS:
        if params.get('asset_image_result_hosts') not in (None, []):
            raise ValueError('GPT Image does not use a URL result-host policy')
        return AssetImageProtocol(protocol, GPT_IMAGE_PROFILE, GPT_IMAGE_SIZES, (), 3)
    if provider == 'volcengine' and protocol == SEEDREAM_IMAGE_PROTOCOL and model in SEEDREAM_MODELS:
        return AssetImageProtocol(protocol, SEEDREAM_MODELS[model], SEEDREAM_SIZES,
                                  result_host_policy(params.get('asset_image_result_hosts')), 3)
    raise ValueError('Unsupported declared image protocol or exact model profile')
