"""Append-only receipts for explicitly changed individual project spending ceilings."""
from sqlalchemy import BigInteger, ForeignKey, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column
from app.core.db import Base
from app.models.execution_budget import ExecutionBudget  # noqa: F401


class ProjectBudgetChange(Base):
    """Keep project, trusted actor and prior/new ceilings without model inputs or secrets."""
    __tablename__ = 'project_budget_changes'
    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    project_id: Mapped[str] = mapped_column(ForeignKey('execution_budgets.project_id'), nullable=False)
    actor_id: Mapped[str] = mapped_column(String(64), nullable=False)
    previous_limit_minor: Mapped[int | None] = mapped_column(BigInteger)
    limit_minor: Mapped[int] = mapped_column(BigInteger, nullable=False)
    revision: Mapped[int] = mapped_column(BigInteger, nullable=False)
    created_at: Mapped[int] = mapped_column(BigInteger, nullable=False)
    __table_args__ = (UniqueConstraint('project_id', 'revision'),)
