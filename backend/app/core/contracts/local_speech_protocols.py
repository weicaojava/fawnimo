"""Stable installed-engine voice identities, shared without importing application settings or credentials."""
import hashlib
import json
import re

WINDOWS_PROFILE = 'windows_sapi_pcm_v1'
LINUX_PROFILE = 'linux_espeak_pcm_v1'


def voice_identity(profile, voice, engine_fingerprint=None):
    """Preserve existing SAPI IDs and bind Linux selection to its explicit installed engine witness."""
    if not isinstance(voice, dict) or set(voice) != {'name', 'culture'}:
        raise ValueError('Invalid installed voice fields')
    for field, maximum in (('name', 200), ('culture', 32)):
        value = voice[field]
        if not isinstance(value, str) or not 1 <= len(value) <= maximum or not value.strip() or any(ord(char) < 32 for char in value):
            raise ValueError('Invalid installed voice text')
        value.encode('utf8')
    if profile == WINDOWS_PROFILE:
        prefix, payload = 'sapi_', voice
    elif profile == LINUX_PROFILE:
        if not isinstance(engine_fingerprint, str) or re.fullmatch(r'[a-f0-9]{64}', engine_fingerprint) is None:
            raise ValueError('Missing installed Linux engine witness')
        prefix, payload = 'espeak_', {'profile': profile, 'engine': engine_fingerprint, 'voice': voice}
    else:
        raise ValueError('Unsupported local speech profile')
    return prefix+hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(',', ':'), ensure_ascii=False).encode()).hexdigest()
