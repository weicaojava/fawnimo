"""Real file-backed SQLite contention tests; amounts are synthetic test units."""
import asyncio
import pytest
from sqlalchemy import event, select
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker
from app.models.execution_budget import ExecutionBudget, ExecutionBudgetEntry
from app.services.worker.execution_budget import BudgetError, configure, reserve, release, settle, mark_unknown, finish


async def scenario(path, check):
    """Create only ledger tables in a disposable database and dispose connections."""
    engine = create_async_engine('sqlite+aiosqlite:///' + str(path))
    @event.listens_for(engine.sync_engine, 'connect')
    def pragmas(connection, _):
        """Bound lock waits and enable actual foreign key enforcement per connection."""
        connection.execute('PRAGMA busy_timeout=10000')
        connection.execute('PRAGMA foreign_keys=ON')
    async with engine.begin() as conn:
        await conn.run_sync(lambda c: ExecutionBudget.__table__.create(c))
        await conn.run_sync(lambda c: ExecutionBudgetEntry.__table__.create(c))
    maker = async_sessionmaker(engine, expire_on_commit=False)
    async def call(fn, *args):
        """Use separate real connections/transactions for every operation."""
        async with maker() as db:
            return await fn(db, *args)
    async def balances():
        """Read committed counters independently of service identity maps."""
        async with maker() as db:
            row = await db.get(ExecutionBudget, 'p')
            return row.reserved_minor, row.spent_minor
    try:
        await call(configure, 'p', 'TEST', 100)
        await check(call, balances, maker)
    finally:
        await engine.dispose()


def test_real_sqlite_concurrent_ceiling(tmp_path):
    """Concurrent distinct jobs cannot reserve beyond a shared 100-unit ceiling."""
    async def check(call, balances, _maker):
        """Race twenty independent writers and verify exactly ten reservations."""
        results = await asyncio.gather(*(call(reserve, 'p', str(i), 10, 'TEST') for i in range(20)), return_exceptions=True)
        assert results.count('reserved') == 10
        assert sum(isinstance(r, BudgetError) for r in results) == 10
        assert await balances() == (100, 0)
    asyncio.run(scenario(tmp_path/'concurrent.db', check))


def test_concurrent_same_job_and_terminal_calls(tmp_path):
    """Retry storms reserve and settle once, not once per request."""
    async def check(call, balances, _maker):
        """Race identical operations and assert a single debit and final charge."""
        assert await asyncio.gather(*(call(reserve, 'p', 'j', 60, 'TEST') for _ in range(12))) == ['reserved']*12
        assert await balances() == (60, 0)
        with pytest.raises(BudgetError):
            await call(reserve, 'p', 'j', 61, 'TEST')
        assert await asyncio.gather(*(call(settle, 'p', 'j', 45) for _ in range(12))) == ['settled']*12
        assert await balances() == (0, 45)
        with pytest.raises(BudgetError):
            await call(settle, 'p', 'j', 44)
        with pytest.raises(BudgetError):
            await call(release, 'p', 'j')
        assert await call(reserve, 'p', 'j', 60, 'TEST') == 'settled'
        assert await balances() == (0, 45)
    asyncio.run(scenario(tmp_path/'idempotent.db', check))


def test_unknown_holds_and_reconciliation(tmp_path):
    """Unknown acceptance holds funds until settlement or explicit proven rejection."""
    async def check(call, balances, _maker):
        """Exercise timeout holds, rejected oversettlement and idempotent release."""
        await call(reserve, 'p', 'j', 80, 'TEST')
        await call(mark_unknown, 'p', 'j')
        with pytest.raises(BudgetError):
            await call(release, 'p', 'j')
        with pytest.raises(BudgetError):
            await call(settle, 'p', 'j', 81)
        assert await balances() == (80, 0)
        await call(finish, 'p', 'j', 'release_reconciled')
        await call(release, 'p', 'j')
        assert await balances() == (0, 0)
        with pytest.raises(BudgetError):
            await call(settle, 'p', 'j', 5)
        await call(reserve, 'p', 'k', 100, 'TEST')
        await call(mark_unknown, 'p', 'k')
        await call(settle, 'p', 'k', 90)
        assert await balances() == (0, 90)
    asyncio.run(scenario(tmp_path/'unknown.db', check))


@pytest.mark.parametrize('quote', [None, -1, 1.5, True, '10', 2**63])
def test_unknown_and_noninteger_quotes_rejected(tmp_path, quote):
    """Invalid or unknown prices must never authorize a reservation."""
    async def check(call, balances, _maker):
        """Verify all rejected inputs leave counters unchanged."""
        with pytest.raises(BudgetError):
            await call(reserve, 'p', 'j', quote, 'TEST')
        assert await balances() == (0, 0)
    asyncio.run(scenario(tmp_path/'invalid.db', check))


def test_project_currency_and_session_boundaries(tmp_path):
    """Accounting cannot silently change currency, reuse jobs or commit caller work."""
    async def check(call, balances, maker):
        """Reject ownership/configuration mismatch and nested transactions."""
        await call(configure, 'p', 'TEST', 100)
        for fn, args in [(configure, ('p','USD',100)), (reserve, ('p','x',1,'USD')), (reserve, ('absent','x',1,'TEST'))]:
            with pytest.raises(BudgetError):
                await call(fn, *args)
        await call(reserve, 'p', 'j', 10, 'TEST')
        await call(configure, 'other', 'TEST', 100)
        with pytest.raises(BudgetError):
            await call(reserve, 'other', 'j', 10, 'TEST')
        async with maker() as db:
            await db.execute(select(ExecutionBudget))
            with pytest.raises(BudgetError):
                await release(db, 'p', 'j')
            await db.rollback()
        await call(release, 'p', 'j')
        await call(release, 'p', 'j')
        assert await call(reserve, 'p', 'j', 10, 'TEST') == 'released'
        assert await balances() == (0, 0)
    asyncio.run(scenario(tmp_path/'boundaries.db', check))
