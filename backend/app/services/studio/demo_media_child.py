"""Render only this project's original geometry example, with no database, model, network or user-media input.

The parent owns this process and closes its stdin ownership gate before work
starts. Encoding uses PyAV inside this same process, with no encoder descendants.
Every picture is drawn here; it is not a model result.
"""
from __future__ import annotations

from fractions import Fraction
import hashlib
import json
import math
import os
from pathlib import Path
import sys
import threading

import av
from PIL import Image, ImageDraw

TEMPLATE = 'original-light-island-geometry-v1'
WIDTH, HEIGHT, FPS, SECONDS = 360, 640, 12, 4
CLIPS = 7


def unique_object(items):
    """Reject duplicate helper parameters instead of letting their order choose a path or limit."""
    result = {}
    for name, value in items:
        if name in result:
            raise ValueError('Duplicate parameter')
        result[name] = value
    return result


def configuration(raw: bytes):
    """Accept only the parent's bounded fixed-profile instruction and a new empty output directory."""
    if len(raw) > 16384:
        raise ValueError('Bounded helper input required')
    value = json.loads(raw.decode('utf-8'), object_pairs_hook=unique_object)
    if not isinstance(value, dict) or set(value) != {'schema_version', 'output', 'deadline'}:
        raise ValueError('Unknown helper input')
    if type(value['schema_version']) is not int or value['schema_version'] != 1 or type(value['deadline']) is not int or value['deadline'] != 120:
        raise ValueError('Unsupported fixed helper profile')
    if not isinstance(value['output'], str) or not 1 <= len(value['output']) <= 4096:
        raise ValueError('Bounded server-owned paths required')
    output = Path(value['output'])
    if not output.is_absolute() or output.is_symlink() or not output.is_dir() or any(output.iterdir()):
        raise ValueError('A new empty output directory is required')
    if getattr(output.lstat(), 'st_file_attributes', 0) & 0x400:
        raise ValueError('Output cannot be a redirected directory')
    return output.resolve(strict=True)


def frame(scene: int, index: int):
    """Draw the seven original story phases with shared geometry and deterministic source-frame clocks."""
    progress = index / (FPS * SECONDS - 1)
    phase = scene + progress
    scale = 1 - (.12 * progress if scene == 6 else 0)
    image = Image.new('RGB', (WIDTH, HEIGHT), (10, 20, 40))
    draw = ImageDraw.Draw(image)

    def point(x, y):
        """Use one consistent composition, with a gentle final pull-back around the island centre."""
        return round(180 + (x - 180) * scale), round(345 + (y - 345) * scale)

    def polygon(vertices, fill, outline=None, width=1):
        """Draw self-authored coordinates without loading fonts, textures or external illustrations."""
        draw.polygon([point(x, y) for x, y in vertices], fill=fill)
        if outline:
            draw.line([point(*value) for value in vertices + vertices[:1]], fill=outline, width=width)

    def glow(x, y, radius, colour):
        """Render a bounded layered light mark using original vector geometry."""
        cx, cy = point(x, y)
        for distance in range(radius * 3, 0, -1):
            factor = max(.06, (1 - distance / (radius * 3 + 1)) ** 2)
            fill = tuple(round(background + (value - background) * factor) for background, value in zip((10, 20, 40), colour))
            draw.ellipse((cx - distance, cy - distance, cx + distance, cy + distance), fill=fill)

    polygon([(48, 384), (116, 330), (246, 340), (314, 397), (247, 453), (105, 442)], (19, 58, 75), (50, 112, 132), 2)
    polygon([(48, 384), (105, 442), (247, 453), (314, 397), (304, 424), (245, 478), (104, 465), (53, 410)], (14, 36, 57))
    corners = [(139, 296), (221, 296), (221, 378), (139, 378)]
    draw.line([point(*value) for value in corners + corners[:1]], fill=(86, 108, 124), width=3)
    for edge in range(4):
        amount = min(1., max(0., phase - 2 - edge * .25))
        if amount:
            start, end = corners[edge], corners[(edge + 1) % 4]
            target = (start[0] + (end[0] - start[0]) * amount, start[1] + (end[1] - start[1]) * amount)
            draw.line([point(*start), point(*target)], fill=(244, 199, 96), width=4)
    for item, x in enumerate((111, 181, 250)):
        intensity = min(1., max(0., (phase - 4) * 3 - item))
        colour = tuple(round(a + (b - a) * intensity) for a, b in zip((41, 75, 88), (106, 211, 232)))
        polygon([(x, 408), (x - 12, 429), (x + 12, 429)], colour)
        if scene >= 5:
            draw.line([point(x, 414), point(180, 255)], fill=(45, 94, 115), width=1)
    if scene == 0:
        star = (43, 337)
    elif scene == 1:
        star = (43 + 96 * progress, 337)
    elif scene == 2:
        star = (139, 337 - 41 * progress)
    elif scene == 3:
        position = progress * 4
        edge = min(3, int(position))
        start, end = corners[edge], corners[(edge + 1) % 4]
        part = position - edge
        star = (start[0] + (end[0] - start[0]) * part, start[1] + (end[1] - start[1]) * part)
    else:
        star = (180 + (1 - progress) * 12 * math.sin(progress * math.pi * 2) if scene == 5 else 180, 255)
    glow(*star, radius=5 if scene == 0 else 7, colour=(255, 216, 125))
    if scene == 0:
        # Original flat bands suggest fog without importing photographic textures.
        for row in range(8):
            y = 210 + row * 40 + round(progress * 5)
            draw.line([(0, y), (WIDTH, y)], fill=(18, 30, 48), width=3)
    return image


