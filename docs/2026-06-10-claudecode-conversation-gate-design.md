# optio-claudecode Conversation Gate — Design (Phase I)

This spec was written against the following baseline:

**Base revision:** `34329320f364d886104644a975084ac099006d3c` on branch `main` (as of 2026-06-09T22:53:53Z)

## Summary

Turn optio-claudecode into a generic conversation gate towards Claude Code. Today a
task launches Claude Code with a predefined assignment, runs to completion, and offers
no programmatic conversation surface (the ttyd iframe is for humans; the optio.log
keyword channel is task-shaped, not conversational). This feature adds a per-task
**conversation mode** in which Claude Code runs headlessly (no tmux, no ttyd) over its
native bidirectional stream-json stdio protocol, and the launching code receives a live
`Conversation` object — send messages, subscribe to events and answers, check busy
state, gate tool permissions, interrupt, close.

Three packages change:

| Package | Change |
|---|---|
| optio-core | Generic `ctx.publish_result(obj)` + `launch_and_await_result()`; engine-side result registry |
| optio-agents | `Conversation` Protocol (type surface only); protocol driver gains a scaffolding-only mode (`host_protocol` off) |
| optio-claudecode | `mode`/`host_protocol` config fields; stream-json conversation driver; `ClaudeCodeConversation` implementation; prompt adjustments |

**Phase II (separate spec, postponed):** tunneling the conversation through clamator /
MongoDB → optio-api → an optio-ui React component. A feasibility check was done; see
Section 11.

## Verified foundations

These were verified empirically or by code inspection before this design was settled:

- One `claude -p --input-format stream-json --output-format stream-json` process holds a
  **persistent multi-turn session**: user messages are written as NDJSON lines to stdin;
  events (`system/init`, `assistant`, `result`, …) stream back as NDJSON on stdout; the
  process stays alive between turns and exits cleanly on stdin EOF. Same `session_id`
  across turns. Verified live on 2026-06-09 with OAuth subscription credentials — auth
  source is whatever `$HOME/.claude` holds, exactly what the existing HOME isolation and
  seed machinery plant.
- `Host.launch_subprocess(..., stdin=True)` already exposes a writable
  `ProcessHandle.stdin` on both `LocalHost` (asyncio PIPE) and `RemoteHost` (asyncssh
  `SSHWriter`). Conversation mode therefore works **local and remote from day one** with
  no optio-host change.
- optio-core today has no return channel from a task to its launcher:
  `TaskInstance.execute` is `Callable[..., Awaitable[None]]`, the executor discards the
  return value, `launch()` returns a snapshot, `launch_and_wait()` returns `None`.
- opencode has **no schema-compatible interface** (its programmatic story is HTTP + SSE
  with `{entity}.{action}` events; `session.idle` is the turn-complete analog). The
  conversation *semantics* (send / events / answers / busy / close) map cleanly onto
  both backends, so the abstract Protocol is shared while every concrete event payload
  is backend-specific.

## Decisions (settled during brainstorming)

1. **Per-task mode**: `mode: Literal["iframe", "conversation"] = "iframe"` on
   `ClaudeCodeTaskConfig`. `"iframe"` is byte-for-byte today's behavior. A "both" mode
   (iframe + conversation on one session) is **dropped for now**: the stream-json event
   stream is rich enough to build a full replacement UI later, and the only route to
   "both" (transcript tailing + tmux injection against a TUI session) is markedly
   messier. Recorded as a potential future addition.
2. **Strict backward compatibility** for every change in optio-core and
   optio-claudecode. All new config fields default to existing behavior; existing
   callers run unchanged.
3. **`host_protocol: bool = True`** on `ClaudeCodeTaskConfig`: opt-out for the optio.log
   keyword channel (STATUS / DELIVERABLE / DONE / ERROR / BROWSER / ATTENTION /
   DOMAIN_MESSAGE). Independent of `mode`, except: `mode="iframe"` **requires**
   `host_protocol=True` (in iframe mode the keyword channel is the only completion
   signal) — validated in `__post_init__`.
