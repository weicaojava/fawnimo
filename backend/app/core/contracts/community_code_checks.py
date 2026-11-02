"""A separate fixed Linux container-check profile, never a preparation lease or a client-selected command/network policy."""
from dataclasses import dataclass
from pathlib import PurePosixPath
from pydantic import BaseModel, ConfigDict, Field, StrictInt


class CommunityCodeCheckInput(BaseModel):
    """A local maintainer names one existing inert bundle and an original check key; executor/image policy is not supplied by issue JSON."""
    model_config = ConfigDict(extra='forbid', frozen=True)
    request_key: str = Field(min_length=8, max_length=128, pattern=r'^[A-Za-z0-9][A-Za-z0-9_.:-]*$')
    bundle_id: str = Field(pattern=r'^[0-9a-f]{64}$')


class LinuxCodeCheckPolicy(BaseModel):
    """Finite immutable server constants for the initial explicitly enabled check profile; changes require a new protocol."""
    model_config = ConfigDict(extra='forbid', frozen=True)
    schema_version: StrictInt = Field(default=1, ge=1, le=1)
    max_concurrent: StrictInt = Field(default=1, ge=1, le=1)
    max_attempts_per_bundle: StrictInt = Field(default=3, ge=3, le=3)
    max_attempts_total: StrictInt = Field(default=384, ge=384, le=384)
    max_events: StrictInt = Field(default=4096, ge=4096, le=4096)
    supervisor_seconds: StrictInt = Field(default=45, ge=45, le=45)
    controller_seconds: StrictInt = Field(default=60, ge=60, le=60)
    docker_command_seconds: StrictInt = Field(default=15, ge=15, le=15)
    cleanup_seconds: StrictInt = Field(default=20, ge=20, le=20)
    max_candidate_output_bytes: StrictInt = Field(default=65536, ge=65536, le=65536)
    memory_bytes: StrictInt = Field(default=536870912, ge=536870912, le=536870912)
    tmpfs_bytes: StrictInt = Field(default=134217728, ge=134217728, le=134217728)
    shm_bytes: StrictInt = Field(default=1048576, ge=1048576, le=1048576)
    pids: StrictInt = Field(default=64, ge=64, le=64)
    max_open_files: StrictInt = Field(default=128, ge=128, le=128)
    nano_cpus: StrictInt = Field(default=1000000000, ge=1000000000, le=1000000000)
    max_source_files: StrictInt = Field(default=4096, ge=4096, le=4096)
    max_source_bytes: StrictInt = Field(default=67108864, ge=67108864, le=67108864)
    external_fee_limit_fen: StrictInt = Field(default=0, ge=0, le=0)


def checked_source_path(path):
    """Select the complete declared runtime source/resource subset from a pinned public Git tree, excluding private/runtime paths and the trusted supervisor."""
    if (not isinstance(path, str) or not path or len(path) > 500 or path.startswith('/') or '\\' in path or ':' in path
            or any(ord(char) < 32 for char in path) or any(part in {'', '.', '..'} for part in path.split('/'))):
        return False
    parts = PurePosixPath(path).parts
    if (any(part in {'.git','.venv','__pycache__','node_modules','private','local-files','local-media','local-studio','logs','work','outputs'} for part in parts)
            or any(part.startswith('.env') for part in parts) or path.endswith(('.pyc','.pyo'))
            or path == 'backend/community_isolated_runner.py'):
        return False
    return (path.startswith(('backend/app/', 'backend/tests/', 'backend/sql/'))
        or len(parts) == 2 and parts[0] == 'backend' and parts[1].endswith('.py')
        or path in {'backend/pyproject.toml','backend/uv.lock','backend/README.md','backend/.python-version','scripts/features.py','capabilities/features.json'})


@dataclass(frozen=True)
class CodeCheckAdmission:
    """Internal fresh-source admission from the trusted collector, never parsed from issue JSON or a preparation token."""
    bundle_id: str
    scope_hash: str
    candidate_commit: str
    candidate_tree: str
    candidate_parent: str
    image_id: str
    repository_id: int
    repository_name: str
    source_fingerprint: str
    checked_at_us: int


@dataclass(frozen=True)
class CodeCheckObservation:
    """Internal result after validating the owned trusted supervisor and stopped OCI state; candidate stdout is retained only by hash/size."""
    outcome: str
    process_exit_code: int
    supervisor_sha256: str
    output_sha256: str
    output_bytes: int
    kernel_limits_verified: bool
    uid_separation_verified: bool
    namespace_isolation_verified: bool


@dataclass(frozen=True)
class CodeCheckCleanup:
    """Internal identity-fenced daemon observation; absence alone cannot resolve an unacknowledged creation request."""
    attempt_id: str
    container_name: str
    image_id: str
    owner_token: str
    container_id: str | None
    engine_observed: bool
    resource_absent: bool
