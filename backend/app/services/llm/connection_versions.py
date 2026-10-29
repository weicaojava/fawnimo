"""Pin model calls to provider changes without hashing or retaining any credential value."""
from fastapi import HTTPException
from sqlalchemy import func, select, text

from app.models.provider_connection_version import ProviderConnectionVersion
from app.models.llm import Model, Provider


async def migrate_connection_versions(engine) -> None:
    """Install additive SQLite clocks; untouched legacy connections start at revision zero.

    The trigger observes ORM and raw SQL writes in the same transaction. No
    backfill opens saved keys, and tombstones survive deletion/recreation.
    """
    async with engine.begin() as connection:
        await connection.run_sync(lambda sync: ProviderConnectionVersion.__table__.create(sync, checkfirst=True))
        if engine.dialect.name != 'sqlite':
            return
        statements = {
            'provider_connection_integer': "BEFORE INSERT ON provider_connection_versions WHEN typeof(NEW.revision)!='integer' OR NEW.revision<1 OR NEW.revision>9223372036854775806 BEGIN SELECT RAISE(ABORT,'Provider connection clock requires a bounded integer'); END",
            'provider_connection_no_replace': "BEFORE INSERT ON provider_connection_versions WHEN EXISTS(SELECT 1 FROM provider_connection_versions WHERE provider_id=NEW.provider_id) BEGIN SELECT RAISE(ABORT,'Provider connection clock cannot be replaced'); END",
            'provider_connection_no_delete': 'BEFORE DELETE ON provider_connection_versions BEGIN SELECT RAISE(ABORT,\'Provider connection clock is retained\'); END',
            'provider_connection_monotonic': 'BEFORE UPDATE ON provider_connection_versions WHEN typeof(NEW.revision)!=\'integer\' OR NEW.provider_id IS NOT OLD.provider_id OR NEW.revision != OLD.revision + 1 BEGIN SELECT RAISE(ABORT,\'Provider connection clock must advance\'); END',
        }
        for event in ('INSERT','UPDATE','DELETE'):
            owner = 'OLD' if event == 'DELETE' else 'NEW'
            statements['provider_connection_'+event.lower()] = (
                f'AFTER {event} ON providers BEGIN '
                f'UPDATE provider_connection_versions SET revision=revision+1 WHERE provider_id={owner}.id; '
                f'INSERT INTO provider_connection_versions(provider_id,revision) SELECT {owner}.id,1 '
                f'WHERE NOT EXISTS(SELECT 1 FROM provider_connection_versions WHERE provider_id={owner}.id); END'
            )
        for name,body in statements.items():
            expected=f'CREATE TRIGGER {name} {body}'
            actual=await connection.scalar(text('SELECT sql FROM sqlite_master WHERE type=\'trigger\' AND name=:name'),{'name':name})
            if actual is None:
                await connection.execute(text(expected))
            elif ' '.join(actual.split()) != ' '.join(expected.split()):
                raise RuntimeError('Provider connection clock guard differs from supported schema')


async def connection_version(db, provider_id: str) -> int:
    """Read only a numeric clock, never a key, key digest or provider payload."""
    value=await db.scalar(select(ProviderConnectionVersion.revision).where(ProviderConnectionVersion.provider_id==provider_id))
    if value is not None and (type(value) is not int or not 1<=value<=9223372036854775806):
        raise HTTPException(503,'供应商连接版本无效，未接受调用')
    return value or 0


async def model_connection_snapshot(db, model_id: str) -> tuple[Model,Provider,int]:
    """Load model, provider material and clock in one statement, bypassing cached ORM values.

    Keeping the returned objects alive lets a caller copy the exact captured key
    to its private HTTP call; a later rotation never supplies a different key to
    an already verified binding. Credentials never enter the saved safe binding.
    """
    statement=(select(Model,Provider,func.coalesce(ProviderConnectionVersion.revision,0))
        .join(Provider,Provider.id==Model.provider_id)
        .outerjoin(ProviderConnectionVersion,ProviderConnectionVersion.provider_id==Provider.id)
        .where(Model.id==model_id).execution_options(populate_existing=True))
    row=(await db.execute(statement)).one_or_none()
    if row is None:
        raise HTTPException(503,'模型或供应商连接不存在，请核对原配置')
    if type(row[2]) is not int or not 0<=row[2]<=9223372036854775806:
        raise HTTPException(503,'供应商连接版本无效，未接受调用')
    return row[0],row[1],row[2]