4. **Interface delivery**: generic optio-core mechanism, not a claudecode callback. The
   launching module (module A) is not necessarily the task-defining module (module B),
   so module B cannot plant a callback that reaches module A. The running task calls
   `ctx.publish_result(obj)`; the launcher awaits the object via
   `launch_and_await_result()`. ProcessContext stays generic — the published object is
   opaque to optio-core.
5. **Process lifecycle stays in optio**: monitoring / start / cancel via existing optio
   interfaces. The `Conversation` is a secondary interface scoped to talking with the
   model. `close()` is sugar: it sets a task-local `caller_wants_to_close` flag which
   joins `ctx.should_continue()` as a second cooperative-shutdown trigger; the normal
   claudecode teardown (snapshot capture, seed save-back, cleanup) runs unchanged.
6. **Transparent events, two tiers**: Claude Code's own stream-json events are channeled
   through as-is (no re-modelling); a simplified `on_message(text)` tier delivers one
   final answer text per turn.
7. **Protocol placement**: abstract `Conversation` Protocol in **optio-agents**
   (semantic surface shared with a future opencode implementation over HTTP + SSE);
   concrete `ClaudeCodeConversation` in **optio-claudecode**.
8. **Resume**: symmetric with today. Same `supports_resume` flag, same snapshot
   machinery (the Claude transcript lives under `home/.claude/projects/` and travels in
   the session blob), `--continue` appended on a transcript-bearing resume. Resume
   notice and auto-start kickoff are sent as stream-json stdin messages instead of
   positional argv (print mode with `--input-format stream-json` takes no positional
   prompt).

## 1. optio-core: `publish_result`

### ProcessContext

```python
def publish_result(self, obj: Any) -> None
```

- May be called **at most once** per process run; a second call raises `RuntimeError`.
- The object is opaque to optio-core. It is held **in memory only** (never persisted to
  Mongo) — this mechanism is for same-process (direct Python call) launches. The
  clamator RPC route needs a proxy layer and is Phase II.
- Two effects:
  1. Resolves the pending launcher-side future (if a `launch_and_await_result()` call
     is waiting).
  2. Registers the object in an engine-side **result registry**:
     `dict[process_id, object]`, exposed internally as
     `Optio.get_published_result(process_id) -> Any | None`. The registry entry is
     removed when the process reaches a terminal state. This registry is the Phase II
     attachment point (RPC/listener handlers look up the live object); in Phase I it
     also lets a same-process caller re-obtain the handle without having raced
     `launch_and_await_result`.

### Launch surface

```python
async def launch_and_await_result(
    self, process_id: str, resume: bool = False, *,
    session_id: str | None, timeout: float | None = None,
) -> Any
```

- Launches exactly like `launch()` (same `LaunchOutcome` failure modes → raises
  `LaunchError(reason)` on a not-ok outcome), then waits for the task to call
  `publish_result`.
- Returns the published object **while the task keeps running**.
- If the task reaches a terminal state (done / failed / cancelled) without publishing,
  raises `ResultNotPublished` (carrying the terminal state) instead of hanging.
- `timeout` (optional): `asyncio.TimeoutError` on expiry; the task keeps running.
- Existing `launch()` and `launch_and_wait()` are untouched.

### Executor wiring

The executor creates an `asyncio.Future` per launched process when (and only when) a
`launch_and_await_result` caller is waiting, plus the registry entry on publish. On
terminal transition: unresolved future → `ResultNotPublished`; registry entry dropped.

## 2. optio-agents

### 2.1 `Conversation` Protocol (`optio_agents/conversation.py`)

`typing.Protocol` (structural, matching the existing `HookContextProtocol` style).
Type surface only — no behavior, no claudecode imports.

```python
class Conversation(Protocol):
    async def send(self, text: str) -> None: ...
    def on_event(self, handler: Callable[[dict], Awaitable[None] | None]) -> Callable[[], None]: ...
    def on_message(self, handler: Callable[[str], Awaitable[None] | None]) -> Callable[[], None]: ...
    def on_permission_request(
        self, handler: Callable[[PermissionRequest], Awaitable[PermissionDecision]],
    ) -> Callable[[], None]: ...
    def is_pending(self) -> bool: ...
    async def interrupt(self) -> None: ...
    async def close(self) -> None: ...
    @property
    def closed(self) -> bool: ...
```

