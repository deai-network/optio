# Conversation-Mode File Upload — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: superpowers:subagent-driven-development / executing-plans. This plan is **parallel-shaped**: one owner per file, file-disjoint tasks run **concurrently**, and ALL verification (pytest/vitest/tsc) is deferred to the final task. The tree will not compile mid-execution — expected.

**Goal:** Let the operator attach files to a conversation-mode turn from the dashboard widget. opencode delivers them inline (a data-URL `file` part on `prompt_async`); Claude Code uploads them into the session workdir (`POST /upload` → `uploads/`) and references them via the documented `System:` convention in the same prompt turn.

**Architecture:** Shared attach UI in both engine views. opencode is pure client-side (build `file` parts). Claude Code adds a `POST /upload` endpoint on the per-task conversation listener that writes bytes to the workdir via the Host abstraction (works local + remote); the widget then sends one turn = `System:` upload lines + the prompt. Verified earlier: opencode accepts data-URL `file` parts (HTTP 204); Claude Code's `Read` tool renders workdir images visually.

**Tech Stack:** TypeScript + React + antd (optio-conversation-ui); Python aiohttp listener + asyncio (optio-claudecode); Python dataclass config (optio-opencode).

## Global Constraints

- Parallel-shaped: one owner per file; verification only in the final task.
- Both engines shipped together (parity rule). Builds on the committed model-switch code (listener + views already extended).
- Decisions (delegated): `show_file_upload: bool = False` gate on both task configs; `max_upload_bytes: int = 10_000_000` (~10 MB) per file; allow-all mime under the cap; `uploads/` rides into the workdir/snapshot (no prune).
- Python env = repo-root venv `/home/csillag/deai/optio/.venv/bin/python`; optio pytest prefixed `OPTIO_SKIP_PREFLIGHT_TESTS=1`.
- TS tooling from `packages/optio-conversation-ui`: `node_modules/.bin/{vitest,tsc}`. Never npx.
- Branch `csillag/opencode-frontend`, in-place. No push, no merge.

## Pinned Interfaces

```
# optio-claudecode listener
ConversationListener(conversation, *, password, initial_events=None,
                     upload_writer=None, max_upload_bytes=10_000_000)
    # upload_writer: Callable[[str, bytes], Awaitable[str]]  (filename, data) -> stored relpath
    POST /upload  (multipart/form-data; one or more file parts, field name "file")
        -> for each: enforce max_upload_bytes, await upload_writer(filename, bytes)
        200 {"ok":true,"files":[{"filename":str,"path":str}]}
        413 {"ok":false,"reason":"too-large"} | 400 bad | 409 {"ok":false,"reason":"no-writer"}

# optio-claudecode session.py provides the writer:
async def _write_upload(name, data) -> str:   # sanitize -> put_file_to_host -> "uploads/<safe>"

# config (BOTH engines)
show_file_upload: bool = False        # requires conversation_ui=True (+ mode="conversation")
max_upload_bytes: int = 10_000_000

# widgetData (both engines, conversation): + showFileUpload: bool, maxUploadBytes: int

# optio-conversation-ui src/attachments.ts
export interface Attachment { file: File; mime: string; filename: string }
export function readAsDataUrl(file: File): Promise<string>   // "data:<mime>;base64,<…>"
export function withinCap(files: Attachment[], cap: number): boolean

# OpencodeView send(): parts = [...attachments.map(a => ({type:'file', mime:a.mime, filename:a.filename, url:<dataUrl>})), {type:'text', text}]
# ClaudeCodeView send(): POST `${widgetProxyUrl}upload` (FormData, each file appended as "file")
#   -> resp.files[].path -> text = "System: upload received, stored in <path>\n"×N + "\n" + prompt -> post('send',{text})
```

## File Ownership

| File | Task |
|---|---|
| `optio-conversation-ui/src/attachments.ts` (new) | A1 |
| `optio-conversation-ui/src/opencode/OpencodeView.tsx` | A2 |
| `optio-conversation-ui/src/claudecode/ClaudeCodeView.tsx` | A3 |
| `optio-opencode/src/optio_opencode/types.py` | B1 |
| `optio-opencode/src/optio_opencode/session.py` | B2 |
| `optio-claudecode/src/optio_claudecode/types.py` | C1 |
| `optio-claudecode/src/optio_claudecode/conversation_listener.py` | C2 |
| `optio-claudecode/src/optio_claudecode/session.py` | C3 |
| `optio-demo/src/optio_demo/tasks/opencode.py` | D1 |
| `optio-demo/src/optio_demo/tasks/claudecode.py` | D2 |
| `optio-opencode/tests/test_file_upload.py` (new) | V1 |
| `optio-claudecode/tests/test_file_upload.py` (new) | V2 |
| `optio-conversation-ui/src/__tests__/conversation-upload.test.tsx` (new) | V3 |

