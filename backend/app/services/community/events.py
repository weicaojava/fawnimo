"""Verify exact GitHub webhook bytes and project only explicitly public issue requirement facts."""
from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import hmac
import json
import math
import re
import uuid

from app.core.contracts.community_requests import CommunityIntakeConfiguration

MAX_DELIVERY_BYTES = 1024 * 1024
ISSUE_ACTIONS = frozenset({'opened', 'edited', 'reopened', 'closed', 'labeled', 'unlabeled'})


class InvalidCommunityEvent(ValueError):
    """Signal an invalid public event without reflecting its body, signature or private operator setup."""


@dataclass(frozen=True)
class PublicIssueVersion:
    """Store a canonical source version as untrusted requirement data, never executable agent instructions."""
    issue_number: int
    github_issue_id: int
    author_id: int
    updated_us: int
    title: str
    body: str
    labels: tuple[str, ...]
    state: str
    eligible: bool
    fingerprint: str


def verify_github_signature(secret: bytes, signature: str, content: bytes) -> bool:
    """Use GitHub's SHA-256 HMAC over the original UTF-8 bytes with constant-time comparison."""
    if not isinstance(signature, str) or re.fullmatch(r'sha256=[0-9a-f]{64}', signature) is None:
        return False
    expected = 'sha256=' + hmac.new(secret, content, hashlib.sha256).hexdigest()
    return hmac.compare_digest(signature, expected)


def delivery_identity(value: str) -> str:
    """Accept one canonical opaque GitHub delivery UUID, never a path, command or caller database selector."""
    if not isinstance(value, str) or len(value) != 36:
        raise InvalidCommunityEvent('Invalid event identity')
    try:
        result = str(uuid.UUID(value))
    except (ValueError, AttributeError):
        raise InvalidCommunityEvent('Invalid event identity') from None
    if result != value.lower():
        raise InvalidCommunityEvent('Invalid event identity')
    return result


def unique_fields(pairs):
    """Reject duplicate JSON fields before target, visibility or source eligibility can be interpreted ambiguously."""
    result = {}
    for name, value in pairs:
        if name in result:
            raise InvalidCommunityEvent('Ambiguous event fields')
        result[name] = value
    return result


def invalid_number(_value):
    """Only finite strict JSON is accepted; malformed payload contents are not reflected in errors."""
    raise InvalidCommunityEvent('Invalid event number')


def finite_float(value):
    """Reject exponent overflow as well as explicit NaN/Infinity, including in otherwise unused GitHub fields."""
    result = float(value)
    if not math.isfinite(result):
        raise InvalidCommunityEvent('Invalid event number')
    return result


def positive_id(value) -> int:
    """Reject boolean or string identifiers instead of silently selecting a different repository or issue."""
    if type(value) is not int or not 1 <= value <= 9223372036854775806:
        raise InvalidCommunityEvent('Invalid source identity')
    return value


def updated_clock(value) -> int:
    """Compare actual source clocks, keeping same-clock conflicts explicit rather than inventing event order."""
    if not isinstance(value, str) or not re.fullmatch(r'\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d(?:\.\d{1,6})?Z', value):
        raise InvalidCommunityEvent('Invalid source timestamp')
    try:
        moment = datetime.fromisoformat(value[:-1] + '+00:00')
        difference = moment - datetime(1970, 1, 1, tzinfo=timezone.utc)
        result = (difference.days * 86400 + difference.seconds) * 1_000_000 + difference.microseconds
        if not 0 <= result <= 253402300799999999:
            raise ValueError()
        return result
    except ValueError:
        raise InvalidCommunityEvent('Invalid source timestamp') from None


def project_public_issue(content: bytes, event: str, config: CommunityIntakeConfiguration):
    """Return only one bounded public target issue version, ignoring unsupported events without importing their data."""
    if not content or len(content) > MAX_DELIVERY_BYTES:
        raise InvalidCommunityEvent('Invalid event size')
    try:
        payload = json.loads(content.decode('utf-8'), object_pairs_hook=unique_fields, parse_constant=invalid_number, parse_float=finite_float)
    except (ValueError, UnicodeError, RecursionError):
        raise InvalidCommunityEvent('Invalid event JSON') from None
    if not isinstance(payload, dict):
        raise InvalidCommunityEvent('Invalid event structure')
    repository = payload.get('repository')
    if (not isinstance(repository, dict) or positive_id(repository.get('id')) != config.repository_id
            or not isinstance(repository.get('full_name'), str) or repository['full_name'].casefold() != config.repository_name.casefold()
            or repository.get('private') is not False):
        raise InvalidCommunityEvent('Wrong or nonpublic source repository')
    action = payload.get('action', '')
    if not isinstance(action, str) or len(action) > 64:
        raise InvalidCommunityEvent('Invalid event action')
    if event != 'issues' or action not in ISSUE_ACTIONS:
        return action, None
    issue = payload.get('issue')
    if not isinstance(issue, dict) or 'pull_request' in issue:
        raise InvalidCommunityEvent('Invalid issue source')
    number, identifier = positive_id(issue.get('number')), positive_id(issue.get('id'))
    author = issue.get('user')
    if not isinstance(author, dict) or author.get('type') != 'User':
        return action, None
    author_id = positive_id(author.get('id'))
    title, body, state = issue.get('title'), issue.get('body'), issue.get('state')
    if body is None:
        body = ''
    if (not isinstance(title, str) or not 1 <= len(title.strip()) <= 256
            or any(ord(c) < 32 for c in title) or not isinstance(body, str)
            or not isinstance(state, str) or state not in {'open', 'closed'}):
        raise InvalidCommunityEvent('Invalid public requirement text')
    try:
        if len(title.encode('utf-8')) > 1024 or len(body.encode('utf-8')) > 65536:
            raise InvalidCommunityEvent('Invalid public requirement size')
    except UnicodeError:
        raise InvalidCommunityEvent('Invalid public requirement encoding') from None
    raw_labels = issue.get('labels')
    if not isinstance(raw_labels, list) or len(raw_labels) > 100:
        raise InvalidCommunityEvent('Invalid issue labels')
    labels = []
    for row in raw_labels:
        name = row.get('name') if isinstance(row, dict) else None
        if not isinstance(name, str) or not 1 <= len(name) <= 100 or any(ord(c) < 32 for c in name):
            raise InvalidCommunityEvent('Invalid issue labels')
        try:
            name.encode('utf-8')
        except UnicodeError:
            raise InvalidCommunityEvent('Invalid issue label encoding') from None
        labels.append(name)
    labels = tuple(sorted(set(labels)))
    eligible = config.proposal_label in labels and state == 'open'
    updated = updated_clock(issue.get('updated_at'))
    facts = {'issue_number': number, 'github_issue_id': identifier, 'author_id': author_id, 'updated_us': updated,
        'title': title, 'body': body, 'labels': labels, 'state': state, 'eligible': eligible}
    fingerprint = hashlib.sha256(json.dumps(facts, ensure_ascii=False, sort_keys=True, separators=(',', ':')).encode()).hexdigest()
    return action, PublicIssueVersion(**facts, fingerprint=fingerprint)
