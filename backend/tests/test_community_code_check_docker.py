"""Authored CLI/configuration contracts never qualify a real daemon's kernel or namespace enforcement."""
from copy import deepcopy
import json
import subprocess

import pytest

from app.services.community.code_check_docker import (DockerCodeBoundary,DockerBoundaryError,OWNER_LABEL,ATTEMPT_LABEL,
    docker_environment,local_endpoint,isolated_docker_cli)

IMAGE = 'sha256:' + 'e'*64
CID = 'c'*64
OWNER = 'a'*64
IDENTIFIER = '11111111-1111-4111-8111-111111111111'
ATTEMPT = {'id':IDENTIFIER,'container_name':'fawnimo-code-'+IDENTIFIER.replace('-',''),'owner_token':OWNER,'image_id':IMAGE}
ENV = ['PATH=/usr/local/bin:/usr/bin:/bin','LANG=C.UTF-8','PYTHON_VERSION=3.12.9']


def record_for(attempt=ATTEMPT):
    """Construct explicit inspect fixture facts, rather than implying Docker produced or enforced them."""
    return {'Id':CID,'Image':IMAGE,'Name':'/'+attempt['container_name'],'Mounts':[],
        'Config':{'User':'0:0','WorkingDir':'/tmp','Entrypoint':['/usr/local/bin/python'],
            'Cmd':['-I','-B','-S','/app/backend/community_isolated_runner.py'],'Tty':False,'OpenStdin':True,
            'Env':ENV+['PYTHONUTF8=1'],'Labels':{OWNER_LABEL:attempt['owner_token'],ATTEMPT_LABEL:attempt['id']},'Healthcheck':{'Test':['NONE']}},
        'HostConfig':{'ReadonlyRootfs':True,'Privileged':False,'Init':True,'CapDrop':['ALL'],'CapAdd':['KILL','SETUID','SETGID'],
            'SecurityOpt':['no-new-privileges:true'],'NetworkMode':'none','IpcMode':'private','CgroupnsMode':'private','PidMode':'','UTSMode':'',
            'PublishAllPorts':False,'OomKillDisable':False,'Memory':536870912,'MemorySwap':536870912,'NanoCpus':1000000000,'PidsLimit':64,
            'ShmSize':1048576,'Tmpfs':{'/tmp':'rw,noexec,nosuid,nodev,size=134217728,mode=1777'},
            'Ulimits':[{'Name':'core','Soft':0,'Hard':0},{'Name':'nofile','Soft':128,'Hard':128}],
            'RestartPolicy':{'Name':'no','MaximumRetryCount':0},'LogConfig':{'Type':'local','Config':{'max-size':'1m','max-file':'1'}}},
        'State':{'Status':'created','Running':False,'Restarting':False,'OOMKilled':False,'ExitCode':0}}


class AuthoredDaemon:
    """Record literal argv/env and simulate known/unknown ownership responses without spawning a daemon."""
    def __init__(self):
        """Begin with no check resource and a positive explicitly authored Linux capability report."""
        self.calls=[]; self.record=None; self.info={'OSType':'linux','CgroupVersion':'2',
            'MemoryLimit':True,'SwapLimit':True,'PidsLimit':True,'CpuCfsPeriod':True,'CpuCfsQuota':True}

    def __call__(self,argv,**kwargs):
        """Only a finite positive list of daemon commands has fixture behavior; any unexpected command fails the test."""
        self.calls.append((argv,kwargs)); args=argv[5:]
        if argv == ['docker','context','inspect']: output=[{'Endpoints':{'docker':{'Host':'unix:///var/run/docker.sock'}}}]
        elif args[:1] == ['info']: output=self.info
        elif args[:2] == ['image','inspect']: output=[{'Id':IMAGE,'Config':{'Env':ENV,'Volumes':None}}]
        elif args[:2] == ['container','create']: self.record=record_for(); return subprocess.CompletedProcess(argv,0,(CID+'\n').encode(),b'')
        elif args[:2] == ['container','ls']: return subprocess.CompletedProcess(argv,0,(CID+'\n').encode() if self.record else b'',b'')
        elif args[:2] == ['container','inspect']: output=[self.record]
        elif args[:3] == ['container','rm','--force']: self.record=None; return subprocess.CompletedProcess(argv,0,(CID+'\n').encode(),b'')
        else: raise AssertionError('Unexpected Docker command')
        return subprocess.CompletedProcess(argv,0,json.dumps(output).encode(),b'')


@pytest.fixture
def boundary(tmp_path):
    """Own a fresh empty test config so user Docker settings cannot influence the adapter contract."""
    path=tmp_path/'empty'; path.mkdir(); (path/'config.json').write_bytes(b'{}\n')
    daemon=AuthoredDaemon(); value=DockerCodeBoundary('unix:///var/run/docker.sock',path,runner=daemon)
    value.image(IMAGE)
    return value,daemon