def file_fact(path: Path):
    """Return bounded hashes and sizes only, never server-local absolute paths."""
    size = path.stat().st_size
    if not 0 < size <= 8 * 1024 * 1024:
        raise ValueError('Rendered example output exceeds its bound')
    checksum = hashlib.sha256()
    with path.open('rb') as stream:
        for block in iter(lambda: stream.read(65536), b''):
            checksum.update(block)
    return {'name': path.name, 'size': size, 'sha256': checksum.hexdigest()}


def decoded_video(path: Path):
    """Fully decode each actual MP4 and verify its exact four-second contiguous picture clock, without audio claims."""
    count = 0
    with av.open(str(path)) as container:
        if len(container.streams.video) != 1 or container.streams.audio:
            raise ValueError('The original example must contain exactly one silent picture stream')
        for picture in container.decode(video=0):
            if count >= FPS * SECONDS or picture.pts is None or picture.time_base is None:
                raise ValueError('Unexpected picture count or clock')
            if (picture.width, picture.height) != (WIDTH, HEIGHT) or Fraction(picture.pts) * picture.time_base != Fraction(count, FPS):
                raise ValueError('Example dimensions or picture clock changed')
            count += 1
    if count != FPS * SECONDS:
        raise ValueError('Incomplete example video')
    return {'width': WIDTH, 'height': HEIGHT, 'frames': count, 'fps': FPS, 'duration_ms': SECONDS * 1000, 'audio': False}


def render(output: Path):
    """Encode exclusive fixed-name files inside this owned process, without spawning an external encoder."""
    clips = []
    for scene in range(CLIPS):
        video, picture = output / f'scene-{scene + 1:02d}.mp4', output / f'scene-{scene + 1:02d}.png'
        with picture.open('xb') as stream:
            frame(scene, 0).save(stream, format='PNG')
        with video.open('xb') as target, av.open(target, mode='w', format='mp4') as container:
            stream = container.add_stream('libx264', rate=FPS)
            stream.width, stream.height, stream.pix_fmt = WIDTH, HEIGHT, 'yuv420p'
            stream.codec_context.thread_count = 2
            stream.options = {'preset': 'fast'}
            container.metadata['comment'] = 'Fawnimo original geometry example; no model call'
            for index in range(FPS * SECONDS):
                picture_frame = av.VideoFrame.from_image(frame(scene, index))
                picture_frame.pts, picture_frame.time_base = index, Fraction(1, FPS)
                for packet in stream.encode(picture_frame):
                    container.mux(packet)
            for packet in stream.encode():
                container.mux(packet)
        clips.append({'index': scene + 1, 'video': {**file_fact(video), **decoded_video(video)}, 'picture': file_fact(picture)})
    return {'schema_version': 1, 'template_version': TEMPLATE, 'clips': clips, 'external_model_calls': 0,
            'provenance': 'original_geometry_code', 'license': 'Apache-2.0', 'model_quality_qualified': False}


def main():
    """Accept the complete ownership-gated instruction before encoding and keep failures free of paths/content."""
    watchdog = None
    try:
        output = configuration(sys.stdin.buffer.read(16385))
        watchdog = threading.Timer(120, lambda: os._exit(2))
        watchdog.daemon = True
        watchdog.start()
        result = render(output)
    except Exception:
        result = {'error': 'Original example rendering failed within its fixed local profile'}
    finally:
        if watchdog:
            watchdog.cancel()
    print(json.dumps(result), flush=True)
    if 'error' in result:
        raise SystemExit(1)


if __name__ == '__main__':
    main()
