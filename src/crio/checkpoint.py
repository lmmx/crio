import os
import signal
import time
from contextlib import contextmanager

from . import criu
from .lock import hold_lock, release_lock
from .paths import CheckpointPaths

__all__ = ("checkpoint",)

_READY_TIMEOUT_S = 120
_POLL_INTERVAL_S = 0.05


def _wait_for(path, timeout_s=_READY_TIMEOUT_S):
    deadline = time.time() + timeout_s
    while not path.exists():
        if time.time() > deadline:
            raise RuntimeError(f"Timed out waiting for {path.name}")
        time.sleep(_POLL_INTERVAL_S)


def _run_parent(child_pid: int, paths: CheckpointPaths) -> None:
    """Orchestrate the CRIU dump then exit."""
    try:
        _wait_for(paths.child_ready)

        print(f"Creating checkpoint for PID {child_pid}")
        criu.dump(child_pid, paths.tmp_dir)

        paths.dump_complete.touch()
        paths.checkpoint_exists.touch()
        print("Checkpoint created successfully")

        os.waitpid(child_pid, 0)
    except Exception:
        try:
            os.kill(child_pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        raise
    finally:
        os._exit(0)


@contextmanager
def checkpoint(context: dict | None = None):
    paths = CheckpointPaths(context)
    paths.ensure_dirs()

    with hold_lock(paths.lock_file) as lock_fd:
        # Fast path: restore from existing checkpoint
        if paths.has_checkpoint():
            print("Found existing checkpoint, attempting restore...")
            release_lock(lock_fd, paths.lock_file)
            criu.restore(paths.tmp_dir)
            # unreachable — restore replaces the process

        # Slow path: fork, run imports in child, dump from parent
        pid = os.fork()

        if pid != 0:
            # Parent — orchestrate dump then _exit(0).
            # Parent owns the lock fd; it dies with the process.
            _run_parent(pid, paths)
            # unreachable

        # Child — close inherited lock fd, run user code
        os.close(lock_fd)

    # Child continues here after the with-block exits
    try:
        yield  # user imports execute
    finally:
        # Signal readiness, then sleep until dump completes
        paths.child_ready.touch()
        while not paths.dump_complete.exists():
            time.sleep(_POLL_INTERVAL_S)
        # Clean up stale lock file
        paths.lock_file.unlink(missing_ok=True)
