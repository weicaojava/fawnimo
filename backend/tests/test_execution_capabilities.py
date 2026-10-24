"""Preflight fixture tests do not qualify an external model or spend money."""
from decimal import Decimal
import pytest
from fastapi import HTTPException
from app.core.contracts.execution_capabilities import Capability, preflight


@pytest.mark.parametrize('change',[
    {'duration':True},{'duration':12},{'model_id':'other'},
    {'references':[{'media_id':'a','kind':'audio'}]},
    {'references':[{'media_id':'a','kind':'image','url':'https://injected.invalid'}]},
    {'endpoint':'https://injected.invalid'},
])
def test_unsupported_inputs_rejected(change):
    """Capabilities come from trusted configuration, not requested overrides."""
    request={'model_id':'fixture','duration':5,'references':[],**change}
    with pytest.raises(HTTPException):preflight(request,Capability('fixture',frozenset({5}),frozenset({'image'}),1),quoted_cost=Decimal('1'),remaining_budget=Decimal('2'),iteration=0,iteration_limit=1)


@pytest.mark.parametrize('quote,budget,iteration',[(None,'2',0),(Decimal('NaN'),'2',0),(Decimal('1'),'0',0),(Decimal('1'),'2',1)])
def test_limits_pause_without_submission(quote,budget,iteration):
    """Unknown quotes, exhausted budgets and iteration limits cannot pass preflight."""
    with pytest.raises(HTTPException):preflight({'model_id':'fixture','duration':5},Capability('fixture',frozenset({5}),frozenset(),0),quoted_cost=quote,remaining_budget=Decimal(budget),iteration=iteration,iteration_limit=1)
