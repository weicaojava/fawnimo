"""Durable sequential Agent attempts; node audit survives edits or deletion of its source config."""
from datetime import datetime

from sqlalchemy import ForeignKey, Index, Integer, JSON, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base


class AgentRun(Base):
    """Persist admission before HTTP and use a compare-and-swap lease to prevent duplicate workers."""
    __tablename__ = 'agent_execution_runs'
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    kind: Mapped[str] = mapped_column(String(32), nullable=False)
    scope_key: Mapped[str] = mapped_column(String(128), nullable=False)
    request_key: Mapped[str] = mapped_column(String(128), nullable=False)
    request_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    agent_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    agent_name: Mapped[str] = mapped_column(String(255), nullable=False)
    agent_version: Mapped[int] = mapped_column(Integer, nullable=False)
    config_snapshot: Mapped[dict] = mapped_column(JSON, nullable=False)
    model_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    model_name: Mapped[str] = mapped_column(String(255), nullable=False)
    model_binding: Mapped[dict] = mapped_column(JSON, nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, index=True)
    input_text: Mapped[str] = mapped_column(Text, nullable=False)
    output_text: Mapped[str] = mapped_column(Text, nullable=False, default='')
    error: Mapped[str] = mapped_column(Text, nullable=False, default='')
    max_tokens: Mapped[int] = mapped_column(Integer, nullable=False)
    attempt: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    retry_of: Mapped[str | None] = mapped_column(String(64), nullable=True)
    lease_token: Mapped[str | None] = mapped_column(String(64), nullable=True)
    lease_expires_at: Mapped[datetime | None] = mapped_column(nullable=True)
    created_at: Mapped[datetime] = mapped_column(nullable=False)
    started_at: Mapped[datetime | None] = mapped_column(nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(nullable=True)
    __table_args__ = (UniqueConstraint('scope_key', 'request_key', name='uq_agent_run_request'), Index('ix_agent_run_lease', 'status', 'lease_expires_at'))


class AgentNodeRun(Base):
    """Record one concrete node execution and its dependency result without credential-bearing headers."""
    __tablename__ = 'agent_execution_nodes'
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    run_id: Mapped[str] = mapped_column(ForeignKey('agent_execution_runs.id', ondelete='CASCADE'), nullable=False, index=True)
    node_id: Mapped[str] = mapped_column(String(64), nullable=False)
    position: Mapped[int] = mapped_column(Integer, nullable=False)
    label: Mapped[str] = mapped_column(String(80), nullable=False)
    type: Mapped[str] = mapped_column(String(16), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    input_text: Mapped[str] = mapped_column(Text, nullable=False, default='')
    output_text: Mapped[str] = mapped_column(Text, nullable=False, default='')
    prompt: Mapped[str] = mapped_column(Text, nullable=False, default='')
    parameters: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    usage: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    error: Mapped[str] = mapped_column(Text, nullable=False, default='')
    started_at: Mapped[datetime | None] = mapped_column(nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(nullable=True)
    __table_args__ = (UniqueConstraint('run_id', 'node_id', name='uq_agent_run_node'), UniqueConstraint('run_id', 'position', name='uq_agent_run_position'))
