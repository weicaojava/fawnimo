"""Webhook authentication/response handling only; public requirement persistence lives in its isolated service."""
import asyncio
import re
import sqlite3

from fastapi import APIRouter, HTTPException, Request
from app.core.contracts.community_requests import CommunityDeliveryReceipt
from app.schemas.common import ApiResponse, success_response
from app.services.community.events import (MAX_DELIVERY_BYTES, InvalidCommunityEvent,
    delivery_identity, project_public_issue, verify_github_signature)
from app.services.community.store import CommunityStoreConflict

router = APIRouter(prefix='/community', tags=['community-intake'])


def single_header(request, name):
    """Require one actual header value so duplicated authentication or event identities cannot be interpreted differently."""
    values = request.headers.getlist(name)
    if len(values) != 1:
        raise HTTPException(400, '事件标识或签名头无效')
    return values[0]


@router.post('/github/events', response_model=ApiResponse[CommunityDeliveryReceipt])
async def receive_public_github_event(request: Request):
    """Verify the original bounded bytes, then acknowledge only after atomic source/delivery persistence."""
    config = request.app.state.community_configuration
    if not config.enabled:
        raise HTTPException(503, '社区需求接入尚未启用')
    signature = single_header(request, 'x-hub-signature-256')
    if re.fullmatch(r'sha256=[0-9a-f]{64}', signature) is None:
        raise HTTPException(403, '事件签名未通过')
    event = single_header(request, 'x-github-event')
    if re.fullmatch(r'[a-z_]{1,64}', event) is None:
        raise HTTPException(400, '事件类型无效')
    try:
        delivery = delivery_identity(single_header(request, 'x-github-delivery'))
    except InvalidCommunityEvent:
        raise HTTPException(400, '事件标识无效') from None
    content_type = request.headers.get('content-type', '').split(';', 1)[0].strip().casefold()
    if content_type != 'application/json' or request.headers.get('content-encoding'):
        raise HTTPException(415, '仅接受原始JSON事件')
    content = bytearray()
    async for chunk in request.stream():
        if len(content) + len(chunk) > MAX_DELIVERY_BYTES:
            raise HTTPException(413, '事件内容超过接入上限')
        content.extend(chunk)
    raw = bytes(content)
    if not verify_github_signature(request.app.state.community_webhook_secret, signature, raw):
        raise HTTPException(403, '事件签名未通过')
    try:
        action, version = project_public_issue(raw, event, config)
        result = await asyncio.to_thread(request.app.state.community_store.ingest, delivery, raw, event, action, version)
    except InvalidCommunityEvent:
        raise HTTPException(400, '事件来源或公开需求内容无效') from None
    except (CommunityStoreConflict, sqlite3.IntegrityError):
        raise HTTPException(409, '事件或来源版本已有冲突，原需求记录已保留') from None
    except sqlite3.Error:
        raise HTTPException(503, '需求登记暂时不可用，请使用原投递标识重试') from None
    return success_response(result)
