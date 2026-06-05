# Agent Feedback Channel Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a best-effort engine→agent message channel (`hook_ctx.send_to_agent`), a `System:`-prefixed framing, return-value routing for `on_deliverable`, a resume notification over that channel, the agent-facing protocol docs for it, and an in-repo demo that exercises the round-trip on both backends.

**Architecture:** A single imperative method on `HookContext`, backed by an optional sender closure each backend injects into `run_log_protocol_session`. opencode wires the sender over its HTTP `prompt_async`; claudecode over tmux fake-typing. The deliverable loop also routes a returned string through the same method. A `System:` prefix marks all engine-originated input; the protocol documentation teaches the agent about the inbound channel. Resume adds a push notification (`System: you have been resumed`) at each backend's proven launch-time inject point while keeping `resume.log`.

**Tech Stack:** Python 3, asyncio, pytest (+ pytest-asyncio), the optio monorepo packages `optio-agents`, `optio-opencode`, `optio-claudecode`, `optio-demo`.

**Spec:** `docs/2026-06-05-agent-feedback-channel-design.md` (base + Addenda 1–5, committed `dc703f4`).

---

## Parallel-shaping (read before executing)

This plan is **parallel-shaped**. Honor it:

- **Every source/test file is owned by exactly one task.** No two tasks edit the same file. Tasks 1–12 are file-disjoint and run **concurrently**.
- **Do NOT run tests inside Tasks 1–12.** All verification (pytest, grep) is deferred to **Task 13**. Accept that the tree will not import-resolve mid-execution (e.g. Task 5 imports `SYSTEM_MESSAGE_PREFIX` that Task 1 defines) — Task 13 catches everything once the bulk has landed.
- Each task ends by committing **only its own files**.
- Execution happens on a feature branch (see Execution Handoff), ideally one worktree per concurrent agent.

**Shared contract pinned across tasks (use these exact names/strings):**
- `SYSTEM_MESSAGE_PREFIX = "System: "` — defined in `optio_agents/context.py`, re-exported from `optio_agents`.
- `RESUME_NOTICE = "you have been resumed"` — defined in `optio_agents/protocol/prompt.py`, re-exported from `optio_agents` and `optio_agents.protocol`.
- `AgentSender = Callable[[str], Awaitable[None]]` — defined in `optio_agents/protocol/session.py`, re-exported from `optio_agents` and `optio_agents.protocol`.
- `DeliverableCallback = Callable[["HookContext", str, str], Awaitable["str | None"]]` (return type widened).
- Demo marker: deliverable text must end with `over and out`; nudge cap = 2.

---

## File Structure

| File | Task | Responsibility |
|------|------|----------------|
| `optio-agents/src/optio_agents/context.py` | T1 | `SYSTEM_MESSAGE_PREFIX`, `_agent_sender` slot, `send_to_agent`, protocol sig |
| `optio-agents/src/optio_agents/protocol/session.py` | T2 | `AgentSender`, widened `DeliverableCallback`, `agent_sender` param + attach, deliverable-loop routing |
| `optio-agents/src/optio_agents/protocol/prompt.py` | T3 | `RESUME_NOTICE`, `_FEEDBACK` doc block |
| `optio-agents/src/optio_agents/__init__.py` + `.../protocol/__init__.py` | T4 | export the new symbols |
| `optio-opencode/src/optio_opencode/session.py` | T5 | wire `agent_sender`; resume-notify |
| `optio-opencode/src/optio_opencode/prompt.py` | T6 | resume-section `System:` sentence |
| `optio-claudecode/src/optio_claudecode/host_actions.py` | T7 | `send_text_to_claude`, `build_resume_notice_args` |
| `optio-claudecode/src/optio_claudecode/session.py` | T8 | wire `agent_sender`; append resume-notice arg |
| `optio-claudecode/src/optio_claudecode/prompt.py` | T9 | resume-section `System:` sentence |
| `optio-demo/src/optio_demo/tasks/_feedback.py` | T10 | shared feedback-on-deliverable helper |
| `optio-demo/src/optio_demo/tasks/opencode.py` | T11 | rewire `_on_deliverable` to the helper |
| `optio-demo/src/optio_demo/tasks/claudecode.py` | T12 | rewire `_on_deliverable` to the helper |
| (all tests run) | T13 | verification |

New test files (each owned by its task, all distinct):
`test_send_to_agent.py` (T1), `test_deliverable_routing.py` (T2), `test_prompt_feedback.py` (T3), `test_exports_feedback.py` (T4), `test_agent_sender_opencode.py` (T5), `test_resume_sentence_opencode.py` (T6), `test_send_text_to_claude.py` (T7), `test_agent_sender_claudecode.py` (T8), `test_resume_sentence_claudecode.py` (T9), `test_feedback_helper.py` (T10).

---

### Task 1: `optio-agents` — `HookContext.send_to_agent` + `System:` prefix

**Files:**
- Modify: `packages/optio-agents/src/optio_agents/context.py`
- Test: `packages/optio-agents/tests/test_send_to_agent.py`

- [ ] **Step 1: Add the constant + `_agent_sender` slot + `send_to_agent` method.**

In `context.py`, after the module imports (after line 10), add the constant:

```python
# Prefix on every harness→agent message pushed through send_to_agent. Marks
# input that originates from the engine/hooks rather than a real user.
SYSTEM_MESSAGE_PREFIX = "System: "
```

In `HookContext.__init__`, after the `browser_launch_env` line (line 29), add:

```python
        # Optional best-effort engine→agent sender, injected by
        # run_log_protocol_session from the backend's transport. None when no
        # channel is wired; send_to_agent then returns False.
        object.__setattr__(self, "_agent_sender", None)
```

Add the method (place it right after `__getattr__`, before `run_on_host`):

```python
    async def send_to_agent(self, message: str) -> bool:
        """Best-effort: push a message into the live agent session. Returns
        True if delivered, False if no channel is wired or the send failed.
        A dead/unreachable agent must never crash a hook. The message is
        prefixed with SYSTEM_MESSAGE_PREFIX so the agent can tell harness
        input from a real user turn."""
        sender = self._agent_sender
        if sender is None:
            self._ctx.report_progress(None, "send_to_agent: no channel for this agent")
            return False
        try:
            await sender(f"{SYSTEM_MESSAGE_PREFIX}{message}")
            return True
        except Exception as e:  # noqa: BLE001
            self._ctx.report_progress(None, f"send_to_agent failed: {e!r}")
            return False
```

- [ ] **Step 2: Add the method signature to `HookContextProtocol`.**

In `HookContextProtocol` (after the `download_file` signature, around line 220), add:

```python
    async def send_to_agent(self, message: str) -> bool: ...
```

- [ ] **Step 3: Write the test file** `packages/optio-agents/tests/test_send_to_agent.py`:

```python
import pytest

from optio_agents.context import HookContext, SYSTEM_MESSAGE_PREFIX


class _Ctx:
    def __init__(self):
        self.msgs = []

    def report_progress(self, percent, message=None):
        self.msgs.append(message)


@pytest.mark.asyncio
async def test_no_sender_returns_false_and_logs():
    hc = HookContext(_Ctx(), object())
    assert await hc.send_to_agent("hi") is False
    assert any("no channel" in (m or "") for m in hc._ctx.msgs)


@pytest.mark.asyncio
async def test_sender_success_prefixes_and_returns_true():
    sent = []

    async def sender(m):
        sent.append(m)

    hc = HookContext(_Ctx(), object())
    hc._agent_sender = sender
    assert await hc.send_to_agent("hi") is True
    assert sent == [f"{SYSTEM_MESSAGE_PREFIX}hi"]


@pytest.mark.asyncio
async def test_sender_raise_returns_false_and_logs():
    async def sender(m):
        raise RuntimeError("dead")

    hc = HookContext(_Ctx(), object())
    hc._agent_sender = sender
    assert await hc.send_to_agent("hi") is False
    assert any("send_to_agent failed" in (m or "") for m in hc._ctx.msgs)
```

- [ ] **Step 4: Commit (do NOT run tests — Task 13 verifies).**

```bash
git add packages/optio-agents/src/optio_agents/context.py \
        packages/optio-agents/tests/test_send_to_agent.py
git commit -m "feat(agents): add HookContext.send_to_agent + System: prefix"
```

---

### Task 2: `optio-agents` — `AgentSender`, return-value routing, `agent_sender` wiring

**Files:**
- Modify: `packages/optio-agents/src/optio_agents/protocol/session.py`
- Test: `packages/optio-agents/tests/test_deliverable_routing.py`

- [ ] **Step 1: Widen `DeliverableCallback` and add `AgentSender`.**

Replace the `DeliverableCallback` alias (lines 55–64) — change its return type to `Awaitable["str | None"]` and update the docstring:

```python
DeliverableCallback = Callable[["HookContext", str, str], Awaitable["str | None"]]
"""Consumer callback invoked per fetched DELIVERABLE.

Arguments: ``(hook_ctx, deliverable_path, decoded_text)``.

May return a non-empty string to send back to the running agent (the
deliverable loop routes it through ``hook_ctx.send_to_agent``); return
``None`` or ``""`` to send nothing. A hook may also call
``hook_ctx.send_to_agent(...)`` directly instead of / in addition to
returning.

``deliverable_path`` is the path of the deliverable file relative to
``<workdir>/deliverables/`` (e.g. ``"summary.md"`` or
``"sub/summary.md"``). It matches the value emitted in the
auto-generated ``"Deliverable: <path>"`` progress message.
"""
```

After the `HookCallback` alias (after line 69), add:

```python
AgentSender = Callable[[str], Awaitable[None]]
"""Backend transport that pushes one message into the live agent session.

Raises on transport failure (worker down / tmux session gone / non-zero
exit); ``HookContext.send_to_agent`` catches that and returns False."""
```

- [ ] **Step 2: Add the `agent_sender` parameter and attach it.**

In the `run_log_protocol_session` signature, add a parameter after `browser_url_rewrite` (line 102):

```python
    agent_sender: "AgentSender | None" = None,
```

Right after `hook_ctx = HookContext(ctx, host)` (line 141), add:

```python
    hook_ctx._agent_sender = agent_sender
```

- [ ] **Step 3: Route the deliverable callback's return value.**

In `_deliverable_fetch_loop`, replace the callback invocation block (lines 330–337):

```python
            if callback is None:
                continue
            try:
                await callback(hook_ctx, display, text)
            except Exception as exc:  # noqa: BLE001
                ctx.report_progress(
                    None, f"on_deliverable callback raised: {exc!r}",
                )
```

with:

```python
            if callback is None:
                continue
            try:
                feedback = await callback(hook_ctx, display, text)
            except Exception as exc:  # noqa: BLE001
                ctx.report_progress(
                    None, f"on_deliverable callback raised: {exc!r}",
                )
            else:
                if isinstance(feedback, str) and feedback.strip():
                    await hook_ctx.send_to_agent(feedback)
```

