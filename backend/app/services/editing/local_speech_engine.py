"""Installed Windows system speech only; no HTTP client, API key, SSML or paid fallback."""
import asyncio
import hashlib
import json
import os
from pathlib import Path
import sys
from fastapi import HTTPException
from app.config import settings
from app.core.owned_process import run_owned_process
from app.services.editing.media import stat_identity
from app.services.editing.sound_process import child_environment

PROFILE = 'windows_sapi_pcm_v1'


def available():
    """Offline voice generation stays unavailable on unsupported or imported locked workers."""
    return os.name == 'nt' and settings.local_speech_enabled and not settings.workspace_runtime_locked


class LocalSpeechEngine:
    """Bind a fixed trusted system binary and repo-owned script, never a client command."""
    def __init__(self):
        """Select the installed Windows PowerShell runtime; profile is explicitly platform-specific."""
        self.executable = Path(os.environ.get('SystemRoot', 'C:/Windows'))/'System32/WindowsPowerShell/v1.0/powershell.exe'

    async def call(self, value, timeout):
        """Deliver UTF8 JSON only after process ownership; terminate and join on every exit."""
        if not available() or not self.executable.is_file(): raise HTTPException(503, '本地系统语音仅在已启用的Windows工作机上可用')
        try:
            raw, code = await run_owned_process([str(self.executable), '-NoLogo', '-NoProfile', '-NonInteractive', '-ExecutionPolicy', 'Bypass', '-File', str(Path(__file__).with_name('local_speech.ps1'))],
                json.dumps(value, ensure_ascii=False).encode(), timeout=timeout, max_stdout_bytes=16384, environment=child_environment())
            result = json.loads(raw.decode('utf-8-sig'))
            if code or not isinstance(result, dict) or result.get('error'): raise ValueError('Local voice rejected')
            return result
        except asyncio.CancelledError:
            raise
        except (OSError, ValueError, asyncio.TimeoutError) as error:
            raise HTTPException(409, '本地语音引擎未完成，请检查已安装声音或处理时限') from error

    async def catalog(self):
        """Enumerate actual enabled installed voices without speaking or sending content anywhere."""
        if not available():
            reason = '迁入空间的后台执行已暂停' if settings.workspace_runtime_locked else '当前已关闭本地系统配音' if not settings.local_speech_enabled else '本地系统语音需要Windows及已安装声音'
            return {'enabled': False, 'reason': reason+'；仍可导入WAV进行声音剪辑', 'profile': PROFILE, 'external_fee_fen': 0, 'voices': []}
        try:
            result = await self.call({'mode': 'catalog'}, 10)
            rows = result['voices']
            if not isinstance(rows, list) or len(rows) > 64: raise ValueError('Invalid voice catalog')
            voices, seen = [], set()
            for row in rows:
                if set(row) != {'name', 'culture'} or not isinstance(row['name'], str) or not 1 <= len(row['name']) <= 200 or not isinstance(row['culture'], str) or not 1 <= len(row['culture']) <= 32: raise ValueError('Invalid installed voice')
                identifier = 'sapi_'+hashlib.sha256(json.dumps(row, sort_keys=True, separators=(',', ':'), ensure_ascii=False).encode()).hexdigest()
                if identifier in seen: raise ValueError('Duplicate installed voice')
                seen.add(identifier); voices.append({'id': identifier, **row})
            return {'enabled': bool(voices), 'reason': '' if voices else '未发现可用的已安装系统声音', 'profile': PROFILE, 'external_fee_fen': 0, 'voices': voices}
        except (HTTPException, ValueError, KeyError):
            return {'enabled': False, 'reason': '无法读取本地系统声音；原候选和声音剪辑仍可使用', 'profile': PROFILE, 'external_fee_fen': 0, 'voices': []}

    async def render(self, intent, folder):
        """Synthesize exact original text to a fresh private WAV, then fully decode actual output samples."""
        if intent['profile'] != PROFILE or intent['external_fee_fen'] != 0: raise HTTPException(409, '本地语音协议不匹配')
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
