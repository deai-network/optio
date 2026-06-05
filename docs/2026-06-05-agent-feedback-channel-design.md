# Agent Feedback Channel (engine → running agent)

This spec was written against the following baseline:

**Base revision:** `3792f93bbf6d2fe9f5961b61f001f3214f68331d` on branch `main` (as of 2026-06-05T00:21:58Z)

## Context

Today an agent task's hooks (`before_execute`, `after_execute`, `on_deliverable`) can read host files and report progress, but **cannot send a message back to the running agent**. The `on_deliverable` callback's return value is dropped (`optio-agents/.../protocol/session.py` deliverable loop), and `HookContext` carries no handle to the live session transport.

A downstream consumer (the excavator engine) wants this: when `on_deliverable` rejects a delivered artifact (e.g. a recipe with a missing/invalid version header), it should tell the agent *why*, mid-session, so the agent can re-emit a corrected artifact instead of the rejection being silent.

Both backends already have a viable transport:
- **opencode** runs an HTTP worker; `_post_opencode_prompt(port, password, session_id, msg)` (`optio-opencode/.../session.py`) injects a prompt (`POST /session/<id>/prompt_async`, fire-and-forget). It's what auto-start uses.
- **claudecode** runs `claude` inside a detached **tmux** session under ttyd; text can be injected by "fake typing" via `tmux paste-buffer` + `send-keys Enter`.

This spec adds a single, generic feedback method at the **agent layer** (`optio-agents`) plus the two backend transports. `optio-core` / `ProcessContext` is **not** touched — agent messaging is an agent-layer concern.

## Summary

Add `await hook_ctx.send_to_agent(message) -> bool` to `HookContext`. It is backed by an **optional** sender closure that each agent backend injects into `run_log_protocol_session`, which attaches it to the `hook_ctx` it builds for the session. opencode wires a sender over its HTTP prompt API; claudecode wires one over `tmux`. Delivery is best-effort: a missing channel or a dead/unreachable agent returns `False` (logged), never raising.

## Scope and decisions

- **Layer:** entirely in `optio-agents` (the `HookContext` / `run_log_protocol_session` surface) + the two backends. `optio-core`/`ProcessContext` untouched.
- **API shape:** a single imperative method `send_to_agent(message) -> bool`. The `DeliverableCallback` return type stays `Awaitable[None]` — feedback is via the method, callable from any hook, not via a return value.
- **Sender is optional:** a backend that does not implement the channel injects nothing; `send_to_agent` then returns `False`. opencode + claudecode both implement it here; the interface tolerates future backends that don't.
- **Best-effort:** the only runtime failure is a gone/unreachable agent (crashed worker, ended tmux session). `send_to_agent` swallows the error, logs via `report_progress`, and returns `False`. A dead agent never crashes a hook or session teardown.
- **Fire-and-forget:** no delivery confirmation, mirroring `prompt_async` and tmux injection. The message queues / types into the agent's input; the agent consumes it when next at its prompt.

## Architecture

### Part A — `optio-agents`

**`HookContext` (`packages/optio-agents/src/optio_agents/context.py`):**
- In `__init__`, alongside `browser_launch_env`: `object.__setattr__(self, "_agent_sender", None)`.
- New method:
  ```python
  async def send_to_agent(self, message: str) -> bool:
      """Best-effort: push a message into the live agent session. Returns
      True if delivered, False if no channel is wired or the send failed.
      A dead/unreachable agent must never crash a hook."""
      sender = self._agent_sender
      if sender is None:
          self._ctx.report_progress(None, "send_to_agent: no channel for this agent")
          return False
      try:
          await sender(message)
          return True
      except Exception as e:  # noqa: BLE001
          self._ctx.report_progress(None, f"send_to_agent failed: {e!r}")
          return False
  ```
- Add the matching method signature to `HookContextProtocol` (IDE discoverability).

**`run_log_protocol_session` (`packages/optio-agents/src/optio_agents/protocol/session.py`):**
- New type alias near the top: `AgentSender = Callable[[str], Awaitable[None]]` (the sender raises on transport failure; `send_to_agent` catches).
- New parameter `agent_sender: AgentSender | None = None`.
- Right after it builds `hook_ctx = HookContext(ctx, host)` (where it also sets `browser_launch_env`), wire it: `hook_ctx._agent_sender = agent_sender`.
- Export `AgentSender` from the package (`__init__.py` / `protocol/__init__.py`) alongside `DeliverableCallback`.

No change to the deliverable loop's invocation or to `DeliverableCallback`'s signature.

