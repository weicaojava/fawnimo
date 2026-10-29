"""Small OpenAI-compatible text adapter using the existing bounded, cancellable HTTP transport."""
import json
import re
from dataclasses import dataclass
from urllib.parse import urlsplit

from app.core.integrations.trusted_http import request_isolated_async


class AgentModelError(RuntimeError):
    """A sanitized failure whose request outcome may be unknown and must never auto-retry."""

    def __init__(self,message,*,provider_request_id=None):
        """Keep optional private request evidence out of the user-facing exception message."""
        super().__init__(message)
        self.provider_request_id=provider_request_id


@dataclass(frozen=True)
class AgentTextResult:
    """Persist only model content and numeric usage, excluding arbitrary upstream metadata."""
    text: str
    usage: dict
    provider_request_id: str | None = None


async def invoke_agent_text(*, base_url: str, api_key: str, model: str, system: str, input_text: str, temperature: float, top_p: float, max_tokens: int, model_params: dict, timeout: int = 30, trusted_local_host: str | None = None, trusted_local_ports: frozenset[int] = frozenset()) -> AgentTextResult:
    """Make exactly one real HTTP request; no SDK retry or redirect can duplicate billable work."""
    parsed = urlsplit(base_url)
    if parsed.query or parsed.fragment or parsed.username or parsed.password or not parsed.hostname:
        raise AgentModelError('模型地址无效；未发起请求')
    payload = {'model': model, 'messages': [{'role': 'system', 'content': system}, {'role': 'user', 'content': input_text}], 'temperature': temperature, 'top_p': top_p, 'max_tokens': max_tokens, 'stream': False}
    # Restrict stored model JSON to inert sampling parameters; never route URLs or auth through it.
    for key in ('frequency_penalty', 'presence_penalty', 'seed', 'stop'):
        if key in model_params:
            payload[key] = model_params[key]
    try:
        response = await request_isolated_async(base_url.rstrip('/') + '/chat/completions', allowed_hosts={parsed.hostname.lower()}, trusted_local_host=trusted_local_host, trusted_local_ports=set(trusted_local_ports), method='POST', headers={'Authorization': f'Bearer {api_key}', 'Content-Type': 'application/json', 'Accept': 'application/json'}, data=json.dumps(payload, ensure_ascii=False).encode(), timeout=timeout, max_bytes=2 * 1024 * 1024, max_redirects=0)
    except Exception:
        raise AgentModelError('模型请求失败或超时，结果可能未知；不会自动重试，重新执行可能重复计费') from None
    request_id=response.headers.get('x-request-id')
    if not isinstance(request_id,str) or not re.fullmatch(r'[A-Za-z0-9_.:-]{1,128}',request_id) or request_id==api_key:
        request_id=None
    if response.status < 200 or response.status >= 300:
        raise AgentModelError(f'模型返回 HTTP {response.status}；不会自动重试，重新执行可能重复计费',provider_request_id=request_id)
    try:
        result = json.loads(response.body)
        content = result['choices'][0]['message']['content']
        if not isinstance(content, str) or not content.strip() or len(content) > 64000:
            raise ValueError()
        usage = result.get('usage') or {}
        safe_usage = {key: int(usage[key]) for key in ('prompt_tokens', 'completion_tokens', 'total_tokens') if isinstance(usage.get(key), int) and 0 <= usage[key] <= 10**9}
        return AgentTextResult(text=content.replace(api_key, '[redacted]') if api_key else content, usage=safe_usage,provider_request_id=request_id)
    except (ValueError, TypeError, KeyError, IndexError):
        raise AgentModelError('模型响应格式无效或内容超出限制；不会伪造输出，重新执行可能重复计费',provider_request_id=request_id) from None
