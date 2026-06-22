# Conversation-Mode File Download — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: superpowers:subagent-driven-development / executing-plans. **Parallel-shaped**: one owner per file, file-disjoint tasks run concurrently, ALL verification deferred to the final task. The tree won't compile mid-execution — expected.

**Goal:** Agents present produced files as one-click downloads via a sentinel markdown link `[name](optio-file:relpath)`; opencode serves bytes through its existing `file/content`, claude code through a new `/download` listener route; the agent is taught the convention by a two-wording "downloadables" instruction block.

**Architecture:** Shared `Markdown.tsx` renders the `optio-file:` scheme as a download control that calls an injected `onFileDownload` (a React context, so `AnswerBlock` stays prop-free and degrades gracefully where no provider wraps it). Each engine view supplies the handler (opencode decodes `FileContent`; claude blob-fetches `/download`). The claude route reads via a `session.py` closure capturing `host`+workdir (path-confinement + size cap). A shared `optio_agents` block teaches the agent, gated on a new `file_download` config flag.

**Tech Stack:** TypeScript + React + antd; Python (aiohttp listener, dataclass config, prompt synthesis).

## Global Constraints

- Parallel-shaped: one owner per file; all pytest/vitest/tsc in the final verification task only.
- Both engines together (parity). Builds on committed model-switch + upload code.
- Sentinel scheme: exactly `optio-file:<relpath>` (relpath is workdir-relative).
- `file_download: bool = False` + `max_download_bytes: int = 10_000_000` on both task configs; `file_download` requires `conversation_ui=True` (claudecode also `mode="conversation"`).
- Python env = repo-root venv `/home/csillag/deai/optio/.venv/bin/python`; optio pytest prefixed `OPTIO_SKIP_PREFLIGHT_TESTS=1`. TS from `packages/optio-conversation-ui` via `node_modules/.bin/{vitest,tsc}`; never npx.
- Branch `csillag/opencode-frontend`, in-place. No push, no merge.

## Pinned Interfaces

```
# optio-agents (shared)
optio_agents.prompt.downloadables_block(comparative: bool) -> str
    # comparative=True: explains downloadables vs deliverables (protocol active)
    # comparative=False: standalone wording (protocol off)
    # both contain the exact sentinel form: [name](optio-file:relpath)

# both engines' prompt.compose_agents_md gains a kwarg:
compose_agents_md(..., file_download: bool = False)
    # when file_download: append downloadables_block(comparative=host_protocol) to the instructions

# optio-claudecode listener
ConversationListener(..., download_reader=None, max_download_bytes=10_000_000)
    download_reader: Callable[[str], Awaitable[tuple[bytes, str]]]   # relpath -> (data, mime)
    GET /download?path=<relpath>
        200 body=data, Content-Type=mime, Content-Disposition: attachment; filename="<base>"
        400 missing path | 401 unauth | 404 FileNotFoundError | 403 ValueError(confinement) | 413 too-large | 409 no reader

# optio-claudecode session.py provides the reader:
async def _read_download(relpath) -> tuple[bytes, str]
    # realpath-confine to host.workdir; stat<=max; host.fetch_bytes_from_host; mime via mimetypes

# config (BOTH engines): file_download: bool, max_download_bytes: int
# widgetData (BOTH): + fileDownload: bool, maxDownloadBytes: int

# optio-conversation-ui
src/FileDownloadContext.tsx:
    export type FileDownloadHandler = (relpath: string, filename: string) => void
    export const FileDownloadContext: React.Context<FileDownloadHandler | null>  // default null
Markdown a-renderer: href startsWith "optio-file:" -> download control calling the context handler
each view: wrap chat content in <FileDownloadContext.Provider value={onFileDownload}> when widgetData.fileDownload
shared blobDownload(bytes: BlobPart, mime, filename) helper in FileDownloadContext.tsx
```

## File Ownership

