"""Credential-free S25A preparation catalog, permanently unable to execute tasks."""
from __future__ import annotations

from dataclasses import dataclass
from types import MappingProxyType

from app.core.contracts.execution_capabilities import Capability
from app.core.contracts.seedance25 import (
    MODEL_ID, PREPARATION_PROFILE_HASH, SUPPORTED_DURATIONS,
)
from app.services.worker.execution_admission import AdmissionPolicy


def preparation_policy() -> AdmissionPolicy:
    """Describe the narrow software profile with an explicitly unknown, unusable quote."""
    return AdmissionPolicy(
        capability=Capability(
            model_id=MODEL_ID, durations=SUPPORTED_DURATIONS,
            reference_kinds=frozenset(), max_references=0,
        ),
        quote_minor=None, currency='CNY', iteration_limit=1,
        binding_id='s25a-preparation:' + PREPARATION_PROFILE_HASH,
    )


@dataclass(frozen=True, slots=True)
class _PreparationWorker:
    """Expose only the owned database factory needed for preparation compatibility."""

    factory: object
    budget_required: bool = True
    enforce_binding: bool = True

    async def step(self, _job_id: str) -> None:
        """Fail before task mutation if execution is mistakenly requested internally."""
        raise RuntimeError('S25A preparation runtime cannot execute')


class Seedance25PreparationRuntime:
    """A catalog runtime with no transport, credentials, dispatcher or activation switch."""

    __slots__ = ('_worker', '_policies')

    def __init__(self, factory):
        """Retain the injected factory without opening a connection or reading secrets."""
        if not callable(factory):
            raise TypeError('Preparation database factory must be callable')
        self._worker = _PreparationWorker(factory)
        self._policies = MappingProxyType({MODEL_ID: preparation_policy()})

    @property
    def worker(self) -> _PreparationWorker:
        """Return the immutable non-executing worker descriptor."""
        return self._worker

    @property
    def policies(self):
        """Expose immutable preparation capabilities for existing M4B compilation."""
        return self._policies

    @property
    def allow_submission(self) -> bool:
        """Preparation never permits admission, including after a lifecycle start."""
        return False

    @property
    def running(self) -> bool:
        """No dispatcher or background work exists in this runtime."""
        return False

    async def start(self) -> None:
        """Support app lifecycle without activating scheduling, connections or network."""
        return None

    async def stop(self) -> None:
        """Support app shutdown without owning background processes or tasks."""
        return None


def build_preparation_runtime(factory) -> Seedance25PreparationRuntime:
    """Construct an inert default; only explicit external runtimes can ever execute."""
    return Seedance25PreparationRuntime(factory)
