"""Integer minor-unit budgets; these records contain configured quotes, not real prices."""
from sqlalchemy import BigInteger, CheckConstraint, ForeignKey, String
from sqlalchemy.orm import Mapped, mapped_column
from app.core.db import Base


class ExecutionBudget(Base):
    """One explicitly configured currency and spending ceiling per project."""
    __tablename__ = 'execution_budgets'
    project_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    currency: Mapped[str] = mapped_column(String(16), nullable=False)
    limit_minor: Mapped[int] = mapped_column(BigInteger, nullable=False)
    reserved_minor: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    spent_minor: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    revision: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    __table_args__ = (CheckConstraint('limit_minor >= 0 AND reserved_minor >= 0 AND spent_minor >= 0 AND reserved_minor + spent_minor <= limit_minor'),)


class ExecutionBudgetEntry(Base):
    """A job's immutable quote and one-way accounting lifecycle, globally idempotent by job."""
    __tablename__ = 'execution_budget_entries'
    job_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    project_id: Mapped[str] = mapped_column(ForeignKey('execution_budgets.project_id'), nullable=False)
    quote_minor: Mapped[int] = mapped_column(BigInteger, nullable=False)
    actual_minor: Mapped[int | None] = mapped_column(BigInteger)
    state: Mapped[str] = mapped_column(String(16), nullable=False)
    __table_args__ = (CheckConstraint('quote_minor >= 0 AND (actual_minor IS NULL OR (actual_minor >= 0 AND actual_minor <= quote_minor))'), CheckConstraint("state IN ('reserved','unknown','released','settled')"),)
