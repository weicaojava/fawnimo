"""Bind a short-lived one-video capability to an explicit fixed workspace and application-owned origin."""
from dataclasses import dataclass,field
import hashlib
import hmac
import math
import re
import time

from app.core.contracts.seedance_reference_profile import checked_delivery_origin
from app.core.workspace_boundary import validate_workspace_id

WORKSPACE_REFERENCE_PREFIX='/api/v1/workspace-reference-media/'
WORKSPACE_REFERENCE_PROTOCOL='workspace_reference_delivery_v1'
DELIVERY_SECONDS=3600


def workspace_reference_key(master:bytes,workspace_id:str)->bytes:
    """Derive only this space's signing role; never send the gateway master or another space's key to a worker."""
    validate_workspace_id(workspace_id)
    if type(master) is not bytes or len(master)!=32:
        raise ValueError('A fixed private reference master key is required')
    return hmac.new(master,b'fawnimo/workspace-reference/key/v1\n'+workspace_id.encode('ascii'),hashlib.sha256).digest()


@dataclass(frozen=True,slots=True)
class WorkspaceReferencePolicy:
    """Carry one private startup policy; public profile descriptors exclude all signing material and key digests."""

    workspace_id:str
    origin:str
    signing_key:bytes=field(repr=False)
    trusted_local_origin:str|None=None

    def __post_init__(self):
        """Validate scope, independent origin exception and key before any worker data or service is created."""
        validate_workspace_id(self.workspace_id)
        checked_delivery_origin(self.origin,trusted_local_origin=self.trusted_local_origin)
        if type(self.signing_key) is not bytes or len(self.signing_key)!=32:
            raise ValueError('A fixed private workspace reference key is required')

    @classmethod
    def from_private_message(cls,value,workspace_id):
        """Accept only the bounded explicit private-pipe schema and require its scope to match the enclosing worker."""
        fields={'schema','workspace_id','origin','signing_key','trusted_local_origin'}
        if not isinstance(value,dict) or set(value)!=fields or value.get('schema')!=WORKSPACE_REFERENCE_PROTOCOL or value.get('workspace_id')!=workspace_id:
            raise ValueError('Invalid private reference delivery scope')
        key=value.get('signing_key')
        if not isinstance(key,str) or re.fullmatch(r'[a-f0-9]{64}',key) is None:
            raise ValueError('Invalid private reference delivery key')
        return cls(workspace_id,value['origin'],bytes.fromhex(key),value['trusted_local_origin'])

    def private_message(self):
        """Serialize signing material only for the already owned process stdin; never use this as an API projection."""
        return {'schema':WORKSPACE_REFERENCE_PROTOCOL,'workspace_id':self.workspace_id,'origin':self.origin,
            'signing_key':self.signing_key.hex(),'trusted_local_origin':self.trusted_local_origin}

    def public_descriptor(self):
        """Describe only the fixed wire namespace for exact execution-profile binding."""
        return {'schema':WORKSPACE_REFERENCE_PROTOCOL,'workspace_id':self.workspace_id,'path':WORKSPACE_REFERENCE_PREFIX}

    def canonical(self,delivery_id,token,expires):
        """Use one unambiguous bounded ASCII signing message; callers cannot change algorithm or inject fields."""
        if (not isinstance(delivery_id,str) or re.fullmatch(r'[a-f0-9]{32}',delivery_id) is None
                or not isinstance(token,str) or re.fullmatch(r'[a-f0-9]{64}',token) is None
                or type(expires) is not int or not 0<expires<=2**53-1):
            raise ValueError('Invalid reference delivery capability')
        return ('fawnimo/workspace-reference/link/v1\n'+self.workspace_id+'\n'+self.origin+'\n'+delivery_id+'\n'+token+'\n'+str(expires)).encode('ascii')

    def signed_url(self,delivery_id,token,created_at):
        """Create an ephemeral URL for the original prepared intent; the existing database still grants/counts actual reads."""
        if type(created_at) not in (int,float) or not math.isfinite(created_at) or created_at<0:
            raise ValueError('Invalid reference delivery creation time')
        expires=int(created_at)+DELIVERY_SECONDS
        signature=hmac.new(self.signing_key,self.canonical(delivery_id,token,expires),hashlib.sha256).hexdigest()
        return self.origin+WORKSPACE_REFERENCE_PREFIX+self.workspace_id+'/'+delivery_id+'?token='+token+'&expires='+str(expires)+'&signature='+signature

    def verifies(self,delivery_id,token,expires,signature,*,clock=time.time):
        """Check namespace/origin/message and fixed expiry before any workspace lookup, process or file read."""
        try:
            if not isinstance(signature,str) or re.fullmatch(r'[a-f0-9]{64}',signature) is None:
                return False
            message=self.canonical(delivery_id,token,expires)
            now=clock()
            if type(now) not in (int,float) or not math.isfinite(now) or not 0<=now<expires<=int(now)+DELIVERY_SECONDS:
                return False
            expected=hmac.new(self.signing_key,message,hashlib.sha256).hexdigest()
            return hmac.compare_digest(expected,signature)
        except (TypeError,ValueError,OverflowError):
            return False
