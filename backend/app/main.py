"""FastAPI 应用入口。"""

from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from sqlalchemy.exc import IntegrityError
from pydantic import ValidationError

from app.api.v1 import router as api_v1_router
from app.bootstrap import bootstrap_all_registries
from app.config import settings
from app.schemas.common import ApiResponse


def _error_message(detail: object) -> str:
    """将异常 detail 转为前端可读的 message。"""
    if isinstance(detail, str):
        return detail
    if isinstance(detail, list):
        parts = []
        for item in detail:
            if isinstance(item, dict) and "msg" in item:
                loc = item.get("loc", ())
                loc_str = ".".join(str(x) for x in loc if x != "body")
                parts.append(f"{loc_str}: {item['msg']}" if loc_str else item["msg"])
            else:
                parts.append(str(item))
        return "; ".join(parts) if parts else "Validation error"
    return str(detail)


async def http_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    """HTTP 异常统一为 { code, message, data: null }。"""
    from fastapi import HTTPException

    if isinstance(exc, HTTPException):
        code = exc.status_code
        message = _error_message(exc.detail)
    else:
        code = 500
        message = "Internal server error"
    body = ApiResponse[None](code=code, message=message, data=None, meta=None).model_dump()
    return JSONResponse(status_code=code, content=body, headers=exc.headers if isinstance(exc, HTTPException) else None)


async def validation_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    """422 校验异常统一为 { code: 422, message, data: null }。"""
    assert isinstance(exc, RequestValidationError)
    message = _error_message(exc.errors())
    body = ApiResponse[None](code=422, message=message, data=None, meta=None).model_dump()
    return JSONResponse(status_code=422, content=body)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """应用生命周期：启动时初始化，关闭时清理。"""
    # 启动时：供应商注册 + 任务执行器注册（幂等）
    from sqlalchemy import text
    from app.core.db import engine
    from app.core.sqlite_runtime import prepare_sqlite
    await prepare_sqlite(engine)
    if engine.dialect.name == 'sqlite':
        async with engine.connect() as conn:
            version=(await conn.execute(text('SELECT version FROM production_schema'))).scalars().all()
            if version != [1]:
                raise RuntimeError('Run the supported production migration before starting')
    bootstrap_all_registries()
    reverse_scanner=None
    from pathlib import Path
    if settings.reverse_analysis_enabled and Path(settings.production_probe_executable).is_file():
        if engine.dialect.name=='sqlite':
            async with engine.connect() as connection:
                try:reverse_version=(await connection.execute(text('SELECT version FROM reverse_schema'))).scalars().all()
                except Exception:raise RuntimeError('Run the supported reverse-analysis migration before enabling video analysis') from None
                if reverse_version!=[1]:raise RuntimeError('Unsupported reverse-analysis schema version')
        from app.services.reverse.runtime import ReverseScanner
        from app.core.db import async_session_maker
        reverse_scanner=ReverseScanner(async_session_maker,settings.production_probe_executable)
    app.state.reverse_scanner=reverse_scanner
    local_export_worker=None
    from app.services.editing.exports import LocalExportWorker, enabled
    if enabled(settings.production_probe_executable):
        async with engine.connect() as connection:
            try:
                editing_versions=(await connection.execute(text('SELECT version FROM editing_schema'))).scalars().all()
                export_versions=(await connection.execute(text('SELECT version FROM edit_export_schema'))).scalars().all()
            except Exception:
                raise RuntimeError('Run the supported editing migration before enabling local export') from None
            if editing_versions!=[1] or export_versions!=[1]:
                raise RuntimeError('Unsupported editing/export schema version')
        from app.core.db import async_session_maker
        local_export_worker=LocalExportWorker(async_session_maker,settings.production_probe_executable)
    app.state.local_export_worker=local_export_worker
    transition_export_worker = None
    if local_export_worker is not None:
        from app.core.db import async_session_maker
        from app.services.editing.transition_exports import TransitionExportWorker
        transition_export_worker = TransitionExportWorker(async_session_maker, settings.production_probe_executable)
    app.state.transition_export_worker = transition_export_worker
    from app.services.worker.model_prices import load_model_prices
    model_prices=load_model_prices(settings.model_price_config)
    app.state.model_prices=model_prices
    from app.services.worker.workspace_local_models import load_workspace_local_models
    local_models = load_workspace_local_models(settings.workspace_local_models_config, workspace_id=settings.workspace_id)
    app.state.workspace_local_models = local_models
    # Add the inert catalog only after migration checks succeed. Respect even an
    # explicitly injected None, and remove only the runtime this lifespan owns.
    owns_preparation_runtime=not hasattr(app.state,'execution_runtime')
    if owns_preparation_runtime:
        from app.core.db import async_session_maker
        from app.services.worker.seedance25_runtime import build_preparation_runtime
        from app.services.worker.workspace_video_runtime import build_managed_video_runtime
        configured_runtime, setup = await build_managed_video_runtime(async_session_maker, settings.video_runtime_config,model_prices,
            trusted_local_host=local_models.host_for('native_video'), trusted_local_port=local_models.native_video_port)
        app.state.workspace_video_setup = setup
        app.state.native_video_history_factory=async_session_maker
        app.state.execution_runtime=configured_runtime or build_preparation_runtime(async_session_maker)
    runtime=getattr(app.state,'execution_runtime',None)
    from app.core.db import async_session_maker
    from app.services.agent_execution import AgentExecutionRuntime
    agent_execution_runtime = AgentExecutionRuntime(async_session_maker,price_registry=model_prices,
        trusted_local_host=local_models.host_for('agent_text'), trusted_local_ports=local_models.agent_text_ports)
    app.state.agent_execution_runtime = agent_execution_runtime
    from app.services.asset_image_generation import AssetImageGenerationRuntime
    asset_image_generation_runtime = AssetImageGenerationRuntime(async_session_maker,price_registry=model_prices,
        trusted_local_host=local_models.host_for('asset_image'), trusted_local_ports=local_models.asset_image_ports)
    app.state.asset_image_generation_runtime = asset_image_generation_runtime
    from app.services.video_understanding.runtime import UnderstandingWorker
    understanding_worker = UnderstandingWorker(async_session_maker, settings.production_probe_executable,
        enabled=settings.video_understanding_enabled, allow_remote=settings.video_understanding_allow_remote,
        price_registry=model_prices,
        trusted_local_host=local_models.host_for('video_understanding') if settings.workspace_id else settings.video_understanding_local_host,
        trusted_local_ports=local_models.video_understanding_ports if settings.workspace_id else settings.video_understanding_local_ports)
    app.state.understanding_worker = understanding_worker
    try:
        # Imported historical queues retain their original records without
        # automatically replaying model calls, decoders or export work.
        if not settings.workspace_runtime_locked:
            if runtime is not None:await runtime.start()
            await agent_execution_runtime.start()
            await asset_image_generation_runtime.start()
            await understanding_worker.start()
            if reverse_scanner is not None:await reverse_scanner.start()
            if local_export_worker is not None:await local_export_worker.start()
            if transition_export_worker is not None:await transition_export_worker.start()
        yield
    finally:
        await asset_image_generation_runtime.stop()
        app.state.asset_image_generation_runtime = None
        if transition_export_worker is not None:await transition_export_worker.stop()
        app.state.transition_export_worker = None
        await understanding_worker.stop()
        app.state.understanding_worker = None
        await agent_execution_runtime.stop()
        app.state.agent_execution_runtime = None
        if local_export_worker is not None:await local_export_worker.stop()
        app.state.local_export_worker=None
        if reverse_scanner is not None:await reverse_scanner.stop()
        app.state.reverse_scanner=None
        if runtime is not None:await runtime.stop()
        if owns_preparation_runtime:
            delattr(app.state,'execution_runtime')
            delattr(app.state,'native_video_history_factory')


