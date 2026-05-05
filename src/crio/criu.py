import os
import subprocess
from pathlib import Path

__all__ = ("restore", "dump")

_COMMON_FLAGS = (
    "--shell-job",
    "--skip-in-flight",
    "--ext-unix-sk",
    "--file-locks",
    "--link-remap",
    "--manage-cgroups",
)


def restore(image_dir: Path) -> None:
    """Replace the current process via criu restore (never returns)."""
    os.execvp(
        "sudo",
        [
            "sudo",
            "criu",
            "restore",
            "-D",
            str(image_dir),
            *_COMMON_FLAGS,
        ],
    )


def dump(pid: int, image_dir: Path) -> None:
    """Snapshot a running process, leaving it alive afterwards."""
    _result = subprocess.run(
        [
            "sudo",
            "criu",
            "dump",
            "-t",
            str(pid),
            "-D",
            str(image_dir),
            "--leave-running",
            *_COMMON_FLAGS,
        ],
        check=True,
        capture_output=True,
        text=True,
    )
