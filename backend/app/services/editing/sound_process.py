"""Explicit non-credential environment for local sound decoder, encoder and installed-voice children."""
import os
import asyncio


def child_environment():
    """Retain only platform/runtime paths and locale; model and application secrets are unnecessary."""
    allowed = {'SYSTEMROOT', 'WINDIR', 'COMSPEC', 'PATH', 'PATHEXT', 'TEMP', 'TMP', 'TMPDIR', 'LANG', 'LC_ALL', 'TZ', 'USERPROFILE', 'APPDATA', 'LOCALAPPDATA'}
    value = {key: item for key, item in os.environ.items() if key.upper() in allowed}
    return {**value, 'PYTHONUTF8': '1', 'PYTHONDONTWRITEBYTECODE': '1'}


async def cancel_and_join(task):
    """Join an owned rendering task on every failure, including database errors before its result is consumed."""
    if task is None: return
    if not task.done(): task.cancel()
    try: await task
    except (asyncio.CancelledError, Exception): pass
