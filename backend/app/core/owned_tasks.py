"""Join owned asynchronous cleanup while preserving cancellation of its caller."""
import asyncio


async def join_tasks(*tasks, cancel=False):
    """Consume child outcomes, wait through repeated caller cancellation, then propagate that caller cancellation.

    Gathering child exceptions first distinguishes an intentionally cancelled
    child from cancellation of the owner awaiting its shielded cleanup.
    """
    if cancel:
        for task in tasks:
            if not task.done() and not task.cancelling():
                task.cancel()
    joined = asyncio.gather(*tasks,return_exceptions=True)
    cancellation = None
    while not joined.done():
        try:
            await asyncio.shield(joined)
        except asyncio.CancelledError as error:
            cancellation = error
    result = joined.result()
    if cancellation is not None:
        raise cancellation
    return result
