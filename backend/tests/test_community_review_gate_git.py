"""Actual temporary Git objects prove workflow pins come from committed trees, never private staged/working content or arbitrary paths."""
import hashlib

import pytest

from app.core.contracts.community_review_gates import WORKFLOW_REQUIREMENTS
from app.services.community.candidate_git import CandidateGitRepository
from app.services.community.store import CommunityStoreConflict
from tests.test_community_candidate_git import git


@pytest.fixture
def workflow_repo(tmp_path):
    """Own a small public-like repository with both fixed regular workflow files; it has no remote, credentials, user data or candidate process."""
    root=tmp_path/'public-workflows'; root.mkdir(); git(root,'init','--initial-branch=main')
    for index,path in enumerate(WORKFLOW_REQUIREMENTS):
        target=root/path; target.parent.mkdir(parents=True,exist_ok=True); target.write_text(f'name: authored-{index}\non: push\n','utf8',newline='\n')
    git(root,'add','--','.github'); git(root,'commit','-m','authored trusted workflow base')
    tree=git(root,'rev-parse','HEAD^{tree}').decode().strip()
    return CandidateGitRepository(repository=root),tree


def test_workflow_pins_ignore_private_staged_working_content_and_leave_index_unchanged(workflow_repo):
    """Read exact committed base/candidate blobs even after a maintainer staged and edited different local workflow text."""
    repository,tree=workflow_repo; root=repository.root; expected=repository.workflow_blobs(tree,tree)
    target=root/next(iter(WORKFLOW_REQUIREMENTS)); target.write_text('authored staged private marker\n','utf8')
    git(root,'add','--',target.relative_to(root).as_posix()); target.write_text('authored unstaged private marker\n','utf8')
    before=hashlib.sha256((root/'.git/index').read_bytes()).hexdigest(); head=git(root,'rev-parse','HEAD')
    assert repository.workflow_blobs(tree,tree)==expected
    assert hashlib.sha256((root/'.git/index').read_bytes()).hexdigest()==before and git(root,'rev-parse','HEAD')==head
    assert target.read_text('utf8')=='authored unstaged private marker\n'


def test_changed_candidate_workflow_is_reported_as_different_committed_blob(workflow_repo):
    """A different candidate CI definition cannot be hidden behind a matching filename; the gate receives distinct immutable blob witnesses."""
    repository,base=workflow_repo; path=next(iter(WORKFLOW_REQUIREMENTS)); target=repository.root/path
    target.write_text('name: changed authored rule\non: push\n','utf8'); git(repository.root,'add','--',path)
    git(repository.root,'commit','-m','authored changed workflow'); candidate=git(repository.root,'rev-parse','HEAD^{tree}').decode().strip()
    value=repository.workflow_blobs(base,candidate)
    assert value[path]['base']!=value[path]['candidate']
    assert all(value[name]['base']==value[name]['candidate'] for name in WORKFLOW_REQUIREMENTS if name!=path)


def test_missing_or_noncanonical_tree_never_falls_back_to_current_worktree(workflow_repo):
    """A missing workflow or malformed tree cannot cause the reader to substitute a convenient current local file."""
    repository,base=workflow_repo; path=next(iter(WORKFLOW_REQUIREMENTS)); git(repository.root,'rm','--',path)
    git(repository.root,'commit','-m','authored missing workflow'); missing=git(repository.root,'rev-parse','HEAD^{tree}').decode().strip()
    with pytest.raises(CommunityStoreConflict): repository.workflow_blobs(base,missing)
    with pytest.raises(CommunityStoreConflict): repository.workflow_blobs('HEAD',base)
