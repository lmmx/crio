import fcntl
import os
import signal
import subprocess
from contextlib import contextmanager
from pathlib import Path


@contextmanager
def checkpoint():
    checkpoint_dir = Path("/tmp/crio")
    lock_file = checkpoint_dir / "crio.lock"

    # Create directory if it doesn't exist
    try:
        checkpoint_dir.mkdir(parents=True, exist_ok=True)
    except PermissionError:
        raise RuntimeError("Cannot create checkpoint directory - permission denied")

    # Try to acquire lock file first - fail fast if we can't write
    try:
        lock_fd = os.open(lock_file, os.O_CREAT | os.O_RDWR)
    except PermissionError:
        raise RuntimeError("Cannot create lock file - permission denied")

    try:
        # Non-blocking lock attempt
        fcntl.flock(lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except IOError:
        os.close(lock_fd)
        raise RuntimeError("Another crio process is running")

    try:
        # Now we have the lock, check for existing checkpoint
        if (checkpoint_dir / "checkpoint.exists").exists():
            # Restore
            try:
                subprocess.run(
                    ["criu", "restore", "-D", str(checkpoint_dir)], check=True
                )
            except subprocess.CalledProcessError:
                raise RuntimeError("Checkpoint restore failed")
        else:
            pid = os.fork()
            if pid == 0:  # Child
                # Child doesn't need the lock
                os.close(lock_fd)
                try:
                    yield
                    os.kill(os.getpid(), signal.SIGSTOP)
                except BaseException:
                    # Make sure child exits on any error
                    os._exit(1)
            else:  # Parent
                try:
                    # Wait for child to stop
                    os.waitpid(pid, 0)
                    # Try checkpoint
                    subprocess.run(
                        ["criu", "dump", "-t", str(pid), "-D", str(checkpoint_dir)],
                        check=True,
                    )
                    # Mark success
                    (checkpoint_dir / "checkpoint.exists").touch()
                except BaseException:
                    # Kill child if anything went wrong
                    os.kill(pid, signal.SIGKILL)
                    raise
                finally:
                    # Clean up parent
                    os._exit(0)
    finally:
        # Release lock and close file descriptor
        fcntl.flock(lock_fd, fcntl.LOCK_UN)
        os.close(lock_fd)