- [ ] **Step 4: Write the test file** `packages/optio-agents/tests/test_deliverable_routing.py`:

```python
import asyncio

import pytest

from optio_agents.context import HookContext
from optio_agents.protocol import session as S


class _Host:
    async def fetch_bytes_from_host(self, path):
        return b"deliverable body"


class _Ctx:
    def report_progress(self, percent, message=None):
        pass


async def _drive(callback):
    hc = HookContext(_Ctx(), _Host())
    sent = []

    async def spy(msg):
        sent.append(msg)
        return True

    hc.send_to_agent = spy  # shadow the bound method with a spy
    q: asyncio.Queue = asyncio.Queue()
    await q.put(("/abs/x.md", "x.md"))
    task = asyncio.create_task(
        S._deliverable_fetch_loop(_Host(), callback, q, _Ctx(), hc)
    )
    await q.join()
    task.cancel()
    return sent


@pytest.mark.asyncio
async def test_returned_string_is_routed_to_send():
    async def cb(hook_ctx, display, text):
        return "reject: missing header"

    assert await _drive(cb) == ["reject: missing header"]


@pytest.mark.asyncio
async def test_none_return_sends_nothing():
    async def cb(hook_ctx, display, text):
        return None

    assert await _drive(cb) == []


@pytest.mark.asyncio
async def test_empty_return_sends_nothing():
    async def cb(hook_ctx, display, text):
        return "   "

    assert await _drive(cb) == []
```

> Note (scoping, not a placeholder): the `agent_sender`-attach line is exercised end-to-end by the backend integration tests (Tasks 5 and 8, which drive real local sessions through the existing shims). This task unit-tests the routing logic directly via `_deliverable_fetch_loop`.

- [ ] **Step 5: Commit.**

```bash
git add packages/optio-agents/src/optio_agents/protocol/session.py \
        packages/optio-agents/tests/test_deliverable_routing.py
git commit -m "feat(agents): route on_deliverable return value + AgentSender wiring"
```

---

### Task 3: `optio-agents` — protocol documentation for the inbound channel

**Files:**
- Modify: `packages/optio-agents/src/optio_agents/protocol/prompt.py`
- Test: `packages/optio-agents/tests/test_prompt_feedback.py`

- [ ] **Step 1: Add `RESUME_NOTICE` and the `_FEEDBACK` block.**

In `prompt.py`, after the `BrowserMode` import (after line 15), add:

```python
# The push notification a backend sends a resumed agent over the channel.
# Shared so the doc prose and both backends agree on one string.
RESUME_NOTICE = "you have been resumed"
```

After the `_RULES` constant (after line 69), add:

```python
_FEEDBACK = """
## Messages from the harness

After you emit a deliverable, the harness may send you a message on the
same input channel where the user normally talks — that channel carries
both genuine user input and harness messages. Every harness message is
prefixed `System:`. Treat a `System:` message as an instruction. In
particular, if it tells you a delivered artifact was rejected, revise the
artifact and emit the `DELIVERABLE:` line again. You may also be notified
of a resume by a `System:` message; when you see one, follow the
`resume.log` procedure described elsewhere.
"""
```

Change the `build_log_channel_prompt` return (line 76) to append `_FEEDBACK`:

```python
    return _HEADER + browser_bullet + _TAIL_BULLETS + suppress_note + _RULES + _FEEDBACK
```

- [ ] **Step 2: Write the test file** `packages/optio-agents/tests/test_prompt_feedback.py`:

```python
from optio_agents.protocol.prompt import RESUME_NOTICE, build_log_channel_prompt


def test_resume_notice_is_nonempty_str():
    assert isinstance(RESUME_NOTICE, str) and RESUME_NOTICE.strip()


def test_feedback_block_present_in_every_mode():
    for mode in ("ignore", "redirect", "suppress"):
        doc = build_log_channel_prompt(mode)
        assert "System:" in doc
        assert "input channel" in doc.lower()
        assert "Messages from the harness" in doc
```

- [ ] **Step 3: Commit.**

```bash
git add packages/optio-agents/src/optio_agents/protocol/prompt.py \
        packages/optio-agents/tests/test_prompt_feedback.py
git commit -m "feat(agents): document the inbound System: channel + RESUME_NOTICE"
```

---

### Task 4: `optio-agents` — export the new symbols

**Files:**
- Modify: `packages/optio-agents/src/optio_agents/protocol/__init__.py`
- Modify: `packages/optio-agents/src/optio_agents/__init__.py`
- Test: `packages/optio-agents/tests/test_exports_feedback.py`

- [ ] **Step 1: Export from `protocol/__init__.py`.**

In the `from optio_agents.protocol.session import (...)` block (lines 21–27), add `AgentSender,`. In the `from optio_agents.protocol.prompt import ...` line (line 29), add `RESUME_NOTICE`:

```python
from optio_agents.protocol.session import (
    DELIVERABLE_QUEUE_BOUND,
    AgentSender,
    DeliverableCallback,
    HookCallback,
    fetch_deliverable_text,
    run_log_protocol_session,
)
...
from optio_agents.protocol.prompt import RESUME_NOTICE, build_log_channel_prompt
```

In `__all__`, add `"AgentSender",` (near `"DeliverableCallback"`) and `"RESUME_NOTICE",` (near `"build_log_channel_prompt"`).

