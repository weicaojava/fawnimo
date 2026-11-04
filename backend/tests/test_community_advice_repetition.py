"""Retained repeated-response counterexamples and actual owned HTTP/history checks; no AI inference or quality claim."""
from copy import deepcopy

import pytest

from app.services.community import advice_child
from app.services.community.advice import create_advice,original_context,response_receipt
from tests import test_community_advice as original
from tests.test_community_advice import setup_advice
from tests.test_community_evaluations import NOW,proof


def repeated_advice():
    """Reproduce the repeated question seen in the retained local-model observation using public, authored test content."""
    return {**deepcopy(original.ADVICE),'suggested_decision':'needs_clarification','acceptance':[],
        'questions':['What is the expected format of the CSV file?','What are the required fields in the CSV file?']+
            ['What is the expected output format of the CSV file?']*6}


@pytest.mark.asyncio
async def test_owned_http_repetition_is_uncertain_and_original_recovery_never_reposts(setup_advice,monkeypatch):
    """A structurally valid repeated response cannot become new ready advice or consume a second local call on replay."""
    intake,journal,request,_source,observe,config_path=setup_advice
    monkeypatch.setattr(original,'ADVICE',repeated_advice())
    with original.authored_server() as (port,state):
        config_path.write_text(original.model_configuration(port).model_dump_json(),'utf8')
        failed=await create_advice(intake,journal,request,config_path,source_verifier=observe,clock_us=lambda:NOW)
        assert failed['state']=='uncertain' and failed['receipt']['submitted'] is True
        assert failed['receipt'].get('advice') is None and state.posts==1 and state.tags==1
        assert not failed['execution_authorized'] and not failed['model_quality_qualified']
        config_path.unlink()
        assert await create_advice(intake,journal,request,config_path,source_verifier=observe,clock_us=lambda:NOW)==failed
        assert state.posts==1


@pytest.mark.parametrize('field',['questions','acceptance'])
@pytest.mark.parametrize('second',['Confirm adopted audio?','  Confirm   adopted\u00a0audio?  ','CONFIRM ADOPTED AUDIO?'])
def test_new_statement_repetition_is_rejected_without_rewriting_original(field,second,setup_advice):
    """Exact repeats, Unicode whitespace and letter casing cannot fill new advice with duplicate statements."""
    intake,_journal,request,*_=setup_advice
    _row,context,_registry=original_context(intake,request.version_id)
    value=deepcopy(original.ADVICE)
    if field=='questions':value.update(suggested_decision='needs_clarification',acceptance=[])
    value[field]=['Confirm adopted audio?',second]
    before=deepcopy(value)
    with pytest.raises(ValueError,match='Repeated new advice statement'):
        advice_child.validate_fresh_advice(value,context)
    assert value==before


def test_parent_revalidates_new_child_statement_repetition(setup_advice):
    """A ready-looking child envelope cannot bypass the fresh-response guard in the parent process."""
    intake,_journal,request,*_=setup_advice
    _row,context,_registry=original_context(intake,request.version_id)
    value={'state':'ready','submitted':True,'advice':repeated_advice(),'usage':{'prompt_eval_count':539,'eval_count':221},
        'model_digest':original.MODEL_DIGEST,'response_sha256':'b'*64}
    with pytest.raises(ValueError,match='Repeated new advice statement'):
        response_receipt(value,0,context,original.model_configuration())


def test_distinct_new_statements_keep_their_original_text(setup_advice):
    """Valid distinct criteria and clarification preserve casing/spacing and retain their existing advisory-only contract."""
    intake,_journal,request,*_=setup_advice
    _row,context,_registry=original_context(intake,request.version_id)
    value=deepcopy(original.ADVICE)
    value['acceptance']=['  Preserve adopted audio.  ','Restore the original version.']
    assert advice_child.validate_fresh_advice(value,context).model_dump()==value
    value.update(suggested_decision='needs_clarification',acceptance=[],questions=['Which project?','Which output format?'])
    assert advice_child.validate_fresh_advice(value,context).model_dump()==value


@pytest.mark.asyncio
async def test_existing_ready_repetition_remains_original_history_without_a_new_call(setup_advice):
    """Tighter acceptance of new model replies cannot erase or reinterpret a previously stored structurally valid receipt."""
    intake,journal,request,source,observe,missing_config=setup_advice
    _row,context,registry=original_context(intake,request.version_id)
    observation={**proof(source).receipt(),'version_id':request.version_id,'feature_registry_sha256':registry}
    claimed,_original=journal.reserve(request,original.model_configuration(),context,observation);assert claimed
    receipt={'state':'ready','submitted':True,'advice':repeated_advice(),'usage':{'prompt_eval_count':539,'eval_count':221},
        'model_digest':original.MODEL_DIGEST,'response_sha256':'b'*64,'execution_authorized':False,'external_fee_fen':0,
        'model_quality_qualified':False,'independent_review_completed':False,'public_feedback_sent':False,
        'source_current_at_completion':True,'requires_fresh_source_for_maintainer_decision':True}
    stored=journal.finish(request,receipt)
    async def no_new_call(*_args,**_kwargs):
        """Any attempted inference during original-history recovery is a regression."""
        raise AssertionError('Original history must not invoke a child')
    recovered=await create_advice(intake,journal,request,missing_config,source_verifier=observe,runner=no_new_call,clock_us=lambda:NOW)
    assert recovered['receipt']==stored['receipt'] and recovered['receipt']['advice']==receipt['advice']
    assert journal.replay(request)==stored and not recovered['execution_authorized']