### Part B — `optio-opencode`

In `run_opencode_session` (`packages/optio-opencode/src/optio_opencode/session.py`), at the `run_log_protocol_session(...)` call (the body has `worker_port`, `session_id`, `password` already established by this point), build and pass a sender:

```python
async def _agent_sender(message: str) -> None:
    # worker_port / session_id / password are set earlier in the body.
    await _post_opencode_prompt(worker_port, password, session_id, message)

await run_log_protocol_session(
    ...,
    on_deliverable=config.on_deliverable,
    agent_sender=_agent_sender,
)
```

`_post_opencode_prompt` already raises on a non-2xx / unreachable worker, which `send_to_agent` converts to `False`.

### Part C — `optio-claudecode`

Add a host helper in `packages/optio-claudecode/src/optio_claudecode/host_actions.py` that injects text into the claude TUI via tmux. Use **`set-buffer` + `paste-buffer`** (robust for arbitrary text, including spaces/newlines, which `send-keys -l` would mistreat — an embedded newline is a submit), then a single `Enter` to submit:

```python
async def send_text_to_claude(
    host: "Host", tmux_path: str, tmux_socket: str, tmux_session: str, text: str,
) -> None:
    """Fake-type a message into the claude TUI and submit it. Raises on a
    tmux failure (caller treats that as 'agent unreachable')."""
    s = shlex.quote(tmux_socket)
    sess = shlex.quote(tmux_session)
    buf = "optio-feedback"
    cmd = (
        f"{tmux_path} -S {s} set-buffer -b {buf} -- {shlex.quote(text)} && "
        f"{tmux_path} -S {s} paste-buffer -d -b {buf} -t {sess} && "
        f"{tmux_path} -S {s} send-keys -t {sess} Enter"
    )
    await host.run_command(cmd)  # non-zero exit -> caller's send_to_agent returns False
```
(Match the exact `tmux -S <socket>` invocation form used by the launch path in this module — `launch_ttyd_with_claude` / `_require_tmux` establish `tmux_path`, `tmux_socket`, `tmux_session`.)

**Verified manually (2026-06-05):** the `set-buffer` → `paste-buffer` → `send-keys Enter` sequence injected a message into a live `claude` TUI (running detached under tmux), which landed in the input box and was submitted as a new turn — claude processed it and replied. The fake-typing transport works; this is no longer an assumption.

In `run_claudecode_session` (`session.py`), at the `run_log_protocol_session(...)` call (the body has `tmux_path`, `tmux_socket`, `tmux_session` set by `launch_ttyd_with_claude`), pass a sender:

```python
async def _agent_sender(message: str) -> None:
    await host_actions.send_text_to_claude(
        host, tmux_path, tmux_socket, tmux_session, message,
    )

await run_log_protocol_session(
    ...,
    agent_sender=_agent_sender,
)
```

## Data flow

```
hook (e.g. on_deliverable rejects an artifact)
  └─ await hook_ctx.send_to_agent("Recipe rejected: missing recipe-dsl-version header")
       └─ _agent_sender(message)        [injected by the backend]
            ├─ opencode: _post_opencode_prompt(port, pw, session_id, msg)  → POST /session/<id>/prompt_async
            └─ claude:  tmux set-buffer → paste-buffer → send-keys Enter   (fake typing)
       └─ success → True ; no channel / transport error → log + False
```

## Error handling

- No sender injected → `report_progress("send_to_agent: no channel …")`, return `False`.
- Sender raises (worker down, tmux session gone, non-zero exit) → caught in `send_to_agent`, logged, return `False`.
- `send_to_agent` itself never raises.

## Testing

**`optio-agents`:**
- `send_to_agent` with no sender → returns `False`, emits a `report_progress` line (stub ctx records calls).
- `send_to_agent` with a sender that succeeds → returns `True`, sender awaited once with the message.
- `send_to_agent` with a sender that raises → returns `False`, logged, no exception propagates.
- `run_log_protocol_session(agent_sender=fn)` attaches `fn` to the loop's `hook_ctx` (assert the hook sees it; reuse the existing protocol-session test harness).

**`optio-opencode`:**
- The wired `_agent_sender` calls `_post_opencode_prompt` with `(worker_port, password, session_id, message)` (monkeypatch `_post_opencode_prompt`, drive the sender, assert args).

**`optio-claudecode`:**
- `send_text_to_claude` issues the `set-buffer` → `paste-buffer` → `send-keys Enter` sequence with the right socket/session and a shell-quoted message (monkeypatch `host.run_command`, assert the command string).
- The wired `_agent_sender` delegates to `send_text_to_claude` with the session's tmux handles.

