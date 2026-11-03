"""Exercise actual private key files, separate roots and the supported whole-root offline archive/restore path."""
import os
import time
from urllib.parse import urlsplit

from fastapi import HTTPException
import pytest

from app.controlplane.reference_delivery import GatewayReferenceAuthority
from app.controlplane.store import IdentityStore
from app.services.community.local_release_files import copy_snapshot,restore_snapshot
from tests.test_workspace_reference_policy import A,IDENTIFIER,TOKEN

ORIGIN='https://studio.example.com'


def authority(root,*,origin=ORIGIN):
    """Build one independently owned fixture authority with a deterministic test clock and no network calls."""
    return GatewayReferenceAuthority(root,origin,public_origin=origin,secure_cookies=True,clock=lambda:1001)


def test_durable_same_root_key_separate_root_keys_and_exact_tuple_rejection(tmp_path):
    """Restarts preserve one root's capability, another root cannot reuse it, and extra query fields do not broaden authority."""
    first=tmp_path/'first';second=tmp_path/'second';first.mkdir();second.mkdir()
    original=authority(first);url=original.policy_for_workspace(A).signed_url(IDENTIFIER,TOKEN,1000)
    raw=urlsplit(url).query.encode('ascii');saved=(first/'.reference-delivery.key').read_bytes()
    assert len(saved)==32 and authority(first).authorize(A,IDENTIFIER,raw)==TOKEN
    assert (first/'.reference-delivery.key').read_bytes()==saved
    other=authority(second)
    assert (second/'.reference-delivery.key').read_bytes()!=saved
    for service,query in ((other,raw),(original,raw+b'&root=foreign'),(original,raw+b'&token='+TOKEN.encode()),(original,b'\xff'),(original,b'x'*257)):
        with pytest.raises(HTTPException) as failure:service.authorize(A,IDENTIFIER,query)
        assert failure.value.status_code==404 and TOKEN not in str(failure.value.detail)


@pytest.mark.parametrize('invalid',['short','hardlink','redirect'])
def test_existing_invalid_key_is_not_replaced_or_repaired_from_another_file(tmp_path,invalid):
    """Existing bytes and destinations survive a refused initialization, including actual hard-link and redirect cases."""
    root=tmp_path/'root';root.mkdir();key=root/'.reference-delivery.key'
    if invalid=='short':key.write_bytes(b'incomplete-private-key')
    else:
        original=tmp_path/'other-private-key';original.write_bytes(bytes(range(32)))
        try:
            if invalid=='hardlink':os.link(original,key)
            else:key.symlink_to(original)
        except (OSError,NotImplementedError):pytest.skip('This platform cannot create the authored link fixture')
        if os.name!='nt':original.chmod(0o600)
    before=key.read_bytes()
    with pytest.raises((ValueError,OSError)):authority(root)
    assert key.read_bytes()==before


def test_invalid_gateway_origin_fails_before_private_key_or_identity_storage(tmp_path):
    """A request-like origin cannot initialize private storage or infer a local network exception."""
    from app.controlplane.gateway import create_gateway
    root=tmp_path/'not-created'
    with pytest.raises(ValueError):
        create_gateway(root,public_origin=ORIGIN,reference_media_origin='http://127.0.0.1:19000')
    assert not root.exists()


@pytest.mark.parametrize('overlap',['data_in_public','public_in_data','same'])
def test_private_gateway_storage_cannot_become_a_static_frontend_resource(tmp_path,overlap):
    """The actual before-fix static-key leak configuration fails before identity/key creation or service start."""
    from app.controlplane.gateway import create_gateway
    public=tmp_path/'public';public.mkdir();(public/'index.html').write_text('Authored frontend','utf8')
    data=public/'misplaced-private' if overlap=='data_in_public' else tmp_path if overlap=='public_in_data' else public
    with pytest.raises(ValueError):
        create_gateway(data,frontend=public,public_origin=ORIGIN,reference_media_origin=ORIGIN)
    assert not (data/'control.db').exists() and not (data/'.reference-delivery.key').exists()
    assert (public/'index.html').read_text('utf8')=='Authored frontend'


def test_real_offline_whole_root_copy_and_fresh_restore_keep_the_original_signing_authority(tmp_path):
    """Use actual existing SQLite/WAL/file snapshot code; no root key regenerates when restored to a private fresh path."""
    root=tmp_path/'identity';IdentityStore(root)
    original=authority(root);key=(root/'.reference-delivery.key').read_bytes()
    url=original.policy_for_workspace(A).signed_url(IDENTIFIER,TOKEN,1000)
    payload=tmp_path/'archive';deadline=time.monotonic()+60
    manifest=copy_snapshot(root,payload,deadline)
    assert '.reference-delivery.key' in manifest['files'] and manifest['private_archive']
    target=tmp_path/'restored';restore_snapshot(payload,manifest,target,deadline)
    if os.name!='nt':
        assert target.stat().st_mode&0o077==0
        (target/'.reference-delivery.key').chmod(0o644)
    recovered=authority(target)
    assert recovered.authorize(A,IDENTIFIER,urlsplit(url).query.encode('ascii'))==TOKEN
    assert (target/'.reference-delivery.key').read_bytes()==(root/'.reference-delivery.key').read_bytes()==key
    assert not manifest['services_started'] and manifest['external_model_calls']==0


@pytest.mark.skipif(os.name=='nt',reason='POSIX effective path permissions are not Windows ACLs')
def test_posix_broad_key_permissions_need_a_private_enclosing_root(tmp_path):
    """A key readable through both a broad file mode and broad enclosing root is never accepted."""
    root=tmp_path/'root';root.mkdir(mode=0o755)
    original=authority(root);key=root/'.reference-delivery.key';key.chmod(0o644);root.chmod(0o755)
    with pytest.raises(ValueError):authority(root)
    root.chmod(0o700)
    assert authority(root).policy_for_workspace(A)==original.policy_for_workspace(A)
