# Splitting DOMAIN_MESSAGE into CLIENT_MESSAGE and CALLER_MESSAGE

This spec was written against the following baseline:

**Base revision:** `ffe57cdd0e7942d94a7a0cdeb23065b50e385b9f` on branch `main` (as of 2026-06-10T21:43:16Z)

## Summary

The `DOMAIN_MESSAGE:` keyword in the optio.log protocol is removed and replaced
by two purpose-specific keywords:

- `CLIENT_MESSAGE: <keyword> <json>` — routed to the originating browser
  session's frontend (exactly today's `DOMAIN_MESSAGE` behavior).
- `CALLER_MESSAGE: <keyword> <json>` — routed to the embedding application
  (server side) via a new `on_caller_message` callback. Never stored in the
  database; never reaches the browser.

Both keywords are **off by default**. `CLIENT_MESSAGE` is enabled by a
`use_client_messages` flag; `CALLER_MESSAGE` is enabled by passing an
`on_caller_message` callback. When a keyword is disabled it is excluded from
both the parser (the line falls through to `UnknownLine`) and the agent-facing
keyword documentation — the LLM never learns about keywords it cannot use.

This is a **full rename with no deprecation aliases** (hard break, pre-1.0):
the downstream pipeline renames `domain_message` → `client_message`, stored
event type `"domain"` → `"client"`, and UI callback `onDomainMessage` →
`onClientMessage`.

## Background

Agents write keyword lines to `<workdir>/optio.log`; the optio-agents session
driver tails and parses them. `DOMAIN_MESSAGE: <keyword> <single-line-json>`
is today's application-extensibility keyword: it is unconditionally parsed,
unconditionally documented to the agent, and has exactly one routing path —
`ProcessContext.domain_message()` appends a `{"type": "domain"}` session event
that the optio-api SSE poller forwards to the originating browser session.

Two problems:

1. There is no server-side path. An embedding application (e.g. the
   conversation-scripter wrapper, Excavator) that wants to react to agent
   messages in Python must watch the database itself; the framework offers no
   hook.
2. The keyword is always on. Agents are taught a keyword that, in deployments
   with no frontend handler registered, goes nowhere (the SSE stream is never
   even opened without an `onDomainMessage` callback).

## Design

### 1. Protocol layer (optio-agents)

**Keywords.** `DOMAIN_MESSAGE:` is removed. Two successors share its line
grammar (`KEYWORD: <token> <single-line-json>`, mandatory trailing newline):

| Keyword | Routing |
|---|---|
| `CLIENT_MESSAGE: <keyword> <json>` | originating browser session frontend |
| `CALLER_MESSAGE: <keyword> <json>` | embedding application callback |

**Events** (`protocol/parser.py`). `DomainMessageEvent` is replaced by:

```python
@dataclass(frozen=True)
class ClientMessageEvent:
    keyword: str
    data: object

@dataclass(frozen=True)
class CallerMessageEvent:
    keyword: str
    data: object
```

Both join the `LogEvent` union. Malformed JSON drops the line to
`UnknownLine`, as today. A `DOMAIN_MESSAGE:` line now parses as `UnknownLine`.

**ProtocolFeatures** (`protocol/protocol.py`). New frozen value object —
the single vocabulary for protocol variation:

```python
@dataclass(frozen=True)
class ProtocolFeatures:
    browser: BrowserMode = "ignore"
    client_messages: bool = False
    caller_messages: bool = False
```

**Factory.** `get_protocol(*, browser="ignore", client_messages=False,
caller_messages=False) -> Protocol` builds a `ProtocolFeatures` and stores it
on `Protocol` as `features` (replacing the bare `browser` field; a
`Protocol.browser` property remains for existing readers). Documentation and
parser are both derived from the features — `get_protocol` stays the single
decision point binding docs + parser + shims.

**Parser.** `parse_log_line(line, *, features: ProtocolFeatures =
ProtocolFeatures())` replaces the `recognize_browser` kwarg. A disabled
keyword's line falls through to `UnknownLine` (same precedent as `BROWSER:`
outside redirect mode) and is therefore forwarded verbatim as progress text.

**Prompt** (`protocol/prompt.py`). `build_log_channel_prompt(features)`
includes the `CLIENT_MESSAGE` bullet only when `features.client_messages` and
the `CALLER_MESSAGE` bullet only when `features.caller_messages`. Bullet
wording (final prose at implementation): client = "push a message to the user
interface of the application that launched you"; caller = "send a message to
the controlling application; you may receive a reply".

### 2. Session driver (optio-agents `protocol/session.py`)

**New callback type and parameter:**

```python
CallerMessageCallback = Callable[["HookContext", str, object], Awaitable["str | None"]]
"""Arguments: (hook_ctx, keyword, data). A non-None return value is pushed
back into the live agent session as feedback."""

async def run_log_protocol_session(
    ...,
    on_caller_message: CallerMessageCallback | None = None,
    ...
) -> None:
```

**Dispatch.**

- `ClientMessageEvent` → `await ctx.client_message(ev.keyword, ev.data)`
  inline in the tail loop (a fast DB append, as today).
