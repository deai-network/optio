# Conversation file DOWNLOAD — research notes (scoping for a future brainstorm)

Status: research only, no code changed. Branch: `csillag/opencode-frontend`.
Companion to the in-flight file-UPLOAD feature (commits `f6d1b7a`..`6f151c4`); this
document maps the symmetric DOWNLOAD direction for both engines.

Goal under study: an agent produces a file in its workdir and presents it to the
operator embedded in an assistant response, downloadable in one click.

---

## 0. Architecture recap (the seams a download must travel)

The conversation widget (`packages/optio-conversation-ui`) is engine-neutral. Each
engine view speaks its engine's native HTTP/SSE through the **optio-api widget
proxy**, which is a *transparent reverse proxy*:

- `packages/optio-api/src/adapters/fastify.ts:290-309` — `onResponse`: any
  non-`text/html` response (binary, JSON) is **streamed through unchanged** with its
  original headers (`stream.pipe(rawRes)`), only HTML is rewritten. So a binary
  download body and a `Content-Disposition` header pass straight through.
- `packages/optio-api/src/widget-proxy-core.ts:45-62` — `applyInnerAuthHeaders`
  injects `Authorization: Basic …` on every forwarded request (the inner basic-auth).
- The proxy forwards **arbitrary paths and methods** to the upstream; no per-route
  allow-list. A new upstream route (e.g. claudecode `GET /download`) needs **zero
  proxy changes** — it rides through automatically.

Consequence: for both engines the download transport is "just another GET to the
upstream through the proxy". The work is at the two ends — surfacing the produced
file in the UI, and (claudecode only) adding the upstream route.

---

## 1. How agent-produced files surface today — per engine

### opencode

A produced file surfaces as a **`tool` part**, not a `file` part. In real sessions
the agent writes via the `write`/`edit` tool; the fixture
(`packages/optio-conversation-ui/src/__tests__/fixtures/opencode-events.json`)
contains part types `text / step-start / reasoning / tool / step-finish` — never
`file`. `FilePart` is the *user-attachment* direction (what the upload feature emits
into `prompt_async`).

Two opencode-native signals that a file was produced (from `/tmp/oc-spec.json`):
- **`ToolPart.state` = `ToolStateCompleted`** carries `input` (e.g. `filePath`/`path`
  for write/edit), `output`, AND an **`attachments: FilePart[]`** array — each
  `FilePart` has `{ mime, filename, url, source }`. This is opencode's own
  "tool produced these files" channel.
- The `Part` union (used by both message history and `message.part.updated` events)
  *does* include `FilePart`, so an assistant message *can* carry a file part — but in
  practice produced files show up as tool parts/attachments, not bare assistant file
  parts.

What the reducer does with this today — **drops it**:
- `packages/optio-conversation-ui/src/opencode/events.ts:85-119`
  (`message.part.updated`): only `part.type === 'text'` (→ bubble) and
  `part.type === 'tool'` (→ ephemeral tool row carrying `state.input`) are handled.
  `file`, `patch`, everything else: the type is remembered (so its deltas are
  ignored) and **nothing is rendered** (line 116-118). The tool row keeps only
  `input`, discards `state.output` and `state.attachments`.
- `events.ts:179-197` (`historyToChatItems`): only `text` parts are mapped; tool and
  file parts in history are ignored.

So today opencode produced-file info reaches the reducer but is thrown away.

### claude code

The agent writes files to the workdir via its `Write`/`Bash`/`Edit` tools. The optio
harness does **not** dissect Claude's NDJSON — it routes a few control types and fans
*every* event to SSE subscribers untouched
(`packages/optio-claudecode/src/optio_claudecode/conversation.py`, `_route` ~line
100-123; everything queued at ~line 123).

How we'd know a file was produced — three candidate signals, none giving a clean
machine-readable "file X produced":
1. **`tool_use` events on the stream** name the tool and its `input` (e.g.
   `Write` with `file_path`), surfaced by the reducer as an ephemeral `tool` row
   (`packages/optio-conversation-ui/src/claudecode/events.ts:173-178`). The *input*
   path is visible; whether the write succeeded is not, and tool rows are ephemeral
   (superseded by the next text — `events.ts:111-113,194`).