A1–D2, V1–V3 are file-disjoint → concurrent. Final task **V4** runs all checks + commits.

---

### Task A1: shared attachment helpers

**File:** Create `packages/optio-conversation-ui/src/attachments.ts`

```typescript
/** Shared file-attachment helpers for the conversation views. Engine-neutral. */

export interface Attachment {
  file: File;
  mime: string;
  filename: string;
}

/** Wrap a picked File as an Attachment (mime from the File, filename basename). */
export function toAttachment(file: File): Attachment {
  return {
    file,
    mime: file.type || 'application/octet-stream',
    filename: file.name.split(/[\\/]/).pop() || 'file',
  };
}

/** Read a File as a base64 data URL: "data:<mime>;base64,<…>". */
export function readAsDataUrl(file: File): Promise<string> {
  return new Promise((resolve, reject) => {
    const r = new FileReader();
    r.onload = () => resolve(String(r.result));
    r.onerror = () => reject(r.error);
    r.readAsDataURL(file);
  });
}

/** True if the total size of the attachments is within `cap` bytes. */
export function withinCap(atts: Attachment[], cap: number): boolean {
  return atts.every((a) => a.file.size <= cap);
}
```

- [ ] Commit: `git add packages/optio-conversation-ui/src/attachments.ts && git commit -m "feat(optio-conversation-ui): shared file-attachment helpers"`

---

### Task A2: opencode attach UI + inline file parts

**File:** Modify `packages/optio-conversation-ui/src/opencode/OpencodeView.tsx`

- [ ] Import the helpers: `import { type Attachment, toAttachment, readAsDataUrl, withinCap } from '../attachments.js';`
- [ ] Read the flags from widgetData near the other reads: `const showFileUpload = Boolean((props.process.widgetData as any)?.showFileUpload); const maxUploadBytes = Number((props.process.widgetData as any)?.maxUploadBytes ?? 10_000_000);`
- [ ] Add state: `const [attachments, setAttachments] = useState<Attachment[]>([]);`
- [ ] In `send()` (line 256), build file parts when attachments exist. Replace the `prompt_async` body construction so it includes file parts as data URLs:

```typescript
    const fileParts = await Promise.all(
      attachments.map(async (a) => ({
        type: 'file' as const, mime: a.mime, filename: a.filename, url: await readAsDataUrl(a.file),
      })),
    );
    const promptBody: any = { parts: [...fileParts, { type: 'text', text: body }] };
    if (currentModel) promptBody.model = currentModel;
    const ok = await post(`session/${sessionID}/prompt_async${q}`, promptBody);
```

  and on success also `setAttachments([])` next to `setText('')`. Allow send when there are attachments even if the text box is non-empty (existing `if (!body ...)` guard already requires text; keep requiring a prompt — attachments accompany a prompt).
- [ ] Add an attach control + chips in the input bar. Insert before the textarea (and a chip row above the bar). Use a hidden `<input type="file" multiple>` triggered by an antd `Button` (paperclip-ish), gated on `showFileUpload`, disabled while `closed`:

```tsx
        {showFileUpload && (
          <>
            <input
              data-testid="file-input"
              type="file"
              multiple
              style={{ display: 'none' }}
              ref={fileInputRef}
              onChange={(e) => {
                const picked = Array.from(e.target.files ?? []).map(toAttachment);
                const next = [...attachments, ...picked];
                if (!withinCap(next, maxUploadBytes)) { setError('File too large.'); return; }
                setAttachments(next);
                e.target.value = '';
              }}
            />
            <Button size="small" data-testid="attach-button" disabled={closed}
              onClick={() => fileInputRef.current?.click()}>📎</Button>
          </>
        )}
```

  with `const fileInputRef = useRef<HTMLInputElement>(null);` near the other refs, and a chip row rendered just above the input bar `<div>`:

```tsx
        {attachments.length > 0 && (
          <div data-testid="attach-chips" style={{ display: 'flex', flexWrap: 'wrap', gap: 4, padding: '4px 8px' }}>
            {attachments.map((a, i) => (
              <span key={i} style={{ fontSize: 12, padding: '2px 6px', border: `1px solid ${token.colorBorderSecondary}`, borderRadius: 4 }}>
                {a.filename}
                <a style={{ marginLeft: 6 }} onClick={() => setAttachments(attachments.filter((_, j) => j !== i))}>×</a>
              </span>
            ))}
          </div>
        )}
```

