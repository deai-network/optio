# optio-host

Local-or-remote host abstraction for optio task types. The agent-coordination protocol that used to live here now lives in [optio-agents](../optio-agents).

`optio-host` lets a task author run shell commands, manage workdirs, and stream files **without caring whether the work happens locally or on a remote host over SSH**.

## What's in the box

- **`Host` Protocol + `LocalHost` / `RemoteHost` / `make_host()`** — uniform interface for running commands, opening port forwards, transferring files, and tearing down workdirs. SSH details (auth, multiplexing, channel cleanup) are hidden behind `asyncssh`.
- **`RunResult` / `HostCommandError`** — the result and error types produced by `Host.run_command`.
- **`create_download_task(...)`** — a ready-made optio task that downloads a file from a remote host with progress reporting and integrity checks.
- For the log/deliverables coordination protocol, the keyword parser, and **HookContext**, see **[optio-agents](../optio-agents)**.

## When to use it

You're building an [optio](https://github.com/deai-network/optio) task type that needs to run work on a host — local or remote — and you want:

- one abstraction that works in both modes,
- a structured way for the running process to talk back to optio (progress + deliverables) — see [optio-agents](../optio-agents),
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
