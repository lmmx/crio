# Debugging CRIU Process Checkpointing

This guide helps diagnose and test process checkpointing with CRIU.

We'll use a minimal test case to verify CRIU functionality before attempting more complex Python imports.

## Prerequisites

1. Install CRIU:
```bash
sudo add-apt-repository ppa:criu/ppa
sudo apt update
sudo apt install criu
```

2. Set capabilities:
```bash
sudo setcap cap_checkpoint_restore+eip $(which criu)
```

3. Add permissions:

For your specific user, assuming `which criu` gives `/usr/sbin/criu`:

```bash
louis ALL = NOPASSWD:/usr/sbin/criu dump *
louis ALL = NOPASSWD:/usr/sbin/criu restore *
```

or for all users:

```
ALL ALL = NOPASSWD:/usr/sbin/criu dump *
ALL ALL = NOPASSWD:/usr/sbin/criu restore *
```

or for the group `users`:

```
%users ALL = NOPASSWD:/usr/sbin/criu dump *
%users ALL = NOPASSWD:/usr/sbin/criu restore *
```

## Test Files

Create a minimal test file `debug.py` (provided in this repo at `debugging/debug.py`):

```python
import os
import signal
import time

# Minimal process that just waits for signals
def main():
    # Print PID for checkpointing
    print(f"Process {os.getpid()} ready for checkpointing")
    
    # Block until signaled
    signal.pause()

if __name__ == "__main__":
    main()
```

## Testing Process

1. Start the test process:
```bash
python3 debug.py
# Note the printed PID, e.g., 123456
```

2. In another terminal, create checkpoint directory and dump process state:
```bash
sudo mkdir -p /tmp/criu-test
sudo criu dump -t 123456 \
    --shell-job \
    -D /tmp/criu-test \
    --track-mem
```

3. The original process should exit. Now restore it:
```bash
sudo criu restore --shell-job -D /tmp/criu-test
```

## Common Issues & Solutions

### Permission Errors
- If using ~/.cache or other user directories, ensure proper permissions
- For testing, use /tmp/criu-test which avoids permission issues
- For production, ensure directories are created with correct ownership

### CUDA Warning
```
Warn (cuda_plugin.c:474): cuda_plugin: check that cuda-checkpoint is present in $PATH
```
- This warning can be ignored if not using CUDA
- If using CUDA, ensure cuda-checkpoint is in PATH:
```bash
export PATH=$PATH:~/opt/bin  # adjust path as needed
```

### Page Spicing Error
```
Error (criu/page-xfer.c:263): Unable to spice data: Invalid argument
```
- Add --track-mem flag to criu dump command
- Avoid complex memory operations before checkpointing

### Cannot Restore (File Exists)
```
Error (criu/cr-restore.c:1228): Can't fork for PID: File exists
```
- Don't use --leave-running when dumping
- Ensure original process has exited before restore
- Clean up /tmp/criu-test between attempts

## Production Usage Notes

1. **Directory Management**
   - Use consistent paths with proper permissions
   - Clean up old checkpoints
   - Handle directory creation and permissions in library code

2. **Process Flow**
   - Start process
   - Create checkpoint (process freezes)
   - Original process exits
   - Later restore from checkpoint

3. **Memory Considerations**
   - Minimize memory operations before checkpoint
   - Use --track-mem for better memory handling
   - Consider pre-fork strategies for complex imports

## Testing Your Own Code

Once basic CRIU functionality is verified with debug.py:
1. Start with minimal imports
2. Add complexity gradually
3. Test checkpoint/restore at each step
4. Monitor memory usage and patterns
