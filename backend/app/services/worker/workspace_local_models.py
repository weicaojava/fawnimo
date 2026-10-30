"""Read only a fixed workspace's own optional local-model exceptions, defaulting every protocol closed."""
from dataclasses import dataclass, field
import json
from pathlib import Path
from app.core.contracts.workspace_local_models import WorkspaceLocalModelConfiguration


@dataclass(frozen=True)
class WorkspaceLocalModelEndpoints:
    """Hold immutable non-secret protocol exceptions; a local origin does not supply prices or execution permission."""
    issue: str = 'not_configured'
    agent_text_ports: frozenset[int] = field(default_factory=frozenset)
    asset_image_ports: frozenset[int] = field(default_factory=frozenset)
    video_understanding_ports: frozenset[int] = field(default_factory=frozenset)
    native_video_port: int | None = None

    def host_for(self, protocol: str) -> str | None:
        """Allow exactly IPv4 loopback when that protocol has an explicit port; never use DNS aliases or caller hosts."""
        ports = {'agent_text': self.agent_text_ports, 'asset_image': self.asset_image_ports,
            'video_understanding': self.video_understanding_ports, 'native_video': self.native_video_port}
        return '127.0.0.1' if ports.get(protocol) else None


def _unique_fields(pairs):
    """Reject ambiguous duplicate operator fields instead of silently taking the last enabled value."""
    result = {}
    for name, value in pairs:
        if name in result:
            raise ValueError('Duplicate local model configuration field')
        result[name] = value
    return result


def _invalid_number(_value):
    """Private configurations use finite strict JSON values only, without reflecting malformed contents."""
    raise ValueError('Invalid local model configuration number')


def load_workspace_local_models(filename: str | None, *, workspace_id: str | None) -> WorkspaceLocalModelEndpoints:
    """Validate a bounded private file and fixed owner before enabling any local transport exception.

    File errors and schema diagnostics may contain private operator text, so only
    fixed issue codes leave this function. A browser, inherited environment or
    copied config from another workspace cannot choose the expected owner.
    """
    if not filename or workspace_id is None:
        return WorkspaceLocalModelEndpoints()
    try:
        path = Path(filename)
        if not path.is_absolute() or path.is_symlink() or path.resolve() != path.absolute():
            raise ValueError()
        if not path.exists():
            return WorkspaceLocalModelEndpoints()
        if not path.is_file() or path.stat().st_size > 16384:
            raise ValueError()
        with path.open('rb') as stream:
            content = stream.read(16385)
        if len(content) > 16384:
            raise ValueError()
        decoded = json.loads(content, object_pairs_hook=_unique_fields, parse_constant=_invalid_number)
        config = WorkspaceLocalModelConfiguration.model_validate(decoded)
        if config.workspace_id != workspace_id:
            raise ValueError()
        if not config.enabled:
            return WorkspaceLocalModelEndpoints(issue='disabled')
        return WorkspaceLocalModelEndpoints(issue='configured', agent_text_ports=frozenset(config.agent_text_ports),
            asset_image_ports=frozenset(config.asset_image_ports), video_understanding_ports=frozenset(config.video_understanding_ports),
            native_video_port=config.native_video_port)
    except Exception:
        return WorkspaceLocalModelEndpoints(issue='invalid_configuration')
