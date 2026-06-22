# Conversation-Mode File Download — Design

**Base:** branch `csillag/opencode-frontend`, 2026-06-22. The download counterpart
to the shipped file-upload feature; developed for both engines together per the
package's parity rule. Grounded in
`docs/2026-06-22-conversation-file-download-research-notes.md`.

## Summary

An agent produces a file in its workdir and presents it to the operator,
embedded in an assistant response, downloadable in one click. Detection is an
**explicit agent affordance**: the agent emits a sentinel markdown link
`[report.md](optio-file:report.md)`. The shared markdown renderer turns that into
a click-to-download control; the engine view supplies the actual fetch (opencode
through its existing `file/content` route, claude code through a new `/download`
listener route). The agent learns the convention from a **downloadables** block
added to the synthesized agent instructions, in two wordings depending on whether
the deliverable keyword protocol is active.

## Decisions (settled in brainstorming)

1. **Detection: explicit affordance**, not passive inference. The agent
   deliberately marks a file deliverable to the *human*; the widget never guesses
   downloadables from tool-event noise.
2. **Sentinel scheme** `optio-file:<relpath>` in a markdown link — unambiguous,
   zero false positives, engine-symmetric, one prompt line to teach.
3. **Render surface: the shared `Markdown.tsx` renderer** — no `ChatState`
   change, no reducer work. The engine view injects the download behavior.
4. **claude `/download` is minimal** — a single `GET /download?path=`; no
   `/file`/`/list` browsing surface (the sentinel link already carries the
   relpath).
5. **Gated by config** `file_download: bool` — off by default; gates the prompt
   block, the route, and the widget wiring.
6. **Lifecycle: while the task lives only.** opencode's server (and its proxy
   route) dies with the task; no post-mortem artifact download in v1.

## 1. Shared widget — sentinel renderer + injected handler

`Markdown.tsx` (used by `AnswerBlock`, engine-neutral — it has no proxy URL or
engine identity) gains, in its `a` renderer, detection of the `optio-file:`
scheme:

- `href` starts with `optio-file:` → strip the scheme to a `relpath`, render a
  click-to-download control (a `Typography.Link`/button with a download glyph and
  the link's text as the filename) instead of a navigating `<a>`.
- On click, call an injected `onFileDownload(relpath: string, filename: string)`.
- Any other `href` → today's behavior (navigating link).

The handler is injected by the engine view via an **optional React context**
(`FileDownloadContext`) wrapping the conversation chrome, or an optional prop
threaded `AnswerBlock → Markdown`. When **absent** (e.g. conversation-scripter's
reuse of `AnswerBlock`), the sentinel link falls back to a plain disabled-looking
link — never breaks. This keeps `Markdown`/`AnswerBlock` engine-neutral.

The shared download primitive both handlers use:

```ts
function blobDownload(bytes: BlobPart, mime: string, filename: string) {
  const url = URL.createObjectURL(new Blob([bytes], { type: mime }));
  const a = document.createElement('a');
  a.href = url; a.download = filename; a.click();
  URL.revokeObjectURL(url);
}
```

## 2. Transport — per engine (the view-supplied handler)

### opencode — no backend work

The view's `onFileDownload` fetches
`${widgetProxyUrl}file/content?path=<relpath>&directory=<dir>` (the proxy forwards
it and injects auth; the view already passes `?directory=`). Response is
`FileContent` JSON: `{ type: "text"|"binary", content }` — decode `content`
(UTF-8 text, or base64 for `binary`) to bytes, then `blobDownload`. The widget
only ever passes a relpath it derived from the agent's own sentinel link (never
operator-typed); confinement on opencode's side is opencode's own server's
responsibility.

### claude code — new listener route

`conversation_listener.py` gains `GET /download`:

1. `app.router.add_get("/download", self._handle_download)`.
2. `if not self._authorized(request): return 401` (every route is authed).
3. `path = request.query.get("path")`; 400 if missing.
4. `if self._download_reader is None: return 409`.
5. `data, mime = await self._download_reader(path)` — may raise
   `FileNotFoundError` (→ 404) or `ValueError` for a confinement/size violation
   (→ 403/413).
6. `return web.Response(body=data, headers={"Content-Type": mime,
   "Content-Disposition": f'attachment; filename="{basename}"'})`.

