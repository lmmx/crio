# crio

> Freeze Python processes after imports using CRIU (Checkpoint/Restore In Userspace)

By capturing the state of your interpreter at a desired point in time — right
after heavy imports — `crio` enables near-instant startup in subsequent runs.

## Installation

```shell
pip install git+https://github.com/lmmx/crio.git # pip install crio
```

### Dependencies

Requires `criu`. If it's not in your distribution/PPAs add this one or build from source.

```
sudo add-apt-repository ppa:criu/ppa
sudo apt update -y
sudo apt install criu -y
```

If `criu check --unprivileged` reports

> `CRIU needs to have the CAP_SYS_ADMIN or the CAP_CHECKPOINT_RESTORE capability`

then activate it first:

```bash
sudo setcap cap_checkpoint_restore+eip $(which criu)
```

## Usage

Write your script with a `crio.checkpoint()` context manager around the imports

```py
import crio

with crio.checkpoint():
    import torch

print(torch.cuda.is_available())
```

Behind the scenes, crio will check for an existing checkpoint to reload,
and if it doesn't find a pre-existing one:

- Fork the process: the child runs the imports, the parent orchestrates the checkpoint
- Once imports are complete, the child signals readiness and waits
- The parent calls `criu dump` to snapshot the child's memory to disk
- The child is released and continues normally through the rest of the script

On subsequent runs, crio detects the existing checkpoint and uses `criu restore`
to replace the current process with the saved snapshot — resuming directly after
the context manager block with all imports already in memory.
