"""Closed native-video model/protocol metadata, independent of credentials, runtime activation and provider selection heuristics."""
from dataclasses import dataclass
from types import MappingProxyType

from app.core.contracts.seedance25 import MODEL_ID as SEEDANCE_MODEL,SUPPORTED_DURATIONS as SEEDANCE_DURATIONS
from app.core.contracts.seedance_references import REFERENCE_PROTOCOL as SEEDANCE_REFERENCE
from app.core.contracts.wan30 import MODEL_ID as WAN_MODEL,TEXT_PROTOCOL as WAN_TEXT,REFERENCE_PROTOCOL as WAN_REFERENCE,SUPPORTED_DURATIONS as WAN_DURATIONS


@dataclass(frozen=True,slots=True)
class NativeVideoProfile:
    """Describe only an explicitly implemented supported subset; no metadata grants transport or budget authority."""
    model_name:str
    provider_key:str
    text_protocol:str
    reference_protocol:str
    durations:frozenset[int]
    supports_reference_video:bool


SEEDANCE=NativeVideoProfile(SEEDANCE_MODEL,'volcengine','seedance25_text_v1',SEEDANCE_REFERENCE,SEEDANCE_DURATIONS,True)
WAN=NativeVideoProfile(WAN_MODEL,'aliyun_bailian',WAN_TEXT,WAN_REFERENCE,WAN_DURATIONS,False)
PROFILES=MappingProxyType({SEEDANCE.model_name:SEEDANCE,WAN.model_name:WAN})
REFERENCE_PROTOCOLS=frozenset({SEEDANCE.reference_protocol,WAN.reference_protocol})
NATIVE_PROTOCOLS=frozenset({SEEDANCE.text_protocol,SEEDANCE.reference_protocol,WAN.text_protocol,WAN.reference_protocol})


def declared_profile(model_name,protocol):
    """Match both exact model and explicit protocol; category/provider aliases alone cannot select a native adapter."""
    if not isinstance(model_name,str) or not isinstance(protocol,str):return None
    profile=PROFILES.get(model_name)
    return profile if profile is not None and protocol in (profile.text_protocol,profile.reference_protocol) else None


def reference_protocol(protocol):
    """Expose only a recognized reference-price identity, preserving the legacy omission for text-only quotes."""
    return protocol if isinstance(protocol,str) and protocol in REFERENCE_PROTOCOLS else None
