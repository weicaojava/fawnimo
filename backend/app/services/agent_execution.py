"""Persistent, sequential Agent execution with CAS ownership and explicit retry semantics."""
import asyncio
from datetime import datetime, timedelta, timezone
import hashlib
import json
from urllib.parse import urlsplit
import uuid

from fastapi import HTTPException
from sqlalchemy import func, select, update

from app.bootstrap import bootstrap_all_registries
from app.core.contracts.agent_execution import AgentExecutionAvailability, AgentNodeRunRead, AgentRunCreate, AgentRunRead, AgentRunRetry, ExecutionModelChoice, ModelConnectionTestCreate
from app.core.integrations.agent_text import AgentModelError, invoke_agent_text
from app.models.agent_execution import AgentNodeRun, AgentRun
from app.models.llm import Model, ModelSettings, Provider
from app.schemas.agent_config import AgentConfigCreate
from app.services.agent_config import draft_of, owned_agent
from app.services.llm.provider_registry import get_provider_spec, resolve_provider_key_from_name
from app.services.llm.provider_resolver import resolve_provider_config_by_model

TERMINAL = {'succeeded', 'failed', 'cancelled'}
INTERRUPTED = '执行中断，模型请求结果可能未知；不会自动重发，显式重试将从头开始且可能重复计费'


def clock():
    """Store UTC without offset, matching mapped DateTime on SQLite and PostgreSQL."""
    return datetime.now(timezone.utc).replace(tzinfo=None)


