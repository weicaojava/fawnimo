"""Apply all supported additive schemas before running the account-free editing software."""
import asyncio
from app.core.db import init_db, close_db


async def main():
    """Upgrade the explicitly configured local database and close every runtime handle."""
    try:
        await init_db()
    finally:
        await close_db()


if __name__ == '__main__':
    asyncio.run(main())
