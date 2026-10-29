"""Cooperative process-lifetime locks for supported gateway, worker and offline maintenance tools."""
import os
from pathlib import Path


class MaintenanceBusy(RuntimeError):
    """Reject maintenance while a supported service still owns the same root or workspace."""


class ProcessLease:
    """Hold an OS-backed exclusive byte lock; crashes release it without stale PID guessing."""

    def __init__(self, path: Path):
        """Keep the lock outside replaceable workspace directories and never unlink a live lock."""
        self.path = Path(path).absolute()
        self.stream = None

    def __enter__(self):
        """Acquire without waiting; another process cannot share an offline import destination."""
        self.path.parent.mkdir(parents=True, exist_ok=True)
        if self.path.is_symlink() or self.path.resolve() != self.path:
            raise ValueError('Maintenance lock cannot follow a redirected path')
        self.stream = self.path.open('a+b')
        try:
            if self.path.stat().st_size == 0:
                self.stream.write(b'\0'); self.stream.flush()
            self.stream.seek(0)
            if os.name == 'nt':
                import msvcrt
                msvcrt.locking(self.stream.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(self.stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            return self
        except OSError:
            self.stream.close(); self.stream = None
            raise MaintenanceBusy('Service or maintenance operation is active; stop it before retrying') from None

    def __exit__(self, *_):
        """Release on every exit, including interruption, without deleting the lock inode."""
        if self.stream:
            try:
                self.stream.seek(0)
                if os.name == 'nt':
                    import msvcrt
                    msvcrt.locking(self.stream.fileno(), msvcrt.LK_UNLCK, 1)
                else:
                    import fcntl
                    fcntl.flock(self.stream.fileno(), fcntl.LOCK_UN)
            finally:
                self.stream.close(); self.stream = None
