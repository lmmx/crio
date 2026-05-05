import glob
import hashlib
import json
import os
import shutil
import sys
from pathlib import Path

from platformdirs import user_cache_dir

__all__ = ("CheckpointPaths", "clear_checkpoints")

CACHE_APP_NAME = "crio"


def _generate_checkpoint_id(context: dict | None = None) -> str:
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


class CheckpointPaths:
    """Resolved paths for a single checkpoint identity."""

    def __init__(self, context: dict | None = None) -> None:
        ckpt_id = _generate_checkpoint_id(context)
        self.base_dir = Path(user_cache_dir(CACHE_APP_NAME)) / ckpt_id
        self.tmp_dir = Path(f"/tmp/criu-{ckpt_id}")
        self.lock_file = self.base_dir / "crio.lock"
        self.symlink = self.base_dir / "ckpt"

    # Sentinel files
    @property
    def checkpoint_exists(self) -> Path:
        return self.tmp_dir / "checkpoint.exists"

    @property
    def child_ready(self) -> Path:
        return self.tmp_dir / "child.ready"

    @property
    def dump_complete(self) -> Path:
        return self.tmp_dir / "dump.complete"

    def has_checkpoint(self) -> bool:
        return self.checkpoint_exists.exists()

    def ensure_dirs(self) -> None:
        for dir_path in (self.base_dir, self.tmp_dir):
            try:
                dir_path.mkdir(parents=True, exist_ok=True)
            except PermissionError:
                raise RuntimeError(f"Cannot create {dir_path} — permission denied")
        if not self.symlink.exists():
            self.symlink.symlink_to(self.tmp_dir)


def clear_checkpoints(context: dict | None = None) -> None:
    if context is not None:
        paths = CheckpointPaths(context)
        if paths.base_dir.exists():
            if paths.symlink.is_symlink():
                paths.symlink.unlink()
            shutil.rmtree(paths.base_dir)
        if paths.tmp_dir.exists():
            shutil.rmtree(paths.tmp_dir)
    else:
        base = Path(user_cache_dir(CACHE_APP_NAME))
        shutil.rmtree(base, ignore_errors=True)
        base.mkdir(parents=True, exist_ok=True)
        for tmp_dir in glob.glob("/tmp/criu-*"):
            shutil.rmtree(tmp_dir)
