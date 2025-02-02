import fcntl
import hashlib
import json
import os
import signal
import subprocess
import time
from contextlib import contextmanager
from pathlib import Path

from platformdirs import user_cache_dir

__all__ = (
    "_get_checkpoint_path",
    "_generate_checkpoint_id",
    "checkpoint",
    "clear_checkpoints",
)


def _get_checkpoint_path() -> Path:
    """Get the directory for storing checkpoints"""
    base_dir = Path(user_cache_dir("crio"))
    base_dir.mkdir(parents=True, exist_ok=True)
    return base_dir


def _generate_checkpoint_id(context: dict | None = None) -> str:
    """Generate unique identifier for checkpoint based on Python environment"""
    checkpoint_context = {
        "python_version": os.sys.version,
        # Only include environment vars that affect Python imports
        "env": {
            k: v
            for k, v in os.environ.items()
            if k.startswith(("PYTHONPATH", "PYTHONHOME"))
        },
    }

    if context is not None:
        checkpoint_context.update(context)

    context_str = json.dumps(checkpoint_context, sort_keys=True)
    return hashlib.sha256(context_str.encode()).hexdigest()[:16]


@contextmanager
def checkpoint(context: dict | None = None):
    # Base user-level checkpoint directory
    base_checkpoint_dir = _get_checkpoint_path() / _generate_checkpoint_id(context)
    
    # Temporary checkpoint directory in /tmp
    tmp_checkpoint_dir = Path(f"/tmp/criu-{_generate_checkpoint_id(context)}")
    
    lock_file = base_checkpoint_dir / "crio.lock"
    lock_fd = None

    try:
        # Create base checkpoint directory
        try:
            base_checkpoint_dir.mkdir(parents=True, exist_ok=True)
        except PermissionError:
            raise RuntimeError("Cannot create checkpoint directory - permission denied")

        # Create tmp checkpoint directory
        try:
            tmp_checkpoint_dir.mkdir(parents=True, exist_ok=True)
        except PermissionError:
            raise RuntimeError("Cannot create temporary checkpoint directory - permission denied")

        # Create symlink from base to tmp if it doesn't exist
        symlink_path = base_checkpoint_dir / "ckpt"
        if not symlink_path.exists():
            symlink_path.symlink_to(tmp_checkpoint_dir)

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
        if (tmp_checkpoint_dir / "checkpoint.exists").exists():
            try:
                subprocess.run(
                    [
                        "sudo", "criu",
                        "restore",
                        "-D", str(tmp_checkpoint_dir),
                        "--unprivileged",
                        "--shell-job",
                        "--skip-in-flight",
                        "--ext-unix-sk",
                        "--file-locks",
                        "--link-remap",
                        "--manage-cgroups",
                    ],
                    check=True,
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
                            [
                                "sudo", "criu",
                                "dump",
                                "-t", str(pid),
                                "-D", str(tmp_checkpoint_dir),
                                "--unprivileged",
                                "--shell-job",
                                "--leave-running",  # Don't kill the process after dumping
                                "--skip-in-flight",  # Skip in-flight TCP connections
                                "--ext-unix-sk",  # External unix sockets
                                "--file-locks",  # File locks
                                "--link-remap",  # Handle symlinks
                                "--manage-cgroups",  # Handle cgroups
                            ],
                            check=True,
                        )
                        (tmp_checkpoint_dir / "checkpoint.exists").touch()
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


def clear_checkpoints(context: dict | None = None) -> None:
    """Clear all checkpoints or those matching a specific context"""
    base_dir = _get_checkpoint_path()
    if context is not None:
        # Remove specific checkpoint
        base_checkpoint_dir = base_dir / _generate_checkpoint_id(context)
        tmp_checkpoint_dir = Path(f"/tmp/criu-{_generate_checkpoint_id(context)}")
        
        # Remove symlink and base checkpoint dir
        if base_checkpoint_dir.exists():
            import shutil
            
            # Remove symlink first
            symlink_path = base_checkpoint_dir / "ckpt"
            if symlink_path.is_symlink():
                symlink_path.unlink()
            
            # Remove base checkpoint directory
            shutil.rmtree(base_checkpoint_dir)
        
        # Remove tmp checkpoint directory
        if tmp_checkpoint_dir.exists():
            import shutil
            shutil.rmtree(tmp_checkpoint_dir)
    else:
        # Remove all checkpoints
        import shutil
        import glob
        
        # Remove user cache dir checkpoints
        shutil.rmtree(base_dir)
        base_dir.mkdir(parents=True)
        
        # Remove all /tmp criu checkpoint directories
        for tmp_dir in glob.glob("/tmp/criu-*"):
            shutil.rmtree(tmp_dir)