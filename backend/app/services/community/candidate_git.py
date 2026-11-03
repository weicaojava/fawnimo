"""Construct/recover public candidate Git objects through an independent index, without checking out or executing proposed source."""
from dataclasses import dataclass
from contextvars import ContextVar
from functools import wraps
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import tempfile
import time
import uuid

from app.services.community.change_bundles import build_change_bundle, verify_change_bundle, verify_public_snapshot
from app.services.community.public_code import CODE_PROTOCOL, PublicCodeObservation
from app.services.community.candidate_lock import candidate_object_lock
from app.services.community.store import CommunityStoreConflict

GIT_DEADLINE = ContextVar('community_candidate_git_deadline', default=None)


def bounded_git_operation(method):
    """Bound the entire nested create/recovery operation to20 seconds, independently of the15-second individual builtin cap."""
    @wraps(method)
    def run(*args, **kwargs):
        """Share a deadline across nested calls while keeping concurrent controller threads independent."""
        token = GIT_DEADLINE.set(GIT_DEADLINE.get() or time.monotonic() + 20)
        try:
            with candidate_object_lock(args[0].objects, GIT_DEADLINE.get()):
                return method(*args, **kwargs)
        finally:
            GIT_DEADLINE.reset(token)
    return run


@dataclass(frozen=True)
class CandidateCommit:
    """A local public-source proposal reference, never a PR, independent approval, execution capability or deployment result."""
    bundle_id: str
    branch: str
    commit: str
    tree: str
    parent: str
    repository_id: int
    repository_name: str

    def receipt(self):
        """Expose immutable public trace IDs and explicitly deny every unperformed qualification."""
        return {**self.__dict__, 'scope': 'local_candidate_commit_only', 'code_executed': False, 'github_push_performed': False,
            'pr_created': False, 'independent_review_completed': False, 'ci_qualified': False, 'deployment_authorized': False,
            'external_model_calls': 0, 'paid_authorization': 0}


