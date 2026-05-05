import fcntl
import os
from contextlib import contextmanager
from pathlib import Path

__all__ = ("hold_lock",)


@contextmanager
def hold_lock(lock_file: Path):
    """Acquire an exclusive, non-blocking file lock. Yields the fd.

    The caller is responsible for deciding *which process* runs the
    finally block — see checkpoint.py for the fork-aware cleanup.
    """
    fd = None
    try:
        fd = os.open(lock_file, os.O_CREAT | os.O_RDWR)
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        yield fd
    except PermissionError:
        if fd is not None:
            os.close(fd)
        raise RuntimeError("Cannot create lock file — permission denied")
    except IOError:
        if fd is not None:
            os.close(fd)
        raise RuntimeError("Another crio process is running")


def release_lock(fd: int, lock_file: Path) -> None:
    try:
        fcntl.flock(fd, fcntl.LOCK_UN)
        os.close(fd)
        lock_file.unlink(missing_ok=True)
    except OSError:
        pass
