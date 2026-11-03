"""Opt-in workspace video transport and protected project budget administration."""
from __future__ import annotations

import os
import hashlib
import json
from pathlib import Path

from fastapi import HTTPException
from sqlalchemy import func, select, update

from app.core.contracts.execution_capabilities import Capability
from app.core.contracts.seedance25 import MODEL_ID, SUPPORTED_DURATIONS, Seedance25Configuration
from app.core.contracts.video_runtime_setup import ProjectBudgetRead, ProjectBudgetWrite, VideoRuntimeConfiguration
from app.core.integrations.volcengine.seedance25 import Seedance25DurableTransport
from app.models.execution_budget import ExecutionBudget, ExecutionBudgetEntry
from app.services.worker.model_billing import billing_summary,project_read_snapshot
from app.models.model_charge import ModelCallCharge
from app.services.studio.production import project_exists
from app.services.worker.durable_execution import DurableExecution
from app.services.worker.execution_admission import AdmissionPolicy
from app.services.worker.execution_budget import transaction
from app.services.worker.project_budget import record_budget_change
from app.services.worker.execution_runtime import ExecutionRuntime
from app.core.integrations.volcengine.seedance25_payload import build_seedance25_body
from app.core.contracts.seedance_references import REFERENCE_PROTOCOL,snapshot_reference_selection


def setup_projection(config: VideoRuntimeConfiguration | None = None, *, issue_code='not_configured', message=None, credential_present=False) -> dict:
    """Keep the preparation catalog distinct from an explicitly configured executable runtime."""
    return {
        'configured': config is not None, 'enabled': bool(config and config.enabled), 'running': False,
        'issue_code': issue_code, 'message': message or '视频执行服务尚未配置；可先准备资料并保存工程预算。',
        'model_id': MODEL_ID, 'durations': sorted(SUPPORTED_DURATIONS),
        'quote_minor': config.quote_minor if config else None,
        'quote_note': config.quote_note if config else '',
        'iteration_limit': config.iteration_limit if config else 10,
        'credential_present': credential_present,
        'output_description': '文生视频 · 9:16 · 480p · MP4 · 无生成音频 · 保留水印；本配置不接受参考图片。',
        'real_model_qualified': False,
    }


def build_workspace_video_runtime(factory, filename: str | None, *, environment=None, configuration_factory=Seedance25Configuration, transport_factory=Seedance25DurableTransport):
    """Build, but never start or call, the explicitly enabled server-file configuration.

    Injection is for isolated protocol tests only. JSON cannot override endpoint,
    download hosts, network policy, credential values or qualification claims.
    An invalid configuration leaves manual project work usable and reports a
    fixed error without reflecting secret file contents into the API or logs.
    """
    if not filename:
        return None, setup_projection()
    try:
        path = Path(filename).expanduser()
        if not path.is_file() or path.stat().st_size > 16384:
            raise ValueError()
        raw = path.read_bytes()
        if len(raw) > 16384:
            raise ValueError()
        config = VideoRuntimeConfiguration.model_validate_json(raw)
    except Exception:
        return None, setup_projection(issue_code='invalid_configuration', message='视频服务配置文件无效；请核对模板字段，项目手动功能仍可使用。')
    if not config.enabled:
        return None, setup_projection(config, issue_code='disabled', message='视频服务配置已保存且保持关闭，没有启动模型任务。')
    if config.quote_minor is None or not config.quote_note.strip():
        return None, setup_projection(config, issue_code='quote_required', message='尚未登记经核对的单次费用预留值及依据，视频服务保持关闭。')
    secret = (os.environ if environment is None else environment).get(config.credential_env, '')
    if not secret:
        return None, setup_projection(config, issue_code='credential_required', message='尚未提供专用视频凭据，请按配置模板设置本地凭据后重启。')
    try:
        trusted = configuration_factory(api_key=secret, service_version=config.service_version, account_version=config.account_version)
        transport = transport_factory(trusted)
        policy = AdmissionPolicy(
            capability=Capability(MODEL_ID, SUPPORTED_DURATIONS, frozenset(), 0),
            quote_minor=config.quote_minor, currency='CNY', iteration_limit=config.iteration_limit,
            binding_id=trusted.binding_id,
        )
        runtime = ExecutionRuntime(DurableExecution(transport, factory=factory), {MODEL_ID: policy}, allow_submission=True)
    except Exception:
        return None, setup_projection(config, issue_code='invalid_credentials_or_profile', message='视频凭据或独立配置版本不符合要求，服务保持关闭。')
    return runtime, setup_projection(config, issue_code='configured', message='执行配置已就绪；仅显式提交且预算足够的受信计划会创建任务。', credential_present=True)