| File | Task |
|---|---|
| `optio-agents/src/optio_agents/prompt.py` | S1 |
| `optio-conversation-ui/src/FileDownloadContext.tsx` (new) | A1 |
| `optio-conversation-ui/src/Markdown.tsx` | A2 |
| `optio-conversation-ui/src/opencode/OpencodeView.tsx` | A3 |
| `optio-conversation-ui/src/claudecode/ClaudeCodeView.tsx` | A4 |
| `optio-opencode/src/optio_opencode/types.py` | B1 |
| `optio-opencode/src/optio_opencode/prompt.py` | B2 |
| `optio-opencode/src/optio_opencode/session.py` | B3 |
| `optio-claudecode/src/optio_claudecode/types.py` | C1 |
| `optio-claudecode/src/optio_claudecode/prompt.py` | C2 |
| `optio-claudecode/src/optio_claudecode/conversation_listener.py` | C3 |
| `optio-claudecode/src/optio_claudecode/session.py` | C4 |
| `optio-demo/src/optio_demo/tasks/opencode.py` | D1 |
| `optio-demo/src/optio_demo/tasks/claudecode.py` | D2 |
| `optio-agents/tests/test_downloadables_block.py` (new) | V1 |
| `optio-claudecode/tests/test_file_download.py` (new) | V2 |
| `optio-opencode/tests/test_file_download.py` (new) | V3 |
| `optio-conversation-ui/src/__tests__/file-download.test.tsx` (new) | V4 |

S1, A1–A4, B1–D2, V1–V4 are file-disjoint → concurrent. Final task **V5** runs all checks + commits.

---

### Task S1: shared downloadables instruction block

**File:** Modify `packages/optio-agents/src/optio_agents/prompt.py`

- [ ] Append the function:

```python
def downloadables_block(comparative: bool) -> str:
    """Instruction paragraph teaching the agent to offer a file to the human as
    a one-click download via a sentinel markdown link. Two wordings:
    comparative (when the deliverable keyword protocol is active) vs standalone.
    """
    sentinel = "`[name](optio-file:relpath)`"
    if comparative:
        return (
            "\n\n## Downloadable files\n"
            "Deliverables (the DELIVERABLE keyword) are shipped to the host harness "
            "for automatic processing. **Downloadable files are different**: they go "
            "directly to the human user. Produce one only **deliberately**, when the "
            "user interactively asks you for a file. To offer a file for download, "
            "write it into the working directory and present it as a markdown link "
            f"with the optio-file scheme: {sentinel} — where `relpath` is the file's "
            "path relative to the working directory."
        )
    return (
        "\n\n## Downloadable files\n"
        "When the user asks you for a file, write it into the working directory and "
        f"present it to them as a one-click download: a markdown link {sentinel}, "
        "where `relpath` is the file's path relative to the working directory."
    )
```

- [ ] Commit: `git add packages/optio-agents/src/optio_agents/prompt.py && git commit -m "feat(optio-agents): downloadables instruction block (two wordings)"`

---

### Task A1: download context + blob helper

**File:** Create `packages/optio-conversation-ui/src/FileDownloadContext.tsx`

```tsx
import { createContext } from 'react';

/** Engine view -> Markdown renderer seam: turns an `optio-file:` sentinel link
 *  into an actual download. Null when no engine view provides one (e.g.
 *  conversation-scripter's reuse of AnswerBlock) — the renderer then degrades
 *  to a plain link. */
export type FileDownloadHandler = (relpath: string, filename: string) => void;

export const FileDownloadContext = createContext<FileDownloadHandler | null>(null);

/** Trigger a browser download of in-memory bytes. */
export function blobDownload(bytes: BlobPart, mime: string, filename: string): void {
  const url = URL.createObjectURL(new Blob([bytes], { type: mime || 'application/octet-stream' }));
  const a = document.createElement('a');
  a.href = url;
  a.download = filename;
  a.click();
  URL.revokeObjectURL(url);
}
```

- [ ] Commit: `git add packages/optio-conversation-ui/src/FileDownloadContext.tsx && git commit -m "feat(optio-conversation-ui): file-download context + blob helper"`

---

### Task A2: Markdown sentinel renderer

**File:** Modify `packages/optio-conversation-ui/src/Markdown.tsx`

- [ ] Add imports: `import { useContext } from 'react';` (extend the existing react import) and `import { FileDownloadContext } from './FileDownloadContext.js';`
- [ ] Replace the `a` renderer in `COMPONENTS` (the current `a: ({ href, children }) => <Typography.Link …>`):