- [ ] **Step 2: Export from the package root `__init__.py`.**

In `from optio_agents.context import HookContext, HookContextProtocol` (line 8), add `SYSTEM_MESSAGE_PREFIX`:

```python
from optio_agents.context import HookContext, HookContextProtocol, SYSTEM_MESSAGE_PREFIX
```

In the `from optio_agents.protocol import (...)` block (lines 9–28), add `AgentSender,` and `RESUME_NOTICE,`. In `__all__`, add `"SYSTEM_MESSAGE_PREFIX"`, `"AgentSender"`, `"RESUME_NOTICE"`.

- [ ] **Step 3: Write the test file** `packages/optio-agents/tests/test_exports_feedback.py`:

```python
def test_root_exports():
    from optio_agents import (  # noqa: F401
        AgentSender,
        RESUME_NOTICE,
        SYSTEM_MESSAGE_PREFIX,
    )

    assert SYSTEM_MESSAGE_PREFIX == "System: "
    assert RESUME_NOTICE == "you have been resumed"


def test_protocol_exports():
    from optio_agents.protocol import AgentSender, RESUME_NOTICE  # noqa: F401
```

- [ ] **Step 4: Commit.**

```bash
git add packages/optio-agents/src/optio_agents/__init__.py \
        packages/optio-agents/src/optio_agents/protocol/__init__.py \
        packages/optio-agents/tests/test_exports_feedback.py
git commit -m "feat(agents): export AgentSender, SYSTEM_MESSAGE_PREFIX, RESUME_NOTICE"
```

---

### Task 5: `optio-opencode` — wire the sender + resume notification

**Files:**
- Modify: `packages/optio-opencode/src/optio_opencode/session.py`
- Test: `packages/optio-opencode/tests/test_agent_sender_opencode.py`

- [ ] **Step 1: Import the constants.**

In the import block where `optio_agents` symbols are imported, add:

```python
from optio_agents import RESUME_NOTICE, SYSTEM_MESSAGE_PREFIX
```

- [ ] **Step 2: Resume notification at the auto-start site.**

Replace the auto-start block (lines 326–329):

```python
        if config.auto_start and not resuming:
            await _post_opencode_prompt(
                worker_port, password, session_id, AUTO_START_PROMPT,
            )
```

with:

```python
        if config.auto_start and not resuming:
            await _post_opencode_prompt(
                worker_port, password, session_id, AUTO_START_PROMPT,
            )
        elif resuming and config.supports_resume:
            # Push notification: make the resumed agent NOTICE the resume
            # promptly (resume.log remains the pull-based source of truth).
            await _post_opencode_prompt(
                worker_port, password, session_id,
                f"{SYSTEM_MESSAGE_PREFIX}{RESUME_NOTICE}",
            )
```

- [ ] **Step 3: Wire `agent_sender` into the protocol session.**

`worker_port` and `session_id` are `nonlocal` in `_opencode_body` (lines 173–174) and `password` is established at function scope; the closure resolves their values at send time (after launch). Immediately before the `run_log_protocol_session(...)` call (line 352), define the sender:

```python
        async def _agent_sender(message: str) -> None:
            # worker_port / session_id are set by _opencode_body at launch;
            # password is established at function scope. _post_opencode_prompt
            # raises on a non-2xx / unreachable worker, which
            # send_to_agent converts to False.
            await _post_opencode_prompt(worker_port, password, session_id, message)
```

Add `agent_sender=_agent_sender,` to the call:

```python
        await run_log_protocol_session(
            host, ctx,
            body=_opencode_body,
            on_deliverable=config.on_deliverable,
            after_execute=config.after_execute,
            protocol=protocol,
            agent_sender=_agent_sender,
        )
```

(Indentation: this `_agent_sender` sits inside `run_opencode_session`, at the same indent level as the `try:` that wraps the call — i.e. it is defined just above that `try:`.)

- [ ] **Step 4: Write the test file** `packages/optio-opencode/tests/test_agent_sender_opencode.py`:

```python
import pytest

import optio_opencode.session as S
from optio_agents import RESUME_NOTICE, SYSTEM_MESSAGE_PREFIX


@pytest.mark.asyncio
async def test_post_prompt_signature_used_by_sender(monkeypatch):
    """The opencode sender forwards (port, password, session_id, message) to
    _post_opencode_prompt verbatim. Mirrors the closure built in
    run_opencode_session."""
    calls = []

    async def fake_post(port, password, session_id, message):
        calls.append((port, password, session_id, message))

    monkeypatch.setattr(S, "_post_opencode_prompt", fake_post)

    worker_port, password, session_id = 4321, "pw", "sess-1"

    async def _agent_sender(message: str) -> None:
        await S._post_opencode_prompt(worker_port, password, session_id, message)

    await _agent_sender("hello")
    assert calls == [(4321, "pw", "sess-1", "hello")]


def test_resume_notice_string():
    assert f"{SYSTEM_MESSAGE_PREFIX}{RESUME_NOTICE}" == "System: you have been resumed"
```

> Note: the live wiring (`agent_sender=` passed, the resume `elif`) is additionally covered by the existing local-session integration suite in Task 13.

- [ ] **Step 5: Commit.**

```bash
git add packages/optio-opencode/src/optio_opencode/session.py \
        packages/optio-opencode/tests/test_agent_sender_opencode.py
git commit -m "feat(opencode): wire send_to_agent transport + resume notification"
```

