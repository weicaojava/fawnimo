"""Publish one exact checked candidate as a draft, durably separating attempted writes from positively observed results."""
from app.core.contracts.community_publication import publication_flags
from app.services.community.review_gate_decisions import validate_local_check,positive,hex_id
from app.services.community.store import CommunityStoreConflict


def remote_scope(remote,policy,candidate):
    """Before each active mutation, require the same public nonarchived repository and unchanged exact original main."""
    repo=remote.repository(); main=remote.ref('main')
    if (not isinstance(repo,dict) or repo.get('id')!=policy.repository_id or repo.get('full_name','').casefold()!=policy.repository_name
            or repo.get('private') is not False or repo.get('visibility')!='public' or repo.get('archived') is not False or repo.get('disabled') is not False
            or not isinstance(main,dict) or main.get('ref')!='refs/heads/main' or main.get('object',{}).get('type')!='commit'
            or main['object'].get('sha')!=candidate.parent):
        raise CommunityStoreConflict('Remote repository/main no longer matches original candidate')


def branch_head(remote,candidate):
    """Distinguish exact absence from a foreign branch; existing refs are never updated or deleted."""
    value=remote.ref(candidate.branch)
    if value is None: return None
    if (not isinstance(value,dict) or value.get('ref')!='refs/heads/'+candidate.branch or value.get('object',{}).get('type')!='commit'
            or value['object'].get('sha')!=candidate.commit):
        raise CommunityStoreConflict('Reserved remote branch belongs to a different commit')
    return candidate.commit


def original_pull(remote,policy,candidate):
    """Bind a discovered PR to exact repository/head/base; closed/modified/duplicate results require maintainer attention and never a new PR."""
    values=remote.pulls(candidate.branch)
    if not isinstance(values,list) or len(values)>=100: raise CommunityStoreConflict('Incomplete original PR set')
    if not values: return None
    if len(values)!=1: raise CommunityStoreConflict('Ambiguous original PR set')
    value=values[0]
    if not valid_pull(value,policy,candidate): raise CommunityStoreConflict('Original PR changed or belongs to another source')
    return value['number']


def valid_pull(value,policy,candidate):
    """Only a real open unmerged draft for the exact original candidate/main is a successful publication receipt, never CI qualification."""
    if not isinstance(value,dict) or not positive(value.get('number')) or value.get('state')!='open' or value.get('draft') is not True or value.get('merged_at') is not None:
        return False
    head=value.get('head',{}); base=value.get('base',{})
    return (head.get('ref')==candidate.branch and head.get('sha')==candidate.commit and head.get('repo',{}).get('id')==policy.repository_id
        and head.get('repo',{}).get('full_name','').casefold()==policy.repository_name and base.get('ref')=='main' and base.get('sha')==candidate.parent
        and base.get('repo',{}).get('id')==policy.repository_id and base.get('repo',{}).get('full_name','').casefold()==policy.repository_name)


async def publish_candidate(store,body,artifact,facts,objects,remote,*,revalidate):
    """The sole reservation winner may perform fixed writes. Every ref/PR mutation is journaled first; any failure returns history without replay permission."""
    old=store.replay(body)
    if old is not None: return old
    validate_local_check(body,artifact,facts,store.policy); candidate=facts.candidate
    if objects.get('candidate')!=candidate: raise CommunityStoreConflict('Publication objects differ from checked candidate')
    record,owned=store.reserve(body,candidate)
    if not owned: return record
    identifier=record['id']; trace={'candidate_commit':candidate.commit,'candidate_tree':candidate.tree,'branch':candidate.branch,'base_commit':candidate.parent}
    try:
        await revalidate(); remote_scope(remote,store.policy,candidate)
        prior=branch_head(remote,candidate); existing=original_pull(remote,store.policy,candidate)
        if existing is not None:
            if prior!=candidate.commit: raise CommunityStoreConflict('Original PR branch is absent')
            return store.append(identifier,'published',{**trace,'pr_number':existing})
        if prior is None:
            for blob in objects['blobs']:
                if remote.create_object('blobs',blob['body']).get('sha')!=blob['sha']: raise CommunityStoreConflict('Remote blob identity differs')
            if remote.create_object('trees',objects['tree']).get('sha')!=candidate.tree: raise CommunityStoreConflict('Remote tree identity differs')
            if remote.create_object('commits',objects['commit']).get('sha')!=candidate.commit: raise CommunityStoreConflict('Remote commit identity differs')
            store.append(identifier,'objects_verified',trace)
            await revalidate(); remote_scope(remote,store.policy,candidate)
            # This endpoint creates only. If another writer wins the race, the
            # intent remains unknown until an explicit read-only reconcile.
            store.append(identifier,'ref_intent',trace)
            remote.create_ref(candidate.branch,candidate.commit)
            if branch_head(remote,candidate)!=candidate.commit: raise CommunityStoreConflict('New ref was not positively observed')
        store.append(identifier,'ref_observed',{**trace,'remote_head':candidate.commit})
        await revalidate(); remote_scope(remote,store.policy,candidate)
        if branch_head(remote,candidate)!=candidate.commit: raise CommunityStoreConflict('Candidate branch changed before PR creation')
        existing=original_pull(remote,store.policy,candidate)
        if existing is not None: return store.append(identifier,'published',{**trace,'pr_number':existing})
        store.append(identifier,'pr_intent',trace)
        created=remote.create_pull(candidate.branch,body)
        if not valid_pull(created,store.policy,candidate): raise CommunityStoreConflict('PR creation was not confirmed for the exact candidate')
        observed=original_pull(remote,store.policy,candidate)
        if observed!=created['number'] or branch_head(remote,candidate)!=candidate.commit: raise CommunityStoreConflict('Created PR/head not positively confirmed')
        return store.append(identifier,'published',{**trace,'pr_number':observed})
    except Exception:
        # Details never include provider prose, credentials or candidate output.
        # Even a known HTTP error is not reused as permission for another POST.
        return store.append(identifier,'attention',{'reason':'remote_or_source_result_requires_read_only_reconcile'})


def reconcile_publication(store,identifier,remote):
    """Observe original branch/PR after interruption; absence remains unresolved and never authorizes another POST or branch update."""
    from app.services.community.candidate_git import CandidateCommit
    record=store.get(identifier); saved=record['publication']['candidate']
    candidate=CandidateCommit(**{name:saved[name] for name in CandidateCommit.__dataclass_fields__})
    try:
        remote_scope(remote,store.policy,candidate)
        head=branch_head(remote,candidate); number=original_pull(remote,store.policy,candidate)
        details={'candidate_commit':candidate.commit,'remote_head':head,'reason':'original_write_not_confirmed'}
        if head==candidate.commit and number is not None: details.update(pr_number=number,reason='original_exact_draft_observed')
        return store.append(identifier,'reconciled',details)
    except Exception:
        return store.append(identifier,'attention',{'reason':'original_remote_scope_or_result_unavailable'})
