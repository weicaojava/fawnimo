"""A fixed-space ceiling, derived balances and append-only ceiling change receipts."""
from sqlalchemy import BigInteger, CheckConstraint, Integer, String
from sqlalchemy.orm import Mapped, mapped_column
from app.core.db import Base


class WorkspaceBudget(Base):
    """One ceiling per fixed worker database; project ledgers remain the balance authority."""
    __tablename__ = 'workspace_budget'
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    currency: Mapped[str] = mapped_column(String(3), nullable=False, default='CNY')
    limit_minor: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    revision: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    __table_args__ = (CheckConstraint("id = 1 AND currency = 'CNY' AND limit_minor >= 0 AND revision >= 0"),)


class WorkspaceBudgetChange(Base):
    """Keep who changed a ceiling and from which revision without credentials or prompts."""
    __tablename__ = 'workspace_budget_changes'
    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    actor_id: Mapped[str] = mapped_column(String(64), nullable=False)
    previous_limit_minor: Mapped[int] = mapped_column(BigInteger, nullable=False)
    limit_minor: Mapped[int] = mapped_column(BigInteger, nullable=False)
    revision: Mapped[int] = mapped_column(BigInteger, nullable=False, unique=True)
    created_at: Mapped[int] = mapped_column(BigInteger, nullable=False)
