"""Bounded uploads and all-or-nothing safe archive ingestion into project-owned media."""
import hashlib
import io
import json
import re
import stat
import uuid
import zipfile
import tempfile
import subprocess
import asyncio
from pathlib import Path, PurePosixPath
from fastapi import HTTPException, UploadFile
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from app.config import settings
from app.models.production import ProductionMedia
from app.models.studio import FileItem, Project


def safe_name(name: str) -> str:
    """Reject Windows/POSIX traversal, devices, streams, and ambiguous path aliases."""
    path=PurePosixPath(name)
    if not name or len(name)>200 or '\\' in name or ':' in name or '\x00' in name or path.is_absolute() or any(p in {'','.','..'} or p.endswith((' ','.')) or re.match(r'(?i)^(con|prn|aux|nul|com[0-9]|lpt[0-9])(?:\.|$)',p) for p in name.split('/')):
        raise HTTPException(400,'不安全的文件名或归档路径')
    return name


def inspect_media(name: str, data: bytes) -> str:
    """Allow a small binary media set; extensions and signatures must agree."""
    safe_name(name)
    ext=Path(name).suffix.lower()
    ok={'.png':('image/png',data.startswith(b'\x89PNG\r\n\x1a\n') and b'IHDR' in data[:32] and data.endswith(b'IEND\xaeB`\x82')),
        '.jpg':('image/jpeg',data.startswith(b'\xff\xd8\xff') and data.endswith(b'\xff\xd9')),
        '.jpeg':('image/jpeg',data.startswith(b'\xff\xd8\xff') and data.endswith(b'\xff\xd9')),
        '.webp':('image/webp',data.startswith(b'RIFF') and data[8:12]==b'WEBP'),
        '.mp4':('video/mp4',len(data)>32 and data[4:8]==b'ftyp' and b'mdat' in data),
        '.wav':('audio/wav',data.startswith(b'RIFF') and data[8:12]==b'WAVE' and b'data' in data)}
    if not data or ext not in ok or not ok[ext][1]:
        raise HTTPException(400,'空文件、不支持的格式或文件内容与后缀不符')
    return ok[ext][0]