```tsx
  a: ({ href, children }) => {
    const onDownload = useContext(FileDownloadContext);
    const SENTINEL = 'optio-file:';
    if (typeof href === 'string' && href.startsWith(SENTINEL)) {
      const relpath = href.slice(SENTINEL.length);
      const filename = relpath.split('/').pop() || relpath;
      if (onDownload) {
        return (
          <Typography.Link
            onClick={() => onDownload(relpath, filename)}
            style={{ cursor: 'pointer' }}
          >
            ⬇ {children}
          </Typography.Link>
        );
      }
      // No handler (e.g. scripter reuse): plain text, no navigation.
      return <Typography.Text>{children}</Typography.Text>;
    }
    return (
      <Typography.Link href={href} target="_blank" rel="noreferrer">
        {children}
      </Typography.Link>
    );
  },
```

(`useContext` inside a `COMPONENTS` entry is a legal hook call — react-markdown `createElement`s these as component types; the existing `th`/`td`/`blockquote` already call `theme.useToken()` the same way.)

- [ ] Commit: `git add packages/optio-conversation-ui/src/Markdown.tsx && git commit -m "feat(optio-conversation-ui): render optio-file: links as downloads"`

---

### Task A3: opencode download handler

**File:** Modify `packages/optio-conversation-ui/src/opencode/OpencodeView.tsx`

- [ ] Import: `import { FileDownloadContext, blobDownload } from '../FileDownloadContext.js';`
- [ ] Read the flag near the other widgetData reads: `const fileDownload = Boolean((props.process.widgetData as any)?.fileDownload);`
- [ ] Add the handler (near `post`/`send`):

```tsx
  async function onFileDownload(relpath: string, filename: string) {
    try {
      const r = await fetch(`${widgetProxyUrl}file/content?path=${encodeURIComponent(relpath)}${q.slice(1) ? '&' + q.slice(1) : ''}`);
      if (!r.ok) { setError('Download failed.'); return; }
      const fc = await r.json();                       // FileContent {type, content}
      const mime = 'application/octet-stream';
      const bytes = fc.type === 'binary'
        ? Uint8Array.from(atob(fc.content), (c) => c.charCodeAt(0))
        : new TextEncoder().encode(fc.content ?? '');
      blobDownload(bytes, mime, filename);
    } catch { setError('Download failed.'); }
  }
```

  (`q` is the existing `?directory=…` string; appending `&directory=…` after `path`. If `q` is empty, omit it.)
- [ ] Wrap the existing returned chat tree in the provider when `fileDownload`: change the top-level `return ( <div …> … </div> )` to wrap with `<FileDownloadContext.Provider value={fileDownload ? onFileDownload : null}>…</FileDownloadContext.Provider>`. (Wrap the outermost element returned by `OpencodeChat`.)
- [ ] Commit: `git add packages/optio-conversation-ui/src/opencode/OpencodeView.tsx && git commit -m "feat(optio-conversation-ui): opencode file-download handler"`

---

### Task A4: claude code download handler

**File:** Modify `packages/optio-conversation-ui/src/claudecode/ClaudeCodeView.tsx`

- [ ] Import: `import { FileDownloadContext, blobDownload } from '../FileDownloadContext.js';`
- [ ] Read the flag: `const fileDownload = Boolean((props.process.widgetData as any)?.fileDownload);`
- [ ] Add the handler:

```tsx
  async function onFileDownload(relpath: string, filename: string) {
    try {
      const r = await fetch(`${widgetProxyUrl}download?path=${encodeURIComponent(relpath)}`);
      if (!r.ok) { setError('Download failed.'); return; }
      const mime = r.headers.get('content-type') || 'application/octet-stream';
      const bytes = new Uint8Array(await r.arrayBuffer());
      blobDownload(bytes, mime, filename);
    } catch { setError('Download failed.'); }
  }
```

- [ ] Wrap the returned chat tree in `<FileDownloadContext.Provider value={fileDownload ? onFileDownload : null}>…</FileDownloadContext.Provider>` (outermost element of the chat component).
- [ ] Commit: `git add packages/optio-conversation-ui/src/claudecode/ClaudeCodeView.tsx && git commit -m "feat(optio-conversation-ui): claudecode file-download handler"`

---

### Task B1: opencode config

**File:** Modify `packages/optio-opencode/src/optio_opencode/types.py`

