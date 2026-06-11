# Opencode Conversation Mode Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Give optio-opencode a `mode="conversation"` that hands the caller a live `Conversation` object (shared optio-agents protocol) implemented as an HTTP+SSE client of the opencode server the task already spawns, plus an engine-neutral `optio-conversation-ui` widget package that renders both the claudecode and opencode conversation protocols.

**Architecture:** No new server component. The spawned opencode server (already password-authed, tunneled, with one pre-created session) is the single backend: the Python `OpencodeConversation` speaks its native HTTP+SSE API engine-side, and the conversation widget speaks the same native API through the optio-api widget proxy (exactly like iframe mode). Events pass through engine-native; only the protocol verbs and the permission request/decision shapes are normalized. Spec: `docs/2026-06-11-opencode-conversation-mode-design.md`.

**Tech Stack:** Python 3.11+ (asyncio, aiohttp client), React + TypeScript (vitest), pnpm workspace, pytest.

**Base:** branch `csillag/opencode-frontend` at `65753bdf838f0674cc652ed45362417205728140`.

---

## Pinned opencode wire facts (shipped server — same one `opencode web` runs)

Verified against opencode source at `/home/csillag/deai/opencode` (`packages/opencode/src/server/routes/instance/httpapi/groups/*.ts`, `packages/core/src/v1/session.ts`, `packages/opencode/src/session/status.ts`, `packages/opencode/src/permission/index.ts`):

| Operation | Wire |
|---|---|
| Live events | `GET /event?directory=<urlencoded>` — SSE; each frame `data:` is `{"id", "type", "properties"}`; first event `server.connected`; heartbeat `server.heartbeat` every 10s |
| Send prompt | `POST /session/{sid}/prompt_async?directory=…` body `{"parts":[{"type":"text","text":…}]}` → 204 |
| Interrupt | `POST /session/{sid}/abort?directory=…` body `{}` → boolean |
| List pending permissions | `GET /permission?directory=…` → array of PermissionV1.Request `{id, sessionID, permission, patterns, metadata, always, tool?}` |
| Answer permission | `POST /permission/{requestID}/reply?directory=…` body `{"reply":"once"\|"always"\|"reject","message"?}` → boolean |
| Message history | `GET /session/{sid}/message?directory=…` → array of `{info, parts}` (v1 message + parts) |
| Auth | Basic `opencode:<OPENCODE_SERVER_PASSWORD>` (what `launch_opencode` already sets) |

Event types relevant to a conversation (all carry `sessionID` in `properties` or nested objects):