---

### Task 6: `optio-opencode` — resume-section `System:` sentence

**Files:**
- Modify: `packages/optio-opencode/src/optio_opencode/prompt.py`
- Test: `packages/optio-opencode/tests/test_resume_sentence_opencode.py`

- [ ] **Step 1: Add the sentence to `RESUME_SECTION_TEMPLATE`.**

In `prompt.py`, replace the closing paragraph of the resume section (lines 82–83):

```python
If a resume slips past unnoticed, a failing tool call is the
next-best signal — re-check `./resume.log` then.
"""
```

with:

```python
If a resume slips past unnoticed, a failing tool call is the
next-best signal — re-check `./resume.log` then.

You may also be notified of a resume by a `System:` message on your input
channel; when you see one, follow the `resume.log` procedure above.
"""
```

- [ ] **Step 2: Write the test file** `packages/optio-opencode/tests/test_resume_sentence_opencode.py`:

```python
from optio_opencode.prompt import _render_resume_section


def test_resume_section_mentions_system_notification():
    section = _render_resume_section(None)
    assert "`System:` message" in section
    assert "resume.log" in section
```

- [ ] **Step 3: Commit.**

```bash
git add packages/optio-opencode/src/optio_opencode/prompt.py \
        packages/optio-opencode/tests/test_resume_sentence_opencode.py
git commit -m "docs(opencode): note the System: resume notification in resume section"
```

---

### Task 7: `optio-claudecode` — `send_text_to_claude` + `build_resume_notice_args`

**Files:**
- Modify: `packages/optio-claudecode/src/optio_claudecode/host_actions.py`
- Test: `packages/optio-claudecode/tests/test_send_text_to_claude.py`

- [ ] **Step 1: Import the constants (runtime).**

In `host_actions.py`, after the existing imports (after line 15), add:

```python
from optio_agents import RESUME_NOTICE, SYSTEM_MESSAGE_PREFIX
```

- [ ] **Step 2: Add `send_text_to_claude`.**

Add this function near the other tmux helpers (after `build_auto_start_args`, around line 646):

```python
async def send_text_to_claude(
    host: "Host", tmux_path: str, tmux_socket: str, tmux_session: str, text: str,
) -> None:
    """Fake-type a message into the claude TUI and submit it.

    Uses ``set-buffer`` + ``paste-buffer`` (robust for arbitrary text incl.
    spaces, which ``send-keys -l`` would mistreat) then a single ``Enter``.
    Raises on a tmux failure (the caller treats that as 'agent unreachable',
    which ``send_to_agent`` converts to False). Verified manually against a
    live claude TUI (see the spec's Part C)."""
    s = shlex.quote(tmux_socket)
    sess = shlex.quote(tmux_session)
    tp = shlex.quote(tmux_path)
    buf = "optio-feedback"
    cmd = (
        f"{tp} -S {s} set-buffer -b {buf} -- {shlex.quote(text)} && "
        f"{tp} -S {s} paste-buffer -d -b {buf} -t {sess} && "
        f"{tp} -S {s} send-keys -t {sess} Enter"
    )
    result = await host.run_command(cmd)
    if result.exit_code != 0:
        raise RuntimeError(
            f"send_text_to_claude: tmux injection failed "
            f"(exit {result.exit_code}): {result.stderr!r}"
        )
```

- [ ] **Step 3: Add `build_resume_notice_args`.**

Add right after `build_auto_start_args` (so it sits next to its sibling):

```python
def build_resume_notice_args(*, resuming: bool, pass_continue: bool) -> list[str]:
    """Trailing positional prompt that notifies a resumed claude session.

    Returns ``[f"{SYSTEM_MESSAGE_PREFIX}{RESUME_NOTICE}"]`` ONLY when the
    session is both resuming AND continuing a transcript (``pass_continue``).
    The positional only *appends* to the restored conversation when claude is
    launched with ``--continue`` (verified: ``claude --continue '<text>'``
    resumes and processes the text as a new turn). On a no-transcript resume
    there is nothing to append to, so no notice is sent. Empty otherwise."""
    if resuming and pass_continue:
        return [f"{SYSTEM_MESSAGE_PREFIX}{RESUME_NOTICE}"]
    return []
```

- [ ] **Step 4: Write the test file** `packages/optio-claudecode/tests/test_send_text_to_claude.py`:

```python
import pytest

from optio_agents import RESUME_NOTICE, SYSTEM_MESSAGE_PREFIX
from optio_claudecode.host_actions import (
    build_resume_notice_args,
    send_text_to_claude,
)


class _Result:
    exit_code = 0
    stdout = ""
    stderr = ""


class _Host:
    def __init__(self):
        self.commands = []

    async def run_command(self, cmd, **kwargs):
        self.commands.append(cmd)
        return _Result()


class _FailHost:
    async def run_command(self, cmd, **kwargs):
        r = _Result()
        r.exit_code = 1
        r.stderr = "no server"
        return r


@pytest.mark.asyncio
async def test_send_text_sequence_and_quoting():
    host = _Host()
    await send_text_to_claude(host, "/usr/bin/tmux", "/tmp/sock", "optio", 'hi "there"')
    cmd = host.commands[0]
    assert "/usr/bin/tmux -S /tmp/sock set-buffer -b optio-feedback -- " in cmd
    assert "paste-buffer -d -b optio-feedback -t optio" in cmd
    assert cmd.rstrip().endswith("send-keys -t optio Enter")
    # the message is shell-quoted (contains the embedded double-quote intact)
    assert '\'hi "there"\'' in cmd


@pytest.mark.asyncio
async def test_send_text_raises_on_nonzero_exit():
    with pytest.raises(RuntimeError):
        await send_text_to_claude(_FailHost(), "tmux", "/tmp/s", "optio", "x")


def test_resume_notice_args_gating():
    expect = [f"{SYSTEM_MESSAGE_PREFIX}{RESUME_NOTICE}"]
    assert build_resume_notice_args(resuming=True, pass_continue=True) == expect
    assert build_resume_notice_args(resuming=True, pass_continue=False) == []
    assert build_resume_notice_args(resuming=False, pass_continue=False) == []
    assert build_resume_notice_args(resuming=False, pass_continue=True) == []
```

