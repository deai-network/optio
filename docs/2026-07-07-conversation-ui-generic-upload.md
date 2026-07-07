# Generic Conversation-UI File Upload Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace all 7 engines' file-upload implementations with one generic, agent-agnostic path that materializes every upload into the task's workdir and optionally invokes a per-task `on_upload` callback.

**Architecture:** The widget POSTs a file to a new generic optio-api route; the API streams the bytes into **GridFS** (bytes never cross Redis) and then calls a new **clamator RPC** `materialize_upload(process_id, blob_id, filename)`. The engine-side handler (in the optio-core process, reachable by `process_id` like `cancel`) looks up the running task's **in-process registered writer**, which reads the GridFS blob and writes it into `<workdir>/uploads/<original-filename>` via the task's `Host` (works local and remote-SSH), then — if the task registered an `on_upload` callback — invokes it. The extracted writer/materialize logic lives once in `optio-agents`; per-engine code is just config + a one-line registration.

**Tech Stack:** Python (optio-agents, optio-core, engine packages), TypeScript (optio-api Fastify, optio-contracts zod + codegen, optio-conversation-ui React), MongoDB/GridFS, clamator-over-redis RPC.

## Global Constraints

- Python tests: `/home/csillag/deai/optio/.venv/bin/python -m pytest <path> -q`. TS: `node_modules/.bin/tsc --noEmit` and the conversation-ui vitest runner. NEVER `npx`.
- Branch: `csillag/antigravity`. No worktree. Commit own files only, explicit `git add <paths>` (never `-A`). No `Co-Authored-By` trailer.
- The uploaded file MUST land at `<workdir>/uploads/<basename>` where `<basename>` preserves the original filename but is basenamed (directory components stripped) and traversal-guarded (`realpath` must stay under `<workdir>/uploads`). Same-name re-upload overwrites (no dedup-suffix in this version).
- `on_upload` is OPTIONAL and ADDITIVE: the existing `System: upload received, stored in <path>` LLM announce still fires whether or not `on_upload` is set. Signature mirrors `on_deliverable` (`packages/optio-agents/.../protocol/session.py:132`, invoked `(hook_ctx, path, text)`), minus `text`: `async def on_upload(hook_ctx, path)`.
- The materialize path handles ALL file types uniformly, images included (agents read images off disk if they want). No inline data-URL parts anywhere after this plan.
- clamator contract change requires re-running the codegen that produces `packages/optio-core/src/optio_core/_generated/optio_engine.py` and `packages/optio-api/src/_generated/optio-engine.ts` from `packages/optio-contracts`. Find and run the repo's codegen command (check `packages/optio-contracts/package.json` scripts, e.g. `pnpm --filter optio-contracts codegen`); do NOT hand-edit generated files.

---

## File Structure

**Created:**
- `packages/optio-agents/src/optio_agents/uploads.py` — the agent-agnostic writer + materialize helper + `UploadCallback` type + `on_upload` invocation. The single shared copy.
- `packages/optio-conversation-ui/src/uploads.ts` — the shared client helper: `uploadFiles(uploadRouteUrl, attachments, maxBytes)` (multipart POST to the new route) + `bundleUploadNotice(paths, body)` (the `System:` prefixing lifted from `ClaudeCodeView`).

**Modified — control plane:**
- `packages/optio-contracts/src/optio-engine-to-api.ts` — add the `materializeUpload` method definition.
- `packages/optio-core/src/optio_core/_engine_service.py` — implement `materialize_upload`.
- `packages/optio-core/src/optio_core/context.py` — add `ctx.register_upload_writer(writer)` / `clear_upload_writer()`.
- `packages/optio-core/src/optio_core/lifecycle.py` — the in-process registry + `Optio.materialize_upload(process_id, blob_id, filename)` that resolves the writer.
- `packages/optio-api/src/adapters/fastify.ts` — add `POST /api/widget-upload/:database/:prefix/:processId`.
- `packages/optio-api/src/upload-forward.ts` (new) — `forwardUpload(...)`: stream to GridFS + call `materializeUpload` (mirror of `agent-input.ts`).

