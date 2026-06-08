# Claude Code Human-Input Channel (Phase 1) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Give the operator a client-side input box that delivers a complete message to the running claude session via a session-opened HTTP listener, fake-typed as one serialized burst — eliminating the human/system tmux-input garbling.

**Architecture:** The claude session opens an in-process (engine-side) aiohttp `POST /input` listener, registered as a `controlUpstream` and reached through the API widget proxy exactly as ttyd is. A single `asyncio.Lock` in the session serializes both the human-input path and the existing system `_agent_sender` path. A new `iframe-input` UI widget (display iframe + prose box) POSTs to `/api/widget-control/...` and shows a delivery ack.

**Tech Stack:** Python (aiohttp, asyncio, motor), TypeScript (Fastify, vitest), React (vitest + Testing Library). Design spec: `docs/2026-06-08-claudecode-input-channel-design.md`.

> **EXECUTION SHAPE — parallel.** This plan is parallel-shaped (per the standing project directive). **Every file is owned by exactly one task; no two tasks edit the same file.** Tasks 1–6 are file-disjoint and may run concurrently by an agent swarm. Some tasks reference symbols defined in a sibling task (e.g. the fastify route calls `forwardAgentInput` from Task 2); the exact signatures are pinned in this plan, so they can be authored concurrently — the tree is **not expected to compile or pass tests mid-execution**. **ALL verification (pytest / tsc / vitest / eslint) is deferred to the final tasks V1–V2** — do NOT gate individual tasks on green tests. Tests are written inside each task but run only at the end.

---

## File Structure

| Package | File | Owner task | Responsibility |
|---|---|---|---|
| optio-core | `src/optio_core/store.py` | T1 | `update_control_upstream` / `clear_control_upstream` Mongo writers |
| optio-core | `src/optio_core/context.py` | T1 | `set_control_upstream` / `clear_control_upstream` on ProcessContext |
| optio-core | `tests/test_control_upstream.py` | T1 | store roundtrip test |
| optio-api | `src/agent-input.ts` | T2 | `forwardAgentInput(...)` — resolve `controlUpstream`, POST to `/input`, return `{status, body}` |
| optio-api | `src/__tests__/agent-input.test.ts` | T2 | unit test (fake db + fake fetch) |
| optio-api | `src/adapters/fastify.ts` | T3 | wire `POST /api/widget-control/:database/:prefix/:processId` route |
| optio-claudecode | `src/optio_claudecode/input_listener.py` | T4 | aiohttp `/input` listener factory + `serialized()` lock helper |
| optio-claudecode | `pyproject.toml` | T4 | add `aiohttp` dependency |
| optio-claudecode | `tests/test_input_listener.py` | T4 | listener POST→callback + ack test |
| optio-claudecode | `tests/test_injection_serialize.py` | T4 | `serialized()` no-interleave test |
| optio-claudecode | `src/optio_claudecode/session.py` | T5 | lock, on-input callback, listener start/stop, control-upstream register/clear, `ui_widget` switch |
| optio-ui | `src/widgets/IframeInputWidget.tsx` | T6 | composite widget (display iframe + prose box + ack) |
| optio-ui | `src/index.ts` | T6 | export widget (triggers `registerWidget`) |
| optio-ui | `src/__tests__/IframeInputWidget.test.tsx` | T6 | widget render + submit test |

---

## Task 1: optio-core — controlUpstream store + context methods

**Files:**
- Modify: `packages/optio-core/src/optio_core/store.py` (after `clear_widget_upstream`, ~line 451)
- Modify: `packages/optio-core/src/optio_core/context.py` (after `clear_widget_upstream`, ~line 246)
- Test: `packages/optio-core/tests/test_control_upstream.py` (create)

- [ ] **Step 1: Add store writers** — append to `store.py` right after `clear_widget_upstream`:

