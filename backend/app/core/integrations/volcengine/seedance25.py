"""Bounded Seedance 2.5 durable protocol; qualified only with software fixtures here."""
from __future__ import annotations

import asyncio
from email.utils import parsedate_to_datetime
import json
import math
import re
import time
from urllib.parse import quote, urlsplit

from app.core.contracts.execution_artifact import DownloadedArtifact
from app.core.contracts.execution_control import QueryBackoff, RejectedSubmission
from app.core.contracts.seedance25 import (
    CREATE_PATH, MODEL_ID, Seedance25Configuration, valid_remote_id,
)
from app.core.integrations.trusted_http import HttpResult, request_isolated_async
from app.core.integrations.volcengine.seedance25_payload import build_seedance25_body

_MAX_SAFE_INTEGER = 9007199254740991
_STATUSES = frozenset({'queued', 'running', 'succeeded', 'failed', 'expired', 'cancelled'})


class Seedance25ProtocolError(RuntimeError):
    """A fixed non-sensitive error, never a raw provider response, URL or credential."""


def _unique_object(pairs: list[tuple[str, object]]) -> dict:
    """Reject duplicate JSON names instead of accepting ambiguous last-wins data."""
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError('Duplicate JSON field')
        result[key] = value
    return result


def _invalid_constant(_value: str) -> None:
    """Reject JavaScript non-finite constants allowed by Python's permissive parser."""
    raise ValueError('Invalid JSON constant')


def _strict_json(data: bytes) -> dict:
    """Decode one bounded finite Unicode JSON object without preserving error input."""
    try:
        if not isinstance(data, bytes) or len(data) > 65536:
            raise ValueError()
        result = json.loads(data.decode('utf-8'), object_pairs_hook=_unique_object,
                            parse_constant=_invalid_constant)
        if not isinstance(result, dict):
            raise ValueError()
        pending = [(result, 0)]
        count = 0
        while pending:
            value, depth = pending.pop()
            count += 1
            if depth > 24 or count > 10000:
                raise ValueError()
            if isinstance(value, dict):
                for key, child in value.items():
                    key.encode('utf-8')
                    pending.append((child, depth + 1))
            elif isinstance(value, list):
                pending.extend((child, depth + 1) for child in value)
            elif isinstance(value, str):
                value.encode('utf-8')
            elif isinstance(value, float) and not math.isfinite(value):
                raise ValueError()
        return result
    except (TypeError, ValueError, UnicodeError, RecursionError, OverflowError):
        raise Seedance25ProtocolError('Invalid provider JSON') from None


def _retry_after(headers: dict[str, str], now: float) -> int:
    """Honor safe delta-seconds or HTTP dates, with conservative malformed fallback."""
    values = [value for key, value in headers.items() if key.lower() == 'retry-after']
    if len(values) != 1 or not isinstance(values[0], str):
        return 30
    value = values[0].strip()
    try:
        if re.fullmatch(r'[0-9]+', value, re.ASCII):
            seconds = int(value)
        else:
            date = parsedate_to_datetime(value)
            if date.tzinfo is None:
                return 30
            seconds = math.ceil(date.timestamp() - now)
        if seconds > _MAX_SAFE_INTEGER:
            return 30
        return max(5, seconds)
    except (TypeError, ValueError, OverflowError, OSError):
        return 30


