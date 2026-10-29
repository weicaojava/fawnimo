"""Agent configuration CRUD and history; no provider or task execution lives here."""
from datetime import datetime, timezone
import uuid

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.agent_config import AgentConfig, AgentConfigRevision
from app.schemas.agent_config import AgentConfigCreate, AgentConfigExport, AgentConfigUpdate

CONFIG_FIELDS = tuple(AgentConfigCreate.model_fields)


def draft_of(agent: AgentConfig) -> AgentConfigCreate:
    """Serialize only editable fields, keeping metadata and private state out of exports."""
    return AgentConfigCreate.model_validate({name: getattr(agent, name) for name in CONFIG_FIELDS})


async def owned_agent(db: AsyncSession, agent_id: str) -> AgentConfig:
    """Return a stored local configuration or a readable not-found response."""
    agent = await db.get(AgentConfig, agent_id)
    if agent is None:
        raise HTTPException(404, 'Agent 配置不存在')
    return agent


async def record_revision(db: AsyncSession, agent: AgentConfig) -> None:
    """Keep a complete editable snapshot whenever the saved version changes."""
    db.add(AgentConfigRevision(agent_id=agent.id, version=agent.version, document=draft_of(agent).model_dump(mode='json'), saved_at=datetime.now(timezone.utc)))
    await db.flush()


async def clear_other_defaults(db: AsyncSession, *, type_key: str, except_id: str) -> None:
    """Maintain one default per type and record the changed peers in their own histories."""
    rows = (await db.scalars(select(AgentConfig).where(AgentConfig.type == type_key, AgentConfig.id != except_id, AgentConfig.is_default.is_(True)))).all()
    for row in rows:
        row.is_default = False
        row.version += 1
        row.updated_at = datetime.now(timezone.utc)
        await record_revision(db, row)


async def create_agent(db: AsyncSession, body: AgentConfigCreate) -> AgentConfig:
    """Create the exact validated draft and its first saved revision."""
    agent = AgentConfig(id=uuid.uuid4().hex, version=1, **body.model_dump(mode='json'))
    if agent.is_default:
        await clear_other_defaults(db, type_key=agent.type, except_id=agent.id)
    db.add(agent)
    await db.flush()
    await db.refresh(agent)
    await record_revision(db, agent)
    return agent


async def update_agent(db: AsyncSession, agent_id: str, body: AgentConfigUpdate) -> AgentConfig:
    """Apply explicit edits, validate the whole result and refuse stale or null updates."""
    agent = await owned_agent(db, agent_id)
    if body.expected_version is not None and body.expected_version != agent.version:
        raise HTTPException(409, '此 Agent 已被更新，请刷新后重试')
    changes = body.model_dump(exclude_unset=True, exclude={'expected_version'}, mode='json')
    if any(value is None for value in changes.values()):
        raise HTTPException(422, '配置字段不可为空；清空文本时请使用空字符串')
    validated = AgentConfigCreate.model_validate({**draft_of(agent).model_dump(mode='json'), **changes})
    if validated == draft_of(agent):
        return agent
    if validated.is_default:
        await clear_other_defaults(db, type_key=validated.type.value, except_id=agent.id)
    for key, value in validated.model_dump(mode='json').items():
        setattr(agent, key, value)
    agent.version += 1
    agent.updated_at = datetime.now(timezone.utc)
    await db.flush()
    await record_revision(db, agent)
    return agent


async def copy_agent(db: AsyncSession, agent_id: str) -> AgentConfig:
    """Create a new non-default copy without changing the source or running the workflow."""
    source = await owned_agent(db, agent_id)
    draft = draft_of(source).model_copy(update={'name': source.name[:250] + ' 副本', 'is_default': False})
    return await create_agent(db, draft)


async def import_agent(db: AsyncSession, body: AgentConfigExport) -> AgentConfig:
    """Import as a fresh identity and avoid silently replacing the current default."""
    return await create_agent(db, body.config.model_copy(update={'is_default': False}))
