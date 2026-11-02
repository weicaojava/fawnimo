"""Bounded single-result Seedream profiles with exact reference bytes and credential-free CDN downloads."""
import asyncio
import base64
from dataclasses import replace
import hashlib
import json
import math
import re
from urllib.parse import urlsplit

from app.core.contracts.asset_image_protocols import (
    AssetImageProtocol, SEEDREAM_IMAGE_PROTOCOL, SEEDREAM_MODELS, SEEDREAM_SIZES, result_host_policy,
)
from app.core.integrations.asset_image import AssetImageModelError
from app.core.integrations.trusted_http import request_isolated_async
from app.core.integrations.verified_image import verify_image_bytes
from app.core.owned_tasks import join_tasks

PURPOSES = {'appearance': '外观与主体细节', 'composition': '构图与空间关系', 'style': '画面风格与色调'}


def strict_json(content):
    """Reject duplicate response fields and nonfinite values before interpreting the one permitted result."""
    def unique(pairs):
        """A provider cannot supply competing interpretations of one response field."""
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError()
            result[key] = value
        return result

    def invalid(_value):
        """JSON infinity and NaN are not protocol values."""
        raise ValueError()

    def finite(value):
        """Exponent overflow is also nonfinite JSON data, even without a literal Infinity token."""
        result = float(value)
        if not math.isfinite(result):
            raise ValueError()
        return result

    return json.loads(content, object_pairs_hook=unique, parse_constant=invalid, parse_float=finite)


async def owned_request(*args, **kwargs):
    """Cancel the transport once and shield its actual child-process cleanup from repeated owner cancellation."""
    task = asyncio.create_task(request_isolated_async(*args, **kwargs))
    try:
        return await asyncio.shield(task)
    finally:
        if not task.done():
            await join_tasks(task, cancel=True)


async def record_response(on_response, request_id):
    """Join the original charge's response witness before interpreting or downloading any output."""
    if on_response is not None:
        task = asyncio.create_task(on_response(request_id))
        result = (await join_tasks(task))[0]
        if isinstance(result, BaseException):
            raise result


def request_identity(response, secret):
    """Keep only a bounded private request identifier; keys and unsafe upstream values are never evidence."""
    value = response.headers.get('x-request-id')
    return value if isinstance(value, str) and re.fullmatch(r'[A-Za-z0-9_.:-]{1,128}', value) and value != secret else None


def result_url(value, hosts):
    """Require an exact sealed HTTPS host at port443; response data cannot grant local or alternate-port access."""
    if not isinstance(value, str) or not 1 <= len(value) <= 4096 or value != value.strip() or any(ord(c) < 33 for c in value):
        raise ValueError()
    parsed = urlsplit(value)
    if parsed.scheme != 'https' or parsed.hostname not in hosts or parsed.port not in {None, 443} or parsed.username or parsed.password or parsed.fragment or '%' in (parsed.hostname or ''):
        raise ValueError()
    return value


