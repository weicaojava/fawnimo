"""Actually spawn private descendants and verify their death after every helper outcome."""
import asyncio
import ctypes
from ctypes import wintypes
import os
from pathlib import Path
import sys
import pytest
from app.core.owned_process import run_owned_process, WindowsProcessJob


HELPER = r'''
import json, os, subprocess, sys, time
from pathlib import Path
sys.stdin.buffer.read()  # ownership gate: no descendant before complete stdin
mode, marker = sys.argv[1], Path(sys.argv[2])
ready = str(marker) + '.ready'
child_code = "import os,sys,time;from pathlib import Path;Path(sys.argv[1]).write_text(str(os.getpid()));time.sleep(60)"
child = subprocess.Popen([sys.executable, '-c', child_code, ready], stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
until = time.monotonic()+5
while not Path(ready).exists():
    if time.monotonic()>until: os._exit(5)
    time.sleep(.005)
marker.write_text(str(child.pid))
if mode == 'crash': os._exit(2)
if mode == 'success': print(json.dumps({'fixture': True})); sys.exit(0)
if mode == 'overflow': print('x'*4096); sys.stdout.flush()
time.sleep(60)
'''


def process_alive(pid):
    """Observe only this test's known private PID; never enumerate or kill unrelated processes."""
    if os.name == 'nt':
        kernel = ctypes.WinDLL('kernel32', use_last_error=True)
        kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]; kernel.OpenProcess.restype = wintypes.HANDLE
        kernel.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]; kernel.WaitForSingleObject.restype = wintypes.DWORD
        kernel.CloseHandle.argtypes = [wintypes.HANDLE]
        handle = kernel.OpenProcess(0x00100000, False, pid)
        if not handle: return False
        try: return kernel.WaitForSingleObject(handle, 0) == 258
        finally: kernel.CloseHandle(handle)
    proc = Path(f'/proc/{pid}/stat')
    if proc.exists() and proc.read_text().split(') ', 1)[1].startswith('Z'): return False
    try: os.kill(pid, 0); return True
    except ProcessLookupError: return False


async def marker_pid(marker):
    """Wait until the private descendant proves it really started, before cancellation tests."""
    for _ in range(500):
        if marker.exists(): return int(marker.read_text())
        await asyncio.sleep(.01)
    raise AssertionError('Private test descendant did not start')


@pytest.mark.asyncio
@pytest.mark.parametrize('mode', ['crash', 'success', 'timeout', 'cancel', 'overflow'])
async def test_owned_tree_dies_even_after_helper_has_already_exited(tmp_path, mode):
    helper, marker = tmp_path / 'trusted_gated_fixture.py', tmp_path / 'descendant.pid'
    helper.write_text(HELPER, 'utf8')
    task = asyncio.create_task(run_owned_process([sys.executable, str(helper), mode, str(marker)], b'{}', timeout=.8 if mode == 'timeout' else 10, max_stdout_bytes=1024))
    pid = await marker_pid(marker)
    if mode == 'cancel':
        assert process_alive(pid)
        task.cancel()
        with pytest.raises(asyncio.CancelledError): await task
    elif mode == 'timeout':
        assert process_alive(pid)
        with pytest.raises(asyncio.TimeoutError): await task
    elif mode == 'overflow':
        with pytest.raises(ValueError, match='output limit'): await task
    else:
        _stdout, code = await task
        assert code == (2 if mode == 'crash' else 0)
    for _ in range(100):
        if not process_alive(pid): break
        await asyncio.sleep(.01)
    assert not process_alive(pid), 'An actual private encoder descendant survived its owner'


@pytest.mark.skipif(os.name != 'nt', reason='Windows assignment gate')
@pytest.mark.asyncio
async def test_failed_windows_job_assignment_never_opens_stdin_gate(tmp_path, monkeypatch):
    helper, marker = tmp_path / 'trusted_gate.py', tmp_path / 'must-not-spawn.pid'
    helper.write_text(HELPER, 'utf8')

    def fail_assignment(_self, _pid):
        """Represent an unsupported host policy without allowing the helper to launch an encoder."""
        raise OSError('Forced private Job Object assignment failure')
    monkeypatch.setattr(WindowsProcessJob, 'attach', fail_assignment)
    with pytest.raises(OSError, match='assignment failure'):
        await run_owned_process([sys.executable, str(helper), 'success', str(marker)], b'{}', timeout=10)
    assert not marker.exists() and not Path(str(marker) + '.ready').exists()
