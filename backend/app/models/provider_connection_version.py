"""Credential-free connection clocks survive deletion and reuse of a provider identifier."""
from sqlalchemy import BigInteger, CheckConstraint, String
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base


class ProviderConnectionVersion(Base):
    """Retain a monotonic tombstone so old jobs cannot follow a recreated provider's key."""

    __tablename__ = 'provider_connection_versions'
    # Deliberately no cascading FK: deleting and recreating the same provider ID
    # must advance its clock rather than make an old queued binding valid again.
    provider_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    revision: Mapped[int] = mapped_column(BigInteger, nullable=False)
    __table_args__ = (CheckConstraint('revision >= 1 AND revision <= 9223372036854775806'),)
