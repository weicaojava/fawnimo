"""Preview an owned current M4B plan against the exact non-executing S25A profile."""
from fastapi import HTTPException
from app.core.contracts.seedance25 import MODEL_ID, PREPARATION_PROFILE_HASH
from app.core.contracts.execution_binding import binding_fingerprint
from app.core.contracts.seedance25_preparation import Seedance25ProfileRead, Seedance25PlanPreview
from app.core.integrations.volcengine.seedance25_payload import build_seedance25_body
from app.services.worker.seedance25_runtime import preparation_policy
from app.services.shot_plan.compiler import content_hash, issue, public_policy
from app.services.shot_plan.service import owned_plan, preflight_plan


def profile_view():
    """Return public software limits without loading credentials or testing a model."""
    return Seedance25ProfileRead.model_validate({
        'model_id': MODEL_ID,
        'profile_hash': PREPARATION_PROFILE_HASH,
        'preparation_binding_hash': binding_fingerprint(preparation_policy()),
        'unsupported_modes': ['首帧/首尾帧', '图像参考', '视频参考', '音频参考', '编辑/延长', '样片draft', '自动时长'],
        'message': 'S25A仅供文生视频准备与参数检查。报价未知、付费执行关闭，未验证真实模型；已保存密钥不自动启用生成。',
    }).model_dump()


async def preview_plan(db, project_id: str, plan_id: str):
    """Require current facts and exact preparation binding, without requiring paid readiness.

    Old unbound, stale or other-account plans remain readable in their ordinary
    history view, but receive no reconstructed body under this newer profile.
    """
    plan = await owned_plan(db, project_id, plan_id)
    common = []
    if plan.payload.get('binding') != public_policy(preparation_policy()):
        common.append(issue('seedance25_profile_mismatch', '该历史计划未绑定当前S25A准备档案；请保存新草稿修订并重新编译，原计划不升级'))
    check = await preflight_plan(db, project_id, plan_id)
    common.extend(check['issues'])
    entries = []
    for entry in plan.payload['entries']:
        problems = []
        body = None
        if not common:
            try:
                body = build_seedance25_body(entry['request'])
            except (ValueError, TypeError, HTTPException):
                problems.append(issue('seedance25_input_unsupported', '此切片仅支持指定模型、4–30整数秒和纯文本；资产版本自带媒体及显式参考均不可送入', entry['shot_id']))
        entries.append({'shot_id': entry['shot_id'], 'entry_hash': entry['entry_hash'], 'body': body, 'issues': problems})
    ready = bool(entries) and not common and not any(entry['issues'] for entry in entries)
    result = {
        'plan_id': plan.id, 'content_hash': plan.content_hash,
        'profile_hash': PREPARATION_PROFILE_HASH,
        'wire_hash': content_hash({'profile_hash': PREPARATION_PROFILE_HASH, 'entries': entries}) if ready else None,
        'can_prepare': ready, 'issues': common, 'entries': entries,
    }
    return Seedance25PlanPreview.model_validate(result).model_dump()