2. **Assistant text** mentioning a path ("Created `report.md`"). Free-form, unreliable
   to parse.
3. **Watching the workdir** (a `/list?path=` route + diff). Heaviest; out of scope for
   a minimal v1.

There is **no synthetic "file produced" event** today. The cleanest claudecode signal
for a *deliberate* download is therefore an explicit agent affordance (see §3/Design),
not passive stream inference.

---

## 2. A download transport — per engine

### opencode — already exists

`GET /file/content?path=…&directory=…` → `FileContent` (`/tmp/oc-spec.json`,
operationId `file.read`). `FileContent = { type: "text"|"binary", content: string,
diff?, patch? }` — `content` is the file text, or base64 for `type:"binary"`.
Companion routes: `GET /file?path=` (`file.list` → `FileNode[]`), `GET /file/status`
(`file.status`). The view already passes `?directory=` on every call
(`OpencodeView.tsx:143`). The widget can fetch produced bytes via
`${widgetProxyUrl}file/content?path=<relpath>&directory=<dir>` with **no backend
work** — the proxy forwards it and injects auth. (Note: JSON+base64, not a raw binary
stream; the widget decodes `content` itself.)

### claude code — must be added

No download route exists. Listener routes today
(`packages/optio-claudecode/src/optio_claudecode/conversation_listener.py:274-281`):
`GET /events`, `POST /send|interrupt|model|upload|permission`. aiohttp
`web.Application` router, static registration, each handler
`async def _handle_X(self, request) -> web.Response`, every handler first calls
`self._authorized(request)` (auth at `conversation_listener.py:121-130`, basic
`optio:<password>`, **no route exempt**).

A `GET /download?path=…` handler would need:
1. Register `app.router.add_get("/download", self._handle_download)` near line 280.
2. `if not self._authorized(request): return 401`.
3. `path = request.query.get("path")`.
4. Read bytes confined to the workdir (see §5).
5. Return `web.Response(body=data, headers={Content-Disposition: attachment; …,
   Content-Type: …})` (or `web.FileResponse` for the LocalHost case).

Reading bytes goes through the Host abstraction:
- `packages/optio-host/src/optio_host/host.py:195-200` —
  `async def fetch_bytes_from_host(self, absolute_path, *, progress_cb=None) -> bytes`
  (protocol). LocalHost impl at `host.py:596` (`open(...,"rb").read()` in a thread);
  RemoteHost at `host.py:876` (SFTP, maps missing file → `FileNotFoundError`).

**Key structural finding — the listener holds no `host`/workdir.** Upload works
because `session.py` injects a closure `upload_writer` that captures `host`
(`conversation_listener.py:52,57,247`; the `_write_upload` closure +
`host.put_file_to_host` live in `session.py` ~line 500-512). DOWNLOAD must be
symmetric: inject a `download_reader: Callable[[str], Awaitable[bytes]]` (or
`-> (bytes, mime)`) closure built in `session.py`, capturing `host` and
`host.workdir`, that performs the path-confinement guard and calls
`host.fetch_bytes_from_host`. The listener stays host-agnostic.

---

## 3. UI rendering of a downloadable file in a response

What `Markdown.tsx` does with links today:
`packages/optio-conversation-ui/src/Markdown.tsx:88-92` — the `a` renderer maps to
`<Typography.Link href={href} target="_blank" rel="noreferrer">`. No `download`
attribute, no blob handling. A bare markdown link to a `file://`/sandbox path would
not download — it would navigate, and the proxy can't resolve a `file://`.

`AnswerBlock.tsx` simply wraps `<Markdown>` (`AnswerBlock.tsx:38-40`) — no
attachment/file concept exists.

The widget never auto-downloads on stream (correct: a streaming answer must not
trigger downloads); a one-click affordance must be an explicit, click-to-fetch
control. Minimal addition — **fetch via the proxy, then blob-download**:

```ts
async function downloadViaProxy(relpath, filename) {
  const r = await fetch(`${widgetProxyUrl}<route>?path=${encodeURIComponent(relpath)}…`);
  const blob = await r.blob();           // claudecode: raw body; opencode: decode FileContent.content first
  const url = URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = url; a.download = filename; a.click();
  URL.revokeObjectURL(url);
}
```

Three rendering options for where the affordance appears (for the brainstorm):
1. **Custom Markdown renderer for a sentinel link scheme** (e.g.
   `[report.md](optio-file:report.md)`). The agent is told (system prompt / output
   convention) to emit such links; a new `a`-renderer in `Markdown.tsx` detects the
   scheme and renders a click-to-download control instead of a navigating link.
   Lowest UI surface, reuses the existing markdown path, works for both engines
   identically. Downside: depends on the agent emitting the convention.
2. **Attachment chip below the bubble** (mirrors the upload `attach-chips` UI,
   `OpencodeView.tsx:523-532` / `ClaudeCodeView.tsx:449-468`). Needs a new ChatItem
   kind (`attachment`) and a reducer that derives produced files from the engine
   signal (opencode `ToolStateCompleted.attachments` / write-tool `input.filePath`;
   claudecode `Write` tool `input.file_path` or an agent affordance). Most "native"
   feel, most reducer work.
3. **Both** — engine reducer emits an attachment item *and* the markdown renderer
   handles inline sentinel links.

Minimal v1 leans on option 1 (renderer + sentinel scheme) because it needs no
ChatState change and no new event semantics, and is engine-symmetric.

---

## 4. Already-supported vs must-add

### opencode

| Capability | Present? | file:line | Effort |
| --- | --- | --- | --- |
| Backend route to read produced bytes | Yes (`GET /file/content`) | `/tmp/oc-spec.json` `file.read`; view passes `?directory=` at `OpencodeView.tsx:143` | — |
| Proxy forwards the route + injects auth | Yes (transparent) | `fastify.ts:290-309`, `widget-proxy-core.ts:45-62` | — |
| List produced files | Yes (`GET /file`, `/file/status`) | `/tmp/oc-spec.json` `file.list`/`file.status` | — |
| Reducer surfaces produced-file signal | **No** (file/tool-output dropped) | `events.ts:110-118` | M |
| ChatState attachment concept | **No** | `chat.ts:5-18` | S |
| UI click-to-download affordance | **No** | `Markdown.tsx:88-92` (links navigate) | S–M |
| Decode `FileContent` (text vs base64) | **No** | (new) | S |

### claude code

| Capability | Present? | file:line | Effort |
| --- | --- | --- | --- |
| Backend route to read produced bytes | **No** | listener routes `conversation_listener.py:274-281` | M |
| Host byte-read primitive | Yes | `host.py:195` (proto), `:596` Local, `:876` Remote | — |
| Workdir-capturing closure pattern (precedent) | Yes (upload) | `conversation_listener.py:52,247`; `session.py:~500-512` | — |
| Path-confinement guard | **No** (only upload filename-sanitization) | `session.py:~503` | S |
| Proxy forwards a new route + auth | Yes (transparent) | `fastify.ts:290-309` | — |
| Listener auth on every route | Yes | `conversation_listener.py:121-130` | — |
| Machine-readable "file produced" event | **No** | `conversation.py:~100-123` (passthrough only) | M–L |
| ChatState attachment concept | **No** | `chat.ts:5-18` | S |
| UI click-to-download affordance | **No** | `Markdown.tsx:88-92` | S–M |

---

## 5. Security

- **Path-traversal confinement (the load-bearing guard).** No path guard exists today
  on either side (upload only *sanitizes the filename* to a single component,
  `session.py:~503`). A download MUST confine to the workdir:
  `real = os.path.realpath(os.path.join(workdir, path)); reject unless real ==
  workdir or real.startswith(workdir + os.sep)`. Place this in the **`session.py`
  download closure** (where `host.workdir` is in scope), not the listener — symmetric
  with upload. opencode: `/file/content` resolves `path` relative to `?directory=`
  inside opencode's own server; confinement there is opencode's responsibility, but
  the widget should still only pass relpaths it derived from the engine's own
  produced-file signal, never operator-supplied paths.