- [ ] **Step 5: Commit.**

```bash
git add packages/optio-claudecode/src/optio_claudecode/host_actions.py \
        packages/optio-claudecode/tests/test_send_text_to_claude.py
git commit -m "feat(claudecode): add send_text_to_claude + build_resume_notice_args"
```

---

### Task 8: `optio-claudecode` — wire the sender + append resume-notice arg

**Files:**
- Modify: `packages/optio-claudecode/src/optio_claudecode/session.py`
- Test: `packages/optio-claudecode/tests/test_agent_sender_claudecode.py`

- [ ] **Step 1: Append the resume-notice positional to `claude_flags`.**

Right after the `build_auto_start_args` block (lines 227–232), add a second extension:

```python
        # Resume notification: a System:-prefixed positional appended after
        # --continue, so the restored conversation gets a "you have been
        # resumed" turn. Gated on pass_continue (a transcript is actually
        # being continued); a no-transcript resume sends nothing.
        claude_flags = [
            *claude_flags,
            *host_actions.build_resume_notice_args(
                resuming=resuming, pass_continue=pass_continue,
            ),
        ]
```

- [ ] **Step 2: Wire `agent_sender` into the protocol session.**

`tmux_path`, `tmux_socket`, `tmux_session` are `nonlocal` in `_claudecode_body` (line 160) and set at launch; `host` is the outer parameter. Immediately before the `run_log_protocol_session(...)` call (line 270), define the sender:

```python
    async def _agent_sender(message: str) -> None:
        # tmux_* are set by _claudecode_body at launch; host is the session's
        # Host. send_text_to_claude raises on a tmux failure, which
        # send_to_agent converts to False.
        await host_actions.send_text_to_claude(
            host, tmux_path, tmux_socket, tmux_session, message,
        )
```

Add `agent_sender=_agent_sender,` to the call:

```python
        await run_log_protocol_session(
            host, ctx,
            body=_claudecode_body,
            on_deliverable=config.on_deliverable,
            after_execute=config.after_execute,
            protocol=protocol,
            browser_url_rewrite=rewrite_oauth_redirect,
            agent_sender=_agent_sender,
        )
```

(Indentation: `_agent_sender` is defined at the same level as the `try:` that wraps the call — i.e. just above that `try:`, inside `run_claudecode_session`.)

- [ ] **Step 3: Write the test file** `packages/optio-claudecode/tests/test_agent_sender_claudecode.py`:

```python
import pytest

import optio_claudecode.host_actions as H


@pytest.mark.asyncio
async def test_sender_delegates_to_send_text_to_claude(monkeypatch):
    """The claudecode sender forwards (host, tmux_path, tmux_socket,
    tmux_session, message) to send_text_to_claude. Mirrors the closure built
    in run_claudecode_session."""
    calls = []

    async def fake_send(host, tmux_path, tmux_socket, tmux_session, message):
        calls.append((host, tmux_path, tmux_socket, tmux_session, message))

    monkeypatch.setattr(H, "send_text_to_claude", fake_send)

    host, tmux_path, tmux_socket, tmux_session = object(), "tmux", "/tmp/s", "optio"

    async def _agent_sender(message: str) -> None:
        await H.send_text_to_claude(
            host, tmux_path, tmux_socket, tmux_session, message,
        )

    await _agent_sender("ping")
    assert calls == [(host, "tmux", "/tmp/s", "optio", "ping")]
```

> Note: the live wiring (`agent_sender=` passed, the resume-notice flag append) is additionally covered by the existing claudecode local-session integration suite (`test_session_local.py` / `fake_claude.py` shim) in Task 13.

- [ ] **Step 4: Commit.**

```bash
git add packages/optio-claudecode/src/optio_claudecode/session.py \
        packages/optio-claudecode/tests/test_agent_sender_claudecode.py
git commit -m "feat(claudecode): wire send_to_agent transport + resume notice arg"
```

---

### Task 9: `optio-claudecode` — resume-section `System:` sentence

**Files:**
- Modify: `packages/optio-claudecode/src/optio_claudecode/prompt.py`
- Test: `packages/optio-claudecode/tests/test_resume_sentence_claudecode.py`

- [ ] **Step 1: Add the sentence to `RESUME_SECTION_TEMPLATE`.**

In `prompt.py`, replace the closing paragraph (lines 75–76):

```python
If a resume slips past unnoticed, a failing tool call is the
next-best signal — re-check `./resume.log` then.
"""
```

with:

```python
If a resume slips past unnoticed, a failing tool call is the
next-best signal — re-check `./resume.log` then.

You may also be notified of a resume by a `System:` message on your input
channel; when you see one, follow the `resume.log` procedure above.
"""
```

