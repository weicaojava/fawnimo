"""Supported loopback-only local launcher with a bounded HTTP shutdown phase."""
import argparse
import asyncio
import uvicorn


def build_server(port: int = 8000) -> uvicorn.Server:
    """Keep HTTP draining finite so application worker cleanup and DB disposal can run.

    Migrate the configured database before launching. The five-second HTTP drain
    does not replace the application's own durable worker kill/join cleanup.
    """
    if type(port) is not int or not 1 <= port <= 65535:
        raise ValueError('Port must be an integer from 1 through 65535')
    return uvicorn.Server(uvicorn.Config('app.main:app', host='127.0.0.1', port=port, timeout_graceful_shutdown=5, timeout_keep_alive=2, log_level='info'))


def main():
    """Launch the migrated local app without model calls, database paths or public binding arguments."""
    parser = argparse.ArgumentParser(description='Start the migrated Jellyfish local API')
    parser.add_argument('--port', type=int, default=8000)
    args = parser.parse_args()
    asyncio.run(build_server(args.port).serve())


if __name__ == '__main__':
    main()
