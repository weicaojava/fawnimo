"""Managed native video admission, original-connection dispatch and common billing authority."""
import uuid

from fastapi import HTTPException
from sqlalchemy import select,update

from app.core.contracts.execution_binding import binding_fingerprint
from app.core.contracts.execution_capabilities import Capability
from app.core.contracts.seedance25 import MODEL_ID,SUPPORTED_DURATIONS,Seedance25Configuration
from app.core.integrations.volcengine.seedance25 import Seedance25DurableTransport
from app.core.integrations.volcengine.seedance25_payload import build_seedance25_body
from app.models.execution import ExecutionJob,ExecutionSlot,SubmissionAttempt
from app.models.native_video_charge import NativeVideoChargeBatch
from app.models.shot_plan import PlanJobLink
from app.models.task_links import GenerationTaskLink
from app.services.shot_plan.compiler import media_evidence
from app.services.shot_plan.admission import entry_for,request_identity,validate_send_locked
from app.services.shot_plan.service import owned_plan,validate_current,runtime_issues
from app.services.studio.production import project_exists
from app.services.worker.database_operation import joined_database
from app.services.worker.execution_admission import AdmissionPolicy,_insert_reserved_job,freeze_request
from app.services.worker.execution_budget import BudgetError,transaction
from app.services.worker.model_charges import actor,prepare_call,record_accepted_call
from app.services.worker.model_job_charges import binding_digest
from app.services.worker.native_video_charges import bind_new_jobs,job_charge,verify_plan
from app.services.worker.native_video_models import material,original_material,safe_binding


def identity(project_id,submitted_by,selection,price_fingerprint,*,confirmed=True):
    """Recover an exact accepted user action before current balances, prices or editable source revisions."""
    actor(submitted_by)
    return binding_digest({'domain':'native-video-project-request-v1','project_id':project_id,'submitted_by':submitted_by,
        'selection':selection,'price_fingerprint':price_fingerprint,'confirm_project_charges':confirmed})


async def replay(db,project_id,request_key,request_hash):
    """An exact original request may recover its original jobs; it can never take over legacy unlinked history."""
    prior=await db.scalar(select(NativeVideoChargeBatch).where(NativeVideoChargeBatch.project_id==project_id,
        NativeVideoChargeBatch.request_key==request_key).execution_options(populate_existing=True))
    if prior is None:return None
    if prior.request_hash!=request_hash:raise HTTPException(409,'原请求已绑定其他内容、提交人或费用版本')
    await verify_plan(db,prior.id)
    return {'batch_id':prior.id,'job_ids':[unit['job_id'] for unit in prior.context['units']]}


@joined_database
async def recover_request(factory,project_id,request_key,request_hash):
    """Close one stable read snapshot through cancellation; concurrent real bill reconciliation cannot produce a mixed recovery view."""
    async with factory() as db:
        return await replay(db,project_id,request_key,request_hash)


