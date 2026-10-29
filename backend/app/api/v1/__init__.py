"""API v1 路由聚合。"""

from fastapi import APIRouter
from app.controlplane.routes import router as identity_router
from app.api import execution, execution_submission, reverse, benchmark, script_preparation, shot_plan, editing, editing_export, video_dna, video_dna_preparation
from app.api import video_dna_production, video_runtime_setup, video_understanding, editing_v2, workspace_tasks, workspace_budget

from app.api.v1.routes import agents, agent_execution, asset_image_generation, film, health, llm, studio, script_processing

router = APIRouter()
router.include_router(identity_router)
router.include_router(agents.router)
router.include_router(agent_execution.router)
router.include_router(asset_image_generation.router)
router.include_router(execution.router)
router.include_router(execution_submission.router)
router.include_router(reverse.router)
router.include_router(benchmark.router)
router.include_router(script_preparation.router)
router.include_router(shot_plan.router)
router.include_router(editing.router)
router.include_router(editing_v2.router)
router.include_router(editing_export.router)
router.include_router(video_dna.router)
router.include_router(video_dna_production.router)
router.include_router(video_runtime_setup.router)
router.include_router(workspace_budget.router)
router.include_router(video_understanding.router)
router.include_router(workspace_tasks.router)
router.include_router(video_dna_preparation.router)

router.include_router(health.router, tags=["health"])
router.include_router(film.router, prefix="/film", tags=["film"])
router.include_router(llm.router, prefix="/llm", tags=["llm"])
router.include_router(studio.router, prefix="/studio")
router.include_router(script_processing.router)
