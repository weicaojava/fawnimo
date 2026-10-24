"""Application-owned runtime; imported projects cannot activate a provider or worker."""
import asyncio
from collections.abc import Mapping
from app.services.worker.execution_admission import AdmissionPolicy
from app.services.worker.execution_dispatcher import ExecutionDispatcher
from app.core.contracts.execution_binding import binding_fingerprint


class ExecutionRuntime:
    """Bind trusted policies and one worker explicitly, remaining disabled by default."""
    def __init__(self,worker,policies: Mapping[str,AdmissionPolicy], *, allow_submission=False, dispatcher=None):
        self.worker=worker
        self.policies=dict(policies)
        self.allow_submission=allow_submission
        if allow_submission and (not worker.budget_required or not worker.enforce_binding or not self.policies):
            raise ValueError('Enabled runtime requires budget, binding enforcement and trusted policies')
        if any(key!=policy.capability.model_id for key,policy in self.policies.items()):
            raise ValueError('Runtime model keys must match trusted capability identities')
        self.dispatcher=dispatcher or ExecutionDispatcher(worker,factory=worker.factory)
        self.task=None

    @property
    def running(self) -> bool:
        """A failed or stopped dispatcher must not accept fresh paid work."""
        return self.task is not None and not self.task.done()

    async def start(self) -> None:
        """Only an explicitly authorized runtime starts background processing."""
        if self.allow_submission and not self.running:
            self.worker.allowed_bindings=frozenset(binding_fingerprint(policy) for policy in self.policies.values())
            self.task=asyncio.create_task(self.dispatcher.run(),name='durable-execution-dispatcher')

    async def stop(self) -> None:
        """Join the dispatcher so shutdown cannot leave network work behind."""
        if self.task is not None:
            await self.dispatcher.stop()
            if not self.task.done():self.task.cancel()
            await asyncio.gather(self.task,return_exceptions=True)
            self.task=None
