"""Explicit installed Windows/Linux speech profiles; no HTTP client, API key, SSML or paid fallback."""
import asyncio
import hashlib
import json
import os
from pathlib import Path
import sys
from fastapi import HTTPException
from app.config import settings
from app.core.contracts.local_speech_protocols import WINDOWS_PROFILE, LINUX_PROFILE, voice_identity
from app.core.owned_process import run_owned_process
from app.services.editing.media import stat_identity
from app.services.editing.sound_process import child_environment

# Preserve the original exported identifier for historical callers and Windows records.
PROFILE = WINDOWS_PROFILE


def available():
    """Offline voice generation stays unavailable on unsupported or imported locked workers."""
    installed_linux = sys.platform.startswith('linux') and Path('/usr/bin/espeak-ng').is_file() and Path('/usr/bin/ffmpeg').is_file()
    return (os.name == 'nt' or installed_linux) and settings.local_speech_enabled and not settings.workspace_runtime_locked


class LocalSpeechEngine:
    """Bind a fixed trusted system binary and repo-owned script, never a client command."""
    def __init__(self):
        """Select only the fixed installed engine for this platform, independently of any client-selected model or path."""
        self.profile = LINUX_PROFILE if sys.platform.startswith('linux') else WINDOWS_PROFILE
        self.executable = Path('/usr/bin/espeak-ng') if self.profile == LINUX_PROFILE else Path(os.environ.get('SystemRoot', 'C:/Windows'))/'System32/WindowsPowerShell/v1.0/powershell.exe'
        self.last_failure = {}

    async def call(self, value, timeout):
        """Deliver UTF8 JSON only after process ownership; terminate and join on every exit."""
        if not available() or not self.executable.is_file(): raise HTTPException(503, '本地配音未启用或当前机器未安装对应离线声音引擎')
        try:
            self.last_failure = {}
            arguments = [sys.executable, str(Path(__file__).with_name('local_speech_linux_child.py'))] if self.profile == LINUX_PROFILE else [str(self.executable), '-NoLogo', '-NoProfile', '-NonInteractive', '-ExecutionPolicy', 'Bypass', '-File', str(Path(__file__).with_name('local_speech.ps1'))]
            raw, code = await run_owned_process(arguments,
                json.dumps(value, ensure_ascii=False).encode(), timeout=timeout, max_stdout_bytes=16384, environment=child_environment())
            result = json.loads(raw.decode('utf-8-sig'))
            if code or not isinstance(result, dict) or result.get('error'):
                if isinstance(result, dict):
                    self.last_failure = {key: result[key] for key in ('stage', 'error_type', 'returncode') if key in result}
                raise ValueError('Local voice rejected')
            return result
        except asyncio.CancelledError:
            raise
        except (OSError, ValueError, asyncio.TimeoutError) as error:
            raise HTTPException(409, '本地语音引擎未完成，请检查已安装声音或处理时限') from error

    async def catalog(self):
        """Enumerate actual enabled installed voices without speaking or sending content anywhere."""
        if not available():
            reason = '迁入空间的后台执行已暂停' if settings.workspace_runtime_locked else '当前已关闭本地系统配音' if not settings.local_speech_enabled else '当前系统不支持已声明的离线声音引擎'
            return {'enabled': False, 'reason': reason+'；仍可导入WAV进行声音剪辑', 'profile': self.profile, 'external_fee_fen': 0, 'voices': []}
        try:
            result = await self.call({'mode': 'catalog'}, 10)
            if self.profile == LINUX_PROFILE and set(result) != {'voices', 'engine_fingerprint'}:
                raise ValueError('Invalid installed Linux engine catalog')
            rows = result['voices']
            if not isinstance(rows, list) or len(rows) > 64: raise ValueError('Invalid voice catalog')
            voices, seen = [], set()
            for row in rows:
                if set(row) != {'name', 'culture'} or not isinstance(row['name'], str) or not 1 <= len(row['name']) <= 200 or not isinstance(row['culture'], str) or not 1 <= len(row['culture']) <= 32: raise ValueError('Invalid installed voice')
                identifier = voice_identity(self.profile, row, result.get('engine_fingerprint'))
                if identifier in seen: raise ValueError('Duplicate installed voice')
                seen.add(identifier); voices.append({'id': identifier, **row})
            return {'enabled': bool(voices), 'reason': '' if voices else '未发现可用的已安装系统声音', 'profile': self.profile, 'external_fee_fen': 0, 'voices': voices}
        except (HTTPException, ValueError, KeyError):
            return {'enabled': False, 'reason': '无法读取已安装的离线声音；原候选和声音剪辑仍可使用', 'profile': self.profile, 'external_fee_fen': 0, 'voices': []}

    async def render(self, intent, folder):
        """Synthesize exact original text to a fresh private WAV, then fully decode actual output samples."""
        if intent['profile'] != self.profile or type(intent['external_fee_fen']) is not int or intent['external_fee_fen'] != 0: raise HTTPException(409, '本地语音协议不匹配')
        result = await self.call({'mode': 'render', 'voice': intent['voice'], 'text': intent['text'], 'rate': intent['rate'], 'folder': str(folder)}, 60)
        if result != {'completed': True}: raise HTTPException(409, '本地语音未返回完整结果')
        path = Path(folder)/'voice.wav'
        before = stat_identity(path)
        if not 44 <= before[0] <= 32 * 1024 * 1024: raise HTTPException(409, '本地语音输出大小超限')
        checksum = await asyncio.to_thread(file_hash, path)
        raw, code = await run_owned_process([sys.executable, str(Path(__file__).with_name('sound_probe_child.py'))],
            json.dumps({'path': str(path), 'expected_hash': checksum}).encode(), timeout=20, max_stdout_bytes=4096, environment=child_environment())
        facts = json.loads(raw)
        if code or facts.get('error') or before != stat_identity(path): raise HTTPException(409, '本地语音输出未通过完整样本校验')
        facts.pop('interval_peaks_pcm16')
        if facts['peak_pcm16'] <= 32: raise HTTPException(409, '本地语音没有有效声音信号')
        return {'sha256': checksum, 'size': before[0], 'stat': before, 'audio': facts}


def file_hash(path):
    """Read full private output bytes with bounded memory outside database writers."""
    with Path(path).open('rb') as stream: return hashlib.file_digest(stream, 'sha256').hexdigest()
