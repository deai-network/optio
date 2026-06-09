# optio-claudecode — Agent Cheatsheet

Run Anthropic Claude Code as an optio task — local subprocess or remote
host via SSH — with the interactive TUI exposed in the dashboard via a
ttyd-served iframe.

Full design: `docs/2026-05-28-optio-claudecode-design.md`.

## Public API

```python
from optio_claudecode import ClaudeCodeTaskConfig, create_claudecode_task

create_claudecode_task(
    process_id="my-task",
    name="My task",
    config=ClaudeCodeTaskConfig(
        consumer_instructions="...",
        credentials_json=...,        # opaque dict/bytes/str → ~/.claude/.credentials.json
        claude_config=...,           # dict → ~/.claude/settings.json
        env={"ANTHROPIC_BASE_URL": "..."},
        permission_mode=None,        # default | plan | acceptEdits | bypassPermissions
        allowed_tools=None,
        disallowed_tools=None,
        ssh=None,
        install_if_missing=True,
        install_ttyd_if_missing=True,
        claude_install_dir=None,     # default ~/.local/bin (per host)
        ttyd_install_dir=None,
        before_execute=None,
        after_execute=None,
        on_deliverable=None,
    ),
)
```

`TaskInstance` returned has `ui_widget="iframe"` and `supports_resume`
tracking the config field (defaults to `True`). Resume snapshots the
`<workdir>/home/.claude/` subtree (encryptable session blob) plus a plaintext
workdir blob; on resume the workdir is restored and `--continue` is appended to
claude's argv. See `docs/2026-05-29-optio-claudecode-resume-design.md`.

## ClaudeCodeTaskConfig field semantics

* `credentials_json` — opaque payload; planted at
  `<workdir>/home/.claude/.credentials.json` with mode 0600. dict →
  JSON-encoded; bytes → UTF-8 decoded verbatim; str → written
  verbatim.
* `claude_config` — JSON-encoded to
  `<workdir>/home/.claude/settings.json`.
* `permission_mode` — forwarded verbatim to `claude
  --permission-mode`. Validation happens in `__post_init__`.
* HOME isolation: every task sees `HOME=<workdir>/home` so concurrent
  tasks on one host never share `~/.claude/` state.

## Conversation mode

Full design: `docs/2026-06-10-claudecode-conversation-gate-design.md`.

