"""Transport-equivalent official origin checks reject Unicode-dot aliases before credentials or a client exists."""
import httpx
import pytest
from fastapi import HTTPException

from app.core.contracts.video_generation import VideoGenerationInput
from app.core.integrations.openai.video import OpenAIVideoApiAdapter
from app.models.llm import ModelCategoryKey
from app.services.llm.provider_resolver import resolve_provider_config_from_provider
from tests.test_retired_openai_video import UnreadCredential

ORIGINS=[f'https://{host}/v1' for dot in ('\u3002','\uff0e','\uff61')
    for host in (f'api{dot}openai.com',f'api.openai{dot}com',f'api.openai.com{dot}')]


@pytest.mark.asyncio
@pytest.mark.parametrize('base',ORIGINS,ids=[f'alias-{index}' for index in range(len(ORIGINS))])
@pytest.mark.parametrize('operation',['resolve','create','get'])
async def test_transport_equivalent_retired_origins_stop_before_key_and_client(base,operation,monkeypatch):
    """The check uses the same IDNA host as HTTPX, while retaining the fixed unavailable message and doing no network work."""
    assert httpx.URL(base).host.rstrip('.')=='api.openai.com'
    def no_client(*_args,**_kwargs):
        """A normalized official alias must not construct any network client."""
        raise AssertionError('Retired alias constructed a client')
    monkeypatch.setattr(httpx,'AsyncClient',no_client)
    cfg=UnreadCredential(base)
    if operation=='resolve':
        with pytest.raises(HTTPException) as caught:
            resolve_provider_config_from_provider(provider=cfg,category=ModelCategoryKey.video)
        assert caught.value.status_code==503 and '2026-09-24' in caught.value.detail
    else:
        with pytest.raises(ValueError,match='2026-09-24'):
            if operation=='create':
                await OpenAIVideoApiAdapter().create_video(cfg=cfg,input_=VideoGenerationInput(prompt='Authored alias fixture',ratio='9:16'),timeout_s=1)
            else:await OpenAIVideoApiAdapter().get_video(cfg=cfg,video_id='authored-old-task',timeout_s=1)
