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
