# CLIENT_MESSAGE / CALLER_MESSAGE Split — Implementation Plan

> **For agentic workers:** This plan is parallel-shaped: Tasks A–S are independent (disjoint files) and are executed concurrently via Workflow orchestration; **no task runs any tests, builds, or codegen**. ALL verification is deferred to the final serial phase. Spec: `docs/2026-06-11-optio-message-split-design.md`.

**Goal:** Replace the always-on `DOMAIN_MESSAGE:` optio.log keyword with two opt-in keywords: `CLIENT_MESSAGE:` (browser-session frontend, today's behavior) and `CALLER_MESSAGE:` (embedding-application callback), with full downstream rename `domain` → `client`.

**Architecture:** A frozen `ProtocolFeatures` value object (browser mode + two booleans) drives parser, agent-facing docs, and `get_protocol`. Caller messages dispatch through a bounded queue + worker (deliverable pattern) to a feedback-capable `on_caller_message` callback. Hard break, no aliases.

**Tech Stack:** Python (optio-agents, optio-core, optio-claudecode, optio-opencode, optio-demo), TypeScript (optio-contracts zod, optio-api, optio-ui, optio-dashboard), clamator codegen.

**Branch:** create `feature/message-split` from `main` before executing.

**Global conventions for every task:**
- Do not run tests/linters/builds — the final phase does.
- Do not touch `packages/optio-contracts/dist/` or any `_generated/` directory — the final phase regenerates them.
- Commit nothing — the final phase commits once after verification.

---

### Task A: ProtocolFeatures + parser

**Files:**
- Create: `packages/optio-agents/src/optio_agents/protocol/features.py`
- Modify: `packages/optio-agents/src/optio_agents/protocol/parser.py`

- [ ] **Step A1: Create `features.py`** (new module rather than `protocol.py` so `parser.py` can import it without an import cycle — `protocol.py` imports `parser.py`):

```python
"""Protocol feature flags — the value object describing per-agent variation.

Lives in its own module so both ``parser.py`` and ``protocol.py`` can
import it (``protocol.py`` already imports ``parser.py``).
"""

from __future__ import annotations

from dataclasses import dataclass

from optio_agents.browser_shims import BrowserMode


@dataclass(frozen=True)
class ProtocolFeatures:
    """Which optional protocol facets are active for one agent.

    ``browser="redirect"`` enables the ``BROWSER:`` keyword;
    ``client_messages`` enables ``CLIENT_MESSAGE:`` (frontend-routed);
    ``caller_messages`` enables ``CALLER_MESSAGE:`` (embedding-app callback).
    Disabled keywords are excluded from BOTH the parser and the LLM-facing
    documentation. Defaults are conservative: everything off.
    """

    browser: BrowserMode = "ignore"
    client_messages: bool = False
    caller_messages: bool = False
```

- [ ] **Step A2: In `parser.py`, replace `DomainMessageEvent` (lines 48–51) with two events:**

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

- [ ] **Step A3: Update the `LogEvent` union (lines 59–62):**

```python
LogEvent = Union[
    StatusEvent, DeliverableEvent, DoneEvent, ErrorEvent,
    BrowserEvent, AttentionEvent, ClientMessageEvent, CallerMessageEvent,
    UnknownLine,
]
```

- [ ] **Step A4: Replace `_RE_DOMAIN_MESSAGE` (line 71) with:**

```python
_RE_CLIENT_MESSAGE = re.compile(r"^CLIENT_MESSAGE:\s*(\S+)\s+(.*)$")
_RE_CALLER_MESSAGE = re.compile(r"^CALLER_MESSAGE:\s*(\S+)\s+(.*)$")
```

- [ ] **Step A5: Add import** `from optio_agents.protocol.features import ProtocolFeatures` and **replace the whole `parse_log_line` function** (lines 74–130) with:

```python
def _message_event(stripped: str, m: "re.Match[str]", cls) -> LogEvent:
    """Build a Client/CallerMessageEvent from a matched message line.

    Malformed JSON drops the line (not dispatched), surfaced as UnknownLine
    so the tail loop logs the raw line for diagnosis.
    """
    keyword, payload = m.group(1), m.group(2)
    try:
        data = json.loads(payload)
    except (ValueError, json.JSONDecodeError):
        return UnknownLine(text=stripped)
    return cls(keyword=keyword, data=data)


def parse_log_line(
    line: str, *, features: ProtocolFeatures = ProtocolFeatures(),
) -> LogEvent:
    """Classify one line from optio.log into a LogEvent.

    ``features`` controls the optional keywords. ``BROWSER:`` is recognized
    only for ``features.browser == "redirect"``; ``CLIENT_MESSAGE:`` /
    ``CALLER_MESSAGE:`` only when the corresponding flag is set. A disabled
    keyword's line falls through to ``UnknownLine`` — an agent cannot
    trigger a facility nobody enabled.
    """
    stripped = line.rstrip("\r\n").rstrip()
    m = _RE_STATUS.match(stripped)
    if m:
        pct_raw, msg = m.group(1), m.group(2)
        percent: int | None
        if pct_raw is None:
            percent = None
        else:
            percent = min(int(pct_raw), 100)
        return StatusEvent(percent=percent, message=msg)

    m = _RE_DELIVERABLE.match(stripped)
    if m:
        return DeliverableEvent(path=m.group(1))

    m = _RE_DONE.match(stripped)
    if m:
        summary = m.group(1) if m.group(1) else None
        return DoneEvent(summary=summary)

    m = _RE_ERROR.match(stripped)
    if m:
        msg = m.group(1) if m.group(1) else None
        return ErrorEvent(message=msg)

    if features.browser == "redirect":
        m = _RE_BROWSER.match(stripped)
        if m:
            # The browser shim emits `BROWSER: "<url>"` (printf '"%s"') — the
            # quotes delimit the value so a URL with spaces stays one token.
            # Strip them; the consumer wants the bare URL.
            return BrowserEvent(url=m.group(1).strip('"'))

    m = _RE_ATTENTION.match(stripped)
    if m:
        return AttentionEvent(reason=m.group(1))

    if features.client_messages:
        m = _RE_CLIENT_MESSAGE.match(stripped)
        if m:
            return _message_event(stripped, m, ClientMessageEvent)

    if features.caller_messages:
        m = _RE_CALLER_MESSAGE.match(stripped)
        if m:
            return _message_event(stripped, m, CallerMessageEvent)

    return UnknownLine(text=stripped)
```

**Behavior change (intended, hard break):** bare `parse_log_line(line)` no longer recognizes `BROWSER:` — the old default was `recognize_browser=True`, the new default `ProtocolFeatures()` is all-off. `get_protocol`-built parsers are unaffected (they pass explicit features).

---

### Task B: prompt builder

**Files:**
- Modify: `packages/optio-agents/src/optio_agents/protocol/prompt.py`

- [ ] **Step B1: Replace the module docstring (lines 1–11):**

```python
"""Single source of truth for the LLM-facing keyword-protocol documentation.

This module lives next to ``parser.py`` so the prose that teaches an agent
how to speak the protocol cannot drift from the regexes that enforce it.

``build_log_channel_prompt(features)`` assembles the feature-specific block:
the ``BROWSER:`` keyword is documented only for ``browser="redirect"``; a
trailing "no browser here" paragraph is appended only for
``browser="suppress"``; ``CLIENT_MESSAGE:`` / ``CALLER_MESSAGE:`` are
documented only when the corresponding ``ProtocolFeatures`` flag is set.
The core keywords (``STATUS:`` / ``DELIVERABLE:`` / ``DONE`` / ``ERROR`` /
``ATTENTION:``) are documented in every mode.
"""
```

- [ ] **Step B2: Change the import** (line 15) to:

```python
from optio_agents.protocol.features import ProtocolFeatures
```

(`BrowserMode` is no longer referenced here once the signature changes.)

- [ ] **Step B3: Replace `_TAIL_BULLETS` (lines 42–48) with:**

```python
_ATTENTION_BULLET = """- `ATTENTION:` — request human attention with a short reason, e.g.
  `ATTENTION: waiting for your approval`.
"""

_CLIENT_MESSAGE_BULLET = """- `CLIENT_MESSAGE:` — push a message to the user interface of the
  application that launched you: a keyword token followed by single-line
  JSON, e.g. `CLIENT_MESSAGE: build-finished {"artifact":"app.zip"}`.
  The JSON must be valid and on one line; malformed JSON is dropped.
"""

_CALLER_MESSAGE_BULLET = """- `CALLER_MESSAGE:` — send a message to the controlling application:
  a keyword token followed by single-line JSON, e.g.
  `CALLER_MESSAGE: tests-passed {"suite":"unit"}`. The JSON must be valid
  and on one line; malformed JSON is dropped. The application may answer
  with a `System:` message on your input channel.
"""
```

- [ ] **Step B4: Replace `build_log_channel_prompt` (lines 97–101) with:**

```python
def build_log_channel_prompt(
    features: ProtocolFeatures = ProtocolFeatures(),
) -> str:
    """Build the keyword-protocol documentation block for ``features``."""
    browser_bullet = _BROWSER_BULLET if features.browser == "redirect" else ""
    client_bullet = _CLIENT_MESSAGE_BULLET if features.client_messages else ""
    caller_bullet = _CALLER_MESSAGE_BULLET if features.caller_messages else ""
    suppress_note = _SUPPRESS_NOTE if features.browser == "suppress" else ""
    return (
        _HEADER
        + browser_bullet
        + _ATTENTION_BULLET
        + client_bullet
        + caller_bullet
        + suppress_note
        + _RULES
        + _FEEDBACK
    )
```

---

### Task C: protocol factory

**Files:**
- Modify: `packages/optio-agents/src/optio_agents/protocol/protocol.py`

- [ ] **Step C1: Update module docstring (lines 1–7):**

```python
"""The Protocol value object and its factory.

``get_protocol(browser=…, client_messages=…, caller_messages=…)`` is the
single decision point binding the facets that vary per agent — the keyword
documentation, the parser variant, and the browser-open shim behavior — to
one ``ProtocolFeatures``. The returned ``Protocol`` carries them so they
cannot drift apart.
"""
```

- [ ] **Step C2: Add import** `from optio_agents.protocol.features import ProtocolFeatures` (keep the `BrowserMode` import).

- [ ] **Step C3: Replace the `Protocol` dataclass body (lines 23–39):** swap the `browser: BrowserMode` field for `features: ProtocolFeatures` and add a compatibility property:

```python
@dataclass(frozen=True)
class Protocol:
    """Per-agent protocol variation: docs + parser + browser-shim behavior."""

    documentation: str
    parse_log_line: Callable[[str], LogEvent]
    features: ProtocolFeatures

    @property
    def browser(self) -> BrowserMode:
        return self.features.browser

    async def prepare_browser_shims(self, host: "Host") -> dict[str, str] | None:
        """Install this mode's browser shims; return launch-env additions.

        Returns ``None`` for ``ignore`` (no shims). The session driver calls
        this after ``setup_workdir`` and stashes the result on
        ``HookContext.browser_launch_env``; the agent body merges it into
        the launched subprocess's env.
        """
        return await prepare_browser_shims(host, self.features.browser)
```

- [ ] **Step C4: Replace `get_protocol` (lines 42–51):**

```python
def get_protocol(
    *,
    browser: BrowserMode = "ignore",
    client_messages: bool = False,
    caller_messages: bool = False,
) -> Protocol:
    """Build the ``Protocol`` for the requested feature set."""
    features = ProtocolFeatures(
        browser=browser,
        client_messages=client_messages,
        caller_messages=caller_messages,
    )
    return Protocol(
        documentation=build_log_channel_prompt(features),
        parse_log_line=functools.partial(parse_log_line, features=features),
        features=features,
    )
```

---

### Task D: session driver

**Files:**
- Modify: `packages/optio-agents/src/optio_agents/protocol/session.py`

- [ ] **Step D1: Update the parser imports (lines 28–41):** replace `DomainMessageEvent,` with `CallerMessageEvent,` and `ClientMessageEvent,` (alphabetical position: both after `BrowserEvent`).

- [ ] **Step D2: After `DELIVERABLE_QUEUE_BOUND = 64` (line 51) add:**

```python
CALLER_MESSAGE_QUEUE_BOUND = 64
```

- [ ] **Step D3: After the `DeliverableCallback` alias block (line 71) add:**

```python
CallerMessageCallback = Callable[["HookContext", str, object], Awaitable["str | None"]]
"""Consumer callback invoked per CALLER_MESSAGE log line.

Arguments: ``(hook_ctx, keyword, data)`` — ``keyword`` is the agent's
message-type token, ``data`` the decoded single-line JSON payload.

May return a non-empty string to send back to the running agent (the
caller-message loop routes it through ``hook_ctx.send_to_agent``); return
``None`` or ``""`` to send nothing. A hook may also call
``hook_ctx.send_to_agent(...)`` directly instead of / in addition to
returning.
"""
```

- [ ] **Step D4: Add the parameter to `run_log_protocol_session`** — after `on_deliverable: DeliverableCallback | None = None,` (line 112) insert:

```python
    # Server-side message channel. Enables nothing by itself: the protocol
    # must also be built with get_protocol(caller_messages=True); the two are
    # cross-checked at session start (ValueError on mismatch).
    on_caller_message: CallerMessageCallback | None = None,
```

- [ ] **Step D5: Add the consistency guards** right after the `protocol is None` default resolution (after line 180, before `hook_ctx = HookContext(ctx, host)`):

```python
    if protocol.features.caller_messages and on_caller_message is None:
        raise ValueError(
            "protocol enables CALLER_MESSAGE but no on_caller_message "
            "callback was provided"
        )
    if on_caller_message is not None and not protocol.features.caller_messages:
        raise ValueError(
            "on_caller_message callback provided but the protocol does not "
            "enable it; build with get_protocol(caller_messages=True)"
        )
```

- [ ] **Step D6: Wire the caller-message queue.** In the task-spawn block (lines 209–246):
  - add `caller_task: asyncio.Task | None = None` next to the other `* _task: asyncio.Task | None = None` declarations;
  - after the `deliverable_queue` creation (line 222–224) add:

```python
        caller_queue: asyncio.Queue[tuple[str, object]] = asyncio.Queue(
            maxsize=CALLER_MESSAGE_QUEUE_BOUND,
        )
```

  - inside `if keywords:` (line 228), after the `fetch_task` creation add:

```python
            caller_task = asyncio.create_task(
                _caller_message_loop(on_caller_message, caller_queue, ctx, hook_ctx),
            )
```

  - pass `caller_queue` to `_tail_and_dispatch` (its signature changes in Step D8): the call becomes

```python
            tail_task = asyncio.create_task(
                _tail_and_dispatch(
                    host, ctx, deliverable_queue, caller_queue, done_flag,
                    error_flag, protocol.parse_log_line, browser_url_rewrite,
                ),
            )
```

- [ ] **Step D7: Drain + teardown.** Next to the existing drain (lines 266–268) add the caller queue:

```python
        # Drain remaining deliverables + caller messages before returning.
        if keywords:
            await deliverable_queue.join()
            await caller_queue.join()
```

In the `finally` block, include `caller_task` in the `active_tasks` tuple (line 276):

```python
        active_tasks = [
            t for t in (tail_task, body_task, cancel_task, fetch_task, caller_task)
            if t is not None
        ]
```

(Feedback emitted after DONE/ERROR is inherently best-effort: `hook_ctx.send_to_agent` returns False when the agent is gone — no special handling.)

- [ ] **Step D8: Update `_tail_and_dispatch`** — new signature and dispatch arms. Signature (lines 300–308):

```python
async def _tail_and_dispatch(
    host: Host,
    ctx: "ProcessContext",
    deliverable_queue: asyncio.Queue[tuple[str, str]],
    caller_queue: asyncio.Queue[tuple[str, object]],
    done_flag: asyncio.Event,
    error_flag: list,
    parse_line: "Callable[[str], LogEvent]",
    browser_url_rewrite: "Callable[[str], str] | None" = None,
) -> None:
```

Replace the `DomainMessageEvent` arm (lines 342–343) with:

```python
        elif isinstance(ev, ClientMessageEvent):
            await ctx.client_message(ev.keyword, ev.data)
        elif isinstance(ev, CallerMessageEvent):
            item = (ev.keyword, ev.data)
            try:
                caller_queue.put_nowait(item)
            except asyncio.QueueFull:
                await caller_queue.put(item)
```

- [ ] **Step D9: Add the worker loop** after `_deliverable_fetch_loop` (after line 421):

```python
async def _caller_message_loop(
    callback: CallerMessageCallback | None,
    queue: asyncio.Queue[tuple[str, object]],
    ctx: "ProcessContext",
    hook_ctx: HookContext,
) -> None:
    """Drain the caller-message queue: invoke the consumer callback, push
    any returned feedback to the live agent.

    A raising callback is logged and the session continues — one bad
    handler call must not kill the task. ``callback`` can only be None
    here if the start-time consistency guard was bypassed; treated as
    drop-with-log, defensively.
    """
    while True:
        keyword, data = await queue.get()
        try:
            if callback is None:
                ctx.report_progress(
                    None,
                    f"CALLER_MESSAGE {keyword!r} dropped: no on_caller_message",
                )
                continue
            try:
                feedback = await callback(hook_ctx, keyword, data)
            except Exception as exc:  # noqa: BLE001
                ctx.report_progress(
                    None, f"on_caller_message callback raised: {exc!r}",
                )
                continue
            if isinstance(feedback, str) and feedback.strip():
                await hook_ctx.send_to_agent(feedback.strip())
        finally:
            queue.task_done()
```

---

### Task E: package exports

**Files:**
- Modify: `packages/optio-agents/src/optio_agents/protocol/__init__.py`
- Modify: `packages/optio-agents/src/optio_agents/__init__.py`

- [ ] **Step E1: `protocol/__init__.py`:** in the parser import block replace `DomainMessageEvent,` with `CallerMessageEvent,` + `ClientMessageEvent,`; in the session import block add `CALLER_MESSAGE_QUEUE_BOUND,` and `CallerMessageCallback,`; change the protocol import line to:

```python
from optio_agents.protocol.features import ProtocolFeatures
from optio_agents.protocol.protocol import BrowserMode, Protocol, get_protocol
```

In `__all__`: replace `"DomainMessageEvent"` with `"ClientMessageEvent", "CallerMessageEvent"`; add `"CallerMessageCallback"`, `"CALLER_MESSAGE_QUEUE_BOUND"`, `"ProtocolFeatures"`.

- [ ] **Step E2: `optio_agents/__init__.py`:** in the `from optio_agents.protocol import (...)` block add `CALLER_MESSAGE_QUEUE_BOUND,` `CallerMessageCallback,` `ProtocolFeatures,` (alphabetical). `DomainMessageEvent` is not re-exported at this level today; do not add the new event types either. Add the three new names to `__all__` next to their siblings (`"CallerMessageCallback"` after `"DeliverableCallback"`, `"CALLER_MESSAGE_QUEUE_BOUND"` after `"DELIVERABLE_QUEUE_BOUND"`, `"ProtocolFeatures"` after `"Protocol"`).

---

### Task F: optio-agents parser tests

**Files:**
- Modify: `packages/optio-agents/tests/test_protocol_parser.py`

- [ ] **Step F1: Update imports:** replace `DomainMessageEvent,` with `CallerMessageEvent,` + `ClientMessageEvent,` and add `from optio_agents.protocol.features import ProtocolFeatures`.

- [ ] **Step F2: Browser tests now need explicit features.** Update `test_browser_event` and `test_browser_event_unquoted` (lines 185–195) to pass `features=ProtocolFeatures(browser="redirect")` to `parse_log_line`. Replace the `recognize_browser` toggle tests (lines 216–231 and the `test_attention_still_recognized_when_browser_disabled` test that follows) with:

```python
# ---- feature toggles ----

def test_browser_not_recognized_by_default():
    # Conservative default: ProtocolFeatures() has browser="ignore".
    ev = parse_log_line("BROWSER: https://example.com")
    assert isinstance(ev, UnknownLine)
    assert ev.text == "BROWSER: https://example.com"


def test_browser_recognized_under_redirect():
    ev = parse_log_line(
        "BROWSER: https://example.com",
        features=ProtocolFeatures(browser="redirect"),
    )
    assert isinstance(ev, BrowserEvent)


def test_attention_recognized_in_every_mode():
    for features in (ProtocolFeatures(), ProtocolFeatures(browser="redirect")):
        ev = parse_log_line("ATTENTION: ping", features=features)
        assert isinstance(ev, AttentionEvent)
```

- [ ] **Step F3: Replace the domain-message tests (lines 204–213) with:**

```python
_MSGS_ON = ProtocolFeatures(client_messages=True, caller_messages=True)


def test_client_message_event():
    ev = parse_log_line(
        'CLIENT_MESSAGE: build-done {"artifact": "app.zip"}', features=_MSGS_ON,
    )
    assert isinstance(ev, ClientMessageEvent)
    assert ev.keyword == "build-done"
    assert ev.data == {"artifact": "app.zip"}


def test_caller_message_event():
    ev = parse_log_line(
        'CALLER_MESSAGE: tests-passed {"suite": "unit"}', features=_MSGS_ON,
    )
    assert isinstance(ev, CallerMessageEvent)
    assert ev.keyword == "tests-passed"
    assert ev.data == {"suite": "unit"}


def test_message_keywords_disabled_by_default():
    for line in ('CLIENT_MESSAGE: k {"n": 1}', 'CALLER_MESSAGE: k {"n": 1}'):
        ev = parse_log_line(line)
        assert isinstance(ev, UnknownLine)
        assert ev.text == line


def test_message_malformed_json_drops_to_unknown():
    for line in ("CLIENT_MESSAGE: k {not json}", "CALLER_MESSAGE: k {not json}"):
        ev = parse_log_line(line, features=_MSGS_ON)
        assert isinstance(ev, UnknownLine)


def test_domain_message_keyword_is_gone():
    # Removal regression pin: the old keyword is inert even with messages on.
    ev = parse_log_line('DOMAIN_MESSAGE: k {"n": 1}', features=_MSGS_ON)
    assert isinstance(ev, UnknownLine)
```

---

### Task G: optio-agents prompt + protocol tests

**Files:**
- Modify: `packages/optio-agents/tests/test_prompt.py`
- Modify: `packages/optio-agents/tests/test_protocol.py`

- [ ] **Step G1: Rewrite `test_prompt.py` as:**

```python
"""Tests for the feature-aware keyword-protocol prompt builder."""

from optio_agents.protocol.features import ProtocolFeatures
from optio_agents.protocol.prompt import build_log_channel_prompt


def test_core_keywords_present_in_every_mode():
    for browser in ("ignore", "suppress", "redirect"):
        block = build_log_channel_prompt(ProtocolFeatures(browser=browser))
        for kw in ("STATUS:", "DELIVERABLE:", "DONE", "ERROR", "ATTENTION:"):
            assert kw in block, (browser, kw)


def test_browser_keyword_only_in_redirect():
    assert "BROWSER:" in build_log_channel_prompt(ProtocolFeatures(browser="redirect"))
    assert "BROWSER:" not in build_log_channel_prompt(ProtocolFeatures(browser="ignore"))
    assert "BROWSER:" not in build_log_channel_prompt(ProtocolFeatures(browser="suppress"))


def test_suppress_trailing_note_only_in_suppress():
    note = "impossible to launch a browser"
    assert note in build_log_channel_prompt(ProtocolFeatures(browser="suppress"))
    assert note not in build_log_channel_prompt(ProtocolFeatures(browser="ignore"))
    assert note not in build_log_channel_prompt(ProtocolFeatures(browser="redirect"))


def test_client_message_documented_only_when_enabled():
    assert "CLIENT_MESSAGE:" in build_log_channel_prompt(
        ProtocolFeatures(client_messages=True))
    assert "CLIENT_MESSAGE:" not in build_log_channel_prompt(ProtocolFeatures())


def test_caller_message_documented_only_when_enabled():
    assert "CALLER_MESSAGE:" in build_log_channel_prompt(
        ProtocolFeatures(caller_messages=True))
    assert "CALLER_MESSAGE:" not in build_log_channel_prompt(ProtocolFeatures())


def test_domain_message_never_documented():
    assert "DOMAIN_MESSAGE:" not in build_log_channel_prompt(
        ProtocolFeatures(client_messages=True, caller_messages=True))


def test_block_mentions_log_and_deliverables_paths():
    block = build_log_channel_prompt()
    assert "./optio.log" in block
    assert "./deliverables/" in block


def test_block_states_trailing_newline_requirement():
    block = build_log_channel_prompt()
    assert "newline" in block
    assert "tail -c 1" in block


def test_block_has_log_channel_and_deliverables_sections():
    block = build_log_channel_prompt()
    assert "## Log channel" in block
    assert "## Deliverables" in block


def test_block_is_framing_neutral():
    assert "optio-opencode" not in build_log_channel_prompt()
```

- [ ] **Step G2: Rewrite `test_protocol.py` as:**

```python
"""get_protocol binds documentation + parser + features together."""

from optio_agents import get_protocol
from optio_agents.protocol.parser import (
    BrowserEvent,
    CallerMessageEvent,
    ClientMessageEvent,
    UnknownLine,
)


def test_browser_property_matches_request():
    for browser in ("ignore", "suppress", "redirect"):
        assert get_protocol(browser=browser).browser == browser


def test_default_is_all_off():
    p = get_protocol()
    assert p.features.browser == "ignore"
    assert p.features.client_messages is False
    assert p.features.caller_messages is False


def test_documentation_reflects_features():
    assert "BROWSER:" in get_protocol(browser="redirect").documentation
    assert "BROWSER:" not in get_protocol(browser="ignore").documentation
    assert "impossible to launch a browser" in get_protocol(browser="suppress").documentation
    assert "CLIENT_MESSAGE:" in get_protocol(client_messages=True).documentation
    assert "CLIENT_MESSAGE:" not in get_protocol().documentation
    assert "CALLER_MESSAGE:" in get_protocol(caller_messages=True).documentation
    assert "CALLER_MESSAGE:" not in get_protocol().documentation


def test_parser_reflects_features():
    redirect = get_protocol(browser="redirect")
    suppress = get_protocol(browser="suppress")
    assert isinstance(redirect.parse_log_line("BROWSER: https://x"), BrowserEvent)
    assert isinstance(suppress.parse_log_line("BROWSER: https://x"), UnknownLine)

    msgs = get_protocol(client_messages=True, caller_messages=True)
    plain = get_protocol()
    assert isinstance(
        msgs.parse_log_line('CLIENT_MESSAGE: k {"n": 1}'), ClientMessageEvent)
    assert isinstance(
        msgs.parse_log_line('CALLER_MESSAGE: k {"n": 1}'), CallerMessageEvent)
    assert isinstance(
        plain.parse_log_line('CLIENT_MESSAGE: k {"n": 1}'), UnknownLine)
    assert isinstance(
        plain.parse_log_line('CALLER_MESSAGE: k {"n": 1}'), UnknownLine)
```

---

### Task H: optio-agents dispatch tests

**Files:**
- Modify: `packages/optio-agents/tests/test_client_directed_dispatch.py`

- [ ] **Step H1: Rewrite the file as:**

```python
"""_tail_and_dispatch + _caller_message_loop route client-directed events."""

import asyncio

import pytest

from optio_agents.protocol.session import (
    _caller_message_loop,
    _tail_and_dispatch,
    run_log_protocol_session,
)
from optio_agents import get_protocol
from optio_agents.context import HookContext


class _FakeHost:
    def __init__(self, lines, workdir="/wd"):
        self._lines = lines
        self.workdir = workdir

    async def tail_file(self, _path):
        for line in self._lines:
            yield line


class _FakeCtx:
    def __init__(self):
        self.browser = []
        self.attention = []
        self.client = []
        self.progress = []

    async def request_browser_open(self, url):
        self.browser.append(url)
        return "rid-b"

    async def need_attention(self, reason):
        self.attention.append(reason)
        return "rid-a"

    async def client_message(self, keyword, data):
        self.client.append((keyword, data))
        return "rid-c"

    def report_progress(self, percent, message):
        self.progress.append((percent, message))


@pytest.mark.asyncio
async def test_dispatch_routes_browser_attention_client_caller():
    host = _FakeHost([
        "BROWSER: https://x\n",
        "ATTENTION: help me\n",
        'CLIENT_MESSAGE: ev {"n": 1}\n',
        'CALLER_MESSAGE: ask {"q": 2}\n',
        "DONE\n",
    ])
    ctx = _FakeCtx()
    done = asyncio.Event()
    caller_queue = asyncio.Queue()
    await _tail_and_dispatch(
        host, ctx, asyncio.Queue(), caller_queue, done, [],
        get_protocol(
            browser="redirect", client_messages=True, caller_messages=True,
        ).parse_log_line,
    )
    assert ctx.browser == ["https://x"]
    assert ctx.attention == ["help me"]
    assert ctx.client == [("ev", {"n": 1})]
    assert caller_queue.get_nowait() == ("ask", {"q": 2})
    assert done.is_set()


@pytest.mark.asyncio
async def test_dispatch_ignores_browser_under_suppress():
    host = _FakeHost([
        "BROWSER: https://x\n",
        "ATTENTION: help me\n",
        "DONE\n",
    ])
    ctx = _FakeCtx()
    done = asyncio.Event()
    await _tail_and_dispatch(
        host, ctx, asyncio.Queue(), asyncio.Queue(), done, [],
        get_protocol(browser="suppress").parse_log_line,
    )
    assert ctx.browser == []                 # BROWSER line was inert (UnknownLine)
    assert ctx.attention == ["help me"]      # ATTENTION still routed
    assert done.is_set()


@pytest.mark.asyncio
async def test_dispatch_messages_inert_when_disabled():
    host = _FakeHost([
        'CLIENT_MESSAGE: ev {"n": 1}\n',
        'CALLER_MESSAGE: ask {"q": 2}\n',
        "DONE\n",
    ])
    ctx = _FakeCtx()
    done = asyncio.Event()
    caller_queue = asyncio.Queue()
    await _tail_and_dispatch(
        host, ctx, asyncio.Queue(), caller_queue, done, [],
        get_protocol().parse_log_line,
    )
    assert ctx.client == []
    assert caller_queue.empty()
    # Disabled keywords surface verbatim as progress text.
    texts = [m for (_p, m) in ctx.progress]
    assert 'CLIENT_MESSAGE: ev {"n": 1}' in texts
    assert 'CALLER_MESSAGE: ask {"q": 2}' in texts


def _hook_ctx_with_sender(ctx, sent):
    hook_ctx = HookContext(ctx, _FakeHost([]))
    async def _sender(message):
        sent.append(message)
    hook_ctx._agent_sender = _sender
    return hook_ctx


@pytest.mark.asyncio
async def test_caller_loop_invokes_callback_and_sends_feedback():
    ctx = _FakeCtx()
    sent: list[str] = []
    received: list[tuple[str, object]] = []

    async def on_caller(hook_ctx, keyword, data):
        received.append((keyword, data))
        return "the answer is 42"

    queue: asyncio.Queue = asyncio.Queue()
    await queue.put(("ask", {"q": 2}))
    loop_task = asyncio.create_task(
        _caller_message_loop(on_caller, queue, ctx, _hook_ctx_with_sender(ctx, sent)),
    )
    await queue.join()
    loop_task.cancel()

    assert received == [("ask", {"q": 2})]
    assert sent == ["the answer is 42"]


@pytest.mark.asyncio
async def test_caller_loop_none_feedback_sends_nothing():
    ctx = _FakeCtx()
    sent: list[str] = []

    async def on_caller(hook_ctx, keyword, data):
        return None

    queue: asyncio.Queue = asyncio.Queue()
    await queue.put(("ask", 1))
    loop_task = asyncio.create_task(
        _caller_message_loop(on_caller, queue, ctx, _hook_ctx_with_sender(ctx, sent)),
    )
    await queue.join()
    loop_task.cancel()

    assert sent == []


@pytest.mark.asyncio
async def test_caller_loop_survives_callback_exception():
    ctx = _FakeCtx()
    sent: list[str] = []
    calls: list[str] = []

    async def on_caller(hook_ctx, keyword, data):
        calls.append(keyword)
        if keyword == "boom":
            raise RuntimeError("handler exploded")
        return "ok-2"

    queue: asyncio.Queue = asyncio.Queue()
    await queue.put(("boom", 1))
    await queue.put(("fine", 2))
    loop_task = asyncio.create_task(
        _caller_message_loop(on_caller, queue, ctx, _hook_ctx_with_sender(ctx, sent)),
    )
    await queue.join()
    loop_task.cancel()

    assert calls == ["boom", "fine"]         # second message still processed
    assert sent == ["ok-2"]
    assert any("on_caller_message callback raised" in (m or "")
               for (_p, m) in ctx.progress)


class _GuardHost(_FakeHost):
    """Host stub for guard tests: run_log_protocol_session must raise the
    ValueError before touching the host, so every method explodes."""

    def __init__(self):
        super().__init__([])

    async def setup_workdir(self):
        raise AssertionError("guard must fire before workdir setup")


@pytest.mark.asyncio
async def test_guard_feature_on_without_callback():
    async def body(host, hook_ctx):
        pass

    with pytest.raises(ValueError, match="no on_caller_message"):
        await run_log_protocol_session(
            _GuardHost(), _FakeCtx(), body=body,
            protocol=get_protocol(caller_messages=True),
        )


@pytest.mark.asyncio
async def test_guard_callback_without_feature():
    async def body(host, hook_ctx):
        pass

    async def on_caller(hook_ctx, keyword, data):
        return None

    with pytest.raises(ValueError, match="does not enable it"):
        await run_log_protocol_session(
            _GuardHost(), _FakeCtx(), body=body,
            protocol=get_protocol(),
            on_caller_message=on_caller,
        )
```

---

### Task I: optio-core rename

**Files:**
- Modify: `packages/optio-core/src/optio_core/context.py:306-314`
- Modify: `packages/optio-core/src/optio_core/store.py:515-521`

- [ ] **Step I1: Rename the context method** (context.py lines 306–314):

```python
    async def client_message(self, keyword: str, data) -> str:
        """Push an application-defined message to the launching browser
        session's frontend. Session-scoped. `data` must be JSON-serializable;
        optio does not interpret it. Returns the requestId."""
        from optio_core.store import append_session_event
        return await append_session_event(
            self._db, self._prefix, self._process_oid,
            {"type": "client", "keyword": keyword, "data": data},
        )
```

- [ ] **Step I2: Update the `append_session_event` docstring** (store.py line 519): change `{"type": "domain", "keyword": <str>, "data": <json>}` to `{"type": "client", "keyword": <str>, "data": <json>}`.

---

### Task J: optio-core tests

**Files:**
- Modify: `packages/optio-core/tests/test_client_directed_events.py`

- [ ] **Step J1:** In `test_append_session_event_attention_and_domain` (rename to `test_append_session_event_attention_and_client`): change both `"type": "domain"` literals (lines 46 and 50) to `"type": "client"`.

- [ ] **Step J2:** In `test_ctx_need_attention_and_domain_message` (rename to `test_ctx_need_attention_and_client_message`): change `await ctx.domain_message(...)` (line 72) to `await ctx.client_message(...)` and the expected record's `"type": "domain"` (line 76) to `"type": "client"`.

---

### Task K: optio-contracts schema

**Files:**
- Modify: `packages/optio-contracts/src/schemas/process.ts:38`
- Modify: `packages/optio-contracts/src/__tests__/session-events-schema.test.ts:19-21`
- Modify: `packages/optio-contracts/src/__tests__/process-schema.test.ts:59`

- [ ] **Step K1:** process.ts line 38: `z.literal('domain')` → `z.literal('client')`.
- [ ] **Step K2:** session-events-schema.test.ts lines 19–21: all three `'domain'` literals → `'client'`.
- [ ] **Step K3:** process-schema.test.ts line 59: `type: 'domain'` → `type: 'client'`.
- [ ] Do NOT touch `dist/` — the final phase rebuilds it and re-runs codegen.

---

### Task L: optio-api test literal

**Files:**
- Modify: `packages/optio-api/src/__tests__/session-events-poller.test.ts:68`

- [ ] **Step L1:** `type: 'domain'` → `type: 'client'` in the `$push`ed sessionEvents record. Check the same file for any assertion on `type === 'domain'` and update it identically.

---

### Task M: optio-ui rename

**Files:**
- Modify: `packages/optio-ui/src/session/sessionEvents.ts`
- Modify: `packages/optio-ui/src/context/OptioProvider.tsx`
- Modify: `packages/optio-ui/src/__tests__/sessionEvents.test.tsx`

- [ ] **Step M1: sessionEvents.ts:**
  - line 17: `| { requestId: string; type: 'client'; keyword: string; data: unknown };`
  - line 21: `onClientMessage?: (processId: string, keyword: string, data: unknown) => void;`
  - lines 87–88: `} else if (ev.type === 'client') { _callbacks.onClientMessage?.(processId, ev.keyword, ev.data); }`
  - line 111: `const active = Boolean((callbacks.onAttention || callbacks.onClientMessage) && prefix);`
  - line 135: `if (_callbacks.onAttention || _callbacks.onClientMessage) connect();`
- [ ] **Step M2: OptioProvider.tsx:** rename the `onDomainMessage` prop to `onClientMessage` at lines 23, 47, 54, 56 (type, destructuring, callbacks object, deps array).
- [ ] **Step M3: sessionEvents.test.tsx:** lines 64–82: rename `onDomainMessage` → `onClientMessage` (3 occurrences) and `type: 'domain'` → `type: 'client'` (line 71).

---

### Task N: optio-dashboard

**Files:**
- Modify: `packages/optio-dashboard/src/app/App.tsx:71-76,142`

- [ ] **Step N1:** Replace lines 71–76 with:

```tsx
  // Client messages: surface to the console (apps can do richer handling).
  const onClientMessage = (processId: string, keyword: string, data: unknown) => {
    // eslint-disable-next-line no-console
    console.log('[optio client_message]', { processId, keyword, data });
    notification.info({ message: `Client message: ${keyword}`, description: JSON.stringify(data) });
  };
```

- [ ] **Step N2:** Line 142: `onDomainMessage={onDomainMessage}` → `onClientMessage={onClientMessage}`.

---

### Task O: optio-demo

**Files:**
- Modify: `packages/optio-demo/src/optio_demo/tasks/client_directed.py`

- [ ] **Step O1:** Module docstring line 12: `- \`\`client-message-demo\`\`: \`\`ctx.client_message\`\` (session-scoped).`
- [ ] **Step O2:** Replace `_domain_message_demo` (lines 78–84) with:

```python
async def _client_message_demo(ctx) -> None:
    ctx.report_progress(0, "Sending a client message")
    rid = await ctx.client_message(
        "demo-event",
        {"severity": "info", "detail": "hello from the demo task"},
    )
    ctx.report_progress(100, f"Client message sent (requestId={rid})")
```

- [ ] **Step O3:** Update the TaskInstance (lines 119–127):

```python
        TaskInstance(
            execute=_client_message_demo,
            process_id="client-message-demo",
            name="Send a client message",
            description=(
                "Calls ctx.client_message(keyword, data). Session-scoped; the "
                "dashboard surfaces it via onClientMessage (console/toast)."
            ),
        ),
```

---

### Task P: optio-claudecode wiring

**Files:**
- Modify: `packages/optio-claudecode/src/optio_claudecode/types.py`
- Modify: `packages/optio-claudecode/src/optio_claudecode/session.py:135,574-584`

- [ ] **Step P1: types.py:** extend the existing optio-agents callback import (the line importing `DeliverableCallback` / `HookCallback`) with `CallerMessageCallback`. After `on_deliverable: DeliverableCallback | None = None` (line 124) add:

```python
    # Enable the CLIENT_MESSAGE keyword: agent-pushed messages routed to the
    # originating browser session's frontend (stored as sessionEvents,
    # surfaced via optio-ui's onClientMessage). Off by default.
    use_client_messages: bool = False
    # Enable the CALLER_MESSAGE keyword: agent-pushed messages routed to this
    # callback in the embedding application. A non-None return value is sent
    # back to the agent as feedback. Off (None) by default.
    on_caller_message: CallerMessageCallback | None = None
```

Also add `"CallerMessageCallback"` to `__all__` if the module re-exports the callback types (mirror how `DeliverableCallback` is handled).

- [ ] **Step P2: session.py line 135:**

```python
    protocol = get_protocol(
        browser="redirect",
        client_messages=config.use_client_messages,
        caller_messages=config.on_caller_message is not None,
    )
```

- [ ] **Step P3: session.py call site (lines 574–584):** add `on_caller_message=config.on_caller_message,` after the `on_deliverable=config.on_deliverable,` line:

```python
        await run_log_protocol_session(
            host, ctx,
            body=body,
            prepare=_prepare,
            on_deliverable=config.on_deliverable,
            on_caller_message=config.on_caller_message,
            after_execute=config.after_execute,
            protocol=protocol,
            browser_url_rewrite=rewrite_oauth_redirect,
            agent_sender=_agent_sender,
            keywords=config.host_protocol,
        )
```

---

### Task Q: optio-opencode wiring

**Files:**
- Modify: `packages/optio-opencode/src/optio_opencode/types.py`
- Modify: `packages/optio-opencode/src/optio_opencode/session.py:83,370-378`

- [ ] **Step Q1: types.py:** change the import (line 12) to:

```python
from optio_agents.protocol.session import (
    CallerMessageCallback,
    DeliverableCallback,
    HookCallback,
)
```

Add `"CallerMessageCallback"` to `__all__` (line 16). After `on_deliverable: DeliverableCallback | None = None` (line 25) add the same two fields as Task P Step P1 (identical code + comments).

- [ ] **Step Q2: session.py line 83:**

```python
    protocol = get_protocol(
        browser="suppress",
        client_messages=config.use_client_messages,
        caller_messages=config.on_caller_message is not None,
    )
```

- [ ] **Step Q3: session.py call site (lines 370–378):** add `on_caller_message=config.on_caller_message,` after `on_deliverable=config.on_deliverable,`:

```python
        await run_log_protocol_session(
            host, ctx,
            body=_opencode_body,
            prepare=_prepare,
            on_deliverable=config.on_deliverable,
            on_caller_message=config.on_caller_message,
            after_execute=config.after_execute,
            protocol=protocol,
            agent_sender=_agent_sender,
        )
```

---

### Task R: optio-opencode e2e

**Files:**
- Modify: `packages/optio-opencode/tests/fake_opencode.py` (SCENARIOS dict, line 105)
- Modify: `packages/optio-opencode/tests/test_session_local.py`

- [ ] **Step R1: Add two scenarios to `SCENARIOS`:**

```python
    "caller_message": [
        ("log", 'CALLER_MESSAGE: ping {"n": 1}'),
        ("sleep", 0.1),
        ("log", "DONE"),
    ],
    "client_message": [
        ("log", 'CLIENT_MESSAGE: notify {"msg": "hi"}'),
        ("sleep", 0.05),
        ("log", "DONE"),
    ],
```

- [ ] **Step R2: Add two tests at the end of the scenarios section of `test_session_local.py`** (same fixtures and style as `test_happy_path`, lines 171–185):

```python
async def test_caller_message_reaches_callback(ctx_and_captures, _supply_scenario):
    ctx, _cap, _ = ctx_and_captures
    _supply_scenario["name"] = "caller_message"

    received: list[tuple[str, object]] = []
    async def on_caller(hook_ctx, keyword, data):
        received.append((keyword, data))
        return "pong"

    cfg = OpencodeTaskConfig(
        consumer_instructions="(scenario: caller_message)",
        on_caller_message=on_caller,
    )
    await run_opencode_session(ctx, cfg)

    assert received == [("ping", {"n": 1})]


async def test_client_message_stored_as_session_event(
    ctx_and_captures, _supply_scenario, mongo_db,
):
    ctx, _cap, _ = ctx_and_captures
    _supply_scenario["name"] = "client_message"

    cfg = OpencodeTaskConfig(
        consumer_instructions="(scenario: client_message)",
        use_client_messages=True,
    )
    await run_opencode_session(ctx, cfg)

    doc = await mongo_db["test_processes"].find_one({"processId": "p"})
    events = [e for e in doc.get("sessionEvents", []) if e["type"] == "client"]
    assert len(events) == 1
    assert events[0]["keyword"] == "notify"
    assert events[0]["data"] == {"msg": "hi"}
```

(`mongo_db` is the same fixture `ctx_and_captures` builds on; if the fixture signature differs, fetch the db from `ctx` the way other tests in this file inspect mongo. The CALLER_MESSAGE feedback transport — `_agent_sender` → fake opencode HTTP — is covered by `test_caller_loop_invokes_callback_and_sends_feedback` in optio-agents; here we assert the callback round-trip through a real session.)

---

### Task S: documentation

**Files:**
- Modify: `docs/2026-04-22-optio-opencode-design.md` (Section 6)
- Modify: `docs/2026-05-29-optio-protocol-variation-design.md` (top)
- Modify: `packages/optio-claudecode/README.md`
- Modify: `packages/optio-opencode/README.md`

- [ ] **Step S1:** At the top of Section 6 of the opencode design doc, add:

> **Note (2026-06-11):** `DOMAIN_MESSAGE:` has been split into the opt-in `CLIENT_MESSAGE:` / `CALLER_MESSAGE:` keywords — see `docs/2026-06-11-optio-message-split-design.md`.

- [ ] **Step S2:** Same one-line note near the top of the protocol-variation design doc (after its title), additionally mentioning that `get_protocol` now takes `client_messages` / `caller_messages` and `Protocol.browser` became `Protocol.features`.

- [ ] **Step S3:** In each README's task-config section (wherever `on_deliverable` is documented; if absent, add a "Messages" subsection), document:

```markdown
- `use_client_messages` (bool, default `False`) — enable the `CLIENT_MESSAGE:`
  log keyword: the agent can push `{keyword, data}` messages to the browser
  session that launched the task (surfaced via optio-ui's `onClientMessage`).
- `on_caller_message` (async callback, default `None`) — enable the
  `CALLER_MESSAGE:` log keyword: the agent can push `{keyword, data}` messages
  to your application. Signature `(hook_ctx, keyword, data) -> str | None`;
  a non-None return is sent back to the agent as feedback. Keywords that are
  not enabled are absent from both the parser and the agent-facing protocol
  documentation.
```

---

### Final phase (serial, after ALL tasks): codegen + verification + commit

- [ ] **Step V1: Regenerate stubs + rebuild contracts:**

```bash
cd /home/csillag/deai/optio
make codegen
pnpm --filter optio-contracts build
```

Expected: `packages/optio-core/src/optio_core/_generated/optio_engine.py` now has `type: Literal["client"]`; `packages/optio-contracts/dist/` regenerated.

- [ ] **Step V2: Python suites** (repo venv; MongoDB via Docker / mongodb-memory-server per the conftest):

```bash
.venv/bin/python -m pytest packages/optio-agents/tests -q
.venv/bin/python -m pytest packages/optio-core/tests -q
.venv/bin/python -m pytest packages/optio-opencode/tests -q
.venv/bin/python -m pytest packages/optio-claudecode/tests -q
.venv/bin/python -m pytest packages/optio-demo/tests -q  # if the package has tests
```

Expected: all pass. Known pre-existing flake: `test_cancel_shared_deadline_across_subtree` in optio-core (~1/5–10 runs, timing race) — re-run before suspecting a regression.

- [ ] **Step V3: TypeScript suites + typecheck:**

```bash
OPTIO_SKIP_PREFLIGHT_TESTS=1 pnpm -r test
pnpm -r --filter './packages/*' exec tsc --noEmit 2>/dev/null || pnpm -r build
```

Known pre-existing flake: optio-api fastify-widget-proxy WS tests under `pnpm -r` load — re-run in isolation (`pnpm --filter optio-api test`) before suspecting a regression.

- [ ] **Step V4: Residual-reference sweep** — must return ONLY hits in `docs/` (historical design docs + this plan/spec) and the two superseded-by notes:

```bash
grep -rn "DOMAIN_MESSAGE\|DomainMessageEvent\|domain_message\|onDomainMessage" \
  --include="*.py" --include="*.ts" --include="*.tsx" packages/ | grep -v node_modules
```

Expected: no output (dist/ and _generated/ were regenerated in V1).

- [ ] **Step V5: Commit** everything as one commit on `feature/message-split`:

```bash
git add -A
git commit -m "feat!: split DOMAIN_MESSAGE into CLIENT_MESSAGE and CALLER_MESSAGE

BREAKING CHANGE: the DOMAIN_MESSAGE log keyword is removed. CLIENT_MESSAGE
(browser-session frontend, opt-in via use_client_messages) and CALLER_MESSAGE
(embedding-app callback, opt-in via on_caller_message) replace it; the
downstream pipeline renames domain_message -> client_message, session-event
type \"domain\" -> \"client\", onDomainMessage -> onClientMessage."
```

Do NOT merge or push — integration is the user's call.
