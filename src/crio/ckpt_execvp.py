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
    base_checkpoint_dir = _get_checkpoint_path() / _generate_checkpoint_id(context)
    tmp_checkpoint_dir = Path(f"/tmp/criu-{_generate_checkpoint_id(context)}")
    lock_file = base_checkpoint_dir / "crio.lock"
    lock_fd = None
    child_pid = None
    try:
        # Directory creation and locking logic remains the same
        for dir_path, err_msg in [
            (base_checkpoint_dir, "checkpoint directory"),
            (tmp_checkpoint_dir, "temporary checkpoint directory"),
        ]:
            dir_path.mkdir(parents=True, exist_ok=True)
        symlink_path = base_checkpoint_dir / "ckpt"
        if not symlink_path.exists():
            symlink_path.symlink_to(tmp_checkpoint_dir)
        lock_fd = os.open(lock_file, os.O_CREAT | os.O_RDWR)
        fcntl.flock(lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        if (tmp_checkpoint_dir / "checkpoint.exists").exists():
            print("Found existing checkpoint, attempting restore...")
            try:
                # Use execvp to replace current process with criu restore
                os.execvp(
                    "sudo",
                    [
                        "sudo",
                        "criu",
                        "restore",
                        "-D",
                        str(tmp_checkpoint_dir),
                        "--unprivileged",
                        "--shell-job",
                        "--skip-in-flight",
                        "--ext-unix-sk",
                        "--file-locks",
                        "--link-remap",
                        "--manage-cgroups",
                    ],
                )
            except OSError as e:
                print(f"Restore failed: {e}")
                raise RuntimeError("Checkpoint restore failed")
        else:
            pid = os.fork()
            if pid == 0:  # Child
                try:
                    yield  # Execute code inside the with block
                    os.kill(os.getpid(), signal.SIGSTOP)
                finally:
                    os._exit(0)
            else:  # Parent
                child_pid = pid
                # Wait for child to stop
                while True:
                    _, status = os.waitpid(pid, os.WUNTRACED)
                    if os.WIFSTOPPED(status):
                        break
                # Create checkpoint
                subprocess.run(
                    [
                        "sudo",
                        "criu",
                        "dump",
                        "-t",
                        str(pid),
                        "-D",
                        str(tmp_checkpoint_dir),
                        "--unprivileged",
                        "--shell-job",
                        "--leave-running",
                        "--skip-in-flight",
                        "--ext-unix-sk",
                        "--file-locks",
                        "--link-remap",
                        "--manage-cgroups",
                    ],
                    check=True,
                )
                (tmp_checkpoint_dir / "checkpoint.exists").touch()
                # Resume child and wait for exit
                os.kill(pid, signal.SIGCONT)
                os.waitpid(pid, 0)
    finally:
        # Cleanup logic remains
        if lock_fd is not None:
            fcntl.flock(lock_fd, fcntl.LOCK_UN)
            os.close(lock_fd)
            lock_file.unlink(missing_ok=True)

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
        import glob
        import shutil
        # Remove user cache dir checkpoints
        shutil.rmtree(base_dir)
        base_dir.mkdir(parents=True)
        # Remove all /tmp criu checkpoint directories
        for tmp_dir in glob.glob("/tmp/criu-*"):
            shutil.rmtree(tmp_dir)