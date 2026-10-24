"""Account-free, application-owned scanner for local technical video analyses."""
import asyncio
import logging
from app.services.reverse.ingestion import tick

logger=logging.getLogger(__name__)


class ReverseScanner:
    """Run one bounded decode at a time and recover expired leases after restart."""
    def __init__(self,factory,trusted_ffmpeg):
        self.factory=factory;self.ffmpeg=trusted_ffmpeg;self.task=None

    async def run(self):
        """Scan persistent work; log only exception type, never private media paths."""
        while True:
            try:await tick(self.factory,self.ffmpeg)
            except Exception as error:logger.error('Reverse scanner failed (%s)',type(error).__name__)
            await asyncio.sleep(1)

    async def start(self):
        """Start only when the application has validated its local decoder config."""
        if self.task is None or self.task.done():self.task=asyncio.create_task(self.run(),name='reverse-video-scanner')

    async def stop(self):
        """Cancel and join the decoder so application shutdown cannot orphan work."""
        if self.task is not None:
            self.task.cancel();await asyncio.gather(self.task,return_exceptions=True);self.task=None