- **Auth.** Every claudecode route already enforces basic-auth
  (`conversation_listener.py:121-130`); the proxy injects the credential
  (`widget-proxy-core.ts:51-54`). A new `/download` inherits this by calling
  `_authorized` first — no new auth surface. opencode `/file/content` is reached
  through the same authed proxy.
- **Size cap.** Upload caps at `max_upload_bytes` (default 10 MB,
  `conversation_listener.py:53,245`). Download should cap symmetrically — stat the
  file before reading (RemoteHost reads the whole file into memory via SFTP,
  `host.py:887`, so an uncapped read of a multi-GB artifact is a memory DoS). Add a
  `max_download_bytes` config mirroring `max_upload_bytes`.
- **Landlock.** The Claude Code subprocess is fs-sandboxed (claustrum/Landlock,
  referenced in claudecode `types.py`/`host_actions.py`). That constrains the *agent*,
  not the listener; the download closure reads via the Host on the host side, so the
  workdir-prefix guard — not Landlock — is the boundary that matters for download.

---

## 6. Open questions for the brainstorm

1. **Detection model.** Passive (infer produced files from tool events:
   opencode `ToolStateCompleted.attachments` / write `input.filePath`; claudecode
   `Write` tool `input.file_path`) vs. an explicit agent affordance (a sentinel
   markdown link / output convention the agent is prompted to emit)? Passive is
   automatic but noisy/ambiguous; explicit is deliberate but depends on agent
   cooperation and a prompt addition.
2. **Engine symmetry vs. engine-native.** opencode already has `/file/content`,
   `/file`, `/file/status` for free. Do we (a) build claudecode a *minimal* `/download`
   only, or (b) give claudecode a matching `/file`+`/file/content`+`/list` surface so
   both engines expose the same widget-facing contract (and the reducer/UI is
   engine-agnostic)? (b) is more code but a cleaner single UI seam.
3. **Render surface.** Inline sentinel-link renderer in `Markdown.tsx` (no ChatState
   change) vs. a new `attachment` ChatItem + chip (reducer work, more native). Or both.
4. **opencode `FileContent` shape.** It returns JSON (`content` text or base64), not a
   raw binary stream — the widget must branch on `type` and base64-decode. Acceptable,
   or do we want a raw-bytes path for large binaries? (Affects the size-cap story too.)
5. **Lifecycle.** opencode's server (and the proxy route to it) dies with the task
   (`OpencodeView.tsx:172-174`). Download is only possible while the task lives —
   confirm that's the intended scope (no post-mortem download of artifacts).
6. **Cap + memory.** Pick `max_download_bytes`; decide whether RemoteHost needs a
   streaming read (vs. the current read-all-into-memory at `host.py:887`) before
   un-capping.

---

## Pointer index (load-bearing files)

- UI: `packages/optio-conversation-ui/src/{Markdown.tsx,AnswerBlock.tsx,chat.ts}`,
  `opencode/{events.ts,OpencodeView.tsx}`, `claudecode/{events.ts,ClaudeCodeView.tsx}`
- Upload precedent (symmetry): `conversation_listener.py:222-249` (`_handle_upload`),
  `session.py:~500-512` (`_write_upload` closure), upload tests in
  `__tests__/conversation-upload.test.tsx`
- Proxy: `optio-api/src/adapters/fastify.ts:290-309`,
  `optio-api/src/widget-proxy-core.ts:45-62`
- Host: `optio-host/src/optio_host/host.py:195` (proto `fetch_bytes_from_host`),
  `:596` Local, `:876` Remote
- opencode spec: `/tmp/oc-spec.json` (`file.read`/`file.list`/`file.status`,
  `FileContent`, `FilePart`, `ToolStateCompleted.attachments`)
