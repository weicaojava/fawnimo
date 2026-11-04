"""Managed native video admission, original-connection dispatch and common billing authority."""
import asyncio
import uuid

from fastapi import HTTPException
from sqlalchemy import select,update

from app.core.contracts.execution_binding import binding_fingerprint
from app.core.contracts.execution_capabilities import Capability
from app.core.contracts.seedance25 import MODEL_ID,SUPPORTED_DURATIONS,Seedance25Configuration
from app.core.contracts.seedance_reference_profile import SeedanceReferenceConfiguration
from app.core.contracts.seedance_references import REFERENCE_PROTOCOL,snapshot_reference_selection
from app.core.integrations.volcengine.seedance25 import Seedance25DurableTransport
from app.core.integrations.volcengine.seedance_reference import SeedanceReferenceTransport
from app.core.integrations.volcengine.seedance_reference_payload import build_seedance_reference_body
from app.core.integrations.volcengine.seedance25_payload import build_seedance25_body
from app.core.contracts.wan30 import Wan30Configuration,PROTOCOLS as WAN_PROTOCOLS,REFERENCE_PROTOCOL as WAN_REFERENCE_PROTOCOL
from app.core.contracts.native_video_profiles import declared_profile,reference_protocol
from app.core.integrations.wan30 import Wan30DurableTransport
from app.core.integrations.wan30_payload import build_wan30_body
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
from app.services.worker.video_reference_inputs import (
    NativeReferenceInputError,install_video_input_set,prepare_video_inputs,prepare_saved_video_inputs,
    recheck_video_inputs,saved_video_input_set,saved_fields,
)
from app.services.worker.reference_delivery import prepare_reference_deliveries,install_reference_deliveries


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
    def __init__(self,factory,config,price_registry,*,trusted_local_host=None,trusted_local_port=None,transport_factory=Seedance25DurableTransport,
                 trusted_local_media_origin=None,reference_delivery_available=False,reference_delivery_scope=None):
        """Trusted application composition supplies the only network exception and transport implementation."""
        self.factory=factory;self.config=config;self.registry=price_registry;self.transport_factory=transport_factory
        self.network={'trusted_local_host':trusted_local_host,'trusted_local_port':trusted_local_port}
        if type(reference_delivery_available) is not bool:raise ValueError('Explicit delivery availability required')
        self.reference_delivery_available=reference_delivery_available
        if reference_delivery_scope is not None:
            from app.core.contracts.workspace_reference_delivery import WorkspaceReferencePolicy
            if not isinstance(reference_delivery_scope,WorkspaceReferencePolicy) or not reference_delivery_available:
                raise ValueError('An enabled fixed reference delivery scope is required')
            if trusted_local_media_origin is not None and trusted_local_media_origin!=reference_delivery_scope.trusted_local_origin:
                raise ValueError('Independent reference fixture origins differ')
            trusted_local_media_origin=reference_delivery_scope.trusted_local_origin
        self.reference_delivery_scope=reference_delivery_scope
        self.trusted_local_media_origin=trusted_local_media_origin
        self._reference_lane=asyncio.Lock()
        from app.services.worker.reference_delivery import ReferenceMediaDelivery
        self.reference_delivery=ReferenceMediaDelivery(factory) if reference_delivery_available else None
        self.allow_new_post=False

    def transport_config(self,connection,quote,secret):
        """Construct the fixed service profile from current trusted server settings and the original account quote."""
        options={**self.network,'base_url':connection['base_url']}
        if self.network['trusted_local_host'] is not None:options['download_hosts']=frozenset({self.network['trusted_local_host']})
        if connection.get('protocol') in WAN_PROTOCOLS:
            return Wan30Configuration(api_key=secret,service_version=self.config.service_version,account_version=quote.account_version,
                protocol=connection['protocol'],**options)
        if connection.get('protocol')==REFERENCE_PROTOCOL:
            origin=getattr(self.config,'reference_media_origin',None)
            if origin is not None and not self.reference_delivery_available:
                raise HTTPException(503,'当前入口没有接通参考视频素材交付；请先移除该交付配置，仍可准备参考图片')
            if origin is not None and self.reference_delivery_scope is not None and origin!=self.reference_delivery_scope.origin:
                raise HTTPException(503,'参考视频交付地址与当前身份网关不一致，未接受生成')
            try:
                media_fixture_origin=self.trusted_local_media_origin
                if self.reference_delivery_scope is not None and origin is None:media_fixture_origin=None
                return SeedanceReferenceConfiguration(api_key=secret,service_version=self.config.service_version,account_version=quote.account_version,
                    input_delivery_origin=origin,trusted_local_media_origin=media_fixture_origin,
                    delivery_workspace_id=self.reference_delivery_scope.workspace_id if origin is not None and self.reference_delivery_scope is not None else None,**options)
            except ValueError:
                raise HTTPException(503,'参考生成的独立素材交付配置无效，未接受调用') from None
        return Seedance25Configuration(api_key=secret,service_version=self.config.service_version,account_version=quote.account_version,**options)

    def profile_and_policy(self,connection,quote):
        """Bind the entire service, selected connection, original quote and iteration limit without a key digest."""
        profile=self.transport_config(connection,quote,'inert-profile-placeholder')
        declared=declared_profile(connection.get('model_name'),connection.get('protocol'))
        if declared is None:raise HTTPException(503,'原生视频模型协议未登记，未接受调用')
        binding_id='native:'+binding_digest({'connection':connection,'profile_hash':profile.profile_hash,'price':quote.fingerprint})
        input_protocol=reference_protocol(connection.get('protocol'))
        if input_protocol is not None:
            video=declared.supports_reference_video and getattr(profile,'input_delivery_origin',None) is not None
            capability=Capability(declared.model_name,declared.durations,frozenset({'image','video'} if video else {'image'}),4 if video else 3,input_protocol)
        else:capability=Capability(declared.model_name,declared.durations,frozenset(),0)
        policy=AdmissionPolicy(capability,quote.max_call_minor,'CNY',self.config.iteration_limit,binding_id)
        safe={'schema':'native-managed-v1','service_version':self.config.service_version,'account_version':quote.account_version,
            'profile_hash':profile.profile_hash,'iteration_limit':self.config.iteration_limit,'quote_fingerprint':quote.fingerprint,
            'execution_binding_hash':binding_fingerprint(policy)}
        return safe,policy

    async def current(self,db,expected=None):
        """Use one coherent key-presence projection and the explicit native protocol quote for this exact saved model."""
        connection=await safe_binding(db,self.config.model_id,**self.network)
        input_protocol=reference_protocol(connection.get('protocol'))
        price_options={'input_protocol':input_protocol} if input_protocol is not None else {}
        quote=self.registry.required('native_video',self.config.model_id,**price_options) if expected is None else self.registry.confirmed('native_video',self.config.model_id,expected,**price_options)
        if (quote.model_id,quote.model_name,quote.provider_id,quote.provider_connection_version)!=(connection['model_id'],connection['model_name'],connection['provider_id'],connection['provider_connection_version']):
            raise HTTPException(503,'原生视频实际模型或连接版本与受信报价不符，未接受调用')
        profile,policy=self.profile_and_policy(connection,quote)
        return connection,quote,profile,policy

    @joined_database
    async def reference_model_declared(self):
        """Inspect only saved model capability JSON, without reading provider key material or changing legacy error precedence."""
        from app.models.llm import Model
        async with self.factory() as db:
            params=await db.scalar(select(Model.params).where(Model.id==self.config.model_id,Model.category=='video'))
        return isinstance(params,dict) and reference_protocol(params.get('native_video_protocol')) is not None

    @joined_database
    async def declared_model_info(self):
        """Read only the selected model's declared profile for disabled/unpriced setup views, without provider secrets or execution authority."""
        from app.models.llm import Model
        async with self.factory() as db:
            row=(await db.execute(select(Model.name,Model.params).where(Model.id==self.config.model_id,Model.category=='video'))).one_or_none()
        protocol=row.params.get('native_video_protocol') if row is not None and isinstance(row.params,dict) else None
        return(declared_profile(row.name,protocol),protocol) if row is not None else(None,None)

    async def admit(self,project_id,request_key,selection,*,submitted_by,confirmed,price_fingerprint,runtime):
        """Prepare file proof outside writers; atomically create all selected actual jobs and their sole project holds."""
        if not isinstance(request_key,str) or not request_key.strip() or len(request_key)>128:raise HTTPException(422,'请求标识须为1至128字符')
        request_hash=identity(project_id,submitted_by,selection,price_fingerprint,confirmed=confirmed)
        proof=None
        old=await recover_request(self.factory,project_id,request_key,request_hash)
        if old:return old
        if await self.reference_model_declared():
            return await self.admit_reference(project_id,request_key,selection,submitted_by,confirmed,price_fingerprint,request_hash,runtime)
        if selection.get('kind')=='plan':
            async with self.factory() as db:
                plan=await owned_plan(db,project_id,selection['plan_id']);_,proof=await validate_current(db,plan)
        return await self._admit_locked(project_id,request_key,selection,submitted_by,confirmed,price_fingerprint,request_hash,proof,runtime)

    async def admit_reference(self,project_id,key,selection,submitted_by,confirmed,expected,request_hash,runtime):
        """Prepare at most one reference shot outside writers, preserving exact original replay before this bounded lane."""
        if confirmed is not True:raise HTTPException(422,'请明确确认本次原生视频完整项目预留费用')
        if runtime is None or not runtime.allow_submission or not runtime.running or runtime.worker.factory is not self.factory:
            raise HTTPException(503,'原生视频执行服务尚未启用，未创建任务')
        if self._reference_lane.locked():
            raise HTTPException(429,'参考素材校验繁忙，请稍后核对原提交')
        async with self._reference_lane:
            proof=None
            async with self.factory() as db:
                connection,_quote,_profile,policy=await self.current(db,expected)
                if reference_protocol(connection['protocol']) is None:
                    raise HTTPException(409,'参考模型声明已变化，请重新核对')
                if selection.get('kind')=='plan':
                    ids=selection.get('shot_ids')
                    if not isinstance(ids,list) or len(ids)!=1 or not isinstance(ids[0],str):
                        raise HTTPException(422,'参考生成每次明确提交一个镜头')
                    plan=await owned_plan(db,project_id,selection.get('plan_id'))
                    _,proof=await validate_current(db,plan)
                    unit=ids[0];snapshot=entry_for(plan,unit)['request']
                elif selection.get('kind')=='direct':
                    unit='video';snapshot,_hash=freeze_request(selection.get('snapshot'),policy)
                else:raise HTTPException(422,'原生视频提交范围无效')
            try:selected=snapshot_reference_selection(snapshot)
            except (TypeError,ValueError):raise HTTPException(422,'请明确选择参考输入模式与原项目素材') from None
            if (snapshot.get('model_id')!=policy.capability.model_id or type(snapshot.get('duration')) is not int
                    or snapshot['duration'] not in policy.capability.durations):
                raise HTTPException(422,'参考输入与固定视频模型或时长不符')
            if len(selected.references)>policy.capability.max_references or any(item.kind not in policy.capability.reference_kinds for item in selected.references):
                raise HTTPException(503,'当前执行配置不支持这些参考素材；参考视频还需要独立素材交付入口')
            prepared=await prepare_video_inputs(self.factory,project_id,selected)
            if connection['protocol']==WAN_REFERENCE_PROTOCOL:
                try:build_wan30_body(snapshot,protocol=WAN_REFERENCE_PROTOCOL,project_id=project_id,
                    prepared=tuple(item.wire_proof for item in prepared.references))
                except (TypeError,ValueError):raise HTTPException(422,'万相参考图片不符合已声明档案，未预留费用或发起调用') from None
            return await self._admit_locked(project_id,key,selection,submitted_by,confirmed,expected,request_hash,proof,runtime,
                input_proofs={unit:prepared})

    @joined_database
    async def _admit_locked(self,project_id,key,selection,submitted_by,confirmed,expected,request_hash,proof,runtime,input_proofs=None):
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
                    if (reference_protocol(connection.get('protocol')) is not None)!=bool(input_proofs):
                        raise HTTPException(409,'视频输入协议已变化，请重新核对原提交')
                    if runtime.policies.get(policy.capability.model_id)!=policy:raise HTTPException(409,'执行配置已变化，请重新核对当前计划')
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
                        if input_proofs:
                            prepared=input_proofs.get(unit)
                            if prepared is None or prepared.project_id!=project_id or snapshot_reference_selection(snapshot)!=prepared.selection:
                                raise HTTPException(409,'参考素材预检与原镜头不符')
                            async def references(session,project,frozen):
                                """Recheck only previously decoded original bytes under this actual single-hold transaction."""
                                await recheck_video_inputs(session,prepared)
                        else:
                            if connection['protocol'] in WAN_PROTOCOLS:build_wan30_body(snapshot,protocol=connection['protocol'])
                            else:build_seedance25_body(snapshot)
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
                    if input_proofs:
                        for job_id,unit in jobs:
                            await install_video_input_set(db,await db.get(ExecutionJob,job_id),input_proofs[unit])
                    return {'batch_id':batch.id,'job_ids':[job_id for job_id,_unit in jobs]}
        except BudgetError:raise HTTPException(409,'本项目预算不足或预留冲突，整批任务未创建') from None

    async def prepare(self,job,token,proof,clock):
        """Decode exact saved conditions and validate the whole wire outside writer ownership before final charge preparation."""
        if 'video_reference' not in job.snapshot:
            return await self._prepare_locked(job,token,proof,clock)
        async with self._reference_lane:
            try:
                prepared=await prepare_saved_video_inputs(self.factory,job)
                if prepared is None:raise NativeReferenceInputError()
            except HTTPException:
                raise NativeReferenceInputError() from None
            async with self.factory() as db:
                connection,quote,_profile,_policy=await self.current(db)
            config=self.transport_config(connection,quote,'inert-profile-placeholder')
            if isinstance(config,Wan30Configuration):
                if config.protocol!=WAN_REFERENCE_PROTOCOL:raise NativeReferenceInputError()
                wire=tuple(item.wire_proof for item in prepared.references);intents=()
                try:build_wan30_body(job.snapshot,protocol=config.protocol,project_id=job.project_id,prepared=wire)
                except (TypeError,ValueError):raise NativeReferenceInputError() from None
                return await self._prepare_locked(job,token,proof,clock,prepared,wire,intents)
            if not isinstance(config,SeedanceReferenceConfiguration):
                raise HTTPException(409,'原参考模型声明已变化')
            wire,intents=prepare_reference_deliveries(prepared,job.id,config.input_delivery_origin,
                trusted_local_origin=config.trusted_local_media_origin,clock=clock,
                delivery_scope=self.reference_delivery_scope if config.delivery_workspace_id is not None else None)
            try:
                build_seedance_reference_body(project_id=job.project_id,model_id=job.snapshot['model_id'],
                    prompt=job.snapshot['prompt'],duration=job.snapshot['duration'],selection=prepared.selection,prepared=wire,
                    delivery_origin=config.input_delivery_origin,trusted_local_delivery_origin=config.trusted_local_media_origin,
                    delivery_workspace_id=config.delivery_workspace_id)
            except (TypeError,ValueError):raise NativeReferenceInputError() from None
            return await self._prepare_locked(job,token,proof,clock,prepared,wire,intents)

    @joined_database
    async def _prepare_locked(self,job,token,proof,clock,prepared=None,wire=None,intents=()):
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
                if reference_protocol(connection.get('protocol')) is not None:
                    if prepared is None or wire is None:raise NativeReferenceInputError()
                    try:
                        selected,fields=await saved_video_input_set(db,current)
                        if selected!=prepared.selection or fields!=[saved_fields(item.fields) for item in prepared.references]:
                            raise NativeReferenceInputError()
                        await recheck_video_inputs(db,prepared)
                    except HTTPException:raise NativeReferenceInputError() from None
                else:
                    if prepared is not None:raise HTTPException(409,'视频输入协议已变化')
                    if connection['protocol'] in WAN_PROTOCOLS:build_wan30_body(current.snapshot,protocol=connection['protocol'])
                    else:build_seedance25_body(current.snapshot)
                await prepare_call(db,project_id=job.project_id,charge_id=job.id,binding_hash=binding_digest(connection))
                attempt_id=str(uuid.uuid4())
                db.add(SubmissionAttempt(id=attempt_id,job_id=job.id,request_hash=job.request_hash,state='prepared'))
                await db.execute(update(ExecutionJob).where(ExecutionJob.id==job.id,ExecutionJob.lease_token==token).values(state='submitting',started_at=clock()))
                if intents:await install_reference_deliveries(db,current,attempt_id,intents)
            descriptor={'attempt_id':attempt_id,'connection':connection,'quote':quote,'secret':secret}
            if prepared is not None:descriptor.update(project_id=job.project_id,reference_selection=prepared.selection,wire_references=wire)
            return descriptor

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
            """Retain only a sanitized original POST response witness, separately from the task handle and business state."""
            if request_id is not None:await self.record_response(job_id,attempt_id,request_id)
        factory=self.transport_factory
        options={}
        protocol=descriptor['connection'].get('protocol')
        if protocol in WAN_PROTOCOLS and factory is Seedance25DurableTransport:factory=Wan30DurableTransport
        if reference_protocol(protocol) is not None:
            if protocol==REFERENCE_PROTOCOL and factory is Seedance25DurableTransport:factory=SeedanceReferenceTransport
            options={'project_id':descriptor.get('project_id'),'selection':descriptor.get('reference_selection'),
                'references':descriptor.get('wire_references')}
        return factory(config,on_submission_response=on_response if attempt_id else None,**options)

    @joined_database
    async def record_response(self,job_id,attempt_id,request_id):
        """Late original response evidence may advance only its prepared charge, never business state or balances."""
        async with self.factory() as db:
            async with transaction(db):
                link=await job_charge(db,job_id,required=True)
                attempt=await db.get(SubmissionAttempt,attempt_id)
                if attempt is None or attempt.job_id!=job_id or attempt.request_hash!=link.request_hash:raise HTTPException(409,'原生视频响应与原尝试不符')
                await record_accepted_call(db,project_id=link.project_id,charge_id=job_id,provider_request_id=request_id)