## Out of scope

- No delivery confirmation / read receipts (fire-and-forget).
- No return-value routing for `DeliverableCallback` (the method is the only path).
- No new backend transports beyond opencode + claudecode.
- The consumer behavior (excavator rejecting a recipe and calling `send_to_agent`) lives in the excavator repo, not here.
</content>

## Addendum: return-value routing (supersedes the imperative-only decision)

After review, the deliverable callback **also** gets return-value routing as ergonomic sugar on top of the imperative method. The `send_to_agent` method (Part A) stays exactly as specified; this adds an automatic path for the common "reject a deliverable and tell the agent why" case.

**Divergences from the body above (explicit):**
- Part A's "No change to ... `DeliverableCallback`'s signature" and "the return type stays `Awaitable[None]`" are **superseded** by this addendum.
- "Out of scope: No return-value routing for `DeliverableCallback`" is **superseded** by this addendum.

**Change — `optio-agents` (`protocol/session.py`):**
- `DeliverableCallback` becomes `Callable[["HookContext", str, str], Awaitable["str | None"]]`.
- In the deliverable loop, route a non-empty returned string through the same channel:
  ```python
  feedback = await callback(hook_ctx, display, text)
  if isinstance(feedback, str) and feedback.strip():
      await hook_ctx.send_to_agent(feedback)
  ```
  (Replaces the current `await callback(hook_ctx, display, text)` that drops the return. The existing `try/except` around the callback is unchanged — a callback that raises is still logged, not routed.)

Both paths now exist and compose: a hook may call `hook_ctx.send_to_agent(...)` directly at any time, **and/or** return a string from `on_deliverable` to have the loop send it. A `None`/empty return sends nothing.

**Additional test (`optio-agents`):**
- `on_deliverable` returning `"reason"` → the deliverable loop calls `send_to_agent("reason")` once (spy the sender / `hook_ctx.send_to_agent`); returning `None` or `""` → no send.

## Addendum 2: `System:` prefix on every engine→agent message (2026-06-05)

Every message that reaches a running agent through this channel originates from the harness / a hook — never from a real user. The agent must be able to tell harness-originated input apart from genuine user turns (they arrive on the same input channel). So all outbound traffic through the channel is prefixed.

**Change — `optio-agents`:**
- New constant `SYSTEM_MESSAGE_PREFIX = "System: "` in `context.py` (exported from the package).
- `send_to_agent` prepends it once, at the single chokepoint, before handing to the backend sender:
  ```python
  await sender(f"{SYSTEM_MESSAGE_PREFIX}{message}")
  ```
  The chokepoint covers both paths that flow through `send_to_agent`: the imperative `send_to_agent(...)` call and the `on_deliverable` return-value sugar (Addendum 1, which calls `send_to_agent`). The resume notification (Addendum 4) is emitted at the backend launch site, where no `hook_ctx`/`send_to_agent` is available, so it applies the **same** `SYSTEM_MESSAGE_PREFIX` constant explicitly in its message string. Either way the one constant is the single source of the prefix; backends stay dumb transports and never add it themselves.

**Additional test (`optio-agents`):**
- `send_to_agent("hi")` with a spy sender → sender awaited once with exactly `"System: hi"`.

## Addendum 3: protocol documentation — the inbound channel (2026-06-05)

The agent-facing protocol prose is currently output-only (it teaches `STATUS:`/`DELIVERABLE:`/`DONE`/`ERROR`, all agent→harness). It says nothing about the agent *receiving* messages. Now that a real harness→agent channel exists, the agent must be told about it, or a `System:` message will read as a confusing user turn.

**Change — `optio-agents` (`protocol/prompt.py`):**
- Add a new block, always included by `build_log_channel_prompt(...)` (independent of `browser` mode), with agent-facing prose along these lines:
  > After you emit a deliverable, the harness may send you a message on the same input channel where the user normally talks — that channel carries both user input and harness messages. Harness messages are prefixed `System:`. Treat a `System:` message as an instruction. In particular, if it tells you a delivered artifact was rejected, revise the artifact and emit the deliverable again.
- The block is documented as "always present" because it describes a *possibility* (`may send`); a backend with no sender wired simply never sends, which is harmless.

**Additional test (`optio-agents`):**
- `build_log_channel_prompt(...)` output contains the `System:` inbound-channel block for every `browser` mode.

## Addendum 4: resume notification over the channel — keep `resume.log`, add a push (2026-06-05)

