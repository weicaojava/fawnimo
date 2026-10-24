"""Apply the additive execution schema to an explicitly configured local database."""
import asyncio
from app.services.worker.execution_schema import migrate_execution

if __name__=='__main__':
    asyncio.run(migrate_execution())
