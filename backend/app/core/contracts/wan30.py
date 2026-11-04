"""Explicit bounded Beijing Wan3.0 profiles; constructor data cannot borrow another workspace's endpoint or key."""
from dataclasses import dataclass,field
import hashlib
import ipaddress
import json
import re
from types import MappingProxyType
from urllib.parse import urlsplit

MODEL_ID='wan3.0-video'
TEXT_PROTOCOL='wan30_text_v1'
REFERENCE_PROTOCOL='wan30_reference_v1'
PROTOCOLS=frozenset({TEXT_PROTOCOL,REFERENCE_PROTOCOL})
SUPPORTED_DURATIONS=frozenset(range(4,31))
CREATE_PATH='/services/aigc/video-generation/video-synthesis'
QUERY_PATH='/tasks/'
DOWNLOAD_HOSTS=frozenset({'dashscope-result-bj.oss-cn-beijing.aliyuncs.com'})
OUTPUT_PROFILE=MappingProxyType({'ratio':'9:16','resolution':'480p','output_format':'mp4','generate_audio':False,'watermark':True})
MAX_IMAGE_BYTES=4*1024*1024
MAX_REQUEST_BYTES=18*1024*1024
_VERSION=re.compile(r'[A-Za-z0-9_-]{1,128}',re.ASCII)
_WORKSPACE_HOST=re.compile(r'[a-z0-9][a-z0-9-]{0,62}\.cn-beijing\.maas\.aliyuncs\.com',re.ASCII)
_REMOTE_ID=re.compile(r'[A-Za-z0-9_-]{1,128}',re.ASCII)


def valid_remote_id(value):
    """Accept one bounded opaque task path segment without URLs, escapes or traversal."""
    return isinstance(value,str) and _REMOTE_ID.fullmatch(value) is not None


def checked_base_url(value,*,trusted_local_host=None,trusted_local_port=None):
    """Validate an explicit workspace-specific Beijing origin, or an independently named exact loopback fixture, without DNS or credentials."""
    if not isinstance(value,str) or len(value)>512:
        raise ValueError('Explicit Wan3.0 workspace video origin required')
    try:
        target=urlsplit(value)
        if trusted_local_host is None:
            if (trusted_local_port is not None or target.scheme!='https' or target.hostname is None
                    or _WORKSPACE_HOST.fullmatch(target.hostname) is None
                    or value!='https://'+target.hostname+'/api/v1'):
                raise ValueError()
        else:
            host=trusted_local_host
            loopback=host=='localhost' or isinstance(host,str) and ipaddress.ip_address(host).is_loopback
            if not loopback or type(trusted_local_port) is not int or not 1<=trusted_local_port<=65535:
                raise ValueError()
            authority='['+host+']' if ':' in host else host
            if value!=f'http://{authority}:{trusted_local_port}/api/v1':raise ValueError()
        return value
    except (ValueError,TypeError,UnicodeError):
        raise ValueError('Untrusted Wan3.0 control origin') from None


@dataclass(frozen=True,slots=True)
class Wan30Configuration:
    """Bind one model, protocol, original account version and exact origins; construction performs no I/O and never exposes the key."""
    api_key:str=field(repr=False)
    service_version:str
    account_version:str
    base_url:str
    protocol:str=TEXT_PROTOCOL
    download_hosts:frozenset[str]=DOWNLOAD_HOSTS
    trusted_local_host:str|None=None
    trusted_local_port:int|None=None

    def __post_init__(self):
        """Freeze narrow independent transport authority instead of inferring it from a model name or imported project."""
        if (not isinstance(self.api_key,str) or not 1<=len(self.api_key)<=8192 or not self.api_key.isascii()
                or any(ord(char)<=32 or ord(char)>=127 for char in self.api_key)):
            raise ValueError('Invalid trusted credential')
        if not isinstance(self.protocol,str) or self.protocol not in PROTOCOLS:
            raise ValueError('Explicit supported Wan3.0 protocol required')
        for version in (self.service_version,self.account_version):
            if not isinstance(version,str) or _VERSION.fullmatch(version) is None or version=='PREPARATION_ONLY':
                raise ValueError('Independent executable service/account version required')
        checked_base_url(self.base_url,trusted_local_host=self.trusted_local_host,trusted_local_port=self.trusted_local_port)
        if not isinstance(self.download_hosts,(set,frozenset)) or any(not isinstance(host,str) for host in self.download_hosts):
            raise ValueError('Exact Wan3.0 download hosts required')
        hosts=frozenset(self.download_hosts);object.__setattr__(self,'download_hosts',hosts)
        expected=DOWNLOAD_HOSTS if self.trusted_local_host is None else frozenset({self.trusted_local_host})
        if hosts!=expected:raise ValueError('Untrusted Wan3.0 download hosts')

    @property
    def control_host(self):
        """Return the single constructor-validated hostname used by the isolated HTTP policy."""
        return urlsplit(self.base_url).hostname

    @property
    def profile_hash(self):
        """Bind the exact supported subset, original versions and origins without producing a credential fingerprint."""
        payload={'profile':self.protocol,'model':MODEL_ID,'durations':sorted(SUPPORTED_DURATIONS),'output':dict(OUTPUT_PROFILE),
            'service_version':self.service_version,'account_version':self.account_version,'base_url':self.base_url,
            'download_hosts':sorted(self.download_hosts),'trusted_local_host':self.trusted_local_host,'trusted_local_port':self.trusted_local_port,
            'create_path':CREATE_PATH,'query_path':QUERY_PATH,'async_header':'enable','prompt_extend':False,
            'images':{'maximum':3,'bytes_each':MAX_IMAGE_BYTES,'dimensions':[300,4096],'max_pixels':16000000,
                'mime':['image/jpeg','image/png'],'frames':'exact9:16','transparency':False},
            'unsupported':['reference_video','reference_audio','file','link','seed','adaptive_duration'],
            'control':{'timeout':15,'response_bytes':65536,'redirects':0,'request_bytes':MAX_REQUEST_BYTES},
            'download':{'timeout':30,'total_timeout':45,'redirects':3},'reconciliation':'unsupported-no-resubmission','cancellation':'query-only'}
        return hashlib.sha256(json.dumps(payload,sort_keys=True,separators=(',',':')).encode('utf8')).hexdigest()

    @property
    def binding_id(self):
        """Keep Wan3.0 policy identities separate from Seedance even for identical service/account labels."""
        return 'wan30:'+self.profile_hash
