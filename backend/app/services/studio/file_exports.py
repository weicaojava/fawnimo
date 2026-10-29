"""Build bounded local file archives using the same verified storage reads as downloads."""
from io import BytesIO
import re
import zipfile

from fastapi import HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from app.services.studio.files import read_file_bytes

MAX_EXPORT_BYTES = 32 * 1024 * 1024
MAX_EXPORT_FILES = 20


async def export_file_bundle(db: AsyncSession, file_ids: list[str]) -> bytes:
    """Validate every selected file before returning a complete, path-safe ZIP archive."""
    if not file_ids or len(file_ids) > MAX_EXPORT_FILES:
        raise HTTPException(422, '每次请选择 1 至 20 个文件')
    ids = list(dict.fromkeys(file_ids))
    buffer = BytesIO()
    total = 0
    with zipfile.ZipFile(buffer, 'w', compression=zipfile.ZIP_STORED) as archive:
        for index, file_id in enumerate(ids, 1):
            row, data = await read_file_bytes(db, file_id=file_id)
            total += len(data)
            if total > MAX_EXPORT_BYTES:
                raise HTTPException(413, '批量导出总大小不得超过 32 MiB，请减少文件数量')
            name = re.sub(r'[\\/:*?"<>|\x00-\x1f]', '_', row.name).strip('. ')[:160] or row.id
            archive.writestr(f'{index:02d}-{name}', data)
    return buffer.getvalue()