- `message.updated` — `properties.info` is the v1 message info (`{id, sessionID, role: "user"|"assistant", time: {created, completed?}, …}`)
- `message.part.updated` — `properties.part` is a full part state: text part `{id, messageID, sessionID, type:"text", text}`, tool part `{id, messageID, sessionID, type:"tool", callID, tool, state:{status:"pending"|"running"|"completed"|"error", input?, …}}`
- `message.part.delta` — streaming text: `properties` `{sessionID, messageID, partID, delta}` (appended to the part's text)
- `session.status` — `properties` `{sessionID, status: {type:"busy"} | {type:"idle", …}}` (deprecated twin: `session.idle`)
- `permission.asked` — `properties` = PermissionV1.Request fields; `permission.replied` — `properties` `{sessionID, requestID, reply}`

Mapping decisions (from the spec): `PermissionDecision.behavior "allow"` → reply `"once"`; `"deny"` → reply `"reject"` (with `message`). `updated_input` has no opencode equivalent — ignored (documented). Task 8 captures real fixtures and re-verifies all of the above empirically before the UI adapter is built.

## Worktree setup (once, before Task 1)

```bash
cd /home/csillag/deai/optio/.claude/worktrees/csillag+opencode-frontend
python3 -m venv .venv && source .venv/bin/activate
pip install -e packages/optio-core -e packages/optio-host -e packages/optio-agents \
            -e packages/optio-claudecode -e packages/optio-opencode -e packages/optio-demo
pip install pytest pytest-asyncio aiohttp
pnpm install
```

Baseline check: `cd packages/optio-opencode && python -m pytest tests/test_types.py tests/test_prompt.py -q` → all pass.
All Python commands below run from `packages/optio-opencode` with the venv active unless stated otherwise.

---

### Task 1: Conversation config fields + validation

**Files:**
- Modify: `packages/optio-opencode/src/optio_opencode/types.py`
- Modify: `packages/optio-opencode/src/optio_opencode/__init__.py`
- Create: `packages/optio-opencode/tests/test_conversation_config.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_conversation_config.py`:

```python
"""OpencodeTaskConfig validation for conversation mode (mirrors claudecode)."""

import pytest

from optio_opencode.types import OpencodeTaskConfig


def _cfg(**kw):
    return OpencodeTaskConfig(consumer_instructions="do things", **kw)


def test_defaults_preserve_iframe_behavior():
    cfg = _cfg()
    assert cfg.mode == "iframe"
    assert cfg.host_protocol is True
    assert cfg.conversation_ui is False
    assert cfg.tool_verbosity == "description-only"


def test_mode_must_be_known():
    with pytest.raises(ValueError, match="mode="):
        _cfg(mode="tui")


def test_iframe_requires_host_protocol():
    with pytest.raises(ValueError, match="host_protocol=False requires"):
        _cfg(mode="iframe", host_protocol=False)


def test_conversation_allows_host_protocol_off():
    cfg = _cfg(mode="conversation", host_protocol=False)
    assert cfg.host_protocol is False


def test_conversation_ui_requires_conversation_mode():
    with pytest.raises(ValueError, match="conversation_ui=True requires"):
        _cfg(conversation_ui=True)
    cfg = _cfg(mode="conversation", conversation_ui=True)
    assert cfg.conversation_ui is True


def test_tool_verbosity_validated():
    with pytest.raises(ValueError, match="tool_verbosity"):
        _cfg(mode="conversation", tool_verbosity="chatty")


def test_empty_instructions_allowed_in_conversation_mode():
    cfg = OpencodeTaskConfig(consumer_instructions="", mode="conversation")
    assert cfg.consumer_instructions == ""
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_conversation_config.py -q`
Expected: FAIL — `TypeError: ... unexpected keyword argument 'mode'` (and similar).

- [ ] **Step 3: Implement**

In `src/optio_opencode/types.py`:

1. Extend the typing import (line 8 area) to include `Literal`:
```python
from typing import Any, Awaitable, Callable, Literal
```
2. After the `SeedProvider` alias, add:
```python
# Conversation-mode vocabulary (mirrors optio-claudecode).
ConversationMode = Literal["iframe", "conversation"]
ToolVerbosity = Literal["silent", "description-only", "verbose"]
_VALID_TOOL_VERBOSITY = {"silent", "description-only", "verbose"}
```
3. Add `"ConversationMode", "ToolVerbosity",` to `__all__`.
4. Add fields at the end of the dataclass (after `scrub_env`):
```python
    # --- conversation mode (mirrors optio-claudecode) ---
    # "iframe": today's behavior — embedded opencode web SPA, keyword-channel
    # completion. "conversation": generic gateway — the caller receives a live
    # Conversation (optio_agents.conversation) via ctx.publish_result().
    mode: ConversationMode = "iframe"
    # Keep the optio.log keyword channel running. May only be disabled in
    # conversation mode, where close() is the alternative completion signal.
    host_protocol: bool = True
    # Register the conversation widget (ui_widget="conversation"). Requires
    # mode="conversation". The opencode server itself is the widget upstream.
    conversation_ui: bool = False
    # Rendering hint forwarded to the widget via widgetData; only affects
    # conversation_ui rendering.
    tool_verbosity: ToolVerbosity = "description-only"
```
5. Append to `__post_init__` (after the encrypt/decrypt check):
```python
        if self.mode not in ("iframe", "conversation"):
            raise ValueError(
                f"OpencodeTaskConfig.mode={self.mode!r} is not one of "
                "['iframe', 'conversation']"
            )
        if self.mode == "iframe" and not self.host_protocol:
            raise ValueError(
                "OpencodeTaskConfig: host_protocol=False requires "
                "mode='conversation' (in iframe mode the optio.log keyword "
                "channel is the only completion signal)."
            )
        if self.conversation_ui and self.mode != "conversation":
            raise ValueError(
                "OpencodeTaskConfig: conversation_ui=True requires "
                "mode='conversation'."
            )
        if self.tool_verbosity not in _VALID_TOOL_VERBOSITY:
            raise ValueError(
                f"OpencodeTaskConfig.tool_verbosity={self.tool_verbosity!r} "
                f"is not one of {sorted(_VALID_TOOL_VERBOSITY)}"
            )
```
6. In `src/optio_opencode/__init__.py`, extend the `from optio_opencode.types import (...)` block with `ConversationMode,` and `ToolVerbosity,` and add both names to `__all__`.

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_conversation_config.py tests/test_types.py -q`
Expected: all PASS (test_types.py proves no regression).

- [ ] **Step 5: Commit**

```bash
git add packages/optio-opencode/src/optio_opencode/types.py \
        packages/optio-opencode/src/optio_opencode/__init__.py \
        packages/optio-opencode/tests/test_conversation_config.py
git commit -m "feat(optio-opencode): conversation-mode config surface"
```

### Task 2: Prompt composition for conversation mode

**Files:**
- Modify: `packages/optio-opencode/src/optio_opencode/prompt.py`
- Modify: `packages/optio-opencode/tests/test_prompt.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_prompt.py`:

```python
# --- conversation-mode composition -------------------------------------

from optio_opencode.prompt import DEFAULT_CONVERSATION_INSTRUCTIONS


def test_host_protocol_off_omits_keyword_docs():
    out = compose_agents_md(
        "talk to me", workdir_exclude=None, host_protocol=False,
    )
    assert "optio.log" not in out
    assert "DELIVERABLE" not in out
    assert "talk to me" in out


def test_host_protocol_off_resume_gains_system_explainer():
    out = compose_agents_md(
        "x", workdir_exclude=None, host_protocol=False, supports_resume=True,
    )
    assert "System:" in out          # the explainer
    assert "## Resumes" in out       # resume section retained


def test_omit_task_framing_drops_task_header():
    out = compose_agents_md(
        DEFAULT_CONVERSATION_INSTRUCTIONS,
        workdir_exclude=None, host_protocol=False, supports_resume=False,
        omit_task_framing=True,
    )
    assert "## Task" not in out
    assert out.rstrip().endswith(DEFAULT_CONVERSATION_INSTRUCTIONS)


def test_default_composition_unchanged():
    out = compose_agents_md("body", workdir_exclude=None)
    assert "## Task" in out
    assert "optio.log" in out
```

(`compose_agents_md` is already imported at the top of `test_prompt.py`; keep the existing import and add the `DEFAULT_CONVERSATION_INSTRUCTIONS` import as shown.)

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_prompt.py -q`
Expected: new tests FAIL — `TypeError: ... unexpected keyword argument 'host_protocol'` / ImportError for `DEFAULT_CONVERSATION_INSTRUCTIONS`. Pre-existing tests still pass.

- [ ] **Step 3: Implement**

In `src/optio_opencode/prompt.py`:

1. Add module-level constants (near `_OPENCODE_INTRO`):
```python
DEFAULT_CONVERSATION_INSTRUCTIONS = "Let's have a conversation with the user."

_SYSTEM_PREFIX_EXPLAINER = """
(Messages prefixed `System:` on your input channel originate from the
harness coordinating this session, not from the human user.)
"""
```
2. Add `"DEFAULT_CONVERSATION_INSTRUCTIONS",` to the module `__all__` (create the entry if `__all__` lists exports explicitly).
3. Replace the tail of `compose_agents_md` (the body after the docstring) with:
```python
    if host_protocol:
        if documentation is None:
            documentation = build_log_channel_prompt("suppress")
        base_prompt_pre = _OPENCODE_INTRO + documentation
    else:
        # Conversation mode without the optio.log channel: no keyword docs,
        # and the intro that frames them is dropped too.
        base_prompt_pre = ""
    if supports_resume:
        resume_block = _render_resume_section(workdir_exclude)
        if not host_protocol:
            # The protocol docs normally explain the `System:` convention;
            # without them the resume section carries its own explainer.
            resume_block = resume_block + _SYSTEM_PREFIX_EXPLAINER
        resume_block = resume_block + "\n"
    else:
        resume_block = ""
    body = consumer_instructions.rstrip()
    pre = f"{base_prompt_pre}\n" if base_prompt_pre else ""
    if omit_task_framing:
        return f"{pre}{resume_block}{body}\n"
    return f"{pre}{resume_block}{BASE_PROMPT_POST}\n{body}\n"
```
and extend the signature with the two new keyword-only params (defaults preserve current output byte-for-byte):
```python
def compose_agents_md(
    consumer_instructions: str,
    *,
    workdir_exclude: list[str] | None,
    documentation: str | None = None,
    supports_resume: bool = True,
    host_protocol: bool = True,
    omit_task_framing: bool = False,
) -> str:
```
Update the docstring with the two new params (same wording as claudecode's `prompt.py:126-151`).

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_prompt.py -q`
Expected: all PASS (including pre-existing composition tests — the refactor must not change default output).

- [ ] **Step 5: Commit**

```bash
git add packages/optio-opencode/src/optio_opencode/prompt.py \
        packages/optio-opencode/tests/test_prompt.py
git commit -m "feat(optio-opencode): conversation-mode prompt composition"
```

### Task 3: `OpencodeConversation` driver

**Files:**
- Modify: `packages/optio-opencode/pyproject.toml` (add `"aiohttp>=3.9",` to dependencies — same pin as optio-claudecode)
- Create: `packages/optio-opencode/src/optio_opencode/conversation.py`
- Create: `packages/optio-opencode/tests/test_conversation_driver.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_conversation_driver.py`. The fixture is an in-process aiohttp web app faking the opencode server surface (SSE `/event`, `prompt_async`, `abort`, `/permission` list+reply):

```python
"""OpencodeConversation unit tests against an in-process fake opencode server."""

import asyncio
import json

import pytest
import pytest_asyncio
from aiohttp import web

from optio_agents.conversation import (
    Conversation,
    ConversationClosed,
    PermissionDecision,
)
from optio_opencode.conversation import OpencodeConversation

SID = "ses_test"


class FakeServer:
    """Minimal opencode-server fake: scripted SSE events + request journal."""

    def __init__(self):
        self.journal: list[tuple[str, dict]] = []
        self.events: asyncio.Queue = asyncio.Queue()
        self.pending_permissions: list[dict] = []
        self.app = web.Application()
        self.app.router.add_get("/event", self._event)
        self.app.router.add_post("/session/{sid}/prompt_async", self._prompt)
        self.app.router.add_post("/session/{sid}/abort", self._abort)
        self.app.router.add_get("/permission", self._perm_list)
        self.app.router.add_post("/permission/{rid}/reply", self._perm_reply)
        self.runner: web.AppRunner | None = None
        self.port: int | None = None

    async def start(self):
        self.runner = web.AppRunner(self.app)
        await self.runner.setup()
        site = web.TCPSite(self.runner, "127.0.0.1", 0)
        await site.start()
        self.port = site._server.sockets[0].getsockname()[1]

    async def stop(self):
        await self.runner.cleanup()

    def emit(self, type_: str, properties: dict):
        self.events.put_nowait({"id": f"evt_{type_}", "type": type_, "properties": properties})

    async def _event(self, request):
        resp = web.StreamResponse(headers={"content-type": "text/event-stream"})
        await resp.prepare(request)
        await resp.write(b'data: {"id":"evt_0","type":"server.connected","properties":{}}\n\n')
        try:
            while True:
                ev = await self.events.get()
                await resp.write(f"data: {json.dumps(ev)}\n\n".encode())
        except (asyncio.CancelledError, ConnectionResetError):
            return resp

    async def _prompt(self, request):
        self.journal.append(("prompt", await request.json()))
        return web.Response(status=204)

    async def _abort(self, request):
        self.journal.append(("abort", {}))
        return web.json_response(True)

    async def _perm_list(self, request):
        return web.json_response(self.pending_permissions)

    async def _perm_reply(self, request):
        self.journal.append(
            ("perm_reply", {"rid": request.match_info["rid"], **(await request.json())})
        )
        return web.json_response(True)


@pytest_asyncio.fixture
async def server():
    s = FakeServer()
    await s.start()
    yield s
    await s.stop()


@pytest_asyncio.fixture
async def conv(server):
    c = OpencodeConversation(
        port=server.port, password="pw", session_id=SID, directory="/work",
    )
    reader = asyncio.create_task(c.run_reader())
    await asyncio.sleep(0.05)  # let the SSE connect
    yield c
    reader.cancel()
    try:
        await reader
    except asyncio.CancelledError:
        pass


def test_implements_protocol():
    c = OpencodeConversation(port=1, password="x", session_id=SID, directory="/w")
    assert isinstance(c, Conversation)


async def test_send_posts_prompt_async(conv, server):
    await conv.send("hello")
    await asyncio.sleep(0.05)
    assert ("prompt", {"parts": [{"type": "text", "text": "hello"}]}) in server.journal


async def test_on_event_is_raw_passthrough(conv, server):
    seen: list[dict] = []
    conv.on_event(seen.append)
    server.emit("message.part.delta",
                {"sessionID": SID, "messageID": "m1", "partID": "p1", "delta": "He"})
    await asyncio.sleep(0.05)
    assert any(e.get("type") == "message.part.delta" for e in seen)
    raw = next(e for e in seen if e.get("type") == "message.part.delta")
    assert raw["properties"]["delta"] == "He"  # unmodified native payload


async def test_on_message_fires_on_completed_assistant_message(conv, server):
    msgs: list[str] = []
    conv.on_message(msgs.append)
    server.emit("message.part.updated",
                {"part": {"id": "p1", "messageID": "m1", "sessionID": SID,
                          "type": "text", "text": "final answer"}})
    server.emit("message.updated",
                {"info": {"id": "m1", "sessionID": SID, "role": "assistant",
                          "time": {"created": 1, "completed": 2}}})
    await asyncio.sleep(0.05)
    assert msgs == ["final answer"]


async def test_busy_tracking_via_session_status(conv, server):
    assert conv.is_pending() is False
    await conv.send("q")
    assert conv.is_pending() is True
    server.emit("session.status", {"sessionID": SID, "status": {"type": "idle"}})
    await asyncio.sleep(0.05)
    assert conv.is_pending() is False


async def test_interrupt_posts_abort(conv, server):
    await conv.send("q")
    await conv.interrupt()
    await asyncio.sleep(0.05)
    assert ("abort", {}) in server.journal


async def test_permission_flow_allow_maps_to_once(conv, server):
    async def handler(req):
        assert req.tool_name == "bash"
        assert req.raw["id"] == "per_1"
        return PermissionDecision(behavior="allow")

    conv.on_permission_request(handler)
    server.emit("permission.asked",
                {"id": "per_1", "sessionID": SID, "permission": "bash",
                 "patterns": [], "metadata": {}, "always": []})
    await asyncio.sleep(0.1)
    assert ("perm_reply", {"rid": "per_1", "reply": "once"}) in server.journal


async def test_permission_deny_maps_to_reject_with_message(conv, server):
    async def handler(req):
        return PermissionDecision(behavior="deny", message="nope")

    conv.on_permission_request(handler)
    server.emit("permission.asked",
                {"id": "per_2", "sessionID": SID, "permission": "bash",
                 "patterns": [], "metadata": {}, "always": []})
    await asyncio.sleep(0.1)
    assert ("perm_reply", {"rid": "per_2", "reply": "reject", "message": "nope"}) in server.journal


async def test_permission_asked_before_handler_is_queued(conv, server):
    server.emit("permission.asked",
                {"id": "per_3", "sessionID": SID, "permission": "bash",
                 "patterns": [], "metadata": {}, "always": []})
    await asyncio.sleep(0.05)

    async def handler(req):
        return PermissionDecision(behavior="allow")

    conv.on_permission_request(handler)
    await asyncio.sleep(0.1)
    assert ("perm_reply", {"rid": "per_3", "reply": "once"}) in server.journal


async def test_pending_permissions_swept_on_connect(server):
    server.pending_permissions = [
        {"id": "per_old", "sessionID": SID, "permission": "bash",
         "patterns": [], "metadata": {}, "always": []},
    ]
    c = OpencodeConversation(
        port=server.port, password="pw", session_id=SID, directory="/work",
    )

    async def handler(req):
        return PermissionDecision(behavior="allow")

    c.on_permission_request(handler)
    reader = asyncio.create_task(c.run_reader())
    await asyncio.sleep(0.15)
    reader.cancel()
    try:
        await reader
    except asyncio.CancelledError:
        pass
    assert ("perm_reply", {"rid": "per_old", "reply": "once"}) in server.journal


async def test_close_sets_close_requested(conv):
    await conv.close()
    assert conv.close_requested.is_set()
    assert conv.closed is False  # close() requests; _finish() concludes


async def test_finish_emits_x_optio_closed_and_send_raises(conv, server):
    seen: list[dict] = []
    conv.on_event(seen.append)
    await conv._finish("test over")  # what the session body's finally does
    assert conv.closed is True
    assert {"type": "x-optio-closed", "reason": "test over"} in seen
    with pytest.raises(ConversationClosed):
        await conv.send("more")
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_conversation_driver.py -q`
Expected: FAIL at import — `ModuleNotFoundError: No module named 'optio_opencode.conversation'`.

- [ ] **Step 3: Implement**

Add `"aiohttp>=3.9",` to `[project] dependencies` in `packages/optio-opencode/pyproject.toml`, then `pip install -e packages/optio-opencode` (from the worktree root) to pick it up.

Create `src/optio_opencode/conversation.py`:

```python
"""OpencodeConversation — engine-side driver for one opencode session over
the spawned server's native HTTP+SSE API.

The session body launches the opencode server (``launch_opencode``),
pre-creates a session, constructs this object with the same
``(worker_port, password, session_id)`` it already produces, publishes it via
``ctx.publish_result``, and runs ``run_reader()`` until teardown.

Event payloads are transparent: every SSE frame is fanned out to ``on_event``
subscribers as a dict, unmodified (``{"id", "type", "properties"}``).
Synthetic events use the ``x-optio-`` type prefix. Permission requests are
event-driven (``permission.asked``) with a list-endpoint sweep on every SSE
(re)connect, so requests that fired during a stream gap are never lost.

See docs/2026-06-11-opencode-conversation-mode-design.md.
"""

from __future__ import annotations

import asyncio
import json
import logging

import aiohttp

from optio_agents.conversation import (
    ConversationClosed,
    PermissionDecision,
    PermissionRequest,
)

_LOG = logging.getLogger(__name__)

# Reconnect backoff for the SSE reader (capped; the session body cancels the
# reader at teardown, so there is no give-up path while the task is alive).
_RECONNECT_DELAYS = (0.2, 0.5, 1.0, 2.0, 5.0)


class OpencodeConversation:
    """Implements optio_agents.conversation.Conversation for opencode."""

    def __init__(
        self, *, port: int, password: str, session_id: str, directory: str,
    ) -> None:
        self._base = f"http://127.0.0.1:{port}"
        self._auth = aiohttp.BasicAuth("opencode", password)
        self._session_id = session_id
        self._directory = directory
        self._pending = False
        self._closed = asyncio.Event()
        self._close_reason: str | None = None
        # Cooperative-shutdown request towards the owning task body.
        self.close_requested = asyncio.Event()
        self._event_queue: asyncio.Queue[dict] = asyncio.Queue()
        self._event_handlers: list = []
        self._message_handlers: list = []
        self._permission_handler = None
        self._queued_permission_requests: list[dict] = []
        self._answered_permissions: set[str] = set()
        # Text parts of the in-flight assistant message, keyed by part id —
        # joined and fired via on_message when the message completes.
        self._part_texts: dict[str, dict[str, str]] = {}
        self._dispatcher_task: asyncio.Task | None = None
        self._http: aiohttp.ClientSession | None = None

    # -- wiring ------------------------------------------------------------

    async def run_reader(self) -> None:
        """Connect to /event and dispatch frames until cancelled (by the
        session body at teardown) or closed. Reconnects with backoff."""
        self._dispatcher_task = asyncio.create_task(self._dispatch_loop())
        self._http = aiohttp.ClientSession(auth=self._auth)
        attempt = 0
        try:
            while not self._closed.is_set():
                try:
                    await self._consume_sse()
                    attempt = 0  # clean EOF: server still alive, reconnect fresh
                except (aiohttp.ClientError, ConnectionError, asyncio.TimeoutError) as exc:
                    _LOG.info("conversation: SSE drop (%s); reconnecting", exc)
                delay = _RECONNECT_DELAYS[min(attempt, len(_RECONNECT_DELAYS) - 1)]
                attempt += 1
                await asyncio.sleep(delay)
        finally:
            await self._finish("process ended")
            await self._http.close()

    def _url(self, path: str) -> str:
        return f"{self._base}{path}"

    def _params(self) -> dict:
        return {"directory": self._directory}

    async def _consume_sse(self) -> None:
        timeout = aiohttp.ClientTimeout(total=None, sock_connect=10)
        async with self._http.get(
            self._url("/event"), params=self._params(), timeout=timeout,
        ) as resp:
            resp.raise_for_status()
            # A (re)connect can postdate permission.asked events we never saw.
            await self._sweep_permissions()
            data_lines: list[str] = []
            async for raw in resp.content:
                line = raw.decode("utf-8", errors="replace").rstrip("\n").rstrip("\r")
                if line.startswith("data:"):
                    data_lines.append(line[5:].lstrip())
                    continue
                if line == "" and data_lines:
                    payload = "\n".join(data_lines)
                    data_lines = []
                    try:
                        obj = json.loads(payload)
                    except ValueError:
                        _LOG.warning("conversation: unparseable SSE data: %.200s", payload)
                        self._event_queue.put_nowait(
                            {"type": "x-optio-unparseable", "line": payload},
                        )
                        continue
                    self._route(obj)

    # -- event routing -------------------------------------------------------

    def _for_this_session(self, props: dict) -> bool:
        sid = (
            props.get("sessionID")
            or (props.get("info") or {}).get("sessionID")
            or (props.get("part") or {}).get("sessionID")
        )
        return sid is None or sid == self._session_id

    def _route(self, obj: dict) -> None:
        t = obj.get("type") or ""
        props = obj.get("properties") or {}
        if t == "permission.asked" and self._for_this_session(props):
            self._on_permission_asked(props)
        elif t == "message.part.updated":
            part = props.get("part") or {}
            if part.get("type") == "text" and self._for_this_session(props):
                mid, pid = str(part.get("messageID")), str(part.get("id"))
                self._part_texts.setdefault(mid, {})[pid] = part.get("text") or ""
        elif t == "message.updated":
            info = props.get("info") or {}
            if (
                info.get("role") == "assistant"
                and (info.get("time") or {}).get("completed")
                and self._for_this_session(props)
            ):
                parts = self._part_texts.pop(str(info.get("id")), {})
                if parts:
                    self._fire_message("\n\n".join(parts.values()))
        elif t in ("session.status", "session.idle") and self._for_this_session(props):
            status = props.get("status") or {}
            if t == "session.idle" or status.get("type") == "idle":
                self._pending = False
            elif status.get("type") == "busy":
                self._pending = True
        self._event_queue.put_nowait(obj)

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

    # -- permission gate -------------------------------------------------------

    def _on_permission_asked(self, props: dict) -> None:
        rid = str(props.get("id") or "")
        if not rid or rid in self._answered_permissions:
            return
        if self._permission_handler is None:
            # Queue until a handler is registered: opencode blocks the session
            # on the unanswered ask, which closes the publish/registration
            # race. Documented caller contract: register promptly.
            self._queued_permission_requests.append(props)
            return
        asyncio.ensure_future(self._answer_permission(props))

    async def _sweep_permissions(self) -> None:
        """Fetch pending permission requests and feed unanswered ones for our
        session to the gate. Gap-safety: covers asks fired while the SSE
        stream was down (opencode's /event has no server-side replay)."""
        try:
            async with self._http.get(
                self._url("/permission"), params=self._params(),
            ) as resp:
                resp.raise_for_status()
                pending = await resp.json()
        except (aiohttp.ClientError, ConnectionError, ValueError) as exc:
            _LOG.warning("conversation: permission sweep failed: %s", exc)
            return
        for props in pending:
            if props.get("sessionID") in (None, self._session_id):
                self._on_permission_asked(props)

    async def _answer_permission(self, props: dict) -> None:
        rid = str(props.get("id"))
        if rid in self._answered_permissions:
            return
        self._answered_permissions.add(rid)
        request = PermissionRequest(
            tool_name=str(props.get("permission") or ""),
            input=props.get("metadata") or {},
            raw=props,
        )
        try:
            decision = await self._permission_handler(request)
        except Exception:  # noqa: BLE001
            _LOG.exception("conversation: permission handler raised; denying")
            decision = PermissionDecision(
                behavior="deny", message="optio harness: permission handler failed",
            )
        # opencode reply vocabulary: allow → "once" (never "always": the optio
        # gate decides per request); deny → "reject". updated_input has no
        # opencode equivalent and is ignored.
        body: dict = (
            {"reply": "once"} if decision.behavior == "allow"
            else {"reply": "reject", "message": decision.message or "Denied by the operator."}
        )
        try:
            async with self._http.post(
                self._url(f"/permission/{rid}/reply"),
                params=self._params(), json=body,
            ) as resp:
                if resp.status >= 400:
                    _LOG.warning(
                        "conversation: permission reply %s → HTTP %s "
                        "(likely already answered elsewhere)", rid, resp.status,
                    )
        except (aiohttp.ClientError, ConnectionError) as exc:
            _LOG.warning("conversation: permission reply failed: %s", exc)
        self._event_queue.put_nowait({
            "type": "x-optio-permission-answered",
            "request_id": rid,
            "behavior": decision.behavior,
        })

    # -- Conversation protocol surface --------------------------------------

    async def send(self, text: str) -> None:
        if self._closed.is_set():
            raise ConversationClosed(self._close_reason or "conversation closed")
        self._pending = True
        try:
            async with self._http.post(
                self._url(f"/session/{self._session_id}/prompt_async"),
                params=self._params(),
                json={"parts": [{"type": "text", "text": text}]},
            ) as resp:
                resp.raise_for_status()
        except (aiohttp.ClientError, ConnectionError) as exc:
            self._pending = False
            raise ConversationClosed(f"send failed: {exc}") from exc

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
        for props in queued:
            asyncio.ensure_future(self._answer_permission(props))

        def _unsub() -> None:
            if self._permission_handler is handler:
                self._permission_handler = None
        return _unsub

    def is_pending(self) -> bool:
        return self._pending

    async def interrupt(self) -> None:
        if self._closed.is_set():
            raise ConversationClosed(self._close_reason or "conversation closed")
        if not self._pending:
            return
        async with self._http.post(
            self._url(f"/session/{self._session_id}/abort"),
            params=self._params(), json={},
        ) as resp:
            resp.raise_for_status()

    async def close(self) -> None:
        self.close_requested.set()

    @property
    def closed(self) -> bool:
        return self._closed.is_set()

    # -- internals -----------------------------------------------------------

    async def _finish(self, reason: str) -> None:
        if self._closed.is_set():
            return
        self._closed.set()
        self._close_reason = reason
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

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_conversation_driver.py -q`
Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add packages/optio-opencode/pyproject.toml \
        packages/optio-opencode/src/optio_opencode/conversation.py \
        packages/optio-opencode/tests/test_conversation_driver.py
git commit -m "feat(optio-opencode): OpencodeConversation HTTP+SSE driver"
```

### Task 4: fake_opencode conversation surface

**Files:**
- Modify: `packages/optio-opencode/tests/fake_opencode.py`

The session-level tests (Tasks 5–7) run `run_opencode_session` against the scripted `fake_opencode.py` subprocess; `OpencodeConversation` will connect to it, so it needs `/event` (SSE), `/abort`, `/permission`, and a journal of received POSTs that tests can assert on.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_conversation_driver.py` (reusing the real driver against the subprocess fake exercises the same surface the session tests will rely on):

```python
# --- fake_opencode.py subprocess parity --------------------------------

import os
import subprocess
import sys

FAKE = os.path.join(os.path.dirname(__file__), "fake_opencode.py")


@pytest_asyncio.fixture
async def fake_proc(tmp_path):
    proc = subprocess.Popen(
        [sys.executable, FAKE, "--port", "0", "--scenario", "conversation"],
        stdout=subprocess.PIPE, cwd=tmp_path, text=True,
    )
    line = proc.stdout.readline()  # "Listening on http://127.0.0.1:<port>/"
    port = int(line.rsplit(":", 1)[1].strip().rstrip("/"))
    yield port, tmp_path
    proc.terminate()
    proc.wait(timeout=5)


async def test_driver_against_fake_opencode_subprocess(fake_proc):
    port, workdir = fake_proc
    c = OpencodeConversation(
        port=port, password="pw", session_id="fake-session-id", directory=str(workdir),
    )
    seen: list[dict] = []
    c.on_event(seen.append)
    reader = asyncio.create_task(c.run_reader())
    await asyncio.sleep(0.3)  # scenario emits its scripted events
    await c.send("hi there")
    await c.interrupt()
    await asyncio.sleep(0.2)
    reader.cancel()
    try:
        await reader
    except asyncio.CancelledError:
        pass
    # Scenario's scripted event arrived over SSE:
    assert any(e.get("type") == "message.part.delta" for e in seen)
    # The fake journaled our POSTs:
    journal = (workdir / "conv_journal.jsonl").read_text().splitlines()
    kinds = [json.loads(l)["kind"] for l in journal]
    assert "prompt_async" in kinds and "abort" in kinds
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_conversation_driver.py::test_driver_against_fake_opencode_subprocess -q`
Expected: FAIL — unknown scenario "conversation" / no SSE events / missing `conv_journal.jsonl`.

- [ ] **Step 3: Implement in `tests/fake_opencode.py`**

Three additions, following the file's existing socket-server style:

1. **Journal helper** (top-level, near the existing file-write helpers):
```python
def _journal(kind: str, payload: dict) -> None:
    with open("conv_journal.jsonl", "a", encoding="utf-8") as f:
        f.write(json.dumps({"kind": kind, **payload}) + "\n")
```
2. **New scenario + `conv_event` op.** Add to the scenario dict:
```python
"conversation": [
    ("sleep", 0.1),
    ("conv_event", {"type": "message.part.delta",
                    "properties": {"sessionID": "fake-session-id",
                                   "messageID": "m1", "partID": "p1",
                                   "delta": "Hello"}}),
    ("conv_event", {"type": "message.part.updated",
                    "properties": {"part": {"id": "p1", "messageID": "m1",
                                            "sessionID": "fake-session-id",
                                            "type": "text", "text": "Hello from fake"}}}),
    ("conv_event", {"type": "message.updated",
                    "properties": {"info": {"id": "m1",
                                            "sessionID": "fake-session-id",
                                            "role": "assistant",
                                            "time": {"created": 1, "completed": 2}}}}),
    ("conv_event", {"type": "session.status",
                    "properties": {"sessionID": "fake-session-id",
                                   "status": {"type": "idle"}}}),
    ("sleep", 3600),  # hold the server open; tests terminate the process
],
```
and in the scenario runner add the op:
```python
elif op[0] == "conv_event":
    CONV_EVENTS.append(op[1])
```
with module-level `CONV_EVENTS: list[dict] = []`.
3. **HTTP endpoints.** In the request handler (where `POST /session` and `prompt_async` are matched), add:
   - `GET /event` (path match before the catch-all): respond with `text/event-stream` headers, write `data: {"id":"evt_0","type":"server.connected","properties":{}}\n\n`, then loop: drain any new entries from `CONV_EVENTS` (tracking a per-connection index) as `data: <json>\n\n` frames, sleeping 0.05s between polls, until the socket closes. Follow the file's existing chunked/raw socket write style.
   - `POST /session/<sid>/prompt_async`: keep the existing 200 response but call `_journal("prompt_async", {"sid": sid, "body": parsed_body})` first.
   - `POST /session/<sid>/abort`: `_journal("abort", {"sid": sid})`, respond `200` with body `true` (content-type `application/json`).
   - `GET /permission`: respond `200` with body `json.dumps(PENDING_PERMISSIONS)` (module-level `PENDING_PERMISSIONS: list[dict] = []`, scenario-settable via a new `("permission_pending", {...})` op that appends to it).
   - `POST /permission/<rid>/reply`: `_journal("perm_reply", {"rid": rid, "body": parsed_body})`, remove the matching entry from `PENDING_PERMISSIONS`, emit a `permission.replied` conv-event (`CONV_EVENTS.append({"type": "permission.replied", "properties": {"sessionID": "fake-session-id", "requestID": rid, "reply": parsed_body.get("reply")}})`), respond `200` body `true`.

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_conversation_driver.py -q`
Expected: all PASS (in-process fake tests and the new subprocess parity test).

- [ ] **Step 5: Commit**

```bash
git add packages/optio-opencode/tests/fake_opencode.py \
        packages/optio-opencode/tests/test_conversation_driver.py
git commit -m "test(optio-opencode): conversation surface on fake_opencode"
```

### Task 5: Conversation-mode session body

**Files:**
- Modify: `packages/optio-opencode/src/optio_opencode/session.py`
- Create: `packages/optio-opencode/tests/test_conversation_session.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_conversation_session.py`. Reuse the fixtures from `test_session_local.py` — import them (`from tests.test_session_local import ...` is not the repo style; instead copy the `ctx_and_captures` + `_supply_scenario` fixture pattern, which `test_session_hooks.py` already does — follow that file's exact import/copy approach). Tests:

```python
"""Conversation-mode session body: publish_result, close-driven teardown,
keywords on/off, opencode.json question-tool merge."""

import asyncio
import json

import pytest

from optio_opencode import OpencodeTaskConfig
from optio_opencode.session import run_opencode_session

# ctx_and_captures / _supply_scenario fixtures: same pattern as
# tests/test_session_hooks.py (copied from test_session_local.py).
# _supply_scenario must point the fake at scenario "conversation".


async def _launch(ctx, cfg):
    """Run the session as a task; wait until publish_result was called."""
    sess = asyncio.create_task(run_opencode_session(ctx, cfg))
    for _ in range(200):
        if ctx.published_results:           # captured by the fixture wrapper
            return sess, ctx.published_results[0]
        await asyncio.sleep(0.05)
    sess.cancel()
    raise AssertionError("conversation was never published")


async def test_conversation_published_and_close_ends_session(ctx_and_captures, _supply_scenario):
    ctx, cap, _ = ctx_and_captures
    _supply_scenario["name"] = "conversation"
    cfg = OpencodeTaskConfig(
        consumer_instructions="", mode="conversation", host_protocol=False,
    )
    sess, conv = await _launch(ctx, cfg)
    assert not conv.closed
    await conv.close()
    await asyncio.wait_for(sess, timeout=30)
    assert conv.closed                      # _finish ran during teardown


async def test_conversation_with_host_protocol_done_keyword_also_ends(ctx_and_captures, _supply_scenario):
    ctx, cap, _ = ctx_and_captures
    _supply_scenario["name"] = "conversation_then_done"   # scenario emits DONE after a delay
    cfg = OpencodeTaskConfig(
        consumer_instructions="chat", mode="conversation", host_protocol=True,
    )
    sess, conv = await _launch(ctx, cfg)
    await asyncio.wait_for(sess, timeout=30)              # DONE from the keyword channel ends it
    assert conv.closed


async def test_question_tool_disabled_in_conversation_opencode_json(ctx_and_captures, _supply_scenario, tmp_workdir_peek):
    ctx, cap, _ = ctx_and_captures
    _supply_scenario["name"] = "conversation"
    cfg = OpencodeTaskConfig(
        consumer_instructions="", mode="conversation", host_protocol=False,
        opencode_config={"theme": "dark", "tools": {"webfetch": True}},
    )
    sess, conv = await _launch(ctx, cfg)
    written = json.loads(tmp_workdir_peek("opencode.json"))
    assert written["tools"] == {"webfetch": True, "question": False}
    assert written["theme"] == "dark"
    await conv.close()
    await asyncio.wait_for(sess, timeout=30)


async def test_iframe_mode_opencode_json_untouched(ctx_and_captures, _supply_scenario, tmp_workdir_peek):
    ctx, cap, _ = ctx_and_captures
    _supply_scenario["name"] = "happy"
    cfg = OpencodeTaskConfig(consumer_instructions="task", opencode_config={"theme": "dark"})
    await run_opencode_session(ctx, cfg)
    assert json.loads(tmp_workdir_peek("opencode.json")) == {"theme": "dark"}


async def test_premature_server_exit_fails_session(ctx_and_captures, _supply_scenario):
    ctx, cap, _ = ctx_and_captures
    _supply_scenario["name"] = "conversation_early_exit"  # scenario: short sleep then ("exit", 1)
    cfg = OpencodeTaskConfig(
        consumer_instructions="", mode="conversation", host_protocol=False,
    )
    sess, conv = await _launch(ctx, cfg)
    with pytest.raises(Exception):
        await asyncio.wait_for(sess, timeout=30)
    assert conv.closed
```

Fixture additions this file needs (define locally in the test module):
- `ctx.published_results`: extend the `ctx_and_captures` wrapper pattern with `orig_publish = ctx.publish_result; ctx.published_results = []` and a wrapper appending the object.
- `tmp_workdir_peek(name)`: helper closure returning `open(os.path.join(workdir, name)).read()` — the fake-launch monkeypatch already pins the task workdir; mirror how `test_session_local.py` locates it.
- Two new fake scenarios (add in `tests/fake_opencode.py` in this task): `"conversation_then_done"` = the `"conversation"` script with `("sleep", 0.5), ("log", "DONE: chat over")` replacing the final hold; `"conversation_early_exit"` = `[("sleep", 0.3), ("exit", 1)]`.

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_conversation_session.py -q`
Expected: FAIL — session runs the iframe body (no publish_result; `published_results` stays empty → AssertionError "conversation was never published").

- [ ] **Step 3: Implement in `src/optio_opencode/session.py`**

1. New imports: `from optio_opencode.conversation import OpencodeConversation` and `from optio_opencode.prompt import DEFAULT_CONVERSATION_INSTRUCTIONS` (extend the existing `from optio_opencode.prompt import compose_agents_md` line).
2. **Instructions defaulting** — at the top of `run_opencode_session`, after `protocol = get_protocol(...)`:
```python
    instructions = config.consumer_instructions
    omit_task_framing = False
    if config.mode == "conversation" and not instructions:
        instructions = DEFAULT_CONVERSATION_INSTRUCTIONS
        omit_task_framing = True
```
3. **AGENTS.md composition** (fresh-start branch in `_opencode_body`, currently `compose_agents_md(config.consumer_instructions, documentation=protocol.documentation, ...)`):
```python
            await host.write_text(
                "AGENTS.md",
                compose_agents_md(
                    instructions,
                    documentation=protocol.documentation if config.host_protocol else None,
                    workdir_exclude=config.workdir_exclude,
                    supports_resume=config.supports_resume,
                    host_protocol=config.host_protocol,
                    omit_task_framing=omit_task_framing,
                ),
            )
```
4. **opencode.json question-tool merge** (same branch, replacing the plain `json.dumps(config.opencode_config, ...)`):
```python
            opencode_cfg = dict(config.opencode_config)
            if config.mode == "conversation":
                # Questions (multi-choice asks) have no conversation-mode
                # answering path yet — disable the tool so a session can
                # never block on one. See design doc "Non-goals".
                opencode_cfg["tools"] = {**opencode_cfg.get("tools", {}), "question": False}
            await host.write_text(
                "opencode.json", json.dumps(opencode_cfg, indent=2),
            )
```
5. **Conversation wiring + wait loop** — in `_opencode_body`, replace the final `proc = launched_handle.pid_like / await proc.wait()` block with a mode fork. Declare `nonlocal conversation, reader_task` (new enclosing-scope vars initialized `None` next to `launched_handle`):
```python
        if config.mode != "conversation":
            # --- await opencode subprocess exit (iframe mode, unchanged) ---
            proc = launched_handle.pid_like
            await proc.wait()  # type: ignore[union-attr]
            return

        # --- conversation mode: publish the gateway, then wait on
        # close-requested vs. process exit (mirrors optio-claudecode).
        conversation = OpencodeConversation(
            port=worker_port, password=password,
            session_id=session_id, directory=host.workdir,
        )
        reader_task = asyncio.create_task(conversation.run_reader())
        ctx.publish_result(conversation)
        ctx.report_progress(None, "opencode conversation is live")

        proc = launched_handle.pid_like
        wait_task = asyncio.create_task(proc.wait())  # type: ignore[union-attr]
        close_task = asyncio.create_task(conversation.close_requested.wait())
        try:
            done, _ = await asyncio.wait(
                {wait_task, close_task}, return_when=asyncio.FIRST_COMPLETED,
            )
            if close_task in done and wait_task not in done:
                # Caller asked to close: cooperative shutdown, clean end.
                wait_task.cancel()
                if config.host_protocol:
                    # The keyword driver treats a body return without DONE as
                    # a premature exit. A caller-requested close IS the clean
                    # end, so emit the DONE ourselves and park until the
                    # driver observes it and cancels us (claudecode parity).
                    log_path = f"{host.workdir}/optio.log"
                    await host.run_command(f"echo DONE >> {shlex.quote(log_path)}")
                    await asyncio.Event().wait()  # cancelled by the driver
                return
            # Server exited on its own.
            close_task.cancel()
            if not conversation.close_requested.is_set() and ctx.should_continue():
                raise RuntimeError("opencode exited unexpectedly")
        finally:
            reader_task.cancel()
            try:
                await reader_task
            except asyncio.CancelledError:
                pass
```
(`shlex` is already imported in this module via host quoting; add `import shlex` if not.)
6. **Widget suppression for now**: in conversation mode, skip the iframe `set_widget_upstream`/`set_widget_data` block entirely (wrap the existing block in `if config.mode != "conversation":`). Task 6 adds the conversation-widget variant.
7. **Driver keywords flag** — the `run_log_protocol_session(...)` call gains `keywords=config.host_protocol`.
8. **`create_opencode_task` ui_widget** (claudecode parity):
```python
        ui_widget=(
            "iframe" if config.mode == "iframe"
            else ("conversation" if config.conversation_ui else None)
        ),
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_conversation_session.py tests/test_session_local.py tests/test_session_hooks.py -q`
Expected: all PASS (iframe-mode suites prove no regression).

- [ ] **Step 5: Commit**

```bash
git add packages/optio-opencode/src/optio_opencode/session.py \
        packages/optio-opencode/tests/test_conversation_session.py \
        packages/optio-opencode/tests/fake_opencode.py
git commit -m "feat(optio-opencode): conversation-mode session body"
```

### Task 6: conversation_ui widget wiring

**Files:**
- Modify: `packages/optio-opencode/src/optio_opencode/session.py`
- Create: `packages/optio-opencode/tests/test_conversation_ui_session.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_conversation_ui_session.py` (same fixture pattern as Task 5):

```python
"""conversation_ui=True wiring: ui_widget type, upstream, widgetData."""

import asyncio

from optio_core import BasicAuth  # match the import used in session.py
from optio_opencode import OpencodeTaskConfig, create_opencode_task


def test_create_opencode_task_ui_widget_matrix():
    base = dict(consumer_instructions="x")
    t_iframe = create_opencode_task("p1", "n", OpencodeTaskConfig(**base))
    assert t_iframe.ui_widget == "iframe"
    t_conv = create_opencode_task(
        "p2", "n", OpencodeTaskConfig(**base, mode="conversation", conversation_ui=True),
    )
    assert t_conv.ui_widget == "conversation"
    t_headless = create_opencode_task(
        "p3", "n", OpencodeTaskConfig(**base, mode="conversation"),
    )
    assert t_headless.ui_widget is None


async def test_conversation_ui_sets_upstream_and_widget_data(ctx_and_captures, _supply_scenario):
    ctx, cap, _ = ctx_and_captures
    _supply_scenario["name"] = "conversation"
    cfg = OpencodeTaskConfig(
        consumer_instructions="", mode="conversation", host_protocol=False,
        conversation_ui=True, tool_verbosity="verbose",
    )
    sess, conv = await _launch(ctx, cfg)          # helper as in Task 5
    assert len(cap.widget_upstream) == 1          # opencode server is the upstream
    url, auth = cap.widget_upstream[0]
    assert url.startswith("http://127.0.0.1:")
    assert auth.username == "opencode"            # iframe-mode auth model unchanged
    [data] = cap.widget_data
    assert data["protocol"] == "opencode"
    assert data["sessionID"] == "fake-session-id"
    assert data["directory"]                      # the task workdir
    assert data["toolVerbosity"] == "verbose"
    assert "iframeSrc" not in data                # no SPA iframe fields
    await conv.close()
    await asyncio.wait_for(sess, timeout=30)


async def test_headless_conversation_sets_no_widget(ctx_and_captures, _supply_scenario):
    ctx, cap, _ = ctx_and_captures
    _supply_scenario["name"] = "conversation"
    cfg = OpencodeTaskConfig(
        consumer_instructions="", mode="conversation", host_protocol=False,
    )
    sess, conv = await _launch(ctx, cfg)
    assert cap.widget_upstream == []
    assert cap.widget_data == []
    await conv.close()
    await asyncio.wait_for(sess, timeout=30)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_conversation_ui_session.py -q`
Expected: `test_create_opencode_task_ui_widget_matrix` PASSES already (Task 5 step 8); the two async tests FAIL (no widget calls in conversation mode yet / widget data missing).

- [ ] **Step 3: Implement in `src/optio_opencode/session.py`**

In `_opencode_body`, where Task 5 wrapped the iframe widget block, complete the fork:

```python
        if config.mode != "conversation":
            # (existing iframe upstream + iframeSrc/localStorageOverrides block)
            ...
        elif config.conversation_ui:
            # Conversation widget: the opencode server itself is the upstream
            # (same proxy + inner-auth model as iframe mode); the widget talks
            # opencode's native API through the proxy.
            await ctx.set_widget_upstream(
                f"http://{upstream_host}:{worker_port}",
                inner_auth=BasicAuth(username="opencode", password=password),
            )
            await ctx.set_widget_data({
                "protocol": "opencode",
                "sessionID": session_id,
                # opencode routes resolve their project instance from the
                # request's location context; the widget sends this as the
                # ?directory= query param on every call.
                "directory": host.workdir,
                "toolVerbosity": config.tool_verbosity,
            })
```
Note placement: this must run after `session_id` is established (it already is — the widget block sits below session creation).

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_conversation_ui_session.py tests/test_conversation_session.py -q`
Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add packages/optio-opencode/src/optio_opencode/session.py \
        packages/optio-opencode/tests/test_conversation_ui_session.py
git commit -m "feat(optio-opencode): conversation_ui widget wiring"
```

### Task 7: Conversation-mode resume coverage

**Files:**
- Modify: `packages/optio-opencode/tests/test_session_resume.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/test_session_resume.py`, following its existing run→cancel→resume choreography (reuse its fixtures/helpers exactly as the neighboring tests do):

```python
async def test_conversation_mode_resume_reattaches_session(ctx_and_captures, _supply_scenario):
    """Resume of a conversation task: same opencode session id is reused
    (preserved_session_id path) and the caller gets a working Conversation."""
    ctx, cap, flag = ctx_and_captures
    _supply_scenario["name"] = "conversation"
    cfg = OpencodeTaskConfig(
        consumer_instructions="", mode="conversation", host_protocol=False,
        conversation_ui=True, supports_resume=True,
    )
    # First run: launch, then cancel (snapshot capture on teardown).
    sess, conv = await _launch(ctx, cfg)
    first_widget_data = cap.widget_data[-1]
    flag.set()                                    # simulate cancel, as existing tests do
    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(sess, timeout=30)

    # Resume run (the file's existing helpers re-create ctx with resume=True).
    ctx2, cap2 = await _resumed_context(ctx)      # follow the file's existing resume helper
    sess2, conv2 = await _launch(ctx2, cfg)
    assert cap2.widget_data[-1]["sessionID"] == first_widget_data["sessionID"]
    assert not conv2.closed
    await conv2.send("we're back")                # gateway functional after resume
    await conv2.close()
    await asyncio.wait_for(sess2, timeout=30)
```

Adapt the cancel/resume mechanics to the file's actual helpers (`test_session_resume.py` already has a canonical first-run/resume pattern — mirror it precisely; only the config and the Conversation assertions are new).

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_session_resume.py -q -k conversation`
Expected: FAIL or ERROR — surfaces whatever resume plumbing conversation mode breaks (most likely none beyond fixture mismatches; if it passes immediately, verify it exercises the preserved-session-id branch by asserting the fake's `POST /session` was NOT called on resume — the fake journals it).

- [ ] **Step 3: Fix anything the test exposes**

Expected adjustments (apply only what the failure shows): the resume-notice prompt (`SYSTEM_MESSAGE_PREFIX`) for conversation mode should go through `conversation.send(...)` only when `host_protocol=True` (claudecode parity, session.py resume-notice branch); keep using `_post_opencode_prompt` otherwise — both reach the same endpoint, so the minimal fix is to leave the existing branch untouched.

- [ ] **Step 4: Run the full opencode Python suite**

Run: `python -m pytest tests/ -q -x --ignore=tests/test_session_remote.py --ignore=tests/test_host_primitives_remote.py --ignore=tests/test_host_remote_resume.py`
Expected: all PASS (remote suites need the sshd container; skipped here, run in Task 12).

- [ ] **Step 5: Commit**

```bash
git add packages/optio-opencode/tests/test_session_resume.py packages/optio-opencode/src/optio_opencode/session.py
git commit -m "test(optio-opencode): conversation-mode resume coverage"
```

### Task 8: Capture real opencode event fixtures

**Files:**
- Create: `packages/optio-conversation-ui/src/__tests__/fixtures/opencode-events.json` (directory created here; package scaffold lands in Task 9)
- Create: `packages/optio-conversation-ui/src/__tests__/fixtures/opencode-history.json`

This task empirically verifies every pinned wire fact before the UI adapter is written, and produces the fixtures its reducer tests consume.

- [ ] **Step 1: Start a disposable opencode server**

```bash
mkdir -p /tmp/oc-fixture && cd /tmp/oc-fixture
printf '{"permission": {"bash": "ask"}}' > opencode.json
OPENCODE_SERVER_PASSWORD=fixturepw opencode serve --port 14096 &
sleep 2
curl -su opencode:fixturepw "http://127.0.0.1:14096/session?directory=$(python3 -c 'import urllib.parse;print(urllib.parse.quote("/tmp/oc-fixture"))')" -X POST -H 'content-type: application/json' -d '{}'
```
Expected: JSON with an `id` field (`ses_…`). Export it: `export SID=<id>`.

- [ ] **Step 2: Record an event stream while driving one prompt**

```bash
curl -sNu opencode:fixturepw "http://127.0.0.1:14096/event?directory=%2Ftmp%2Foc-fixture" > events.raw &
CURL_PID=$!
curl -su opencode:fixturepw "http://127.0.0.1:14096/session/$SID/prompt_async?directory=%2Ftmp%2Foc-fixture" \
  -H 'content-type: application/json' \
  -d '{"parts":[{"type":"text","text":"Run `echo hi` in the shell, then tell me what it printed."}]}' -i
sleep 30
curl -su opencode:fixturepw "http://127.0.0.1:14096/permission?directory=%2Ftmp%2Foc-fixture"
```
Expected: prompt_async returns 204; the permission list shows one pending `bash` request (the `"ask"` rule). Reply to it:
```bash
curl -su opencode:fixturepw "http://127.0.0.1:14096/permission/<requestID>/reply?directory=%2Ftmp%2Foc-fixture" \
  -H 'content-type: application/json' -d '{"reply":"once"}'
sleep 20 && kill $CURL_PID
curl -su opencode:fixturepw "http://127.0.0.1:14096/session/$SID/message?directory=%2Ftmp%2Foc-fixture" > history.json
```

- [ ] **Step 3: Distill fixtures and verify the pinned facts**

From `events.raw` (strip `data: ` prefixes, one JSON per frame), select a minimal honest sequence into `packages/.../fixtures/opencode-events.json` (a JSON array, in arrival order) covering: `server.connected`, the user `message.updated`+`message.part.updated`, `session.status` busy, assistant `message.part.delta`(s), assistant text `message.part.updated`, tool part updates (`pending`→`running`→`completed`), `permission.asked`, `permission.replied`, final assistant `message.updated` (with `time.completed`), `session.status` idle. Copy `history.json` to `fixtures/opencode-history.json`.

**Verification gate — compare against the "Pinned opencode wire facts" table.** If any name/shape differs (e.g. busy/idle arrives only as deprecated `session.idle`, sessionID nests differently, the permission reply path 404s): update the table in this plan, fix `OpencodeConversation._route`/`_sweep_permissions` and the Task 4 fake's event shapes accordingly, and re-run `python -m pytest tests/test_conversation_driver.py -q` before proceeding. Do not carry a known fixture/driver mismatch into Task 10.

- [ ] **Step 4: Clean up and commit**

```bash
kill %1 2>/dev/null; rm -rf /tmp/oc-fixture
git add packages/optio-conversation-ui/src/__tests__/fixtures/
git commit -m "test(optio-conversation-ui): recorded opencode wire fixtures"
```

### Task 9: Scaffold `optio-conversation-ui` (claudecode adapter intact)

**Files:**
- Create: `packages/optio-conversation-ui/` — `package.json`, `tsconfig.json`, `vitest.config.ts`, `vitest.setup.ts`
- Create: `packages/optio-conversation-ui/src/` — `index.ts`, `ConversationWidget.tsx`, `chat.ts`, `claudecode/events.ts`, `claudecode/ClaudeCodeView.tsx`, shared components copied: `AnswerBlock.tsx`, `Markdown.tsx`, `Mermaid.tsx`
- Create: `packages/optio-conversation-ui/src/__tests__/claudecode-events.test.ts` (ported)
- `optio-claudecode-ui` is NOT touched (frozen backup per the spec).

- [ ] **Step 1: Scaffold by copying the existing package**

```bash
cd /home/csillag/deai/optio/.claude/worktrees/csillag+opencode-frontend/packages
mkdir -p optio-conversation-ui/src/__tests__
cp optio-claudecode-ui/tsconfig.json optio-claudecode-ui/vitest.config.ts \
   optio-claudecode-ui/vitest.setup.ts optio-conversation-ui/
cp optio-claudecode-ui/src/AnswerBlock.tsx optio-claudecode-ui/src/Markdown.tsx \
   optio-claudecode-ui/src/Mermaid.tsx optio-conversation-ui/src/
mkdir -p optio-conversation-ui/src/claudecode
cp optio-claudecode-ui/src/events.ts optio-conversation-ui/src/claudecode/events.ts
cp optio-claudecode-ui/src/ClaudeCodeConversationWidget.tsx optio-conversation-ui/src/claudecode/ClaudeCodeView.tsx
cp optio-claudecode-ui/src/__tests__/events.test.ts optio-conversation-ui/src/__tests__/claudecode-events.test.ts
cp optio-claudecode-ui/src/__tests__/widget.test.tsx optio-conversation-ui/src/__tests__/claudecode-widget.test.tsx
```

Create `optio-conversation-ui/package.json` — copy `optio-claudecode-ui/package.json` and change exactly: `"name": "optio-conversation-ui"`, `"version": "0.1.0"`, `"description": "Engine-neutral conversation widget for optio tasks (claudecode + opencode protocols)"`. Dependencies/peerDependencies/scripts stay identical.

- [ ] **Step 2: Split shared chat model out of the claudecode reducer**

Create `src/chat.ts` — move (cut, not copy) from `src/claudecode/events.ts`: the `ChatItem` type, `ChatState` interface, and `initialChatState`. In `claudecode/events.ts` replace them with `import type { ChatItem, ChatState } from '../chat.js'; export { initialChatState } from '../chat.js';` and keep `reduceEvent` (claudecode wire → ChatItem) plus its helpers unchanged.

- [ ] **Step 3: Generic widget + claudecode view**

In `src/claudecode/ClaudeCodeView.tsx`: rename the exported component `ClaudeCodeConversationWidget` → `ClaudeCodeView`; delete its `registerClaudeCodeConversationWidget` function (registration moves to the package root); fix relative imports (`./events.js` stays, `./Markdown.js` → `../Markdown.js`, etc.).

Create `src/ConversationWidget.tsx`:

```tsx
import React from 'react';
import { registerWidget, type WidgetProps } from 'optio-ui';
import { ClaudeCodeView } from './claudecode/ClaudeCodeView.js';
import { OpencodeView } from './opencode/OpencodeView.js';

/** Engine-neutral conversation widget: the task declares its wire protocol
 *  via widgetData.protocol; each view speaks that protocol natively through
 *  the widget proxy. */
export function ConversationWidget(props: WidgetProps) {
  const protocol = (props.process as any)?.widgetData?.protocol ?? 'claudecode';
  if (protocol === 'opencode') return <OpencodeView {...props} />;
  return <ClaudeCodeView {...props} />;
}

export function registerConversationWidget(): void {
  registerWidget('conversation', ConversationWidget);
  console.info('[optio-conversation-ui] conversation widget registered');
}
```

(For this task only, create a placeholder `src/opencode/OpencodeView.tsx` that renders `<div>opencode protocol view</div>` — replaced wholesale in Task 10. This keeps the package compiling without faking any claudecode behavior.)

Note: `ClaudeCodeView` reads `widgetData` the same way the old widget did; `widgetData.protocol` is simply absent for claudecode tasks today — hence the `'claudecode'` default above. The claudecode task's widgetData gains `protocol: "claudecode"` explicitly in Task 11.

Create `src/index.ts`:

```typescript
export { ConversationWidget, registerConversationWidget } from './ConversationWidget.js';
export { reduceEvent as reduceClaudecodeEvent } from './claudecode/events.js';
export { reduceOpencodeEvent, historyToChatItems } from './opencode/events.js';
export { initialChatState } from './chat.js';
export type { ChatItem, ChatState } from './chat.js';
export { AnswerBlock } from './AnswerBlock.js';
```

(`src/opencode/events.ts` with `reduceOpencodeEvent`/`historyToChatItems` arrives in Task 10; for this task export only the claudecode + shared names and add the opencode line in Task 10.)

- [ ] **Step 4: Port the tests and run**

In the two copied test files, update imports to the new paths (`../claudecode/events.js`, `../ConversationWidget.js`); in `claudecode-widget.test.tsx`, render `ConversationWidget` with a process doc whose `widgetData` lacks `protocol` (default-to-claudecode path) and assert the existing behaviors unchanged.

```bash
cd /home/csillag/deai/optio/.claude/worktrees/csillag+opencode-frontend
pnpm install            # registers the new workspace package
cd packages/optio-conversation-ui
node_modules/.bin/tsc --noEmit && pnpm test
```
Expected: build clean, all ported tests PASS.

- [ ] **Step 5: Commit**

```bash
git add packages/optio-conversation-ui pnpm-lock.yaml
git commit -m "feat(optio-conversation-ui): engine-neutral package, claudecode adapter"
```

### Task 10: Opencode protocol adapter (reducer + view)

**Files:**
- Create: `packages/optio-conversation-ui/src/opencode/events.ts`
- Replace: `packages/optio-conversation-ui/src/opencode/OpencodeView.tsx`
- Create: `packages/optio-conversation-ui/src/__tests__/opencode-events.test.ts`
- Modify: `packages/optio-conversation-ui/src/index.ts` (add the opencode exports line from Task 9)

- [ ] **Step 1: Write the failing reducer tests**

Create `src/__tests__/opencode-events.test.ts`:

```typescript
import { describe, expect, it } from 'vitest';
import { initialChatState, type ChatState } from '../chat.js';
import { historyToChatItems, reduceOpencodeEvent } from '../opencode/events.js';
import fixtureEvents from './fixtures/opencode-events.json';
import fixtureHistory from './fixtures/opencode-history.json';

const SID = fixtureEvents.find((e: any) => e.properties?.sessionID)?.properties.sessionID;

function play(events: any[], from: ChatState = initialChatState): ChatState {
  return events.reduce((s, ev, i) => reduceOpencodeEvent(s, ev, i, SID), from);
}

describe('opencode event reducer (recorded fixtures)', () => {
  it('full recorded session produces a coherent chat', () => {
    const s = play(fixtureEvents as any[]);
    expect(s.items.some((i) => i.kind === 'user')).toBe(true);
    expect(s.items.some((i) => i.kind === 'assistant' && !i.pending)).toBe(true);
    expect(s.items.some((i) => i.kind === 'permission')).toBe(true);
    expect(s.busy).toBe(false);          // ends on session.status idle
    expect(s.closed).toBe(false);
  });

  it('deltas stream into a pending bubble; part.updated is authoritative', () => {
    const s = play([
      { type: 'session.status', properties: { sessionID: SID, status: { type: 'busy' } } },
      { type: 'message.part.delta', properties: { sessionID: SID, messageID: 'm1', partID: 'p1', delta: 'He' } },
      { type: 'message.part.delta', properties: { sessionID: SID, messageID: 'm1', partID: 'p1', delta: 'llo' } },
    ]);
    const bubble = s.items.find((i) => i.kind === 'assistant');
    expect(bubble && bubble.kind === 'assistant' && bubble.text).toBe('Hello');
    expect(bubble && bubble.kind === 'assistant' && bubble.pending).toBe(true);
    const s2 = play(
      [{ type: 'message.part.updated', properties: { part: { id: 'p1', messageID: 'm1', sessionID: SID, type: 'text', text: 'Hello world' } } }],
      s,
    );
    const b2 = s2.items.find((i) => i.kind === 'assistant');
    expect(b2 && b2.kind === 'assistant' && b2.text).toBe('Hello world');
  });

  it('assistant message completion finalizes the bubble', () => {
    const s = play([
      { type: 'message.part.updated', properties: { part: { id: 'p1', messageID: 'm1', sessionID: SID, type: 'text', text: 'done' } } },
      { type: 'message.updated', properties: { info: { id: 'm1', sessionID: SID, role: 'assistant', time: { created: 1, completed: 2 } } } },
    ]);
    const b = s.items.find((i) => i.kind === 'assistant');
    expect(b && b.kind === 'assistant' && b.pending).toBe(false);
  });

  it('tool part renders an ephemeral tool row', () => {
    const s = play([
      { type: 'message.part.updated', properties: { part: { id: 't1', messageID: 'm1', sessionID: SID, type: 'tool', callID: 'c1', tool: 'bash', state: { status: 'running', input: { command: 'echo hi' } } } } },
    ]);
    const t = s.items.find((i) => i.kind === 'tool');
    expect(t && t.kind === 'tool' && t.name).toBe('bash');
  });

  it('permission.asked creates a card; permission.replied answers it', () => {
    const ask = { type: 'permission.asked', properties: { id: 'per_1', sessionID: SID, permission: 'bash', patterns: [], metadata: { command: 'rm -rf' }, always: [] } };
    const s = play([ask]);
    const card = s.items.find((i) => i.kind === 'permission');
    expect(card && card.kind === 'permission' && card.requestId).toBe('per_1');
    expect(card && card.kind === 'permission' && card.answered).toBe(null);
    const s2 = play([{ type: 'permission.replied', properties: { sessionID: SID, requestID: 'per_1', reply: 'reject' } }], s);
    const card2 = s2.items.find((i) => i.kind === 'permission');
    expect(card2 && card2.kind === 'permission' && card2.answered).toBe('deny');
  });

  it('other-session events are ignored', () => {
    const s = play([
      { type: 'message.part.delta', properties: { sessionID: 'ses_other', messageID: 'mx', partID: 'px', delta: 'noise' } },
    ]);
    expect(s.items).toHaveLength(0);
  });

  it('history bootstrap maps user/assistant/tool parts', () => {
    const items = historyToChatItems(fixtureHistory as any[], SID);
    expect(items.some((i) => i.kind === 'user')).toBe(true);
    expect(items.some((i) => i.kind === 'assistant')).toBe(true);
  });
});
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd packages/optio-conversation-ui && pnpm test -- opencode-events`
Expected: FAIL — `Cannot find module '../opencode/events.js'`.

- [ ] **Step 3: Implement `src/opencode/events.ts`**

```typescript
import type { ChatItem, ChatState } from '../chat.js';

/** Reducer over opencode's native /event SSE frames ({id, type, properties}).
 *  Normalization to ChatItem happens here — the wire stays engine-native. */

function sid(ev: any): string | undefined {
  const p = ev?.properties ?? {};
  return p.sessionID ?? p.info?.sessionID ?? p.part?.sessionID;
}

function upsertAssistant(
  items: ChatItem[], msgId: string, seq: number,
  update: (prev: { text: string; pending: boolean }) => { text: string; pending: boolean },
): ChatItem[] {
  const idx = items.findIndex((i) => i.kind === 'assistant' && i.msgId === msgId);
  if (idx === -1) {
    const fresh = update({ text: '', pending: true });
    return [...items, { kind: 'assistant', msgId, seq, ...fresh }];
  }
  const prev = items[idx] as Extract<ChatItem, { kind: 'assistant' }>;
  const next = { ...prev, ...update({ text: prev.text, pending: prev.pending }) };
  return [...items.slice(0, idx), next, ...items.slice(idx + 1)];
}

const dropTools = (items: ChatItem[]) => items.filter((i) => i.kind !== 'tool');

export function reduceOpencodeEvent(
  state: ChatState, ev: any, seq: number, sessionID: string,
): ChatState {
  const t = ev?.type as string | undefined;
  const p = ev?.properties ?? {};
  if (!t) return state;
  const evSid = sid(ev);
  if (evSid !== undefined && evSid !== sessionID) return state;

  // Synthetic, dispatched locally by the view on send (optimistic echo):
  if (t === 'x-optio-local-user') {
    return { ...state, busy: true, items: [...state.items, { kind: 'user', text: p.text ?? '', seq, local: true }] };
  }

  if (t === 'session.status' || t === 'session.idle') {
    const busy = t === 'session.status' && p.status?.type === 'busy';
    return { ...state, busy };
  }

  if (t === 'message.part.delta') {
    const msgId = String(p.messageID ?? '');
    return { ...state, items: upsertAssistant(state.items, msgId, seq, (prev) => ({ text: prev.text + (p.delta ?? ''), pending: true })) };
  }

  if (t === 'message.part.updated') {
    const part = p.part ?? {};
    const msgId = String(part.messageID ?? '');
    if (part.type === 'text') {
      return { ...state, items: upsertAssistant(state.items, msgId, seq, () => ({ text: part.text ?? '', pending: true })) };
    }
    if (part.type === 'tool') {
      return {
        ...state,
        items: [...dropTools(state.items), { kind: 'tool', name: part.tool ?? 'tool', input: part.state?.input ?? {}, seq }],
      };
    }
    return state;
  }

  if (t === 'message.updated') {
    const info = p.info ?? {};
    if (info.role === 'user') {
      // The wire echo of a sent message: confirm the optimistic local bubble
      // in place (FIFO by presence of the `local` flag), claudecode parity.
      const idx = state.items.findIndex((i) => i.kind === 'user' && i.local);
      if (idx !== -1) {
        const items = [...state.items];
        items[idx] = { ...(items[idx] as Extract<ChatItem, { kind: 'user' }>), local: false };
        return { ...state, items, busy: true };
      }
      return state; // user message text arrives via its own part events / history
    }
    if (info.role === 'assistant' && info.time?.completed) {
      const msgId = String(info.id ?? '');
      return { ...state, items: dropTools(upsertAssistant(state.items, msgId, seq, (prev) => ({ ...prev, pending: false }))) };
    }
    return state;
  }

  if (t === 'permission.asked') {
    return {
      ...state,
      items: [...dropTools(state.items), {
        kind: 'permission', requestId: String(p.id ?? ''), toolName: String(p.permission ?? ''),
        input: p.metadata ?? {}, answered: null, seq,
      }],
    };
  }

  if (t === 'permission.replied') {
    const rid = String(p.requestID ?? '');
    const answered = p.reply === 'reject' ? 'deny' as const : 'allow' as const;
    return {
      ...state,
      items: state.items.map((i) =>
        i.kind === 'permission' && i.requestId === rid ? { ...i, answered } : i),
    };
  }

  // user text parts: opencode emits user message parts too
  return state;
}

/** Map GET /session/:id/message ({info, parts}[]) to the initial ChatItem list. */
export function historyToChatItems(history: any[], sessionID: string): ChatItem[] {
  const items: ChatItem[] = [];
  let seq = -1_000_000; // history sorts before any live seq
  for (const entry of history ?? []) {
    const info = entry?.info ?? {};
    if (info.sessionID !== undefined && info.sessionID !== sessionID) continue;
    const parts = entry?.parts ?? [];
    if (info.role === 'user') {
      const text = parts.filter((p: any) => p.type === 'text').map((p: any) => p.text).join('\n');
      if (text) items.push({ kind: 'user', text, seq: seq++ });
      continue;
    }
    if (info.role === 'assistant') {
      const text = parts.filter((p: any) => p.type === 'text').map((p: any) => p.text).join('\n\n');
      if (text) items.push({ kind: 'assistant', text, pending: false, seq: seq++, msgId: String(info.id ?? '') });
    }
  }
  return items;
}
```

One shared-model addition: the user `ChatItem` already carries `local?: boolean` (claudecode model — unchanged); the user-part text for *live* opencode user messages arrives via the optimistic echo plus history; no additional shapes are needed in `chat.ts`.

- [ ] **Step 4: Run reducer tests**

Run: `pnpm test -- opencode-events`
Expected: all PASS. If a fixture-driven assertion fails, the fixtures are authoritative — fix the reducer, not the fixture.

- [ ] **Step 5: Implement `src/opencode/OpencodeView.tsx`** (replacing the placeholder)

Structure mirrors `ClaudeCodeView` (reuse its rendering JSX wholesale — bubbles, tool rows, permission cards, input, interrupt, closed banner, `toolVerbosity`); only the transport differs:

```tsx
// Transport core of OpencodeView (rendering JSX copied from ClaudeCodeView):
const widgetData = (props.process as any)?.widgetData ?? {};
const sessionID: string = widgetData.sessionID;
const directory: string = widgetData.directory ?? '';
const q = `?directory=${encodeURIComponent(directory)}`;
const seqRef = useRef(0);
const localSeqRef = useRef(-1);

// Bootstrap: subscribe FIRST and buffer, then fetch history and reconcile —
// events that arrive between the two calls are replayed after the bootstrap
// (the spec's history-then-subscribe race fix).
useEffect(() => {
  let bootstrapped = false;
  const buffer: any[] = [];
  const es = new EventSource(`${widgetProxyUrl}event${q}`);
  es.onmessage = (msg) => {
    let ev: any;
    try { ev = JSON.parse(msg.data); } catch { return; }
    if (!bootstrapped) { buffer.push(ev); return; }
    dispatch({ ev, seq: seqRef.current++ });
  };
  void (async () => {
    try {
      const resp = await fetch(`${widgetProxyUrl}session/${sessionID}/message${q}`);
      const history = resp.ok ? await resp.json() : [];
      dispatch({ kind: 'bootstrap', items: historyToChatItems(history, sessionID) });
    } finally {
      bootstrapped = true;
      for (const ev of buffer) dispatch({ ev, seq: seqRef.current++ });
    }
  })();
  return () => es.close();
}, [widgetProxyUrl, sessionID]);

// Actions (post() helper identical to ClaudeCodeView's):
const send = (text: string) => post(`session/${sessionID}/prompt_async${q}`, { parts: [{ type: 'text', text }] })
  .then((ok) => { if (ok) dispatch({ ev: { type: 'x-optio-local-user', properties: { text } }, seq: localSeqRef.current-- }); return ok; });
const interrupt = () => post(`session/${sessionID}/abort${q}`, {});
const answerPermission = (requestId: string, behavior: 'allow' | 'deny') =>
  post(`permission/${requestId}/reply${q}`,
       behavior === 'deny' ? { reply: 'reject', message: 'Denied by the operator.' } : { reply: 'once' });
```

The view's reducer wrapper handles two action kinds: `{kind:'bootstrap', items}` (replace items, keep busy/closed) and `{ev, seq}` → `reduceOpencodeEvent(state, ev, seq, sessionID)`. "Session ended" state: derive `closed` from the process document's terminal state exactly as `IframeWidget.tsx` does (`isWidgetLive(tree)` pattern) — when not live, disable input and append the closed divider.

- [ ] **Step 6: Build, test, commit**

```bash
node_modules/.bin/tsc --noEmit && pnpm test
git add packages/optio-conversation-ui
git commit -m "feat(optio-conversation-ui): opencode protocol adapter"
```
Expected: build clean, full package suite PASS.

### Task 11: Integration switch — dashboard, claudecode rename, demo

**Files:**
- Modify: `packages/optio-dashboard/src/app/App.tsx` and `packages/optio-dashboard/package.json`
- Modify: `packages/optio-claudecode/src/optio_claudecode/session.py` (two one-line changes)
- Modify: `packages/optio-claudecode/tests/test_conversation_ui_session.py` (widget-type assertion) — locate with `grep -rn "claudecode-conversation" packages/optio-claudecode/tests/`
- Modify: `packages/optio-demo/src/optio_demo/tasks/opencode.py`
- Modify: `packages/optio-demo/tests/test_demo_smoke.py` (extend the task-inventory assertion if it counts opencode tasks — check with `grep -n "opencode" packages/optio-demo/tests/test_demo_smoke.py`)

- [ ] **Step 1: Claudecode emits the generic widget type**

In `packages/optio-claudecode/src/optio_claudecode/session.py`:
- `create_claudecode_task` (line ~1302): `"claudecode-conversation"` → `"conversation"`.
- The conversation_ui `set_widget_data` call (line ~491): `{"toolVerbosity": config.tool_verbosity}` → `{"protocol": "claudecode", "toolVerbosity": config.tool_verbosity}`.

Run: `cd packages/optio-claudecode && python -m pytest tests/ -q -k "conversation"` — fix the widget-type assertions the grep found (expect `"conversation"`), re-run to PASS.

- [ ] **Step 2: Dashboard registers the new package**

In `packages/optio-dashboard/package.json`: replace dependency `"optio-claudecode-ui": "workspace:*"` with `"optio-conversation-ui": "workspace:*"`.
In `src/app/App.tsx`: replace the import and call:
```typescript
import { registerConversationWidget } from 'optio-conversation-ui';
…
registerConversationWidget();
```
Run: `pnpm install && cd packages/optio-dashboard && node_modules/.bin/tsc --noEmit` (or the package's build script) → clean.

- [ ] **Step 3: Demo task**

In `packages/optio-demo/src/optio_demo/tasks/opencode.py`, inside the per-seed loop (after the existing seed-pinned append), add:

```python
        tasks.append(
            create_opencode_task(
                process_id=f"opencode-conversation-seed-{seed_id}",
                name=f"opencode conversation — {name}",
                description=(
                    "Conversation-mode opencode session from a captured seed: "
                    "drive it from the dashboard's conversation widget (send, "
                    "interrupt, approve/deny permissions). Caller-driven; no "
                    "keyword channel."
                ),
                config=OpencodeTaskConfig(
                    consumer_instructions="",   # defaulted conversation prompt
                    mode="conversation",
                    conversation_ui=True,
                    host_protocol=False,
                    ssh=ssh,
                    seed_id=seed_id,
                    supports_resume=True,
                ),
            )
        )
```

- [ ] **Step 4: Demo smoke + commit**

```bash
cd packages/optio-demo && python -m pytest tests/test_demo_smoke.py -q
```
Expected: PASS (extend the inventory assertion if it enumerates opencode tasks).

```bash
git add packages/optio-dashboard packages/optio-claudecode packages/optio-demo pnpm-lock.yaml
git commit -m "feat: switch dashboard+engines to optio-conversation-ui; opencode conversation demo"
```

### Task 12: Full verification

- [ ] **Step 1: Python suites**

```bash
cd /home/csillag/deai/optio/.claude/worktrees/csillag+opencode-frontend
source .venv/bin/activate
(cd packages/optio-agents && python -m pytest -q)
(cd packages/optio-claudecode && python -m pytest -q)
(cd packages/optio-opencode && python -m pytest -q)
(cd packages/optio-demo && python -m pytest -q)
```
Expected: all PASS. Known pre-existing flake: optio-core `test_cancel_shared_deadline_across_subtree` (re-run before suspecting a regression). Remote (sshd-container) suites: run if the container infra is available (`docker compose -f packages/optio-opencode/tests/docker-compose.sshd.yml up -d`), otherwise note as not run.

- [ ] **Step 2: JS/TS suites**

```bash
OPTIO_SKIP_PREFLIGHT_TESTS=1 pnpm -r test
pnpm -r build
```
Expected: PASS (known flake: optio-api fastify-widget-proxy WS tests under `-r` load — re-run isolated if hit).

- [ ] **Step 3: Spec conformance read-back**

Re-read `docs/2026-06-11-opencode-conversation-mode-design.md` section by section against the diff (`git diff 65753bd..HEAD --stat`); confirm: no listener package was created, events pass through raw, `optio-claudecode-ui` is untouched, question tool disabled in conversation mode, widget type is `"conversation"` on both engines.

- [ ] **Step 4: Release-tooling note (no action unless releasing)**

`optio-conversation-ui` is a NEW workspace package: before any `make release-*`, it must be registered with the release tooling and the tree must be clean (publish git-checks fail on untracked files). `optio-claudecode-ui` must NOT be published anymore (frozen backup).

- [ ] **Step 5: Final commit (if any stragglers)**

```bash
git status --porcelain   # expect empty; commit anything intentional that remains
```

---

## Self-review notes

- **Spec coverage:** config surface (T1), prompt/question-disable (T2/T5), `OpencodeConversation` incl. permission sweep + `x-optio-closed` + closed errors (T3), session body + completion semantics + premature exit (T5), `conversation_ui` wiring + auth model (T6), resume (T7), native-fixture verification gate (T8), `optio-conversation-ui` with both adapters + history-then-subscribe race fix + multiple-viewer benign permission race (T9/T10), claudecode rename + dashboard + demo + e2e (T11), full verification (T12). Frozen-backup status of `optio-claudecode-ui`: enforced by *not touching it* (T9) and the no-publish note (T12).
- **Known empirical risks, contained:** exact live event payload nesting (T8 is a hard gate with a fix-then-retest loop before the UI adapter); fake_opencode's socket-level SSE details (T4 follows the file's existing style; its parity test runs the real driver against it).
- **Type consistency:** `ChatItem`/`ChatState` live in `chat.ts` and are imported everywhere; `reduceOpencodeEvent(state, ev, seq, sessionID)` signature matches between tests (T10 S1) and implementation (T10 S3); `OpencodeConversation(port, password, session_id, directory)` matches between T3, T5 body code, and T6 widgetData.
