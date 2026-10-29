"""Pinned network transport for trusted provider configuration and bounded media downloads."""
from dataclasses import dataclass
import http.client
import ipaddress
import socket
import ssl
import time
from urllib.parse import urljoin, urlsplit


class UnsafeTarget(ValueError):
    """The URL or a resolved address is outside the explicit network trust boundary."""


@dataclass(frozen=True)
class HttpResult:
    """Bounded bytes and minimal metadata, without loggable credential headers."""
    status: int
    headers: dict[str,str]
    body: bytes


def resolve_target(url: str, allowed_hosts: set[str], *, trusted_local_host: str | None=None, trusted_local_ports: set[int] | None=None):
    """Resolve once, reject mixed private DNS answers, and return a pinned IP.

    A local fixture/model exception must match its independently configured exact
    host and resolve entirely to loopback. Imported data cannot expand this list.
    """
    try:
        parsed=urlsplit(url);host=(parsed.hostname or '').lower();port=parsed.port or (443 if parsed.scheme=='https' else 80)
    except ValueError as exc:raise UnsafeTarget('Invalid target') from exc
    if parsed.scheme not in {'http','https'} or not host or parsed.username or parsed.password or parsed.fragment or '%' in host or host not in allowed_hosts:
        raise UnsafeTarget('Untrusted target')
    local=trusted_local_host is not None and host==trusted_local_host.lower()
    if local and port not in (trusted_local_ports or set()):raise UnsafeTarget('Local port is not explicitly trusted')
    if parsed.scheme=='http' and not local:raise UnsafeTarget('HTTPS is required')
    try:answers=socket.getaddrinfo(host,port,type=socket.SOCK_STREAM)
    except OSError as exc:raise UnsafeTarget('Target resolution failed') from exc
    addresses=list(dict.fromkeys(answer[4][0] for answer in answers))
    if not addresses:raise UnsafeTarget('No resolved address')
    for address in addresses:
        ip=ipaddress.ip_address(address)
        if local:
            if not ip.is_loopback:raise UnsafeTarget('Local exception must resolve to loopback')
        elif not ip.is_global:raise UnsafeTarget('Private or metadata address rejected')
    return parsed,addresses[0],port


def request_bounded(url: str, *, allowed_hosts: set[str], trusted_local_host: str | None=None, trusted_local_ports: set[int] | None=None, method='GET', data: bytes | None=None, headers: dict[str,str] | None=None, timeout=60, deadline_limit=60, max_bytes=64*1024*1024, max_redirects=3) -> HttpResult:
    """Make a DNS-pinned request and validate every redirect before connecting.

    TLS still validates the original hostname. Provider authorization is never
    forwarded across origins; media callers supply no credential headers.
    """
    # Only trusted understanding code opts into its established 300-second
    # request policy. Existing callers retain their original 60-second ceiling.
    if type(deadline_limit) is not int or deadline_limit not in {60,300} or timeout<=0 or timeout>deadline_limit or max_bytes<1:raise ValueError('Invalid transport limits')
    if any(key.lower() in {'host','proxy-authorization','connection','transfer-encoding','content-length'} for key in (headers or {})):raise UnsafeTarget('Caller-controlled routing headers are forbidden')
    current=url;outgoing=dict(headers or {});deadline=time.monotonic()+timeout
    for redirect in range(max_redirects+1):
        parsed,ip,port=resolve_target(current,allowed_hosts,trusted_local_host=trusted_local_host,trusted_local_ports=trusted_local_ports)
        remaining=deadline-time.monotonic()
        if remaining<=0:raise TimeoutError('HTTP deadline exceeded')
        host=parsed.hostname or ''
        if parsed.scheme=='https':connection=http.client.HTTPSConnection(host,port,timeout=remaining,context=ssl.create_default_context())
        else:connection=http.client.HTTPConnection(host,port,timeout=remaining)
        def pinned_connection(_address,connect_timeout=remaining,source_address=None,**_kwargs):
            """Avoid a second hostname resolution between validation and connection."""
            return socket.create_connection((ip,port),connect_timeout,source_address)
        connection._create_connection=pinned_connection
        path=parsed.path or '/'
        if parsed.query:path+='?'+parsed.query
        try:
            connection.request(method,path,body=data,headers=outgoing)
            response=connection.getresponse();response_headers={key.lower():value for key,value in response.getheaders()}
            if response.status in {301,302,303,307,308}:
                if method!='GET' or redirect>=max_redirects or 'location' not in response_headers:raise UnsafeTarget('Redirect not allowed')
                target=urljoin(current,response_headers['location']);new=urlsplit(target)
                if (new.scheme,new.hostname,new.port)!=(parsed.scheme,parsed.hostname,parsed.port):
                    outgoing={key:value for key,value in outgoing.items() if key.lower() in {'accept','range','user-agent'}}
                    data=None
                current=target;continue
            length=response_headers.get('content-length')
            if length and (not length.isdigit() or int(length)>max_bytes):raise UnsafeTarget('Response size exceeds limit')
            chunks=[];size=0
            while True:
                remaining=deadline-time.monotonic()
                if remaining<=0:raise TimeoutError('HTTP deadline exceeded')
                sock=connection.sock or getattr(getattr(response.fp,'raw',None),'_sock',None)
                if sock:sock.settimeout(remaining)
                chunk=response.read1(min(65536,max_bytes-size+1))
                if not chunk:break
                size+=len(chunk)
                if size>max_bytes:raise UnsafeTarget('Response size exceeds limit')
                chunks.append(chunk)
            return HttpResult(response.status,response_headers,b''.join(chunks))
        finally:connection.close()
    raise UnsafeTarget('Too many redirects')


