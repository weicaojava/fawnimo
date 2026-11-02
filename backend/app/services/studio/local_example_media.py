"""Render and verify original example files before opening any business writer transaction."""
import asyncio
import hashlib
import json
import os
from pathlib import Path
import re
import sys
import tempfile

from fastapi import HTTPException
from PIL import Image

from app.config import settings
from app.core.media_environment import media_environment
from app.core.owned_process import run_owned_process
from app.services.editing.media import stat_identity as editing_stat
from app.services.shot_plan.compiler import stat_identity as compiler_stat
from app.services.studio.demo_media_child import TEMPLATE, unique_object
from app.services.studio.production_media import inspect_media, media_path

MAX_FILE = 8 * 1024 * 1024


async def file_operation(function, *arguments):
    """Join a private file thread even under repeated cancellation before releasing the render lane."""
    task = asyncio.create_task(asyncio.to_thread(function, *arguments))
    cancelled = None
    while not task.done():
        try:
            await asyncio.shield(task)
        except asyncio.CancelledError as error:
            cancelled = error
    result = task.result()
    if cancelled is not None:
        raise cancelled
    return result


def resource_id(request_id, kind, index):
    """Derive opaque server-owned identities from one fixed reservation and resource position."""
    return hashlib.sha256(f'{request_id}:{kind}:{index}'.encode()).hexdigest()[:32]


def regular_file(path):
    """Refuse symbolic/reparse files before reading or linking private original media."""
    item = path.lstat()
    if path.is_symlink() or getattr(item, 'st_file_attributes', 0) & 0x400 or not path.is_file():
        raise ValueError('Redirected original example file')
    return item


def verify_bytes(path, size, checksum):
    """Verify bounded complete immutable bytes and capture changes during the read."""
    before = compiler_stat(path)
    if regular_file(path).st_size != size or not 0 < size <= MAX_FILE:
        raise ValueError('Original example file size changed')
    blob = path.read_bytes()
    if len(blob) != size or hashlib.sha256(blob).hexdigest() != checksum or compiler_stat(path) != before:
        raise ValueError('Original example bytes changed')
    return blob


def verify_picture(path, size, checksum):
    """Fully decode original PNGs rather than accepting only their extension or signature."""
    blob = verify_bytes(path, size, checksum)
    if inspect_media(path.name, blob) != 'image/png':
        raise ValueError('Original picture format changed')
    with Image.open(path) as image:
        if image.format != 'PNG' or image.size != (360, 640):
            raise ValueError('Original picture profile changed')
        image.load()


def publish_file(source, relative, size, checksum):
    """Link a fully written same-filesystem nonce exclusively; existing final bytes are verified, never repaired."""
    target = media_path(relative)
    target.parent.mkdir(parents=True, exist_ok=True)
    root = Path(settings.production_media_root).resolve()
    for parent in (target.parent, target.parent.parent):
        if not parent.is_relative_to(root) or parent.is_symlink() or getattr(parent.lstat(), 'st_file_attributes', 0) & 0x400:
            raise ValueError('Redirected original example directory')
    verify_bytes(source, size, checksum)
    try:
        # Rendering already closed and joined its root. This private completed
        # file is the nonce: linking never exposes a partially copied final file.
        os.link(source, target)
    except FileExistsError:
        verify_bytes(target, size, checksum)
    verify_bytes(target, size, checksum)
    return target