```python
async def update_control_upstream(
    db: AsyncIOMotorDatabase,
    prefix: str,
    process_oid: ObjectId,
    url: str,
    inner_auth: InnerAuth | None = None,
) -> None:
    """Set controlUpstream (the session's in-process input listener) — used by
    the agent-input proxy route to forward human messages into the session."""
    entry: dict = {"url": url}
    entry["innerAuth"] = inner_auth.to_dict() if inner_auth is not None else None
    await _collection(db, prefix).update_one(
        {"_id": process_oid},
        {"$set": {"controlUpstream": entry}},
    )


async def clear_control_upstream(
    db: AsyncIOMotorDatabase, prefix: str, process_oid: ObjectId,
) -> None:
    """Clear controlUpstream on a process (teardown)."""
    await _collection(db, prefix).update_one(
        {"_id": process_oid},
        {"$set": {"controlUpstream": None}},
    )
```

- [ ] **Step 2: Add ProcessContext methods** — append to `context.py` right after `clear_widget_upstream`:

```python
    async def set_control_upstream(
        self,
        url: str,
        inner_auth: InnerAuth | None = None,
    ) -> None:
        """Register the URL of this session's input listener for the
        agent-input proxy route. Mirrors set_widget_upstream."""
        from optio_core.store import update_control_upstream
        await update_control_upstream(
            self._db, self._prefix, self._process_oid, url, inner_auth,
        )

    async def clear_control_upstream(self) -> None:
        """Clear controlUpstream (teardown)."""
        from optio_core.store import clear_control_upstream
        await clear_control_upstream(self._db, self._prefix, self._process_oid)
```

- [ ] **Step 3: Write the test** — create `tests/test_control_upstream.py`:

```python
"""controlUpstream store roundtrip (the agent-input listener registration)."""
from optio_core.models import TaskInstance
from optio_core.store import (
    upsert_process, _collection,
    update_control_upstream, clear_control_upstream,
)


async def dummy_execute(ctx):
    pass


async def test_control_upstream_set_and_clear(mongo_db):
    task = TaskInstance(execute=dummy_execute, process_id="ctl_1", name="Ctl")
    proc = await upsert_process(mongo_db, "test", task)

    await update_control_upstream(mongo_db, "test", proc["_id"], "http://engine:54321")
    doc = await _collection(mongo_db, "test").find_one({"_id": proc["_id"]})
    assert doc["controlUpstream"] == {"url": "http://engine:54321", "innerAuth": None}

    await clear_control_upstream(mongo_db, "test", proc["_id"])
    doc = await _collection(mongo_db, "test").find_one({"_id": proc["_id"]})
    assert doc["controlUpstream"] is None
```

- [ ] **Step 4: Commit** (no test run here — verification is deferred to V1)

```bash
git add packages/optio-core/src/optio_core/store.py \
        packages/optio-core/src/optio_core/context.py \
        packages/optio-core/tests/test_control_upstream.py
git commit -m "feat(optio-core): controlUpstream store + context methods for agent-input"
```

---

## Task 2: optio-api — forwardAgentInput logic

**Files:**
- Create: `packages/optio-api/src/agent-input.ts`
- Test: `packages/optio-api/src/__tests__/agent-input.test.ts`

This is the testable core of the new route: resolve `controlUpstream` from Mongo and POST the message to the listener's `/input`. Mirrors `resolveWidgetUpstream` (no cache — agent-input is a one-shot, low-frequency POST). Inner auth honored via the existing `applyInnerAuthHeaders`.

- [ ] **Step 1: Write the failing test** — create `src/__tests__/agent-input.test.ts`:

```typescript
import { describe, it, expect, vi } from 'vitest';
import { ObjectId } from 'mongodb';
import { forwardAgentInput } from '../agent-input.js';

function fakeDb(upstream: unknown) {
  return {
    databaseName: 'testdb',
    collection: () => ({
      findOne: async () => (upstream === undefined ? null : { controlUpstream: upstream }),
    }),
  } as any;
}

const PID = new ObjectId().toHexString();

describe('forwardAgentInput', () => {
  it('404s when no controlUpstream is registered', async () => {
    const res = await forwardAgentInput(fakeDb(null), 'gm', PID, 'hi', vi.fn());
    expect(res.status).toBe(404);
  });

  it('400s on a malformed processId', async () => {
    const res = await forwardAgentInput(fakeDb({ url: 'http://e:1' }), 'gm', 'not-an-oid', 'hi', vi.fn());
    expect(res.status).toBe(400);
  });

  it('forwards POST to <url>/input and returns 200 on ok', async () => {
    const fetchImpl = vi.fn(async () => new Response(JSON.stringify({ ok: true }), { status: 200 }));
    const res = await forwardAgentInput(
      fakeDb({ url: 'http://engine:7682', innerAuth: null }), 'gm', PID, 'hello', fetchImpl as any,
    );
    expect(fetchImpl).toHaveBeenCalledOnce();
    const [url, init] = fetchImpl.mock.calls[0];
    expect(url).toBe('http://engine:7682/input');
    expect(init.method).toBe('POST');
    expect(JSON.parse(init.body)).toEqual({ text: 'hello' });
    expect(res.status).toBe(200);
    expect(res.body).toEqual({ ok: true });
  });

  it('502s when the listener reports failure', async () => {
    const fetchImpl = vi.fn(async () => new Response(JSON.stringify({ ok: false, reason: 'send-failed' }), { status: 502 }));
    const res = await forwardAgentInput(
      fakeDb({ url: 'http://engine:7682', innerAuth: null }), 'gm', PID, 'x', fetchImpl as any,
    );
    expect(res.status).toBe(502);
  });

  it('502s when the listener is unreachable', async () => {
    const fetchImpl = vi.fn(async () => { throw new Error('ECONNREFUSED'); });
    const res = await forwardAgentInput(
      fakeDb({ url: 'http://engine:7682', innerAuth: null }), 'gm', PID, 'x', fetchImpl as any,
    );
    expect(res.status).toBe(502);
  });
});
```

- [ ] **Step 2: Implement** — create `src/agent-input.ts`:

```typescript
import { ObjectId, type Db } from 'mongodb';
import type { InnerAuthDoc, WidgetUpstreamValue } from './widget-upstream-registry.js';
import { applyInnerAuthHeaders } from './widget-proxy-core.js';

export interface AgentInputResult {
  status: number;
  body: unknown;
}

/**
 * Resolve the process's controlUpstream and POST {text} to its /input route.
 * One-shot, low-frequency — no caching. fetchImpl is injectable for tests.
 */
export async function forwardAgentInput(
  db: Db,
  prefix: string,
  processId: string,
  text: string,
  fetchImpl: typeof fetch = fetch,
): Promise<AgentInputResult> {
  let oid: ObjectId;
  try {
    oid = new ObjectId(processId);
  } catch {
    return { status: 400, body: { message: 'Invalid processId' } };
  }

  const doc = await db.collection(`${prefix}_processes`).findOne(
    { _id: oid },
    { projection: { controlUpstream: 1 } },
  );
  const upstream = (doc?.controlUpstream ?? null) as WidgetUpstreamValue | null;
  if (!upstream) {
    return { status: 404, body: { message: 'session not running' } };
  }

  const url = `${upstream.url.replace(/\/$/, '')}/input`;
  const headers = applyInnerAuthHeaders(
    (upstream.innerAuth ?? null) as InnerAuthDoc | null,
    { 'content-type': 'application/json' },
  ) as Record<string, string>;

  try {
    const resp = await fetchImpl(url, {
      method: 'POST',
      headers,
      body: JSON.stringify({ text }),
    });
    let body: unknown = null;
    try { body = await resp.json(); } catch { body = null; }
    return { status: resp.status, body };
  } catch {
    return { status: 502, body: { message: 'session not reachable' } };
  }
}
```

- [ ] **Step 3: Commit**

```bash
git add packages/optio-api/src/agent-input.ts packages/optio-api/src/__tests__/agent-input.test.ts
git commit -m "feat(optio-api): forwardAgentInput — resolve controlUpstream + POST to listener"
```