- [ ] **Step 2: Write the test file** `packages/optio-claudecode/tests/test_resume_sentence_claudecode.py`:

```python
from optio_claudecode.prompt import _render_resume_section


def test_resume_section_mentions_system_notification():
    section = _render_resume_section(None)
    assert "`System:` message" in section
    assert "resume.log" in section
```

- [ ] **Step 3: Commit.**

```bash
git add packages/optio-claudecode/src/optio_claudecode/prompt.py \
        packages/optio-claudecode/tests/test_resume_sentence_claudecode.py
git commit -m "docs(claudecode): note the System: resume notification in resume section"
```

---

### Task 10: `optio-demo` — shared feedback-on-deliverable helper

**Files:**
- Create: `packages/optio-demo/src/optio_demo/tasks/_feedback.py`
- Test: `packages/optio-demo/tests/test_feedback_helper.py`

- [ ] **Step 1: Create the helper** `packages/optio-demo/src/optio_demo/tasks/_feedback.py`:

```python
"""Shared on_deliverable helper that exercises the agent feedback channel.

The harness withholds one formatting requirement from the task prompt (the
"prank"): the deliverable must end with "over and out". When the agent's
delivery lacks it, the hook returns a nudge string; the deliverable loop
routes that back into the live agent via send_to_agent (HTTP for opencode,
tmux fake-typing for claudecode). The agent re-emits a corrected delivery.

A per-process nudge cap bounds a non-complying agent so the demo can't
ping-pong a real paid session forever.
"""

from __future__ import annotations

_MARKER = "over and out"
_NUDGE = (
    'Always finish your deliverables by "over and out." '
    "Otherwise I won't know that you have finished talking."
)
_CAP = 2
_nudges: dict[str, int] = {}  # process_id -> count


def make_feedback_on_deliverable(tag: str):
    """Build an on_deliverable callback that rejects deliveries missing the
    marker (returning a nudge for the loop to send), accepts those that have
    it, and gives up after _CAP nudges per process."""

    async def _on_deliverable(hook_ctx, path, text) -> "str | None":
        print(f"[{tag}] deliverable {path}:\n{text}")
        if text.strip().rstrip(".").lower().endswith(_MARKER):
            return None  # accept
        pid = hook_ctx.process_id
        n = _nudges.get(pid, 0)
        if n >= _CAP:
            hook_ctx.report_progress(None, "feedback: nudge cap reached, accepting")
            return None
        _nudges[pid] = n + 1
        hook_ctx.report_progress(None, f"feedback: nudging agent (#{n + 1})")
        return _NUDGE  # loop auto-sends via send_to_agent (return-value routing)

    return _on_deliverable
```

- [ ] **Step 2: Write the test file** `packages/optio-demo/tests/test_feedback_helper.py`:

```python
import pytest

from optio_demo.tasks._feedback import make_feedback_on_deliverable


class _Ctx:
    def __init__(self, pid):
        self.process_id = pid
        self.msgs = []

    def report_progress(self, percent, message=None):
        self.msgs.append(message)


@pytest.mark.asyncio
async def test_accepts_when_marker_present():
    cb = make_feedback_on_deliverable("t")
    assert await cb(_Ctx("p-accept"), "x.md", "all done. Over and out.") is None


@pytest.mark.asyncio
async def test_nudges_then_caps():
    cb = make_feedback_on_deliverable("t")
    ctx = _Ctx("p-cap")
    r1 = await cb(ctx, "x.md", "no marker here")
    assert r1 is not None and "over and out" in r1.lower()
    r2 = await cb(ctx, "x.md", "still missing")
    assert r2 is not None
    r3 = await cb(ctx, "x.md", "still missing")  # 3rd: past cap=2
    assert r3 is None
    assert any("cap" in (m or "") for m in ctx.msgs)
```

- [ ] **Step 3: Commit.**

```bash
git add packages/optio-demo/src/optio_demo/tasks/_feedback.py \
        packages/optio-demo/tests/test_feedback_helper.py
git commit -m "feat(demo): add shared feedback-on-deliverable helper"
```

---

### Task 11: `optio-demo` — rewire opencode demo to the helper

**Files:**
- Modify: `packages/optio-demo/src/optio_demo/tasks/opencode.py`

- [ ] **Step 1: Import the helper.** Add to the imports near the top of `opencode.py`:

```python
from optio_demo.tasks._feedback import make_feedback_on_deliverable
```

- [ ] **Step 2: Replace the inline `_on_deliverable`.** Replace the definition (lines 112–115):

```python
async def _on_deliverable(
    hook_ctx: HookContext, path: str, text: str,
) -> None:
    print(f"[opencode-demo] deliverable {path}:\n{text}")
```

with:

```python
# Exercises the agent feedback channel: rejects a first delivery that
# doesn't end with "over and out" (withheld from the prompt — the prank),
# nudges the agent, accepts the corrected re-delivery. Keeps the print.
_on_deliverable = make_feedback_on_deliverable("opencode-demo")
```

(All `on_deliverable=_on_deliverable` wiring sites in this module pick up the new behavior automatically.)

- [ ] **Step 3: Commit.**

```bash
git add packages/optio-demo/src/optio_demo/tasks/opencode.py
git commit -m "feat(demo): exercise feedback channel in the opencode demo"
```

---

### Task 12: `optio-demo` — rewire claudecode demo to the helper

**Files:**
- Modify: `packages/optio-demo/src/optio_demo/tasks/claudecode.py`

