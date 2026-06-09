# ClaudeCode Conversation Gate (Phase I) — Implementation Plan

> **For agentic workers:** This plan is **parallel-shaped**. Tasks 1–18 are file-disjoint and run concurrently (one agent per task). Agents make file changes ONLY — **no git, no test runs** inside tasks. All verification and all commits happen in the final sequential phase (Tasks V1–V3), driven by the main loop. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Per-task conversation mode for optio-claudecode: Claude Code runs headlessly over bidirectional stream-json stdio, and the launcher receives a live `Conversation` object via a new generic optio-core `publish_result` mechanism.

**Architecture:** optio-core gains a task→launcher return channel (`ctx.publish_result` + `launch_and_await_result` + in-memory registry). optio-agents gains the abstract `Conversation` Protocol and a scaffolding-only mode for the log-protocol driver. optio-claudecode gains `mode="conversation"`: no tmux/ttyd; `claude -p --input-format stream-json --output-format stream-json` launched via `host.launch_subprocess(stdin=True)`; an engine-side driver parses NDJSON events, dispatches subscribers, gates permissions over the control protocol.

**Spec:** `docs/2026-06-10-claudecode-conversation-gate-design.md` (read it first; decisions there are binding).

**Tech stack:** Python 3.11+, asyncio, Motor/MongoDB (Docker), pytest, existing optio-host Host abstraction (asyncssh for remote).

---

## Execution protocol

1. **Before the fan-out** (main loop): `git checkout -b feature/claudecode-conversation-gate`
2. **Wave 1** — Tasks 1–18 in parallel. Every file is owned by exactly one task. Tree may not import/compile mid-wave; that is expected.
3. **Wave 2** — Tasks V1–V3 sequentially in the main loop: live CLI fact-checks, full test runs + fixes, commits.

## Shared contracts (binding for all tasks)

All tasks must use these exact names/signatures. Do not improvise variations.

```python
# optio_core.exceptions
class LaunchError(Exception):            # .reason: str
class ResultNotPublished(Exception):     # .process_id: str

# optio_core ProcessContext
def publish_result(self, obj: Any) -> None

# optio_core Executor
def publish_result(self, process_id: str, obj: Any) -> None
def get_published_result(self, process_id: str) -> Any | None
def ensure_result_future(self, process_id: str) -> "asyncio.Future[Any]"

# optio_core Optio (lifecycle)
async def launch_and_await_result(self, process_id: str, resume: bool = False,
                                  *, session_id: str | None,
                                  timeout: float | None = None) -> Any
def get_published_result(self, process_id: str) -> Any | None

# optio_agents.conversation
class ConversationClosed(Exception)
@dataclass(frozen=True) class PermissionRequest:  tool_name: str; input: dict; raw: dict
@dataclass(frozen=True) class PermissionDecision: behavior: Literal["allow","deny"]; updated_input: dict | None = None; message: str | None = None
class Conversation(Protocol)        # methods per spec §2.1

# optio_agents.protocol.session
async def run_log_protocol_session(..., keywords: bool = True)

# optio_agents.prompt
def compose_agents_md(consumer_instructions, *, documentation: str | None,
                      resume_section: str | None = None) -> str
# documentation None/"" → omit the intro+protocol-docs block entirely

# optio_claudecode.types (new fields on ClaudeCodeTaskConfig)
mode: Literal["iframe", "conversation"] = "iframe"
host_protocol: bool = True
permission_gate: bool = False

# optio_claudecode.conversation
class ClaudeCodeConversation:               # implements the Protocol
    def __init__(self, *, agent_label: str = "claude") -> None
    def attach(self, handle) -> None        # handle: optio_host ProcessHandle (stdin enabled)
    async def run_reader(self) -> None      # long-running; owned by session body
    close_requested: asyncio.Event

# optio_claudecode.host_actions
def build_conversation_argv(claude_path: str, *, claude_flags: list[str],
                            permission_gate: bool) -> list[str]
def conversation_launch_env(workdir: str, extra_env: dict[str, str] | None) -> dict[str, str]

# optio_claudecode.prompt
DEFAULT_CONVERSATION_INSTRUCTIONS = "Let's have a conversation with the user."
def compose_agents_md(consumer_instructions, *, documentation=None,
                      workdir_exclude=None, supports_resume=True,
                      host_protocol: bool = True,
                      omit_task_framing: bool = False) -> str
```

Synthetic event types emitted by the driver (inside the transparent dict stream):
`{"type": "x-optio-unparseable", "line": <str>}` and
`{"type": "x-optio-closed", "reason": <str>}`.

Stream-json wire facts (verified live 2026-06-09):
- input line: `{"type":"user","message":{"role":"user","content":[{"type":"text","text":...}]}}\n`
- output lines: `{"type":"system","subtype":"init",...}`, `{"type":"assistant","message":{...}}`,
  `{"type":"result","subtype":"success"|...,"result":<final text>,"session_id":...,"is_error":...}`
- control protocol (to be re-verified in V1): CLI→us
  `{"type":"control_request","request_id":R,"request":{"subtype":"can_use_tool","tool_name":...,"input":{...}}}`;
  us→CLI `{"type":"control_response","response":{"subtype":"success","request_id":R,"response":{"behavior":"allow"|"deny",...}}}`;
  us→CLI interrupt `{"type":"control_request","request_id":N,"request":{"subtype":"interrupt"}}` acked by a CLI `control_response`.

---

### Task 1: optio-core exceptions

**Files:**
- Modify: `packages/optio-core/src/optio_core/exceptions.py`

- [ ] **Step 1: Append the two new exception classes**

```python
class LaunchError(Exception):
    """Raised by Optio.launch_and_await_result when the launch itself is
    refused (the LaunchOutcome was not ok). ``reason`` carries the typed
    LaunchOutcome reason string (e.g. "not-found", "not-launchable")."""

    def __init__(self, process_id: str, reason: str):
        self.process_id = process_id
        self.reason = reason
        super().__init__(f"launch of '{process_id}' refused: {reason}")


class ResultNotPublished(Exception):
    """Raised by Optio.launch_and_await_result when the process reached a
    terminal state without ever calling ctx.publish_result."""

    def __init__(self, process_id: str):
        self.process_id = process_id
        super().__init__(
            f"process '{process_id}' ended without publishing a result"
        )
```

---

### Task 2: ProcessContext.publish_result

**Files:**
- Modify: `packages/optio-core/src/optio_core/context.py`

- [ ] **Step 1: Add the method to `ProcessContext`** (place it near `report_progress`; `self._executor` is set by the executor right after context construction — see `executor.py:190`)

```python
def publish_result(self, obj: Any) -> None:
    """Publish an opaque result object to this process's launcher.

    May be called at most once per run; a second call raises RuntimeError.
    The object is held in memory only (never persisted): it reaches a
    same-process caller awaiting ``launch_and_await_result`` and the
    engine-side result registry. The task keeps running after publishing.
    """
    if self._executor is None:
        raise RuntimeError(
            "publish_result: no executor attached to this context"
        )
    self._executor.publish_result(self.process_id, obj)
```

---

### Task 3: Executor result registry + futures

**Files:**
- Modify: `packages/optio-core/src/optio_core/executor.py`

- [ ] **Step 1: Add the two dicts in `Executor.__init__`** (next to `_running_tasks`, line ~69)

```python
        # Task→launcher return channel (in-memory only, same-process).
        # Keyed by processId string. Registry holds published objects for
        # the lifetime of the run; futures exist only while a
        # launch_and_await_result caller is (about to be) waiting.
        self._result_registry: dict[str, Any] = {}
        self._result_futures: dict[str, asyncio.Future] = {}
```

- [ ] **Step 2: Add the three methods on `Executor`**