- [ ] After `show_file_upload`/`max_upload_bytes`, add:

```python
    # Let the agent hand produced files to the user as one-click downloads
    # (optio-file: sentinel links). Requires conversation_ui=True. Adds the
    # downloadables instruction to AGENTS.md and the widget download handler.
    file_download: bool = False
    max_download_bytes: int = 10_000_000
```

- [ ] In `__post_init__`, after the upload validation:

```python
        if self.file_download and not self.conversation_ui:
            raise ValueError(
                "OpencodeTaskConfig: file_download=True requires conversation_ui=True."
            )
```

- [ ] Commit: `git add packages/optio-opencode/src/optio_opencode/types.py && git commit -m "feat(optio-opencode): file_download + max_download_bytes config"`

---

### Task B2: opencode prompt — inject downloadables block

**File:** Modify `packages/optio-opencode/src/optio_opencode/prompt.py`

- [ ] Import: `from optio_agents.prompt import downloadables_block` (next to the existing `from optio_agents.protocol import build_log_channel_prompt`).
- [ ] Add `file_download: bool = False` to `compose_agents_md`'s signature.
- [ ] At the start of `compose_agents_md` body (before the existing composition), append the block to `consumer_instructions` when enabled:

```python
    if file_download:
        consumer_instructions = (
            consumer_instructions.rstrip() + downloadables_block(comparative=host_protocol)
        )
```

- [ ] Commit: `git add packages/optio-opencode/src/optio_opencode/prompt.py && git commit -m "feat(optio-opencode): inject downloadables block when file_download"`

---

### Task B3: opencode session — pass flag + widgetData

**File:** Modify `packages/optio-opencode/src/optio_opencode/session.py`

- [ ] Find the `compose_agents_md(...)` call and add `file_download=config.file_download`.
- [ ] In `conversation_widget_data`, add the two keys:

```python
        "fileDownload": config.file_download,
        "maxDownloadBytes": config.max_download_bytes,
```

- [ ] Commit: `git add packages/optio-opencode/src/optio_opencode/session.py && git commit -m "feat(optio-opencode): file-download flag in prompt + widgetData"`

---

### Task C1: claudecode config

**File:** Modify `packages/optio-claudecode/src/optio_claudecode/types.py`

- [ ] After `show_file_upload`/`max_upload_bytes`, add the same `file_download: bool = False` + `max_download_bytes: int = 10_000_000` (claude-appropriate comment).
- [ ] In `__post_init__`, after the upload validation:

```python
        if self.file_download and not (self.mode == "conversation" and self.conversation_ui):
            raise ValueError(
                "ClaudeCodeTaskConfig: file_download=True requires "
                "mode='conversation' and conversation_ui=True."
            )
```

- [ ] Commit: `git add packages/optio-claudecode/src/optio_claudecode/types.py && git commit -m "feat(optio-claudecode): file_download + max_download_bytes config"`

---

### Task C2: claudecode prompt — inject downloadables block

**File:** Modify `packages/optio-claudecode/src/optio_claudecode/prompt.py`

- [ ] Import `from optio_agents.prompt import downloadables_block`.
- [ ] Add `file_download: bool = False` to `compose_agents_md`'s signature.
- [ ] In the body, before the `if fs_isolation_dirs:` block, append when enabled:

```python
    if file_download:
        consumer_instructions = (
            consumer_instructions.rstrip() + downloadables_block(comparative=host_protocol)
        )
```

- [ ] Commit: `git add packages/optio-claudecode/src/optio_claudecode/prompt.py && git commit -m "feat(optio-claudecode): inject downloadables block when file_download"`

---

### Task C3: claudecode listener — GET /download

**File:** Modify `packages/optio-claudecode/src/optio_claudecode/conversation_listener.py`

- [ ] Extend `__init__` (keep existing params): add `download_reader: "Callable[[str], Awaitable[tuple[bytes, str]]] | None" = None` and `max_download_bytes: int = 10_000_000`; store as `self._download_reader` / `self._max_download_bytes`. (`Awaitable, Callable` already imported by the upload task.)
- [ ] Add the handler after `_handle_upload`:

```python
    async def _handle_download(self, request: web.Request) -> web.Response:
        if not self._authorized(request):
            return web.json_response({"ok": False}, status=401)
        if self._download_reader is None:
            return web.json_response({"ok": False, "reason": "no-reader"}, status=409)
        path = request.query.get("path")
        if not path:
            return web.json_response({"ok": False, "reason": "bad-path"}, status=400)
        try:
            data, mime = await self._download_reader(path)
        except FileNotFoundError:
            return web.json_response({"ok": False, "reason": "not-found"}, status=404)
        except ValueError as e:
            reason = str(e)
            status = 413 if reason == "too-large" else 403
            return web.json_response({"ok": False, "reason": reason}, status=status)
        base = path.split("/")[-1] or "file"
        return web.Response(
            body=data,
            headers={
                "Content-Type": mime,
                "Content-Disposition": f'attachment; filename="{base}"',
            },
        )
```

- [ ] Register in `start()`: `app.router.add_get("/download", self._handle_download)`; add `GET /download` to the docstring route list.
- [ ] Commit: `git add packages/optio-claudecode/src/optio_claudecode/conversation_listener.py && git commit -m "feat(optio-claudecode): listener GET /download endpoint"`

---

### Task C4: claudecode session — reader closure + flag + widgetData

**File:** Modify `packages/optio-claudecode/src/optio_claudecode/session.py`

- [ ] Add imports if absent: `import os`, `import mimetypes` (top of file).
- [ ] Where the `compose_agents_md(...)` is called for this task, add `file_download=config.file_download`.
- [ ] In `_conversation_body`, where `ConversationListener(...)` is constructed (next to the upload wiring), define the reader and pass it:

