"""Cross-process public-object writer ownership, using kernel locks rather than stale PID files or automatic lock deletion."""
from contextlib import contextmanager
from contextvars import ContextVar
import errno
import hashlib
import math
import os
from pathlib import Path
import stat
import time

from app.services.community.store import CommunityStoreConflict

HELD_OBJECT_LOCKS = ContextVar('community_held_object_locks', default=frozenset())


def kernel_lock(descriptor):
    """Acquire a nonblocking OS lock before reading its marker, because Windows byte locks also fence ordinary reads."""
    os.lseek(descriptor, 0, os.SEEK_SET)
    if os.name == 'nt':
        import msvcrt
        msvcrt.locking(descriptor, msvcrt.LK_NBLCK, 1)
    else:
        import fcntl
        fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)


def kernel_unlock(descriptor):
    """Release exactly the known byte/flock; closing the descriptor remains the final kernel ownership guarantee."""
    os.lseek(descriptor, 0, os.SEEK_SET)
    if os.name == 'nt':
        import msvcrt
        msvcrt.locking(descriptor, msvcrt.LK_UNLCK, 1)
    else:
        import fcntl
        fcntl.flock(descriptor, fcntl.LOCK_UN)


@contextmanager
def candidate_object_lock(objects, deadline):
    """Serialize writers sharing a real Git object directory, including separate worktrees/processes; process death releases the kernel lock."""
    now = time.monotonic()
    if type(deadline) not in {int, float} or not math.isfinite(deadline) or not now < deadline <= now + 20.01:
        raise CommunityStoreConflict('Public Git writer requires a finite remaining deadline')
    directory = Path(objects).absolute()
    if directory.resolve() != directory or not directory.is_dir():
        raise CommunityStoreConflict('Public Git object directory is redirected or absent')
    identity = str(directory).casefold() if os.name == 'nt' else str(directory)
    key = hashlib.sha256(identity.encode('utf8')).hexdigest()
    held = HELD_OBJECT_LOCKS.get()
    if key in held:
        yield
        return
    info = directory / 'info'
    if info.resolve() != info:
        raise CommunityStoreConflict('Public Git object metadata directory is redirected')
    info.mkdir(exist_ok=True)
    path = info / 'fawnimo-community-writes.lock'
    if path.resolve() != path:
        raise CommunityStoreConflict('Public Git writer lock is redirected')
    marker = ('fawnimo-public-object-writer-v1\n' + key + '\n').encode('ascii')
    flags = os.O_RDWR | getattr(os, 'O_BINARY', 0)
    created = False
    try:
        descriptor = os.open(path, flags | os.O_CREAT | os.O_EXCL, 0o600); created = True
    except FileExistsError:
        descriptor = os.open(path, flags)
    locked = False
    token = None
    try:
        if not stat.S_ISREG(os.fstat(descriptor).st_mode):
            raise CommunityStoreConflict('Public Git writer lock is not a regular owned metadata file')
        while True:
            if time.monotonic() >= deadline:
                raise CommunityStoreConflict('Public Git object writer is busy or its marker needs attention')
            try:
                kernel_lock(descriptor)
                locked = True
            except OSError as error:
                if error.errno not in {errno.EACCES, errno.EAGAIN}:
                    raise CommunityStoreConflict('Public Git kernel writer lock is unavailable') from None
                time.sleep(.01); continue
            if created:
                os.lseek(descriptor, 0, os.SEEK_SET)
                if os.write(descriptor, marker) != len(marker):
                    raise CommunityStoreConflict('Public Git writer lock initialization was interrupted')
                os.fsync(descriptor); created = False
            os.lseek(descriptor, 0, os.SEEK_SET)
            observed = os.read(descriptor, len(marker) + 1)
            if observed == marker:
                break
            # A noncreator can win the lock before the creator initializes its
            # new file. Release before waiting so that owner can finish. An
            # existing foreign/partial marker is never rewritten or deleted.
            if marker.startswith(observed) and len(observed) < len(marker):
                kernel_unlock(descriptor); locked = False
                time.sleep(.01); continue
            raise CommunityStoreConflict('Public Git writer lock belongs to an unknown metadata profile')
        token = HELD_OBJECT_LOCKS.set(held | {key})
        yield
    finally:
        if token is not None: HELD_OBJECT_LOCKS.reset(token)
        try:
            if locked: kernel_unlock(descriptor)
        finally:
            os.close(descriptor)