---

## Task 3: optio-api — wire the /api/widget-control route

**Files:**
- Modify: `packages/optio-api/src/adapters/fastify.ts` (add a raw route inside `registerOptioApi`, next to the other `app.get('/api/...')` routes, e.g. after the `/api/optio/instances` route ~line 465)

Depends on `forwardAgentInput` from Task 2 (signature pinned above). `resolveDb` is already imported/used in this file (see `registerWidgetProxy`). The global `onRequest` auth hook already gates this as a write method (POST → write role), so no extra auth code here.

- [ ] **Step 1: Add the import** — at the top of `fastify.ts`, with the other local imports:

```typescript
import { forwardAgentInput } from '../agent-input.js';
```

- [ ] **Step 2: Register the route** — inside `registerOptioApi`, after `app.get('/api/optio/instances', ...)`:

```typescript
  app.post(
    '/api/widget-control/:database/:prefix/:processId',
    async (request: any, reply: any) => {
      const { database, prefix, processId } = request.params as {
        database: string; prefix: string; processId: string;
      };
      const text = (request.body as { text?: unknown })?.text;
      if (typeof text !== 'string' || text.length === 0) {
        reply.code(400).send({ message: 'body.text (non-empty string) required' });
        return;
      }
      let db;
      try {
        ({ db } = resolveDb(dbOpts, { database, prefix }));
      } catch {
        reply.code(404).send({ message: 'session not running' });
        return;
      }
      const result = await forwardAgentInput(db, prefix, processId, text);
      reply.code(result.status).send(result.body);
    },
  );
```

- [ ] **Step 3: Commit**

```bash
git add packages/optio-api/src/adapters/fastify.ts
git commit -m "feat(optio-api): POST /api/widget-control route forwarding to session input listener"
```

---

## Task 4: optio-claudecode — input listener + serialization helper

**Files:**
- Create: `packages/optio-claudecode/src/optio_claudecode/input_listener.py`
- Modify: `packages/optio-claudecode/pyproject.toml` (add `aiohttp` to `dependencies`)
- Test: `packages/optio-claudecode/tests/test_input_listener.py`
- Test: `packages/optio-claudecode/tests/test_injection_serialize.py`

- [ ] **Step 1: Add aiohttp dependency** — in `pyproject.toml`, extend the `dependencies` list (currently lines 28–33):

```toml
dependencies = [
    "optio-core>=0.2,<0.3",
    "optio-host>=0.2,<0.3",
    "optio-agents>=0.2,<0.3",
    "asyncssh>=2.14",
    "aiohttp>=3.9",
]
```

- [ ] **Step 2: Implement the listener + helper** — create `input_listener.py`:

```python
"""In-session HTTP listener that receives human-typed messages and a small
lock helper that serializes them against system-message injection.

The listener runs INSIDE the session's asyncio loop (engine-side), so its
handler natively holds the session's injector and lock — no registry, no RPC,
no Mongo poll. It is reached through the API widget proxy exactly as ttyd is
(registered as controlUpstream). See
docs/2026-06-08-claudecode-input-channel-design.md.
"""
from __future__ import annotations

import asyncio
from typing import Awaitable, Callable

from aiohttp import web


def serialized(
    lock: asyncio.Lock, send: Callable[[str], Awaitable[None]],
) -> Callable[[str], Awaitable[None]]:
    """Wrap `send` so every call holds `lock` for the whole injection burst.
    Both the system path (_agent_sender) and the human path go through one
    such wrapper sharing one lock → bursts never interleave."""
    async def _send(text: str) -> None:
        async with lock:
            await send(text)
    return _send


async def start_input_listener(
    *,
    bind_iface: str,
    on_input: Callable[[str], Awaitable[None]],
) -> tuple[web.AppRunner, int]:
    """Start a one-route aiohttp app: POST /input {text} -> on_input(text).

    Returns (runner, port). Bind on port 0 (OS-assigned); the actual port is
    read back from the bound socket. on_input raises on injection failure;
    that becomes a 502 {ok:false, reason:"send-failed"}.
    """
    async def handle(request: web.Request) -> web.Response:
        try:
            payload = await request.json()
        except Exception:
            return web.json_response({"ok": False, "reason": "bad-json"}, status=400)
        text = payload.get("text")
        if not isinstance(text, str) or not text:
            return web.json_response({"ok": False, "reason": "bad-text"}, status=400)
        try:
            await on_input(text)
        except Exception:
            return web.json_response({"ok": False, "reason": "send-failed"}, status=502)
        return web.json_response({"ok": True})

    app = web.Application()
    app.router.add_post("/input", handle)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, bind_iface, 0)
    await site.start()
    # Read the OS-assigned port back from the bound server socket.
    server = site._server  # aiohttp exposes the asyncio.Server here
    port = server.sockets[0].getsockname()[1]
    return runner, port
```

