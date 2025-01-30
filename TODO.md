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