Symmetric to upload: `session.py` injects a `download_reader` closure capturing
`host` + `host.workdir` that performs the path-confinement guard (§4) and the
size cap, then calls `host.fetch_bytes_from_host`. The listener stays
host-agnostic (it holds the closure, not the host). The view's `onFileDownload`
fetches `${widgetProxyUrl}download?path=<relpath>` → raw body blob.

`ConversationListener.__init__` gains
`download_reader: Callable[[str], Awaitable[tuple[bytes, str]]] | None = None`
and `max_download_bytes: int = 10_000_000`.

## 3. Agent instruction — the downloadables block

A shared block in `optio_agents` (single source, both engines), injected by each
engine's instruction composer (`compose_agents_md` in claudecode `prompt.py`; the
opencode equivalent) **only when `file_download=True`**, in two wordings keyed on
whether the deliverable keyword protocol is active (`host_protocol` in claudecode;
the analogous protocol gate in opencode):

- **Protocol active (comparative).** Explains downloadables *against* deliverables:
  deliverables are shipped to the harness/system for automatic processing (the
  DELIVERABLE keyword); **downloadables** go directly to the human user, produced
  **deliberately** only when the user interactively asks for a file. To offer one,
  write it into the workdir and link it as `[name](optio-file:relpath)`.
- **Protocol off (standalone).** No deliverable comparison: "When the user asks
  you for a file, write it into the working directory and present it as a download
  link: `[name](optio-file:relpath)`. The path is relative to the working
  directory."

Both wordings teach the exact sentinel form. The block is added near where the
protocol documentation is composed so its presence tracks the protocol gate.

## 4. Config + security

- **`file_download: bool = False`** on `ClaudeCodeTaskConfig` and
  `OpencodeTaskConfig` (requires `conversation_ui=True`; claudecode also
  `mode="conversation"`). Gates: the prompt block (§3), the claude `/download`
  route wiring, and `widgetData.fileDownload` (so the view installs the handler
  only when on). `max_download_bytes: int = 10_000_000` on both.
- **Path-confinement (load-bearing).** In the claudecode `session.py`
  `download_reader` closure (where `host.workdir` is in scope):
  `real = os.path.realpath(os.path.join(workdir, path)); reject unless real ==
  workdir or real.startswith(workdir + os.sep)` → raise `ValueError`. The widget
  only passes agent-derived relpaths, but the server guard is authoritative.
- **Size cap.** Stat the file before reading; over `max_download_bytes` → reject
  (RemoteHost reads whole files into memory over SFTP, so an uncapped read is a
  memory DoS). v1 reads-all-into-memory under the cap (no streaming read yet).
- **Auth.** Inherited — every listener route calls `_authorized`; the proxy
  injects the basic-auth credential. opencode `file/content` rides the same authed
  proxy. No new auth surface.
- **Lifecycle.** Download only while the task is live (Decision 6).

## 5. Testing

- **Markdown sentinel renderer:** an `optio-file:` link renders a download control
  (not a plain link); clicking calls the injected `onFileDownload(relpath,
  filename)`; with no handler it degrades to a plain link; non-sentinel links are
  untouched.
- **claude `/download` listener:** a fake `download_reader` returns bytes+mime →
  200 with `Content-Disposition`; missing `path` → 400; no reader → 409;
  `FileNotFoundError` → 404; a `../` path → the closure's guard raises → 403; over
  the cap → 413.
- **opencode handler:** decodes a `FileContent` text and a base64 `binary` to the
  right bytes.
- **Prompt block:** present only when `file_download=True`; comparative wording
  when the protocol is active, standalone wording when off; both contain the
  `optio-file:` sentinel form.
- **Config validation:** `file_download=True` without `conversation_ui` raises;
  `widgetData` carries `fileDownload` + `maxDownloadBytes`.

## 6. Scope / non-goals

- Conversation mode only (iframe mode has the engine's native file access).
- No passive produced-file detection, no `/file` browsing surface, no
  post-mortem (after-task) artifact download.
- No raw-bytes streaming path for very large files (capped read-all in v1).
- opencode delivery needs no engine code (existing route); claude code adds the
  `/download` route + closure.