```python
            async def _read_download(relpath: str) -> tuple[bytes, str]:
                workdir = host.workdir.rstrip("/")
                real = os.path.realpath(os.path.join(workdir, relpath))
                if real != workdir and not real.startswith(workdir + os.sep):
                    raise ValueError("forbidden")           # outside the workdir
                data = await host.fetch_bytes_from_host(real)
                if len(data) > config.max_download_bytes:
                    raise ValueError("too-large")
                mime = mimetypes.guess_type(real)[0] or "application/octet-stream"
                return data, mime

            conv_listener = ConversationListener(
                conversation, password=listener_password,
                initial_events=initial_events,
                upload_writer=_write_upload,
                max_upload_bytes=config.max_upload_bytes,
                download_reader=_read_download,
                max_download_bytes=config.max_download_bytes,
            )
```

  (Merge with the existing `ConversationListener(...)` call from the upload feature — add the two download kwargs; don't duplicate the constructor. Note: confine-then-stat via `len(data)` is acceptable for v1; a stat-before-read optimization is out of scope.)
- [ ] In the `set_widget_data({...})` call, add `"fileDownload": config.file_download` and `"maxDownloadBytes": config.max_download_bytes`.
- [ ] Commit: `git add packages/optio-claudecode/src/optio_claudecode/session.py && git commit -m "feat(optio-claudecode): wire download reader + file-download widgetData"`

---

### Task D1 / D2: demo wiring

- [ ] **D1** `packages/optio-demo/src/optio_demo/tasks/opencode.py`: add `file_download=True,` to the `opencode-conversation-seed-<id>` config. Commit: `feat(optio-demo): file download on opencode conversation task`.
- [ ] **D2** `packages/optio-demo/src/optio_demo/tasks/claudecode.py`: add `file_download=True,` to the conversation task block(s). Commit: `feat(optio-demo): file download on claudecode conversation task`.

---

### Task V1: shared block tests

**File:** Create `packages/optio-agents/tests/test_downloadables_block.py`

```python
from optio_agents.prompt import downloadables_block


def test_comparative_mentions_deliverables_and_sentinel():
    s = downloadables_block(comparative=True)
    assert "DELIVERABLE" in s
    assert "optio-file:relpath" in s


def test_standalone_omits_deliverable_comparison():
    s = downloadables_block(comparative=False)
    assert "DELIVERABLE" not in s
    assert "optio-file:relpath" in s
```

(If optio-agents has no `tests/` dir or a different test layout, mirror its existing test setup.) Commit: `test(optio-agents): downloadables block wordings`.

---

### Task V2: claudecode download tests

**File:** Create `packages/optio-claudecode/tests/test_file_download.py`

Cover, with a fake reader (no real host): config validation (`file_download` requires conversation+ui); the listener `_handle_download` — reader returns bytes+mime → 200 with `Content-Disposition`; missing path → 400; no reader → 409; reader raising `FileNotFoundError` → 404; reader raising `ValueError("forbidden")` → 403; `ValueError("too-large")` → 413; and that `compose_agents_md(..., file_download=True, host_protocol=False)` contains `optio-file:`. Build the listener handler test by calling `ConversationListener(conv, password="x", download_reader=fake)._handle_download(req)` with a hand-built `aiohttp` mocked request (mirror the upload test's harness in `__tests__`/the existing listener tests). Commit: `test(optio-claudecode): file-download listener + config + prompt tests`.

---

### Task V3: opencode download tests

**File:** Create `packages/optio-opencode/tests/test_file_download.py`

Cover: config validation (`file_download` requires `conversation_ui`); `conversation_widget_data` carries `fileDownload`/`maxDownloadBytes`; `compose_agents_md(..., file_download=True, host_protocol=True)` contains the comparative wording (mentions deliverables + `optio-file:`). Mirror the existing opencode model/upload config tests' construction (required `consumer_instructions`). Commit: `test(optio-opencode): file-download config + prompt + widgetData tests`.

---

### Task V4: widget download tests

**File:** Create `packages/optio-conversation-ui/src/__tests__/file-download.test.tsx`

Cover: rendering `<FileDownloadContext.Provider value={spy}><Markdown>{'[r.md](optio-file:out/r.md)'}</Markdown></Provider>` shows a clickable control whose click calls `spy('out/r.md', 'r.md')`; with no provider the sentinel link renders as plain text (no navigation); a normal `[x](https://e.com)` link still renders a navigating anchor. (Mock `URL.createObjectURL` if a view-level test exercises `blobDownload`.) Commit: `test(optio-conversation-ui): file-download renderer tests`.

---

### Task V5: verify + commit-already-done sweep

- [ ] Python: `cd /home/csillag/deai/optio && OPTIO_SKIP_PREFLIGHT_TESTS=1 .venv/bin/python -m pytest packages/optio-agents/tests/ packages/optio-opencode/tests/ packages/optio-claudecode/tests/ -q -k "download or conversation or block or widget"` then the three full suites — expect PASS.
- [ ] TS: `cd packages/optio-conversation-ui && node_modules/.bin/vitest run && node_modules/.bin/tsc --noEmit` — expect PASS + clean.
- [ ] Demo: import `optio_demo.tasks.opencode` and `…claudecode` → `ok`.
- [ ] Manual e2e (not unit-testable): ask the agent (each engine) to produce a file and offer it; confirm the `optio-file:` link renders a download control and clicking saves the file. Note as not-run if no live session.
- [ ] Fix failures in the owning task's file; re-run until green.

---

## Self-Review

**Spec coverage:**
- §1 sentinel renderer + injected handler + graceful fallback → A1, A2. ✓
- §2 opencode `file/content` decode → A3; claude `/download` route → C3, reader closure → C4. ✓
- §3 two-wording downloadables block, shared, gated → S1 (block), B2/C2 (inject), B3/C4 (pass flag). ✓
- §4 config + path-confinement + size cap + widgetData gate → B1/C1 (config), C4 (guard+cap), B3/C4 (widgetData). ✓
- §4 lifecycle (task-live only) — inherent (no post-mortem route). ✓
- §5 tests → V1–V4. ✓

**Placeholder scan:** code is concrete; "mirror the existing harness" notes are integration directives with the new code pinned.

**Type consistency:** `downloadables_block(comparative:bool)->str` identical across S1/B2/C2/V1. `download_reader: relpath->(bytes,mime)` identical across C3 (call), C4 (def), V2 (fake). `FileDownloadContext`/`blobDownload`/`FileDownloadHandler` identical across A1/A2/A3/A4/V4. widgetData `fileDownload`/`maxDownloadBytes` identical across B3/C4 (push) and A3/A4 (read). Sentinel `optio-file:` identical across S1, A2, tests.

**Parallel-shape:** every file single-owner; S1–D2, V1–V4 file-disjoint + concurrent; all test runs in V5. ✓
