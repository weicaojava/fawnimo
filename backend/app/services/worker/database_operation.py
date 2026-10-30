"""Join short database units through repeated cancellation so SQLite threads cannot outlive Session cleanup."""
import asyncio
from functools import wraps


def joined_database(operation):
    """Finish only the decorated DB transaction and physical close; callers keep network operations cancellable."""
    @wraps(operation)
    async def joined(*args,**kwargs):
        """Deliver a pending cancellation after commit/rollback and consume any inner failure without leaking a captured key."""
        task=asyncio.create_task(operation(*args,**kwargs));cancellation=None
        while not task.done():
            try:await asyncio.shield(task)
            except asyncio.CancelledError as error:cancellation=error
            except BaseException:break
        if cancellation is not None:
            if not task.cancelled() and task.exception() is None:
                result=task.result()
                if isinstance(result,dict) and 'secret' in result:result['secret']=''
            raise cancellation
        return task.result()
    return joined