def digest(document: dict) -> str:
    """Detect idempotency-key reuse with different input without storing credentials."""
    return hashlib.sha256(json.dumps(document, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def sampler(params: dict) -> dict:
    """Whitelist inert compatible sampling options; reject malformed values before HTTP."""
    result = {}
    for key in ('frequency_penalty', 'presence_penalty'):
        if key in params:
            value = params[key]
            if isinstance(value, bool) or not isinstance(value, (float, int)) or not -2 <= value <= 2:
                raise HTTPException(422, f'模型参数 {key} 必须在 -2 到 2 之间')
            result[key] = value
    if 'seed' in params:
        if isinstance(params['seed'], bool) or not isinstance(params['seed'], int) or abs(params['seed']) > 2**53:
            raise HTTPException(422, '模型 seed 参数无效')
        result['seed'] = params['seed']
    if 'stop' in params:
        value = params['stop']
        values = [value] if isinstance(value, str) else value
        if not isinstance(values, list) or not 1 <= len(values) <= 4 or any(not isinstance(item, str) or not 1 <= len(item) <= 64 for item in values):
            raise HTTPException(422, '模型 stop 参数无效')
        result['stop'] = value
    return result


async def availability(db, runtime, model_id: str | None = None) -> AgentExecutionAvailability:
    """Inspect key presence in SQL without loading existing credential values into the process."""
    bootstrap_all_registries()
    rows = (await db.execute(select(Model.id, Model.name, Provider.name.label('provider_name'), Provider.status, (func.length(func.trim(Provider.api_key)) > 0).label('has_key')).join(Provider, Provider.id == Model.provider_id).where(Model.category == 'text').order_by(Model.name, Model.id).limit(1000))).all()
    choices = []
    for row in rows:
        configured = bool(row.has_key) and row.status != 'disabled'
        message = '已配置文本连接；尚未测试真实响应' if configured else '供应商未启用或未填写密钥'
        try:
            spec = get_provider_spec(resolve_provider_key_from_name(row.provider_name))
            if 'text' not in spec.supported_categories:
                configured, message = False, '此供应商未提供受支持的文本适配器'
        except HTTPException:
            configured, message = False, '此供应商未提供受支持的文本适配器'
        choices.append(ExecutionModelChoice(id=row.id, name=row.name, provider_name=row.provider_name, configured=configured, message=message))
    setting = await db.get(ModelSettings, 1)
    default_id = setting.default_text_model_id if setting else None
    chosen = next((row for row in choices if row.id == (model_id or default_id)), None)
    running = bool(runtime and runtime.running)
    if not running:
        message = 'Agent 文本执行服务尚未启动；配置仍可编辑与保存'
    elif chosen is None:
        message = '请先选择已配置的文本模型；尚未发起模型请求'
    elif not chosen.configured:
        message = chosen.message + '；尚未发起模型请求'
    else:
        message = '可提交文本工作流；执行会真实调用所选模型，可能产生费用'
    return AgentExecutionAvailability(available=running and chosen is not None and chosen.configured, message=message, default_model_id=default_id, models=choices)


async def safe_binding(db, model_id: str) -> dict:
    """Snapshot stable connection metadata and supported parameters, never API credentials."""
    model = await db.get(Model, model_id)
    if model is None or model.category != 'text':
        raise HTTPException(422, '请选择已配置的文本模型')
    config = await resolve_provider_config_by_model(db, model=model)
    parsed = urlsplit(config.base_url or '')
    if not parsed.hostname or parsed.scheme not in {'http', 'https'} or parsed.query or parsed.fragment or parsed.username or parsed.password:
        raise HTTPException(422, '文本模型地址无效；请检查供应商基础地址')
    return {'model_id': model.id, 'model_name': model.name, 'provider_id': model.provider_id, 'base_url': config.base_url, 'params': sampler(model.params or {})}


async def get_run(db, run_id: str) -> AgentRun:
    """Load one persistent attempt or return a domain not-found error."""
    row = await db.get(AgentRun, run_id)
    if row is None:
        raise HTTPException(404, 'Agent 执行记录不存在')
    return row


async def run_read(db, row: AgentRun) -> AgentRunRead:
    """Attach ordered durable node state without leaking leases or connection credentials."""
    nodes = (await db.scalars(select(AgentNodeRun).where(AgentNodeRun.run_id == row.id).order_by(AgentNodeRun.position))).all()
    return AgentRunRead.model_validate(row).model_copy(update={'nodes': [AgentNodeRunRead.model_validate(node) for node in nodes]})


async def existing_request(db, scope: str, request_key: str, request_hash: str):
    """Return the original attempt for identical submissions and reject changed payloads."""
    row = await db.scalar(select(AgentRun).where(AgentRun.scope_key == scope, AgentRun.request_key == request_key))
    if row is not None and row.request_hash != request_hash:
        raise HTTPException(409, '请求标识已用于不同输入；请使用新的请求标识')
    return row


async def insert_run(db, *, scope: str, request_key: str, request_hash: str, kind: str, config: dict, agent_id: str | None, agent_name: str, agent_version: int, binding: dict, input_text: str, max_tokens: int, attempt: int = 1, retry_of: str | None = None):
    """Commit-safe admission and node skeletons precede all potentially billable network work."""
    prior = await existing_request(db, scope, request_key, request_hash)
    if prior:
        return prior
    if await db.scalar(select(func.count()).select_from(AgentRun).where(AgentRun.status.in_(['queued', 'running']))) >= 20:
        raise HTTPException(409, '待处理任务过多，请完成或取消后再提交')
    # SQLite legacy transaction mode may release an outermost SAVEPOINT early. A native
    # conflict-safe INSERT keeps admission and nodes in the request's real transaction.
    dialect = db.get_bind().dialect.name
    if dialect == 'sqlite':
        from sqlalchemy.dialects.sqlite import insert
    elif dialect == 'postgresql':
        from sqlalchemy.dialects.postgresql import insert
    else:
        raise HTTPException(503, 'Agent 执行当前支持 SQLite 与 PostgreSQL 数据库')
    run_id = uuid.uuid4().hex
    values = dict(id=run_id, kind=kind, scope_key=scope, request_key=request_key, request_hash=request_hash, agent_id=agent_id, agent_name=agent_name, agent_version=agent_version, config_snapshot=config, model_id=binding['model_id'], model_name=binding['model_name'], model_binding=binding, status='queued', input_text=input_text, output_text='', error='', max_tokens=max_tokens, attempt=attempt, retry_of=retry_of, created_at=clock())
    inserted = await db.scalar(insert(AgentRun).values(**values).on_conflict_do_nothing(index_elements=['scope_key', 'request_key']).returning(AgentRun.id))
    if inserted is None:
        prior = await existing_request(db, scope, request_key, request_hash)
        if prior:
            return prior
        raise HTTPException(409, '提交发生并发冲突，请使用原请求标识重新查询') from None
    for position, node in enumerate(config['workflow']):
        db.add(AgentNodeRun(run_id=run_id, node_id=node['id'], position=position, label=node['label'], type=node['type'], status='pending', input_text='', output_text='', prompt='', parameters={}, usage={}, error=''))
    await db.flush()
    row = await db.get(AgentRun, run_id)
    return row


async def create_run(db, runtime, agent_id: str, body: AgentRunCreate):
    """Admit the exact saved Agent version as a bounded ordered text-only workflow."""
    scope = 'agent:' + agent_id
    fingerprint = digest(body.model_dump())
    prior = await existing_request(db, scope, body.request_key, fingerprint)
    if prior:
        return prior
    agent = await owned_agent(db, agent_id)
    if agent.version != body.expected_version:
        raise HTTPException(409, 'Agent 已修改，请保存或刷新后再执行')
    config = draft_of(agent).model_dump(mode='json')
    count = sum(node['type'] == 'llm' for node in config['workflow'])
    if not 1 <= count <= 8:
        raise HTTPException(422, '执行工作流须包含 1 至 8 个顺序 LLM 节点；配置编辑仍支持更多节点')
    state = await availability(db, runtime, body.model_id)
    if not state.available:
        raise HTTPException(503, state.message)
    binding = await safe_binding(db, body.model_id or state.default_model_id)
    return await insert_run(db, scope=scope, request_key=body.request_key, request_hash=fingerprint, kind='workflow', config=config, agent_id=agent_id, agent_name=agent.name, agent_version=agent.version, binding=binding, input_text=body.input_text, max_tokens=body.max_tokens)


async def create_connection_test(db, runtime, model_id: str, body: ModelConnectionTestCreate):
    """Use the same durable HTTP execution path for a fixed single eight-token request."""
    scope = 'model-test:' + model_id
    fingerprint = digest({'model_id': model_id, **body.model_dump()})
    prior = await existing_request(db, scope, body.request_key, fingerprint)
    if prior:
        return prior
    state = await availability(db, runtime, model_id)
    if not state.available:
        raise HTTPException(503, state.message)
    binding = await safe_binding(db, model_id)
    config = AgentConfigCreate(name='文本连接测试', type='other', prompt='Respond with OK only. This checks text HTTP connectivity, not model quality.').model_dump(mode='json')
    config['workflow'][1].update({'temperature': 0, 'top_p': 1, 'prompt': 'Respond with OK only.'})
    return await insert_run(db, scope=scope, request_key=body.request_key, request_hash=fingerprint, kind='connection_test', config=config, agent_id=None, agent_name='文本连接测试', agent_version=1, binding=binding, input_text='ping', max_tokens=8)


async def retry_run(db, runtime, run_id: str, body: AgentRunRetry):
    """Start a new explicit full attempt from a frozen snapshot; never silently replay uncertain work."""
    original = await get_run(db, run_id)
    if original.status not in {'failed', 'cancelled'}:
        raise HTTPException(409, '只有失败或取消的任务可显式重试')
    scope = 'retry:' + run_id
    fingerprint = digest(body.model_dump())
    prior = await existing_request(db, scope, body.request_key, fingerprint)
    if prior:
        return prior
    state = await availability(db, runtime, original.model_id)
    if not state.available:
        raise HTTPException(503, state.message)
    binding = await safe_binding(db, original.model_id)
    if binding != original.model_binding:
        raise HTTPException(409, '模型配置已改变，请从 Agent 页面新建执行；旧任务不会按新配置重发')
    return await insert_run(db, scope=scope, request_key=body.request_key, request_hash=fingerprint, kind=original.kind, config=original.config_snapshot, agent_id=original.agent_id, agent_name=original.agent_name, agent_version=original.agent_version, binding=binding, input_text=original.input_text, max_tokens=original.max_tokens, attempt=original.attempt + 1, retry_of=original.id)


async def cancel_run(db, run_id: str):
    """Make cancellation durable first, so any worker must discard a late provider result."""
    row = await get_run(db, run_id)
    if row.status in TERMINAL:
        return row
    await db.execute(update(AgentRun).execution_options(synchronize_session=False).where(AgentRun.id == run_id, AgentRun.status.in_(['queued', 'running'])).values(status='cancelled', error='已取消；已发出的模型请求仍可能计费，结果不再写入', finished_at=clock(), lease_token=None, lease_expires_at=None))
    await db.execute(update(AgentNodeRun).execution_options(synchronize_session=False).where(AgentNodeRun.run_id == run_id, AgentNodeRun.status == 'running').values(status='cancelled', error='执行已取消，未采用迟到结果', finished_at=clock()))
    await db.execute(update(AgentNodeRun).execution_options(synchronize_session=False).where(AgentNodeRun.run_id == run_id, AgentNodeRun.status == 'pending').values(status='skipped', error='任务已取消，未执行', finished_at=clock()))
    await db.refresh(row)
    return row


class AgentExecutionRuntime:
    """Poll durable work using a CAS lease; abandoned in-flight calls fail instead of replaying."""
    def __init__(self, factory, *, poll_interval: float = .25, trusted_local_host: str | None = None, trusted_local_ports: frozenset[int] = frozenset()):
        """Inject sessions and a fixture-only exact loopback trust exception independently of models."""
        self.factory, self.poll_interval = factory, poll_interval
        self.trusted_local_host, self.trusted_local_ports = trusted_local_host, trusted_local_ports
        self.running = False
        self.task = None

    async def start(self):
        """Start the scanner without reading provider credentials or inventing any task."""
        if self.running:
            return
        self.running = True
        self.task = asyncio.create_task(self._loop())

    async def stop(self):
        """Cancel real HTTP work and record an interrupted outcome before returning."""
        self.running = False
        if self.task:
            self.task.cancel()
            try:
                await self.task
            except asyncio.CancelledError:
                pass
            self.task = None

    async def _loop(self):
        """Sequentially dispatch one locally claimed attempt; concurrent runtimes cannot share it."""
        while self.running:
            try:
                await self.tick()
            except asyncio.CancelledError:
                raise
            except Exception:
                # A scanner/database error does not authorize retrying any HTTP request.
                pass
            await asyncio.sleep(self.poll_interval)

    async def tick(self):
        """Expire dead leases as unknown, then atomically claim one queued task."""
        async with self.factory() as db:
            expired = (await db.scalars(select(AgentRun.id).where(AgentRun.status == 'running', AgentRun.lease_expires_at < clock()))).all()
            for run_id in expired:
                result = await db.execute(update(AgentRun).execution_options(synchronize_session=False).where(AgentRun.id == run_id, AgentRun.status == 'running', AgentRun.lease_expires_at < clock()).values(status='failed', error=INTERRUPTED, finished_at=clock(), lease_token=None, lease_expires_at=None))
                if result.rowcount:
                    await db.execute(update(AgentNodeRun).execution_options(synchronize_session=False).where(AgentNodeRun.run_id == run_id, AgentNodeRun.status.in_(['pending', 'running'])).values(status='failed', error=INTERRUPTED, finished_at=clock()))
            candidate = await db.scalar(select(AgentRun.id).where(AgentRun.status == 'queued').order_by(AgentRun.created_at, AgentRun.id).limit(1))
            token = uuid.uuid4().hex
            claimed = False
            if candidate:
                result = await db.execute(update(AgentRun).execution_options(synchronize_session=False).where(AgentRun.id == candidate, AgentRun.status == 'queued').values(status='running', lease_token=token, lease_expires_at=clock() + timedelta(seconds=75), started_at=clock()))
                claimed = bool(result.rowcount)
            await db.commit()
        if claimed:
            await self._execute(candidate, token)

    async def _owned(self, db, run_id, token):
        """Load only the current owner's running attempt; cancelled or expired work cannot commit."""
        return await db.scalar(select(AgentRun).where(AgentRun.id == run_id, AgentRun.status == 'running', AgentRun.lease_token == token, AgentRun.lease_expires_at > clock()))

    async def _call(self, run_id, token, **kwargs):
        """Watch persistent cancellation while HTTP is in flight, killing its network subprocess."""
        async with self.factory() as db:
            if await self._owned(db, run_id, token) is None:
                raise asyncio.CancelledError()
        call = asyncio.create_task(invoke_agent_text(trusted_local_host=self.trusted_local_host, trusted_local_ports=self.trusted_local_ports, **kwargs))
        try:
            while True:
                done, _ = await asyncio.wait({call}, timeout=.1)
                if done:
                    return await call
                async with self.factory() as db:
                    if await self._owned(db, run_id, token) is None:
                        raise asyncio.CancelledError()
        finally:
            if not call.done():
                call.cancel()
            try:
                await call
            except (asyncio.CancelledError, Exception):
                pass

    async def _fail(self, run_id, token, error):
        """Record an honest failure only while retaining the same execution lease."""
        async with self.factory() as db:
            result = await db.execute(update(AgentRun).execution_options(synchronize_session=False).where(AgentRun.id == run_id, AgentRun.status == 'running', AgentRun.lease_token == token, AgentRun.lease_expires_at > clock()).values(status='failed', error=error, finished_at=clock(), lease_token=None, lease_expires_at=None))
            if result.rowcount:
                await db.execute(update(AgentNodeRun).execution_options(synchronize_session=False).where(AgentNodeRun.run_id == run_id, AgentNodeRun.status == 'running').values(status='failed', error=error, finished_at=clock()))
                await db.execute(update(AgentNodeRun).execution_options(synchronize_session=False).where(AgentNodeRun.run_id == run_id, AgentNodeRun.status == 'pending').values(status='skipped', error='前序节点失败，未执行', finished_at=clock()))
            await db.commit()

    async def _execute(self, run_id, token):
        """Execute ordered start/LLM/end nodes, committing dependency inputs before each request."""
        try:
            async with self.factory() as db:
                run = await self._owned(db, run_id, token)
                if run is None:
                    return
                config, previous = run.config_snapshot, run.input_text
                node_ids = (await db.scalars(select(AgentNodeRun.id).where(AgentNodeRun.run_id == run_id).order_by(AgentNodeRun.position))).all()
            for node_id in node_ids:
                async with self.factory() as db:
                    run = await self._owned(db, run_id, token)
                    if run is None:
                        return
                    # Acquiring the write lease before changing a node serializes it with cancellation.
                    ownership = await db.execute(update(AgentRun).execution_options(synchronize_session=False).where(AgentRun.id == run_id, AgentRun.status == 'running', AgentRun.lease_token == token, AgentRun.lease_expires_at > clock()).values(lease_expires_at=clock() + timedelta(seconds=75)))
                    if not ownership.rowcount:
                        return
                    node = await db.get(AgentNodeRun, node_id)
                    spec = config['workflow'][node.position]
                    system = '\n\n'.join(part for part in [config.get('prompt', ''), spec.get('prompt', ''), 'Agent extraction parameters (use as task constraints): ' + json.dumps(config.get('parameters', {}), ensure_ascii=False)] if part)
                    parameters = {'temperature': spec.get('temperature', config.get('temperature', .7)), 'top_p': spec.get('top_p', config.get('top_p', .9)), 'max_tokens': run.max_tokens}
                    node.status, node.input_text, node.prompt, node.parameters, node.started_at = 'running', previous, system if node.type == 'llm' else '', parameters if node.type == 'llm' else {}, clock()
                    # Keep the concrete attempted node even if its queued model binding is now invalid.
                    await db.commit()
                    if node.type == 'llm':
                        binding = await safe_binding(db, run.model_id)
                        if binding != run.model_binding:
                            raise AgentModelError('模型配置在排队后改变；未重定向请求，请新建执行')
                        model = await db.get(Model, run.model_id)
                        provider = await db.get(Provider, model.provider_id)
                        secret = provider.api_key
                        setting = await db.get(ModelSettings, 1)
                        timeout = max(1, min(60, setting.api_timeout if setting else 30))
                    await db.commit()
                if spec['type'] == 'llm':
                    response = await self._call(run_id, token, base_url=binding['base_url'], api_key=secret, model=binding['model_name'], system=system, input_text=previous, model_params=binding['params'], timeout=timeout, **parameters)
                    output, usage = response.text, response.usage
                    secret = ''
                else:
                    output, usage = previous, {}
                async with self.factory() as db:
                    owner = select(AgentRun.id).where(AgentRun.id == run_id, AgentRun.status == 'running', AgentRun.lease_token == token, AgentRun.lease_expires_at > clock()).exists()
                    result = await db.execute(update(AgentNodeRun).execution_options(synchronize_session=False).where(AgentNodeRun.id == node_id, owner).values(status='succeeded', output_text=output, usage=usage, finished_at=clock()))
                    if not result.rowcount:
                        return
                    await db.commit()
                previous = output
            async with self.factory() as db:
                await db.execute(update(AgentRun).execution_options(synchronize_session=False).where(AgentRun.id == run_id, AgentRun.status == 'running', AgentRun.lease_token == token, AgentRun.lease_expires_at > clock()).values(status='succeeded', output_text=previous, finished_at=clock(), lease_token=None, lease_expires_at=None))
                await db.commit()
        except asyncio.CancelledError:
            await self._fail(run_id, token, INTERRUPTED)
            if not self.running:
                raise
        except AgentModelError as failure:
            await self._fail(run_id, token, str(failure))
        except HTTPException:
            await self._fail(run_id, token, '模型配置不可用；未采用任何模拟响应，请检查供应商与文本模型配置')
        except Exception:
            await self._fail(run_id, token, '执行失败或结果未知；不会自动重试，显式重试可能重复计费')