```python
    def publish_result(self, process_id: str, obj: Any) -> None:
        """Register a task-published result; resolve any waiting launcher."""
        if process_id in self._result_registry:
            raise RuntimeError(
                f"publish_result: '{process_id}' already published a result "
                "for this run"
            )
        self._result_registry[process_id] = obj
        fut = self._result_futures.get(process_id)
        if fut is not None and not fut.done():
            fut.set_result(obj)

    def get_published_result(self, process_id: str) -> Any | None:
        """Return the live published object for a running process, or None."""
        return self._result_registry.get(process_id)

    def ensure_result_future(self, process_id: str) -> "asyncio.Future[Any]":
        """Create (or return) the launcher-side future for process_id.

        Called by launch_and_await_result BEFORE scheduling the launch so a
        task that publishes immediately cannot race the waiter.
        """
        fut = self._result_futures.get(process_id)
        if fut is None or fut.done():
            fut = asyncio.get_event_loop().create_future()
            self._result_futures[process_id] = fut
        return fut
```

- [ ] **Step 3: Terminal cleanup.** In `_execute_process`, extend the existing `finally` block (`executor.py:321-323`) — note `proc["processId"]` is in scope:

```python
        finally:
            self._cancellation_flags.pop(oid, None)
            self._running_tasks.pop(oid, None)
            # Result channel teardown: drop the registry entry; fail any
            # still-waiting launcher with ResultNotPublished.
            _pid = proc["processId"]
            self._result_registry.pop(_pid, None)
            _fut = self._result_futures.pop(_pid, None)
            if _fut is not None and not _fut.done():
                from optio_core.exceptions import ResultNotPublished
                _fut.set_exception(ResultNotPublished(_pid))
```

---

### Task 4: Optio.launch_and_await_result

**Files:**
- Modify: `packages/optio-core/src/optio_core/lifecycle.py`

- [ ] **Step 1: Imports** — add `LaunchError` to the existing `optio_core.exceptions` import (or create one near the top of the file if absent).

- [ ] **Step 2: Add the two methods to `Optio`** (right after `launch_and_wait`, line ~447)

```python
    async def launch_and_await_result(
        self, process_id: str, resume: bool = False, *,
        session_id: str | None, timeout: float | None = None,
    ) -> Any:
        """Launch like ``launch()`` and wait for the task to call
        ``ctx.publish_result(obj)``; return that object while the task keeps
        running.

        Raises LaunchError(reason) when the launch is refused,
        ResultNotPublished when the task ends without publishing, and
        asyncio.TimeoutError on ``timeout`` expiry (the task keeps running).
        """
        # Resolve the canonical processId first: the future map is keyed by
        # processId, but callers may pass OID hex (dual-form, like launch()).
        proc = await self._resolve(process_id)
        canonical = proc["processId"] if proc is not None else process_id
        fut = self._executor.ensure_result_future(canonical)
        outcome = await self.launch(
            process_id, resume=resume, session_id=session_id,
        )
        if not outcome.ok:
            self._executor._result_futures.pop(canonical, None)
            raise LaunchError(process_id, outcome.reason or "unknown")
        if timeout is not None:
            return await asyncio.wait_for(fut, timeout)
        return await fut

    def get_published_result(self, process_id: str) -> Any | None:
        """Live published object for a running process, or None.

        Phase II attachment point: RPC/listener handlers look the live
        object up here by process_id.
        """
        return self._executor.get_published_result(process_id)
```

---

### Task 5: optio-core public exports

**Files:**
- Modify: `packages/optio-core/src/optio_core/__init__.py`

- [ ] **Step 1: Bind and export the new surface**

```python
from optio_core.exceptions import LaunchError, ResultNotPublished
```

Add after line 17 (`launch_and_wait = ...`):

```python
launch_and_await_result = _instance.launch_and_await_result
get_published_result = _instance.get_published_result
```

Extend `__all__` with: `"launch_and_await_result"`, `"get_published_result"`, `"LaunchError"`, `"ResultNotPublished"`.

---

### Task 6: optio-core tests for the result channel

**Files:**
- Create: `packages/optio-core/tests/test_publish_result.py`

Follow the conventions of the existing optio-core tests (see `tests/conftest.py` for the mongo fixture and how ad-hoc tasks are defined/launched; mirror the patterns used in `test_cancel_propagation.py`). MongoDB via Docker, as the existing harness does.

- [ ] **Step 1: Write the test file**

```python
"""launch_and_await_result / publish_result matrix."""

import asyncio

import pytest

import optio_core
from optio_core.exceptions import LaunchError, ResultNotPublished


# NOTE: adapt fixture/bootstrap names to tests/conftest.py at implementation
# time — use the same init/teardown the neighbouring lifecycle tests use.


async def _define(optio, process_id, execute):
    await optio.adhoc_define(
        optio_core.TaskInstance(
            execute=execute, process_id=process_id, name=process_id,
        )
    )


@pytest.mark.asyncio
async def test_await_then_publish(optio_running):
    """Caller awaits first; task publishes; object delivered, task continues."""
    release = asyncio.Event()

    async def execute(ctx):
        ctx.publish_result({"hello": "world"})
        await release.wait()

    await _define(optio_running, "pub-1", execute)
    result = await optio_running.launch_and_await_result(
        "pub-1", session_id=None, timeout=10,
    )
    assert result == {"hello": "world"}
    # Task is still running and the registry serves the live object.
    assert optio_running.get_published_result("pub-1") == {"hello": "world"}
    release.set()


@pytest.mark.asyncio
async def test_terminal_without_publish_raises(optio_running):
    async def execute(ctx):
        return  # ends without publishing

    await _define(optio_running, "pub-2", execute)
    with pytest.raises(ResultNotPublished):
        await optio_running.launch_and_await_result(
            "pub-2", session_id=None, timeout=10,
        )


@pytest.mark.asyncio
async def test_double_publish_raises_inside_task(optio_running):
    seen: list[BaseException] = []

    async def execute(ctx):
        ctx.publish_result(1)
        try:
            ctx.publish_result(2)
        except RuntimeError as e:
            seen.append(e)

    await _define(optio_running, "pub-3", execute)
    res = await optio_running.launch_and_await_result(
        "pub-3", session_id=None, timeout=10,
    )
    assert res == 1
    # allow the body to finish
    await asyncio.sleep(0.2)
    assert len(seen) == 1


@pytest.mark.asyncio
async def test_launch_refused_raises_launcherror(optio_running):
    with pytest.raises(LaunchError) as ei:
        await optio_running.launch_and_await_result(
            "no-such-process", session_id=None,
        )
    assert ei.value.reason == "not-found"


@pytest.mark.asyncio
async def test_timeout_keeps_task_running(optio_running):
    release = asyncio.Event()

    async def execute(ctx):
        await release.wait()  # never publishes until released

    await _define(optio_running, "pub-4", execute)
    with pytest.raises(asyncio.TimeoutError):
        await optio_running.launch_and_await_result(
            "pub-4", session_id=None, timeout=0.3,
        )
    proc = await optio_running.get_process("pub-4")
    assert proc["status"]["state"] == "running"
    release.set()


@pytest.mark.asyncio
async def test_registry_cleared_on_terminal(optio_running):
    async def execute(ctx):
        ctx.publish_result("x")

    await _define(optio_running, "pub-5", execute)
    await optio_running.launch_and_await_result(
        "pub-5", session_id=None, timeout=10,
    )
    # wait for terminal state
    for _ in range(100):
        proc = await optio_running.get_process("pub-5")
        if proc["status"]["state"] in {"done", "failed", "cancelled"}:
            break
        await asyncio.sleep(0.05)
    assert optio_running.get_published_result("pub-5") is None
```

---

### Task 7: Conversation Protocol in optio-agents

**Files:**
- Create: `packages/optio-agents/src/optio_agents/conversation.py`

- [ ] **Step 1: Write the module**

