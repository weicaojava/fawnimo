"""Retire aggregate workspace caps while preserving project balances and historical receipts."""
from sqlalchemy import text
from app.models.project_budget import ProjectBudgetChange

RETIRED_TABLES = ('workspace_budget', 'workspace_budget_changes')
RETIRED_GUARDS = (
    'workspace_budget_no_delete', 'workspace_budget_identity', 'workspace_budget_lower_bound',
    'workspace_budget_changes_no_update', 'workspace_budget_changes_no_delete',
    'workspace_budget_project_insert', 'workspace_budget_project_update',
    'workspace_budget_revision_insert', 'workspace_budget_revision_update',
    'workspace_budget_project_identity', 'workspace_budget_project_no_delete',
)


async def migrate_project_budget(engine) -> None:
    """Atomically archive the retired controls; no project amount or task entry is rewritten.

    The fixed workspace boundary must be checked again after this migration, as
    table renames retain data/constraints but need guards under their new names.
    """
    if engine.dialect.name != 'sqlite':
        raise RuntimeError('Project budget migration requires SQLite')
    async with engine.begin() as connection:
        await connection.execute(text('BEGIN IMMEDIATE'))
        tables = set((await connection.execute(text("SELECT name FROM sqlite_master WHERE type='table'"))).scalars())
        for table in RETIRED_TABLES:
            if table in tables and 'retired_' + table in tables:
                raise RuntimeError('Budget archive already exists beside active legacy data')
        for name in RETIRED_GUARDS:
            await connection.execute(text(f'DROP TRIGGER IF EXISTS "{name}"'))
        for table in RETIRED_TABLES:
            if table in tables:
                await connection.execute(text(f'ALTER TABLE "{table}" RENAME TO "retired_{table}"'))
                tables.add('retired_' + table)
        await connection.run_sync(lambda db: ProjectBudgetChange.__table__.create(db, checkfirst=True))
        guards = {
            'project_budget_identity': "BEFORE UPDATE OF project_id,currency ON execution_budgets WHEN NEW.project_id IS NOT OLD.project_id OR NEW.currency IS NOT OLD.currency BEGIN SELECT RAISE(ABORT,'Project budget identity is immutable'); END",
            'project_budget_no_delete': "BEFORE DELETE ON execution_budgets BEGIN SELECT RAISE(ABORT,'Project budget cannot be deleted'); END",
        }
        for table in ('project_budget_changes', *(f'retired_{name}' for name in RETIRED_TABLES if f'retired_{name}' in tables)):
            for action in ('UPDATE', 'DELETE'):
                guards[f'{table}_no_{action.lower()}'] = f"BEFORE {action} ON {table} BEGIN SELECT RAISE(ABORT,'Immutable budget history'); END"
        for name, body in guards.items():
            expected = f'CREATE TRIGGER {name} {body}'
            actual = await connection.scalar(text("SELECT sql FROM sqlite_master WHERE type='trigger' AND name=:name"), {'name':name})
            if actual is None:
                await connection.execute(text(expected))
            elif ' '.join(actual.split()) != ' '.join(expected.split()):
                raise RuntimeError('Project budget guard differs from supported schema')
