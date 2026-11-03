"""Official retired-video refusal before credentials/network, with separate authored compatibility origins and retained local history."""
from types import SimpleNamespace

import httpx
import pytest
from fastapi import HTTPException

from app.core.contracts.provider import ProviderConfig
from app.core.contracts.video_generation import VideoGenerationInput
from app.core.integrations.openai.video import OpenAIVideoApiAdapter
from app.models.llm import ModelCategoryKey
from app.services.llm.provider_resolver import resolve_provider_config_from_provider


class UnreadCredential:
    """Reading a credential for an unavailable official service is itself a failed preflight."""
    name='OpenAI'
    status='enabled'
    id='authored-retired-provider'
    image_base_url=None
    video_base_url=None

    def __init__(self,base_url):
        """Select only the authored endpoint; never supply a real provider key."""
        self.base_url=base_url

    @property
    def api_key(self):
        """A known retired service must fail before requesting its existing secret."""
        raise AssertionError('Retired service read a credential')


@pytest.mark.parametrize('base',[None,'https://api.openai.com/v1','https://API.OPENAI.COM/v1/',
    'https://api.openai.com.:443/v1','https://api.openai.com/compat/v1'])
@pytest.mark.parametrize('operation',['create','get'])
@pytest.mark.asyncio
async def test_official_retired_video_rejected_before_client_or_key(base,operation,monkeypatch):
    """Fresh create and remote queries fail with a clear retirement reason and zero credential/client access."""
    def no_client(*_args,**_kwargs):
        """Constructing a network client for the closed official service is a regression."""
        raise AssertionError('Retired service constructed an HTTP client')
    monkeypatch.setattr(httpx,'AsyncClient',no_client)
    adapter=OpenAIVideoApiAdapter();cfg=UnreadCredential(base)
    with pytest.raises(ValueError,match='2026-09-24'):
        if operation=='create':
            await adapter.create_video(cfg=cfg,input_=VideoGenerationInput(prompt='authored',ratio='9:16',seconds=4),timeout_s=1)
        else:
            await adapter.get_video(cfg=cfg,video_id='old-video-id',timeout_s=1)


@pytest.mark.parametrize('base',[None,'https://api.openai.com/v1','https://API.OPENAI.COM:443/v1/'])
def test_official_model_preflight_returns_unavailable_before_key(base):
    """Normal service resolution exposes a fixed503 retirement message before a stored provider key can be read."""
    with pytest.raises(HTTPException) as caught:
        resolve_provider_config_from_provider(provider=UnreadCredential(base),category=ModelCategoryKey.video)
    assert caught.value.status_code==503 and '2026-09-24' in caught.value.detail


def test_explicit_category_override_uses_its_own_origin_without_reassigning_provider():
    """A separately configured compatibility service is not mistaken for the retired official host."""
    provider=SimpleNamespace(name='OpenAI',status='enabled',id='authored-compatible',api_key='authored-test-key',
        base_url='https://api.openai.com/v1',video_base_url='http://127.0.0.1:18999/v1',image_base_url=None)
    resolved=resolve_provider_config_from_provider(provider=provider,category=ModelCategoryKey.video)
    assert resolved.base_url==provider.video_base_url and resolved.api_key=='authored-test-key'
    assert provider.base_url=='https://api.openai.com/v1'


@pytest.mark.parametrize('category',[ModelCategoryKey.text,ModelCategoryKey.image,ModelCategoryKey.audio])
def test_other_openai_categories_retain_their_existing_resolution(category):
    """Official video retirement does not disable other categories or rewrite provider configuration."""
    provider=SimpleNamespace(name='OpenAI',status='enabled',id='authored-other-category',api_key='authored-test-key',
        base_url='https://api.openai.com/v1',image_base_url=None,video_base_url=None)
    resolved=resolve_provider_config_from_provider(provider=provider,category=category)
    assert resolved.base_url==provider.base_url and resolved.provider_key=='openai'


@pytest.mark.parametrize('operation',['create','get'])
@pytest.mark.asyncio
async def test_malformed_origin_uses_fixed_error_without_echoing_embedded_private_text(operation):
    """URL parser errors cannot disclose an authored user-info marker in a public configuration failure."""
    cfg=UnreadCredential('https://operator:AUTHORED_PRIVATE@api.openai.com\uff0fprivate')
    with pytest.raises(ValueError) as caught:
        if operation=='create':
            await OpenAIVideoApiAdapter().create_video(cfg=cfg,input_=VideoGenerationInput(prompt='authored',ratio='9:16'),timeout_s=1)
        else:
            await OpenAIVideoApiAdapter().get_video(cfg=cfg,video_id='old-video-id',timeout_s=1)
    assert str(caught.value)=='视频服务地址无效，请检查供应商配置。'
    assert 'AUTHORED_PRIVATE' not in str(caught.value)


def test_malformed_origin_resolver_never_echoes_embedded_private_text():
    """The public503 message stays fixed even when a URL parser would normally include the rejected netloc."""
    provider=UnreadCredential('https://operator:AUTHORED_PRIVATE@api.openai.com\uff0fprivate')
    with pytest.raises(HTTPException) as caught:
        resolve_provider_config_from_provider(provider=provider,category=ModelCategoryKey.video)
    assert caught.value.status_code==503 and caught.value.detail=='视频服务地址无效，请检查供应商配置。'
    assert 'AUTHORED_PRIVATE' not in caught.value.detail
