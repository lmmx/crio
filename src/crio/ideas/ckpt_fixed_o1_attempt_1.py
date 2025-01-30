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
    """
    After `fork()`, you now have **two** processes:

    - The **parent** (original process) – used here as the “checkpointer”.
    - The **child** – which actually runs your user code (the stuff after `yield`).

    You want the child to survive and carry on once its memory state is saved. The
    parent’s only job is to orchestrate the checkpoint. So:

    1. Child: does the heavy imports, then `SIGSTOP`s itself.
    2. Parent: “sees” the child is stopped, runs `criu dump` on it, then
       `SIGCONT`s it.
    3. Child: continues from the checkpoint.
    4. Child: eventually leaves the `checkpoint()` context manager, goes on to the
       `print(...)`.
    5. Parent: calls `os._exit(0)` so it goes away quietly.
    """
    # Base user-level checkpoint directory
    base_checkpoint_dir = _get_checkpoint_path() / _generate_checkpoint_id(context)
    # Temporary checkpoint directory in /tmp
    tmp_checkpoint_dir = Path(f"/tmp/criu-{_generate_checkpoint_id(context)}")
    lock_file = base_checkpoint_dir / "crio.lock"
    lock_fd = None
    child_pid = None

    try:
        # Validate and create directories
        for dir_path, err_msg in [
            (base_checkpoint_dir, "checkpoint directory"),
            (tmp_checkpoint_dir, "temporary checkpoint directory"),
        ]:
            try:
                dir_path.mkdir(parents=True, exist_ok=True)
            except PermissionError:
                raise RuntimeError(f"Cannot create {err_msg} - permission denied")

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
            print("Found existing checkpoint, attempting restore...")
            try:
                # Use subprocess to restore
                restore_process = subprocess.Popen(
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
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    text=True,
                )

                # Wait for restore to complete
                stdout, stderr = restore_process.communicate()

                if restore_process.returncode != 0:
                    print(f"Restore failed. Stdout: {stdout}")
                    print(f"Restore failed. Stderr: {stderr}")
                    raise subprocess.CalledProcessError(
                        restore_process.returncode, restore_process.args
                    )

            except subprocess.CalledProcessError as e:
                print(f"Checkpoint restore failed: {e}")
                # Clean up the checkpoint if restore fails
                clear_checkpoints(context)
                raise RuntimeError("Checkpoint restore failed")
            else:
                yield
                return  # Exit the context manager early if restore succeeds

        # If no existing checkpoint, create a new checkpoint
        pid = os.fork()
        if pid == 0:  # Child process
            try:
                # Clean up lock in child process
                if lock_fd is not None:
                    os.close(lock_fd)

                # Yield control back to caller (in context manager body: imports etc.)
                yield

                # Stop ourselves so the parent can checkpoint
                os.kill(os.getpid(), signal.SIGSTOP)
            except Exception as e:
                print(f"Child process error: {e}")
                os._exit(1)
            finally:
                # Don't kill this process after the checkpoint, let normal flow continue
                pass  # os._exit(0)
        else:  # Parent process
            child_pid = pid
            try:
                # Wait for child to stop with timeout
                start_time = time.time()
                while True:
                    try:
                        _, status = os.waitpid(pid, os.WUNTRACED)
                        if os.WIFSTOPPED(status):
                            break
                    except ProcessLookupError:
                        print("Child process exited unexpectedly")
                        raise RuntimeError("Child process exited unexpectedly")

                    if time.time() - start_time > 5:  # 5 second timeout
                        print("Child process failed to stop within timeout")
                        raise RuntimeError(
                            "Child process failed to stop within timeout"
                        )

                    time.sleep(0.1)  # Prevent busy waiting

                # Create checkpoint with --leave-running
                print(f"Creating checkpoint for PID {pid}")
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
                        "--leave-running",  # Keep the process running
                        "--skip-in-flight",
                        "--ext-unix-sk",
                        "--file-locks",
                        "--link-remap",
                        "--manage-cgroups",
                    ],
                    check=True,
                    capture_output=True,
                    text=True,
                )

                # Continue the child process after checkpoint
                os.kill(pid, signal.SIGCONT)

                # Mark checkpoint as existing
                (tmp_checkpoint_dir / "checkpoint.exists").touch()
                print("Checkpoint created successfully")

            except Exception as e:
                try:
                    if child_pid:
                        print(f"Cleaning up child process {child_pid}")
                        os.kill(child_pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass  # Child already gone
                raise
            finally:
                # Parent always exits after handling child (performing checkpoint)
                # (as you don't want two copies of your program continuing in parallel)
                os._exit(0)

    except Exception as e:
        print(f"Checkpoint error: {e}")
        raise
    finally:
        # Clean up lock file and descriptor
        if lock_fd is not None:
            try:
                fcntl.flock(lock_fd, fcntl.LOCK_UN)
                os.close(lock_fd)
                lock_file.unlink()
            except FileNotFoundError:
                pass

    # The child returns normally, so everything after the context manager block can run
    return


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