```python
"""Abstract conversation surface for optio agent backends.

Type surface only: the semantic operations (send / events / answers / busy /
close) are shared across backends; every concrete event payload is
backend-specific and passed through transparently as a dict. Concrete
implementations live in the agent packages (ClaudeCodeConversation in
optio-claudecode; an opencode implementation over HTTP+SSE may follow).

See docs/2026-06-10-claudecode-conversation-gate-design.md §2.1.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Awaitable, Callable, Literal, Protocol, runtime_checkable


class ConversationClosed(Exception):
    """Raised by send()/interrupt() after the conversation has ended."""


@dataclass(frozen=True)
class PermissionRequest:
    """One tool-permission question from the agent backend."""
    tool_name: str
    input: dict
    raw: dict  # full backend payload, transparent


@dataclass(frozen=True)
class PermissionDecision:
    behavior: Literal["allow", "deny"]
    updated_input: dict | None = None  # allow with modified input
    message: str | None = None         # deny reason, surfaced to the model


EventHandler = Callable[[dict], "Awaitable[None] | None"]
MessageHandler = Callable[[str], "Awaitable[None] | None"]
PermissionHandler = Callable[[PermissionRequest], "Awaitable[PermissionDecision]"]
Unsubscribe = Callable[[], None]


@runtime_checkable
class Conversation(Protocol):
    """Live conversation with a running agent session.

    Secondary interface: process management (monitor/cancel) stays on the
    regular optio surface; this object only talks to the model.
    """

    async def send(self, text: str) -> None:
        """Queue one user message. Raises ConversationClosed after end."""
        ...

    def on_event(self, handler: EventHandler) -> Unsubscribe:
        """Subscribe to the transparent backend event stream (dicts,
        unmodified, live-only). Returns an unsubscribe function."""
        ...

    def on_message(self, handler: MessageHandler) -> Unsubscribe:
        """Simplified tier: one call per completed turn with the final
        answer text."""
        ...

    def on_permission_request(self, handler: PermissionHandler) -> Unsubscribe:
        """Register the permission gate (at most one; replaces prior)."""
        ...

    def is_pending(self) -> bool:
        """True while at least one sent message awaits its turn result."""
        ...

    async def interrupt(self) -> None:
        """Abort the current turn at the next safe point. No-op when idle."""
        ...

    async def close(self) -> None:
        """Request cooperative shutdown of the owning task. Idempotent."""
        ...

    @property
    def closed(self) -> bool:
        """True once the session has ended (any cause)."""
        ...
```

---

### Task 8: optio-agents exports

**Files:**
- Modify: `packages/optio-agents/src/optio_agents/__init__.py`

- [ ] **Step 1: Add imports + `__all__` entries**

```python
from optio_agents.conversation import (
    Conversation,
    ConversationClosed,
    PermissionDecision,
    PermissionRequest,
)
```

Extend `__all__` with: `"Conversation"`, `"ConversationClosed"`, `"PermissionDecision"`, `"PermissionRequest"`.

---

### Task 9: Scaffolding-only mode for the protocol driver

**Files:**
- Modify: `packages/optio-agents/src/optio_agents/protocol/session.py`

- [ ] **Step 1: Add the parameter** to `run_log_protocol_session` (after `prepare`):

```python
    keywords: bool = True,
```

