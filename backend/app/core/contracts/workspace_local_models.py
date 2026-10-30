"""Private fixed-worker exceptions for authored loopback model endpoints; never a public paid-execution grant."""
from typing import Annotated, Literal
from pydantic import BaseModel, ConfigDict, Field, StrictBool, StrictInt, field_validator

LocalPort = Annotated[StrictInt, Field(ge=1024, le=65535)]


class WorkspaceLocalModelConfiguration(BaseModel):
    """Bind exact local ports to one workspace without allowing arbitrary hosts, keys or transport overrides."""
    model_config = ConfigDict(extra='forbid', frozen=True)
    schema_version: Literal[1] = 1
    workspace_id: str = Field(pattern=r'^ws_[a-f0-9]{32}$')
    enabled: StrictBool = False
    agent_text_ports: list[LocalPort] = Field(default_factory=list, max_length=8)
    asset_image_ports: list[LocalPort] = Field(default_factory=list, max_length=8)
    video_understanding_ports: list[LocalPort] = Field(default_factory=list, max_length=8)
    native_video_port: LocalPort | None = None

    @field_validator('schema_version', mode='before')
    @classmethod
    def exact_schema_version(cls, value):
        """Keep boolean or string coercions from silently selecting a configuration schema."""
        if type(value) is not int or value != 1:
            raise ValueError('Invalid local model schema')
        return value

    @field_validator('agent_text_ports', 'asset_image_ports', 'video_understanding_ports')
    @classmethod
    def distinct_ports(cls, value):
        """Reject ambiguous repeated exceptions rather than silently normalizing an operator mistake."""
        if len(value) != len(set(value)):
            raise ValueError('Duplicate local model ports')
        return value