def test_fixed_creation_and_verified_owned_cleanup_only(boundary,monkeypatch):
    """Assert argv omits host mounts/socket/network/pull and each daemon call pins empty config and OS-only env."""
    engine,daemon=boundary
    monkeypatch.setenv('ARK_API_KEY','authored-private-sentinel'); monkeypatch.setenv('HTTP_PROXY','http://authored.invalid')
    engine.check_engine(); assert engine.create(ATTEMPT) == CID
    record=engine.find_owned(ATTEMPT,expected_id=CID); engine.validate_created(ATTEMPT,record)
    cleanup=engine.remove_owned(ATTEMPT,expected_id=CID)
    assert cleanup.resource_absent and cleanup.container_id == CID
    command=next(argv for argv,_ in daemon.calls if argv[5:7] == ['container','create'])
    assert '--pull' in command and command[command.index('--pull')+1] == 'never'
    assert not set(command)&{'--volume','-v','--mount','--publish','--privileged','--use-api-socket','--env-file'}
    for argv,options in daemon.calls:
        assert argv[:5] == ['docker','--config',str(engine.configuration),'--host','unix:///var/run/docker.sock']
        assert options['timeout'] <= 15 and not {'ARK_API_KEY','HTTP_PROXY','HTTPS_PROXY','DOCKER_CONFIG'} & set(options['env'])
    assert any(argv[-2:] == ['--force',CID] for argv,_ in daemon.calls)


@pytest.mark.parametrize('bad',['bind','socket','privileged','network','env','memory','capability','log','restart','tmpfs','image','user','stdin','shm'])
def test_actual_inspect_mismatch_rejects_before_start(boundary,bad):
    """An option string passed to create is insufficient if the actual inspect receipt differs from the fixed policy."""
    engine,_daemon=boundary; record=record_for(); host=record['HostConfig']; config=record['Config']
    if bad == 'bind': host['Binds']=['/private:/private']
    elif bad == 'socket': record['Mounts']=[{'Type':'bind','Destination':'/var/run/docker.sock'}]
    elif bad == 'privileged': host['Privileged']=True
    elif bad == 'network': host['NetworkMode']='host'
    elif bad == 'env': config['Env'].append('ARK_API_KEY=authored-private-sentinel')
    elif bad == 'memory': host['Memory']=0
    elif bad == 'capability': host['CapAdd'].append('SYS_ADMIN')
    elif bad == 'log': host['LogConfig']['Type']='json-file'
    elif bad == 'restart': host['RestartPolicy']['Name']='always'
    elif bad == 'tmpfs': host['Tmpfs']['/tmp'] += ',exec'
    elif bad == 'image': record['Image']='sha256:'+'f'*64
    elif bad == 'user': config['User']='10001'
    elif bad == 'stdin': config['OpenStdin']=False
    else: host['ShmSize']=67108864
    with pytest.raises(DockerBoundaryError): engine.validate_created(ATTEMPT,record)


@pytest.mark.parametrize('bad',['owner','name','image','attempt','cid'])
def test_foreign_or_substituted_identity_is_never_removed(boundary,bad):
    """A journal token does not authorize deleting a renamed/replaced/foreign object; exact original identity must match first."""
    engine,daemon=boundary; daemon.record=record_for()
    if bad == 'owner': daemon.record['Config']['Labels'][OWNER_LABEL]='b'*64
    elif bad == 'name': daemon.record['Name']='/foreign'
    elif bad == 'image': daemon.record['Image']='sha256:'+'f'*64
    elif bad == 'attempt': daemon.record['Config']['Labels'][ATTEMPT_LABEL]='22222222-2222-4222-8222-222222222222'
    else: daemon.record['Id']='d'*64
    with pytest.raises(DockerBoundaryError): engine.remove_owned(ATTEMPT,expected_id=CID)
    assert not any(argv[5:7] == ['container','rm'] for argv,_ in daemon.calls)


def test_user_config_and_remote_overrides_cannot_supply_auth_or_proxies(monkeypatch):
    """The only local context read precedes all daemon operations, and all later calls use the owned empty config."""
    daemon=AuthoredDaemon(); monkeypatch.setenv('OPENAI_API_KEY','authored-private-sentinel')
    assert 'OPENAI_API_KEY' not in docker_environment()
    for key in ['DOCKER_HOST','DOCKER_CONTEXT','DOCKER_TLS','DOCKER_TLS_VERIFY','DOCKER_CERT_PATH','DOCKER_CONFIG','DOCKER_CUSTOM_HEADERS','DOCKER_API_VERSION']:
        monkeypatch.delenv(key,raising=False)
    with isolated_docker_cli(runner=daemon) as engine:
        directory=engine.configuration; assert (directory/'config.json').read_bytes() == b'{}\n'
        assert daemon.calls[0][0] == ['docker','context','inspect']
    assert not directory.exists()
    monkeypatch.setenv('DOCKER_HOST','tcp://authored.invalid:2375'); before=len(daemon.calls)
    with pytest.raises(DockerBoundaryError): local_endpoint(runner=daemon)
    assert len(daemon.calls) == before


def test_unsupported_kernel_support_or_spent_operation_budget_fails_closed(boundary):
    """Unavailable enforcement never becomes a successful profile, and a spent group budget issues no extra mutation."""
    engine,daemon=boundary; daemon.info['PidsLimit']=False
    with pytest.raises(DockerBoundaryError): engine.check_engine()
    before=len(daemon.calls)
    with engine.budget(0):
        with pytest.raises(DockerBoundaryError): engine.create(ATTEMPT)
    assert len(daemon.calls) == before