class CandidateGitRepository:
    """Use only committed public objects in the installed or explicitly trusted operator-selected clone; change JSON never selects a path."""

    def __init__(self, *, repository=None):
        """The default is the installed public clone; an explicit clone is a trusted maintainer/embedding choice, never candidate or API configuration."""
        self.root = (Path(repository) if repository is not None else Path(__file__).resolve().parents[4]).absolute()
        if self.root.resolve() != self.root:
            raise CommunityStoreConflict('Candidate repository is redirected')
        if self._git('rev-parse', '--show-object-format').stdout.strip() != b'sha1':
            raise CommunityStoreConflict('Candidate repository uses an unsupported object format')
        common = Path(self._git('rev-parse', '--git-common-dir').stdout.decode().strip())
        self.objects = ((self.root / common).resolve() / 'objects').absolute()
        if self.objects.resolve() != self.objects or not self.objects.is_dir():
            raise CommunityStoreConflict('Candidate public object storage is redirected or absent')

    def _git(self, *arguments, data=None, index=None, allowed_failure=False):
        """Run fixed Git builtins only, without a shell, inherited credentials, filters, hooks, signing or a shared index."""
        allowed = {'SYSTEMROOT', 'WINDIR', 'COMSPEC', 'PATH', 'PATHEXT', 'TEMP', 'TMP', 'TMPDIR', 'LANG', 'LC_ALL', 'TZ'}
        env = {name: value for name, value in os.environ.items() if name.upper() in allowed}
        env.update(GIT_CONFIG_NOSYSTEM='1', GIT_CONFIG_GLOBAL=os.devnull, GIT_TERMINAL_PROMPT='0', GCM_INTERACTIVE='Never',
            GIT_AUTHOR_NAME='Fawnimo local proposal', GIT_AUTHOR_EMAIL='community@fawnimo.invalid',
            GIT_COMMITTER_NAME='Fawnimo local proposal', GIT_COMMITTER_EMAIL='community@fawnimo.invalid')
        if index is not None: env['GIT_INDEX_FILE'] = str(index)
        remaining = (GIT_DEADLINE.get() or time.monotonic() + 15) - time.monotonic()
        if remaining <= 0:
            raise CommunityStoreConflict('Local candidate object deadline reached; recover the original ref before retrying')
        try:
            result = subprocess.run(['git', '--literal-pathspecs', '-c', 'core.hooksPath=' + os.devnull, '-c', 'core.fsmonitor=false',
                '-c', 'commit.gpgSign=false', '-c', 'credential.helper=', '-C', str(self.root), *arguments],
                input=data, capture_output=True, env=env, timeout=min(15, remaining))
        except (subprocess.TimeoutExpired, OSError):
            raise CommunityStoreConflict('Local candidate object operation was interrupted; recover the original ref before retrying') from None
        if result.returncode and not allowed_failure:
            raise CommunityStoreConflict('Local candidate object operation failed; original checkout is retained')
        return result

    def _message(self, artifact):
        """Create fixed proposal provenance; issue text cannot add command flags, commit headers or reviewer identities."""
        payload = verify_change_bundle(artifact)
        return ('community: ' + payload['title'] + '\n\nFawnimo-Bundle: ' + artifact['bundle_id']
            + '\nFawnimo-Scope: ' + payload['scope_hash'] + '\nFawnimo-Work: ' + json.dumps(payload['scope']['work_id'])
            + '\nFawnimo-Features: ' + json.dumps(payload['scope']['feature_ids'])
            + '\n\nLocal inert source proposal; no independent approval, execution, PR or deployment.\n').encode('utf8')

    def _tree(self, artifact):
        """Create a candidate tree through a fresh index; neither staged nor unstaged working files are read or changed."""
        payload = verify_change_bundle(artifact); scope = payload['scope']; base = scope['base_commit']
        if re.fullmatch(r'[0-9a-f]{40}', base) is None or re.fullmatch(r'[0-9a-f]{40}', scope['base_tree']) is None:
            raise CommunityStoreConflict('Invalid exact public base object identity')
        if self._git('rev-parse', base + '^{tree}').stdout.decode().strip() != scope['base_tree']:
            raise CommunityStoreConflict('Local public base tree differs from original source')
        temporary = Path(tempfile.gettempdir()).resolve()
        index = temporary / ('fawnimo-community-' + uuid.uuid4().hex + '.index')
        if index.resolve().parent != temporary or index.exists():
            raise CommunityStoreConflict('Independent candidate index is not a fresh owned file')
        try:
            self._git('read-tree', base, index=index)
            for item in payload['files']:
                path = item['path']
                if item['before_sha256'] is not None:
                    entry = self._git('ls-tree', base, '--', path).stdout
                    if not entry.startswith(b'100644 blob ') or hashlib.sha256(self._git('show', base + ':' + path).stdout).hexdigest() != item['before_sha256']:
                        raise CommunityStoreConflict('Original committed public file bytes or mode changed')
                elif self._git('ls-tree', base, '--', path).stdout:
                    raise CommunityStoreConflict('New public file already exists in the original tree')
                if item['action'] == 'delete':
                    self._git('update-index', '--force-remove', '--', path, index=index)
                else:
                    blob = self._git('hash-object', '-w', '--stdin', data=item['text'].encode('utf8')).stdout.decode().strip()
                    self._git('update-index', '--add', '--cacheinfo', '100644', blob, path, index=index)
            tree = self._git('write-tree', index=index).stdout.decode().strip()
            changed = self._git('diff', '--no-ext-diff', '--name-only', '-z', base, tree).stdout.decode().rstrip('\0').split('\0')
            if set(changed) != {item['path'] for item in payload['files']}:
                raise CommunityStoreConflict('Candidate tree contains an unexpected source change')
            return tree
        finally:
            # Only two known task-owned files are unlinked. No recursive delete,
            # shared index cleanup, user checkout reset or Git garbage collection.
            for suffix in ('', '.lock'):
                owned = Path(str(index) + suffix)
                if owned.resolve().parent != temporary:
                    raise CommunityStoreConflict('Independent candidate index cleanup requires attention')
                owned.unlink(missing_ok=True)

    @bounded_git_operation
    def recover(self, artifact):
        """Read the exact already-created local proposal before new-source gates, preserving the original commit timestamp/identity."""
        payload = verify_change_bundle(artifact); branch = artifact['branch']; ref = 'refs/heads/' + branch
        existing = self._git('rev-parse', '--verify', ref, allowed_failure=True)
        if existing.returncode:
            return None
        commit = existing.stdout.decode().strip(); tree = self._tree(artifact)
        raw = self._git('cat-file', 'commit', commit).stdout
        header, message = raw.split(b'\n\n', 1)
        parents = [line.split(b' ', 1)[1].decode() for line in header.splitlines() if line.startswith(b'parent ')]
        trees = [line.split(b' ', 1)[1].decode() for line in header.splitlines() if line.startswith(b'tree ')]
        if parents != [payload['scope']['base_commit']] or trees != [tree] or message != self._message(artifact):
            raise CommunityStoreConflict('Reserved local candidate branch belongs to another change; it was not overwritten')
        return CandidateCommit(artifact['bundle_id'], branch, commit, tree, parents[0], payload['scope']['repository_id'], payload['scope']['repository_name'])

    @bounded_git_operation
    def create(self, artifact, prepared, observation):
        """Create one local candidate from a fresh exact public snapshot; original recovery never retargets or creates another commit."""
        original = self.recover(artifact)
        if original is not None:
            return original
        payload = verify_change_bundle(artifact); scope = payload['scope']; now = time.time_ns() // 1000
        if (not isinstance(observation, PublicCodeObservation) or observation.protocol != CODE_PROTOCOL
                or type(observation.checked_at_us) is not int or not 0 <= now - observation.checked_at_us <= 60000000
                or observation.snapshot.repository_id != scope['repository_id'] or observation.snapshot.repository_name.casefold() != scope['repository_name']
                or observation.snapshot.commit != scope['base_commit'] or observation.snapshot.tree != scope['base_tree']
                or verify_public_snapshot(observation.snapshot) != payload['base_code_manifest']):
            raise CommunityStoreConflict('New local candidate requires its exact fresh original public code')
        from app.core.contracts.community_changes import CommunityChangeInput
        registry = json.loads(observation.snapshot.files['capabilities/features.json'].decode('utf8'))
        rebuilt = build_change_bundle(CommunityChangeInput.model_validate(payload['input']), prepared, observation.snapshot, registry,
            observations=payload['source_observations'])
        if rebuilt != artifact:
            raise CommunityStoreConflict('Original candidate no longer matches its prepared scope and registered edit profile')
        tree = self._tree(artifact)
        commit = self._git('commit-tree', tree, '-p', scope['base_commit'], data=self._message(artifact)).stdout.decode().strip()
        # Compare-and-swap only the hash-derived reserved branch. A competing
        # controller's valid original result is recovered; other branches never
        # get reset, checked out or forcibly overwritten.
        self._git('update-ref', 'refs/heads/' + artifact['branch'], commit, '0' * 40, allowed_failure=True)
        result = self.recover(artifact)
        if result is None:
            raise CommunityStoreConflict('Local candidate reference was not committed; no remote push was attempted')
        return result

    @bounded_git_operation
    def code_manifest(self, tree):
        """Read the complete fixed code/resource subset from an exact committed tree, so an old image cannot silently test a different base."""
        from app.core.contracts.community_code_checks import checked_source_path
        if not isinstance(tree, str) or re.fullmatch(r'[0-9a-f]{40}', tree) is None:
            raise CommunityStoreConflict('Exact public/candidate tree ID required')
        result = {}
        for entry in self._git('ls-tree', '-r', '-z', tree).stdout.split(b'\0'):
            if not entry: continue
            header, name = entry.split(b'\t', 1); mode, kind, blob = header.decode().split(); path = name.decode('utf8')
            if not checked_source_path(path): continue
            if mode != '100644' or kind != 'blob' or re.fullmatch(r'[0-9a-f]{40}', blob) is None:
                raise CommunityStoreConflict('Pinned code tree contains an unsupported source file type or mode')
            result[path] = {'mode': mode, 'sha': blob}
        if not 1 <= len(result) <= 4096:
            raise CommunityStoreConflict('Pinned code/resource manifest is empty or exceeds profile capacity')
        return dict(sorted(result.items()))

    @bounded_git_operation
    def workflow_blobs(self, base_tree, candidate_tree):
        """Read only the two fixed committed workflow paths, never a dirty worktree/index or a caller-selected CI rule."""
        from app.core.contracts.community_review_gates import WORKFLOW_REQUIREMENTS
        if any(not isinstance(tree,str) or re.fullmatch(r'[0-9a-f]{40}',tree) is None for tree in (base_tree,candidate_tree)):
            raise CommunityStoreConflict('Exact base/candidate tree IDs required for CI policy')
        result={path:{} for path in WORKFLOW_REQUIREMENTS}
        for key,tree in (('base',base_tree),('candidate',candidate_tree)):
            for path in WORKFLOW_REQUIREMENTS:
                entries=[item for item in self._git('ls-tree','-z',tree,'--',path).stdout.split(b'\0') if item]
                if len(entries)!=1: raise CommunityStoreConflict('Both pinned workflow files must exist in the exact committed tree')
                header,name=entries[0].split(b'\t',1); mode,kind,blob=header.decode().split()
                if name.decode('utf8')!=path or mode!='100644' or kind!='blob' or re.fullmatch(r'[0-9a-f]{40}',blob) is None:
                    raise CommunityStoreConflict('Trusted workflow is absent, redirected or has an unsupported mode')
                result[path][key]=blob
        return result
