# Conversation-Mode File Upload — Design

**Base:** branch `csillag/opencode-frontend`, 2026-06-22. A new feature for the
engine-neutral conversation widget (`optio-conversation-ui`), developed for both
engines together per the package's parity rule.

## Summary

Let the operator attach files (images, PDFs, text, data) to a conversation-mode
turn from the dashboard widget. Shared UI (one attach control in the conversation
chrome); per-engine delivery, because the two harnesses ingest files differently:

- **opencode** accepts files **inline** — `prompt_async` parts include a native
  `file` part type, so an attachment rides in the same call as the prompt text.
- **Claude Code** has no inline file ingest that works headless (inline `image`
  blocks are not honored — verified, no vision). Instead, drop the bytes into the
  session workdir and let Claude's `Read` tool fetch them on demand. Verified:
  Claude Code's `Read` tool **renders images visually** (answered "Red" for a
  red PNG placed in `<workdir>/uploads/`), so this path gives full vision/text/PDF.

## Verified facts (this session)

- **opencode `prompt_async` parts** are a union of `text | file | agent | subtask`.
  The input shape is `FilePartInput` = `{ type: "file", mime, filename?, url }`
  (`url` required, an unconstrained string; `source` is optional and is for
  workspace @-references — `FileSource` needs a `path` — not uploads).
  **Verified:** opencode accepts a **data-URL** `url`
  (`POST /session/<id>/prompt_async` with `url: "data:text/plain;base64,…"`
  returned **HTTP 204**). So an attachment rides inline as a data URL — no upload
  endpoint, no engine change. There is no separate upload route (`/file*`,
  `/api/fs/*` are read/find).
- **Claude Code (2.1.185, the optio-pinned build):**
  - Inline `image` content blocks over stream-json stdin are **not honored** —
    Claude replied "No image files found", no vision.
  - File-in-workdir + a prompt that references the path → Claude calls `Read`,
    which **presents images visually** → correct color identified. Works for
    text/PDF too. Requires the `Read` tool to be permitted (conversation tasks
    run `permission_mode=bypassPermissions`, so it is) and the path inside the
    workdir (Landlock grants `--rwx <workdir>`, so `uploads/` is readable).
  - Delivery uses the documented **`System:` convention**: every `conversation.send`
    is one user turn (`conversation.py:_user_message_line`), so file notices and
    the prompt are bundled into a **single** turn (separate sends would each
    trigger a response). The turn reads:
    ```
    System: upload received, stored in uploads/<name1>
    System: upload received, stored in uploads/<name2>

    <the operator's prompt>
    ```
    The agent correlates the System: lines with the prompt in the same turn.

## UI (shared conversation widget)

- An attach control (paperclip button + file input) in the input bar of both
  `OpencodeView` and `ClaudeCodeView`. Selected files show as removable chips
  above the textarea (name + size). Send includes the pending attachments and
  then clears them.
- Gated on a new `show_file_upload` widgetData flag (mirrors `showModelSelector`),
  so a task opts in. Disabled while a turn is running / session closed.
- Optional client-side limits: max file size and a mime allowlist (see Open items).

## Delivery — opencode

Build one `file` part per attachment inline in the existing `prompt_async` body —
`{ type: "file", mime, filename, url: "data:<mime>;base64,<…>" }` alongside the
`text` part. Verified accepted (HTTP 204). No separate transport, no engine
change — pure client-side, like the Phase-1 model picker. Caveat: data URLs
base64-inflate the request body (~33%); see Open item 2 for a size cap.

## Delivery — Claude Code

1. **Upload transport:** add `POST .../upload` (multipart) to the per-task
   conversation listener (`conversation_listener.py`, the same listener that will
   carry the model-change endpoint), reached through the widget proxy. It writes
   each file to `<workdir>/uploads/<sanitized-filename>` and returns the stored
   relative paths. Filenames are sanitized (basename only, collision-suffixed) and
   confined to `uploads/` under the workdir (Landlock-readable).
2. **Prompt:** the widget then sends ONE turn = the `System:` upload lines (one
   per stored file) followed by the operator's prompt text (§ Verified facts).
   Claude `Read`s files on demand → vision for images, content for text/PDF.

## Transport summary

| | opencode | Claude Code |
|---|---|---|
| Mechanism | inline `file` part (data URL) on `prompt_async` — verified | `POST .../upload` → workdir `uploads/`, then one turn = System: lines + prompt |
| Vision | native | via `Read` tool (verified) |
| New engine code | none if `url` takes data URLs | upload endpoint + restart-free; bundled System: send |

## Open items (resolve before / during the plan)

1. ~~opencode `file.url` semantics~~ — **RESOLVED**: accepts a data URL (HTTP 204).
   opencode delivery is inline, no upload endpoint.
2. **Size / mime policy** — max attachment size, allowed types, and whether large
   files should stream to the workdir rather than base64-inline (opencode data
   URLs bloat the prompt body).
3. **Config surface** — `show_file_upload: bool` on both task configs (mirrors
   `show_model_selector`); whether to also gate by an allowed-mime list per task.
4. **Cleanup** — whether `uploads/` is pruned on task end or rides into the
   session snapshot (it lives under the workdir, so it is captured by default).

## Scope / non-goals

- Conversation mode only (iframe mode has the engine's own native upload UI).
- No re-use registry / file dedupe across turns (each attachment is delivered for
  the turn it rides with); a Files-API-style `file_id` cache is out of scope.
- Parity is the bar: ship both engines together, not one then the other.