**Modified — per engine (×7):** `types.py` (add `on_upload` field, keep `show_file_upload`), `session.py` (register the writer + `on_upload`, remove the old listener `/upload` wiring), and remove the old `_write_upload`/`_handle_upload` copies. opencode additionally loses its inline `file`-part construction.

**Modified — client (×7):** each `*View.tsx` `onSend` uses the shared `uploads.ts` helper instead of inline `readAsDataUrl` file parts or per-engine `uploadFiles`.

**Deleted after migration:** the `_handle_upload`/`/upload` route + `upload_writer` params from `optio-claudecode/.../conversation_listener.py` (and its download twin only if it shares no other use — verify), and the per-engine `_write_upload` closures.

---

## Phase A — optio-agents: the shared materialize helper

### Task 1: `UploadCallback` type + `safe_upload_relpath` guard

**Files:**
- Create: `packages/optio-agents/src/optio_agents/uploads.py`
- Test: `packages/optio-agents/tests/test_uploads.py`

**Interfaces — Produces:**
- `UploadCallback = Callable[["HookContext", str], Awaitable[None]]`
- `def safe_upload_relpath(filename: str) -> str` — returns `"uploads/<basename>"`, basenamed + sanitized so it cannot escape `uploads/`. Raises `ValueError` on an empty/`.`/`..`-only name.

- [ ] **Step 1: Write the failing test**
```python
# packages/optio-agents/tests/test_uploads.py
import pytest
from optio_agents.uploads import safe_upload_relpath

def test_preserves_original_name_with_spaces_and_unicode():
    assert safe_upload_relpath("My Report (v2).md") == "uploads/My Report (v2).md"
    assert safe_upload_relpath("résumé.pdf") == "uploads/résumé.pdf"

def test_strips_directory_components():
    assert safe_upload_relpath("/etc/passwd") == "uploads/passwd"
    assert safe_upload_relpath("a/b/c.txt") == "uploads/c.txt"

def test_rejects_traversal_and_empty():
    for bad in ["..", ".", "", "   ", "../../x/..", "/"]:
        with pytest.raises(ValueError):
            safe_upload_relpath(bad)
```

- [ ] **Step 2: Run test to verify it fails**
Run: `.venv/bin/python -m pytest packages/optio-agents/tests/test_uploads.py -q`
Expected: FAIL (ImportError / no module `optio_agents.uploads`).

- [ ] **Step 3: Write minimal implementation**
```python
# packages/optio-agents/src/optio_agents/uploads.py
"""Agent-agnostic file-upload materialization, shared by every engine.

A conversation task registers an upload writer (via ctx.register_upload_writer);
the central clamator ``materialize_upload`` handler resolves it by process id and
calls ``materialize`` below with the GridFS blob bytes. Extracted from the original
claudecode ``_handle_upload``/``_write_upload`` so all engines share one copy.
"""
from __future__ import annotations

import os
from typing import TYPE_CHECKING, Awaitable, Callable

if TYPE_CHECKING:
    from optio_agents.context import HookContext

UploadCallback = Callable[["HookContext", str], Awaitable[None]]

_UPLOADS_DIR = "uploads"


def safe_upload_relpath(filename: str) -> str:
    """`uploads/<basename>` preserving the human name but confined to uploads/.

    Strips any directory components (client-supplied paths are untrusted) and
    rejects names that resolve to nothing / escape the dir. The human-readable
    characters (spaces, unicode, dots) are kept — only path structure is removed.
    """
    base = os.path.basename(filename.strip().replace("\\", "/").rstrip("/"))
    if not base or base in (".", ".."):
        raise ValueError(f"unsafe upload filename: {filename!r}")
    rel = f"{_UPLOADS_DIR}/{base}"
    # Defense in depth: the normalized path must stay directly under uploads/.
    if os.path.normpath(rel) != rel or "/" in base:
        raise ValueError(f"unsafe upload filename: {filename!r}")
    return rel
```

