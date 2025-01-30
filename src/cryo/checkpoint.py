import fcntl
import os
import signal
import subprocess
import time
from contextlib import contextmanager
from pathlib import Path


@contextmanager
def checkpoint():
    checkpoint_dir = Path("/tmp/crio")
    lock_file = checkpoint_dir / "crio.lock"
    lock_fd = None

    try:
        # Create checkpoint directory
        try:
            checkpoint_dir.mkdir(parents=True, exist_ok=True)
        except PermissionError:
            raise RuntimeError("Cannot create checkpoint directory - permission denied")

        # Acquire lock file
        try:
            lock_fd = os.open(lock_file, os.O_CREAT | os.O_RDWR)
            fcntl.flock(lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except (PermissionError, IOError) as e:
            if lock_fd is not None:
                os.close(lock_fd)
            if isinstance(e, PermissionError):
                raise RuntimeError("Cannot create lock file - permission denied")
            else:
                raise RuntimeError("Another crio process is running")

        # Check for existing checkpoint
        if (checkpoint_dir / "checkpoint.exists").exists():
            try:
                subprocess.run(
                    ["criu", "restore", "-D", str(checkpoint_dir)], check=True
                )
            except subprocess.CalledProcessError:
                raise RuntimeError("Checkpoint restore failed")
        else:
            pid = os.fork()

            if pid == 0:  # Child process
                # Clean up lock in child process
                if lock_fd is not None:
                    os.close(lock_fd)
                try:
                    yield  # Run imports
                    os.kill(os.getpid(), signal.SIGSTOP)  # Suspend self
                except BaseException:
                    os._exit(1)
            else:  # Parent process
                try:
                    # Wait for child to stop with timeout
                    start_time = time.time()
                    while True:
                        try:
                            _, status = os.waitpid(pid, os.WUNTRACED)
                            if os.WIFSTOPPED(status):
                                break
                        except ProcessLookupError:
                            raise RuntimeError("Child process exited unexpectedly")

                        if time.time() - start_time > 5:  # 5 second timeout
                            raise RuntimeError(
                                "Child process failed to stop within timeout"
                            )
                        time.sleep(0.1)  # Prevent busy waiting

                    # Create checkpoint
                    try:
                        subprocess.run(
                            ["criu", "dump", "-t", str(pid), "-D", str(checkpoint_dir)],
                            check=True,
                        )
                        (checkpoint_dir / "checkpoint.exists").touch()
                    except subprocess.CalledProcessError:
                        raise RuntimeError("Checkpoint creation failed")
                except BaseException:
                    try:
                        os.kill(pid, signal.SIGKILL)  # Clean up child process
                    except ProcessLookupError:
                        pass  # Child already gone
                    raise
                finally:
                    os._exit(0)  # Parent always exits after handling child
    finally:
        # Clean up lock file and descriptor
        if lock_fd is not None:
            fcntl.flock(lock_fd, fcntl.LOCK_UN)
            os.close(lock_fd)
            try:
                lock_file.unlink()
            except FileNotFoundError:
                pass