Docstring addition: with `keywords=False` the driver runs scaffolding only —
workdir lifecycle + `prepare`, deliverables dir, optio.log creation, browser
shims, hooks, cancel watcher — but no optio.log tail, no deliverable fetch
loop, no DONE/ERROR semantics, and no premature-exit-without-DONE rule (the
body's own return governs: normal return = clean completion).

- [ ] **Step 2: Branch the task fan-out.** Replace the block that creates `fetch_task`/`tail_task` and the `asyncio.wait` set (lines ~212-253) with:

```python
        deliverable_queue: asyncio.Queue[tuple[str, str]] = asyncio.Queue(
            maxsize=DELIVERABLE_QUEUE_BOUND,
        )
        done_flag = asyncio.Event()
        error_flag: list[str | None] = []  # [message] or [] if not fired

        if keywords:
            fetch_task = asyncio.create_task(
                _deliverable_fetch_loop(host, on_deliverable, deliverable_queue, ctx, hook_ctx),
            )
            tail_task = asyncio.create_task(
                _tail_and_dispatch(
                    host, ctx, deliverable_queue, done_flag, error_flag,
                    protocol.parse_log_line, browser_url_rewrite,
                ),
            )
        body_task = asyncio.create_task(body(host, hook_ctx))
        cancel_task = asyncio.create_task(_watch_cancellation(ctx))

        wait_set = {body_task, cancel_task}
        if tail_task is not None:
            wait_set.add(tail_task)
        done, _pending = await asyncio.wait(
            wait_set, return_when=asyncio.FIRST_COMPLETED,
        )

        cancelled = (
            cancel_task in done
            and not cancel_task.cancelled()
            and cancel_task.exception() is None
            and cancel_task.result() is True
        )

        if error_flag:
            raise _SessionFailed(error_flag[0] or "agent reported ERROR")

        if body_task in done and not cancelled:
            exc = body_task.exception()
            if exc is not None:
                raise exc
            if keywords and not done_flag.is_set():
                # Body completed without DONE — premature exit.
                raise _SessionFailed("body returned before DONE was observed")

        # Drain remaining deliverables before returning.
        if keywords:
            await deliverable_queue.join()
```

(The `finally` block's `active_tasks` list already tolerates `None` entries — unchanged.)

---

### Task 10: Shared prompt composer — omittable protocol docs

**Files:**
- Modify: `packages/optio-agents/src/optio_agents/prompt.py`

- [ ] **Step 1: Change `compose_agents_md`** so a falsy `documentation` omits the intro + docs block:

```python
def compose_agents_md(
    consumer_instructions: str,
    *,
    documentation: str | None,
    resume_section: str | None = None,
) -> str:
    pre = (_INTRO + documentation + "\n") if documentation else ""
    body = consumer_instructions.rstrip()
    resume_block = (resume_section + "\n") if resume_section else ""
    return f"{pre}{resume_block}{BASE_PROMPT_POST}\n{body}\n"
```

Note the trailing-newline shuffle: previously `f"{pre}\n{resume_block}..."` with `pre` always non-empty. Keep the rendered output byte-identical for the documentation-present case (existing prompt tests in optio-claudecode/optio-opencode must keep passing) — i.e. `_INTRO + documentation + "\n"` reproduces the old `pre + "\n"`.

---

### Task 11: ClaudeCodeTaskConfig new fields + validation

**Files:**
- Modify: `packages/optio-claudecode/src/optio_claudecode/types.py`

- [ ] **Step 1: Extend the Literal + valid set**

```python
PermissionMode = Literal["default", "plan", "acceptEdits", "bypassPermissions", "dontAsk"]
_VALID_PERMISSION_MODES = {"default", "plan", "acceptEdits", "bypassPermissions", "dontAsk"}
_HEADLESS_SAFE_PERMISSION_MODES = {"acceptEdits", "bypassPermissions", "dontAsk"}

ConversationMode = Literal["iframe", "conversation"]
```

- [ ] **Step 2: Add fields to `ClaudeCodeTaskConfig`** (keep all defaults backward-compatible):

```python
    # --- conversation surface (spec: 2026-06-10 conversation gate) -------
    # "iframe" = today's tmux+ttyd behavior (default, unchanged).
    # "conversation" = headless stream-json session; the task publishes a
    # ClaudeCodeConversation via ctx.publish_result.
    mode: ConversationMode = "iframe"
    # Opt-out for the optio.log keyword channel (STATUS/DELIVERABLE/DONE/…).
    # iframe mode requires True (it is the only completion signal there).
    host_protocol: bool = True
    # Conversation mode only: route Claude Code's can_use_tool permission
    # questions to the Conversation's on_permission_request handler over the
    # stream-json control protocol.
    permission_gate: bool = False
```

- [ ] **Step 3: Extend `__post_init__`** (append after the existing checks):

```python
        if self.mode not in ("iframe", "conversation"):
            raise ValueError(
                f"ClaudeCodeTaskConfig.mode={self.mode!r} is not one of "
                "['iframe', 'conversation']"
            )
        if self.mode == "iframe" and not self.host_protocol:
            raise ValueError(
                "ClaudeCodeTaskConfig: host_protocol=False requires "
                "mode='conversation' (in iframe mode the optio.log keyword "
                "channel is the only completion signal)."
            )
        if self.permission_gate and self.mode != "conversation":
            raise ValueError(
                "ClaudeCodeTaskConfig: permission_gate=True requires "
                "mode='conversation'."
            )
        if self.mode == "conversation" and not self.permission_gate:
            headless_ok = (
                self.permission_mode in _HEADLESS_SAFE_PERMISSION_MODES
                or bool(self.allowed_tools)
            )
            if not headless_ok:
                raise ValueError(
                    "ClaudeCodeTaskConfig: conversation mode without "
                    "permission_gate needs a non-interactive permission "
                    "setup — permission_mode in "
                    f"{sorted(_HEADLESS_SAFE_PERMISSION_MODES)} or a "
                    "non-empty allowed_tools (headless Claude cannot show "
                    "a permission dialog)."
                )
```

Also export `ConversationMode` in `__all__`.

---

### Task 12: ClaudeCodeConversation driver

**Files:**
- Create: `packages/optio-claudecode/src/optio_claudecode/conversation.py`

- [ ] **Step 1: Write the module**

```python
"""ClaudeCodeConversation — engine-side driver for one headless Claude Code
session over the bidirectional stream-json stdio protocol.

The session body launches ``claude -p --input-format stream-json
--output-format stream-json`` via ``host.launch_subprocess(stdin=True)``,
attaches the handle here, publishes this object via ``ctx.publish_result``,
and runs ``run_reader()`` until the subprocess ends.

Event payloads are transparent: every parsed stdout NDJSON object is fanned
out to ``on_event`` subscribers as a dict, unmodified. Synthetic events use
the ``x-optio-`` type prefix. See the design doc §3.3.
"""

from __future__ import annotations

import asyncio
import json
import logging

from optio_agents.conversation import (
    ConversationClosed,
    PermissionDecision,
    PermissionRequest,
)

_LOG = logging.getLogger(__name__)


def _user_message_line(text: str) -> bytes:
    return (json.dumps({
        "type": "user",
        "message": {"role": "user", "content": [{"type": "text", "text": text}]},
    }) + "\n").encode("utf-8")


class ClaudeCodeConversation:
    """Implements optio_agents.conversation.Conversation for Claude Code."""

    def __init__(self, *, agent_label: str = "claude") -> None:
        self._agent_label = agent_label
        self._handle = None
        self._pending = 0
        self._closed = asyncio.Event()
        self._close_reason: str | None = None
        # Cooperative-shutdown request towards the owning task body.
        self.close_requested = asyncio.Event()
        self._write_lock = asyncio.Lock()
        self._event_queue: asyncio.Queue[dict] = asyncio.Queue()
        self._event_handlers: list = []
        self._message_handlers: list = []
        self._permission_handler = None
        self._queued_permission_requests: list[dict] = []
        self._next_request_id = 0
        self._control_acks: dict[str, asyncio.Future] = {}
        self._dispatcher_task: asyncio.Task | None = None

    # -- wiring ------------------------------------------------------------

    def attach(self, handle) -> None:
        """Attach the live ProcessHandle (must have been launched with
        stdin=True)."""
        if handle.stdin is None:
            raise ValueError(
                "ClaudeCodeConversation.attach: handle has no stdin writer; "
                "launch the subprocess with stdin=True"
            )
        self._handle = handle

    async def run_reader(self) -> None:
        """Drain stdout until EOF; dispatch events. Owned by the session
        body; ends when the subprocess ends."""
        self._dispatcher_task = asyncio.create_task(self._dispatch_loop())
        try:
            async for raw in self._handle.stdout:
                line = (
                    raw.decode("utf-8", errors="replace")
                    if isinstance(raw, bytes) else str(raw)
                ).strip()
                if not line:
                    continue
                try:
                    obj = json.loads(line)
                except ValueError:
                    _LOG.warning("conversation: unparseable line: %.200s", line)
                    self._event_queue.put_nowait(
                        {"type": "x-optio-unparseable", "line": line},
                    )
                    continue
                self._route(obj)
        finally:
            await self._finish("process ended")

    def _route(self, obj: dict) -> None:
        t = obj.get("type")
        if t == "result":
            self._pending = max(0, self._pending - 1)
            text = obj.get("result")
            if isinstance(text, str):
                self._fire_message(text)
        elif t == "control_response":
            # Ack for one of OUR control_requests (e.g. interrupt).
            resp = obj.get("response") or {}
            rid = str(resp.get("request_id", ""))
            fut = self._control_acks.pop(rid, None)
            if fut is not None and not fut.done():
                fut.set_result(obj)
        elif t == "control_request":
            req = obj.get("request") or {}
            if req.get("subtype") == "can_use_tool":
                self._on_can_use_tool(obj)
            else:
                _LOG.info(
                    "conversation: unhandled control_request subtype %r",
                    req.get("subtype"),
                )
        self._event_queue.put_nowait(obj)

    # -- event fan-out -----------------------------------------------------

    async def _dispatch_loop(self) -> None:
        while True:
            obj = await self._event_queue.get()
            for handler in list(self._event_handlers):
                await self._call_handler(handler, obj, "on_event")

    async def _call_handler(self, handler, arg, label: str) -> None:
        try:
            result = handler(arg)
            if asyncio.iscoroutine(result):
                await result
        except Exception:  # noqa: BLE001 — subscriber bugs never kill the driver
            _LOG.exception("conversation: %s handler raised", label)

    def _fire_message(self, text: str) -> None:
        for handler in list(self._message_handlers):
            asyncio.ensure_future(self._call_handler(handler, text, "on_message"))

    # -- permission gate ----------------------------------------------------

    def _on_can_use_tool(self, obj: dict) -> None:
        if self._permission_handler is None:
            # Queue until a handler is registered: the turn blocks CLI-side,
            # which closes the publish/registration race. Documented caller
            # contract: register promptly when permission_gate=True.
            self._queued_permission_requests.append(obj)
            return
        asyncio.ensure_future(self._answer_permission(obj))

    async def _answer_permission(self, obj: dict) -> None:
        req = obj.get("request") or {}
        request = PermissionRequest(
            tool_name=req.get("tool_name", ""),
            input=req.get("input") or {},
            raw=obj,
        )
        try:
            decision = await self._permission_handler(request)
        except Exception:  # noqa: BLE001
            _LOG.exception("conversation: permission handler raised; denying")
            decision = PermissionDecision(
                behavior="deny",
                message="optio harness: permission handler failed",
            )
        inner: dict = {"behavior": decision.behavior}
        if decision.behavior == "allow":
            inner["updatedInput"] = (
                decision.updated_input
                if decision.updated_input is not None else request.input
            )
        elif decision.message:
            inner["message"] = decision.message
        await self._write_json({
            "type": "control_response",
            "response": {
                "subtype": "success",
                "request_id": obj.get("request_id"),
                "response": inner,
            },
        })

    # -- Conversation protocol surface --------------------------------------

    async def send(self, text: str) -> None:
        if self._closed.is_set():
            raise ConversationClosed(self._close_reason or "conversation closed")
        self._pending += 1
        try:
            await self._write_bytes(_user_message_line(text))
        except Exception:
            self._pending -= 1
            await self._finish("stdin write failed")
            raise

    def on_event(self, handler):
        self._event_handlers.append(handler)
        return lambda: self._event_handlers.remove(handler)

    def on_message(self, handler):
        self._message_handlers.append(handler)
        return lambda: self._message_handlers.remove(handler)

    def on_permission_request(self, handler):
        self._permission_handler = handler
        queued, self._queued_permission_requests = (
            self._queued_permission_requests, [],
        )
        for obj in queued:
            asyncio.ensure_future(self._answer_permission(obj))

        def _unsub() -> None:
            if self._permission_handler is handler:
                self._permission_handler = None
        return _unsub

    def is_pending(self) -> bool:
        return self._pending > 0

    async def interrupt(self) -> None:
        if self._closed.is_set():
            raise ConversationClosed(self._close_reason or "conversation closed")
        if self._pending == 0:
            return
        self._next_request_id += 1
        rid = f"optio-{self._next_request_id}"
        fut: asyncio.Future = asyncio.get_event_loop().create_future()
        self._control_acks[rid] = fut
        await self._write_json({
            "type": "control_request",
            "request_id": rid,
            "request": {"subtype": "interrupt"},
        })
        await fut

    async def close(self) -> None:
        self.close_requested.set()

    @property
    def closed(self) -> bool:
        return self._closed.is_set()

    # -- internals -----------------------------------------------------------

    async def _write_json(self, obj: dict) -> None:
        await self._write_bytes((json.dumps(obj) + "\n").encode("utf-8"))

    async def _write_bytes(self, data: bytes) -> None:
        async with self._write_lock:
            stdin = self._handle.stdin
            stdin.write(data)
            drain = getattr(stdin, "drain", None)
            if drain is not None:
                await drain()

    async def _finish(self, reason: str) -> None:
        if self._closed.is_set():
            return
        self._closed.set()
        self._close_reason = reason
        # Fail any in-flight interrupt acks.
        for fut in self._control_acks.values():
            if not fut.done():
                fut.set_exception(ConversationClosed(reason))
        self._control_acks.clear()
        self._event_queue.put_nowait({"type": "x-optio-closed", "reason": reason})
        # Stop the dispatcher, then drain whatever it left in the queue so
        # subscribers are guaranteed to see the final x-optio-closed event.
        if self._dispatcher_task is not None:
            self._dispatcher_task.cancel()
            try:
                await self._dispatcher_task
            except asyncio.CancelledError:
                pass
            self._dispatcher_task = None
        while not self._event_queue.empty():
            obj = self._event_queue.get_nowait()
            for handler in list(self._event_handlers):
                await self._call_handler(handler, obj, "on_event")
```

---

### Task 13: host_actions — conversation argv + env builders

**Files:**
- Modify: `packages/optio-claudecode/src/optio_claudecode/host_actions.py`

- [ ] **Step 1: Add the two builders** (place near `build_claude_flags`). The netns seal and the DONE/ERROR bash wrapper are deliberately NOT applied in conversation mode (engine observes the exit directly; conversation mode assumes seeded/planted creds — no in-session OAuth loopback).

```python
def build_conversation_argv(
    claude_path: str, *, claude_flags: list[str], permission_gate: bool,
) -> list[str]:
    """Argv for a headless stream-json conversation session.

    ``--verbose`` is required for stream-json output in -p mode. When
    ``permission_gate`` is set, the stdio permission-prompt plumbing is added
    so can_use_tool questions arrive as control_request lines on stdout.
    (Flag spelling verified against the live CLI in the plan's V1 phase.)
    """
    out = [
        claude_path, "-p",
        "--input-format", "stream-json",
        "--output-format", "stream-json",
        "--verbose",
        *claude_flags,
    ]
    if permission_gate:
        out += ["--permission-prompt-tool", "stdio"]  # V1-VERIFY
    return out


def conversation_launch_env(
    workdir: str, extra_env: dict[str, str] | None,
) -> dict[str, str]:
    """Launch env for conversation mode: same HOME isolation + PATH shape as
    the tmux path (_build_claude_shell_command), minus tmux/netns concerns."""
    workdir_clean = workdir.rstrip("/")
    home_dir = f"{workdir_clean}/home"
    home_local_bin = f"{home_dir}/.local/bin"
    extra = dict(extra_env or {})
    base_path = extra.pop("PATH", None) or os.environ.get(
        "PATH", "/usr/local/bin:/usr/bin:/bin",
    )
    return {
        "HOME": home_dir,
        "PATH": f"{home_local_bin}:{base_path}",
        **extra,
    }
```

---

### Task 14: session.py — mode branch, publish, close flag, teardown

**Files:**
- Modify: `packages/optio-claudecode/src/optio_claudecode/session.py`

This task owns the whole file; internal refactors are allowed as long as iframe-mode behavior stays byte-identical.

- [ ] **Step 1: New imports**

```python
from optio_claudecode.conversation import ClaudeCodeConversation
from optio_claudecode.prompt import DEFAULT_CONVERSATION_INSTRUCTIONS
```

- [ ] **Step 2: Extract the shared workdir-content block.** The fresh/resume planting logic currently inlined in `_claudecode_body` (plant_home_files / merge_seed / CLAUDE.md write / `_maybe_refresh_on_resume` / `_append_resume_log_entry` / `before_execute`) moves to a module-level helper so both bodies share it:

```python
async def _plant_session_content(
    ctx, host, hook_ctx, config, protocol, *,
    resuming: bool, resolved_seed_id: str | None,
) -> "tuple[str | None, dict[str, str]]":
    """Fresh/resume content planting shared by both modes.

    Returns ``(cred_baseline, focus_env)``. Mirrors the pre-existing inline logic
    exactly for the iframe path; conversation mode reuses it unchanged
    except for the prompt-composition kwargs (mode-aware, see Step 3).
    """
    effective_claude_config, focus_env = host_actions.build_focus_mode(
        focus_mode=config.focus_mode, claude_config=config.claude_config,
    )
    cred_baseline: str | None = None
    refreshed_files: list[str] = []
    instructions = config.consumer_instructions
    omit_task_framing = False
    if config.mode == "conversation" and not instructions:
        instructions = DEFAULT_CONVERSATION_INSTRUCTIONS
        omit_task_framing = True
    if not resuming:
        await host_actions.plant_home_files(
            host,
            credentials_json=config.credentials_json,
            claude_config=effective_claude_config,
        )
        if resolved_seed_id is not None:
            await _seeds.merge_seed(
                ctx, host,
                seed_id=resolved_seed_id,
                manifest=CLAUDE_SEED_MANIFEST,
                suffix=CLAUDE_SEED_SUFFIX,
                decrypt=config.session_blob_decrypt,
            )
            cred_baseline = await cred_watcher.cred_fingerprint(host)
        await host.write_text(
            "CLAUDE.md",
            compose_agents_md(
                instructions,
                documentation=protocol.documentation if config.host_protocol else None,
                workdir_exclude=config.workdir_exclude,
                supports_resume=config.supports_resume,
                host_protocol=config.host_protocol,
                omit_task_framing=omit_task_framing,
            ),
        )
    else:
        if resolved_seed_id is not None:
            await _seeds.merge_seed(
                ctx, host,
                seed_id=resolved_seed_id,
                manifest=CLAUDE_CRED_MANIFEST,
                suffix=CLAUDE_SEED_SUFFIX,
                decrypt=config.session_blob_decrypt,
            )
        cred_baseline = await cred_watcher.cred_fingerprint(host)
        refreshed_files = await _maybe_refresh_on_resume(host, hook_ctx, config)
    if config.supports_resume:
        await _append_resume_log_entry(host, refreshed=refreshed_files)
    if config.before_execute is not None:
        await config.before_execute(hook_ctx)
    return cred_baseline, focus_env
```

`_claudecode_body` (the tmux path) is rewritten to call this helper; everything from the launch step on stays as-is. NOTE: `_maybe_refresh_on_resume` already calls `compose_agents_md` — pass the same new kwargs there too (`host_protocol=new_config.host_protocol`, `omit_task_framing` recomputed from the refreshed config).

- [ ] **Step 3: Conversation body.** Add to `run_claudecode_session` (the surrounding state vars follow the existing pattern):

```python
    conversation: ClaudeCodeConversation | None = None
    if config.mode == "conversation":
        conversation = ClaudeCodeConversation()

    async def _conversation_body(host: Host, hook_ctx: HookContext) -> None:
        nonlocal launched_handle, cred_baseline, cred_watch_task
        nonlocal resolved_seed_id, lease_holder

        if callable(config.seed_id):
            resolved_seed_id = await config.seed_id(ctx.process_id)
            lease_holder = ctx.process_id
        else:
            resolved_seed_id = config.seed_id

        cred_baseline_out, focus_env = await _plant_session_content(
            ctx, host, hook_ctx, config, protocol,
            resuming=resuming, resolved_seed_id=resolved_seed_id,
        )
        cred_baseline = cred_baseline_out

        claude_flags = host_actions.build_claude_flags(
            permission_mode=config.permission_mode,
            allowed_tools=config.allowed_tools,
            disallowed_tools=config.disallowed_tools,
            resuming=pass_continue,
        )
        argv = host_actions.build_conversation_argv(
            claude_path, claude_flags=claude_flags,
            permission_gate=config.permission_gate,
        )
        env = host_actions.conversation_launch_env(
            host.workdir,
            {**(config.env or {}), **focus_env, **(hook_ctx.browser_launch_env or {})},
        )
        ctx.report_progress(None, "Launching Claude Code (conversation)…")
        cmd = " ".join(shlex.quote(a) for a in argv)
        handle = await host.launch_subprocess(
            cmd, env=env, cwd=host.workdir,
            env_remove=config.scrub_env, stdin=True,
        )
        launched_handle = handle
        conversation.attach(handle)
        reader_task = asyncio.create_task(conversation.run_reader())

        ctx.publish_result(conversation)
        ctx.report_progress(None, "Claude Code conversation is live")

        # Kickoff / resume notice as first stdin messages (print mode with
        # --input-format stream-json takes no positional prompt).
        if config.auto_start and not resuming:
            await conversation.send(host_actions.AUTO_START_PROMPT)
        elif resuming and pass_continue:
            await conversation.send(f"{SYSTEM_MESSAGE_PREFIX}{RESUME_NOTICE}")

        if resolved_seed_id is not None:
            cred_watch_task = asyncio.create_task(cred_watcher.run_credential_watcher(
                ctx, host,
                seed_id=resolved_seed_id,
                baseline=cred_baseline,
                encrypt=config.session_blob_encrypt,
                decrypt=config.session_blob_decrypt,
                lease_holder=lease_holder,
            ))

        try:
            proc = handle.pid_like
            wait_task = asyncio.create_task(proc.wait())
            close_task = asyncio.create_task(conversation.close_requested.wait())
            done, _ = await asyncio.wait(
                {wait_task, close_task}, return_when=asyncio.FIRST_COMPLETED,
            )
            if close_task in done and wait_task not in done:
                # Caller asked to close: cooperative shutdown, clean end.
                wait_task.cancel()
                return
            # Subprocess exited on its own.
            close_task.cancel()
            if not conversation.close_requested.is_set() and ctx.should_continue():
                rc = getattr(proc, "returncode", None)
                raise RuntimeError(
                    f"claude exited unexpectedly (exit {rc})"
                )
        finally:
            if cred_watch_task is not None:
                cred_watch_task.cancel()
                try:
                    await cred_watch_task
                except asyncio.CancelledError:
                    pass
                cred_watch_task = None
            reader_task.cancel()
            try:
                await reader_task
            except asyncio.CancelledError:
                pass
```

CAVEAT for the implementer: `proc.wait()` on LocalHost is the asyncio subprocess; on RemoteHost `pid_like` is the asyncssh process — check how the existing tmux path awaits exits (`session.py:350-351` in the opencode twin) and reuse the same `proc_wait`-style helper from `optio_host.host` if `pid_like.wait()` is not uniform across hosts.

- [ ] **Step 4: Wire the branch + driver flags.** In `run_claudecode_session`:

```python
    body = _conversation_body if config.mode == "conversation" else _claudecode_body
    ...
        await run_log_protocol_session(
            host, ctx,
            body=body,
            prepare=_prepare,
            on_deliverable=config.on_deliverable,
            after_execute=config.after_execute,
            protocol=protocol,
            browser_url_rewrite=rewrite_oauth_redirect,
            agent_sender=_agent_sender,
            keywords=config.host_protocol,
        )
```

`_agent_sender` in conversation mode sends through the conversation (used by deliverable acks when `host_protocol=True`):

```python
    async def _agent_sender(message: str) -> None:
        if config.mode == "conversation":
            await conversation.send(message)
            return
        async with injection_lock:
            await host_actions.send_text_to_claude(
                host, tmux_path, tmux_socket, tmux_session, message,
            )
```

- [ ] **Step 5: Teardown variant.** In the `finally`: the tmux teardown block is skipped in conversation mode; instead:

```python
        if config.mode == "conversation" and launched_handle is not None:
            try:
                await host.terminate_subprocess(launched_handle, aggressive=cancelled)
            except Exception:
                _LOG.exception("terminate_subprocess (conversation) failed")
            try:
                await host_actions.await_claude_gone(host, claude_path or "claude")
            except Exception:
                _LOG.exception("await_claude_gone failed; proceeding")
```

Everything after (lease release, save-back, seed capture, snapshot capture gated on `launched_handle is not None`, cleanup, disconnect) is shared and unchanged. The crash-orphan rescue bracket stays as-is (keyed to the tmux socket; finds nothing for conversation tasks).

- [ ] **Step 6: `create_claudecode_task` widget per mode**

```python
        ui_widget=("iframe-input" if config.mode == "iframe" else None),
```

---

### Task 15: claudecode prompt — protocol omission, default instructions, resume paragraph

**Files:**
- Modify: `packages/optio-claudecode/src/optio_claudecode/prompt.py`

- [ ] **Step 1: Add the constant + the System: explainer**

```python
DEFAULT_CONVERSATION_INSTRUCTIONS = "Let's have a conversation with the user."

# Appended to the resume section when the keyword-protocol docs (which
# normally explain the System: convention) are omitted (host_protocol=False).
_SYSTEM_PREFIX_EXPLAINER = """
(Messages prefixed `System:` on your input channel originate from the
harness coordinating this session, not from the human user.)
"""
```

- [ ] **Step 2: Extend `compose_agents_md`**

```python
def compose_agents_md(
    consumer_instructions: str,
    *,
    documentation: str | None = None,
    workdir_exclude: list[str] | None = None,
    supports_resume: bool = True,
    host_protocol: bool = True,
    omit_task_framing: bool = False,
) -> str:
    if host_protocol:
        if documentation is None:
            documentation = build_log_channel_prompt("redirect")
    else:
        documentation = None
    resume_section = _render_resume_section(workdir_exclude) if supports_resume else None
    if resume_section is not None and not host_protocol:
        resume_section = resume_section + _SYSTEM_PREFIX_EXPLAINER
    if omit_task_framing:
        body = consumer_instructions.rstrip()
        pre = ""
        resume_block = (resume_section + "\n") if resume_section else ""
        doc_block = ""
        if documentation:
            from optio_agents.prompt import _INTRO  # same framing as shared composer
            doc_block = _INTRO + documentation + "\n"
        return f"{doc_block}{resume_block}{body}\n"
    return _compose_agents_md_host(
        consumer_instructions,
        documentation=documentation,
        resume_section=resume_section,
    )
```

(Importing `_INTRO` is acceptable here, or duplicate the two-line constant — implementer's choice; keep the rendered text identical to the shared composer's framing.)

`__all__` gains `DEFAULT_CONVERSATION_INSTRUCTIONS`.

---

### Task 16: test fixtures — stream-json fake claude

**Files:**
- Modify: `packages/optio-claudecode/tests/fake_claude.py`
- Modify: `packages/optio-claudecode/tests/claude-shim.sh`

The existing fake simulates the tmux-era claude. Add a stream-json mode, activated when `--input-format` appears in argv (leave all existing behavior untouched for the iframe-mode tests).

- [ ] **Step 1: Add to `fake_claude.py`** — a self-contained loop (stdlib only):

```python
import json
import os
import sys


def run_stream_json_mode(argv: list[str]) -> int:
    """Bidirectional NDJSON fake: one scripted reply per user message.

    Env knobs:
      FAKE_CLAUDE_REPLY          — reply text template; '{n}' = turn number
                                   (default 'reply-{n}')
      FAKE_CLAUDE_PERMISSION     — '1': before the first result, emit a
                                   can_use_tool control_request and wait for
                                   the control_response; the decision is
                                   echoed into the result text.
      FAKE_CLAUDE_EXIT_AFTER     — int: exit(7) after that many results
                                   (simulates unexpected death).
    """
    def emit(obj):
        sys.stdout.write(json.dumps(obj) + "\n")
        sys.stdout.flush()

    reply_tpl = os.environ.get("FAKE_CLAUDE_REPLY", "reply-{n}")
    want_permission = os.environ.get("FAKE_CLAUDE_PERMISSION") == "1"
    exit_after = int(os.environ.get("FAKE_CLAUDE_EXIT_AFTER", "0"))
    session_id = "fake-session-0000"
    emit({"type": "system", "subtype": "init", "session_id": session_id,
          "model": "fake-model", "cwd": os.getcwd()})
    n = 0
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        msg = json.loads(line)
        if msg.get("type") == "control_request":
            sub = (msg.get("request") or {}).get("subtype")
            if sub == "interrupt":
                emit({"type": "control_response", "response": {
                    "subtype": "success", "request_id": msg.get("request_id"),
                }})
                emit({"type": "result", "subtype": "error_during_execution",
                      "result": "", "session_id": session_id, "is_error": True})
                n += 1
            continue
        if msg.get("type") != "user":
            continue
        n += 1
        decision_note = ""
        if want_permission and n == 1:
            emit({"type": "control_request", "request_id": "perm-1",
                  "request": {"subtype": "can_use_tool", "tool_name": "Bash",
                              "input": {"command": "echo hi"}}})
            for resp_line in sys.stdin:
                resp = json.loads(resp_line)
                if resp.get("type") == "control_response":
                    inner = (resp.get("response") or {}).get("response") or {}
                    decision_note = f" perm:{inner.get('behavior')}"
                    break
        text = reply_tpl.format(n=n) + decision_note
        emit({"type": "assistant", "message": {
            "role": "assistant", "content": [{"type": "text", "text": text}],
        }, "session_id": session_id})
        emit({"type": "result", "subtype": "success", "result": text,
              "session_id": session_id, "is_error": False,
              "total_cost_usd": 0.0})
        if exit_after and n >= exit_after:
            return 7
    return 0
```

- [ ] **Step 2: Dispatch on argv** in `fake_claude.py`'s main entry: if `"--input-format"` in argv → `sys.exit(run_stream_json_mode(sys.argv))`. Keep every existing path untouched.

- [ ] **Step 3: `claude-shim.sh`** — verify the shim passes argv through to `fake_claude.py` unmodified (it already resolves its own symlink via `readlink -f`); add no behavior, just confirm `"$@"` forwarding and document the stream-json mode in its header comment.

---

### Task 17: driver unit tests (no Mongo)

**Files:**
- Create: `packages/optio-claudecode/tests/test_conversation_driver.py`

Drive `ClaudeCodeConversation` against a fake handle (in-process pipes), no optio engine. Provide a minimal `FakeHandle` with `stdin` (writer with `write`/`drain`) and `stdout` (async iterator) backed by `asyncio.Queue`s.

- [ ] **Step 1: Write the test file**

```python
"""ClaudeCodeConversation unit tests against an in-process fake handle."""

import asyncio
import json

import pytest

from optio_agents.conversation import ConversationClosed, PermissionDecision
from optio_claudecode.conversation import ClaudeCodeConversation


class _FakeStdin:
    def __init__(self):
        self.lines: asyncio.Queue[dict] = asyncio.Queue()

    def write(self, data: bytes) -> None:
        self.lines.put_nowait(json.loads(data.decode()))

    async def drain(self) -> None:
        pass


class _FakeStdout:
    def __init__(self):
        self.queue: asyncio.Queue[bytes | None] = asyncio.Queue()

    def feed(self, obj: dict) -> None:
        self.queue.put_nowait((json.dumps(obj) + "\n").encode())

    def eof(self) -> None:
        self.queue.put_nowait(None)

    def __aiter__(self):
        return self

    async def __anext__(self):
        item = await self.queue.get()
        if item is None:
            raise StopAsyncIteration
        return item


class _FakeHandle:
    def __init__(self):
        self.stdin = _FakeStdin()
        self.stdout = _FakeStdout()


@pytest.fixture
def convo():
    handle = _FakeHandle()
    c = ClaudeCodeConversation()
    c.attach(handle)
    return c, handle


@pytest.mark.asyncio
async def test_send_writes_user_message_and_pending(convo):
    c, handle = convo
    reader = asyncio.create_task(c.run_reader())
    await c.send("hello")
    sent = await asyncio.wait_for(handle.stdin.lines.get(), 1)
    assert sent["type"] == "user"
    assert sent["message"]["content"][0]["text"] == "hello"
    assert c.is_pending()
    handle.stdout.feed({"type": "result", "subtype": "success",
                        "result": "hi back", "is_error": False})
    await asyncio.sleep(0.05)
    assert not c.is_pending()
    handle.stdout.eof()
    await reader


@pytest.mark.asyncio
async def test_on_event_transparent_and_on_message(convo):
    c, handle = convo
    events, texts = [], []
    c.on_event(events.append)
    c.on_message(texts.append)
    reader = asyncio.create_task(c.run_reader())
    handle.stdout.feed({"type": "system", "subtype": "init", "session_id": "s"})
    handle.stdout.feed({"type": "assistant", "message": {"role": "assistant",
                        "content": [{"type": "text", "text": "partial"}]}})
    handle.stdout.feed({"type": "result", "subtype": "success",
                        "result": "final answer", "is_error": False})
    handle.stdout.eof()
    await reader
    types = [e["type"] for e in events]
    assert types[:3] == ["system", "assistant", "result"]
    assert types[-1] == "x-optio-closed"
    assert texts == ["final answer"]


@pytest.mark.asyncio
async def test_unparseable_line_becomes_synthetic_event(convo):
    c, handle = convo
    events = []
    c.on_event(events.append)
    reader = asyncio.create_task(c.run_reader())
    handle.stdout.queue.put_nowait(b"this is not json\n")
    handle.stdout.eof()
    await reader
    assert events[0]["type"] == "x-optio-unparseable"


@pytest.mark.asyncio
async def test_raising_handler_does_not_kill_dispatch(convo):
    c, handle = convo
    good = []
    c.on_event(lambda e: (_ for _ in ()).throw(RuntimeError("boom")))
    c.on_event(good.append)
    reader = asyncio.create_task(c.run_reader())
    handle.stdout.feed({"type": "system", "subtype": "init"})
    handle.stdout.eof()
    await reader
    assert any(e["type"] == "system" for e in good)


@pytest.mark.asyncio
async def test_permission_roundtrip_and_late_registration(convo):
    c, handle = convo
    reader = asyncio.create_task(c.run_reader())
    handle.stdout.feed({"type": "control_request", "request_id": "perm-1",
                        "request": {"subtype": "can_use_tool",
                                    "tool_name": "Bash",
                                    "input": {"command": "rm -rf /"}}})
    await asyncio.sleep(0.05)  # arrives before any handler → queued

    async def handler(req):
        assert req.tool_name == "Bash"
        return PermissionDecision(behavior="deny", message="nope")

    c.on_permission_request(handler)
    resp = await asyncio.wait_for(handle.stdin.lines.get(), 1)
    assert resp["type"] == "control_response"
    assert resp["response"]["request_id"] == "perm-1"
    assert resp["response"]["response"]["behavior"] == "deny"
    assert resp["response"]["response"]["message"] == "nope"
    handle.stdout.eof()
    await reader


@pytest.mark.asyncio
async def test_interrupt_handshake(convo):
    c, handle = convo
    reader = asyncio.create_task(c.run_reader())
    await c.send("long task")
    intr = asyncio.create_task(c.interrupt())
    sent = await asyncio.wait_for(handle.stdin.lines.get(), 1)   # user msg
    ctrl = await asyncio.wait_for(handle.stdin.lines.get(), 1)   # control_request
    assert ctrl["request"]["subtype"] == "interrupt"
    handle.stdout.feed({"type": "control_response", "response": {
        "subtype": "success", "request_id": ctrl["request_id"]}})
    await asyncio.wait_for(intr, 1)
    handle.stdout.eof()
    await reader


@pytest.mark.asyncio
async def test_send_after_close_raises(convo):
    c, handle = convo
    reader = asyncio.create_task(c.run_reader())
    handle.stdout.eof()
    await reader
    assert c.closed
    with pytest.raises(ConversationClosed):
        await c.send("too late")


@pytest.mark.asyncio
async def test_close_sets_close_requested(convo):
    c, handle = convo
    await c.close()
    assert c.close_requested.is_set()
```

---

### Task 18: session-level + config/prompt tests

**Files:**
- Create: `packages/optio-claudecode/tests/test_conversation_session.py`
- Create: `packages/optio-claudecode/tests/test_conversation_config.py`

- [ ] **Step 1: `test_conversation_config.py`** — pure-unit validation + prompt matrix:

```python
"""Config validation + prompt composition for conversation mode."""

import pytest

from optio_claudecode.prompt import (
    DEFAULT_CONVERSATION_INSTRUCTIONS, compose_agents_md,
)
from optio_claudecode.types import ClaudeCodeTaskConfig


def _cfg(**kw):
    base = dict(consumer_instructions="do things")
    base.update(kw)
    return ClaudeCodeTaskConfig(**base)


def test_defaults_backcompat():
    cfg = _cfg()
    assert cfg.mode == "iframe"
    assert cfg.host_protocol is True
    assert cfg.permission_gate is False


def test_iframe_requires_host_protocol():
    with pytest.raises(ValueError):
        _cfg(host_protocol=False)


def test_permission_gate_requires_conversation_mode():
    with pytest.raises(ValueError):
        _cfg(permission_gate=True)


def test_conversation_requires_noninteractive_permissions():
    with pytest.raises(ValueError):
        _cfg(mode="conversation")  # permission_mode=None, no gate
    _cfg(mode="conversation", permission_mode="bypassPermissions")
    _cfg(mode="conversation", permission_mode="acceptEdits")
    _cfg(mode="conversation", allowed_tools=["Read"])
    _cfg(mode="conversation", permission_gate=True)  # gate replaces the rule


def test_dontask_is_valid_permission_mode():
    _cfg(mode="conversation", permission_mode="dontAsk")


def test_prompt_with_host_protocol_off_omits_keyword_docs():
    text = compose_agents_md(
        "instructions", workdir_exclude=None, supports_resume=True,
        host_protocol=False,
    )
    assert "Log channel" not in text
    assert "optio.log" not in text
    assert "resume.log" in text                      # resume section stays
    assert "originate from the harness" in text      # System: explainer added


def test_prompt_default_instructions_and_framing_omission():
    text = compose_agents_md(
        DEFAULT_CONVERSATION_INSTRUCTIONS,
        workdir_exclude=None, supports_resume=True,
        host_protocol=False, omit_task_framing=True,
    )
    assert DEFAULT_CONVERSATION_INSTRUCTIONS in text
    assert "## Task" not in text


def test_prompt_unchanged_for_iframe_path():
    """Regression: default-args output identical to the pre-change renderer."""
    text = compose_agents_md("instructions", workdir_exclude=None)
    assert "Log channel" in text
    assert "## Task" in text
```

- [ ] **Step 2: `test_conversation_session.py`** — full session via the existing integration pattern: monkeypatch `optio_claudecode.session._build_host` exactly as the existing `test_session_local.py` does, point the claude shim at the stream-json fake (Task 16), run a conversation-mode task through real optio-core (Mongo via Docker). Cover, using the conventions of `test_session_local.py` / `tests/conftest.py`:

```python
"""End-to-end conversation-mode session tests (local host, fake claude).

Each test follows test_session_local.py's bootstrap: real optio engine
(Mongo via Docker), _build_host monkeypatched to a LocalHost whose PATH
serves claude-shim.sh as `claude`, fake ttyd not needed (no ttyd in this
mode).

Covered scenarios:
  1. launch_and_await_result returns a live Conversation; send()/on_message()
     round-trip against the fake; is_pending flips around the turn.
  2. close() → task reaches 'done'; subprocess terminated; snapshot capture
     ran (hasSavedState set) when supports_resume=True.
  3. fake exits unexpectedly (FAKE_CLAUDE_EXIT_AFTER=1) → task 'failed',
     error message contains 'exited unexpectedly'; conversation.closed.
  4. auto_start=True → first stdin message is the kickoff prompt (assert via
     FAKE_CLAUDE_REPLY echo / captured stdin in fake's reply text).
  5. host_protocol=False → CLAUDE.md on disk lacks 'Log channel'; task still
     completes via close().
  6. ui_widget is None on the TaskInstance returned by create_claudecode_task
     for conversation mode, 'iframe-input' for iframe mode (pure unit assert).
"""
```

Write all six as real tests; reuse the launch/poll helpers the neighbouring session tests use. (The exact fixture names live in `tests/conftest.py` — mirror them; do not invent a new bootstrap.)

---

### Task 19: docs

**Files:**
- Modify: `packages/optio-claudecode/AGENTS.md`
- Modify: `packages/optio-claudecode/README.md`
- Modify: `packages/optio-agents/README.md`

- [ ] **Step 1: optio-claudecode AGENTS.md** — add a "Conversation mode" section: the three new config fields with semantics, the `Conversation` surface (send/on_event/on_message/on_permission_request/is_pending/interrupt/close), publish/await usage snippet:

```python
conv = await optio.launch_and_await_result("my-task", session_id=None)
conv.on_message(lambda text: print("claude:", text))
await conv.send("hello")
...
await conv.close()
```

plus the completion-semantics table from the spec (§3.4) and a pointer to the design doc.

- [ ] **Step 2: optio-claudecode README.md** — short "Conversation mode" subsection (what it is, 10-line example, defaults unchanged note).

- [ ] **Step 3: optio-agents README.md** — mention the `Conversation` Protocol (one paragraph: semantic surface, backend-specific transparent events, implemented by optio-claudecode).

---

## Wave 2 — sequential verification (main loop)

### Task V1: live CLI fact-checks (spec §8)

- [ ] **Step 1: permission-prompt flag spelling**

Run: `claude -p --help 2>&1 | grep -iA2 'permission'`
Confirm: the stdio permission-prompt-tool variant's exact spelling, and whether `dontAsk` is among `--permission-mode` choices. Update `build_conversation_argv` (Task 13) and `_HEADLESS_SAFE_PERMISSION_MODES` / Literal (Task 11) if reality differs.

- [ ] **Step 2: control protocol schema** — run a tiny live probe (haiku model) with the gate flag on and a Bash-requiring prompt under `permission_mode=default`; capture one real `control_request` line; align `conversation.py`'s request/response field names if they differ from the plan's assumption.

- [ ] **Step 3: queueing + interrupt semantics** — extend `/tmp/test_streamjson.py` style probe: send msg2 while turn 1 is still running (expect native queueing, two `result`s); send an interrupt mid-turn and observe the ack + result shape. Document findings as comments in `conversation.py` where relevant.

### Task V2: full test pass

- [ ] **Step 1:** `cd packages/optio-core && python -m pytest tests/ -q` — expect all green (incl. new `test_publish_result.py`).
- [ ] **Step 2:** `cd packages/optio-agents && python -m pytest tests/ -q` — green (existing protocol tests confirm `keywords=True` default unchanged).
- [ ] **Step 3:** `cd packages/optio-opencode && python -m pytest tests/test_prompt.py -q` — guards the shared-composer byte-compat (Task 10).
- [ ] **Step 4:** `cd packages/optio-claudecode && python -m pytest tests/ -q` — green, incl. all pre-existing iframe-mode tests (backward-compat gate) and the new conversation tests.
- [ ] **Step 5:** Fix whatever Wave 1 left broken (imports, signatures, fixture names). Small fixes inline; if a contract mismatch is found, fix to the Shared Contracts section above.

### Task V3: commits

- [ ] **Step 1:**
```bash
git add packages/optio-core
git commit -m "feat(optio-core): publish_result / launch_and_await_result return channel"
```
- [ ] **Step 2:**
```bash
git add packages/optio-agents
git commit -m "feat(optio-agents): Conversation protocol + scaffolding-only driver mode"
```
- [ ] **Step 3:**
```bash
git add packages/optio-claudecode docs/
git commit -m "feat(claudecode): conversation mode over stream-json stdio"
```
- [ ] **Step 4:** Hand off to `superpowers:finishing-a-development-branch`.

---

## Self-review notes (already applied)

- Spec coverage: §1→Tasks 1-6, §2.1→7-8, §2.2→9, §3.1→11, §3.2/3.4→14, §3.3→12, §3.5/3.6→12, §3.7→10+15, §7→6,16-18,V1-V2, §8→V1. No gaps; Phase II is explicitly out of scope.
- Every file owned by exactly one task; `optio-agents/prompt.py` (Task 10) and `claudecode/prompt.py` (Task 15) are distinct files; `session.py` consolidated in Task 14; test fixtures pair in Task 16.
- Verification fully deferred to V1-V2; agents run no git/tests in Wave 1.
