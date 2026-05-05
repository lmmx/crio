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

    -------------------- Lock file handling during a fork -----------------------------

    There is also the problem of the lock being duplicated.

    When we `fork()`, both parent and child inherit a copy of the lock-file descriptor.
    You then close the FD in the child branch (so that the child does not hold the
    lock), but Python’s context-manager will still run the `finally:` block in the
    **child** (because in this design, the “child” continues normal flow once
    checkpointing is done). The code in `finally:` calls

    ```python
    fcntl.flock(lock_fd, fcntl.LOCK_UN)
    os.close(lock_fd)
    lock_file.unlink()
    ```

    which blows up if the child has already closed the descriptor. Meanwhile, the
    **parent** never returns to Python’s `finally:` code at all because it calls
    `os._exit(0)` after checkpointing.

    To avoid it:

    - The parent takes the lock (and thus owns the valid `lock_fd`).
    - The child quickly closes its *inherited* copy of that FD.
    - At the end, the *parent* manually unlocks and removes the file.
    - The child does *not* run that unlock code, because it never truly held the lock.

    But since the parent calls `os._exit(0)`, it never returns to the normal Python
    context-manager exit. So the code inside `finally:` is only seen by the child.

    To fix this we do the following:

    1. Set a flag to indicate **am I the child or the parent?**
    2. If I am the child, skip the lock-unlock portion in the `finally:` block (since
       the child has no valid FD).
    3. If I am the parent, perform the lock clean-up before calling `os._exit(0)`.
    """
    # Base user-level checkpoint directory
    base_checkpoint_dir = _get_checkpoint_path() / _generate_checkpoint_id(context)
    # Temporary checkpoint directory in /tmp
    tmp_checkpoint_dir = Path(f"/tmp/criu-{_generate_checkpoint_id(context)}")
    lock_file = base_checkpoint_dir / "crio.lock"
    lock_fd = None
    child_pid = None
    am_child = False

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

        # Acquire lock file in the parent (before fork)
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
            # Release lock before exec
            print("Found existing checkpoint, attempting restore...")
            if lock_fd is not None:
                fcntl.flock(lock_fd, fcntl.LOCK_UN)
                os.close(lock_fd)
            os.execvp(
                "sudo",
                [
                    "sudo",
                    "criu",
                    "restore",
                    "-D",
                    str(tmp_checkpoint_dir),
                    "--shell-job",
                    # "--unprivileged",
                    "--skip-in-flight",
                    "--ext-unix-sk",
                    "--file-locks",
                    "--link-remap",
                    "--manage-cgroups",
                ],
            )
            # never reaches here — current process is replaced

        # If no existing checkpoint, create a new checkpoint
        # Pipe for readiness signalling
        ready_r, ready_w = os.pipe()

        # If no existing checkpoint, create a new checkpoint
        pid = os.fork()
        if pid == 0:  # Child process
            am_child = True
            try:
                if lock_fd is not None:
                    os.close(lock_fd)
                    lock_fd = None

                yield  # user imports run here

                # Signal readiness via file (not SIGSTOP)
                (tmp_checkpoint_dir / "child.ready").touch()

                # Sleep loop — CRIU captures us here (S state, not T state).
                # On restore, dump.complete already exists on disk → immediate break.
                while not (tmp_checkpoint_dir / "dump.complete").exists():
                    time.sleep(0.05)

                # Continue to post-with-block code

            except Exception as e:
                print(f"Child process error: {e}")
                os._exit(1)
        else:  # Parent process
            child_pid = pid
            try:
                # Poll for child readiness
                start_time = time.time()
                while not (tmp_checkpoint_dir / "child.ready").exists():
                    if time.time() - start_time > 120:
                        raise RuntimeError("Child failed to become ready")
                    time.sleep(0.05)

                # Child is in sleep loop. CRIU ptrace-freezes it for dump.
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
                        "--shell-job",
                        "--leave-running",
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

                # Tell child dump is done — it breaks out of sleep loop
                (tmp_checkpoint_dir / "dump.complete").touch()
                (tmp_checkpoint_dir / "checkpoint.exists").touch()
                print("Checkpoint created successfully")

                # Wait for child to finish normally
                os.waitpid(pid, 0)

            except Exception:
                try:
                    if child_pid:
                        os.kill(child_pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
                raise
            finally:
                os._exit(0)

    except Exception as e:
        print(f"Checkpoint error: {e}")
        raise
    finally:
        # This final block only gets run by whichever side continues in Python,
        # which in this design is the CHILD.
        if not am_child:
            # "Parent" normally never gets here because of `os.exit(0)` above;
            # but if we removed the `_exit`, we'd do:
            # Clean up lock file and descriptor
            if lock_fd is not None:
                try:
                    fcntl.flock(lock_fd, fcntl.LOCK_UN)
                    os.close(lock_fd)
                    lock_file.unlink()
                except (OSError, FileNotFoundError):
                    pass
        else:
            # We are the child. The child's FD is already closed, so skip the
            # flock(...) / close(...) calls. But we *can* remove the lock file on disk
            # so it doesn't linger:
            if lock_file.exists():
                lock_file.unlink()

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
