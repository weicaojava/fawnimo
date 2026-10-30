"""Run or inspect a dedicated public requirement intake; never send comments, invoke a model or read studio resources."""
import argparse
import json
import os
from pathlib import Path


def main():
    """Require explicit target/setup for activation while defaulting the local listener to a closed intake."""
    parser = argparse.ArgumentParser(description='Fawnimo public GitHub requirement intake')
    parser.add_argument('--root', type=Path, required=True)
    parser.add_argument('--repository-name', default='weicaojava/fawnimo')
    parser.add_argument('--repository-id', type=int)
    parser.add_argument('--proposal-label', default='enhancement')
    parser.add_argument('--enable-intake', action='store_true')
    parser.add_argument('--webhook-secret-file', type=Path)
    parser.add_argument('--host', default='127.0.0.1')
    parser.add_argument('--port', type=int, default=18100)
    parser.add_argument('--list', action='store_true')
    args = parser.parse_args()
    os.environ['JELLYFISH_SKIP_ENV_FILE'] = '1'
    from app.core.contracts.community_requests import CommunityIntakeConfiguration
    config = CommunityIntakeConfiguration(enabled=args.enable_intake, repository_id=args.repository_id,
        repository_name=args.repository_name, proposal_label=args.proposal_label)
    if args.list:
        from app.services.community.store import CommunityRequestStore
        store = CommunityRequestStore(args.root, config, initialize=False)
        print(json.dumps([row.model_dump() for row in store.summaries()], ensure_ascii=False, indent=2))
        return
    if not 1 <= args.port <= 65535:
        parser.error('Port must be between1 and65535')
    secret = None
    if config.enabled:
        if args.webhook_secret_file is None:
            parser.error('Enabled intake requires a separate private webhook-secret-file')
        path = args.webhook_secret_file.absolute()
        if path.is_symlink() or path.resolve() != path or not path.is_file() or not 32 <= path.stat().st_size <= 512:
            parser.error('Webhook secret file is missing, redirected or invalid')
        with path.open('rb') as stream:
            secret = stream.read(513)
    from app.controlplane.community import create_community_app
    import uvicorn
    app = create_community_app(args.root, configuration=config, secret=secret)
    uvicorn.run(app, host=args.host, port=args.port, proxy_headers=False, timeout_graceful_shutdown=5)


if __name__ == '__main__':
    main()
