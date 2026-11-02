"""Read only the fixed public GitHub source, refusing redirects, private sources and stale local versions."""
from dataclasses import dataclass
import hashlib
import http.client
import json
import os
from pathlib import Path
import ssl
import sys
import time

from app.core.contracts.community_requests import CommunityIntakeConfiguration
from app.services.community.events import (PublicIssueVersion, InvalidCommunityEvent, MAX_DELIVERY_BYTES,
    project_public_issue, unique_fields, invalid_number, finite_float, positive_id)

PROTOCOL = 'github-public-rest-2022-11-28'


@dataclass(frozen=True)
class PublicSourceProof:
    """A fresh observation of one explicit public source; it is never continuing execution permission."""
    repository_id: int
    repository_name: str
    issue: PublicIssueVersion
    checked_at_us: int
    response_sha256: tuple[str, str]
    protocol: str = PROTOCOL

    def receipt(self):
        """Persist only observed identities and byte hashes; the original requirement already has an immutable version."""
        return {'protocol':self.protocol, 'repository_id':self.repository_id, 'repository_name':self.repository_name.casefold(),
            'issue_number':self.issue.issue_number, 'github_issue_id':self.issue.github_issue_id, 'author_id':self.issue.author_id,
            'fingerprint':self.issue.fingerprint, 'updated_us':self.issue.updated_us, 'checked_at_us':self.checked_at_us,
            'response_sha256':list(self.response_sha256)}


def parse_public_json(raw):
    """Reject duplicate keys, invalid UTF8 and nonfinite values in the entire bounded response."""
    if not isinstance(raw, bytes) or not 1 <= len(raw) <= MAX_DELIVERY_BYTES: raise InvalidCommunityEvent('Invalid public response size')
    try:
        value=json.loads(raw.decode('utf8'),object_pairs_hook=unique_fields,parse_constant=invalid_number,parse_float=finite_float)
    except (ValueError,UnicodeError,RecursionError): raise InvalidCommunityEvent('Invalid public response') from None
    if not isinstance(value,dict):raise InvalidCommunityEvent('Invalid public response')
    return value


def project_current_public_issue(config, number, repository_raw, issue_raw, *, clock_us=None):
    """Bind repository numeric identity and current Issue URLs/text/clock to the same original public source."""
    positive_id(number)
    repository=parse_public_json(repository_raw);issue=parse_public_json(issue_raw)
    name=config.repository_name.casefold()
    if (positive_id(repository.get('id'))!=config.repository_id or not isinstance(repository.get('full_name'),str) or repository['full_name'].casefold()!=name
        or repository.get('private') is not False or repository.get('visibility')!='public'
        or repository.get('archived') is not False or repository.get('disabled') is not False or repository.get('has_issues') is not True):
        raise InvalidCommunityEvent('Public repository is not currently eligible')
    if (issue.get('number')!=number or issue.get('locked') is not False
        or not isinstance(issue.get('html_url'),str) or issue['html_url'].casefold()!=f'https://github.com/{name}/issues/{number}'
        or not isinstance(issue.get('repository_url'),str) or issue['repository_url'].casefold()!=f'https://api.github.com/repos/{name}'
        or not isinstance(issue.get('url'),str) or issue['url'].casefold()!=f'https://api.github.com/repos/{name}/issues/{number}'):
        raise InvalidCommunityEvent('Issue moved, locked or source identity changed')
    body=json.dumps({'action':'edited','repository':repository,'issue':issue},ensure_ascii=False).encode('utf8')
    _,version=project_public_issue(body,'issues',config)
    if version is None:raise InvalidCommunityEvent('Public Issue is not a human requirement')
    moment=time.time_ns()//1000 if clock_us is None else clock_us
    if type(moment) is not int or moment<0:raise InvalidCommunityEvent('Invalid source observation clock')
    return PublicSourceProof(config.repository_id,name,version,moment,
        (hashlib.sha256(repository_raw).hexdigest(),hashlib.sha256(issue_raw).hexdigest()))


def github_public_get(path):
    """Fixed-host, no-credential HTTPS GET only, with finite bytes/time and no redirects or automatic retries."""
    connection=http.client.HTTPSConnection('api.github.com',timeout=3,context=ssl.create_default_context())
    deadline=time.monotonic()+10
    try:
        connection.request('GET',path,headers={'Accept':'application/vnd.github+json','X-GitHub-Api-Version':'2022-11-28',
            'User-Agent':'Fawnimo-public-source-check','Accept-Encoding':'identity'})
        response=connection.getresponse()
        if response.status!=200:raise InvalidCommunityEvent('Public source unavailable; no automatic retry')
        if response.getheader('Content-Encoding') or response.getheader('Content-Type','').split(';',1)[0].strip().casefold()!='application/json':
            raise InvalidCommunityEvent('Unsupported public response')
        result=bytearray()
        while True:
            if time.monotonic()>deadline:raise InvalidCommunityEvent('Public source time bound exceeded')
            part=response.read(min(16384,MAX_DELIVERY_BYTES+1-len(result)))
            if not part:break
            result.extend(part)
            if len(result)>MAX_DELIVERY_BYTES:raise InvalidCommunityEvent('Public source response too large')
        return bytes(result)
    finally:connection.close()


def inspect_public_issue(config, number, *, reader=None, clock_us=None):
    """Perform sequential fixed repository and Issue reads; injected readers are trusted test code, never client configuration."""
    positive_id(number)
    get=reader or github_public_get
    prefix='/repos/'+config.repository_name
    return project_current_public_issue(config,number,get(prefix),get(prefix+'/issues/'+str(number)),clock_us=clock_us)


async def verify_public_issue(config, number):
    """Run network verification in an owned20-second child with no studio/model/Git credentials or DB configuration."""
    from app.core.owned_process import run_owned_process
    allowed={'SYSTEMROOT','WINDIR','COMSPEC','PATH','PATHEXT','TEMP','TMP','TMPDIR','LANG','LC_ALL','TZ'}
    environment={key:value for key,value in os.environ.items() if key.upper() in allowed}
    environment.update(PYTHONUTF8='1',PYTHONDONTWRITEBYTECODE='1')
    raw,code=await run_owned_process([sys.executable,str(Path(__file__).with_name('source_check_child.py'))],
        json.dumps({'configuration':config.model_dump(),'issue_number':positive_id(number)}).encode(),
        timeout=20,max_stdout_bytes=131072,environment=environment)
    try:
        value=parse_public_json(raw)
        if code or value.get('error') or set(value)!={'repository_id','repository_name','issue','checked_at_us','response_sha256','protocol'}:
            raise ValueError()
        return PublicSourceProof(repository_id=value['repository_id'],repository_name=value['repository_name'],
            issue=PublicIssueVersion(**value['issue']),checked_at_us=value['checked_at_us'],
            response_sha256=tuple(value['response_sha256']),protocol=value['protocol'])
    except (ValueError,TypeError,KeyError):raise InvalidCommunityEvent('Public source could not be verified') from None