async def invoke_seedream_image(*, base_url, api_key, model, prompt, size,
        protocol: AssetImageProtocol, references=(), response_format='b64_json',
        trusted_local_host=None, trusted_local_ports=frozenset(), on_response=None):
    """Send only this exact declared model profile and verified in-memory inputs; never fall back to another format."""
    parsed = urlsplit(base_url)
    if not parsed.hostname or parsed.query or parsed.fragment or parsed.username or parsed.password or protocol.protocol != SEEDREAM_IMAGE_PROTOCOL or SEEDREAM_MODELS.get(model) != protocol.profile or protocol.sizes != SEEDREAM_SIZES or protocol.max_references != 3:
        raise AssetImageModelError('图片模型或协议配置无效，未发起请求')
    if protocol.result_hosts != result_host_policy(list(protocol.result_hosts)) or size not in protocol.sizes or response_format not in protocol.response_formats or len(references) > 3:
        raise AssetImageModelError('图片输入、返回方式或下载范围不受本协议支持')
    image_values, instructions, total = [], [], 0
    for position, (proof, purpose) in enumerate(references):
        total += len(proof.content)
        if purpose not in PURPOSES or len(proof.content) > 4 * 1024 * 1024 or hashlib.sha256(proof.content).hexdigest() != proof.sha256 or proof.mime not in {'image/png', 'image/jpeg'} or min(proof.width, proof.height) < 15 or max(proof.width, proof.height) > 4096 or proof.width * proof.height > 16_000_000 or not 1/16 <= proof.width/proof.height <= 16:
            raise AssetImageModelError('参考图片证明或用途无效，未发起请求')
        image_values.append('data:' + proof.mime + ';base64,' + base64.b64encode(proof.content).decode('ascii'))
        instructions.append(f'参考图{position+1}用于{PURPOSES[purpose]}。')
    if total > 12 * 1024 * 1024 or not isinstance(prompt, str) or not prompt.strip() or len(prompt) > 8000:
        raise AssetImageModelError('图片输入超过本协议资源上限，未发起请求')
    payload = {'model': model, 'prompt': prompt + ('\n\n参考图用途说明：' + ''.join(instructions) if instructions else ''),
               'size': size, 'response_format': response_format, 'watermark': True}
    if image_values:
        payload['image'] = image_values
    if protocol.profile == 'single_v4':
        payload.update(sequential_image_generation='disabled', stream=False)
    content = json.dumps(payload, ensure_ascii=False, separators=(',', ':'), allow_nan=False).encode('utf-8')
    if len(content) > 18 * 1024 * 1024:
        raise AssetImageModelError('图片请求超过本协议资源上限，未发起请求')
    try:
        response = await owned_request(base_url.rstrip('/') + '/images/generations', allowed_hosts={parsed.hostname.lower()},
            trusted_local_host=trusted_local_host, trusted_local_ports=set(trusted_local_ports), method='POST', data=content,
            headers={'Authorization': f'Bearer {api_key}', 'Content-Type': 'application/json', 'Accept': 'application/json'},
            timeout=60, max_bytes=12 * 1024 * 1024, max_redirects=0)
    except Exception:
        raise AssetImageModelError('图片请求失败或结果未知；不会自动重发，原项目费用仍需核对') from None
    request_id = request_identity(response, api_key)
    await record_response(on_response, request_id)
    if not 200 <= response.status < 300:
        raise AssetImageModelError(f'图片模型返回 HTTP {response.status}；未创建候选，原项目费用仍需核对', provider_request_id=request_id)
    try:
        if response.headers.get('content-type', '').split(';', 1)[0].strip().lower() != 'application/json':
            raise ValueError()
        data = strict_json(response.body)
        if not isinstance(data, dict) or data.get('error') or ('model' in data and data['model'] != model):
            raise ValueError()
        items = data['data']
        if not isinstance(items, list) or len(items) != 1 or not isinstance(items[0], dict) or items[0].get('error'):
            raise ValueError()
        item = items[0]
        if response_format == 'b64_json':
            if 'url' in item or not isinstance(item.get('b64_json'), str) or len(item['b64_json']) > 12 * 1024 * 1024:
                raise ValueError()
            image_content = base64.b64decode(item['b64_json'], validate=True)
            declared_mime = None
        else:
            if 'b64_json' in item:
                raise ValueError()
            url = result_url(item.get('url'), protocol.result_hosts)
            # The separate GET deliberately has no model-local exception, secret,
            # cookies, redirect allowance or ability to broaden the sealed hosts.
            downloaded = await owned_request(url, allowed_hosts=set(protocol.result_hosts), method='GET',
                headers={'Accept': 'image/png, image/jpeg'}, timeout=15, max_bytes=8 * 1024 * 1024, max_redirects=0)
            declared_mime = downloaded.headers.get('content-type', '').split(';', 1)[0].strip().lower()
            if downloaded.status != 200 or declared_mime not in {'image/png', 'image/jpeg'}:
                raise ValueError()
            image_content = downloaded.body
        proof = await verify_image_bytes(image_content)
        if (proof.width, proof.height) != tuple(map(int, size.split('x'))) or (item.get('size') is not None and item['size'] != f'{proof.width}x{proof.height}') or (declared_mime and declared_mime != proof.mime):
            raise ValueError()
        return replace(proof, provider_request_id=request_id)
    except Exception:
        raise AssetImageModelError('图片响应、下载或实际尺寸未通过本协议校验，未创建候选；原项目费用仍需核对', provider_request_id=request_id) from None
