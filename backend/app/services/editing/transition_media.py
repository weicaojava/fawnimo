"""A separate killable v2 process boundary; the v1 child protocol is unchanged."""
import asyncio
import json
from pathlib import Path
import sys
from fastapi import HTTPException
from app.core.owned_process import run_owned_process


async def run_child(parameters, deadline=120):
    """Render in a private watchdog process; cancel and join its entire encoder tree on every exit."""
    if deadline != 120: raise ValueError('Transition rendering requires the bounded 120 second profile')
    try:
        raw, returncode = await run_owned_process([sys.executable, str(Path(__file__).with_name('transition_child.py'))],
            json.dumps({**parameters, 'deadline': deadline}, ensure_ascii=False).encode(), timeout=deadline + 1)
        result = json.loads(raw)
        if returncode or not isinstance(result, dict) or result.get('error'): raise ValueError('Actual transition rendering failed')
        return result
    except asyncio.CancelledError:
        raise
    except (ValueError, OSError, asyncio.TimeoutError) as error:
        raise HTTPException(409, '本地转场合成未通过素材、帧数或音轨校验，请检查已锁定源文件') from error
