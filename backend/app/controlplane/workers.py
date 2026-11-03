"""Own a bounded pool of permanently scoped studio processes, never a shared SQL session."""
from __future__ import annotations

import asyncio
from dataclasses import dataclass,field
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
    trial_policy: dict | None = None
    reference_policy: object = field(default=None,repr=False)
    image_admission: object = field(default=None,repr=False)


class WorkspaceWorkers:
    """Route only to server-generated roots and keep running jobs in their original space."""

    def __init__(self, root: Path, *, probe: Path | None = None, capacity: int = 4,trial_policy_provider=None,reference_policy_provider=None,image_admission_provider=None):
        """Use a fixed capacity; reaching it refuses admission instead of evicting active tasks."""
        if not 1 <= capacity <= 16:
            raise ValueError('Workspace process capacity must be between 1 and 16')
        self.root, self.probe, self.capacity = root.resolve(), probe, capacity
        self.trial_policy_provider=trial_policy_provider
        self.reference_policy_provider=reference_policy_provider
        self.image_admission_provider=image_admission_provider
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

    async def policies(self,workspace_id):
        """Recheck immutable owner policies without holding unrelated workspace startup admission."""
        policy=await asyncio.to_thread(self.trial_policy_provider,workspace_id) if self.trial_policy_provider else None
        reference=self.reference_policy_provider(workspace_id) if self.reference_policy_provider else None
        if reference is not None:
            from app.core.contracts.workspace_reference_delivery import WorkspaceReferencePolicy
            if not isinstance(reference,WorkspaceReferencePolicy) or reference.workspace_id!=workspace_id:
                raise HTTPException(503,'参考素材交付范围尚未确认')
        return policy,reference

    def image_policy(self,workspace_id):
        """Bind only a trusted immutable operator ticket to this fixed worker; callers cannot select another workspace or policy."""
        value=self.image_admission_provider(workspace_id) if self.image_admission_provider else None
        if value is not None:
            from app.controlplane.image_admission import ImageAdmissionTicket
            if not isinstance(value,ImageAdmissionTicket):raise HTTPException(503,'图片执行授权尚未确认')
            try:value.checked_payload(workspace_id)
            except ValueError:raise HTTPException(503,'图片执行授权范围尚未确认') from None
        return value

    def reuse(self,workspace_id,worker,policy,reference,image_admission=None):
        """Reuse only the same fixed identity and unchanged policy; a cached process never gains new authority silently."""
        if worker.workspace_id!=workspace_id:raise HTTPException(503,'工作进程身份尚未确认')
        if worker.trial_policy!=policy:raise HTTPException(503,'体验策略已变化，请联系维护者核对工作进程')
        if worker.reference_policy!=reference:raise HTTPException(503,'参考素材交付配置已变化，请联系维护者核对工作进程')
        if getattr(worker,'image_admission',None)!=image_admission:raise HTTPException(503,'图片执行授权已变化，请联系维护者核对工作进程')
        return worker

    async def get(self, workspace_id: str) -> Worker:
        """Start at most one fixed-scope process, fenced by its private readiness token."""
        if not WORKSPACE.fullmatch(workspace_id):
            raise HTTPException(404, '工作空间不存在')
        from app.controlplane.migration import migration_pending
        if await asyncio.to_thread(migration_pending, self.root, workspace_id):
            raise HTTPException(503, '旧数据迁移尚未完成，请由维护者核对原迁移请求')
        cached=self.workers.get(workspace_id)
        if cached is not None and cached.process.returncode is None and not any(worker.process.returncode is not None for worker in self.workers.values()):
            policy,reference=await self.policies(workspace_id)
            if self.workers.get(workspace_id) is cached and cached.process.returncode is None and not any(worker.process.returncode is not None for worker in self.workers.values()):
                return self.reuse(workspace_id,cached,policy,reference,self.image_policy(workspace_id))
        async with self.lock:
            # Reap all dead entries, without interrupting any live workspace's jobs.
            for stale_id, stale in list(self.workers.items()):
                if stale.process.returncode is not None:
                    await self.stop_worker(stale)
                    self.workers.pop(stale_id)
            existing = self.workers.get(workspace_id)
            policy,reference=await self.policies(workspace_id)
            image_admission=self.image_policy(workspace_id)
            if existing and existing.process.returncode is None:
                return self.reuse(workspace_id,existing,policy,reference,image_admission)
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
                worker = Worker(workspace_id, port, token, process, job,policy,reference,image_admission)
                if job:
                    job.attach(process.pid)
                gate = {'root': str(self.root), 'workspace_id': workspace_id, 'port': port, 'internal_token': token}
                if policy is not None: gate['trial_policy']=policy
                if reference is not None:gate['reference_delivery']=reference.private_message()
                if image_admission is not None:gate['image_admission']=image_admission.grant.model_dump(mode='json')
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
                            ready=response.json() if response.status_code==200 else {}
                            expected_image=image_admission.grant.model_dump(mode='json') if image_admission is not None else None
                            if response.status_code == 200 and ready.get('workspace_id') == workspace_id and ready.get('image_admission')==expected_image:
                                self.workers[workspace_id] = worker
                                return worker
                        except (httpx.HTTPError, ValueError):
                            pass
                        await asyncio.sleep(.15)
                raise RuntimeError('Workspace process readiness deadline exceeded')
            except BaseException:
                if process:
                    await self.stop_worker(Worker(workspace_id, port, token, process, job,policy))
                elif job:
                    await asyncio.to_thread(job.terminate_and_close)
                raise HTTPException(503, '工作空间启动失败；原数据已保留') from None

    async def close(self) -> None:
        """Join all owned processes when the identity gateway stops."""
        async with self.lock:
            for worker in self.workers.values():
                await self.stop_worker(worker)
            self.workers.clear()