Three config fields control it (all defaults preserve today's behavior):

* `mode: Literal["iframe", "conversation"] = "iframe"` —
  `"conversation"` runs claude headlessly (no tmux, no ttyd, no iframe
  widget) over its bidirectional stream-json stdio protocol and
  publishes a live `Conversation` object via `ctx.publish_result()`.
* `host_protocol: bool = True` — opt-out for the optio.log keyword
  channel (STATUS / DELIVERABLE / DONE / ERROR / …). With `False` the
  keyword docs are also omitted from the composed CLAUDE.md.
  `mode="iframe"` **requires** `host_protocol=True` (it is the only
  completion signal there); validated in `__post_init__`.
* `permission_gate: bool = False` — only valid with
  `mode="conversation"`. Routes claude's `can_use_tool` control
  requests to the caller's `on_permission_request` handler instead of
  pre-deciding via `--permission-mode` / `--allowed-tools`. With
  `permission_gate=False` in conversation mode, `permission_mode` must
  be one of `{"acceptEdits", "bypassPermissions", "dontAsk"}` or
  `allowed_tools` must be non-empty (headless claude cannot show a
  permission dialog).

The `Conversation` surface (abstract Protocol in
`optio_agents.conversation`; concrete `ClaudeCodeConversation` here):

* `await send(text)` — queue one user message (claude queues stdin
  messages natively); raises `ConversationClosed` after session end.
* `on_event(handler) -> unsubscribe` — transparent passthrough of every
  stdout NDJSON object as a dict, unmodified. Live events only.
  Synthetic `{"type": "x-optio-unparseable", ...}` /
  `{"type": "x-optio-closed", ...}` events use the `x-optio-` prefix.
* `on_message(handler) -> unsubscribe` — simplified tier: one final
  answer text per completed turn (the `result` event's `result` field).
* `on_permission_request(handler) -> unsubscribe` — the permission
  gate; at most one handler, second registration replaces the first.
  Requests arriving before registration are queued (the turn blocks),
  so register promptly when `permission_gate=True`. With the gate off,
  a stray `can_use_tool` is answered with a defensive deny. Note:
  sandbox-safe commands (e.g. a bare `echo`) execute without consulting
  the gate at all — the handler only sees non-sandboxable calls.
* `is_pending()` — `True` while a sent message has no `result` yet.
* `await interrupt()` — abort the current turn; no-op when idle.
  Verified live: the CLI acks with a `control_response` and ends the
  turn with `result` subtype `error_during_execution` (`result: null`,
  so no `on_message` fires for the aborted turn). Messages queued
  behind the interrupted turn are processed normally afterwards.
* `await close()` — cooperative shutdown of the whole task; idempotent.
* `closed` (property) — `True` once the session has ended.

Caller-side usage (publish/await via optio-core):

```python
conv = await optio.launch_and_await_result("my-task", session_id=None)
conv.on_message(lambda text: print("claude:", text))
await conv.send("hello")
...
await conv.close()
```

Task completion semantics in conversation mode:

| Trigger | Task outcome |
|---|---|
| Caller `close()` | cooperative shutdown → **done** |
| optio cancel | existing route → **cancelled** (aggressive teardown) |
| Claude exits on its own before `close()` | **failed** ("claude exited unexpectedly (exit N)") |
| `host_protocol=True` alongside (legal combo) | optio.log DONE/ERROR terminate exactly as today; a caller `close()` emits a harness-side DONE so the task still resolves **done** |

## Hooks

`before_execute(hook_ctx)`, `after_execute(hook_ctx)`,
`on_deliverable(hook_ctx, relative_path, decoded_text)`. Identical
signatures and failure semantics to optio-opencode.

`before_execute` fires **after** AGENTS.md and HOME files are planted
and **before** ttyd launches.

`after_execute` fires after claude exits (or after cancellation), on
both success and ERROR paths.

## Log-file contract

Same as opencode. AGENTS.md tells claude to append to `./optio.log`:

- `STATUS: [N%] <msg>`
- `DELIVERABLE: <workdir-relative-or-absolute-path>` (must resolve
  under `<workdir>/deliverables/`)
- `DONE[: summary]`
- `ERROR[: message]`

DONE / ERROR terminate the session.

## Binary install

* claude — `curl -fsSL https://claude.ai/install.sh | bash`. Vendor
  script places binaries under `~/.local/share/claude/versions/<v>/`
  and a symlink at `~/.local/bin/claude`. The framework always exec's
  the absolute symlink path; no PATH mutation needed.
* ttyd — downloaded from `tsl0922/ttyd` GitHub Releases (pinned
  version). Linux x86_64/aarch64/armv7l only in v1.

Override install locations via `claude_install_dir` /
`ttyd_install_dir` (absolute paths).

## Testing

```
pytest packages/optio-claudecode/tests/
```

Needs MongoDB via Docker for the integration tests.

Fake binaries (`claude-shim.sh`, `ttyd-shim.sh`, `fake_claude.py`) live
in `tests/` and substitute the real ones during integration tests. The
ttyd shim prints a fake "Listening on http://127.0.0.1:N/" banner so
the framework's port discovery completes without opening a real socket.
The claude shim resolves its own symlink (via `readlink -f`) before
locating `fake_claude.py`, since the framework symlinks the shim into
a tmpdir.

Remote SSH automated tests are deferred to a follow-up plan. See the
design doc's "Open follow-ups" section.
