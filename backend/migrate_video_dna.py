"""Apply all additive source-bound DNA schemas without creating provider work."""
import asyncio
from app.core.db import init_db, close_db


async def main():
    """Upgrade the configured local database and release every handle before returning."""
    try:
        await init_db()
    finally:
        await close_db()


if __name__ == '__main__':
    asyncio.run(main())
