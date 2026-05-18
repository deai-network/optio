# optio-opencode

Run [opencode web](https://github.com/opencode-ai/opencode) as an [optio](https://github.com/deai-network/optio) task — local subprocess or remote over SSH — with opencode's UI reachable through optio's UI components.

## What it does

Given an `OpencodeTaskConfig` (workdir contents, prompt, deliverable callback), `optio-opencode`:

1. Provisions a fresh workdir on the chosen host (local or remote).
2. Writes `AGENTS.md` (base prompt + your instructions) and `opencode.json` (your config) into it.
3. Installs the opencode binary if missing (remote mode only).
4. Launches `opencode web` with a random auth password.
5. Registers the opencode UI as a widget that optio's UI components can embed via the widget proxy — SSH tunnel hidden from optio-api.
6. Tails a log file the LLM writes to and translates structured lines into optio events:
   - `STATUS: …` → `ctx.report_progress(percent, message)`
   - `DELIVERABLE: <path>` → fetches the file, invokes your `on_deliverable` callback
   - `DONE [summary]` → clean completion
   - `ERROR [message]` → failure
7. Cleans up workdir and SSH connection on teardown.

The same `OpencodeTaskConfig` works for local and remote modes; only `SSHConfig` differs.

## When to use it

You want an opencode-driven assistant session as a managed optio task — surfaced through optio's UI, with progress reporting and file deliverables — without writing the host management, log parsing, or widget plumbing yourself.

## Installation

```bash
pip install optio-opencode
```

Python 3.11+. Depends on `optio-core`, `optio-host`, and `asyncssh`.

## Minimal example

```python
from optio_opencode import create_opencode_task, OpencodeTaskConfig
from optio_host import SSHConfig

config = OpencodeTaskConfig(
    workdir_files={"AGENTS.md": "Do the thing.", "opencode.json": "{...}"},
    on_deliverable=lambda ctx, path, text: print(f"got {path}: {len(text)} bytes"),
    ssh=SSHConfig(host="worker-1", user="optio", key_path="~/.ssh/id_optio"),
)

task = create_opencode_task(config)
# Schedule / run via optio-core as usual.
```

Set `ssh=None` for local subprocess mode.

## License

Apache-2.0.