`resume.log` (the polled file the agent reads at the start of each message) stays exactly as is — it is the right tool for resume: **pull-based** (robust to the agent not yet being at its prompt at restart), re-readable (can't be missed), and able to carry bulk context and the `REFRESHED:` suffix. We do **not** fold it into the channel. We only **add** a push notification so the agent *notices* a resume promptly instead of waiting to poll. Because nothing is removed, there is no regression risk.

Survey of the harness→agent inbound surface justified this: of four inbound mechanisms, the initial `AGENTS.md`/`CLAUDE.md` prompt cannot fold (it precedes a live agent and is bulky), both auto-start prompts already *are* injections over the channel's transports, and only `resume.log` was foldable — and it is the riskiest to move (snapshot/resume + restart timing). So the high-value, low-risk change is notify-via-channel + payload-via-file.

Both backends already have a **proven** inject point on resume — the same site fresh auto-start uses (auto-start is currently gated off on resume):

- **opencode:** on resume, at the auto-start site (`session.py`, where `worker_port`/`password`/`session_id` are in scope), send the notice via the existing live transport:
  ```python
  await _post_opencode_prompt(
      worker_port, password, session_id,
      f"{SYSTEM_MESSAGE_PREFIX}{RESUME_NOTICE}",
  )
  ```
  Fires on every resume, independent of `auto_start`.
- **claudecode:** on resume, pass the notice as the **positional prompt arg** to `claude` (extend `build_auto_start_args` with a `resuming` branch returning `[f"{SYSTEM_MESSAGE_PREFIX}{RESUME_NOTICE}"]`). claudecode relaunches `claude` fresh in a new tmux session on resume (restored `home/.claude` carries the transcript) and appends `--continue`, so the positional is appended as a new turn against the restored conversation. **Verified manually (2026-06-05):** `claude --continue '<positional>'` resumes the prior conversation *and* processes the positional as a fresh new turn (does not ignore/prepend it). This is distinct from the live tmux paste transport used by `send_to_agent` (Part C) — resume reuses the launch-time positional-arg mechanism, not a live paste.

**Shared constant:** `RESUME_NOTICE` (e.g. `"you have been resumed"`) lives in `optio-agents` (`protocol/prompt.py`, next to the resume prose) and is imported by both backends so the doc and both transports agree on one string.

**Resume doc — one added sentence (both backends' resume section):** keep the entire existing `resume.log` procedure; append:
  > You may also be notified of a resume by a `System:` message on your input channel; when you see one, follow the `resume.log` procedure above.

The push only makes the agent *notice*; `resume.log` (incl. `REFRESHED:`) remains the source of truth, so no resume logic is duplicated into the message.

**Additional tests:**
- `optio-opencode`: on a resume launch, the wired path calls `_post_opencode_prompt` with `f"System: {RESUME_NOTICE}"`; on a fresh launch it does not.
- `optio-claudecode`: `build_auto_start_args(auto_start=…, resuming=True)` returns `[f"System: {RESUME_NOTICE}"]`; `resuming=False` keeps existing fresh/auto-start behavior.
- Resume section prose (both backends) contains the added `System:` sentence.

## Addendum 5: in-repo demo exercise (`optio-demo`) (2026-06-05)

The channel is exercised end-to-end in-repo (no external consumer needed), proving the round-trip on **both** live transports (opencode HTTP `prompt_async`, claudecode tmux paste-buffer). This is a focused "prank" round-trip: the harness withholds one formatting requirement, rejects the first delivery, and the agent re-emits a corrected one.

**New shared helper** `packages/optio-demo/src/optio_demo/tasks/_feedback.py` (one helper, both backends — no duplication):
```python
_MARKER = "over and out"
_NUDGE = ('Always finish your deliverables by "over and out." '
          "Otherwise I won't know that you have finished talking.")
_CAP = 2
_nudges: dict[str, int] = {}  # process_id -> count

def make_feedback_on_deliverable(tag: str):
    async def _on_deliverable(hook_ctx, path, text) -> str | None:
        print(f"[{tag}] deliverable {path}:\n{text}")
        if text.strip().rstrip(".").lower().endswith(_MARKER):
            return None                                  # accept
        pid = hook_ctx.process_id
        n = _nudges.get(pid, 0)
        if n >= _CAP:                                    # runaway guard
            hook_ctx.report_progress(None, "feedback: nudge cap reached, accepting")
            return None
        _nudges[pid] = n + 1
        hook_ctx.report_progress(None, f"feedback: nudging agent (#{n + 1})")
        return _NUDGE                                    # loop auto-sends (Addendum 1)
    return _on_deliverable
```

**Wiring:** both `tasks/opencode.py` and `tasks/claudecode.py` replace their inline `_on_deliverable` with `make_feedback_on_deliverable("opencode-demo")` / `("claudecode-demo")`, preserving the existing print behavior.

**No consumer-prompt edit.** The marker requirement is deliberately *withheld* from the prompt (that is the prank). The agent's willingness to act on feedback and re-emit now lives in the protocol documentation (Addendum 3), so it is general, not demo-specific. The nudge string itself is sent plain; the `System:` prefix (Addendum 2) is added by `send_to_agent`.

**Observable round-trip:** deliverable #1 (no marker) → hook returns the nudge → loop `send_to_agent` → live transport → agent re-emits #2 ending `over and out` → accepted. The `_CAP` guard bounds a non-complying agent to 2 nudges, preventing a runaway loop / token spend.

**Additional test (`optio-demo`):**
- `make_feedback_on_deliverable` with a stub `hook_ctx`: a delivery missing the marker returns the nudge and increments the per-`process_id` count; a delivery ending with `over and out` (case/period tolerant) returns `None`; past `_CAP`, a missing-marker delivery returns `None` and logs the cap.

## Addendum 6: mandatory deliverable acknowledgment (supersedes Addendum 1's silent path) (2026-06-05)

Real testing surfaced a race: the demo agent emitted `DELIVERABLE` and `DONE` in the same turn, so the rejection nudge arrived after the session was already tearing down — the agent never acted on it and never re-delivered. The fix is a **mandatory acknowledgment** protocol: the harness replies to **every** deliverable, and the agent is told to **wait** for that reply before declaring `DONE`. This keeps the session alive across the round-trip.

**Divergence from the body:** Addendum 1 said `on_deliverable` returning `None`/empty sends nothing. That is **superseded** — every deliverable now produces exactly one reply.

**Change — `optio-agents` (`protocol/session.py`, `_deliverable_fetch_loop`):** after fetching each deliverable, send exactly one `send_to_agent` message, wrapped as `deliverable <basename>: <reply>` (the `System:` prefix is added by `send_to_agent`). The reply has three routes:
- **no callback / `None` / `""` / `"ok"`** → `accepted. thanks for the good work.`
- **any other returned non-empty string** → that string verbatim (the revision request).
- **callback raised** → a harness-side trouble note: `I have trouble with this one. Not your fault, but mine. I will probably need human help. Please remember to deliver this one again later, after you are resumed next time.` (logged via `report_progress`; a hook bug must never hang the agent waiting for a reply.)

The ack fires **unconditionally**, including when no `on_deliverable` is wired — otherwise an agent told to wait would hang.

**Change — protocol documentation (`protocol/prompt.py`, `_FEEDBACK`):** instruct the agent that after a `DELIVERABLE:` line it must **end its turn and wait** for the `System: deliverable <name>: ...` reply, and **not declare `DONE`** until every emitted deliverable is accepted. Three handling routes mirror the replies: *accepted* → proceed; *points out something specific about the deliverable* → revise and re-emit that `DELIVERABLE:` line; *reports trouble on the harness side* → don't retry now, remember to re-emit it after the next resume.

**Scope:** this is the generic `optio-agents` layer, so the acknowledgment protocol applies to **all** consumers (opencode + claudecode backends, and downstream consumers such as excavator), by design.

**The channel is now mandatory for agent backends (supersedes "Sender is optional" in Scope and decisions).** Because mandatory acknowledgment makes a deliverable-emitting agent *wait* for a `System: deliverable …` reply, an agent backend that emits deliverables **must** wire a sender — without one the agent hangs. opencode and claudecode both do. The earlier decision that "the interface tolerates future backends that don't [implement the channel]" is withdrawn for agent backends. The one remaining reason `run_log_protocol_session`'s `agent_sender` parameter keeps a `None` default is **non-agent driver tasks** that emit no deliverables (e.g. the `open-browser-via-tool` demo: a plain host script with no LLM, no `on_deliverable`, only `BROWSER:` → `DONE`); there is no agent waiting, so no sender is required. The default is therefore retained deliberately, with a comment at the parameter explaining this; it is not an "optional channel for agents."

**Updated test (`optio-agents`):** `test_deliverable_routing` now asserts the wrapped `deliverable x.md: ...` message for each route — revision string passes through, `None`/`""`/`"ok"`/no-callback all produce the accept ack, and a raising callback produces the trouble note.