def request_isolated(url: str, **kwargs) -> HttpResult:
    """Bound the entire operation, including DNS/TLS, in a killable network child.

    Credentials travel only through a private stdin pipe, never argv or logs. The
    child also enforces its own wall deadline if its parent unexpectedly exits.
    """
    import base64
    import json
    import subprocess
    import sys
    from pathlib import Path
    timeout=kwargs.get('timeout',60)
    limit=kwargs.get('deadline_limit',60)
    if type(limit) is not int or limit not in {60,300} or timeout<=0 or timeout>limit:raise ValueError('Invalid request deadline')
    parameters={'url':url,**kwargs}
    for key in ['allowed_hosts','trusted_local_ports']:
        if key in parameters:parameters[key]=list(parameters[key] or [])
    blob=parameters.pop('data',None)
    parameters['data_b64']=base64.b64encode(blob).decode() if blob is not None else None
    process=subprocess.Popen([sys.executable,str(Path(__file__).resolve()),'--isolated'],stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.DEVNULL,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
    try:stdout,_=process.communicate(json.dumps(parameters).encode(),timeout=timeout+2)
    except subprocess.TimeoutExpired as exc:
        process.kill();process.wait(timeout=5);raise TimeoutError('Network process deadline exceeded') from exc
    if process.returncode:raise UnsafeTarget('Network request rejected or timed out')
    result=json.loads(stdout)
    return HttpResult(result['status'],result['headers'],base64.b64decode(result['body']))


async def request_isolated_async(url: str, **kwargs) -> HttpResult:
    """Worker transport: cancellation kills and joins the actual network process."""
    import asyncio
    import base64
    import json
    import sys
    from pathlib import Path
    timeout=kwargs.get('timeout',60)
    limit=kwargs.get('deadline_limit',60)
    if type(limit) is not int or limit not in {60,300} or timeout<=0 or timeout>limit:raise ValueError('Invalid request deadline')
    parameters={'url':url,**kwargs}
    for key in ['allowed_hosts','trusted_local_ports']:
        if key in parameters:parameters[key]=list(parameters[key] or [])
    blob=parameters.pop('data',None);parameters['data_b64']=base64.b64encode(blob).decode() if blob is not None else None
    process=await asyncio.create_subprocess_exec(sys.executable,str(Path(__file__).resolve()),'--isolated',stdin=asyncio.subprocess.PIPE,stdout=asyncio.subprocess.PIPE,stderr=asyncio.subprocess.DEVNULL)
    try:
        stdout,_=await asyncio.wait_for(process.communicate(json.dumps(parameters).encode()),timeout+2)
        if process.returncode:raise UnsafeTarget('Network request rejected or timed out')
        result=json.loads(stdout);return HttpResult(result['status'],result['headers'],base64.b64decode(result['body']))
    finally:
        if process.returncode is None:process.kill();await process.wait()


if __name__=='__main__':
    import base64
    import json
    import os
    import sys
    import threading
    options=json.loads(sys.stdin.buffer.read())
    # Hard exit also terminates sockets when a parent worker dies mid-request.
    limit=options.get('deadline_limit',60)
    if type(limit) is not int or limit not in {60,300}:sys.exit(2)
    timer=threading.Timer(min(limit,float(options.get('timeout',60))),lambda:os._exit(124));timer.daemon=True;timer.start()
    try:
        blob=options.pop('data_b64',None)
        options['data']=base64.b64decode(blob) if blob is not None else None
        options['allowed_hosts']=set(options['allowed_hosts'])
        if 'trusted_local_ports' in options:options['trusted_local_ports']=set(options['trusted_local_ports'])
        result=request_bounded(**options)
        sys.stdout.write(json.dumps({'status':result.status,'headers':result.headers,'body':base64.b64encode(result.body).decode()}));sys.stdout.flush()
    except Exception:sys.exit(2)
    finally:timer.cancel()