- `send(text)`: queue one user message. Always accepted while the session is live
  (Claude Code queues stdin messages natively); raises `ConversationClosed` after the
  session ends.
- `on_event(handler)`: subscribe to the **transparent passthrough** stream — every
  parsed stdout NDJSON object, as a dict, unmodified. Backend-specific by design.
  Returns an unsubscribe function. Live events only — no backlog replay (history is
  recoverable from the backend's own transcript, not our job). Handlers may be sync or
  async; a raising handler is logged, never fatal; handlers run on the engine loop via
  an internal queue + dispatcher task, so a slow handler cannot stall the stdout reader.
- `on_message(handler)`: simplified tier — called once per completed turn with the final
  answer text (claudecode: the `result` event's `result` field). Mid-turn assistant
  texts are visible via `on_event` only.
- `on_permission_request(handler)`: register the permission gate (see §3.5). At most one
  handler; registering a second replaces the first.
- `is_pending()`: `True` while at least one sent message has not yet received its
  `result` event.
- `interrupt()`: abort the current turn (see §3.6). No-op when idle.
- `close()`: request cooperative shutdown of the whole task (sets
  `caller_wants_to_close`); resolves when teardown has begun. Idempotent.
- `closed`: `True` once the session has ended (any cause).

`PermissionRequest` and `PermissionDecision` are small dataclasses in the same module:

```python
@dataclass(frozen=True)
class PermissionRequest:
    tool_name: str
    input: dict
    raw: dict          # full control_request payload, transparent

@dataclass(frozen=True)
class PermissionDecision:
    behavior: Literal["allow", "deny"]
    updated_input: dict | None = None   # allow-with-modified-input
    message: str | None = None          # deny reason, surfaced to the model
```

### 2.2 Protocol driver split: scaffolding-only mode

`run_log_protocol_session` today fuses two layers: (A) the optio.log keyword channel
(tail + dispatch, deliverable fetch + ack, DONE/ERROR termination, premature-exit
detection) and (B) generic session scaffolding (workdir wipe + `prepare()`, deliverables
dir, browser shims, `before/after_execute` hooks, cancel watcher, `agent_sender`
wiring).

Change: a new keyword-only parameter

```python
async def run_log_protocol_session(..., keywords: bool = True) -> None
```

With `keywords=False` the driver runs layer B only: no optio.log tail task, no
deliverable fetch loop, no DONE/ERROR semantics, no premature-exit-without-DONE rule
(the body's own return discipline governs). `optio.log` is still created (harmless,
keeps the workdir shape uniform) and browser shims are still installed per the
protocol's browser mode. Default `True` preserves today's behavior for every existing
caller.

The LLM-facing keyword documentation is correspondingly omitted from the composed
prompt by the consumer (see §3.7).

## 3. optio-claudecode

### 3.1 Config additions (`ClaudeCodeTaskConfig`)

```python
mode: Literal["iframe", "conversation"] = "iframe"
host_protocol: bool = True
permission_gate: bool = False
```

`__post_init__` validation (all new, additive):

- `mode="iframe"` and `host_protocol=False` → `ValueError` (iframe mode has no other
  completion signal).
- `mode="conversation"` and `permission_gate=False`: `permission_mode` must be one of
  `{"acceptEdits", "bypassPermissions", "dontAsk"}` or `allowed_tools` must be
  non-empty. Headless Claude cannot show a permission dialog; with no gate and an
  interactive permission mode the session would stall or abort mid-turn.
  (`"dontAsk"` is accepted here and added to `_VALID_PERMISSION_MODES`; verify the CLI
  flag spelling at implementation time.)
- `permission_gate=True` is only valid with `mode="conversation"`.

Existing fields apply unchanged in conversation mode: `env`, `scrub_env`, `focus_mode`
(no-op without a TUI — layered settings are harmless; document as ignored), `ssh`,
install flags, seeds (`seed_id`, `on_seed_saved`, cred watcher, lease), resume surface,
`session_blob_encrypt/decrypt`, `before_execute` / `after_execute` / `on_deliverable`
(the latter only meaningful with `host_protocol=True`).

### 3.2 Conversation-mode session flow

`run_claudecode_session` branches on `config.mode` at the launch step; everything
around it (host build, orphan rescue, `_prepare` install + resume restore, seed merge,
HOME planting, CLAUDE.md write, resume.log append, hooks, teardown brackets) is shared.

Conversation-mode body, replacing the tmux/ttyd launch:

1. Build argv:
   `claude -p --input-format stream-json --output-format stream-json --verbose`
   plus the existing flag builders (`--permission-mode`, `--allowed-tools`,
   `--disallowed-tools`, `--continue` on transcript-bearing resume) and, when
   `permission_gate=True`, the stdio permission-prompt plumbing (the flag the Agent SDK
   uses for its `can_use_tool` callback; exact spelling verified at implementation
   time).
2. Launch via `host.launch_subprocess(cmd, env=launch_env, cwd=host.workdir,
   env_remove=config.scrub_env, stdin=True)` — same env construction as today
   (HOME isolation, `config.env`, browser-shim env). No tmux, no ttyd, no tunnel, no
   widget: `ui_widget=None` on the TaskInstance, no input listener, no control
   upstream.
3. Construct `ClaudeCodeConversation` around the handle; call
   `ctx.publish_result(conversation)`.
4. Kickoff: `auto_start=True` and fresh → driver sends `AUTO_START_PROMPT` as the first
   stdin message. Resume with transcript → driver sends the resume notice
   (`System: you have been resumed`) as the first stdin message.
5. Body wait loop: `while ctx.should_continue() and not caller_wants_to_close and
   process is alive: sleep(1)` — mirroring today's tmux-liveness loop with the second
   trigger added.
6. Cred watcher / lease renewal run exactly as today when seeded.

The crash-orphan rescue bracket is a no-op for conversation-mode tasks (a pipe-bound
child does not survive engine death the way a detached tmux tree does); the rescue
check remains keyed to the tmux socket and simply finds nothing.

### 3.3 `ClaudeCodeConversation` (driver internals)

One reader task drains `handle.stdout` line by line, `json.loads`es each line, and:

- pushes every object into the event queue (dispatcher task fans out to `on_event`
  subscribers);
- on `result`: decrements the pending counter, fires `on_message` with the `result`
  field;
- on `control_request`: routes per §3.5 / §3.6;
- on unparseable line: logged, surfaced as a synthetic
  `{"type": "x-optio-unparseable", "line": ...}` event, never fatal.

`send()` serializes writes with a lock, increments the pending counter, writes one
NDJSON line + flush/drain.

Session end (any of: subprocess exit, task cancel, `close()`): the reader drains
remaining stdout, the conversation flips `closed`, pending `send/interrupt` calls raise
`ConversationClosed`, subscribers receive a final synthetic
`{"type": "x-optio-closed", "reason": ...}` event.

Synthetic events use the `x-optio-` prefix so they can never collide with Claude Code's
own types while staying inside the transparent dict stream.

### 3.4 Task completion semantics (conversation mode)

- Caller `close()` → cooperative shutdown → task ends **done**.
- optio cancel → existing route → **cancelled** (aggressive teardown).
- Claude process exits on its own **before** `close()` → task ends **failed**
  ("claude exited unexpectedly (exit N)"). No DONE-wrapper indirection — the engine
  owns the process handle and observes the exit directly.
- With `host_protocol=True` in conversation mode (legal combo), the optio.log keyword
  channel runs in parallel and DONE/ERROR terminate exactly as today.

Teardown (snapshot capture, seed capture/save-back, cleanup, disconnect) is the
existing `finally` bracket, with `teardown_session_tree` replaced in this mode by
`terminate_subprocess(handle)` + the existing `await_claude_gone` quiescence wait
before snapshot capture.

### 3.5 Permission gate (`permission_gate=True`)

Claude Code's control protocol interleaves on the same NDJSON channels:
stdout `{"type":"control_request","request_id":R,"request":{"subtype":"can_use_tool",
"tool_name":...,"input":{...},...}}` blocks the turn until stdin receives
`{"type":"control_response","response":{"subtype":"success","request_id":R,
"response":{"behavior":"allow"|"deny", ...}}}`.

Driver behavior:

- `can_use_tool` request → dispatch to the registered `on_permission_request` handler;
  its `PermissionDecision` is translated to the control response.
- **No handler registered yet** → the request is queued (the turn blocks); dispatched
  on registration. This closes the race between `publish_result` delivery, auto-start
  kickoff, and the caller's handler registration. Documented caller contract: register
  the handler promptly when `permission_gate=True`.
- Handler raises → respond deny with a harness-side message; log.
- `permission_gate=False` → no permission plumbing is requested from the CLI; a
  `control_request` arriving anyway is answered deny (defensive) and logged.

### 3.6 `interrupt()`

Driver writes `{"type":"control_request","request_id":N,"request":{"subtype":
"interrupt"}}` to stdin; the CLI aborts the current turn at the next safe point (Esc
equivalent), emits the turn's `result` and a `control_response` ack. `interrupt()`
resolves on the ack (request-id correlation); no-op when idle. Behavior toward
already-queued-but-unstarted messages (dropped vs preserved) is **verified at
implementation time** and documented then.

### 3.7 Prompt composition changes

- `host_protocol=False` → `compose_agents_md` omits the keyword-protocol documentation
  block entirely.
- `mode="conversation"` with falsy `consumer_instructions` → default instructions:
  `"Let's have a conversation with the user."`; the "## Task" framing block is dropped
  when instructions were defaulted.
- Resume section: content stays (resume.log procedure, REFRESHED tags, exclude
  clauses are protocol-independent). When the keyword docs are omitted, the final
  paragraph's reference to `System:` messages becomes self-contained — one added
  sentence explaining that messages prefixed `System:` originate from the harness, not
  the user.

## 4. Data flow (conversation mode, fresh start)

```
caller                          engine (optio-core + claudecode)            host
------                          --------------------------------            ----
launch_and_await_result(pid) →  launch → execute body
                                  connect, prepare (install, no resume)
                                  plant HOME, seed merge, CLAUDE.md
                                  launch_subprocess(claude -p …, stdin=True) → claude
                                  conversation = ClaudeCodeConversation(handle)
                                  ctx.publish_result(conversation)
        ← conversation
conversation.on_event(h)
conversation.send("hi")       →  stdin NDJSON line                        → claude turn
        ← events (on_event)   ←  stdout NDJSON lines                      ← assistant/…
        ← text (on_message)   ←  result event
conversation.close()          →  caller_wants_to_close → body loop exits
                                  terminate subprocess, snapshot, cleanup
```

## 5. Error handling summary

| Failure | Behavior |
|---|---|
| Task ends without `publish_result` | `launch_and_await_result` raises `ResultNotPublished` |
| `publish_result` called twice | `RuntimeError` in the task |
| Claude exits unexpectedly | Task fails; conversation closes with reason; `x-optio-closed` event |
| Unparseable stdout line | Logged + synthetic event; stream continues |
| Event handler raises | Logged; dispatch continues |
| Permission handler raises | Deny response with harness message; logged |
| `send()` after close | `ConversationClosed` |
| stdin write failure (pipe broken) | `send()` raises; conversation closes |
| Resume restore failure | Existing semantics unchanged (decrypt failure loud; else fresh-start fallback) |

## 6. Backward compatibility

- All new config fields default to current behavior (`mode="iframe"`,
  `host_protocol=True`, `permission_gate=False`).
- `run_log_protocol_session(keywords=True)` default keeps every existing consumer
  (opencode, claudecode iframe mode, demos) byte-identical.
- optio-core additions are purely additive (`publish_result`, registry,
  `launch_and_await_result`); existing entry points untouched.
- No wire/storage format changes; snapshots/seeds unchanged.

## 7. Testing

- **optio-core**: unit tests for `publish_result` / `launch_and_await_result` matrix —
  publish-then-await, await-then-publish, terminal-without-publish, double publish,
  timeout, registry lifecycle. Plain fake tasks, MongoDB via Docker (existing harness).
- **optio-claudecode**: extend `fake_claude.py` / `claude-shim.sh` with a stream-json
  mode: reads NDJSON stdin, emits scripted `system/init` / `assistant` / `result` /
  `control_request` lines. Tests: multi-turn send/receive, on_message extraction,
  pending bracket, queued sends, interrupt handshake, permission gate (allow / deny /
  late registration), close() teardown trigger, unexpected-exit → failed, auto_start
  kickoff message, resume (`--continue` + resume-notice stdin message, no kickoff),
  `host_protocol=False` prompt content, config validation matrix.
- **Remote**: reuse the dockerized sshd harness (`test_session_remote.py` pattern) for
  one end-to-end conversation over `RemoteHost` (stdin over asyncssh is the only
  remote-specific risk).
- **Live verification** (manual or `optio-demo`): one real conversation-mode session
  against the actual CLI, since the stream-json schema and the permission-flag spelling
  are vendor-controlled.

## 8. Implementation-time verifications (flagged, not blocking design)

1. Exact CLI flag for stdio permission prompting (`--permission-prompt-tool` stdio
   variant the Agent SDK uses) and the precise `control_response` schema.
2. `interrupt` effect on queued-but-unstarted messages.
3. Native stdin queueing during a running turn (verified send-after-result only).
4. `"dontAsk"` permission-mode spelling.

## 9. Out of scope / deferred

- **Phase II**: clamator/Mongo tunneling, optio-api SSE + send endpoints, optio-ui
  `ClaudeCodeConversation` React component, engine-side proxy object. Feasibility
  checked (see §11); detailed design postponed.
- "Both" mode (iframe + conversation on one session) — potential future addition.
- Conversation-rendering dashboard widget (depends on Phase II).
- opencode `Conversation` implementation (HTTP + SSE translation layer).
- Caller-side automatic keyword processing (re-adding DONE/DELIVERABLE-like semantics
  on top of the conversation stream for fire-and-forget usage with
  `host_protocol=False`).

## 10. Module/file map

| File | Change |
|---|---|
| `optio-core/src/optio_core/context.py` | `publish_result` |
| `optio-core/src/optio_core/lifecycle.py` | `launch_and_await_result`, registry accessor |
| `optio-core/src/optio_core/executor.py` | result future + registry wiring, terminal cleanup |
| `optio-agents/src/optio_agents/conversation.py` | new: `Conversation` Protocol, `PermissionRequest/Decision`, `ConversationClosed` |
| `optio-agents/src/optio_agents/protocol/session.py` | `keywords: bool = True` scaffolding-only mode |
| `optio-claudecode/src/optio_claudecode/types.py` | `mode`, `host_protocol`, `permission_gate` + validation |
| `optio-claudecode/src/optio_claudecode/conversation.py` | new: `ClaudeCodeConversation` + stream-json driver |
| `optio-claudecode/src/optio_claudecode/session.py` | mode branch, publish, close-flag loop, teardown variant |
| `optio-claudecode/src/optio_claudecode/host_actions.py` | conversation argv builder |
| `optio-claudecode/src/optio_claudecode/prompt.py` | protocol-doc omission, default instructions, self-contained resume paragraph |

## 11. Phase II feasibility (checked, postponed)

Target data flow: optio-ui React component ⇄ optio-api (REST + SSE) ⇄ clamator RPC /
shared MongoDB ⇄ engine-side proxy holding the live `Conversation`.

- Send direction: clamator RPC suffices for `send` / `interrupt` / permission
  responses; alternatively the existing control-upstream proxy pattern (exactly how
  iframe-input messages flow today: UI → optio-api widget-control proxy → per-task
  aiohttp listener in the engine).
- Receive direction: either an engine-side per-task SSE/WS listener proxied through
  fastify-widget-proxy (WS proxying already exists), or Mongo change streams (engine
  appends events to a collection; optio-api tails the change stream and serves SSE) —
  the latter tolerates engine/api restarts and fits the shared-Mongo architecture.
- Attachment point: the Phase I result registry (`publish_result` side effect) gives
  RPC/listener handlers direct access to the live `Conversation` by process_id.

Verdict: feasible with existing plumbing patterns; no architectural blocker.
