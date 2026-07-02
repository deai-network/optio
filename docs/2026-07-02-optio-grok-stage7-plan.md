# optio-grok Stage 7 (Frontend Parity) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: superpowers:subagent-driven-development / executing-plans. Checkbox steps.
> **Depends on:** Stage 6 (conversation + UI) — done.

**Goal:** Bring the grok conversation UI to parity: model switching, file upload, file download, and tool-verbosity — the operator-facing controls claudecode/opencode already have.

**Architecture:** Extend the Stage-6 grok listener (`conversation_listener.py`) with `/model`, `/upload`, `/download` routes and `GrokView.tsx` with the model selector + upload/download controls (all funnel through the shared `ConversationViewProps`). Model-switch mechanism is pinned by a live ACP probe (Task 0). File transfer mirrors claudecode (no headless inline ingest → drop into workdir + `System:` reference; download via a workdir-confined route + `optio-file:` sentinel).

**Tech Stack:** Python (aiohttp), TypeScript (React), grok ACP.

## Global Constraints

- Branch per coordination decision (see the grok build thread — grok may move to its own branch). Reference = `optio-claudecode` Stage-7 docs/code (`conversation_listener.py` `/model` `/upload` `/download`, `models.py`, the restart-based model switch) and `optio-conversation-ui/src/claudecode/` (model selector, upload/download in `ClaudeCodeView`). opencode for inline-model-switch as the alternative.
- Config additions to `GrokTaskConfig` (mirror claudecode): `default_model: str | None`, `show_model_selector: bool = False`, `show_file_upload: bool = False`, `max_upload_bytes: int = 10_000_000`, `file_download: bool = False`, `max_download_bytes: int = 10_000_000`; all require `conversation_ui=True` (`__post_init__`).
- `tool_verbosity` already forwarded to `widgetData` in Stage 6 — the shared `ConversationView` renders per verbosity; Task 4 only confirms grok's tool items carry enough for verbose KV rendering.
- Every task: failing test first, minimal impl, commit. Tests use fake ACP grok / fake listener — no real binary except Task 0's probe.

---

### Task 0: Model-switch probe (pin the mechanism)
- [ ] Determine how grok changes model mid-conversation over ACP: inspect `session/new`'s returned `models`/`modelState`, and probe a live `grok agent stdio` for a set-model method (search the `initialize`/`session/new` response `_meta` and try an `x.ai/*` or `session/set_model`-style method; also check `grok models --help`). Decide: **inline** (a set-model ACP call per `send`, opencode-style) if supported, else **restart** (relaunch `grok agent -m <model> stdio` + resume the session, claudecode-style). Record the finding in a comment atop the model code. (No commit — research.)

### Task 1: Model list + switching (Python)
**Files:** Create `src/optio_grok/models.py`; modify `conversation_listener.py`, `session.py`, `types.py`; Test `tests/test_models.py` + listener test
- `models.py`: `async def fetch_available_models(...) -> list[dict]` (from `grok models` or the ACP session `models`); fallback list.
- listener `/model` route; session pushes `widgetData.models` + `showModelSelector`; apply switch per Task 0's mechanism.
- [ ] RED → implement → GREEN → **Commit** `feat(optio-grok): conversation model switching (Stage 7)`.

### Task 2: File upload (Python + TS)
**Files:** `conversation_listener.py` (`POST /upload`), `session.py` (workdir `uploads/` + `System:` reference), `GrokView.tsx` (upload control), `types.py`; Test listener + reducer
- Mirror claudecode: bytes → `<workdir>/uploads/`, inject a `System:` path reference into the next prompt (grok reads the file via its own read tool). Gate on `show_file_upload`/`max_upload_bytes`.
- [ ] RED → implement → GREEN → **Commit** `feat(optio-grok): conversation file upload (Stage 7)`.

### Task 3: File download (Python + TS)
**Files:** `conversation_listener.py` (`GET /download?path=`, workdir-confined, `max_download_bytes`), `prompt.py` (downloadables block when `file_download`), `GrokView.tsx`/reducer (the `optio-file:` sentinel is already handled by the shared `Markdown`/`FileDownloadContext`); Test listener path-escape guard
- [ ] RED → implement → GREEN → **Commit** `feat(optio-grok): conversation file download (Stage 7)`.

### Task 4: Tool-verbosity confirm (TS)
**Files:** `src/grok/events.ts` (ensure `tool` items carry `input`=rawInput for verbose KV); Test reducer
- [ ] Confirm verbose/description-only/silent render correctly for grok tool items (shared `ConversationView` does the rendering). RED (assert `input` present) → adjust reducer if needed → GREEN → **Commit** `feat(optio-conversation-ui): grok tool-verbosity parity (Stage 7)`.

---

## Verification
- Python: `.venv/bin/python -m pytest packages/optio-grok/tests -v` all green.
- TS: `packages/optio-conversation-ui` `node_modules/.bin/tsc --noEmit` (exit 0) + `node_modules/.bin/vitest run` (grok + claudecode + opencode reducer tests all pass — no shared-renderer regression).

## Self-Review
- Spec Stage 7 (permissions already in Stage 6; model switch, file up/down, verbosity) ↔ Tasks 0-4.
- Model-switch mechanism probed, not guessed (restart vs inline).
- File transfer mirrors claudecode's no-inline-ingest pattern.
- No placeholders; reference pointers + tests per task; names consistent with claudecode config fields.
