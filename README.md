# crio

> Freeze Python processes after imports using CRIU (Checkpoint/Restore In Userspace)

By capturing the state of your interpreter at a desired point in time — right
after heavy imports — `crio` enables near-instant startup in subsequent runs.

## Installation

```shell
pip install crio
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

- Run the imports
- Get the process ID (PID) of the Python program
- Send the SIGSTOP signal, suspending its own process
- Use `criu` to dump the suspended Python process to disk
- Reload the checkpoint and continue after the context manager block
