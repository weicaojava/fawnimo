"""Own a bounded pool of permanently scoped studio processes, never a shared SQL session."""
from __future__ import annotations

import asyncio
from dataclasses import dataclass
import json
import os
from pathlib import Path
import secrets
import signal
import socket
import subprocess
import sys
import time

import httpx
from fastapi import HTTPException

from app.controlplane.store import WORKSPACE
from app.core.owned_process import WindowsProcessJob


def worker_environment() -> dict[str, str]:
    """Allow only OS runtime essentials; exclude user configuration and all cloud/model secrets."""
    result = {key: value for key, value in os.environ.items() if key.upper() in {
        'SYSTEMROOT', 'WINDIR', 'TEMP', 'TMP', 'COMSPEC', 'PATHEXT', 'LANG', 'LC_ALL',
    }}
    result.update(JELLYFISH_SKIP_ENV_FILE='1', PYTHONNOUSERSITE='1', PYTHONDONTWRITEBYTECODE='1', PYTHONUNBUFFERED='1')
    return result


@dataclass
class Worker:
    """Keep the kernel process-tree owner alive for the entire workspace runtime."""
    workspace_id: str
    port: int
    token: str
    process: asyncio.subprocess.Process
    job: WindowsProcessJob | None


class WorkspaceWorkers:
    """Route only to server-generated roots and keep running jobs in their original space."""

    def __init__(self, root: Path, *, probe: Path | None = None, capacity: int = 4):
        """Use a fixed capacity; reaching it refuses admission instead of evicting active tasks."""
        if not 1 <= capacity <= 16:
            raise ValueError('Workspace process capacity must be between 1 and 16')
        self.root, self.probe, self.capacity = root.resolve(), probe, capacity
        self.backend = Path(__file__).resolve().parents[2]
        self.workers: dict[str, Worker] = {}
        self.lock = asyncio.Lock()

    async def stop_worker(self, worker: Worker) -> None:
        """Kill and join the owned tree even when the worker died before its descendants."""
        try:
            if worker.job:
                await asyncio.to_thread(worker.job.terminate_and_close)
            else:
                try:
                    os.killpg(worker.process.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
        finally:
            if worker.process.returncode is None:
                try:
                    worker.process.kill()
                except ProcessLookupError:
                    pass
            await worker.process.wait()

    async def get(self, workspace_id: str) -> Worker:
        """Start at most one fixed-scope process, fenced by its private readiness token."""
        if not WORKSPACE.fullmatch(workspace_id):
            raise HTTPException(404, '工作空间不存在')
        from app.controlplane.migration import migration_pending
        if await asyncio.to_thread(migration_pending, self.root, workspace_id):
            raise HTTPException(503, '旧数据迁移尚未完成，请由维护者核对原迁移请求')
        async with self.lock:
            # Reap all dead entries, without interrupting any live workspace's jobs.
            for stale_id, stale in list(self.workers.items()):
                if stale.process.returncode is not None:
                    await self.stop_worker(stale)
                    self.workers.pop(stale_id)
            existing = self.workers.get(workspace_id)
            if existing and existing.process.returncode is None:
                return existing
            if existing:
                await self.stop_worker(existing)
                self.workers.pop(workspace_id)
            if len(self.workers) >= self.capacity:
                raise HTTPException(503, '工作空间运行容量已满，请联系维护者')
            self.root.mkdir(parents=True, exist_ok=True)
            with socket.socket() as reservation:
                reservation.bind(('127.0.0.1', 0))
                port = reservation.getsockname()[1]
            token = secrets.token_urlsafe(48)
            job = WindowsProcessJob() if os.name == 'nt' else None
            options = {'creationflags': subprocess.CREATE_NO_WINDOW} if os.name == 'nt' else {'start_new_session': True}
            process = None
            try:
                process = await asyncio.create_subprocess_exec(sys.executable, str(self.backend / 'run_tenant_worker.py'),
                    cwd=self.backend, env=worker_environment(), stdin=asyncio.subprocess.PIPE,
                    stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL, **options)
                worker = Worker(workspace_id, port, token, process, job)
                if job:
                    job.attach(process.pid)
                gate = {'root': str(self.root), 'workspace_id': workspace_id, 'port': port, 'internal_token': token}
                if self.probe:
                    gate['probe'] = str(self.probe.resolve(strict=True))
                process.stdin.write(json.dumps(gate).encode())
                await process.stdin.drain()
                process.stdin.close()
                deadline = time.monotonic() + 45
                async with httpx.AsyncClient(trust_env=False, timeout=1, follow_redirects=False) as client:
                    while time.monotonic() < deadline:
                        if process.returncode is not None:
                            raise RuntimeError('Workspace process failed to start')
                        try:
                            response = await client.get(f'http://127.0.0.1:{port}/__workspace/ready', headers={'X-Workspace-Token': token})
                            if response.status_code == 200 and response.json().get('workspace_id') == workspace_id:
                                self.workers[workspace_id] = worker
                                return worker
                        except (httpx.HTTPError, ValueError):
                            pass
                        await asyncio.sleep(.15)
                raise RuntimeError('Workspace process readiness deadline exceeded')
            except BaseException:
                if process:
                    await self.stop_worker(Worker(workspace_id, port, token, process, job))
                elif job:
                    await asyncio.to_thread(job.terminate_and_close)
                raise HTTPException(503, '工作空间启动失败；原数据已保留') from None

    async def close(self) -> None:
        """Join all owned processes when the identity gateway stops."""
        async with self.lock:
            for worker in self.workers.values():
                await self.stop_worker(worker)
            self.workers.clear()
