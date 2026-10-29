"""Own a gated helper and every descendant until exit, timeout or cancellation.

The server-owned helper must read all stdin before it can spawn descendants.
Windows attaches it to a kill-on-close Job Object before opening that input gate.
Job handles are non-inheritable, so application death also closes the last owner.
"""
import asyncio
import ctypes
from ctypes import wintypes
import os
import signal
import subprocess
import time
from collections.abc import Sequence


class WindowsProcessJob:
    """A real kernel process-tree container, independent of the helper's PID lifetime."""

    def __init__(self):
        """Create an unnamed, non-inheritable kill-on-close job before starting the helper."""
        class BasicLimit(ctypes.Structure):
            """Exact Win32 JOBOBJECT_BASIC_LIMIT_INFORMATION layout."""
            _fields_ = [('PerProcessUserTimeLimit', ctypes.c_int64), ('PerJobUserTimeLimit', ctypes.c_int64),
                ('LimitFlags', wintypes.DWORD), ('MinimumWorkingSetSize', ctypes.c_size_t), ('MaximumWorkingSetSize', ctypes.c_size_t),
                ('ActiveProcessLimit', wintypes.DWORD), ('Affinity', ctypes.c_size_t), ('PriorityClass', wintypes.DWORD), ('SchedulingClass', wintypes.DWORD)]

        class IOCounters(ctypes.Structure):
            """Six unsigned 64-bit counters preserve the extended limit structure alignment."""
            _fields_ = [(name, ctypes.c_uint64) for name in ('ReadOperationCount', 'WriteOperationCount', 'OtherOperationCount', 'ReadTransferCount', 'WriteTransferCount', 'OtherTransferCount')]

        class ExtendedLimit(ctypes.Structure):
            """Exact Win32 JOBOBJECT_EXTENDED_LIMIT_INFORMATION layout."""
            _fields_ = [('BasicLimitInformation', BasicLimit), ('IoInfo', IOCounters), ('ProcessMemoryLimit', ctypes.c_size_t),
                ('JobMemoryLimit', ctypes.c_size_t), ('PeakProcessMemoryUsed', ctypes.c_size_t), ('PeakJobMemoryUsed', ctypes.c_size_t)]

        class Accounting(ctypes.Structure):
            """Read actual active descendants after termination instead of enumerating recycled PIDs."""
            _fields_ = [(name, ctypes.c_int64) for name in ('TotalUserTime', 'TotalKernelTime', 'ThisPeriodTotalUserTime', 'ThisPeriodTotalKernelTime')] + [
                (name, wintypes.DWORD) for name in ('TotalPageFaultCount', 'TotalProcesses', 'ActiveProcesses', 'TotalTerminatedProcesses')]

        self.api = ctypes.WinDLL('kernel32', use_last_error=True)
        signatures = {
            'CreateJobObjectW': ([ctypes.c_void_p, wintypes.LPCWSTR], wintypes.HANDLE),
            'SetInformationJobObject': ([wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD], wintypes.BOOL),
            'AssignProcessToJobObject': ([wintypes.HANDLE, wintypes.HANDLE], wintypes.BOOL),
            'OpenProcess': ([wintypes.DWORD, wintypes.BOOL, wintypes.DWORD], wintypes.HANDLE),
            'TerminateJobObject': ([wintypes.HANDLE, wintypes.UINT], wintypes.BOOL),
            'QueryInformationJobObject': ([wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD, ctypes.c_void_p], wintypes.BOOL),
            'CloseHandle': ([wintypes.HANDLE], wintypes.BOOL),
        }
        for name, (arguments, result) in signatures.items():
            method = getattr(self.api, name); method.argtypes = arguments; method.restype = result
        self.accounting_type = Accounting
        self.handle = self.api.CreateJobObjectW(None, None)
        if not self.handle: raise ctypes.WinError(ctypes.get_last_error())
        limits = ExtendedLimit(); limits.BasicLimitInformation.LimitFlags = 0x2000  # JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
        if not self.api.SetInformationJobObject(self.handle, 9, ctypes.byref(limits), ctypes.sizeof(limits)):
            error = ctypes.get_last_error(); self.api.CloseHandle(self.handle); self.handle = None
            raise ctypes.WinError(error)

    def attach(self, pid):
        """Attach before sending stdin; failure is closed and cannot leave an ungated encoder."""
        process = self.api.OpenProcess(0x0100 | 0x0001, False, pid)  # SET_QUOTA | TERMINATE
        if not process: raise ctypes.WinError(ctypes.get_last_error())
        try:
            if not self.api.AssignProcessToJobObject(self.handle, process): raise ctypes.WinError(ctypes.get_last_error())
        finally:
            self.api.CloseHandle(process)

    def terminate_and_close(self):
        """Terminate and observe the whole kernel container even when the helper has already exited."""
        if not self.handle: return
        handle, self.handle = self.handle, None
        try:
            if not self.api.TerminateJobObject(handle, 70): raise ctypes.WinError(ctypes.get_last_error())
            until = time.monotonic() + 5
            while True:
                accounting = self.accounting_type()
                if not self.api.QueryInformationJobObject(handle, 1, ctypes.byref(accounting), ctypes.sizeof(accounting), None): raise ctypes.WinError(ctypes.get_last_error())
                if accounting.ActiveProcesses == 0: break
                if time.monotonic() >= until: raise RuntimeError('Owned process job did not drain')
                time.sleep(.01)
        finally:
            # Closing also enforces the kernel kill policy if querying/draining failed.
            self.api.CloseHandle(handle)


async def run_owned_process(command: Sequence[str], input_data: bytes, *, timeout: float, max_stdout_bytes: int = 2 * 1024 * 1024) -> tuple[bytes, int]:
    """Run trusted gated argv, bound I/O/time, and join its owned tree on every outcome.

    Never pass a client-supplied command. The helper must not spawn before stdin
    reaches EOF: Windows ownership is installed before input_data is delivered.
    The return code is returned for caller-specific protocol validation.
    """
    if not command or timeout <= 0 or max_stdout_bytes < 1: raise ValueError('Invalid owned process bounds')
    job = WindowsProcessJob() if os.name == 'nt' else None
    process = None

    async def cleanup():
        """Close a Job Object or kill the original POSIX group, regardless of helper returncode."""
        try:
            if job is not None:
                await asyncio.to_thread(job.terminate_and_close)
            elif process is not None:
                try: os.killpg(process.pid, signal.SIGKILL)
                except ProcessLookupError: pass
        finally:
            if process is not None:
                if process.returncode is None:
                    try: process.kill()
                    except ProcessLookupError: pass
                await process.wait()

    try:
        options = {'creationflags': subprocess.CREATE_NO_WINDOW} if os.name == 'nt' else {'start_new_session': True}
        process = await asyncio.create_subprocess_exec(*command, stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL, **options)
        if job is not None: job.attach(process.pid)

        async def exchange():
            """Open the input gate only after ownership and stop at one byte beyond the output cap."""
            process.stdin.write(input_data)
            await process.stdin.drain()
            process.stdin.close()
            blocks, size = [], 0
            while True:
                block = await process.stdout.read(min(65536, max_stdout_bytes - size + 1))
                if not block: break
                size += len(block)
                if size > max_stdout_bytes: raise ValueError('Owned helper exceeded output limit')
                blocks.append(block)
            return b''.join(blocks), await process.wait()

        return await asyncio.wait_for(exchange(), timeout)
    finally:
        cleaning = asyncio.create_task(cleanup())
        try:
            await asyncio.shield(cleaning)
        except asyncio.CancelledError:
            await asyncio.shield(cleaning)
            raise