def validated_manifest(result):
    """Accept only the fixed helper's bounded original-geometry protocol, with exact seven clip identities."""
    if not isinstance(result, dict) or set(result) != {'schema_version','template_version','clips','external_model_calls','provenance','license','model_quality_qualified'}:
        raise ValueError('Invalid original example manifest')
    if type(result['schema_version']) is not int or result['schema_version'] != 1 or result['template_version'] != TEMPLATE:
        raise ValueError('Original template version changed')
    if type(result['external_model_calls']) is not int or result['external_model_calls'] != 0 or result['model_quality_qualified'] is not False:
        raise ValueError('Invalid original example qualification')
    if result['provenance'] != 'original_geometry_code' or result['license'] != 'Apache-2.0':
        raise ValueError('Invalid original example provenance')
    if not isinstance(result['clips'], list) or len(result['clips']) != 7:
        raise ValueError('Original example needs seven clips')
    for index, clip in enumerate(result['clips'], 1):
        if not isinstance(clip, dict) or set(clip) != {'index','video','picture'} or type(clip['index']) is not int or clip['index'] != index:
            raise ValueError('Original example clip order changed')
        for kind, extension in (('video','mp4'),('picture','png')):
            value = clip[kind]
            fields = {'name','size','sha256'} | ({'width','height','frames','fps','duration_ms','audio'} if kind == 'video' else set())
            if not isinstance(value, dict) or set(value) != fields or value['name'] != f'scene-{index:02d}.{extension}':
                raise ValueError('Invalid original example resource')
            if type(value['size']) is not int or not 0 < value['size'] <= MAX_FILE or not isinstance(value['sha256'], str) or not re.fullmatch('[0-9a-f]{64}', value['sha256']):
                raise ValueError('Invalid original example byte facts')
            if kind == 'video' and (value['audio'] is not False or any(type(value[key]) is not int for key in ('width','height','frames','fps','duration_ms')) or
                    (value['width'],value['height'],value['frames'],value['fps'],value['duration_ms'],value['audio']) != (360,640,48,12,4000,False)):
                raise ValueError('Original video profile changed')
    return result['clips']


async def probe_original(path, checksum):
    """Fully decode the closed local clip in an owned configuration-free root process."""
    helper = Path(__file__).parent.parent / 'editing' / 'media_child.py'
    output, code = await run_owned_process([sys.executable, str(helper)],
        json.dumps({'mode':'probe','path':str(path),'expected_hash':checksum,'deadline':45}).encode(),
        timeout=46, environment=media_environment())
    facts = json.loads(output, object_pairs_hook=unique_object)
    if code or not isinstance(facts, dict) or set(facts) != {'video','audio'}:
        raise ValueError('Original clip decoding failed')
    video = facts['video']
    if facts['audio'] is not None or (video['width'],video['height'],video['frames'],video['duration_ms'],video['first_pts_us'],video['coverage_us']) != (360,640,48,4000,0,[[0,4000000]]):
        raise ValueError('Original clip clock profile changed')
    return facts


async def prepare_media(request_id, token):
    """Return actual final-file proofs; retain uncertain nonces on every failure or cancellation."""
    if not re.fullmatch('example-[0-9a-f]{32}', request_id) or not re.fullmatch('[0-9a-f]{64}', token):
        raise ValueError('Server-owned original example identity required')
    root = Path(settings.production_media_root).resolve()
    root.mkdir(parents=True, exist_ok=True)
    folder = Path(tempfile.mkdtemp(prefix='original-nonce-', dir=root))
    helper = Path(__file__).with_name('demo_media_child.py')
    try:
        stdout, code = await run_owned_process([sys.executable, str(helper)],
            json.dumps({'schema_version':1,'output':str(folder),'deadline':120}).encode(),
            timeout=125, environment=media_environment())
        result = json.loads(stdout, object_pairs_hook=unique_object)
        if code:
            raise ValueError('Original example rendering failed')
        clips = validated_manifest(result)
        resources, proofs, videos = [], {}, {}
        for clip in clips:
            for kind in ('picture','video'):
                value = clip[kind]
                source = folder / value['name']
                if kind == 'picture':
                    await file_operation(verify_picture, source, value['size'], value['sha256'])
                    facts = None
                else:
                    await file_operation(verify_bytes, source, value['size'], value['sha256'])
                    facts = await probe_original(source, value['sha256'])
                relative = f"original-examples/{request_id}/{kind}-{clip['index']:02d}-{value['sha256']}{source.suffix}"
                path = await file_operation(publish_file, source, relative, value['size'], value['sha256'])
                identifier = resource_id(request_id, kind, clip['index'])
                metadata = {'id':identifier,'sha256':value['sha256'],'size':value['size'],
                    'mime':'image/png' if kind == 'picture' else 'video/mp4','relative_path':relative}
                proofs[identifier] = {'metadata':metadata,'stat':compiler_stat(path)}
                resources.append({**metadata,'name':value['name'],'kind':kind,'clip_index':clip['index']})
                if facts is not None:
                    videos[identifier] = {**metadata,'stat':editing_stat(path),**facts}
        return resources, proofs, videos
    except (ValueError, OSError, KeyError, TypeError, asyncio.TimeoutError) as error:
        raise HTTPException(409, '原创示例素材尚未完整保存，请保留原请求重试；已有工程未被修改') from error
