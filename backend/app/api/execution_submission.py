"""Strict submission boundary; trusted server runtime is mandatory and default-off."""
from fastapi import APIRouter,HTTPException,Request
from pydantic import BaseModel,ConfigDict,Field,StrictInt
from app.schemas.common import ApiResponse,success_response
from app.services.worker.execution_admission import admit

router=APIRouter(prefix='/execution',tags=['execution'])


@router.post('/callbacks',include_in_schema=False)
async def reject_unconfigured_callback():
    """Polling-only mode accepts no callbacks, including delayed duplicate events."""
    raise HTTPException(403,'未启用受信回调，请继续查询原任务；回调未应用')


class ReferenceInput(BaseModel):
    """A project media identity; no URLs, credentials or provider overrides."""
    model_config=ConfigDict(extra='forbid')
    media_id: str=Field(min_length=1,max_length=64)
    kind: str=Field(pattern='^(image|video|audio)$')


class SubmissionInput(BaseModel):
    """Client owns desired content and idempotency identity, never capability or cost."""
    model_config=ConfigDict(extra='forbid')
    request_key: str=Field(min_length=1,max_length=128)
    model_id: str=Field(min_length=1,max_length=128)
    duration: StrictInt
    references: list[ReferenceInput]=Field(default_factory=list,max_length=32)
    prompt: str=Field(min_length=1,max_length=32000)
    prompt_revision: str=Field(default='1',min_length=1,max_length=128)


class AdmissionRead(BaseModel):
    """Return durable identity without external URLs or account details."""
    job_id: str


class RuntimeRead(BaseModel):
    """Minimal availability explains why generation remains unavailable."""
    available: bool
    model_ids: list[str]
    message: str


@router.get('/availability',response_model=ApiResponse[RuntimeRead])
async def execution_availability(request: Request):
    """Unconfigured installation is usable for project work without model accounts."""
    runtime=getattr(request.app.state,'execution_runtime',None)
    available=bool(runtime and runtime.allow_submission and runtime.running)
    return success_response({'available':available,'model_ids':sorted(runtime.policies) if available else [],'message':'生成服务已就绪' if available else '生成服务尚未配置或未启用，可继续准备项目与素材'})


@router.post('/projects/{project_id}/submissions',response_model=ApiResponse[AdmissionRead],status_code=201)
async def submit_execution(project_id: str,body: SubmissionInput,request: Request):
    """Commit admission and budget before the durable scanner can discover this work."""
    runtime=getattr(request.app.state,'execution_runtime',None)
    if runtime is None or not runtime.allow_submission or not runtime.running:
        raise HTTPException(503,'生成服务尚未配置或未启用，未创建任务、未预留费用')
    policy=runtime.policies.get(body.model_id)
    if policy is None:raise HTTPException(422,'该模型尚未配置受信能力与报价')
    snapshot=body.model_dump(exclude={'request_key'})
    job_id=await admit(runtime.worker.factory,project_id,body.request_key,snapshot,policy)
    return success_response({'job_id':job_id})