- [ ] **Step 1: Import the helper.** Add to the imports near the top of `claudecode.py`:

```python
from optio_demo.tasks._feedback import make_feedback_on_deliverable
```

- [ ] **Step 2: Replace the inline `_on_deliverable`.** Replace the definition (lines 110–113):

```python
async def _on_deliverable(
    hook_ctx: HookContext, path: str, text: str,
) -> None:
    print(f"[claudecode-demo] deliverable {path}:\n{text}")
```

with:

```python
# Exercises the agent feedback channel (tmux fake-typing transport): rejects
# a first delivery missing "over and out", nudges, accepts the re-delivery.
_on_deliverable = make_feedback_on_deliverable("claudecode-demo")
```

- [ ] **Step 3: Commit.**

```bash
git add packages/optio-demo/src/optio_demo/tasks/claudecode.py
git commit -m "feat(demo): exercise feedback channel in the claudecode demo"
```

---

### Task 13: Verification (run after Tasks 1–12 have all landed)

**Files:** none (runs tests + greps only).

This is the single verification gate. If any concurrent worktrees were used, merge them onto the feature branch first.

- [ ] **Step 1: Ensure editable installs are current.** Per the project convention, use a `.venv` inside the working tree and (re)install the four packages editable so cross-package imports resolve:

```bash
cd /home/csillag/deai/optio
python -m venv .venv && . .venv/bin/activate   # if not already present/active
pip install -e packages/optio-agents -e packages/optio-opencode \
            -e packages/optio-claudecode -e packages/optio-demo
```

- [ ] **Step 2: Run the new unit tests.**

```bash
.venv/bin/pytest \
  packages/optio-agents/tests/test_send_to_agent.py \
  packages/optio-agents/tests/test_deliverable_routing.py \
  packages/optio-agents/tests/test_prompt_feedback.py \
  packages/optio-agents/tests/test_exports_feedback.py \
  packages/optio-opencode/tests/test_agent_sender_opencode.py \
  packages/optio-opencode/tests/test_resume_sentence_opencode.py \
  packages/optio-claudecode/tests/test_send_text_to_claude.py \
  packages/optio-claudecode/tests/test_agent_sender_claudecode.py \
  packages/optio-claudecode/tests/test_resume_sentence_claudecode.py \
  packages/optio-demo/tests/test_feedback_helper.py -v
```

Expected: all PASS.

- [ ] **Step 3: Run each package's full suite to catch regressions** (the existing local-session integration tests exercise the new `agent_sender=` wiring and resume-notice path):

```bash
.venv/bin/pytest packages/optio-agents/tests -q
.venv/bin/pytest packages/optio-claudecode/tests -q
.venv/bin/pytest packages/optio-demo/tests -q
OPTIO_SKIP_PREFLIGHT_TESTS=1 .venv/bin/pytest packages/optio-opencode/tests -q
```

Expected: all PASS. (If a pre-existing, unrelated failure appears, confirm it reproduces on the base revision `dc703f4` before treating it as caused by this work.)

- [ ] **Step 4: Grep cleanliness — confirm the contract names are consistent and present.**

```bash
grep -rn "SYSTEM_MESSAGE_PREFIX" packages/optio-agents/src/optio_agents/__init__.py
grep -rn "AgentSender" packages/optio-agents/src/optio_agents/protocol/__init__.py
grep -rn "build_resume_notice_args" packages/optio-claudecode/src
grep -rn "make_feedback_on_deliverable" packages/optio-demo/src
grep -rn "send_to_agent" packages/optio-agents/src/optio_agents/context.py
```

Expected: each prints at least one hit. No `TODO`/`FIXME`/`placeholder` left behind:

```bash
git diff dc703f4 --name-only | xargs grep -nE "TODO|FIXME|placeholder" || echo "clean"
```

- [ ] **Step 5: Final commit (if Step 1 created/changed any lockfile or venv metadata that should be tracked — usually nothing).** Otherwise no commit; the work is complete.

---

## Self-Review (performed during planning)

**Spec coverage:**
- Part A (`send_to_agent`, optional sender, best-effort, protocol sig) → T1, T2.
- Part B (opencode sender) → T5.
- Part C (claudecode `send_text_to_claude`) → T7 (impl + verified), wired T8.
- Addendum 1 (return-value routing, widened `DeliverableCallback`) → T2.
- Addendum 2 (`System:` prefix) → T1 (chokepoint) + T5/T7 (explicit at launch sites).
- Addendum 3 (protocol doc inbound block) → T3.
- Addendum 4 (resume notify; keep `resume.log`; one-sentence doc) → T5 (opencode), T7+T8 (claudecode), T6+T9 (doc sentence). Refinement: claudecode gated on `pass_continue` via dedicated `build_resume_notice_args`.
- Addendum 5 (demo round-trip helper, marker, cap=2, no prompt edit) → T10, T11, T12.
- Exports of new symbols → T4.
- Testing sections → per-task tests + T13.

**Placeholder scan:** none — every step carries concrete code/commands. The two "Note" lines in T2/T5/T8 are deliberate scoping statements (integration coverage lives in T13's existing suites), not deferred work.

**Type consistency:** `SYSTEM_MESSAGE_PREFIX`, `RESUME_NOTICE`, `AgentSender`, `build_resume_notice_args`, `make_feedback_on_deliverable`, `send_text_to_claude` are spelled identically across defining and consuming tasks (pinned in the contract block).
