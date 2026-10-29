"""Select only allowlisted task metadata; no ORM entity, prompt, output, binding or credential is loaded."""
import asyncio
from datetime import datetime, timezone
import math
import re
import time
from urllib.parse import urlencode
from sqlalchemy import case, exists, select
from app.models.agent_config import AgentConfig
from app.models.agent_execution import AgentRun
from app.models.asset_image_generation import AssetImageJob
from app.models.llm import Model
from app.models.reverse import ReverseAnalysis
from app.models.studio import Actor, Costume, Project, Prop, Scene
from app.models.video_understanding import UnderstandingAttempt, UnderstandingJob

KINDS = ('agent_workflow', 'model_connection_test', 'video_understanding', 'asset_image_generation')
TITLES = {'agent_workflow': 'Agent工作流执行', 'model_connection_test': '模型连接测试', 'video_understanding': '视频自动拆解', 'asset_image_generation': '资产图片候选生成'}
STATUS = {'queued': 'pending', 'pending': 'pending', 'running': 'running', 'succeeded': 'succeeded', 'failed': 'failed', 'cancelled': 'cancelled', 'candidate': 'succeeded', 'adopted': 'succeeded'}
SAFE_ID = re.compile(r'^[A-Za-z0-9_-]{1,64}$')


def stamp(value) -> float | None:
    """Return only real finite source clocks; missing times do not become invented starts or finishes."""
    if isinstance(value, datetime):
        value = (value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value).timestamp()
    if type(value) not in {int, float} or not math.isfinite(value) or value < 0:
        return None
    return float(value)


def safe_id(value) -> bool:
    """Only opaque local identifiers may form path segments; malformed legacy identities return to a list."""
    return isinstance(value, str) and SAFE_ID.fullmatch(value) is not None


def summary(kind: str, row, target: str, note=None, progress=None) -> dict | None:
    """Normalize recognized status vocabulary without implying adoption, model quality or an unknown percentage."""
    status = STATUS.get(row['status'])
    if status is None:
        return None
    return {'id': f"workspace:{kind}:{row['id']}", 'kind': kind, 'title': TITLES[kind], 'status': status,
        'progress': progress if type(progress) is int and 0 <= progress <= 100 else None,
        'created_at_ts': stamp(row['created_at']), 'started_at_ts': stamp(row.get('started_at')),
        'finished_at_ts': stamp(row.get('finished_at')), 'return_path': target, 'return_note': note}


async def agent_items(factory, kind: str, limit: int) -> list:
    """Read Agent/connection-test metadata and target existence columns, never node or binding data."""
    run_kind = 'workflow' if kind == 'agent_workflow' else 'connection_test'
    target_id = AgentRun.agent_id if kind == 'agent_workflow' else AgentRun.model_id
    target_model = AgentConfig if kind == 'agent_workflow' else Model
    statement = select(AgentRun.id, target_id, AgentRun.status, AgentRun.created_at, AgentRun.started_at, AgentRun.finished_at,
        exists(select(target_model.id).where(target_model.id == target_id)).label('target_exists'))
    statement = statement.where(AgentRun.kind == run_kind).order_by(case((AgentRun.status.in_(['queued', 'running']), 1), else_=0).desc(), AgentRun.created_at.desc(), AgentRun.id).limit(limit)
    async with factory() as db:
        rows = (await db.execute(statement)).mappings().all()
    result = []
    for row in rows:
        if kind == 'agent_workflow':
            valid = row['target_exists'] and safe_id(row['agent_id']) and safe_id(row['id'])
            target = f"/agents/{row['agent_id']}/edit?{urlencode({'agentRun': row['id']})}#agent-execution" if valid else '/agents'
            note = None if valid else '原Agent已移除或不可定位，执行历史仍保留；返回Agent列表查看。'
        else:
            valid = row['target_exists'] and safe_id(row['model_id']) and safe_id(row['id'])
            target = '/models?' + urlencode({'tab': 'models', 'connectionModel': row['model_id'], 'connectionRun': row['id']}) if valid else '/models?tab=models'
            note = None if valid else '原模型已移除或不可定位，测试历史仍保留；返回模型列表查看。'
        result.append(summary(kind, row, target, note))
    return result


