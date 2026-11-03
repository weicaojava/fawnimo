"""Fixed-host authenticated GitHub transport for a trusted controller, never imported by or passed to candidate execution."""
import http.client
import json
import re
import time
from urllib.parse import urlencode
from app.services.community.events import unique_fields,invalid_number,finite_float
from app.services.community.store import CommunityStoreConflict


class PublicationRemoteError(CommunityStoreConflict):
    """Expose a small status classification only, without response prose, token, path, header or request body."""
    def __init__(self,status=0):
        """HTTP failure is not proof that a mutation was rejected; callers preserve unknown write intent."""
        self.status=status; super().__init__('GitHub publication response unavailable or invalid')


class GitHubPublicationTransport:
    """Pin api.github.com, disable redirects/proxy discovery, bound the entire operation and keep the credential private to this controller."""
    def __init__(self,policy,credential_file):
        """A maintainer explicitly supplies a separate short-lived repository credential file; no environment/model/GCM credential is discovered."""
        if not policy.enabled: raise CommunityStoreConflict('Publication is disabled')
        with credential_file.open('rb') as stream: raw=stream.read(4097)
        if len(raw)>4096: raise CommunityStoreConflict('Bounded dedicated credential required')
        token=raw.decode('ascii').strip()
        if re.fullmatch(r'[A-Za-z0-9_\-\.]{20,4096}',token) is None: raise CommunityStoreConflict('Invalid dedicated GitHub credential format')
        self._token=token; self.policy=policy; self.prefix='/repos/'+policy.repository_name; self.deadline=time.monotonic()+90; self.requests=0

    def _request(self,method,path,body=None,*,missing=False):
        """Only internally constructed repository paths may use the authorization header; all bodies and responses have finite byte limits."""
        self.requests+=1; remaining=self.deadline-time.monotonic()
        if method not in {'GET','POST'} or not path.startswith(self.prefix+'/') and path!=self.prefix or '://' in path or '\r' in path or '\n' in path or remaining<=0 or self.requests>48:
            raise PublicationRemoteError()
        encoded=None if body is None else json.dumps(body,ensure_ascii=False,separators=(',',':'),allow_nan=False).encode('utf8')
        if encoded is not None and len(encoded)>2097152: raise PublicationRemoteError()
        connection=http.client.HTTPSConnection('api.github.com',timeout=min(12,remaining))
        try:
            connection.request(method,path,body=encoded,headers={'Authorization':'Bearer '+self._token,'Accept':'application/vnd.github+json',
                'Content-Type':'application/json','User-Agent':'fawnimo-maintainer-publication-v1','X-GitHub-Api-Version':'2022-11-28'})
            response=connection.getresponse(); raw=response.read(1048577)
            if missing and response.status==404: return None
            if len(raw)>1048576 or response.status not in ({200} if method=='GET' else {201}): raise PublicationRemoteError(response.status)
            return json.loads(raw.decode('utf8'),object_pairs_hook=unique_fields,parse_constant=invalid_number,parse_float=finite_float)
        except PublicationRemoteError: raise
        except (OSError,ValueError,http.client.HTTPException,RecursionError): raise PublicationRemoteError() from None
        finally: connection.close()

    def repository(self):
        """Read concrete public repository identity; a rename/redirect or private destination cannot silently receive source."""
        return self._request('GET',self.prefix)

    def ref(self,branch):
        """Read one exact ref, not a prefix match or candidate-supplied URL."""
        if branch!='main' and re.fullmatch(r'community/change-[0-9a-f]{24}',branch) is None:
            raise CommunityStoreConflict('Invalid fixed publication branch')
        return self._request('GET',self.prefix+'/git/ref/heads/'+branch,missing=True)

    def create_object(self,kind,body):
        """Create content-addressed Git objects only; callers must compare every response SHA before creating an active branch."""
        if kind not in {'blobs','trees','commits'}: raise CommunityStoreConflict('Invalid Git object kind')
        return self._request('POST',self.prefix+'/git/'+kind,body)

    def create_ref(self,branch,commit):
        """Create a new reserved branch only; there is no update, force, main push, delete, arbitrary ref or merge endpoint."""
        if re.fullmatch(r'community/change-[0-9a-f]{24}',branch) is None or re.fullmatch(r'[0-9a-f]{40}',commit) is None:
            raise CommunityStoreConflict('Invalid exact publication ref')
        return self._request('POST',self.prefix+'/git/refs',{'ref':'refs/heads/'+branch,'sha':commit})

    def pulls(self,branch):
        """Read all states for the reserved head and reject a full page rather than silently hiding an original PR."""
        if re.fullmatch(r'community/change-[0-9a-f]{24}',branch) is None: raise CommunityStoreConflict('Invalid publication branch')
        query=urlencode({'state':'all','head':self.policy.repository_name.split('/')[0]+':'+branch,'base':'main','per_page':100,'page':1})
        value=self._request('GET',self.prefix+'/pulls?'+query)
        if not isinstance(value,list) or len(value)>=100: raise PublicationRemoteError()
        return value

    def create_pull(self,branch,body):
        """Create one draft with fixed trace text; never accept a source-provided URL, prose, title override or reviewer request."""
        if re.fullmatch(r'community/change-[0-9a-f]{24}',branch) is None: raise CommunityStoreConflict('Invalid publication branch')
        return self._request('POST',self.prefix+'/pulls',{'title':'[proposal] '+body.bundle_id[:12],'head':branch,'base':'main','draft':True,'maintainer_can_modify':False,
            'body':'Public source proposal.\n\nFawnimo-Bundle: '+body.bundle_id+'\nFawnimo-Check: '+body.check_attempt_id+'\n\nIndependent review and exact public CI remain required. No merge or deployment is authorized.'})
