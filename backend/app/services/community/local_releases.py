"""Private local release-data journal, independent preview accounts, offline snapshots, fresh-path rollback preparation and feedback."""
import hashlib
import os
from pathlib import Path
import re
import secrets
import time
from app.controlplane.maintenance import ProcessLease
from app.controlplane.migration import flush_directory
from app.services.community.evaluations import digest
from app.services.community.local_release_files import (
    PROFILE,MAX_SECONDS,LocalReleaseError,plain,separate,read_json,write_json,copy_snapshot,verify_snapshot,restore_snapshot,file_hash,flush_copy,
)

MARKER={'profile':PROFILE,'version':1,'max_operations':128,'max_previews':16,'max_snapshots':16,'max_restores':16,'max_feedback':64,
    'automatic_deployment_enabled':False,'external_model_calls':0,'paid_authorization':0}


def flags():
    """Prepared data/feedback is not independently accepted code, a running preview, an image verification or public deployment."""
    return {'services_started':False,'runtime_revision_verified':False,'ci_qualified':False,'independent_review_completed':False,
        'real_user_acceptance_verified':False,'deployment_authorized':False,'public_deployment_verified':False,'existing_data_overwritten':False,
        'external_model_calls':0,'paid_authorization':0}


class LocalReleaseManager:
    """Keep all private release-data artifacts in one new operator-owned root, completely separate from product/source data and Git."""
    def __init__(self,root,*,initialize=False):
        """Explicit initialization accepts only an empty canonical directory; reopening never silently resets state or starts services."""
        self.root=plain(root,exists=False)
        if initialize:
            if self.root.exists() and any(self.root.iterdir()): raise LocalReleaseError('Local release initialization requires a new empty directory')
            self.root.mkdir(parents=True,exist_ok=True)
            flush_directory(self.root.parent)
            if os.name!='nt': self.root.chmod(0o700)
            write_json(self.root/'local-release.json',MARKER)
            for name in ('operations','previews','archives'): (self.root/name).mkdir()
        if not self.root.is_dir() or read_json(self.root/'local-release.json')!=MARKER: raise LocalReleaseError('Unknown local release profile')
        if any(item.name not in {'local-release.json','operations','previews','archives','.operation.lock'} or plain(item)!=item for item in self.root.iterdir()):
            raise LocalReleaseError('Mixed or redirected local release root')
        for name in ('operations','previews','archives'):
            if not plain(self.root/name).is_dir(): raise LocalReleaseError('Local release directories are missing')

    def _identifier(self,value):
        """Only opaque SHA identifiers may address private journal/archive paths."""
        if not isinstance(value,str) or re.fullmatch(r'[0-9a-f]{64}',value) is None: raise LocalReleaseError('Invalid local release identifier')
        return value

    def _intent(self,identifier):
        """Validate the original journal envelope without requiring current source/destination availability or following stored paths."""
        value=read_json(self.root/'operations'/self._identifier(identifier)/'intent.json')
        if (set(value)!={'id','kind','input','context','intent_sha256'} or value['id']!=identifier
                or hashlib.sha256(value['input']['request_key'].encode()).hexdigest()!=identifier
                or digest({key:item for key,item in value.items() if key!='intent_sha256'})!=value['intent_sha256']):
            raise LocalReleaseError('Original local release intent changed')
        return value

    def get(self,identifier):
        """Recover original completion or interruption as history only; it never resumes a copy, resets a preview or overwrites a target."""
        intent=self._intent(identifier); directory=self.root/'operations'/identifier
        result=read_json(directory/'result.json') if (directory/'result.json').exists() else None
        if result is not None and any(result.get(key)!=value or type(result.get(key)) is not type(value) for key,value in flags().items()):
            raise LocalReleaseError('Local result claims unavailable authority')
        phase='completed' if result is not None else 'attention' if (directory/'attention.json').exists() else 'interrupted'
        return {'id':identifier,'kind':intent['kind'],'phase':phase,'result':result,'historical_only':True,'new_action_authorized':False,**flags()}

    def _operate(self,kind,body,context,action):
        """Journal and fsync once before work; concurrent/crashed operations retain artifacts and never automatically repeat a partial copy."""
        identifier=hashlib.sha256(body.request_key.encode()).hexdigest(); envelope={'id':identifier,'kind':kind,'input':body.model_dump(),'context':context}
        envelope['intent_sha256']=digest(envelope); directory=self.root/'operations'/identifier
        with ProcessLease(self.root/'.operation.lock'):
            if directory.exists():
                if self._intent(identifier)!=envelope: raise LocalReleaseError('Original local release key belongs to another input or destination')
                return self.get(identifier)
            entries=list((self.root/'operations').iterdir())
            if any(not re.fullmatch(r'[0-9a-f]{64}',item.name) or not plain(item).is_dir() for item in entries): raise LocalReleaseError('Unknown local release journal directory')
            limit={'preview':16,'snapshot':16,'restore':16,'feedback':64}[kind]
            if len(entries)>=128 or sum(self._intent(item.name)['kind']==kind for item in entries)>=limit:
                raise LocalReleaseError('Local release capacity reached')
            directory.mkdir(); flush_directory(directory.parent); write_json(directory/'intent.json',envelope)
            try:
                result=action(identifier,time.monotonic()+MAX_SECONDS)
                write_json(directory/'result.json',{**result,**flags()})
            except Exception:
                write_json(directory/'attention.json',{'reason':'original_local_operation_requires_maintainer_attention',**flags()})
                raise LocalReleaseError('Local operation incomplete; preserve original journal and inspect private artifacts') from None
            return self.get(identifier)

    def prepare_preview(self,body,destination):
        """Provision new A/B accounts in an empty data root, with private generated credentials and no copied stable users/media/connections."""
        data=plain(destination,exists=False); separate(self.root,data)
        def create(identifier,deadline):
            """No model or gateway worker is started; later runtime execution and exact image qualification remain separate steps."""
            from app.controlplane.store import IdentityStore
            directory=self.root/'previews'/identifier; directory.mkdir()
            flush_directory(directory.parent)
            if data.exists() or not data.parent.is_dir(): raise LocalReleaseError('Preview data requires a fresh independent target')
            data.mkdir(mode=0o700)
            identity=IdentityStore(data); credentials={}
            for label in ('a','b'):
                password=secrets.token_urlsafe(32); account=identity.provision('preview.'+label+'.'+identifier[:16],password)
                credentials[label]={**account,'password':password}
            flush_copy(data,deadline)
            write_json(directory/'credentials.json',credentials)
            write_json(directory/'preview.json',{'profile':PROFILE,'bundle_id':body.bundle_id,'declared_source_commit':body.source_commit,
                'account_count':2,'data_root':str(data),'source_credentials_copied':False,**flags()})
            return {'preview_root':str(data),'credential_file':str(directory/'credentials.json'),'bundle_id':body.bundle_id,
                'declared_source_commit':body.source_commit,'account_count':2,'source_credentials_copied':False,'models_configured':False}
        return self._operate('preview',body,{'destination':str(data)},create)

    def snapshot(self,body,source_root):
        """Create one private whole-gateway offline archive while all supported gateway and workspace processes are excluded by kernel leases."""
        source=plain(source_root,exists=False); separate(self.root,source)
        def copy(identifier,deadline):
            """Only the original winning request opens source data; unknown/old requests return history without touching it."""
            directory=self.root/'archives'/identifier; directory.mkdir(); flush_directory(directory.parent); payload=directory/'payload'
            manifest=copy_snapshot(source,payload,deadline); manifest['declared_source_commit']=body.source_commit
            write_json(directory/'manifest.json',manifest)
            return {'snapshot_id':identifier,'manifest_sha256':file_hash(directory/'manifest.json',deadline)['sha256'],
                'file_count':len(manifest['files']),'database_count':len(manifest['databases']),'workspace_count':manifest['workspace_count'],'private_archive':True,
                'declared_source_commit':body.source_commit}
        return self._operate('snapshot',body,{'source_root':str(source)},copy)

    def _archive(self,identifier,deadline):
        """Verify exact sealed archive identity and all content before verification/restore; no current source root is needed."""
        saved=self.get(identifier)
        if saved['kind']!='snapshot' or saved['phase']!='completed': raise LocalReleaseError('A completed original snapshot is required')
        directory=self.root/'archives'/identifier
        if file_hash(directory/'manifest.json',deadline)['sha256']!=saved['result']['manifest_sha256']: raise LocalReleaseError('Snapshot manifest changed')
        manifest=read_json(directory/'manifest.json'); verify_snapshot(directory/'payload',manifest,deadline)
        return directory/'payload',manifest

    def verify(self,identifier):
        """Check private archive bytes/SQLite without starting services or granting release authority."""
        with ProcessLease(self.root/'.operation.lock'):
            _payload,manifest=self._archive(self._identifier(identifier),time.monotonic()+MAX_SECONDS)
            return {'snapshot_id':identifier,'verified':True,'file_count':len(manifest['files']),'database_count':len(manifest['databases']),**flags()}

    def restore(self,body,destination):
        """Prepare rollback data at a brand-new disjoint path and keep the original plus all later edits unchanged."""
        target=plain(destination,exists=False); separate(self.root,target)
        def copy(identifier,deadline):
            """Source/release/current destinations are never replaced; partial new targets remain for explicit maintainer attention."""
            payload,manifest=self._archive(body.snapshot_id,deadline); origin=Path(self._intent(body.snapshot_id)['context']['source_root'])
            separate(origin,target)
            result=restore_snapshot(payload,manifest,target,deadline)
            return {**result,'restored_root':str(target),'snapshot_id':body.snapshot_id,'declared_source_commit':manifest['declared_source_commit']}
        return self._operate('restore',body,{'destination':str(target)},copy)

    def feedback(self,body):
        """Attach private local feedback as a separate journal event, never a GitHub message, authenticated review or real-user acceptance claim."""
        def record(identifier,deadline):
            """Require an existing original local release artifact without changing its completion or deployment status."""
            original=self.get(body.release_id)
            if original['kind'] not in {'preview','restore'} or original['phase']!='completed': raise LocalReleaseError('Feedback needs a completed local preview or restore')
            return {'release_id':body.release_id,'decision':body.decision,'local_feedback_recorded':True,'feedback_identity_verified':False,'github_message_sent':False}
        return self._operate('feedback',body,{},record)
