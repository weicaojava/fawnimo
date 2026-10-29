"""Execution inspection and explicit controls, with no submission/execution route."""
from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession
from app.dependencies import get_db
from app.schemas.common import ApiResponse, success_response
from app.services.worker.execution_queries import control_job, list_jobs, safe_view, scoped_job

router=APIRouter(prefix='/execution/projects/{project_id}/jobs',tags=['execution'])


class ExecutionRead(BaseModel):
    """Safe task projection deliberately excluding snapshot, result and remote identity."""
    id: str
    project_id: str
    state: str
    state_label: str
    reason: str
    cancel_requested: bool
    query_retries: int
    download_retries: int
    can_resume: bool
    can_cancel: bool
    can_pause: bool


@router.get('',response_model=ApiResponse[list[ExecutionRead]])
async def get_execution_jobs(project_id: str, offset: int=Query(0,ge=0), limit: int=Query(50,ge=1,le=200), db: AsyncSession=Depends(get_db, scope='function')):
    """查看本工程任务；不公开远程结果地址、请求快照或凭据。"""
    return success_response(await list_jobs(db,project_id,offset,limit))


@router.get('/{job_id}',response_model=ApiResponse[ExecutionRead])
async def get_execution_job(project_id: str, job_id: str, db: AsyncSession=Depends(get_db, scope='function')):
    """查看工程内单个任务的安全状态。"""
    return success_response(safe_view(await scoped_job(db,project_id,job_id)))


@router.post('/{job_id}/pause',response_model=ApiResponse[ExecutionRead])
async def pause_execution_job(project_id: str, job_id: str, db: AsyncSession=Depends(get_db, scope='function')):
    """暂停后续查询或下载；正在执行的一步需先结束。"""
    return success_response(await control_job(db,project_id,job_id,'pause'))


@router.post('/{job_id}/resume',response_model=ApiResponse[ExecutionRead])
async def resume_execution_job(project_id: str, job_id: str, db: AsyncSession=Depends(get_db, scope='function')):
    """仅恢复已暂停任务的查询或下载，不创建或重新提交请求。"""
    return success_response(await control_job(db,project_id,job_id,'resume'))


@router.post('/{job_id}/cancel',response_model=ApiResponse[ExecutionRead])
async def cancel_execution_job(project_id: str, job_id: str, db: AsyncSession=Depends(get_db, scope='function')):
    """记录取消意图；已提交任务需等待远端确认，提交前可本地取消。"""
    return success_response(await control_job(db,project_id,job_id,'cancel'))
