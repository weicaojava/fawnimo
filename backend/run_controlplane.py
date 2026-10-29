"""Start the login-required self-hosted gateway, or securely provision an offline account."""
import argparse
import os
from pathlib import Path
import secrets


def main():
    """Keep deployment/provisioning explicit and never import shared studio credentials."""
    parser = argparse.ArgumentParser(description='Run an isolated-workspace studio gateway')
    parser.add_argument('--root', type=Path, required=True)
    parser.add_argument('--host', default='127.0.0.1')
    parser.add_argument('--port', type=int, default=18000)
    parser.add_argument('--frontend', type=Path)
    parser.add_argument('--probe', type=Path)
    parser.add_argument('--public-origin')
    parser.add_argument('--allow-registration', action='store_true')
    parser.add_argument('--capacity', type=int, default=4)
    parser.add_argument('--insecure-local-cookie', action='store_true')
    parser.add_argument('--provision-account')
    parser.add_argument('--credential-file', type=Path)
    args = parser.parse_args()
    os.environ['JELLYFISH_SKIP_ENV_FILE'] = '1'
    if args.insecure_local_cookie and args.host not in {'127.0.0.1', 'localhost', '::1'}:
        parser.error('Insecure cookies are available only on a loopback listener')
    if not 1 <= args.port <= 65535:
        parser.error('Port must be between 1 and 65535')
    if args.provision_account:
        if not args.credential_file or args.credential_file.exists():
            parser.error('Provisioning requires a new private --credential-file')
        from app.controlplane.store import IdentityStore
        import json
        password = secrets.token_urlsafe(24)
        account = IdentityStore(args.root).provision(args.provision_account, password)
        args.credential_file.parent.mkdir(parents=True, exist_ok=True)
        with args.credential_file.open('x', encoding='utf-8') as stream:
            json.dump({**account, 'password': password}, stream, indent=2)
        if os.name != 'nt':
            args.credential_file.chmod(0o600)
        print('Account created; credentials saved to the specified private file.')
        return
    from app.controlplane.gateway import create_gateway
    import uvicorn
    app = create_gateway(args.root, frontend=args.frontend, probe=args.probe,
                         secure_cookies=not args.insecure_local_cookie, public_origin=args.public_origin,
                         allow_registration=args.allow_registration, capacity=args.capacity)
    uvicorn.run(app, host=args.host, port=args.port, proxy_headers=False, timeout_graceful_shutdown=10)


if __name__ == '__main__':
    main()