app = FastAPI(
    title=settings.app_name,
    version="0.1.0",
    lifespan=lifespan,
    docs_url="/docs",
    redoc_url="/redoc",
)

if settings.workspace_id is not None:
    from app.core.workspace_boundary import TenantBoundaryMiddleware
    if not settings.workspace_internal_token:
        raise RuntimeError('Tenant processes require a gateway-owned internal token')
    app.add_middleware(TenantBoundaryMiddleware, workspace_id=settings.workspace_id,
                       internal_token=settings.workspace_internal_token)

# 统一错误响应格式：{ code, message, data: null }
app.add_exception_handler(RequestValidationError, validation_exception_handler)
app.add_exception_handler(HTTPException, http_exception_handler)
app.add_exception_handler(Exception, http_exception_handler)


@app.exception_handler(IntegrityError)
async def integrity_exception_handler(request: Request, exc: IntegrityError) -> JSONResponse:
    """Report rejected references without exposing SQL, parameters, or private data."""
    return JSONResponse(status_code=409, content={"code":409,"message":"数据引用冲突：请检查工程归属、版本或重复记录","data":None})


@app.exception_handler(ValidationError)
async def domain_validation_exception_handler(request: Request, exc: ValidationError) -> JSONResponse:
    """Treat legacy dictionary-body validation as input failure without echoing payloads."""
    return JSONResponse(status_code=422, content={"code":422,"message":"输入字段不符合要求","data":None})

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins_list,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(api_v1_router, prefix=settings.api_v1_prefix)
# 影视技能路由同时挂到主应用，保证 /api/v1/film 一定可访问


@app.get("/health")
async def health():
    """健康检查。"""
    from app.schemas.common import success_response
    return success_response({"status": "ok"})