def configuration_template() -> dict:
    """Return an inert editable template that contains no credentials or invented pricing."""
    return VideoRuntimeConfiguration().model_dump(mode='json')


async def build_managed_video_runtime(factory,filename,price_registry,*,trusted_local_host=None,trusted_local_port=None,transport_factory=Seedance25DurableTransport,
                                     trusted_local_media_origin=None,reference_delivery_available=None):
    """Compose the application runtime from an explicit saved model and shared prices; legacy raw-key files cannot grant new execution.

    Disabling new submissions still permits only sealed original native jobs to
    query their proven unchanged account. Workspace import locks remain enforced
    by the application's lifecycle and are never overridden here.
    """
    from app.services.worker.native_video_accounting import NativeVideoAccounting
    if reference_delivery_available is None:
        from app.config import settings
        reference_delivery_available=settings.workspace_id is None and settings.api_v1_prefix=='/api/v1'
    if not filename:return None,setup_projection()
    try:
        path=Path(filename).expanduser()
        if path.is_symlink() or path.resolve()!=path.absolute() or not path.is_file() or path.stat().st_size>16384:raise ValueError()
        with path.open('rb') as stream:raw=stream.read(16385)
        if len(raw)>16384:raise ValueError()
        config=VideoRuntimeConfiguration.model_validate_json(raw)
    except Exception:
        return None,setup_projection(issue_code='invalid_configuration',message='视频服务配置文件无效，请核对本地模板。')
    if config.model_id is None:
        return None,setup_projection(config,issue_code='managed_model_required',message='请选择模型库中明确配置的原生视频模型；旧环境凭据与手填费用不授予新执行权限。')
    accountant=NativeVideoAccounting(factory,config,price_registry,trusted_local_host=trusted_local_host,
        trusted_local_port=trusted_local_port,transport_factory=transport_factory,
        trusted_local_media_origin=trusted_local_media_origin,reference_delivery_available=reference_delivery_available)
    policies={};ready=False;quote=None;issue='disabled';message='新生成已关闭；只允许原任务使用未变化的原账号继续查询。'
    if config.enabled:
        try:
            async with factory() as db:_connection,quote,_profile,policy=await accountant.current(db)
            policies={MODEL_ID:policy};ready=True;issue='configured';message='托管模型与完整项目费用已登记；只有明确确认后才接受新任务。'
        except HTTPException:
            issue='managed_connection_or_price_required';message='模型连接或原生视频受信价格需要核对；新生成关闭，原任务只读控制保留。'
        except (ValueError,TypeError):
            issue='invalid_credentials_or_profile';message='执行服务配置版本无效，新生成保持关闭。'
    runtime=ExecutionRuntime(DurableExecution(None,factory=factory,native_accounting=accountant),policies,
        allow_submission=ready,allow_original_control=True)
    view=setup_projection(config,issue_code=issue,message=message,credential_present=ready)
    view.update(managed_model_id=config.model_id,price_fingerprint=quote.fingerprint if quote else None,
        quote_minor=quote.max_call_minor if quote else None,quote_note=quote.source_note if quote else '',original_control_available=True)
    if await accountant.reference_model_declared():
        video=bool(ready and 'video' in policy.capability.reference_kinds)
        view.update(reference_protocol=REFERENCE_PROTOCOL,reference_kinds=['image','video'] if video else ['image'],
            max_references=4 if video else 3,reference_video_delivery_available=video,
            output_description='明确选择参考图片、首帧或首尾帧；素材参考最多三张图片，交付入口就绪后可加一个参考视频。4–30整数秒、竖屏480p、MP4、无生成音频、保留水印。素材在接受任务前实际解码核对。')
    return runtime,view


