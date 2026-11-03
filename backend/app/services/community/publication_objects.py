"""Project exact inert Git objects into GitHub's content-addressed create-object protocol, without checking out or executing candidate source."""
import base64
from datetime import datetime,timedelta,timezone
import hashlib
import re
from app.services.community.candidate_git import bounded_git_operation
from app.services.community.change_bundles import verify_change_bundle
from app.services.community.store import CommunityStoreConflict


@bounded_git_operation
def publication_objects(repository,artifact):
    """Revalidate the original candidate and preserve its complete message, parent, tree, identities, timestamp and timezone; remote SHA equality is mandatory."""
    candidate=repository.recover(artifact)
    if candidate is None: raise CommunityStoreConflict('Original local candidate does not exist')
    bundle=verify_change_bundle(artifact); raw=repository._git('cat-file','commit',candidate.commit).stdout
    header,message=raw.split(b'\n\n',1); fields={}
    for line in header.splitlines():
        key,value=line.split(b' ',1)
        if key in fields: raise CommunityStoreConflict('Unexpected repeated candidate commit header')
        fields[key]=value
    if set(fields)!={b'tree',b'parent',b'author',b'committer'} or fields[b'tree'].decode()!=candidate.tree or fields[b'parent'].decode()!=candidate.parent:
        raise CommunityStoreConflict('Unsupported candidate Git commit shape')
    identities={}
    for key in (b'author',b'committer'):
        match=re.fullmatch(rb'Fawnimo local proposal <community@fawnimo.invalid> ([0-9]{1,12}) ([+-])([0-9]{2})([0-9]{2})',fields[key])
        if match is None: raise CommunityStoreConflict('Unexpected local proposal author identity')
        stamp,sign,hours,minutes=match.groups(); hours=int(hours); minutes=int(minutes)
        if hours>23 or minutes>59: raise CommunityStoreConflict('Invalid proposal timezone')
        offset=(hours*60+minutes)*(1 if sign==b'+' else -1)
        date=datetime.fromtimestamp(int(stamp),timezone(timedelta(minutes=offset))).isoformat(timespec='seconds')
        identities[key.decode()]={'name':'Fawnimo local proposal','email':'community@fawnimo.invalid','date':date}
    objects=[]; entries=[]
    for item in bundle['files']:
        blob=None
        if item['action']!='delete':
            content=item['text'].encode('utf8'); blob=hashlib.sha1(b'blob '+str(len(content)).encode()+b'\0'+content).hexdigest()
            objects.append({'sha':blob,'body':{'content':base64.b64encode(content).decode('ascii'),'encoding':'base64'}})
        entries.append({'path':item['path'],'mode':'100644','type':'blob','sha':blob})
    return {'candidate':candidate,'blobs':objects,'tree':{'base_tree':bundle['scope']['base_tree'],'tree':entries},
        'commit':{'message':message.decode('utf8'),'tree':candidate.tree,'parents':[candidate.parent],**identities}}
