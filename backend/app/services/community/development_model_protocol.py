"""Deterministically turn original local-model text into an inert existing change input; this performs no source reads or execution."""
from app.core.contracts.community_model_code import CommunityCodeModelRequest,CommunityCodeModelResult
from app.core.contracts.community_changes import CommunityChangeInput
from app.services.community.advice_store import digest
from app.services.community.development_model_child import validate_proposal


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
