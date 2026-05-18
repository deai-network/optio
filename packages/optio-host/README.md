# optio-host

Local-or-remote host abstraction plus the log/deliverables coordination protocol used by optio task types.

`optio-host` lets a task author run shell commands, manage workdirs, and stream files **without caring whether the work happens locally or on a remote host over SSH**. It also provides a small line-based protocol that long-running worker processes can use to report progress and produce file deliverables.

## What's in the box

- **`Host` Protocol + `LocalHost` / `RemoteHost` / `make_host()`** — uniform interface for running commands, opening port forwards, transferring files, and tearing down workdirs. SSH details (auth, multiplexing, channel cleanup) are hidden behind `asyncssh`.
- **`HookContext`** — small carrier passed into task hooks so they can run additional host commands, request file fetches, and report progress without touching `optio-core` internals.
- **`optio_host.protocol`** — a line-oriented session driver. A long-running process on the host writes lines prefixed `STATUS:`, `DELIVERABLE:`, `DONE`, or `ERROR`. The driver tails the log, dispatches progress events, fetches deliverable files, and resolves the session on `DONE` / `ERROR`.
- **`create_download_task(...)`** — a ready-made optio task that downloads a file from a remote host with progress reporting and integrity checks.

## When to use it

You're building an [optio](https://github.com/deai-network/optio) task type that needs to run work on a host — local or remote — and you want:

- one abstraction that works in both modes,
- a structured way for the running process to talk back to optio (progress + deliverables),
- SSH transport handled for you.

If you're writing the end-user task type directly (not consuming this library from another optio task package), you probably want `optio-core` instead.

## Installation

```bash
pip install optio-host
```

`optio-host` depends on `optio-core` and `asyncssh`. Python 3.11+.

## Minimal example

```python
from optio_host import make_host, SSHConfig

# Local
async with make_host(ssh=None) as host:
    result = await host.run(["uname", "-a"])
    print(result.stdout)

# Remote
ssh = SSHConfig(host="worker-1", user="optio", key_path="~/.ssh/id_optio")
async with make_host(ssh=ssh) as host:
    result = await host.run(["uname", "-a"])
    print(result.stdout)
```

## License

Apache-2.0.
