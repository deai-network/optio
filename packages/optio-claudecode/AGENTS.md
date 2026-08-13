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
        claude_config=...,           # dict, deep-merged into ~/.claude/settings.json
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
* `claude_config` — deep-merged into
  `<workdir>/home/.claude/settings.json` via a post-seed
  read-modify-write (`host_actions.apply_claude_settings`): applied
  AFTER the seed (fresh) or restored snapshot (resume) on all paths, so
  the caller's keys win per key while every other key the seed/snapshot
  carried is preserved.
* `permission_mode` — forwarded verbatim to `claude
  --permission-mode`. Validation happens in `__post_init__`.
* `session_blob_encrypt/decrypt`, `seed_blob_encrypt/decrypt` —
  inherited from `optio_agents.config_types.BlobCryptoConfigMixin`
  (alongside the `ClaustrumConfigMixin` triad). Optional synchronous
  bytes→bytes transforms at GridFS write/read: the `session_blob` pair
  wraps this process's home/.claude session tar (ds-scoped key), the
  `seed_blob` pair wraps the shared pool-account SEED tar (pool-scoped
  key). Per pair both-or-neither (validated by `_validate_blob_crypto`
  in `__post_init__`); the seed pair falls back to the session pair
  when unset — seed ops read the transforms only through the mixin's
  `seed_encrypt` / `seed_decrypt` accessors.
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

### Conversation UI (`conversation_ui`)

Full design: `docs/2026-06-10-claudecode-conversation-ui-design.md`.
Browser widget: the engine-neutral `optio-conversation-ui` package
(`packages/optio-conversation-ui`) — register it in the host app via
`registerConversationWidget()` (serves both claudecode and opencode;
each task self-declares its engine via `widgetData.protocol`).

* `conversation_ui: bool = False` — strictly opt-in; requires
  `mode="conversation"` (validated in `__post_init__`). The published
  `Conversation` object stays the default gate; this is a deliberate
  parallel path for dashboard monitoring/control.

When `True`:

* Two extra argv flags are appended to the conversation argv:
  `--include-partial-messages` (live partial text on the stream) and
  `--replay-user-messages` (user turns echoed back on stdout — without
  it the stream carries only the assistant side). Gated on the flag so
  in-process-only consumers don't pay for events nobody reads.
* `create_claudecode_task` sets `ui_widget="claudecode-conversation"`
  (instead of `None` in plain conversation mode).
* After `ctx.publish_result(conversation)` the task body starts a
  per-task `ConversationListener` (aiohttp, sibling of
  `input_listener.py`, OS-assigned port, `OPTIO_WIDGET_TUNNEL_BIND`
  interface logic), registers it via
  `ctx.set_widget_upstream(url, inner_auth)` with a per-task random
  basic-auth credential, and calls `ctx.set_widget_data({})`. The
  listener is shut down in the session teardown bracket.

Endpoints (reached through the optio-api widget proxy, which injects
the inner basic-auth credential; GET = viewer role, POST = operator):

| Endpoint | Behavior |
|---|---|
| `GET /events` | SSE. On connect: replay buffer contents, then live tail. Each event's SSE `id:` is its monotonic `seq`; `Last-Event-ID` honored, so reconnects resume without duplicates. |
| `POST /send` | `{text}` → `conversation.send(text)`. 409 when closed. |
| `POST /interrupt` | `{}` → `conversation.interrupt()`. No-op when idle. |
| `POST /permission` | `{request_id, behavior: "allow"\|"deny", updated_input?, message?}` → resolves the pending permission future. 404 for unknown/already-answered request_id. |

Replay-buffer semantics:

* `collections.deque(maxlen=1000)` of raw events, stamped with a
  monotonic `seq`. Session-persistent only — nothing goes to Mongo;
  after the task ends the conversation view is gone.
* Mechanical type filter, not interpretation: events of type
  `stream_event` (the partial-message deltas) are forwarded live but
  never buffered. Everything else — `system`, `user`, `assistant`,
  `result`, `control_request`, `x-optio-*` — is buffered.
* The engine channels raw stream-json events through untouched; all
  interpretation happens client-side in `optio-conversation-ui`. The one
  synthetic listener event is
  `{"type": "x-optio-permission-answered", "request_id": ..., "behavior": ...}`,
  broadcast (and buffered) when a permission is answered so every
  viewer sees the card resolve.

**Handler-slot rule**: `conversation_ui=True` occupies the single
`on_permission_request` slot (the listener registers the handler and
resolves it from `POST /permission`). Consumers that want programmatic
permission gating must not enable `conversation_ui` — or must accept
that the UI is the gate.

When `False` (default): behavior is byte-identical to plain
conversation mode (`ui_widget=None`, no listener, no extra argv flags).

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

* claude — provisioned from a shared, optio-owned version cache
  (`${OPTIO_CLAUDECODE_CACHE_DIR:-${XDG_CACHE_HOME:-$HOME/.cache}/optio-claudecode/versions}`);
  the per-task home gets `home/.local/share/claude/versions` symlinked at the
  cache and `home/.local/bin/claude` pointed at the newest cached version.
  Cache miss → vendor `curl -fsSL https://claude.ai/install.sh | bash`
  (unconfined, `HOME=<workdir>/home`) writes through the symlink into the
  cache. Cache hit → launch-time freshness check against the upstream
  `latest` endpoint install.sh consults
  (`https://downloads.claude.ai/claude-code-releases/latest`); upstream newer
  → same install.sh path refreshes the cache; probe failure keeps the cached
  binary (best-effort). Sessions run with `DISABLE_AUTOUPDATER=1` — under
  claustrum the cache is `--rox`, so in-session self-update can only EACCES;
  freshness is owned by this unconfined provisioning path. See the addendum
  in `docs/2026-05-31-optio-claudecode-runtime-cache-design.md`.
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