- [ ] **Step 3: Test the listener** — create `tests/test_input_listener.py`:

```python
"""POST /input delivers to on_input and maps results to acks."""
import aiohttp

from optio_claudecode.input_listener import start_input_listener


async def _post(port, payload):
    async with aiohttp.ClientSession() as s:
        async with s.post(f"http://127.0.0.1:{port}/input", json=payload) as r:
            return r.status, await r.json()


async def test_listener_delivers_text_and_acks_ok():
    seen = []

    async def on_input(text):
        seen.append(text)

    runner, port = await start_input_listener(bind_iface="127.0.0.1", on_input=on_input)
    try:
        status, body = await _post(port, {"text": "hello world"})
        assert status == 200 and body == {"ok": True}
        assert seen == ["hello world"]
    finally:
        await runner.cleanup()


async def test_listener_502_on_injection_failure():
    async def on_input(text):
        raise RuntimeError("tmux boom")

    runner, port = await start_input_listener(bind_iface="127.0.0.1", on_input=on_input)
    try:
        status, body = await _post(port, {"text": "x"})
        assert status == 502 and body["reason"] == "send-failed"
    finally:
        await runner.cleanup()


async def test_listener_400_on_empty_text():
    async def on_input(text):
        pass

    runner, port = await start_input_listener(bind_iface="127.0.0.1", on_input=on_input)
    try:
        status, _ = await _post(port, {"text": ""})
        assert status == 400
    finally:
        await runner.cleanup()
```

- [ ] **Step 4: Test serialization** — create `tests/test_injection_serialize.py`:

```python
"""serialized() prevents two injection bursts from overlapping."""
import asyncio

from optio_claudecode.input_listener import serialized


async def test_serialized_no_interleave():
    lock = asyncio.Lock()
    active = 0
    max_active = 0

    async def raw_send(text):
        nonlocal active, max_active
        active += 1
        max_active = max(max_active, active)
        await asyncio.sleep(0.02)  # force overlap if unlocked
        active -= 1

    send = serialized(lock, raw_send)
    await asyncio.gather(*(send(f"m{i}") for i in range(5)))
    assert max_active == 1  # never two bursts at once
```

- [ ] **Step 5: Commit**

```bash
git add packages/optio-claudecode/src/optio_claudecode/input_listener.py \
        packages/optio-claudecode/pyproject.toml \
        packages/optio-claudecode/tests/test_input_listener.py \
        packages/optio-claudecode/tests/test_injection_serialize.py
git commit -m "feat(optio-claudecode): in-session input listener + serialized injector + aiohttp dep"
```

---

## Task 5: optio-claudecode — wire listener into the session

**Files:**
- Modify: `packages/optio-claudecode/src/optio_claudecode/session.py`

Depends on `start_input_listener` / `serialized` from Task 4 (signatures pinned). Adds: an `injection_lock`; routes `_agent_sender` through it; starts the listener after `set_widget_upstream` and registers `controlUpstream`; tears it down + clears the upstream in the `finally`; switches `ui_widget`.

- [ ] **Step 1: Import the helpers** — at the top of `session.py`, with the other `optio_claudecode` imports:

```python
from optio_claudecode.input_listener import serialized, start_input_listener
```

- [ ] **Step 2: Declare the new closure vars** — alongside the existing `launched_handle: ProcessHandle | None = None` block (session.py ~lines 92–96), add:

```python
    injection_lock = asyncio.Lock()
    input_runner = None  # aiohttp AppRunner | None
```

- [ ] **Step 3: Extend the body nonlocal** — in `_claudecode_body`, add `input_runner` to the nonlocal (session.py ~line 184):

```python
        nonlocal launched_handle, tmux_path, tmux_socket, tmux_session, input_runner
```

- [ ] **Step 4: Start the listener + register controlUpstream** — in `_claudecode_body`, immediately after the existing `set_widget_data` call (session.py ~lines 308–310), insert:

```python
        # In-session input listener: receives human messages from the
        # iframe-input widget via the API widget-control proxy and injects
        # them under the same lock as system messages (no garbling).
        async def _inject_raw(text: str) -> None:
            await host_actions.send_text_to_claude(
                host, tmux_path, tmux_socket, tmux_session, text,
            )
        input_runner, input_port = await start_input_listener(
            bind_iface=ttyd_iface,
            on_input=serialized(injection_lock, _inject_raw),
        )
        await ctx.set_control_upstream(f"http://{upstream_host}:{input_port}")
```

- [ ] **Step 5: Serialize the system path** — change `_agent_sender` (session.py ~lines 341–347) to hold the same lock:

```python
    async def _agent_sender(message: str) -> None:
        # Serialized against the human-input listener via injection_lock so a
        # system message can never interleave with a human burst.
        async with injection_lock:
            await host_actions.send_text_to_claude(
                host, tmux_path, tmux_socket, tmux_session, message,
            )
```

- [ ] **Step 6: Teardown in finally** — in the `finally` block, after the `teardown_session_tree` block (session.py ~after line 386), insert:

```python
        if input_runner is not None:
            try:
                await input_runner.cleanup()
            except Exception:
                _LOG.exception("input listener cleanup failed")
            try:
                await ctx.clear_control_upstream()
            except Exception:
                _LOG.exception("clear control upstream failed")
```

- [ ] **Step 7: Switch the widget type** — change session.py:860:

```python
        ui_widget="iframe-input",
```

- [ ] **Step 8: Commit**

```bash
git add packages/optio-claudecode/src/optio_claudecode/session.py
git commit -m "feat(optio-claudecode): wire input listener + serialized sender into session"
```

---

## Task 6: optio-ui — iframe-input composite widget

**Files:**
- Create: `packages/optio-ui/src/widgets/IframeInputWidget.tsx`
- Modify: `packages/optio-ui/src/index.ts` (export to trigger registration)
- Test: `packages/optio-ui/src/__tests__/IframeInputWidget.test.tsx`

The widget composes the existing `IframeWidget` (top) and a prose box (bottom). The submit URL is derived from `WidgetProps` (`apiBaseUrl` + `database` + `prefix` + `process._id`) — relative, so it works under Caddy/Vite/dashboard alike.

- [ ] **Step 1: Write the failing test** — create `src/__tests__/IframeInputWidget.test.tsx`:

