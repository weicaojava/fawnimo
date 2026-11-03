"""Actual source/preparation/check/publication databases and separate CLI processes, using only explicitly authored remote observations."""
import asyncio
import json
import os
from pathlib import Path
import subprocess
import sys
import pytest
from app.core.contracts.community_publication import CommunityPublicationInput,CommunityPublicationPolicy
from app.services.community.publication_admission import publish_current_candidate
from app.services.community.publication_store import CommunityPublicationStore
from app.services.community.store import CommunityStoreConflict
from tests.test_community_review_gate_admission import current_gate
from tests.test_community_review_gate_decisions import gate
from tests.test_community_change_bundles import prepared_change
from tests.test_community_queue_leases import factory
from tests.test_community_publication import AuthoredRemote


@pytest.fixture
def admitted(current_gate,tmp_path,monkeypatch):
    """Reuse real immutable SQLite provenance and fabricated public issue/code fixtures, never real kernel/CI/reviewer qualification."""
    preparation,checks,gates,seed,repo,mode,calls=current_gate
    from app.services.community import review_gate_admission as seeded
    from app.services.community import publication_admission as admission
    monkeypatch.setattr(admission,'verify_public_issue',seeded.verify_public_issue)
    monkeypatch.setattr(admission,'verify_public_code',seeded.verify_public_code)
    policy=CommunityPublicationPolicy(repository_id=gates.policy.repository_id,repository_name=gates.policy.repository_name,enabled=True,
        trusted_workflows=gates.policy.trusted_workflows,trusted_check_image_ids=gates.policy.trusted_check_image_ids,trusted_runner_sha256=gates.policy.trusted_runner_sha256)
    store=CommunityPublicationStore(tmp_path/'publication-root',policy,initialize=True,isolated_from=(preparation.intake.root,checks.changes.root,gates.root))
    body=CommunityPublicationInput(**seed.model_dump(exclude={'pr_number'})); candidate=repo.recover(checks.changes.get(body.bundle_id))
    remote=AuthoredRemote(policy,candidate); creations=[]
    def projection(_repository,_artifact):
        """Native Git serialization has its own real repository test; this fixture isolates current source admission around writes."""
        return {'candidate':candidate,'blobs':[],'tree':{'base_tree':candidate.parent,'tree':[]},'commit':{'tree':candidate.tree}}
    def remote_factory(_policy):
        """Prove credential/transport creation happens only after current admission, outside any source writer."""
        with preparation.intake.connect() as db:
            db.execute('BEGIN IMMEDIATE'); db.rollback()
        creations.append(1); return remote
    monkeypatch.setattr(admission,'publication_objects',projection)
    return preparation,checks,store,body,repo,mode,calls,remote,remote_factory,creations


def test_real_source_admission_then_recovery_without_source_git_or_credentials(admitted):
    """Four current source admissions surround object/ref/PR writes; original receipt recovery never discovers a credential or re-runs sources."""
    prep,checks,store,body,repo,_mode,calls,remote,factory,created=admitted
    saved=asyncio.run(publish_current_candidate(prep,checks,store,body,remote_factory=factory,repository=repo))
    assert saved['phase']=='published' and remote.calls==['trees','commits','ref','pr'] and len(created)==1
    assert calls.count('issue')==calls.count('code')==4
    before=list(calls); writes=list(remote.calls)
    recovered=asyncio.run(publish_current_candidate(None,None,store,body,remote_factory=None))
    assert recovered==saved and before==calls and writes==remote.calls and len(created)==1


@pytest.mark.parametrize('bad',['selection','expired','main','catalog','withdrawn'])
def test_current_source_counterexamples_block_before_credential_loading_or_reservation(admitted,bad):
    """Changed public/main/catalog/selection or stale evidence cannot get as far as trusted remote setup."""
    prep,checks,store,body,repo,mode,_calls,remote,factory,created=admitted; mode['value']=bad
    with pytest.raises(CommunityStoreConflict): asyncio.run(publish_current_candidate(prep,checks,store,body,remote_factory=factory,repository=repo))
    assert store.replay(body) is None and not remote.calls and not created


def test_default_disabled_blocks_source_repository_and_credential_access(tmp_path):
    """An unconfigured publication root cannot even discover an installed Git repository or token file."""
    store=CommunityPublicationStore(tmp_path/'closed',CommunityPublicationPolicy(repository_id=17,repository_name='authored/fawnimo'),initialize=True)
    body=CommunityPublicationInput(request_key='closed-original-key',bundle_id='a'*64,check_attempt_id='11111111-1111-4111-8111-111111111111')
    def forbidden(_policy):
        """Fail if disabled publication reaches the credential provider."""
        pytest.fail('Disabled publication accessed credentials')
    with pytest.raises(CommunityStoreConflict): asyncio.run(publish_current_candidate(None,None,store,body,remote_factory=forbidden))
    assert store.replay(body) is None


def test_actual_cli_default_initialization_closed_publish_and_original_history(tmp_path,admitted):
    """Separate Python processes use default-closed policy and recover an actual previously stored history without source roots or credentials."""
    backend=Path(__file__).resolve().parents[1]; env={key:value for key,value in os.environ.items() if not key.startswith(('JELLYFISH_','DATABASE_','FAWNIMO_'))}
    env.update(PYTHONUTF8='1',PYTHONDONTWRITEBYTECODE='1',JELLYFISH_SKIP_ENV_FILE='1')
    root=tmp_path/'cli-publication'
    def command(*args):
        """Run only the fixed real entry point with task-owned paths; error output must not echo credentials or provider prose."""
        return subprocess.run([sys.executable,str(backend/'publish_community.py'),'--publication-root',str(root),*map(str,args)],cwd=backend,env=env,capture_output=True,text=True,timeout=25)
    opened=command('--initialize-publication','--repository-id',17,'--repository-name','authored/fawnimo','--root',tmp_path/'intake','--changes-root',tmp_path/'changes')
    assert opened.returncode==0 and json.loads(opened.stdout)['policy']['enabled'] is False
    request=tmp_path/'input.json'; request.write_text(json.dumps({'request_key':'closed-cli-request','bundle_id':'a'*64,'check_attempt_id':'11111111-1111-4111-8111-111111111111'}))
    rejected=command('--publish-file',request,'--credential-file',tmp_path/'nonexistent-private-credential')
    assert rejected.returncode==1 and not (tmp_path/'intake').exists() and not (tmp_path/'changes').exists()
    prep,checks,store,body,repo,_mode,_calls,_remote,factory,_created=admitted
    saved=asyncio.run(publish_current_candidate(prep,checks,store,body,remote_factory=factory,repository=repo))
    root=store.root; result=command('--get-publication',saved['id'])
    assert result.returncode==0 and json.loads(result.stdout)==saved
    request.write_text(body.model_dump_json()); result=command('--publish-file',request)
    assert result.returncode==0 and json.loads(result.stdout)==saved
