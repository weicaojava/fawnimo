"""Optional URL ingestion has a server-owned allowlist and never forwards credentials."""
from fastapi import HTTPException
from app.config import settings
from app.core.integrations.trusted_http import request_isolated_async
from app.services.studio.production import project_exists
from app.services.studio.production_media import store_batch


async def import_video_url(db,project_id,url):
    """Download bounded MP4 only from explicitly configured public hosts."""
    await project_exists(db,project_id)
    hosts={host.strip().lower() for host in settings.reverse_media_allowed_hosts.split(',') if host.strip()}
    if not hosts:raise HTTPException(503,'尚未配置可信视频下载域名，请使用本地文件导入')
    try:
        response=await request_isolated_async(url,allowed_hosts=hosts,max_bytes=settings.production_upload_limit,timeout=30)
        if response.status!=200:raise ValueError()
    except Exception:raise HTTPException(400,'视频下载失败或地址不受信，请使用本地文件导入') from None
    rows=await store_batch(db,project_id,[('source-url.mp4',response.body)])
    return rows[0].id
