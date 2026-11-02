"""Typed project-media metadata for selection without exposing internal filesystem locations."""
from datetime import datetime
from typing import Literal
from pydantic import BaseModel, ConfigDict


class ProductionMediaRead(BaseModel):
    """Keep actual owned media metadata usable in generated clients; content remains behind its scoped endpoint."""
    model_config = ConfigDict(from_attributes=True)
    id: str
    project_id: str
    name: str
    mime: Literal['image/png', 'image/jpeg', 'image/webp', 'video/mp4', 'audio/wav']
    sha256: str
    size: int
    created_at: datetime
    updated_at: datetime