def probe_media(name: str, data: bytes) -> None:
    """Decode a bounded sample in an isolated temporary file using trusted FFmpeg.

    Uploaded bytes cannot supply options, URLs or executable paths. Signature checks
    are preliminary; a missing decoder or failed decode prevents adoption.
    """
    executable=Path(settings.production_probe_executable)
    if not executable.is_file():raise HTTPException(503,'尚未配置本地媒体校验器')
    root=Path(settings.production_media_root).resolve();root.mkdir(parents=True,exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='validate-',dir=root) as folder:
        path=Path(folder)/('input'+Path(name).suffix.lower());path.write_bytes(data)
        format_name={'.png':'png_pipe','.jpg':'jpeg_pipe','.jpeg':'jpeg_pipe','.webp':'webp_pipe','.mp4':'mov','.wav':'wav'}[path.suffix]
        try:
            result=subprocess.run([str(executable),'-nostdin','-v','error','-max_alloc','67108864','-threads','1','-protocol_whitelist','file,pipe','-f',format_name,'-i',str(path),'-t','0.1','-f','null','-'],capture_output=True,timeout=15,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
        except (OSError,subprocess.TimeoutExpired) as exc:raise HTTPException(400,'媒体无法在限定时间内安全解码') from exc
        if result.returncode or result.stderr:raise HTTPException(400,'媒体内容无法解码')


async def read_bounded(file: UploadFile) -> bytes:
    """Read incrementally with an upper bound instead of trusting Content-Length."""
    chunks=[];size=0
    while chunk:=await file.read(1024*1024):
        size+=len(chunk)
        if size>settings.production_upload_limit:raise HTTPException(413,'文件超过上传上限')
        chunks.append(chunk)
    return b''.join(chunks)


def media_path(relative: str) -> Path:
    """Resolve only server-generated paths within the configured private media root."""
    root=Path(settings.production_media_root).resolve()
    target=(root/relative).resolve()
    if not target.is_relative_to(root):raise HTTPException(400,'媒体路径越界')
    return target


async def store_batch(db: AsyncSession, project_id: str, entries: list[tuple[str,bytes]]) -> list[ProductionMedia]:
    """Validate every entry before writing; rollback records and owned files on failure."""
    if await db.get(Project,project_id) is None:raise HTTPException(404,'工程不存在')
    verified=[(name,data,inspect_media(name,data)) for name,data in entries]
    for name,data,_mime in verified:await asyncio.to_thread(probe_media,name,data)
    paths=[];rows=[]
    try:
        for name,data,mime in verified:
            file_id=str(uuid.uuid4());relative=f'{hashlib.sha256(project_id.encode()).hexdigest()}/{file_id}{Path(name).suffix.lower()}'
            target=media_path(relative);target.parent.mkdir(parents=True,exist_ok=True)
            # Exclusive creation makes collisions or parallel uploads unable to replace bytes.
            with target.open('xb') as stream:stream.write(data)
            paths.append(target)
            item=FileItem(id=file_id,type=mime.split('/')[0],name=Path(name).name,thumbnail='',tags=['user_import'],storage_key='local-production/'+relative)
            db.add(item);await db.flush()
            row=ProductionMedia(id=file_id,project_id=project_id,name=Path(name).name,mime=mime,sha256=hashlib.sha256(data).hexdigest(),size=len(data),relative_path=relative)
            db.add(row);rows.append(row)
        await db.commit()
        return rows
    except Exception:
        await db.rollback()
        for path in paths:path.unlink(missing_ok=True)
        raise


async def import_archive(db: AsyncSession, project_id: str, data: bytes) -> list[ProductionMedia]:
    """Accept a media bundle v1, not an executable or arbitrary project restore package.

    Entire manifests and member metadata are checked before any extraction. Files
    are streamed into memory under total/per-entry bounds, then stored by ID.
    """
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            infos=archive.infolist()
            if not infos or len(infos)>65:raise HTTPException(400,'归档成员数量超限')
            names=set();total=0
            for info in infos:
                safe_name(info.filename)
                mode=info.external_attr>>16
                if info.is_dir() or (stat.S_IFMT(mode) not in (0,stat.S_IFREG)) or info.flag_bits&1:raise HTTPException(400,'不允许目录、链接、设备或加密归档')
                folded=info.filename.casefold()
                if folded in names:raise HTTPException(400,'重复归档路径')
                names.add(folded);total+=info.file_size
                if total>settings.production_upload_limit or info.file_size>settings.production_upload_limit or info.file_size>max(1,info.compress_size)*100:raise HTTPException(413,'归档解压大小或压缩比超限')
            if 'manifest.json' not in archive.namelist():raise HTTPException(400,'缺少媒体清单')
            if archive.getinfo('manifest.json').file_size>65536:raise HTTPException(400,'媒体清单过大')
            manifest=json.loads(archive.read('manifest.json'))
            if set(manifest)!={'schema_version','project_id','files'} or manifest['schema_version']!=1 or manifest['project_id']!=project_id:raise HTTPException(400,'清单版本或工程归属不符')
            files=manifest['files']
            if not isinstance(files,list) or not files or len(files)>64:raise HTTPException(400,'媒体清单无效')
            expected={'manifest.json'};entries=[]
            for item in files:
                if not isinstance(item,dict) or set(item)!={'path','sha256'}:raise HTTPException(400,'媒体项字段无效')
                name=safe_name(item['path'])
                if name in expected or name not in archive.namelist():raise HTTPException(400,'媒体项重复或缺失')
                expected.add(name);blob=archive.read(name)
                if hashlib.sha256(blob).hexdigest()!=item['sha256']:raise HTTPException(400,'媒体校验值不匹配')
                entries.append((name,blob))
            if set(archive.namelist())!=expected:raise HTTPException(400,'归档含有未声明的成员')
    except (zipfile.BadZipFile,ValueError,KeyError,TypeError,RuntimeError) as exc:
        raise HTTPException(400,'无法读取媒体归档') from exc
    return await store_batch(db,project_id,entries)


async def scoped_media(db: AsyncSession, project_id: str, media_id: str) -> ProductionMedia:
    """Check ownership and content hash before exposing or adopting a media file."""
    row=await db.get(ProductionMedia,media_id)
    if row is None or row.project_id!=project_id:raise HTTPException(404,'本工程媒体不存在')
    path=media_path(row.relative_path)
    if not path.is_file() or path.stat().st_size!=row.size or hashlib.sha256(path.read_bytes()).hexdigest()!=row.sha256:raise HTTPException(409,'媒体缺失或校验失败')
    return row
