"""Apply the additive production migration after baseline initialization."""
import asyncio
from app.services.studio.production_schema import migrate_production
from app.core.db import close_db


async def main() -> None:
    """Migrate the explicitly configured test/local database, then release it."""
    await migrate_production()
    await close_db()


if __name__ == '__main__':
    asyncio.run(main())
