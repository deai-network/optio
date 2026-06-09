# optio-claudecode

Run Anthropic Claude Code as an `optio` task — either as a local
subprocess or on a remote host over SSH — with the interactive TUI
embedded in the optio dashboard via an iframe widget served by `ttyd`.

## Install

```bash
pip install optio-claudecode
```

Requires Python 3.11+. Pulls `optio-core`, `optio-host`, and `asyncssh`.

On task start the package auto-installs the host binaries it needs
unless told otherwise:

* `claude` — via Anthropic's vendor script (`https://claude.ai/install.sh`)
* `ttyd` — static binary from `tsl0922/ttyd` GitHub Releases

## Quick start

```python
from optio_claudecode import (
    ClaudeCodeTaskConfig,
    create_claudecode_task,
)

def get_tasks():
    return [
        create_claudecode_task(
            process_id="example-task",
            name="Example",
            config=ClaudeCodeTaskConfig(
                consumer_instructions="Please write a haiku about MongoDB.",
                credentials_json=load_user_creds_from_db(user_id),
                # Optional: skip interactive permission prompts for autonomous flows.
                permission_mode="bypassPermissions",
            ),
        )
    ]
```

`credentials_json` is treated as an opaque payload and written verbatim
to `<workdir>/home/.claude/.credentials.json` (mode 0600) before claude
launches. Format follows whatever Anthropic's CLI currently expects.

## How it works

Each task gets a workdir tempdir (`/tmp/optio-claudecode-<uuid>/`). The
ttyd process is launched with `HOME=<workdir>/home`, so claude reads
all its state — credentials, settings, session history — strictly from
the per-task workdir and never touches the host user's real
`~/.claude/`. Two tasks on the same host can run concurrently without
shared-state races.

The agent is given a `<workdir>/CLAUDE.md` that includes the
`optio.log` coordination protocol — `STATUS:` / `DELIVERABLE:` /
`DONE` / `ERROR` — verbatim from `optio_host.agents`. The same protocol
is used by `optio-opencode`, so the same `consumer_instructions` can be
swapped between the two packages.

See `docs/2026-05-28-optio-claudecode-design.md` for the full design.

## Conversation mode

With `mode="conversation"` the task runs claude headlessly (no ttyd, no
iframe) over its bidirectional stream-json stdio protocol, and the
launching code receives a live `Conversation` object — send messages,
subscribe to events and answers, gate tool permissions, interrupt,
close:

```python
config = ClaudeCodeTaskConfig(
    consumer_instructions=None,   # defaults to a plain conversation prompt
    credentials_json=...,
    mode="conversation",
    host_protocol=False,          # no optio.log keyword channel
    permission_mode="acceptEdits",
)
# ... register the task, then:
conv = await optio.launch_and_await_result("example-task", session_id=None)
conv.on_message(lambda text: print("claude:", text))
await conv.send("hello")
await conv.close()
```

All new config fields default to existing behavior (`mode="iframe"`,
`host_protocol=True`, `permission_gate=False`), so existing callers run
unchanged. See `docs/2026-06-10-claudecode-conversation-gate-design.md`.
