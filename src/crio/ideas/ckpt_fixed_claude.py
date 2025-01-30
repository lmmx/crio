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
        # Directory creation and validation
        for dir_path, err_msg in [
            (base_checkpoint_dir, "checkpoint directory"),
            (tmp_checkpoint_dir, "temporary checkpoint directory"),
        ]:
            try:
                dir_path.mkdir(parents=True, exist_ok=True)
            except PermissionError:
                raise RuntimeError(f"Cannot create {err_msg} - permission denied")

        # Create symlink if it doesn't exist
        symlink_path = base_checkpoint_dir / "ckpt"
        if not symlink_path.exists():
            symlink_path.symlink_to(tmp_checkpoint_dir)

        # Lock handling
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
        checkpoint_exists = (tmp_checkpoint_dir / "checkpoint.exists").exists()

        if checkpoint_exists:
            print("Found existing checkpoint, attempting restore...")
            restore_process = subprocess.run(
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
                capture_output=True,
                text=True,
            )

            if restore_process.returncode != 0:
                print(f"Restore failed. Stdout: {restore_process.stdout}")
                print(f"Restore failed. Stderr: {restore_process.stderr}")
                clear_checkpoints(context)
                raise RuntimeError("Checkpoint restore failed")

            yield
            return

        # No existing checkpoint - create new one
        pid = os.fork()

        if pid == 0:  # Child process
            # Clean up lock in child
            if lock_fd is not None:
                os.close(lock_fd)

            try:
                yield
                # Signal parent we're ready for checkpoint
                os.kill(os.getpid(), signal.SIGSTOP)
                # Continue execution after checkpoint
                return
            except Exception as e:
                print(f"Child process error: {e}")
                os._exit(1)

        else:  # Parent process
            child_pid = pid
            try:
                # Wait for child to stop
                _, status = os.waitpid(pid, os.WUNTRACED)
                if not os.WIFSTOPPED(status):
                    raise RuntimeError("Child process exited unexpectedly")

                print(f"Creating checkpoint for PID {pid}")
                dump_result = subprocess.run(
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
                    capture_output=True,
                    text=True,
                )

                if dump_result.returncode != 0:
                    print(f"Dump failed. Stdout: {dump_result.stdout}")
                    print(f"Dump failed. Stderr: {dump_result.stderr}")
                    raise RuntimeError("Checkpoint creation failed")

                # Mark checkpoint as existing
                (tmp_checkpoint_dir / "checkpoint.exists").touch()

                # Continue child process
                os.kill(pid, signal.SIGCONT)

                # Wait for child to complete
                _, status = os.waitpid(pid, 0)
                if os.WEXITSTATUS(status) != 0:
                    raise RuntimeError("Child process failed")

                return

            except Exception as e:
                if child_pid:
                    try:
                        os.kill(child_pid, signal.SIGKILL)
                    except ProcessLookupError:
                        pass
                raise RuntimeError(f"Checkpoint failed: {e}")

    finally:
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
        import glob
        import shutil

        # Remove user cache dir checkpoints
        shutil.rmtree(base_dir)
        base_dir.mkdir(parents=True)
        # Remove all /tmp criu checkpoint directories
        for tmp_dir in glob.glob("/tmp/criu-*"):
            shutil.rmtree(tmp_dir) @ contextmanager
