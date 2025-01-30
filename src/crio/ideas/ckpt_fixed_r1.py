import fcntl
import hashlib
import json
import os
import signal
import subprocess
import sys
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
        "python_version": sys.version,
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
    helper_pid = None

    try:
        for dir_path, err_msg in [
            (base_checkpoint_dir, "checkpoint directory"),
            (tmp_checkpoint_dir, "temporary checkpoint directory"),
        ]:
            try:
                dir_path.mkdir(parents=True, exist_ok=True)
            except PermissionError:
                raise RuntimeError(f"Cannot create {err_msg} - permission denied")

        symlink_path = base_checkpoint_dir / "ckpt"
        if not symlink_path.exists():
            symlink_path.symlink_to(tmp_checkpoint_dir)

        try:
            lock_fd = os.open(lock_file, os.O_CREAT | os.O_RDWR)
            fcntl.flock(lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except (PermissionError, BlockingIOError) as e:
            if lock_fd is not None:
                os.close(lock_fd)
            raise RuntimeError("Another crio process is running or permission denied")

        if (tmp_checkpoint_dir / "checkpoint.exists").exists():
            print("Found existing checkpoint, attempting restore...")
            try:
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
            except Exception as e:
                print(f"Restore failed: {e}")
                clear_checkpoints(context)
                raise RuntimeError("Checkpoint restore failed")

        else:
            helper_pid = os.fork()
            if helper_pid == 0:
                # Helper process
                main_pid = os.getppid()
                try:
                    _, status = os.waitpid(main_pid, os.WUNTRACED)
                    if os.WIFSTOPPED(status):
                        subprocess.run(
                            [
                                "sudo",
                                "criu",
                                "dump",
                                "-t",
                                str(main_pid),
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
                        os.kill(main_pid, signal.SIGCONT)
                finally:
                    os._exit(0)
            else:
                # Main process runs the code inside the context manager
                yield
                # Signal self to stop after imports
                os.kill(os.getpid(), signal.SIGSTOP)
                # Wait for helper to finish
                os.waitpid(helper_pid, 0)

    except Exception as e:
        print(f"Checkpoint error: {e}")
        raise
    finally:
        if lock_fd is not None:
            try:
                fcntl.flock(lock_fd, fcntl.LOCK_UN)
                os.close(lock_fd)
                lock_file.unlink()
            except FileNotFoundError:
                pass

def clear_checkpoints(context: dict | None = None) -> None:
    base_dir = _get_checkpoint_path()
    if context is not None:
        base_checkpoint_dir = base_dir / _generate_checkpoint_id(context)
        tmp_checkpoint_dir = Path(f"/tmp/criu-{_generate_checkpoint_id(context)}")
        if base_checkpoint_dir.exists():
            import shutil

            symlink_path = base_checkpoint_dir / "ckpt"
            if symlink_path.is_symlink():
                symlink_path.unlink()
            shutil.rmtree(base_checkpoint_dir)
            if tmp_checkpoint_dir.exists():
                shutil.rmtree(tmp_checkpoint_dir)
    else:
        import shutil
        import glob

        shutil.rmtree(base_dir)
        base_dir.mkdir(parents=True)
        for tmp_dir in glob.glob("/tmp/criu-*"):
            shutil.rmtree(tmp_dir)