- [ ] Commit: `git add packages/optio-conversation-ui/src/opencode/OpencodeView.tsx && git commit -m "feat(optio-conversation-ui): opencode file attachments (inline file parts)"`

---

### Task A3: Claude Code attach UI + upload + System: bundling

**File:** Modify `packages/optio-conversation-ui/src/claudecode/ClaudeCodeView.tsx`

Mirror A2's UI (flags, `attachments` state, `fileInputRef`, attach button, chip row — same JSX, this file's token/closed names). The only difference is `send()`:

- [ ] Import helpers (`toAttachment, readAsDataUrl, withinCap, type Attachment`).
- [ ] Add an `uploadFiles()` helper (multipart, not the JSON `post`):

```typescript
  async function uploadFiles(atts: Attachment[]): Promise<string[] | null> {
    const fd = new FormData();
    for (const a of atts) fd.append('file', a.file, a.filename);
    try {
      const resp = await fetch(`${widgetProxyUrl}upload`, { method: 'POST', body: fd });
      if (!resp.ok) return null;
      const j = await resp.json();
      return (j.files ?? []).map((f: any) => String(f.path));
    } catch { return null; }
  }
```

- [ ] In `send()` (line 223), when attachments exist, upload first then bundle System: lines into the prompt:

```typescript
    let prompt = body;
    if (attachments.length > 0) {
      const paths = await uploadFiles(attachments);
      if (!paths) { setError('Upload failed — retry.'); setSending(false); return; }
      const notice = paths.map((p) => `System: upload received, stored in ${p}`).join('\n');
      prompt = `${notice}\n\n${body}`;
    }
    const ok = await post('send', { text: prompt });
    // on success: setAttachments([]) alongside setText('')
```

  (keep the existing optimistic echo using `body` — show the operator's text, not the System: preamble.)

- [ ] Commit: `git add packages/optio-conversation-ui/src/claudecode/ClaudeCodeView.tsx && git commit -m "feat(optio-conversation-ui): claudecode file attachments (upload + System: lines)"`

---

### Task B1: opencode config fields

**File:** Modify `packages/optio-opencode/src/optio_opencode/types.py`

- [ ] After `show_model_selector` (Phase-1), add:

```python
    # Show the file-attach control in the conversation widget. Requires
    # conversation_ui=True. Files ride inline as data-URL `file` parts.
    show_file_upload: bool = False
    # Per-file size cap enforced client-side before the data URL is built.
    max_upload_bytes: int = 10_000_000
```

- [ ] In `__post_init__`, after the existing model-field validation:

```python
        if self.show_file_upload and not self.conversation_ui:
            raise ValueError(
                "OpencodeTaskConfig: show_file_upload=True requires conversation_ui=True."
            )
```

- [ ] Commit: `git add packages/optio-opencode/src/optio_opencode/types.py && git commit -m "feat(optio-opencode): show_file_upload + max_upload_bytes config"`

---

### Task B2: opencode widgetData

**File:** Modify `packages/optio-opencode/src/optio_opencode/session.py`

- [ ] In `conversation_widget_data` (the helper added in Phase 1), add the two keys:

```python
        "showFileUpload": config.show_file_upload,
        "maxUploadBytes": config.max_upload_bytes,
```

- [ ] Commit: `git add packages/optio-opencode/src/optio_opencode/session.py && git commit -m "feat(optio-opencode): file-upload flags in conversation widgetData"`

---

### Task C1: claudecode config fields

**File:** Modify `packages/optio-claudecode/src/optio_claudecode/types.py`

- [ ] After `show_model_selector`, add the same two fields (`show_file_upload: bool = False`, `max_upload_bytes: int = 10_000_000`) with claude-appropriate comments.
- [ ] In `__post_init__`, after the `show_model_selector` validation:

```python
        if self.show_file_upload and not (self.mode == "conversation" and self.conversation_ui):
            raise ValueError(
                "ClaudeCodeTaskConfig: show_file_upload=True requires "
                "mode='conversation' and conversation_ui=True."
            )
```

- [ ] Commit: `git add packages/optio-claudecode/src/optio_claudecode/types.py && git commit -m "feat(optio-claudecode): show_file_upload + max_upload_bytes config"`

---

### Task C2: listener `POST /upload`

**File:** Modify `packages/optio-claudecode/src/optio_claudecode/conversation_listener.py`

- [ ] Extend `__init__` to accept the writer + cap (keep the existing params):

```python
    def __init__(
        self, conversation, *, password: str,
        initial_events: "list[tuple[int, dict]] | None" = None,
        upload_writer: "Callable[[str, bytes], Awaitable[str]] | None" = None,
        max_upload_bytes: int = 10_000_000,
    ) -> None:
        ...
        self._upload_writer = upload_writer
        self._max_upload_bytes = max_upload_bytes
```

  (add `from typing import Awaitable, Callable` to the imports.)
- [ ] Add the handler after `_handle_model`:

```python
    async def _handle_upload(self, request: web.Request) -> web.Response:
        if not self._authorized(request):
            return web.json_response({"ok": False}, status=401)
        if self._upload_writer is None:
            return web.json_response({"ok": False, "reason": "no-writer"}, status=409)
        stored: list[dict] = []
        try:
            reader = await request.multipart()
        except Exception:  # noqa: BLE001
            return web.json_response({"ok": False, "reason": "bad-multipart"}, status=400)
        while True:
            part = await reader.next()
            if part is None:
                break
            if part.name != "file":
                continue
            filename = part.filename or "file"
            buf = bytearray()
            while True:
                chunk = await part.read_chunk()
                if not chunk:
                    break
                buf.extend(chunk)
                if len(buf) > self._max_upload_bytes:
                    return web.json_response({"ok": False, "reason": "too-large"}, status=413)
            path = await self._upload_writer(filename, bytes(buf))
            stored.append({"filename": filename, "path": path})
        return web.json_response({"ok": True, "files": stored})
```

- [ ] Register in `start()`: `app.router.add_post("/upload", self._handle_upload)` and add `POST /upload` to the docstring route list.
- [ ] Commit: `git add packages/optio-claudecode/src/optio_claudecode/conversation_listener.py && git commit -m "feat(optio-claudecode): listener POST /upload endpoint"`

---

### Task C3: claudecode session wiring

**File:** Modify `packages/optio-claudecode/src/optio_claudecode/session.py`

- [ ] In `_conversation_body`, where the `ConversationListener` is constructed (line 491), define the writer and pass it + the cap. Add `import re` at the top if not present.

```python
            uploads_dir = f"{host.workdir}/uploads"

            async def _write_upload(name: str, data: bytes) -> str:
                safe = re.sub(r"[^A-Za-z0-9._-]", "_", (name.split("/")[-1] or "file"))[:200] or "file"
                await host.put_file_to_host(data, f"{uploads_dir}/{safe}")
                return f"uploads/{safe}"

            conv_listener = ConversationListener(
                conversation, password=listener_password,
                initial_events=initial_events,
                upload_writer=_write_upload,
                max_upload_bytes=config.max_upload_bytes,
            )
```

- [ ] In the `set_widget_data` call (model-switch added the model keys), add:

```python
                "showFileUpload": config.show_file_upload,
                "maxUploadBytes": config.max_upload_bytes,
```

- [ ] Commit: `git add packages/optio-claudecode/src/optio_claudecode/session.py && git commit -m "feat(optio-claudecode): wire upload writer + file-upload widgetData"`

---

### Task D1 / D2: demo wiring

- [ ] **D1** `packages/optio-demo/src/optio_demo/tasks/opencode.py`: add `show_file_upload=True,` to the `opencode-conversation-seed-<id>` config block. Commit: `feat(optio-demo): file upload on opencode conversation task`.
- [ ] **D2** `packages/optio-demo/src/optio_demo/tasks/claudecode.py`: add `show_file_upload=True,` to the conversation task block(s). Commit: `feat(optio-demo): file upload on claudecode conversation task`.

---

### Task V1: opencode config tests

**File:** Create `packages/optio-opencode/tests/test_file_upload.py`

```python
import pytest
from optio_opencode.types import OpencodeTaskConfig


def test_show_file_upload_requires_conversation_ui():
    with pytest.raises(ValueError, match="show_file_upload"):
        OpencodeTaskConfig(consumer_instructions="t", mode="conversation",
                           conversation_ui=False, show_file_upload=True)


def test_show_file_upload_ok():
    cfg = OpencodeTaskConfig(consumer_instructions="t", mode="conversation",
                             conversation_ui=True, show_file_upload=True)
    assert cfg.show_file_upload is True
    assert cfg.max_upload_bytes == 10_000_000


def test_widget_data_has_upload_flags():
    from optio_opencode.session import conversation_widget_data
    cfg = OpencodeTaskConfig(consumer_instructions="t", mode="conversation",
                             conversation_ui=True, show_file_upload=True)
    wd = conversation_widget_data(cfg, session_id="s", directory="/w")
    assert wd["showFileUpload"] is True
    assert wd["maxUploadBytes"] == 10_000_000
```

(Match the real required `OpencodeTaskConfig` fields — mirror the Phase-1 model test's construction.) Commit: `test(optio-opencode): file-upload config + widgetData tests`.

---

### Task V2: claudecode listener upload test

**File:** Create `packages/optio-claudecode/tests/test_file_upload.py`

Test the listener's `_handle_upload` with a fake `upload_writer` (no real host), plus config validation. Use `aiohttp.test_utils` (the package already tests aiohttp handlers — mirror the existing listener test's harness if present; otherwise unit-test `_handle_upload` by constructing a multipart request via `aiohttp.test_utils.make_mocked_request` or a `TestClient`). Cover: a posted file calls the writer and returns its path; an oversized file → 413; config validation that `show_file_upload` requires conversation+ui. Commit: `test(optio-claudecode): file-upload listener + config tests`.

(If the listener has no existing aiohttp test harness to copy, write a focused test that calls `ConversationListener(conv, password="x", upload_writer=fake)._handle_upload(req)` with a hand-built multipart `req`; keep it minimal — the integration is verified manually in V4.)

---

### Task V3: TS widget upload tests

**File:** Create `packages/optio-conversation-ui/src/__tests__/conversation-upload.test.tsx`

Mirror the Phase-1 model-widget tests (MockEventSource + fetch router + makeProps). Cover: attach control hidden without `showFileUpload`, shown with it; for OpencodeView, picking a file and sending makes the `prompt_async` body include a `file` part with a `data:` url; for ClaudeCodeView, picking a file and sending first POSTs `upload` (FormData) then `send` with a `System: upload received` preamble. Stub `FileReader`/`readAsDataUrl` as needed (jsdom provides `FileReader`; a small `File([...])` works). Commit: `test(optio-conversation-ui): file-upload widget tests`.

---

### Task V4: verify + (already-committed) sweep

**Files:** none (fix in the owning file if a check fails).

- [ ] Python: `cd /home/csillag/deai/optio && OPTIO_SKIP_PREFLIGHT_TESTS=1 .venv/bin/python -m pytest packages/optio-opencode/tests/ packages/optio-claudecode/tests/ -q -k "upload or conversation or widget"` then the two full suites — expect PASS.
- [ ] TS: `cd packages/optio-conversation-ui && node_modules/.bin/vitest run && node_modules/.bin/tsc --noEmit` — expect PASS + clean.
- [ ] Demo: `import optio_demo.tasks.opencode` and `…claudecode` → `ok`.
- [ ] Manual e2e (not unit-testable): attach an image in each engine's conversation widget; opencode → the agent sees it inline; claude → file lands in `uploads/`, the agent `Read`s it (vision). Note as not-run if no live session.
- [ ] Fix any failure in the file owned by the relevant task; re-run until green.

---

## Self-Review

**Spec coverage** (`docs/2026-06-22-conversation-file-upload-design.md`):
- UI attach control gated on `show_file_upload`, both views → A2, A3. ✓
- opencode inline data-URL `file` part → A2 + B1/B2. ✓
- claude `POST /upload` → workdir via Host + bundled `System:` lines → C2 (endpoint), C3 (writer + host), A3 (System: bundling). ✓
- size cap + config + widgetData → B1/B2, C1/C3. ✓
- demo both engines → D1, D2. ✓

**Placeholder scan:** concrete code for every code step; the "match real names / mirror existing harness" notes are integration directives with the new code pinned.

**Type consistency:** `Attachment`/`readAsDataUrl`/`withinCap`/`toAttachment` (A1) used identically in A2/A3. `upload_writer: (filename, bytes) -> relpath` identical across C2 (call), C3 (def), V2 (fake). widgetData `showFileUpload`/`maxUploadBytes` identical across B2/C3 (push) and A2/A3 (read). `POST /upload` multipart field `file` + `{files:[{filename,path}]}` identical across C2 and A3.

**Parallel-shape:** every file single-owner (table); A1–D2, V1–V3 file-disjoint + concurrent; all test runs in V4. ✓
