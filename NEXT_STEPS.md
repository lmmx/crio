I am trying to do a routine as described below:

```
# crio

> Freeze Python processes after imports using CRIU (Checkpoint/Restore In Userspace)

By capturing the state of your interpreter at a desired point in time — right
after heavy imports — `crio` enables near-instant startup in subsequent runs.

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
```

I have a demo:

```
import crio

with crio.checkpoint():
    import mvdef

print(mvdef)
```

I expect this to print out the module repr for mvdef on the 1st run (which will save the checkpoint) and also to resume from the print statement on 2nd run (which will reload from checkpoint and not actually run the import in the context manager block)

I so far have managed to achieve:

- save to checkpoint
- reloadable checkpoint (but requires manually killing a process)

I am unsure why this process is still hanging around (it was supposed to be handled by the forking logic), nor why the 1st pass is not completing (it doesn't reach the print statement) but instead exitting once it has saved the checkpoint.

There is an earlier version in the repo `ckpt_early.py` which is slightly different (it prints the
warnings) but otherwise has the same behaviour as the more recent version `ckpt_forked.py`.
I also had an attempt using `os.v