class Seedance25DurableTransport:
    """One-create polling adapter using only trusted origins and killable HTTP.

    Request injection is reserved for application-owned tests. Production callers
    use the existing isolated child transport; no project payload can supply it.
    """

    def __init__(self, config: Seedance25Configuration, *, requester=None, clock=time.time):
        """Keep configuration in memory; constructing the adapter performs no I/O."""
        if not isinstance(config, Seedance25Configuration):
            raise TypeError('Trusted S25A configuration required')
        self.config = config
        self._requester = requester
        self._clock = clock

    async def _http(self, url: str, **options) -> HttpResult:
        """Hide upstream exceptions while preserving real cancellation cleanup."""
        try:
            result = await (self._requester or request_isolated_async)(url, **options)
            if not isinstance(result, HttpResult):
                raise TypeError()
            return result
        except asyncio.CancelledError:
            raise
        except Exception:
            raise Seedance25ProtocolError('Provider request not confirmed') from None

    def _network_options(self, hosts: frozenset[str]) -> dict:
        """Return exact origin trust, with only the explicitly configured fixture port."""
        return {
            'allowed_hosts': set(hosts),
            'trusted_local_host': self.config.trusted_local_host,
            'trusted_local_ports': ({self.config.trusted_local_port}
                                    if self.config.trusted_local_port is not None else set()),
        }

    def _backoff(self, response: HttpResult) -> None:
        """Pause any limited read, including CDN downloads, without retaining headers."""
        headers = response.headers
        if not isinstance(headers, dict) or any(not isinstance(key, str) for key in headers):
            raise QueryBackoff(30)
        raise QueryBackoff(_retry_after(headers, self._clock()))

    async def _control(self, path: str, *, method: str = 'GET', body=None,
                       query_backoff: bool = False) -> dict:
        """Perform exactly one fixed-origin JSON request, never following redirects."""
        data = (json.dumps(body, ensure_ascii=False, separators=(',', ':'),
                           allow_nan=False).encode('utf-8') if body is not None else None)
        response = await self._http(
            self.config.base_url + path, method=method, data=data,
            headers={'Authorization': 'Bearer ' + self.config.api_key,
                     'Content-Type': 'application/json'},
            timeout=15, max_bytes=65536, max_redirects=0,
            **self._network_options(frozenset({self.config.control_host})),
        )
        if type(response.status) is not int:
            raise Seedance25ProtocolError('Invalid provider HTTP status')
        if response.status == 429 and query_backoff:
            self._backoff(response)
        if response.status != 200:
            raise Seedance25ProtocolError('Provider HTTP request not confirmed')
        return _strict_json(response.body)

    def _media_url(self, metadata: dict) -> str:
        """Validate an ephemeral signed media URL without storing or logging it."""
        content = metadata.get('content')
        url = content.get('video_url') if isinstance(content, dict) else None
        if (not isinstance(url, str) or not url or len(url) > 16384
                or any(ord(char) <= 32 or ord(char) == 127 for char in url)):
            raise Seedance25ProtocolError('Missing or invalid provider artifact')
        try:
            parsed = urlsplit(url)
            local = self.config.trusted_local_host
            expected_scheme = 'http' if local is not None else 'https'
            expected_port = self.config.trusted_local_port if local is not None else 443
            port = parsed.port or (443 if parsed.scheme == 'https' else 80)
            if (parsed.scheme != expected_scheme or parsed.hostname not in self.config.download_hosts
                    or port != expected_port or parsed.username is not None
                    or parsed.password is not None or parsed.fragment
                    or '%' in (parsed.hostname or '')):
                raise ValueError()
            url.encode('utf-8')
        except (TypeError, ValueError, UnicodeError):
            raise Seedance25ProtocolError('Untrusted provider artifact') from None
        return url

    async def _getmeta(self, remote_id: str) -> dict:
        """Query the original exact identity/model and enforce provider status shape."""
        if not valid_remote_id(remote_id):
            raise Seedance25ProtocolError('Invalid provider identity')
        result = await self._control(CREATE_PATH + '/' + quote(remote_id, safe=''),
                                     query_backoff=True)
        if (not valid_remote_id(result.get('id')) or result['id'] != remote_id
                or result.get('model') != MODEL_ID
                or not isinstance(result.get('status'), str)
                or result['status'] not in _STATUSES):
            raise Seedance25ProtocolError('Provider metadata does not match the task')
        if result['status'] == 'succeeded':
            self._media_url(result)
        return result

    async def submit(self, snapshot: dict, request_id: str) -> str:
        """Attempt one creation; only local pre-network rejection is definitely unaccepted."""
        try:
            body = build_seedance25_body(snapshot)
        except (TypeError, ValueError):
            raise RejectedSubmission('Unsupported local S25A request') from None
        result = await self._control(CREATE_PATH, method='POST', body=body)
        remote_id = result.get('id')
        if not valid_remote_id(remote_id):
            raise Seedance25ProtocolError('Missing or invalid provider identity')
        return remote_id

    async def reconcile(self, request_id: str) -> str | None:
        """No proven request-identity reconciliation exists; never retry creation."""
        return None

    async def query(self, remote_id: str) -> dict:
        """Persist only normalized state and a locator, never signed URLs or raw errors."""
        result = await self._getmeta(remote_id)
        status = result['status']
        if status in {'queued', 'running'}:
            return {'status': 'running'}
        if status == 'succeeded':
            return {'status': 'succeeded', 'locator': remote_id, 'name': 'seedance25.mp4'}
        return {'status': 'failed' if status in {'failed', 'expired'} else 'cancelled'}

    async def cancel(self, remote_id: str) -> bool:
        """Confirm only an observed cancelled state, with no remote DELETE operation."""
        result = await self._getmeta(remote_id)
        return result['status'] == 'cancelled'

    async def download(self, result: dict, media_id: str, project_id: str) -> DownloadedArtifact:
        """Refresh original metadata and retrieve bounded MP4 bytes within one deadline."""
        if (not isinstance(result, dict) or set(result) != {'status', 'locator', 'name'}
                or result.get('status') != 'succeeded' or result.get('name') != 'seedance25.mp4'
                or not valid_remote_id(result.get('locator'))):
            raise Seedance25ProtocolError('Invalid local artifact locator')
        from app.config import settings
        maximum = settings.production_upload_limit
        if type(maximum) is not int or maximum < 1:
            raise Seedance25ProtocolError('Invalid trusted artifact limit')
        try:
            async with asyncio.timeout(45):
                metadata = await self._getmeta(result['locator'])
                if metadata['status'] != 'succeeded':
                    raise Seedance25ProtocolError('Provider artifact is not ready')
                url = self._media_url(metadata)
                response = await self._http(
                    url, method='GET', headers={}, timeout=30,
                    max_bytes=maximum, max_redirects=3,
                    **self._network_options(self.config.download_hosts),
                )
                if type(response.status) is int and response.status == 429:
                    self._backoff(response)
                if (type(response.status) is not int or response.status != 200
                        or not isinstance(response.body, bytes) or not response.body
                        or len(response.body) > maximum):
                    raise Seedance25ProtocolError('Provider artifact download not confirmed')
                return DownloadedArtifact('seedance25.mp4', response.body)
        except QueryBackoff:
            raise
        except asyncio.CancelledError:
            raise
        except Exception:
            raise Seedance25ProtocolError('Provider artifact download not confirmed') from None
