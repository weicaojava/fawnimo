"""Deterministically turn original local-model text into an inert existing change input; this performs no source reads or execution."""
from app.core.contracts.community_model_code import CommunityCodeModelRequest,CommunityCodeModelResult
from app.core.contracts.community_changes import CommunityChangeInput
from app.services.community.advice_store import digest
from app.services.community.development_model_child import validate_proposal
from app.services.community.change_bundles import verify_change_bundle


def change_input(intent,result):
    """Keep original work, test paths and before hashes server-owned, with one stable result-bound bundle request key."""
    request=CommunityCodeModelRequest.model_validate(intent['request'])
    checked=CommunityCodeModelResult.model_validate(result)
    if checked.state!='generated':raise ValueError('Original model source has not been generated')
    proposal=validate_proposal(checked.proposal.model_dump(),intent['context'])
    originals={item['path']:item for item in intent['context']['files']}
    replacements={item.path:item.text for item in proposal.files}
    key='local-code-bundle:'+digest({'request':request.model_dump(),'provenance':intent['provenance'],'result':checked.model_dump()})
    return CommunityChangeInput(request_key=key,work_id=request.work_id,title=proposal.title,
        changes=[{'path':path,'action':'write','before_sha256':originals[path]['before_sha256'],'text':replacements[path]} for path in request.paths],
        test_files=request.test_files)


def validate_bundle_binding(intent,result,artifact):
    """Independently bind saved bundle scope to the model's original base, work, source version, features and acceptance before linking or advancing."""
    payload=verify_change_bundle(artifact);body=change_input(intent,result)
    scope=payload['scope'];original=intent['context']['scope'];provenance=intent['provenance']
    if (payload['input']!=body.model_dump() or scope['work_id']!=body.work_id
            or scope['base_commit']!=provenance.get('base_commit') or scope['base_tree']!=provenance.get('base_tree')
            or scope['version_id']!=original.get('version_id') or scope['feature_ids']!=sorted(original.get('feature_ids',[]))
            or scope['acceptance']!=original.get('acceptance')
            or scope['source_fingerprint']!=provenance.get('issue',{}).get('fingerprint')):
        raise ValueError('Saved source proposal differs from the original model/preparation scope')
    return payload