- [ ] **Step 4: Run test to verify it passes**
Run: `.venv/bin/python -m pytest packages/optio-agents/tests/test_uploads.py -q`
Expected: PASS (3 tests).

- [ ] **Step 5: Commit**
```bash
git add packages/optio-agents/src/optio_agents/uploads.py packages/optio-agents/tests/test_uploads.py
git commit -m "feat(optio-agents): safe upload relpath guard + UploadCallback type"
```

### Task 2: `materialize()` — write blob to workdir + fire on_upload

**Files:**
- Modify: `packages/optio-agents/src/optio_agents/uploads.py`
- Test: `packages/optio-agents/tests/test_uploads.py`

**Interfaces — Consumes:** `safe_upload_relpath` (Task 1); `HookContext` from `optio_agents.context`; the `Host.put_file_to_host(data: bytes, path: str)` protocol (`optio-host/.../host.py:100`).
**Produces:**
- `async def materialize(host, workdir, filename, data, hook_ctx=None, on_upload=None) -> str` — writes `data` to `<workdir>/<relpath>` via `host.put_file_to_host`, returns the `uploads/<name>` relpath; if `on_upload` is not None, `await on_upload(hook_ctx, relpath)` (guarded so a raising callback is logged, not fatal).

- [ ] **Step 1: Write the failing test**
```python
# add to packages/optio-agents/tests/test_uploads.py
import asyncio
from optio_agents.uploads import materialize

class _FakeHost:
    def __init__(self): self.written = {}
    async def put_file_to_host(self, data, path): self.written[path] = data

async def test_materialize_writes_and_returns_relpath():
    h = _FakeHost()
    rel = await materialize(h, "/wd", "notes.md", b"hello")
    assert rel == "uploads/notes.md"
    assert h.written["/wd/uploads/notes.md"] == b"hello"

async def test_materialize_fires_on_upload_with_relpath():
    h = _FakeHost(); seen = []
    async def cb(hook_ctx, path): seen.append((hook_ctx, path))
    rel = await materialize(h, "/wd", "a b.txt", b"x", hook_ctx="HC", on_upload=cb)
    assert rel == "uploads/a b.txt"
    assert seen == [("HC", "uploads/a b.txt")]

async def test_materialize_swallows_on_upload_error():
    h = _FakeHost()
    async def cb(hook_ctx, path): raise RuntimeError("boom")
    rel = await materialize(h, "/wd", "f.txt", b"x", hook_ctx=None, on_upload=cb)
    assert rel == "uploads/f.txt"  # write succeeded, callback error not fatal
```
(Mark the async tests with the repo's asyncio pytest convention — check `packages/optio-agents/tests/conftest.py` / an existing async test for the exact marker/mode.)

- [ ] **Step 2: Run to verify it fails**
Run: `.venv/bin/python -m pytest packages/optio-agents/tests/test_uploads.py -q` → FAIL (`materialize` undefined).

- [ ] **Step 3: Implement**
```python
# append to packages/optio-agents/src/optio_agents/uploads.py
import logging
_LOG = logging.getLogger("optio_agents.uploads")

async def materialize(host, workdir, filename, data, hook_ctx=None, on_upload=None):
    """Write an uploaded blob into <workdir>/uploads/<name> and fire on_upload.

    Runs in the task's own process (only it holds the live Host, which may be a
    remote SFTP connection). Returns the workdir-relative path. on_upload is
    additive to the System: LLM announce the caller emits separately.
    """
    rel = safe_upload_relpath(filename)
    abs_path = f"{workdir.rstrip('/')}/{rel}"
    await host.put_file_to_host(data, abs_path)
    if on_upload is not None:
        try:
            await on_upload(hook_ctx, rel)
        except Exception:
            _LOG.exception("on_upload callback raised for %s", rel)
    return rel
```

- [ ] **Step 4: Run to verify it passes**
Run: `.venv/bin/python -m pytest packages/optio-agents/tests/test_uploads.py -q` → PASS (6 tests).

- [ ] **Step 5: Commit**
```bash
git add packages/optio-agents/src/optio_agents/uploads.py packages/optio-agents/tests/test_uploads.py
git commit -m "feat(optio-agents): materialize() writes upload to workdir and fires on_upload"
```

---

## Phase B — optio-core: in-process writer registry + clamator method

### Task 3: In-process upload-writer registry + ctx.register_upload_writer

**Files:**
- Modify: `packages/optio-core/src/optio_core/context.py` (near `set_control_upstream`, ~line 262)
- Modify: `packages/optio-core/src/optio_core/lifecycle.py` (the `Optio` class — add the registry + lookup)
- Test: `packages/optio-core/tests/test_upload_registry.py`

**Interfaces — Produces:**
- On `ProcessContext`: `def register_upload_writer(self, writer)` and `def clear_upload_writer(self)`, where `writer: Callable[[str, bytes], Awaitable[str]]` (filename, bytes) -> relpath. Stored in an in-process dict on the owning `Optio` keyed by the process's `ObjectId`.
- On `Optio`: `async def materialize_upload(self, process_id: str, data: bytes, filename: str) -> str` — resolves the writer by `process_id` (map processId→oid the same way `cancel` does) and calls it; raises a typed "no upload writer registered" error if absent (task not accepting uploads / already gone).

**Note for implementer:** `ProcessContext` already holds a back-reference to the `Optio`/store (it calls `self._db`, `self._process_oid`, and store helpers). Add a plain dict `self._upload_writers: dict[ObjectId, Callable] = {}` on `Optio.__init__`; `register_upload_writer` sets `optio._upload_writers[self._process_oid] = writer`; `clear_upload_writer` pops it. Confirm how `ProcessContext` reaches its `Optio` (grep for how `cancel`/`self._optio` or the store is threaded in `context.py`) and use that same handle.

- [ ] **Step 1: Write the failing test** (registry round-trip; unknown-process raises)
```python
# packages/optio-core/tests/test_upload_registry.py — sketch; adapt to how the
# suite constructs an Optio + a ProcessContext (mirror an existing context test).
async def test_register_and_materialize(optio, ctx):
    async def writer(filename, data): return f"uploads/{filename}"
    ctx.register_upload_writer(writer)
    rel = await optio.materialize_upload(ctx.process_id, b"x", "f.txt")
    assert rel == "uploads/f.txt"

async def test_materialize_unknown_process_raises(optio):
    with pytest.raises(Exception):
        await optio.materialize_upload("does-not-exist", b"x", "f.txt")

async def test_clear_removes_writer(optio, ctx):
    ctx.register_upload_writer(lambda fn, d: "x")
    ctx.clear_upload_writer()
    with pytest.raises(Exception):
        await optio.materialize_upload(ctx.process_id, b"x", "f.txt")
```

- [ ] **Step 2: Run → FAIL** (`register_upload_writer` undefined).
- [ ] **Step 3: Implement** the dict on `Optio`, the two `ProcessContext` methods (mirror `set_control_upstream`'s structure), and `Optio.materialize_upload` (mirror `Optio.cancel`'s processId→oid resolution, then call the writer).
- [ ] **Step 4: Run → PASS.**
- [ ] **Step 5: Commit**
```bash
git add packages/optio-core/src/optio_core/context.py packages/optio-core/src/optio_core/lifecycle.py packages/optio-core/tests/test_upload_registry.py
git commit -m "feat(optio-core): in-process upload-writer registry + Optio.materialize_upload"
```

### Task 4: clamator `materializeUpload` contract + engine-service impl

**Files:**
- Modify: `packages/optio-contracts/src/optio-engine-to-api.ts` (add the method next to `cancel`, ~line 60)
- Regenerate: `packages/optio-core/src/optio_core/_generated/optio_engine.py`, `packages/optio-api/src/_generated/optio-engine.ts` (via the codegen command — DO NOT hand-edit)
- Modify: `packages/optio-core/src/optio_core/_engine_service.py` (implement the method)
- Test: `packages/optio-core/tests/test_engine_service_upload.py`

**Interfaces — Consumes:** `Optio.materialize_upload` (Task 3).
**Produces:** clamator method `materializeUpload` — params `{ processId: string, blobId: string, filename: string }`, result `{ ok: true, path: string } | { ok: false, reason: string }`. Bytes are NOT in the params (only the GridFS `blobId`).

- [ ] **Step 1: Write the failing test** — an `OptioEngineService.materialize_upload(params)` that reads the GridFS blob by `blobId`, calls `self._optio.materialize_upload(processId, data, filename)`, returns `{ok, path}`. Mock GridFS read + a stub `Optio`.
```python
# packages/optio-core/tests/test_engine_service_upload.py — sketch
async def test_materialize_upload_reads_blob_and_writes(engine_service, fake_gridfs, fake_optio):
    blob_id = await fake_gridfs.put(b"hello")
    res = await engine_service.materialize_upload(
        MaterializeUploadParams(process_id="p1", blob_id=str(blob_id), filename="n.md"))
    assert res.ok is True and res.path == "uploads/n.md"
    assert fake_optio.calls == [("p1", b"hello", "n.md")]
```

- [ ] **Step 2:** Add the `defineMethod` to `optio-engine-to-api.ts`:
```typescript
// packages/optio-contracts/src/optio-engine-to-api.ts (near cancel)
const materializeUploadResult = z.discriminatedUnion('ok', [
  z.object({ ok: z.literal(true), path: z.string() }),
  z.object({ ok: z.literal(false), reason: z.string() }),
]);
// inside the methods object:
materializeUpload: defineMethod({
  params: z.object({ processId: z.string(), blobId: z.string(), filename: z.string() }),
  result: materializeUploadResult,
}),
```

- [ ] **Step 3:** Run the codegen (find it in `packages/optio-contracts/package.json`; likely `pnpm --filter @optio/contracts codegen` or a root script). Verify `MaterializeUploadParams`/`MaterializeUploadResult` now appear in both `_generated` files. Run `node_modules/.bin/tsc --noEmit` for the TS packages.

- [ ] **Step 4:** Implement in `_engine_service.py` (import the generated params/result; read the blob via the same GridFS handle optio-core already uses for snapshots — grep `store_blob`/`load_blob`/`GridFS` in optio-core for the exact accessor):
```python
async def materialize_upload(self, params):  # MaterializeUploadParams
    from optio_core._generated.optio_engine import MaterializeUploadResult
    try:
        data = await self._optio.read_blob_bytes(ObjectId(params.blob_id))  # adapt name
        path = await self._optio.materialize_upload(params.process_id, data, params.filename)
        return MaterializeUploadResult(ok=True, path=path)
    except Exception as exc:
        return MaterializeUploadResult(ok=False, reason=repr(exc))
```
(Adapt `read_blob_bytes` to the real optio-core GridFS read API; if none is exposed at the Optio level, add a small `Optio.read_blob_bytes(oid)` that reads the GridFS bucket the same way snapshot loading does.)

- [ ] **Step 5:** Run `.venv/bin/python -m pytest packages/optio-core/tests/test_engine_service_upload.py -q` → PASS. Commit:
```bash
git add packages/optio-contracts/src/optio-engine-to-api.ts packages/optio-core/src/optio_core/_generated/optio_engine.py packages/optio-api/src/_generated/optio-engine.ts packages/optio-core/src/optio_core/_engine_service.py packages/optio-core/tests/test_engine_service_upload.py
git commit -m "feat: clamator materializeUpload RPC (blob id in, workdir path out)"
```

---

## Phase C — optio-api: the generic upload route

### Task 5: `POST /api/widget-upload/:database/:prefix/:processId`

**Files:**
- Create: `packages/optio-api/src/upload-forward.ts` (mirror `packages/optio-api/src/agent-input.ts`)
- Modify: `packages/optio-api/src/adapters/fastify.ts` (register the route next to the widget-control route at ~line 516)
- Test: `packages/optio-api/src/__tests__/upload-forward.test.ts`

**Interfaces — Consumes:** the generated `materializeUpload` client method (Task 4); a GridFS bucket over the same Mongo db the API already holds.
**Produces:** `async function forwardUpload(deps, database, prefix, processId, filename, stream) -> { ok, path } | { ok:false }` — pipes `stream` into GridFS (`openUploadStream`), gets the `blobId`, calls `engine.materializeUpload({ processId, blobId, filename })`, and (on success or failure) best-effort deletes the GridFS blob afterward (it's transient staging), returning the result. Route accepts `multipart/form-data` with a `file` field; uses `@fastify/multipart` (confirm it's already a dep — claudecode's `/upload` handler proves multipart is available in the stack) and streams each part without buffering the whole file.

- [ ] **Step 1: Write the failing test** — a multipart POST to the route stores to a fake GridFS, calls a fake `materializeUpload` with the returned blobId, returns `{ok, path}`; the staged blob is deleted after. Mirror `agent-input.test.ts` if present.
- [ ] **Step 2: Run → FAIL.**
- [ ] **Step 3: Implement** `upload-forward.ts` (mirror `agent-input.ts`'s dependency-injection shape) and the fastify route (mirror `fastify.ts:516-544` — same `:database/:prefix/:processId` params, auth, error mapping — but consuming `request.file()`/parts and calling `forwardUpload`). Return `{ ok: true, files: [{ filename, path }] }` for the client.
- [ ] **Step 4: Run → PASS;** `node_modules/.bin/tsc --noEmit` clean.
- [ ] **Step 5: Commit**
```bash
git add packages/optio-api/src/upload-forward.ts packages/optio-api/src/adapters/fastify.ts packages/optio-api/src/__tests__/upload-forward.test.ts
git commit -m "feat(optio-api): generic POST /api/widget-upload → GridFS stage + materializeUpload RPC"
```

---

## Phase D — conversation-ui: shared client helper

### Task 6: `uploads.ts` — shared `uploadFiles` + `bundleUploadNotice`

**Files:**
- Create: `packages/optio-conversation-ui/src/uploads.ts`
- Test: `packages/optio-conversation-ui/src/__tests__/uploads.test.ts`

**Interfaces — Produces:**
- `async function uploadFiles(uploadUrl: string, attachments: Attachment[], maxBytes: number): Promise<string[] | null>` — multipart POST each file to `uploadUrl` (the `/api/widget-upload/...` URL, provided via widgetData), returns the stored relpaths (`uploads/<name>`), or `null` on any failure/oversize. Lifted from `ClaudeCodeView.tsx:74-85`.
- `function bundleUploadNotice(paths: string[], body: string): string` — `paths.map(p => \`System: upload received, stored in ${p}\`).join('\n') + '\n\n' + body` (mirror `ClaudeCodeView.tsx:117-119`), returning `body` unchanged when `paths` is empty.

- [ ] **Step 1: Write the failing test** — a fake `fetch` records the multipart POST; `uploadFiles` returns the parsed relpaths; oversize → `null`; `bundleUploadNotice` prefixes correctly and is a no-op for `[]`.
- [ ] **Step 2: Run → FAIL.**
- [ ] **Step 3: Implement** (copy the claudecode client logic, parameterized on `uploadUrl`).
- [ ] **Step 4: Run → PASS;** tsc clean.
- [ ] **Step 5: Commit**
```bash
git add packages/optio-conversation-ui/src/uploads.ts packages/optio-conversation-ui/src/__tests__/uploads.test.ts
git commit -m "feat(conversation-ui): shared uploadFiles + bundleUploadNotice helpers"
```

### Task 7: publish the upload URL in widgetData (shared plumbing)

**Files:**
- Modify: whichever shared type carries widgetData to the views (`ConversationViewProps` / `chat.ts` — grep `showFileUpload` in conversation-ui to find where views read it); add an `uploadUrl` field derived from the `{widgetProxyUrl}`-sibling `/api/widget-upload/...` token.
- Modify: the engine-side `set_widget_data` payloads (Task 9's per-engine work references this) to include the resolved upload URL. Define the token here so all 7 engines set the same key.

**Interfaces — Produces:** widgetData key `uploadUrl: string` (a `{...}`-token the iframe resolver expands to the API's `/api/widget-upload/<db>/<prefix>/<pid>`), read by every View.

- [ ] **Step 1:** Write a test asserting a View passed `widgetData.uploadUrl` forwards it into `uploadFiles`. (Or fold this assertion into Task 8's first View.)
- [ ] **Step 2–4:** Add the field + resolver token; tsc clean.
- [ ] **Step 5: Commit**
```bash
git add packages/optio-conversation-ui/src/<viewprops-file>.ts
git commit -m "feat(conversation-ui): widgetData.uploadUrl for the generic upload route"
```

---

## Phase E — per-engine migration (one task each, all mirror the same shape)

Each engine task does the SAME four things; they are separate tasks only so a reviewer can gate each engine independently and so a live regression in one engine doesn't block the others.

**Per-engine shape (applies to Tasks 8–14):**
1. `types.py`: add `on_upload: UploadCallback | None = None` (import from `optio_agents.uploads`); keep `show_file_upload`.
2. `session.py`: in the conversation-ui branch, register the writer + on_upload:
```python
from optio_agents.uploads import materialize
async def _upload_writer(filename: str, data: bytes) -> str:
    return await materialize(
        host, host.workdir, filename, data,
        hook_ctx=hook_ctx, on_upload=config.on_upload,
    )
ctx.register_upload_writer(_upload_writer)
await ctx.set_widget_data({..., "uploadUrl": _upload_url_token})   # merge into existing widgetData
# teardown: ctx.clear_upload_writer()
```
   (`hook_ctx` is the same `HookContext` the engine already builds for `on_deliverable` — reuse it; grep the engine's `session.py` for where `on_deliverable`/`HookContext` is constructed.)
3. Remove the engine's OLD upload path: the `_write_upload` closure and any `upload_writer=`/`/upload` wiring. For opencode specifically, delete the inline `fileParts` construction (client side, Task 15).
4. `*View.tsx`: `onSend` uses `uploadFiles(widgetData.uploadUrl, attachments, maxUploadBytes)` + `bundleUploadNotice`, then sends the resulting text through the engine's existing send channel — NO `file` parts.

- [ ] **Task 8: claudecode** — additionally REMOVE `_handle_upload` + the `POST /upload` route + `upload_writer`/`max_upload_bytes` ctor params from `conversation_listener.py:229-256,312` and the `_write_upload` closure at `session.py:508-511`. Verify the `/download` twin (`_read_download`) is either kept (unrelated) or, if it shares the removed plumbing, preserved separately. TDD: a task with `show_file_upload` registers a writer and `materialize` lands the file; the listener no longer exposes `/upload`. Full `optio-claudecode` suite green. Commit `feat(optio-claudecode): migrate upload to the generic materialize path`.
- [ ] **Task 9: antigravity** — remove `_write_upload` (`session.py:438-441`) + `upload_writer=` wiring. Same TDD. Commit.
- [ ] **Task 10: kimicode** — same. Commit.
- [ ] **Task 11: grok** — same. Commit.
- [ ] **Task 12: cursor** — same. Commit.
- [ ] **Task 13: codex** — same. Commit.
- [ ] **Task 14: opencode** — register the writer + on_upload (opencode had NO writer before). No listener to strip; the removal is client-side (Task 15). TDD: `materialize` lands the file for an opencode task. Commit `feat(optio-opencode): real workdir-write upload via the generic path`.

Each engine task, Steps: (1) write the failing test that the engine registers a writer and `materialize` writes the file + fires `on_upload`; (2) run → FAIL; (3) implement the per-engine shape above; (4) run the engine suite → PASS; (5) commit that engine's files only.

### Task 15: migrate all 7 Views to the shared helper

**Files:** `packages/optio-conversation-ui/src/{claudecode,antigravity,kimicode,grok,cursor,codex,opencode}/*View.tsx`
- [ ] **Step 1:** For each View, write/adjust a test (mirror `__tests__/conversation-upload.test.tsx`) asserting `onSend` with an attachment POSTs multipart to `widgetData.uploadUrl` (NOT an inline `file` part / data-URL) and sends `bundleUploadNotice(...)` text.
- [ ] **Step 2: Run → FAIL** (opencode still builds `fileParts`; claudecode uses its own `uploadFiles`).
- [ ] **Step 3:** Replace each View's upload code with the shared `uploadFiles` + `bundleUploadNotice`. Delete opencode's `readAsDataUrl` `fileParts` block (`OpencodeView.tsx:206-212`) and claudecode's private `uploadFiles` (now shared).
- [ ] **Step 4: Run → PASS;** `node_modules/.bin/tsc --noEmit` clean; full conversation-ui vitest green.
- [ ] **Step 5: Commit**
```bash
git add packages/optio-conversation-ui/src/*/[A-Z]*View.tsx packages/optio-conversation-ui/src/__tests__/conversation-upload.test.tsx
git commit -m "feat(conversation-ui): all 7 Views upload via the shared route + System: bundling"
```

---

## Phase F — verification & cleanup

### Task 16: on_upload end-to-end + demo wiring + dead-code sweep

**Files:** one engine's `tests/` (an end-to-end on_upload test); `packages/optio-demo/src/optio_demo/tasks/*.py` (optional: demonstrate `on_upload` on one demo task, mirroring `_on_deliverable`); grep sweep for orphaned upload code.

- [ ] **Step 1:** Write an engine-level test (pick claudecode or opencode) that sets `on_upload`, drives a fake upload through `materialize`, and asserts the callback ran with `("uploads/<name>")` AND the `System:` announce still happened. Run → implement if needed → PASS.
- [ ] **Step 2:** Grep for any remaining `_write_upload`, `_handle_upload`, `readAsDataUrl`-as-upload, `type: 'file'` upload parts across all packages; delete stragglers. Run each affected suite.
- [ ] **Step 3:** Run the FULL suites for every touched package (7 engines + optio-agents + optio-core + optio-api + conversation-ui) and `tsc --noEmit`. Record counts. Docker-gated remote tests: run if Docker available (RemoteHost upload path — the SFTP write matters here).
- [ ] **Step 4: Commit** the demo/on_upload example + any cleanup.
```bash
git add packages/optio-demo/... <engine>/tests/...
git commit -m "test: on_upload end-to-end + remove orphaned per-engine upload code"
```

---

## Self-Review notes

- **Spec coverage:** GridFS-not-Redis (Tasks 4–5), clamator method (4), in-process writer registry solving the process-local-Host problem (3), original-filename-basenamed-traversal-guarded (1), on_upload additive to System: (2, per-engine 8–14), all-7 uniform incl. images (8–15), shared optio-agents writer + shared client helper (1–2, 6), opencode inline removal (14–15). Covered.
- **Ordering:** A→B→C→D are the shared substrate (must land before per-engine); E depends on all of them; F last. Within E, engines are independent (parallelizable), but each needs A–D merged.
- **Open confirmations the implementer must resolve from code (not guesses):** the exact codegen command (Task 4); how `ProcessContext` reaches its `Optio` for the registry (Task 3); the optio-core GridFS read accessor name (Task 4); whether `@fastify/multipart` is registered (Task 5); the widgetData→View plumbing file for `uploadUrl` (Task 7); each engine's `HookContext` build site (Tasks 8–14).
