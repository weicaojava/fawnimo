"""Gateway-owned reference authority; fixed per-space private policies never contain the master key."""
import os
from pathlib import Path
import re
import secrets
import stat
import time
from urllib.parse import parse_qs

from fastapi import HTTPException

from app.core.contracts.seedance_reference_profile import checked_delivery_origin
from app.core.contracts.workspace_reference_delivery import WorkspaceReferencePolicy,workspace_reference_key


def validate_reference_origin(origin,public_origin,secure_cookies):
    """Require the explicitly owned gateway origin; HTTP is only for the independently selected local cookie mode."""
    if origin!=public_origin or type(secure_cookies) is not bool:
        raise ValueError('Reference media origin must match the configured gateway public origin')
    trusted=origin if not secure_cookies else None
    checked_delivery_origin(origin,trusted_local_origin=trusted)
    if origin is None:
        raise ValueError('Explicit reference media origin required')
    return trusted


def load_reference_master(root:Path)->bytes:
    """Create one private durable key exclusively, or read the unchanged regular single-link key without following redirects."""
    root=root.resolve()
    path=root/'.reference-delivery.key'
    if not root.is_dir() or path.resolve()!=path:
        raise ValueError('Invalid fixed reference key storage')
    try:
        descriptor=os.open(path,os.O_WRONLY|os.O_CREAT|os.O_EXCL|getattr(os,'O_BINARY',0),0o600)
    except FileExistsError:
        pass
    else:
        with os.fdopen(descriptor,'wb') as stream:
            stream.write(secrets.token_bytes(32));stream.flush();os.fsync(stream.fileno())
        if os.name!='nt':
            directory=os.open(root,os.O_RDONLY|getattr(os,'O_DIRECTORY',0))
            try:os.fsync(directory)
            finally:os.close(directory)
    if path.resolve()!=path:
        raise ValueError('Invalid fixed reference key storage')
    descriptor=os.open(path,os.O_RDONLY|getattr(os,'O_NOFOLLOW',0)|getattr(os,'O_NONBLOCK',0)|getattr(os,'O_BINARY',0))
    try:
        before=os.fstat(descriptor)
        # Offline restoration creates a private0700 root and fresh ordinary
        # files. Its key remains private through that enclosing directory even
        # when the platform's file-creation mask gives it broader mode bits.
        exposed=os.name!='nt' and before.st_mode&0o077 and root.stat().st_mode&0o077
        if (not stat.S_ISREG(before.st_mode) or before.st_nlink!=1 or before.st_size!=32 or exposed):
            raise ValueError('Reference key must be a fixed private regular file')
        key=os.read(descriptor,33)
        after=os.fstat(descriptor)
        if len(key)!=32 or (before.st_size,before.st_mtime_ns,before.st_ino)!=(after.st_size,after.st_mtime_ns,after.st_ino):
            raise ValueError('Reference key changed during startup')
        return key
    finally:
        os.close(descriptor)


class GatewayReferenceAuthority:
    """Authenticate a bounded one-file link before any control database lookup, worker selection or media read."""

    def __init__(self,root,origin,*,public_origin,secure_cookies,clock=time.time):
        """Initialize only after explicit origin validation and the existing identity root is prepared."""
        self.trusted_local_origin=validate_reference_origin(origin,public_origin,secure_cookies)
        self.origin=origin
        self._master=load_reference_master(Path(root))
        self.clock=clock

    def policy_for_workspace(self,workspace_id):
        """Derive a single fixed-scope private policy without retaining an unbounded cache of incoming IDs."""
        return WorkspaceReferencePolicy(workspace_id,self.origin,workspace_reference_key(self._master,workspace_id),self.trusted_local_origin)

    def authorize(self,workspace_id,delivery_id,raw_query):
        """Parse exactly one token/expiry/signature tuple and fail with the same unavailable response for every mismatch."""
        try:
            if type(raw_query) is not bytes or not 1<=len(raw_query)<=256:
                raise ValueError()
            values=parse_qs(raw_query.decode('ascii'),strict_parsing=True,keep_blank_values=True,max_num_fields=3)
            if set(values)!={'token','expires','signature'} or any(len(value)!=1 for value in values.values()):
                raise ValueError()
            expiry=values['expires'][0]
            if re.fullmatch(r'[1-9][0-9]{0,15}',expiry) is None:
                raise ValueError()
            policy=self.policy_for_workspace(workspace_id)
            if not policy.verifies(delivery_id,values['token'][0],int(expiry),values['signature'][0],clock=self.clock):
                raise ValueError()
            return values['token'][0]
        except (UnicodeError,ValueError,TypeError,KeyError):
            raise HTTPException(404,'参考素材不可用') from None


def active_reference_workspace(store,workspace_id):
    """Require an existing enabled owner and owner membership before selecting an already registered fixed space."""
    with store.connect() as db:
        return db.execute("SELECT w.id FROM workspaces w JOIN users u ON u.id=w.owner_id "
            "JOIN memberships m ON m.workspace_id=w.id AND m.user_id=u.id "
            "WHERE w.id=? AND u.disabled=0 AND m.role='owner'",(workspace_id,)).fetchone() is not None