async def read_project_budget(db, project_id: str) -> ProjectBudgetRead:
    """Read current accounting with explicit unsettled/unknown holds and no external requests."""
    await project_read_snapshot(db)
    await project_exists(db, project_id)
    bill=await billing_summary(db,project_id)
    model_entries=await db.scalar(select(func.count()).select_from(ModelCallCharge).where(ModelCallCharge.project_id==project_id))
    owned = ExecutionBudgetEntry.project_id == project_id
    pending = select(func.count()).select_from(ExecutionBudgetEntry).where(owned, ExecutionBudgetEntry.state.in_({'reserved', 'unknown'})).scalar_subquery()
    unknown = select(func.count()).select_from(ExecutionBudgetEntry).where(owned, ExecutionBudgetEntry.state == 'unknown').scalar_subquery()
    result = (await db.execute(select(ExecutionBudget, pending, unknown).where(ExecutionBudget.project_id == project_id))).first()
    if result is None:
        return ProjectBudgetRead(project_id=project_id, exists=False, currency='CNY', limit_minor=0, reserved_minor=0,
                                 spent_minor=0, available_minor=0, revision=0, unsettled_jobs=0, uncertain_jobs=0)
    row, unsettled_jobs, uncertain_jobs = result
    public_bill={**bill,**{key:str(bill[key]) for key in ('bill_receipt_total_minor','observed_pending_bill_minor','unfunded_bill_minor',
        'bill_correction_debit_minor','bill_correction_credit_minor','bill_net_total_minor')}}
    return ProjectBudgetRead(project_id=project_id, exists=True, currency=row.currency,
                             limit_minor=row.limit_minor, reserved_minor=row.reserved_minor, spent_minor=row.spent_minor,
                             available_minor=row.limit_minor-row.reserved_minor-row.spent_minor, revision=row.revision,
                             available_after_bills_minor=str(row.limit_minor-row.reserved_minor-row.spent_minor-bill['unfunded_bill_minor']),
                             unsettled_jobs=unsettled_jobs, uncertain_jobs=uncertain_jobs,
                             coverage='project_ledger' if model_entries else 'durable_video',**public_bill)


async def save_project_budget(factory, project_id: str, body: ProjectBudgetWrite, *, actor_id: str | None = None) -> ProjectBudgetRead:
    """CAS only the ceiling; held/settled funds cannot be erased by lowering or resetting it."""
    async with factory() as db:
        async with transaction(db):
            await project_exists(db, project_id)
            row = await db.get(ExecutionBudget, project_id)
            previous_limit = row.limit_minor if row else None
            if row is None:
                if body.expected_revision != 0:
                    raise HTTPException(409, '预算尚未登记，请刷新后保存。')
                db.add(ExecutionBudget(project_id=project_id, currency='CNY', limit_minor=body.limit_minor, revision=1))
                await db.flush()
            else:
                if row.currency != 'CNY':
                    raise HTTPException(409, '当前服务仅支持人民币预算，不能改写既有币种。')
                if row.revision != body.expected_revision:
                    raise HTTPException(409, '预算已更新，请刷新后重新确认。')
                bill=await billing_summary(db,project_id)
                if body.limit_minor < row.reserved_minor + row.spent_minor + bill['unfunded_bill_minor']:
                    raise HTTPException(409, '预算不能低于已预留、已结算及已导入待补足账单金额；未确认的费用仍保留。')
                result = await db.execute(update(ExecutionBudget).where(
                    ExecutionBudget.project_id == project_id, ExecutionBudget.revision == body.expected_revision
                ).values(limit_minor=body.limit_minor, revision=ExecutionBudget.revision+1).execution_options(synchronize_session=False))
                if result.rowcount != 1:
                    raise HTTPException(409, '预算发生并发更新，请刷新后重试。')
                db.expire(row)
            record_budget_change(db, project_id, actor_id, previous_limit, body.limit_minor, body.expected_revision+1)
            await db.flush()
            result = await read_project_budget(db, project_id)
        return result


