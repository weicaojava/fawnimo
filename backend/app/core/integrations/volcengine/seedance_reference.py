"""Send only explicit prepared conditions through a separately declared one-create native transport."""
import asyncio

from app.core.contracts.execution_control import RejectedSubmission
from app.core.contracts.seedance25 import CREATE_PATH, valid_remote_id
from app.core.contracts.seedance_reference_profile import SeedanceReferenceConfiguration
from app.core.contracts.seedance_references import PreparedReference, VideoReferenceSelection, snapshot_reference_selection
from app.core.integrations.volcengine.seedance25 import Seedance25DurableTransport, Seedance25ProtocolError
from app.core.integrations.volcengine.seedance_reference_payload import build_seedance_reference_body
from app.core.owned_tasks import join_tasks


class SeedanceReferenceTransport(Seedance25DurableTransport):
    """Reuse original-account polling/download, while reference creation requires these exact server-prepared inputs."""
    def __init__(self, config: SeedanceReferenceConfiguration, *, project_id: str | None = None,
                 selection: VideoReferenceSelection | None = None, references: tuple[PreparedReference, ...] | None = None,
                 on_submission_response=None, **options):
        """Construction does no I/O; a control-only descriptor has no ability to create a reference task."""
        if not isinstance(config, SeedanceReferenceConfiguration):
            raise TypeError('A separate trusted reference profile is required')
        self._project_id = project_id
        self._selection = selection
        self._references = references

        async def joined_response(request_id):
            """Preserve the original POST witness through owner cancellation before parsing or accepting its task handle."""
            if on_submission_response is not None:
                task = asyncio.create_task(on_submission_response(request_id))
                result = (await join_tasks(task))[0]
                if isinstance(result, BaseException):
                    raise result
        super().__init__(config, on_submission_response=joined_response, **options)

    async def submit(self, snapshot: dict, request_id: str) -> str:
        """Perform one create with exact in-memory images/scoped video delivery, never an implicit text fallback."""
        try:
            if (not isinstance(snapshot, dict)
                    or set(snapshot)-{'model_id', 'duration', 'references', 'prompt', 'prompt_revision', 'video_reference'}
                    or self._selection is None or self._references is None
                    or snapshot_reference_selection(snapshot) != self._selection):
                raise ValueError()
            body = build_seedance_reference_body(project_id=self._project_id, model_id=snapshot.get('model_id'),
                prompt=snapshot.get('prompt'), duration=snapshot.get('duration'), selection=self._selection,
                prepared=self._references, delivery_origin=self.config.input_delivery_origin,
                trusted_local_delivery_origin=self.config.trusted_local_media_origin)
        except (TypeError, ValueError):
            raise RejectedSubmission('Unsupported original-project reference request') from None
        result = await self._control(CREATE_PATH, method='POST', body=body)
        remote_id = result.get('id')
        if not valid_remote_id(remote_id) or self.config.api_key in remote_id:
            raise Seedance25ProtocolError('Missing or invalid provider identity')
        return remote_id
