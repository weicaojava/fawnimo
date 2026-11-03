"""Explicit local preview-data/backup/restore/feedback tools; no public deployment, code activation or provider calls."""
import argparse
import json
from pathlib import Path
import sqlite3
from observe_community_pr import read_json


def main():
    """Require one dedicated private journal and explicit fresh paths, never auto-discover or overwrite a personal studio."""
    parser=argparse.ArgumentParser(description='Prepare independent local preview data and offline rollback archives')
    parser.add_argument('--release-root',type=Path,required=True)
    parser.add_argument('--preview-root',type=Path)
    parser.add_argument('--source-root',type=Path)
    parser.add_argument('--destination',type=Path)
    actions=parser.add_mutually_exclusive_group(required=True)
    actions.add_argument('--initialize-release-root',action='store_true')
    actions.add_argument('--prepare-preview-file',type=Path)
    actions.add_argument('--snapshot-file',type=Path)
    actions.add_argument('--restore-file',type=Path)
    actions.add_argument('--feedback-file',type=Path)
    actions.add_argument('--verify-snapshot',metavar='SNAPSHOT_ID')
    actions.add_argument('--get-release',metavar='RELEASE_ID')
    args=parser.parse_args()
    from app.core.contracts.local_releases import LocalPreviewInput,LocalSnapshotInput,LocalRestoreInput,LocalReleaseFeedbackInput
    from app.services.community.local_releases import LocalReleaseManager
    try:
        manager=LocalReleaseManager(args.release_root,initialize=args.initialize_release_root)
        if args.initialize_release_root: result={'initialized':True,'services_started':False,'automatic_deployment_enabled':False,'paid_authorization':0}
        elif args.get_release: result=manager.get(args.get_release)
        elif args.verify_snapshot: result=manager.verify(args.verify_snapshot)
        elif args.prepare_preview_file:
            if args.preview_root is None: raise ValueError('A fresh explicit preview data root is required')
            result=manager.prepare_preview(LocalPreviewInput.model_validate(read_json(args.prepare_preview_file,4096)),args.preview_root)
        elif args.snapshot_file:
            if args.source_root is None: raise ValueError('An explicit stopped source gateway root is required')
            result=manager.snapshot(LocalSnapshotInput.model_validate(read_json(args.snapshot_file,4096)),args.source_root)
        elif args.restore_file:
            if args.destination is None: raise ValueError('A fresh explicit restore destination is required')
            result=manager.restore(LocalRestoreInput.model_validate(read_json(args.restore_file,4096)),args.destination)
        else: result=manager.feedback(LocalReleaseFeedbackInput.model_validate(read_json(args.feedback_file,16384)))
        print(json.dumps(result,ensure_ascii=False,indent=2,allow_nan=False))
    except (ValueError,OSError,sqlite3.Error,RuntimeError,RecursionError):
        parser.exit(1,'Local release operation unavailable. Stop the selected gateway/workers, preserve the original journal, and use new independent paths. Unknown or partial copies are not overwritten. Private credentials and data are not printed.\n')


if __name__=='__main__': main()