async def preview_executable_plan(db, project_id: str, plan_id: str, runtime) -> dict:
    """Preview the actual opted-in binding while leaving the original preparation-only API unchanged."""
    from app.services.shot_plan.service import owned_plan, validate_current
    from app.services.shot_plan.compiler import public_policy
    plan = await owned_plan(db, project_id, plan_id)
    problems, entries = [], []
    model_id = plan.payload.get('document', {}).get('model_id')
    policy = runtime.policies.get(model_id) if runtime else None
    binding = plan.payload.get('binding')
    if model_id != MODEL_ID or policy is None or binding != public_policy(policy) or policy.quote_minor is None:
        problems.append({'code': 'executable_binding_required', 'message': '此计划未绑定本机当前可执行配置；请保留原准备计划，保存新修订并重新编译。'})
    try:
        await validate_current(db, plan)
    except HTTPException as error:
        problems.append({'code': 'plan_stale', 'message': str(error.detail)})
    enabled = bool(runtime and runtime.allow_submission and runtime.running)
    if not enabled:
        problems.append({'code': 'runtime_off', 'message': '本机生成服务尚未启用，预览不会启动它。'})
    reference=bool(policy and policy.capability.reference_protocol==REFERENCE_PROTOCOL)
    for entry in plan.payload.get('entries', []):
        body, summary, entry_issues = None, None, []
        if not problems:
            try:
                if reference:
                    from app.services.worker.execution_admission import freeze_request
                    snapshot,_digest=freeze_request(entry['request'],policy)
                    selected=snapshot_reference_selection(snapshot)
                    summary={'input_protocol':REFERENCE_PROTOCOL,'model':MODEL_ID,'duration':snapshot['duration'],
                        'video_reference':selected.model_dump(mode='json'),'resolution':'480p',
                        'ratio':'9:16' if selected.mode=='reference' else 'adaptive (9:16 input required)',
                        'generate_audio':False,'watermark':True,
                        'note':'仅核对计划与素材记录；接受任务前实际解码。实际发送内容含图片或一次性视频交付地址，因此不在预览中生成。'}
                else:body = build_seedance25_body(entry['request'])
            except (KeyError, TypeError, ValueError, HTTPException):
                entry_issues.append({'code': 'unsupported_wire_input', 'message': '此镜头不符合当前生成服务的输入规格。', 'shot_id': entry['shot_id']})
        entries.append({'shot_id': entry['shot_id'], 'entry_hash': entry['entry_hash'], 'body': body,
            'request_summary':summary,'input_validation':'metadata_only' if reference else None,'issues': entry_issues})
    usable = not problems and not any(item['issues'] for item in entries)
    wire_hash = hashlib.sha256(json.dumps([item['body'] for item in entries], ensure_ascii=False, sort_keys=True, separators=(',', ':')).encode()).hexdigest() if usable and not reference else None
    summary_hash=hashlib.sha256(json.dumps([item['request_summary'] for item in entries],ensure_ascii=False,sort_keys=True,separators=(',',':')).encode()).hexdigest() if usable and reference else None
    return {'plan_id': plan.id, 'content_hash': plan.content_hash, 'wire_hash': wire_hash, 'can_prepare': usable,
            'summary_hash':summary_hash,'can_submit': False, 'execution_enabled': enabled, 'model_qualified': False, 'issues': problems, 'entries': entries}