- `CallerMessageEvent` → pushed onto a new bounded queue (bound 64, mirroring
  the deliverable-fetch pattern). A worker coroutine drains it:
  `feedback = await on_caller_message(hook_ctx, keyword, data)`; a non-None
  result is sent to the agent via `agent_sender` (warning-and-skip if no
  sender is configured, matching the deliverable feedback path). A callback
  exception is logged and the session continues — one bad handler call must
  not kill the task. Ordering among caller messages is preserved; ordering
  relative to other events is not guaranteed.

**Consistency guards** (fail fast at session start, `ValueError`):

- `protocol.features.caller_messages` is true but `on_caller_message is None`.
- `on_caller_message` passed but `features.caller_messages` is false.

**Drain semantics.** On `DONE`/`ERROR` the caller-message queue is drained
before the session closes (handlers run for already-emitted messages);
feedback produced after termination is skipped — the agent is gone.

### 3. Downstream rename (full break, no aliases)

- **optio-core.** `ProcessContext.domain_message()` →
  `ProcessContext.client_message()`. Stored session-event record
  `{"type": "domain", ...}` → `{"type": "client", ...}`. `CALLER_MESSAGE`
  never touches optio-core — no DB write, handled entirely in the
  optio-agents driver.
- **optio-api.** Poller and `/api/session-events/stream` are type-agnostic;
  only `"domain"` literals in types/wire-model docs are renamed. Behavior
  unchanged.
- **optio-ui.** `sessionEvents.ts`: `onDomainMessage` → `onClientMessage`;
  event-type filter `"domain"` → `"client"`. Stream-activation gating logic
  unchanged.
- **optio-dashboard.** `App.tsx` handler renamed; notification label
  "Domain message:" → "Client message:".
- **Old data.** Pre-existing `type: "domain"` records are ignored by the new
  filter and not migrated — session events are ephemeral per process.
- **Release order** (content-changed deps first): optio-core → optio-agents →
  optio-api / optio-ui → optio-claudecode / optio-opencode / optio-dashboard.
  External consumers (conversation-scripter, Excavator) pick the change up at
  their next dependency bump — coordinated breaking change.

### 4. Consumer wiring (optio-claudecode, optio-opencode)

Both `ClaudeCodeTaskConfig` and `OpencodeTaskConfig` grow:

```python
use_client_messages: bool = False
on_caller_message: CallerMessageCallback | None = None
```

Both session modules derive protocol features from config:

```python
protocol = get_protocol(
    browser="redirect",  # opencode: "suppress"
    client_messages=config.use_client_messages,
    caller_messages=config.on_caller_message is not None,
)
```

and pass `on_caller_message=config.on_caller_message` to
`run_log_protocol_session`. The driver's consistency guard cannot trip on
this path (flags derived from the same config values); it protects direct
optio-agents users.

Keyword docs reach the agent via `protocol.documentation` as today, so the
conditional bullets require no consumer prompt changes. Defaults are off:
existing claudecode/opencode users lose the keyword from agent docs and
parser until they opt in — intended.

### 5. Testing

- **optio-agents** — `test_protocol_parser.py`: both keywords parse when
  enabled, fall to `UnknownLine` when disabled, malformed JSON →
  `UnknownLine`, `DOMAIN_MESSAGE:` → `UnknownLine` (removal regression pin).
  `test_prompt.py` / `test_protocol.py`: bullet present iff feature enabled,
  across the browser-mode matrix; docs/parser consistency per
  `ProtocolFeatures` combination. `test_client_directed_dispatch.py`:
  client-message routing to `ctx.client_message`; caller-message queue →
  callback → feedback via `agent_sender`; callback exception survival; queue
  drain on DONE; both `ValueError` guards.
- **optio-core** — `test_client_directed_events.py` renames: method
  `client_message`, stored type `"client"`.
- **optio-ui / optio-dashboard** — sessionEvents tests updated for
  `onClientMessage` and the `"client"` filter.
- **optio-opencode in-repo e2e** — one live test driving a `CALLER_MESSAGE`
  round-trip with feedback; one `CLIENT_MESSAGE` storage check.

### 6. Documentation

- `protocol/prompt.py` prose stays the single source of truth for the
  agent-facing keyword spec.
- `docs/2026-04-22-optio-opencode-design.md` §6 and
  `docs/2026-05-29-optio-protocol-variation-design.md` receive
  superseded-by notes pointing at this spec for the affected keyword.
- optio-claudecode and optio-opencode READMEs document the two new config
  fields.

## Decisions log

| Decision | Choice |
|---|---|
| Rename blast radius | Full rename across the stack, no deprecation aliases |
| Callback semantics | Feedback-capable: `str \| None` return, non-None pushed to agent |
| Flag placement | `get_protocol` kwargs; callback at session level; runtime consistency guards |
| Dispatch | Bounded queue + worker (deliverable pattern), not inline, not per-task |
| Variation mechanism | `ProtocolFeatures` frozen value object (no keyword registry — YAGNI) |
| Defaults | Both features off |
