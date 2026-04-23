# optio-opencode — Agent Cheatsheet

Run `opencode web` as an optio task.  Either a local subprocess or a
remote host reached over SSH; the optio dashboard embeds opencode's UI
via the widget proxy.

Full design: `docs/2026-04-22-optio-opencode-design.md`.

## Public API

```python
from optio_opencode import (
    create_opencode_task,
    OpencodeTaskConfig,
    SSHConfig,
    DeliverableCallback,
)

async def on_file(path: str, text: str) -> None:
    ...

task = create_opencode_task(
    process_id="my-task",
    name="My task",
    config=OpencodeTaskConfig(
        consumer_instructions="...",      # prepended with optio-opencode's base prompt
        opencode_config={"model": "..."},  # passthrough to opencode.json
        ssh=None,                          # None = local subprocess
        on_deliverable=on_file,
        install_if_missing=True,
    ),
    description="optional",
)
```

The returned `TaskInstance` has `ui_widget="iframe"` baked in.

## Log-file contract

optio-opencode tells opencode (via AGENTS.md) to append one line per
event to `./optio.log` with keywords:

- `STATUS: [N%] <msg>`
- `DELIVERABLE: <path>`
- `DONE[: summary]`
- `ERROR[: message]`

The Python side tails the file, dispatches by keyword, SFTPs
deliverable files back, decodes UTF-8, and invokes `on_deliverable`.
DONE / ERROR terminate the session; other keywords flow through as
progress updates / log entries.

## Operating modes

- **Local**: `asyncio.create_subprocess_exec("opencode", "web", ...)`.
  Workdir is a `tempfile.mkdtemp`.  Expects opencode pre-installed
  (or use `OPTIO_OPENCODE_BINARY_DIR`, below).
- **Remote**: single asyncssh connection multiplexes exec (install,
  launch, `tail -F`, teardown), SFTP, and local port forward.  Workdir
  is `/tmp/optio-opencode-<uuid>/` on the remote.

## Shipping a specific opencode binary

Set `OPTIO_OPENCODE_BINARY_DIR` in the worker's environment to a
directory matching opencode's build layout (i.e. containing per-target
subdirs like `opencode-linux-x64/bin/opencode`, `opencode-darwin-arm64/
bin/opencode`, `opencode-linux-x64-baseline-musl/bin/opencode`, …).  When
set, optio-opencode:

1. Detects the target host's OS / arch / libc / AVX2 via `uname`, `ldd`
   and `/proc/cpuinfo` (detection mirrors opencode's upstream install
   script so the subdir name is the one opencode's build would emit).
2. Resolves the matching binary inside `OPTIO_OPENCODE_BINARY_DIR`.
3. **Local mode:** runs that binary directly (bypasses any `opencode`
   on PATH).
4. **Remote mode:** SFTP-uploads the binary to `~/.local/bin/opencode`
   on the remote host (atomic via temp-file + rename), skipping the
   upload when the existing file's SHA-256 already matches.  Launches
   via the absolute path, so the user doesn't need `~/.local/bin` on
   their PATH for optio-opencode to work (though they'll benefit from
   having it on PATH for later manual invocations).

When the env var is unset, behavior falls back to the previous scheme:
local mode expects `opencode` on PATH, remote mode runs the upstream
curl installer if `opencode` is missing.

This is a bridge feature for shipping an iframe-embeddability fork of
opencode until those fixes land upstream; once upstream ships, the env
var's only remaining use is pinning to a specific build.

**Where the fork lives:** the patched binaries are built from
<https://github.com/csillag/opencode> on branch
`csillag/make-web-embeddable-in-iframes` — three small orthogonal
patches (relative vite base, CSP `'unsafe-eval'`, `getCurrentUrl`
honoring localStorage).  Upstream PR:
<https://github.com/anomalyco/opencode/pull/23912>.  To populate
`OPTIO_OPENCODE_BINARY_DIR`: check out that branch, run
`bun install && bun run --cwd packages/opencode build`, and point the
env var at the resulting `packages/opencode/dist/` directory (which
contains per-target subdirs such as `opencode-linux-x64/bin/opencode`).

## Testing

Unit + local integration: `pytest tests/` (needs MongoDB via Docker).

Remote integration: `pytest tests/test_session_remote.py` — skips on
machines without Docker; brings up `linuxserver/openssh-server` on
`127.0.0.1:22222`.

## Known limits (MVP)

- SSH auth is key-path only; no agent, inline keys, or passwords.
- No host-key verification (`known_hosts=None`).
- Fail-fast on SSH drop; no reconnect.
- Text deliverables only (non-UTF-8 files are skipped, not delivered).
- Grace-period sensitive: teardown can exceed 5 s; host apps running
  slow / remote sessions should call
  `optio_core.shutdown(grace_seconds=30)`.

Deferred items live in spec Section 11.
