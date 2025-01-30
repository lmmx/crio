import os
import signal
import time

# Don't do any imports or memory allocation in the main process
# Just block waiting for signals
def main():
    # Print out our PID
    print(f"Process {os.getpid()} ready for checkpointing")
    
    # Use signals to coordinate - pause until we receive SIGCONT
    signal.pause()

if __name__ == "__main__":
    main()