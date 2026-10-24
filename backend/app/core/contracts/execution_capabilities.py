"""Pure preflight checks; trusted configuration supplies capabilities and quotes."""
from dataclasses import dataclass
from decimal import Decimal
from fastapi import HTTPException


@dataclass(frozen=True)
class Capability:
    """An administrator-owned capability record, never an imported prompt field."""
    model_id: str
    durations: frozenset[int]
    reference_kinds: frozenset[str]
    max_references: int


def preflight(request: dict, capability: Capability, *, quoted_cost: Decimal | None, remaining_budget: Decimal, iteration: int, iteration_limit: int) -> None:
    """Reject unsupported requests and unknown costs before reserving or sending.

    This pure check does not reserve money. The eventual submission transaction
    must reserve the quoted amount atomically before a worker can send a request.
    """
    if set(request)-{'model_id','duration','references','prompt','prompt_revision'}:
        raise HTTPException(422,'包含未支持的生成参数')
    if request.get('model_id')!=capability.model_id:
        raise HTTPException(422,'模型与受信能力配置不一致')
    duration=request.get('duration')
    if type(duration) is not int or duration not in capability.durations:
        raise HTTPException(422,'模型不支持该片段时长')
    references=request.get('references',[])
    if not isinstance(references,list) or len(references)>capability.max_references:
        raise HTTPException(422,'参考素材数量不受支持')
    for reference in references:
        if not isinstance(reference,dict) or set(reference)!={'media_id','kind'} or reference.get('kind') not in capability.reference_kinds or not isinstance(reference.get('media_id'),str) or not reference['media_id']:
            raise HTTPException(422,'参考素材类型或标识不受支持')
    if type(iteration) is not int or type(iteration_limit) is not int or not 0<=iteration<iteration_limit:
        raise HTTPException(409,'已达到迭代次数限制')
    if quoted_cost is None or not quoted_cost.is_finite() or quoted_cost<0:
        raise HTTPException(409,'报价未知，任务保持暂停')
    if not remaining_budget.is_finite() or remaining_budget<quoted_cost:
        raise HTTPException(409,'预算不足，任务保持暂停')
