"""Add graph journal guards without rewriting chapter rows, existing receipts or source clocks."""
from sqlalchemy import text
from app.core.db import Base
from app.models.shot_graph import SQLITE_GRAPH_TRIGGERS


async def migrate_shot_graph(engine):
    """Install additive same-column scope/CAS guards, including already-created v1 journal tables."""
    if engine.dialect.name != 'sqlite':
        return
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
        for name in ('shot_graph_cursors', 'shot_graph_receipts'):
            actual = {row[1] for row in (await connection.execute(text(f'PRAGMA table_info({name})'))).all()}
            if actual != {column.name for column in Base.metadata.tables[name].columns}:
                raise RuntimeError('Unsupported shot graph journal schema')
        for statement in SQLITE_GRAPH_TRIGGERS.values():
            await connection.execute(text(statement))
        for operation in ('UPDATE', 'DELETE'):
            await connection.execute(text(f"CREATE TRIGGER IF NOT EXISTS shot_graph_receipts_{operation.lower()}_immutable BEFORE {operation} ON shot_graph_receipts BEGIN SELECT RAISE(ABORT, 'Immutable shot graph receipt'); END"))
        if (await connection.execute(text('PRAGMA foreign_key_check'))).first():
            raise RuntimeError('Shot graph foreign key conflict')
