"""Studio 实体图片 CRUD。"""

from __future__ import annotations

from typing import Any

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.utils import apply_order, paginate
from app.models.studio import CharacterImage
from app.services.common import entity_not_found
from app.services.studio.entity_specs import entity_spec, normalize_entity_type

IMAGE_ORDER_FIELDS = {"id", "quality_level", "view_angle", "created_at", "updated_at"}


async def list_entity_images_paginated(
    db: AsyncSession,
    *,
    entity_type: str,
    entity_id: str,
    order: str | None,
    is_desc: bool,
    page: int,
    page_size: int,
) -> tuple[list[dict[str, Any]], int]:
    spec = entity_spec(entity_type)
    parent = await db.get(spec.model, entity_id)
    if parent is None:
        raise HTTPException(status_code=404, detail=entity_not_found(spec.model.__name__))

    id_field = getattr(spec.image_model, spec.id_field)
    stmt = select(spec.image_model).where(id_field == entity_id)
    stmt = apply_order(
        stmt,
        model=spec.image_model,
        order=order,
        is_desc=is_desc,
        allow_fields=IMAGE_ORDER_FIELDS,
        default="id",
    )
    items, total = await paginate(db, stmt=stmt, page=page, page_size=page_size)
    payload = [spec.image_read_model.model_validate(x).model_dump() for x in items]
    return payload, total


async def create_entity_image(
    db: AsyncSession,
    *,
    entity_type: str,
    entity_id: str,
    body: dict[str, Any],
) -> dict[str, Any]:
    """Atomically ensure identity-only empty slots, while preserving explicit image creation conflicts.

    StrictMode, multiple tabs and concurrent requests may discover the same missing
    slot. Only a raw body containing angle/quality identity is an ensure intent;
    file, dimensions, primary, format or any other supplied field is not reusable.
    """
    entity_type_norm = normalize_entity_type(entity_type)
    spec = entity_spec(entity_type_norm)
    parent = await db.get(spec.model, entity_id)
    if parent is None:
        raise HTTPException(status_code=404, detail=entity_not_found(spec.model.__name__))

    parsed = spec.image_create_model.model_validate(body).model_dump()
    identity_only = set(body).issubset({'quality_level', 'view_angle'})
    created_new = True
    dialect = db.get_bind().dialect.name
    if dialect in {'sqlite', 'postgresql'}:
        if dialect == 'sqlite':
            from sqlalchemy.dialects.sqlite import insert
        else:
            from sqlalchemy.dialects.postgresql import insert
        statement = insert(spec.image_model).values(**{spec.id_field: entity_id, **parsed}).on_conflict_do_nothing(index_elements=[spec.id_field, 'quality_level', 'view_angle']).returning(spec.image_model.id)
        identifier = await db.scalar(statement)
        if identifier is None:
            if not identity_only:
                raise HTTPException(409, '该视角与精度的图片槽位已存在；已有图片保持不变，请明确编辑原槽位')
            created_new = False
            obj = await db.scalar(select(spec.image_model).where(getattr(spec.image_model, spec.id_field) == entity_id, spec.image_model.quality_level == parsed['quality_level'], spec.image_model.view_angle == parsed['view_angle']))
            if obj is None:
                raise HTTPException(409, '图片槽位在初始化期间发生变化，请刷新后重新确认')
        else:
            obj = await db.get(spec.image_model, identifier)
    else:
        # Preserve legacy behavior on other dialects; the supported workspace uses SQLite.
        obj = spec.image_model(**{spec.id_field: entity_id, **parsed})
        db.add(obj)
        await db.flush()
        await db.refresh(obj)

    if created_new and entity_type_norm == "character" and getattr(obj, "is_primary", False):
        stmt = (
            CharacterImage.__table__.update()
            .where(CharacterImage.character_id == entity_id, CharacterImage.id != obj.id)
            .values(is_primary=False)
        )
        await db.execute(stmt)
        await db.refresh(obj)

    return spec.image_read_model.model_validate(obj).model_dump()


async def update_entity_image(
    db: AsyncSession,
    *,
    entity_type: str,
    entity_id: str,
    image_id: int,
    body: dict[str, Any],
) -> dict[str, Any]:
    entity_type_norm = normalize_entity_type(entity_type)
    spec = entity_spec(entity_type_norm)
    obj = await db.get(spec.image_model, image_id)
    if obj is None or getattr(obj, spec.id_field) != entity_id:
        raise HTTPException(status_code=404, detail=entity_not_found(spec.image_model.__name__))

    update_data = spec.image_update_model.model_validate(body).model_dump(exclude_unset=True)
    for key, value in update_data.items():
        setattr(obj, key, value)
    await db.flush()
    await db.refresh(obj)

    if entity_type_norm == "character" and update_data.get("is_primary") is True:
        stmt = (
            CharacterImage.__table__.update()
            .where(CharacterImage.character_id == entity_id, CharacterImage.id != obj.id)
            .values(is_primary=False)
        )
        await db.execute(stmt)
        await db.refresh(obj)

    return spec.image_read_model.model_validate(obj).model_dump()


async def delete_entity_image(
    db: AsyncSession,
    *,
    entity_type: str,
    entity_id: str,
    image_id: int,
) -> None:
    spec = entity_spec(entity_type)
    obj = await db.get(spec.image_model, image_id)
    if obj is None or getattr(obj, spec.id_field) != entity_id:
        return
    await db.delete(obj)
    await db.flush()
