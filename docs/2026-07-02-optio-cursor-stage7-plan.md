# optio-cursor Stage 7 (Frontend Parity) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: superpowers:subagent-driven-development / executing-plans. Checkbox steps.
> **Depends on:** Stage 6 (conversation + UI).

**Goal:** Bring the cursor conversation UI to parity: model switching, file upload, file download, and tool-verbosity.

**Architecture:** Extend the Stage-6 cursor listener with `/model`, `/upload`, `/download` routes and `CursorView.tsx` with the model selector + upload/download controls (all funnel through the shared `ConversationViewProps`). Model-switch mechanism: cursor's binary carries ACP `session/set_model` (grok's exact inline mechanism, live-verified for grok) — assume **inline**, auth-gated probe to confirm, restart-based (`--resume <chatId> --model <m>`) as the fallback. File transfer mirrors grok/claudecode (drop into workdir + `System:` reference; download via workdir-confined route + `optio-file:` sentinel).

**Tech Stack:** Python (aiohttp), TypeScript (React), cursor ACP.

## Global Constraints

- Branch `csillag/cursor`. Reference = `optio-grok` Stage-7 code (`conversation_listener.py` `/model` `/upload` `/download` routes, `models.py`, inline `session/set_model` switch; `GrokView.tsx` controls).
- Config additions to `CursorTaskConfig` (mirror grok): `default_model: str | None`, `show_model_selector: bool = False`, `show_file_upload: bool = False`, `max_upload_bytes: int = 10_000_000`, `file_download: bool = False`, `max_download_bytes: int = 10_000_000`; all require `conversation_ui=True` (`__post_init__`).
- `tool_verbosity` already forwarded to `widgetData` in Stage 6; the shared ACP reducer already carries tool `input` (grok's Stage-7 parity work) — cursor inherits it via `src/acp/`; Task 4 is a confirm-only test.
- Every task: failing test first, minimal impl, commit (no Co-Authored-By). Tests use fake ACP cursor — no real binary except the auth-gated Task 0 probe.

---

### Task 0: Model-switch probe (auth-gated)
- [ ] If `cursor-agent status` shows a login: live-probe `session/set_model {sessionId, modelId}` on a `cursor-agent acp` session; record result + the model-id catalogue source (`cursor-agent models` output shape and/or the `session/new` response models field). If not logged in: adopt grok's inline mechanism as the working assumption, record `[cursor runtime-unverified]`, and keep the restart fallback documented in `models.py`'s header. (No commit — research.)

### Task 1: Model list + switching (Python)
**Files:** Create `src/optio_cursor/models.py`; modify `conversation_listener.py`, `session.py`, `types.py`; Test `tests/test_models.py` + listener test
- `models.py`: `async def fetch_available_models(...) -> list[dict]` (from `cursor-agent models` / `--list-models` or the ACP session response); fallback list (current cursor catalogue incl. the composer/gpt/sonnet/opus families).
- listener `/model` route; session pushes `widgetData.models` + `showModelSelector`; apply switch inline via `session/set_model` (fallback restart path only if Task 0 disproves inline).
- [ ] RED → implement (adapt grok `models.py` + listener route) → GREEN → **Commit** `feat(optio-cursor): conversation model switching (Stage 7)`.

### Task 2: File upload (Python + TS)
**Files:** `conversation_listener.py` (`POST /upload`), `session.py` (workdir `uploads/` + `System:` reference), `CursorView.tsx` (upload control), `types.py`; Test listener + reducer
- Mirror grok: bytes → `<workdir>/uploads/`, inject a `System:` path reference into the next prompt. Gate on `show_file_upload`/`max_upload_bytes`.
- [ ] RED → implement → GREEN → **Commit** `feat(optio-cursor): conversation file upload (Stage 7)`.

### Task 3: File download (Python + TS)
**Files:** `conversation_listener.py` (`GET /download?path=`, workdir-confined, `max_download_bytes`), `prompt.py` (downloadables block when `file_download`), `CursorView.tsx` (the `optio-file:` sentinel is handled by the shared `Markdown`/`FileDownloadContext`); Test listener path-escape guard
- [ ] RED → implement → GREEN → **Commit** `feat(optio-cursor): conversation file download (Stage 7)`.

### Task 4: Tool-verbosity confirm (TS)
**Files:** Test only (`src/__tests__/cursor-events.test.ts`) — the shared `src/acp/events.ts` already carries `input` for verbose KV.
- [ ] Assert cursor tool items carry `input` through `reduceCursorEvent` (verbose/description-only/silent covered by the shared renderer). Fix in `src/acp/` ONLY if red (then grok tests must stay green). **Commit** `test(optio-conversation-ui): cursor tool-verbosity parity (Stage 7)`.

---

## Verification
- Python: `.venv/bin/pytest packages/optio-cursor/tests -v` all green.
- TS: `packages/optio-conversation-ui` `node_modules/.bin/tsc --noEmit` (exit 0) + `node_modules/.bin/vitest run` (cursor + grok + claudecode + opencode reducer tests all pass — no shared-reducer regression).

## Self-Review
- Spec Stage 7 (permissions in Stage 6; model switch, file up/down, verbosity) ↔ Tasks 0-4.
- Model-switch assumed inline on strong evidence (same ACP method grok live-verified), probe-gated, restart fallback named.
- Shared-ACP-reducer leverage: verbosity is confirm-only; any fix lands in the shared module with grok regression-guarded.
- No placeholders; reference pointers + tests per task; config field names mirror grok/claudecode.