class NativeVideoAccounting:
    """Use the managed model library and shared trusted quote registry; no startup credential is cached."""
    def __init__(self,factory,config,price_registry,*,trusted_local_host=None,trusted_local_port=None,transport_factory=Seedance25DurableTransport):
        """Trusted application composition supplies the only network exception and transport implementation."""
        self.factory=factory;self.config=config;self.registry=price_registry;self.transport_factory=transport_factory
        self.network={'trusted_local_host':trusted_local_host,'trusted_local_port':trusted_local_port}
        self.allow_new_post=False

    def transport_config(self,connection,quote,secret):
        """Construct the fixed service profile from current trusted server settings and the original account quote."""
        options={**self.network,'base_url':connection['base_url']}
        if self.network['trusted_local_host'] is not None:options['download_hosts']=frozenset({self.network['trusted_local_host']})
        return Seedance25Configuration(api_key=secret,service_version=self.config.service_version,account_version=quote.account_version,**options)

    def profile_and_policy(self,connection,quote):
        """Bind the entire service, selected connection, original quote and iteration limit without a key digest."""
        profile=self.transport_config(connection,quote,'inert-profile-placeholder')
        binding_id='native:'+binding_digest({'connection':connection,'profile_hash':profile.profile_hash,'price':quote.fingerprint})
        policy=AdmissionPolicy(Capability(MODEL_ID,SUPPORTED_DURATIONS,frozenset(),0),quote.max_call_minor,'CNY',self.config.iteration_limit,binding_id)
        safe={'schema':'native-managed-v1','service_version':self.config.service_version,'account_version':quote.account_version,
            'profile_hash':profile.profile_hash,'iteration_limit':self.config.iteration_limit,'quote_fingerprint':quote.fingerprint,
            'execution_binding_hash':binding_fingerprint(policy)}
        return safe,policy

    async def current(self,db,expected=None):
        """Use one coherent key-presence projection and the explicit native protocol quote for this exact saved model."""
        connection=await safe_binding(db,self.config.model_id,**self.network)
        quote=self.registry.required('native_video',self.config.model_id) if expected is None else self.registry.confirmed('native_video',self.config.model_id,expected)
        if (quote.model_id,quote.model_name,quote.provider_id,quote.provider_connection_version)!=(connection['model_id'],connection['model_name'],connection['provider_id'],connection['provider_connection_version']):
            raise HTTPException(503,'原生视频实际模型或连接版本与受信报价不符，未接受调用')
        profile,policy=self.profile_and_policy(connection,quote)
        return connection,quote,profile,policy

    async def admit(self,project_id,request_key,selection,*,submitted_by,confirmed,price_fingerprint,runtime):
        """Prepare file proof outside writers; atomically create all selected actual jobs and their sole project holds."""
        if not isinstance(request_key,str) or not request_key.strip() or len(request_key)>128:raise HTTPException(422,'请求标识须为1至128字符')
        request_hash=identity(project_id,submitted_by,selection,price_fingerprint,confirmed=confirmed)
        proof=None
        old=await recover_request(self.factory,project_id,request_key,request_hash)
        if old:return old
        if selection.get('kind')=='plan':
            async with self.factory() as db:
                plan=await owned_plan(db,project_id,selection['plan_id']);_,proof=await validate_current(db,plan)
        return await self._admit_locked(project_id,request_key,selection,submitted_by,confirmed,price_fingerprint,request_hash,proof,runtime)

    @joined_database
    async def _admit_locked(self,project_id,key,selection,submitted_by,confirmed,expected,request_hash,proof,runtime):
        """Serialize exact replay, current full quote, all jobs, their existing holds and final plan sealing."""
        try:
            async with self.factory() as db:
                async with transaction(db):
                    await project_exists(db,project_id)
                    old=await replay(db,project_id,key,request_hash)
                    if old:return old
                    if confirmed is not True:raise HTTPException(422,'请明确确认本次原生视频完整项目预留费用')
                    if runtime is None or not runtime.allow_submission or not runtime.running or runtime.worker.factory is not self.factory:
                        raise HTTPException(503,'原生视频执行服务尚未启用，未创建任务')
                    connection,quote,profile,policy=await self.current(db,expected)
                    if runtime.policies.get(MODEL_ID)!=policy:raise HTTPException(409,'执行配置已变化，请重新核对当前计划')
                    entries=[];plan=None
                    if selection.get('kind')=='plan':
                        plan=await owned_plan(db,project_id,selection['plan_id'])
                        ids=selection['shot_ids']
                        if not isinstance(ids,list) or not 1<=len(ids)<=64 or len(set(ids))!=len(ids):raise HTTPException(422,'请选择1至64个不重复镜头')
                        if runtime_issues(plan.payload,runtime):raise HTTPException(409,'执行计划与当前原生视频配置不符')
                        await validate_current(db,plan,verified=proof)
                        for shot_id in ids:
                            entry=entry_for(plan,shot_id);entries.append((shot_id,entry['request'],request_identity(plan,entry),entry))
                    elif selection.get('kind')=='direct':
                        from app.models.script_preparation import ScriptDraft
                        if await db.scalar(select(ScriptDraft.id).where(ScriptDraft.project_id==project_id).limit(1)):
                            raise HTTPException(409,'本项目已启用结构化剧本，请从分镜工作室提交镜头计划')
                        frozen,source_hash=freeze_request(selection['snapshot'],policy)
                        entries=[('video',frozen,source_hash,None)]
                    else:raise HTTPException(422,'原生视频提交范围无效')
                    jobs=[]
                    for unit,snapshot,source_hash,entry in entries:
                        build_seedance25_body(snapshot)
                        async def references(session,project,frozen):
                            """Recheck only proven immutable media metadata under the short writer lock."""
                            for reference in frozen.get('references',[]):await media_evidence(session,project,reference['media_id'],(proof or {}).get(reference['media_id'],{}))
                        job_key='native:'+binding_digest({'key':key,'unit':unit})
                        job_id=await _insert_reserved_job(db,project_id,job_key,snapshot,source_hash,policy,reference_validator=references)
                        if plan is not None:
                            db.add(PlanJobLink(job_id=job_id,project_id=project_id,plan_id=plan.id,shot_id=unit,entry_hash=entry['entry_hash']))
                            job=await db.get(ExecutionJob,job_id)
                            db.add(GenerationTaskLink(task_id=job.task_id,resource_type='video',relation_type='video',relation_entity_id=unit))
                        await db.flush();jobs.append((job_id,unit))
                    batch=await bind_new_jobs(db,project_id=project_id,submitted_by=submitted_by,request_key=key,request_hash=request_hash,
                        connection=connection,profile=profile,selection=selection,jobs=jobs,quote=quote)
                    return {'batch_id':batch.id,'job_ids':[job_id for job_id,_unit in jobs]}
        except BudgetError:raise HTTPException(409,'本项目预算不足或预留冲突，整批任务未创建') from None

    @joined_database
    async def prepare(self,job,token,proof,clock):
        """Fence the actual job/slot and entire original batch, capture the coherent key, then commit unknown before HTTP."""
        async with self.factory() as db:
            async with transaction(db):
                if not self.allow_new_post:raise HTTPException(503,'新原生视频提交当前关闭；只允许原任务控制读取')
                current=await db.get(ExecutionJob,job.id);slot=await db.get(ExecutionSlot,1)
                if current is None or current.state!='queued' or current.cancel_requested or current.lease_token!=token or current.lease_until<=clock() or slot is None or slot.job_id!=job.id or slot.token!=token or slot.lease_until<=clock():
                    raise HTTPException(409,'原生视频任务租约或取消状态已变化，未发送请求')
                link=await job_charge(db,job.id,required=True)
                batch,calls=await verify_plan(db,link.batch_id,review_bills=True)
                await validate_send_locked(db,job.id,proof)
                if batch.context['selection'].get('kind')=='plan':
                    for unit in batch.context['units']:
                        await validate_send_locked(db,unit['job_id'],proof)
                original=next(call.quote for call in calls if call.unit_key==link.unit_key)
                connection,quote,profile,_policy=await self.current(db,original.fingerprint)
                if connection!=batch.context['connection'] or profile!=batch.context['profile'] or quote!=original:
                    raise HTTPException(409,'原生视频原连接或服务配置已变化，未向新账号发送')
                captured,secret=await material(db,connection['model_id'],**self.network)
                if captured!=connection:raise HTTPException(409,'原生视频连接在发送前发生变化')
                build_seedance25_body(current.snapshot)
                await prepare_call(db,project_id=job.project_id,charge_id=job.id,binding_hash=binding_digest(connection))
                attempt_id=str(uuid.uuid4())
                db.add(SubmissionAttempt(id=attempt_id,job_id=job.id,request_hash=job.request_hash,state='prepared'))
                await db.execute(update(ExecutionJob).where(ExecutionJob.id==job.id,ExecutionJob.lease_token==token).values(state='submitting',started_at=clock()))
            return {'attempt_id':attempt_id,'connection':connection,'quote':quote,'secret':secret}

    @joined_database
    async def control_material(self,job_id):
        """A safe original-account read does not need today's quote; rotation never borrows the replacement key."""
        async with self.factory() as db:
            link=await job_charge(db,job_id,required=True);batch,calls=await verify_plan(db,link.batch_id)
            original=next(call.quote for call in calls if call.unit_key==link.unit_key)
            connection,secret=await original_material(db,batch.context['connection'],**self.network)
            profile,_policy=self.profile_and_policy(connection,original)
            if connection!=batch.context['connection'] or any(profile[field]!=batch.context['profile'].get(field) for field in ('schema','service_version','account_version','profile_hash')):
                raise HTTPException(409,'原账号连接或执行配置已变化，原任务保持暂停核对')
            return {'connection':connection,'quote':original,'secret':secret}

    def transport(self,descriptor,job_id,attempt_id=None):
        """Create a short-lived fixed transport only after the joined DB descriptor has safely reached this caller."""
        config=self.transport_config(descriptor['connection'],descriptor['quote'],descriptor['secret'])
        descriptor['secret']=''
        async def on_response(request_id):
            """Retain only a private sanitized real POST response header, separately from the task handle."""
            if request_id is not None:await self.record_response(job_id,attempt_id,request_id)
        return self.transport_factory(config,on_submission_response=on_response if attempt_id else None)

    @joined_database
    async def record_response(self,job_id,attempt_id,request_id):
        """Late original response evidence may advance only its prepared charge, never business state or balances."""
        async with self.factory() as db:
            async with transaction(db):
                link=await job_charge(db,job_id,required=True)
                attempt=await db.get(SubmissionAttempt,attempt_id)
                if attempt is None or attempt.job_id!=job_id or attempt.request_hash!=link.request_hash:raise HTTPException(409,'原生视频响应与原尝试不符')
                await record_accepted_call(db,project_id=link.project_id,charge_id=job_id,provider_request_id=request_id)
