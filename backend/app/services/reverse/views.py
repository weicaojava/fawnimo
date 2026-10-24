"""Project-scoped technical evidence views and fixed artifact identities."""
from pathlib import Path
from fastapi import HTTPException
from sqlalchemy import select
from app.models.reverse import ReverseAnalysis
from app.services.studio.production_media import media_path,scoped_media


async def owned_analysis(db,project_id,analysis_id):
    """Resolve an owned job and verify the immutable original before serving evidence."""
    row=(await db.execute(select(ReverseAnalysis).where(ReverseAnalysis.id==analysis_id,ReverseAnalysis.project_id==project_id))).scalar_one_or_none()
    if row is None:raise HTTPException(404,'本工程视频分析不存在')
    media=await scoped_media(db,project_id,row.media_id)
    if media.sha256!=row.source_hash:raise HTTPException(409,'原视频校验失败')
    return row


def analysis_view(row, *, include_manifest=True):
    """Expose evidence without the server's private artifact directory or lease token."""
    return {'id':row.id,'media_id':row.media_id,'state':row.state,'source_hash':row.source_hash,'revision':row.revision,'manifest':row.manifest if include_manifest else None,'confirmed_cuts':row.confirmed_cuts,'reason':row.reason}


def artifact_path(row,name):
    """Only recorded relative files in this analysis attempt may be served."""
    if row.state not in {'ready','degraded'} or not row.artifact_dir:raise HTTPException(409,'分析尚未完成')
    prefix=f'_reverse/{row.id}/'
    if not row.artifact_dir.startswith(prefix):raise HTTPException(409,'分析产物归属无效')
    directory=media_path(row.artifact_dir)
    target=(directory/name).resolve()
    if not target.is_relative_to(directory) or not target.is_file():raise HTTPException(409,'分析产物缺失或路径无效，请重新分析')
    return target