```tsx
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import { IframeInputWidget } from '../widgets/IframeInputWidget.js';

function makeProps(over: any = {}) {
  return {
    process: { _id: 'pid123', name: 'n', widgetData: { iframeSrc: '{widgetProxyUrl}/' }, status: { state: 'running' } },
    apiBaseUrl: '',
    widgetProxyUrl: '/api/widget/db/gm/pid123/',
    prefix: 'gm',
    database: 'db',
    ...over,
  };
}

describe('IframeInputWidget', () => {
  beforeEach(() => vi.restoreAllMocks());

  it('renders the terminal iframe and the input box', () => {
    render(<IframeInputWidget {...makeProps()} />);
    expect(screen.getByTestId('optio-widget-iframe')).toBeInTheDocument();
    expect(screen.getByTestId('agent-input-box')).toBeInTheDocument();
  });

  it('Enter posts the text to the widget-control URL and clears on ok', async () => {
    const fetchMock = vi.fn(async () => new Response(JSON.stringify({ ok: true }), { status: 200 }));
    vi.stubGlobal('fetch', fetchMock);
    render(<IframeInputWidget {...makeProps()} />);
    const box = screen.getByTestId('agent-input-box') as HTMLTextAreaElement;
    fireEvent.change(box, { target: { value: 'hello' } });
    fireEvent.keyDown(box, { key: 'Enter' });
    await waitFor(() => expect(fetchMock).toHaveBeenCalledOnce());
    const [url, init] = fetchMock.mock.calls[0];
    expect(url).toBe('/api/widget-control/db/gm/pid123');
    expect(JSON.parse((init as any).body)).toEqual({ text: 'hello' });
    await waitFor(() => expect(box.value).toBe(''));
  });

  it('Shift+Enter does not submit', () => {
    const fetchMock = vi.fn();
    vi.stubGlobal('fetch', fetchMock);
    render(<IframeInputWidget {...makeProps()} />);
    const box = screen.getByTestId('agent-input-box');
    fireEvent.change(box, { target: { value: 'line1' } });
    fireEvent.keyDown(box, { key: 'Enter', shiftKey: true });
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it('keeps the text and shows an error when the session is not running', async () => {
    const fetchMock = vi.fn(async () => new Response(JSON.stringify({ message: 'session not running' }), { status: 404 }));
    vi.stubGlobal('fetch', fetchMock);
    render(<IframeInputWidget {...makeProps()} />);
    const box = screen.getByTestId('agent-input-box') as HTMLTextAreaElement;
    fireEvent.change(box, { target: { value: 'hi' } });
    fireEvent.keyDown(box, { key: 'Enter' });
    await waitFor(() => expect(screen.getByTestId('agent-input-error')).toBeInTheDocument());
    expect(box.value).toBe('hi');
  });
});
```

- [ ] **Step 2: Implement the widget** — create `src/widgets/IframeInputWidget.tsx`:

```tsx
import { useState } from 'react';
import type { WidgetProps } from './registry.js';
import { registerWidget } from './registry.js';
import { IframeWidget } from './IframeWidget.js';

export function IframeInputWidget(props: WidgetProps) {
  const [text, setText] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const controlUrl =
    `${props.apiBaseUrl}/api/widget-control/${encodeURIComponent(props.database ?? '')}` +
    `/${encodeURIComponent(props.prefix)}/${props.process._id}`;

  async function submit() {
    const body = text;
    if (!body || busy) return;
    setBusy(true);
    setError(null);
    try {
      const resp = await fetch(controlUrl, {
        method: 'POST',
        headers: { 'content-type': 'application/json' },
        body: JSON.stringify({ text: body }),
      });
      if (resp.ok) {
        setText('');
      } else {
        setError(resp.status === 404 ? 'Session not running.' : 'Send failed — retry.');
      }
    } catch {
      setError('Send failed — retry.');
    } finally {
      setBusy(false);
    }
  }

  function onKeyDown(e: React.KeyboardEvent<HTMLTextAreaElement>) {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault();
      void submit();
    }
  }

  return (
    <div style={{ display: 'flex', flexDirection: 'column', width: '100%', height: '100%' }}>
      <div style={{ flex: 1, minHeight: 0 }}>
        <IframeWidget {...props} />
      </div>
      <div style={{ borderTop: '1px solid #ddd', padding: 8, display: 'flex', gap: 8, alignItems: 'flex-end' }}>
        <textarea
          data-testid="agent-input-box"
          value={text}
          disabled={busy}
          onChange={(e) => setText(e.target.value)}
          onKeyDown={onKeyDown}
          placeholder="Message Claude…  (Enter to send, Shift+Enter for newline)"
          rows={2}
          style={{ flex: 1, resize: 'vertical', fontFamily: 'inherit' }}
        />
        <button data-testid="agent-input-send" onClick={() => void submit()} disabled={busy || !text}>
          Send
        </button>
        {error && (
          <span data-testid="agent-input-error" style={{ color: '#b00', alignSelf: 'center' }}>
            {error}
          </span>
        )}
      </div>
    </div>
  );
}

registerWidget('iframe-input', IframeInputWidget);
```

