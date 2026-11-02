"""Bind a separate fixed reference transport and optional application-owned media delivery origin."""
from dataclasses import dataclass
import hashlib
import ipaddress
import json
import re
from urllib.parse import urlsplit

from app.core.contracts.seedance25 import Seedance25Configuration
from app.core.contracts.seedance_references import REFERENCE_PROTOCOL


def checked_delivery_origin(value: str | None, *, trusted_local_origin: str | None = None) -> str | None:
    """Accept one exact server-owned HTTPS origin; only independently injected loopback fixtures may use HTTP."""
    if value is None:
        if trusted_local_origin is not None:
            raise ValueError('Explicit fixture media origin required')
        return None
    if not isinstance(value, str) or value != value.strip() or len(value) > 512:
        raise ValueError('Invalid media delivery origin')
    parsed = urlsplit(value)
    host = parsed.hostname
    if (not host or parsed.username or parsed.password or parsed.path or parsed.query or parsed.fragment
            or value != parsed.scheme+'://'+parsed.netloc or parsed.netloc != parsed.netloc.lower()):
        raise ValueError('Invalid media delivery origin')
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        address = None
    if trusted_local_origin is not None:
        if (value != trusted_local_origin or parsed.scheme != 'http' or parsed.port is None
                or not 1 <= parsed.port <= 65535 or not (host == 'localhost' or address is not None and address.is_loopback)):
            raise ValueError('Invalid fixture media origin')
        return value
    if (parsed.scheme != 'https' or parsed.port not in (None, 443) or address is not None
            or host.endswith(('.localhost', '.local', '.internal', '.test', '.invalid', '.example'))
            or '.' not in host or len(host) > 253 or re.fullmatch(r'[a-z0-9.-]+', host) is None
            or any(not label or len(label) > 63 or label.startswith('-') or label.endswith('-') for label in host.split('.'))):
        raise ValueError('Invalid public media origin')
    return value


@dataclass(frozen=True, slots=True)
class SeedanceReferenceConfiguration(Seedance25Configuration):
    """Retain the original Ark control/download boundaries while separately binding conditions and delivery policy."""
    input_delivery_origin: str | None = None
    trusted_local_media_origin: str | None = None

    def __post_init__(self):
        """Do not borrow the control-port fixture exception as authorization for a media delivery origin."""
        Seedance25Configuration.__post_init__(self)
        checked_delivery_origin(self.input_delivery_origin, trusted_local_origin=self.trusted_local_media_origin)
        if self.trusted_local_media_origin is not None and self.trusted_local_host is None:
            raise ValueError('Fixture media origin needs an independently configured fixture transport')

    @property
    def profile_hash(self) -> str:
        """Hash public protocol policy only; no API key, media bytes, capability token or key digest enters this identity."""
        value = {'schema': REFERENCE_PROTOCOL,
            'base_transport_profile': Seedance25Configuration.profile_hash.fget(self),
            'input_delivery_origin': self.input_delivery_origin,
            'trusted_local_media_origin': self.trusted_local_media_origin,
            'modes': ['reference', 'first_frame', 'first_last_frames'],
            'references': {'image_count': 3, 'image_bytes_each': 4*1024*1024, 'image_total_bytes': 12*1024*1024,
                'image_min_edge': 300, 'image_max_edge': 4096, 'image_max_pixels': 16_000_000,
                'image_mimes': ['image/png', 'image/jpeg'], 'image_ratio': [0.4, 2.5],
                'frame_ratio': '9:16', 'video_count': 1 if self.input_delivery_origin else 0,
                'video_bytes': 32*1024*1024, 'video_seconds': [2, 10], 'video_fps': [24, 25, 30],
                'video_edges': [300, 1920], 'video_pixels': [407696, 2073600], 'video_ratio': [0.4, 2.5],
                'video_codecs': ['h264', 'hevc'], 'audio_codecs': ['aac', 'mp3'],
                'audio_channels': [1, 2], 'audio_rates': [8000, 48000],
                'delivery_path': '/api/v1/execution-reference-media/', 'delivery_ttl_seconds': 3600,
                'delivery_access_limit': 16},
            'output': {'reference_ratio': '9:16', 'frames_ratio': 'adaptive-with-actual-9:16-input',
                'resolution': '480p', 'format': 'mp4', 'audio': False, 'watermark': True},
            'request_bytes': 18*1024*1024, 'automatic_format_or_input_fallback': False}
        return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()).hexdigest()

    @property
    def binding_id(self) -> str:
        """Keep the new execution identity distinct even when its model and account match an old text service."""
        return 's25-reference:'+self.profile_hash