async def understanding_items(factory, limit: int) -> list:
    """Read stored phase progress and attempt times, excluding source manifests, model endpoints and candidates."""
    statement = select(UnderstandingJob.id, UnderstandingJob.project_id, UnderstandingJob.analysis_id,
        UnderstandingJob.state.label('status'), UnderstandingJob.progress, UnderstandingJob.created_at,
        UnderstandingAttempt.started_at, UnderstandingAttempt.finished_at,
        exists(select(Project.id).where(Project.id == UnderstandingJob.project_id)).label('project_exists'),
        exists(select(ReverseAnalysis.id).where(ReverseAnalysis.id == UnderstandingJob.analysis_id, ReverseAnalysis.project_id == UnderstandingJob.project_id)).label('analysis_exists'))
    statement = statement.outerjoin(UnderstandingAttempt, (UnderstandingAttempt.job_id == UnderstandingJob.id) & (UnderstandingAttempt.project_id == UnderstandingJob.project_id) & (UnderstandingAttempt.attempt == UnderstandingJob.attempt))
    statement = statement.order_by(case((UnderstandingJob.state.in_(['queued', 'running']), 1), else_=0).desc(), UnderstandingJob.created_at.desc(), UnderstandingJob.id).limit(limit)
    async with factory() as db:
        rows = (await db.execute(statement)).mappings().all()
    result = []
    for row in rows:
        valid = row['project_exists'] and row['analysis_exists'] and all(safe_id(row[key]) for key in ('project_id', 'analysis_id', 'id'))
        target = f"/projects/{row['project_id']}?" + urlencode({'tab': 'production', 'analysis': row['analysis_id'], 'understandingJob': row['id']}) + '#video-understanding' if valid else '/projects'
        note = None if valid else '原工程或分析已移除或不可定位，理解历史仍保留；返回项目列表查看。'
        result.append(summary('video_understanding', row, target, note, row['progress']))
    return result


async def image_items(factory, limit: int) -> list:
    """Read image-candidate status and asset existence, excluding prompts, storage keys and media evidence."""
    targets = {'actor': (Actor, 'actors'), 'costume': (Costume, 'costumes'), 'scene': (Scene, 'scenes'), 'prop': (Prop, 'props')}
    existence = case(*[(AssetImageJob.asset_kind == kind, exists(select(model.id).where(model.id == AssetImageJob.asset_id))) for kind, (model, _plural) in targets.items()], else_=False).label('asset_exists')
    statement = select(AssetImageJob.id, AssetImageJob.asset_kind, AssetImageJob.asset_id, AssetImageJob.status, AssetImageJob.created_at, AssetImageJob.finished_at, existence)
    statement = statement.order_by(case((AssetImageJob.status.in_(['queued', 'running']), 1), else_=0).desc(), AssetImageJob.created_at.desc(), AssetImageJob.id).limit(limit)
    async with factory() as db:
        rows = (await db.execute(statement)).mappings().all()
    result = []
    for row in rows:
        valid = row['asset_exists'] and row['asset_kind'] in targets and safe_id(row['asset_id']) and safe_id(row['id'])
        target = f"/assets/{targets[row['asset_kind']][1]}/{row['asset_id']}/edit?{urlencode({'imageJob': row['id']})}#asset-image-generation" if valid else '/assets'
        note = None if valid else '原资产已移除或不可定位，图片任务历史仍保留；返回资产列表查看。'
        result.append(summary('asset_image_generation', row, target, note))
    return result


async def recent_tasks(factory, limit: int = 100) -> dict:
    """Collect bounded independent sources; one unavailable source does not hide known tasks from the others."""
    per_source = min(limit, 50)
    results = await asyncio.gather(agent_items(factory, 'agent_workflow', per_source), agent_items(factory, 'model_connection_test', per_source), understanding_items(factory, per_source), image_items(factory, per_source), return_exceptions=True)
    items, sources = [], []
    for kind, result in zip(KINDS, results):
        if isinstance(result, BaseException):
            sources.append({'kind': kind, 'available': False, 'message': TITLES[kind] + '状态暂时无法读取；保留上次记录，请重试或回所属页面核对。'})
            continue
        valid = [item for item in result if item is not None]
        complete = len(valid) == len(result)
        items.extend(valid)
        sources.append({'kind': kind, 'available': complete, 'message': '' if complete else TITLES[kind] + '有未识别状态，部分记录未更新；请回所属页面核对。'})
    priority = {'running': 2, 'pending': 1}
    items.sort(key=lambda item: (priority.get(item['status'], 0), item['started_at_ts'] or item['created_at_ts'] or 0, item['id']), reverse=True)
    return {'items': items[:limit], 'sources': sources, 'complete': all(source['available'] for source in sources), 'limit': limit, 'per_source_limit': per_source, 'read_at_ts': time.time()}