- [ ] **Step 3: Export to trigger registration** — in `src/index.ts`, after the existing `export { IframeWidget } ...` line (~line 44):

```typescript
export { IframeInputWidget } from './widgets/IframeInputWidget.js';
```

- [ ] **Step 4: Commit**

```bash
git add packages/optio-ui/src/widgets/IframeInputWidget.tsx \
        packages/optio-ui/src/index.ts \
        packages/optio-ui/src/__tests__/IframeInputWidget.test.tsx
git commit -m "feat(optio-ui): iframe-input composite widget (display iframe + prose box + ack)"
```

---

## Task V1: Verification — Python (run AFTER Tasks 1, 4, 5 land)

**Files:** none (verification only)

- [ ] **Step 1: Reinstall the worktree venv** (aiohttp is new; use the in-worktree `.venv`, never global):

```bash
.venv/bin/pip install -e packages/optio-core -e packages/optio-host -e packages/optio-agents -e packages/optio-claudecode
```

- [ ] **Step 2: Run optio-core tests**

Run: `cd packages/optio-core && ../../.venv/bin/python -m pytest -q`
Expected: all pass (incl. `tests/test_control_upstream.py`).

- [ ] **Step 3: Run optio-claudecode tests**

Run: `cd packages/optio-claudecode && ../../.venv/bin/python -m pytest -q`
Expected: all pass (incl. `test_input_listener.py`, `test_injection_serialize.py`); existing tmux/session tests still green.

- [ ] **Step 4: Commit any fixes** discovered here (small errors are expected — that's the point of deferred verification):

```bash
git add -A && git commit -m "fix(optio-claudecode): address verification findings (python)"
```

---

## Task V2: Verification — TypeScript (run AFTER Tasks 2, 3, 6 land)

**Files:** none (verification only)

- [ ] **Step 1: Typecheck + test optio-api**

Run: `pnpm --filter optio-api build && pnpm --filter optio-api test`
Expected: `tsc` clean; vitest green (incl. `agent-input.test.ts`).

- [ ] **Step 2: Typecheck + test optio-ui**

Run: `pnpm --filter optio-ui build && pnpm --filter optio-ui test`
Expected: `tsc` clean; vitest green (incl. `IframeInputWidget.test.tsx`).

- [ ] **Step 3: Lint optio-api**

Run: `pnpm --filter optio-api lint`
Expected: no errors.

- [ ] **Step 4: Commit any fixes**

```bash
git add -A && git commit -m "fix(optio-api,optio-ui): address verification findings (typescript)"
```

---

## Self-review notes (spec coverage)

- Composite widget (display iframe + prose box) → T6.
- Prose path → server → session → `send_text_to_claude` → T2/T3 (api) + T4/T5 (listener) + T6 (UI).
- Single serialized injection lock (human + system) → T4 (`serialized`) + T5 (`_agent_sender` + `on_input` share `injection_lock`).
- `controlUpstream` Mongo field + register/clear → T1 + T5.
- `/api/widget-control` HTTP-only route, no WS, existing auth → T3.
- Engine-side listener, binds `bind_iface`, no `establish_tunnel` → T5 (uses `ttyd_iface`/`upstream_host`).
- Delivery ack (delivered / not-running / failed) → listener acks (T4) → route status (T2/T3) → widget states (T6).
- Human text raw (no `System:` prefix) → T5 `_inject_raw` (no prefix; prefix is only in optio-agents `send_to_agent`, untouched).
- Verification deferred, batched → V1 (python), V2 (ts).

## Release order (after merge, separate step — not part of execution)

Deps first: `optio-core` → `optio-api` → `optio-claudecode` → `optio-ui`.
