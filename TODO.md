# TODO

Next steps to get this to work are:

- You can make a directory in `/tmp` e.g. `/tmp/criu-test-dir`
- Then you can symlink it in the user cache e.g. `ln -s /tmp/criu-test-4 ~/.cache/crio/ceff1b4f9c2618d3/ckpt`
- The user level dir can be used for the lockfile while the symlink inside it pointing to tmp can
  store the actual checkpoint directory filled with `.img` files
- `sudo` can therefore be used with the user cache directory symlink to the `/tmp/` subdirectory

```
sudo criu dump -t $PID --shell-job --track-mem -D ~/.cache/crio/ceff1b4f9c2618d3/ckpt/
```

## Issues

There are 2 versions, an earlier version and a "forked" one which is quieter (nicer to use) but
doesn't actually perform the proper routine (it only runs up to the checkpoint on 1st run and hangs
on 2nd run).

- It would be better to revert to the earlier version which at least completed the entire program
  when it saved the checkpoint, but I didn't mark explicitly which version that was (maybe in git
  repo?)

- I think `os.execvp` might be able to achieve the 'swapping out' desired but I haven't been able to
  make it work
