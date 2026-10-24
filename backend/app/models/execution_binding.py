"""Immutable ownership of each durable job by a trusted runtime configuration."""
from sqlalchemy import ForeignKey, String
from sqlalchemy.orm import Mapped, mapped_column
from app.core.db import Base


class ExecutionBinding(Base):
    """Persist the service/account/config fingerprint without storing credentials."""
    __tablename__='execution_bindings'
    job_id: Mapped[str]=mapped_column(ForeignKey('execution_jobs.id',ondelete='RESTRICT'),primary_key=True)
    binding_hash: Mapped[str]=mapped_column(String(64),nullable=False)
