"""Install aggregate spending guards only in explicitly managed fixed-space databases."""
from sqlalchemy import select, text
from app.models.workspace_budget import WorkspaceBudget, WorkspaceBudgetChange


def guard_statements() -> dict[str, str]:
    """Guard every project balance mutation, including cancellation and recovery SQL paths."""
    total = '(SELECT COALESCE(SUM(reserved_minor + spent_minor), 0) FROM execution_budgets)'
    ceiling = '(SELECT limit_minor FROM workspace_budget WHERE id=1)'
    statements = {
        'workspace_budget_no_delete': "BEFORE DELETE ON workspace_budget BEGIN SELECT RAISE(ABORT,'Workspace budget is immutable'); END",
        'workspace_budget_identity': "BEFORE UPDATE OF id,currency ON workspace_budget WHEN NEW.id IS NOT OLD.id OR NEW.currency IS NOT OLD.currency BEGIN SELECT RAISE(ABORT,'Workspace budget identity is immutable'); END",
        'workspace_budget_lower_bound': f"BEFORE UPDATE OF limit_minor ON workspace_budget WHEN NEW.limit_minor < {total} BEGIN SELECT RAISE(ABORT,'Workspace budget holds retained'); END",
        'workspace_budget_changes_no_update': "BEFORE UPDATE ON workspace_budget_changes BEGIN SELECT RAISE(ABORT,'Immutable budget receipt'); END",
        'workspace_budget_changes_no_delete': "BEFORE DELETE ON workspace_budget_changes BEGIN SELECT RAISE(ABORT,'Immutable budget receipt'); END",
    }
    for operation in ('INSERT', 'UPDATE'):
        delta = 'NEW.reserved_minor + NEW.spent_minor'
        changed = '1'
        if operation == 'UPDATE':
            delta += ' - OLD.reserved_minor - OLD.spent_minor'
            changed = 'NEW.reserved_minor IS NOT OLD.reserved_minor OR NEW.spent_minor IS NOT OLD.spent_minor'
        statements[f'workspace_budget_project_{operation.lower()}'] = f"""BEFORE {operation} ON execution_budgets
            WHEN ({delta}) > 0 AND (NEW.currency != 'CNY' OR {ceiling} IS NULL OR {total} + ({delta}) > {ceiling})
            BEGIN SELECT RAISE(ABORT,'Workspace spending ceiling exceeded'); END"""
        statements[f'workspace_budget_revision_{operation.lower()}'] = f"""AFTER {operation} ON execution_budgets
            WHEN {changed} BEGIN UPDATE workspace_budget SET revision=revision+1 WHERE id=1; END"""
    statements['workspace_budget_project_identity'] = "BEFORE UPDATE OF project_id,currency ON execution_budgets WHEN NEW.project_id IS NOT OLD.project_id OR NEW.currency IS NOT OLD.currency BEGIN SELECT RAISE(ABORT,'Project budget identity is immutable'); END"
    statements['workspace_budget_project_no_delete'] = "BEFORE DELETE ON execution_budgets BEGIN SELECT RAISE(ABORT,'Project budget cannot be deleted'); END"
    return {name: f'CREATE TRIGGER {name} {body}' for name, body in statements.items()}


async def migrate_workspace_budget(engine) -> None:
    """Create a zero ceiling before worker startup; never authorize or erase existing spending."""
    if engine.dialect.name != 'sqlite':
        raise RuntimeError('Workspace spending guards require SQLite')
    async with engine.begin() as connection:
        await connection.run_sync(lambda db: WorkspaceBudget.__table__.create(db, checkfirst=True))
        await connection.run_sync(lambda db: WorkspaceBudgetChange.__table__.create(db, checkfirst=True))
        current = (await connection.execute(select(WorkspaceBudget.id))).scalars().all()
        if not current:
            used = await connection.scalar(text('SELECT COALESCE(SUM(reserved_minor+spent_minor),0) FROM execution_budgets'))
            if used:
                raise RuntimeError('Existing spending requires an explicit offline budget migration')
            await connection.execute(WorkspaceBudget.__table__.insert().values(id=1, currency='CNY', limit_minor=0, revision=0))
        elif current != [1]:
            raise RuntimeError('Invalid fixed-space budget identity')
        for name, statement in guard_statements().items():
            actual = await connection.scalar(text("SELECT sql FROM sqlite_master WHERE type='trigger' AND name=:name"), {'name': name})
            if actual is None:
                await connection.execute(text(statement))
            elif ' '.join(actual.split()) != ' '.join(statement.split()):
                raise RuntimeError('Workspace budget guard differs from supported schema')
