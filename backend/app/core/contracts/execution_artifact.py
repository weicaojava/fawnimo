"""Bounded artifact delivery contract shared by transports and publication services."""
from dataclasses import dataclass


@dataclass(frozen=True)
class DownloadedArtifact:
    """Downloaded bytes are not persisted or trusted until the service validates them."""
    name: str
    data: bytes
