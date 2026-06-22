# ClaudeCode Conversation-Mode Model Switching — Implementation Plan (Phase 2)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development or superpowers:executing-plans. This plan is **parallel-shaped**: every file is owned by exactly one task, tasks are file-disjoint and run **concurrently**, and **ALL verification is deferred to the final tasks** — do NOT gate each task on green tests. The tree will not compile mid-execution; that is expected.

**Goal:** Add a session-sticky model picker to the Claude Code conversation widget; a model change kills and relaunches `claude` in the same workdir with `--continue` + the new `--model`, preserving the transcript.

**Architecture:** A widget→engine control channel (`POST /model` on the per-task conversation listener) sets a model-change signal on the `ClaudeCodeConversation`; the conversation body's launch/wait loop catches it, kills the process, and relaunches through the normal launch path (re-applying the Landlock wrap) with the new model. The available-model list is fetched engine-side (`GET /v1/models`) and pushed via `widgetData`. Restart stays inside the body loop, so the outer `run_session` teardown captures never fire on a swap.

**Tech Stack:** Python (optio-claudecode: aiohttp listener, asyncio subprocess), TypeScript + React + antd (optio-conversation-ui).

## Global Constraints

- Parallel-shaped: one owner per file; defer all pytest/vitest/tsc to the verification tasks.
- Claude Code only; opencode (Phase 1) untouched. Reuse the restart machinery shape from `csillag/restart-on-demand` where helpful.
- Python env: repo-root venv `/home/csillag/deai/optio/.venv` → `/home/csillag/deai/optio/.venv/bin/python -m pytest …`. Prefix optio-claudecode pytest with `OPTIO_SKIP_PREFLIGHT_TESTS=1` if it has the same preflight flake (use it defensively).
- TS tooling from `packages/optio-conversation-ui`: `node_modules/.bin/vitest`, `node_modules/.bin/tsc`. Never npx.
- Branch `csillag/opencode-frontend`, in-place. No push, no merge.
- Config: claude's existing `config.model` IS the default model — do NOT add a `default_model` field.

## Pinned Interfaces (the contract every task codes against)

```
# optio-claudecode (Python)
ClaudeCodeConversation:
    self.model_change_requested: asyncio.Event           # set on a model-change request
    self.requested_model: str | None = None              # the model to relaunch with
    def request_model_change(self, model: str) -> None    # raises ConversationClosed if closed

models.py:
    FALLBACK_MODELS: dict   # {"models":[{"id","label"}...], "default": None}
    async def fetch_available_models(host, *, home_dir: str) -> dict
        # returns {"models":[{"id":str,"label":str}...], "default": str|None}; best-effort, FALLBACK on any error

ConversationListener route:
    POST /model  body {"model": <str>}  -> conversation.request_model_change(model)
        200 {"ok": true} | 400 {"ok":false,"reason":"bad-json"|"bad-model"} | 409 {"ok":false,"reason":"closed"}

widgetData (claudecode conversation) keys:
    protocol="claudecode", toolVerbosity, showModelSelector: bool,
    models: [{"id":str,"label":str}], currentModel: str | None

ClaudeCodeTaskConfig:
    show_model_selector: bool = False     # requires mode=="conversation" and conversation_ui=True
```

```
# optio-conversation-ui (TS)
ClaudeCodeWidgetData adds: showModelSelector?: boolean; models?: {id:string;label:string}[]; currentModel?: string
Picker: antd <Select data-testid="model-select"> over widgetData.models, gated on showModelSelector,
        value = currentModel state (init from widgetData.currentModel), onChange => optimistic setCurrentModel + POST `${widgetProxyUrl}model` {model}
```

## File Ownership

| File | Owner task |
|---|---|
| `optio-claudecode/src/optio_claudecode/types.py` | P1 |
| `optio-claudecode/src/optio_claudecode/models.py` (new) | P2 |
| `optio-claudecode/src/optio_claudecode/conversation.py` | P3 |
| `optio-claudecode/src/optio_claudecode/conversation_listener.py` | P4 |
| `optio-claudecode/src/optio_claudecode/session.py` | P5 |
| `optio-demo/src/optio_demo/tasks/claudecode.py` | P6 |
| `optio-conversation-ui/src/claudecode/ClaudeCodeView.tsx` | T1 |
| `optio-claudecode/tests/test_model_switch.py` (new) | V1 |
| `optio-conversation-ui/src/__tests__/claudecode-model-widget.test.tsx` (new) | V2 |

P1–P6 + T1 are file-disjoint → run concurrently. V1/V2 are written against the pinned interfaces (also concurrent), then the final task V3 runs everything.

---

### Task P1: config field — `show_model_selector`

**File:** Modify `packages/optio-claudecode/src/optio_claudecode/types.py`

- [ ] Add to `ClaudeCodeTaskConfig`, immediately after the existing `conversation_ui` / `tool_verbosity` fields (search for `conversation_ui` near line 195 region):

```python
    # Show the model picker in the conversation widget. Requires
    # mode="conversation" and conversation_ui=True. The default model is
    # config.model (no separate field).
    show_model_selector: bool = False
```

- [ ] In `__post_init__`, after the existing conversation/conversation_ui validation, add:

```python
        if self.show_model_selector and not (self.mode == "conversation" and self.conversation_ui):
            raise ValueError(
                "ClaudeCodeTaskConfig: show_model_selector=True requires "
                "mode='conversation' and conversation_ui=True."
            )
```

(If the exact field names for mode/conversation_ui differ in this file, match them — read the surrounding fields first. The validation intent is fixed: the flag requires conversation mode + the conversation UI.)

- [ ] Commit: `git add packages/optio-claudecode/src/optio_claudecode/types.py && git commit -m "feat(optio-claudecode): show_model_selector config field"`

---

### Task P2: model-list fetch — `models.py`

**File:** Create `packages/optio-claudecode/src/optio_claudecode/models.py`

- [ ] Write the module:

```python
"""Fetch the account-available Claude model list for the conversation widget.

Claude Code exposes no programmatic model list, so we call the Anthropic Models
API (GET /v1/models) using the OAuth access token in the seeded
home/.claude/.credentials.json. Best-effort: any failure returns FALLBACK_MODELS
so the picker still offers the common aliases.
"""
from __future__ import annotations

import json
import logging

_LOG = logging.getLogger(__name__)

# Shown when the live fetch fails (offline, no creds, API change). The picker
# stays useful; the engine still accepts any model string on relaunch.
FALLBACK_MODELS: dict = {
    "models": [
        {"id": "claude-opus-4-8", "label": "Claude Opus 4.8"},
        {"id": "claude-sonnet-4-6", "label": "Claude Sonnet 4.6"},
        {"id": "claude-haiku-4-5", "label": "Claude Haiku 4.5"},
    ],
    "default": None,
}


def _read_oauth_token(creds_json: str) -> str | None:
    """Extract the Claude Code OAuth access token from a .credentials.json blob."""
    try:
        data = json.loads(creds_json)
    except Exception:  # noqa: BLE001
        return None
    # Claude Code stores {"claudeAiOauth": {"accessToken": "..."}} (shape may
    # vary by version; probe the common locations).
    oauth = data.get("claudeAiOauth") or data.get("oauth") or {}
    return oauth.get("accessToken") or oauth.get("access_token") or data.get("accessToken")


def parse_models(api_json: dict) -> dict:
    """Map GET /v1/models response ({data:[{id, display_name}]}) to widget shape."""
    out = []
    for m in api_json.get("data", []):
        mid = m.get("id")
        if isinstance(mid, str) and mid:
            out.append({"id": mid, "label": m.get("display_name") or mid})
    if not out:
        return FALLBACK_MODELS
    return {"models": out, "default": None}


async def fetch_available_models(host, *, home_dir: str) -> dict:
    """Best-effort GET /v1/models with the session's OAuth token. Never raises."""
    try:
        creds = await host.read_file(f"{home_dir}/.claude/.credentials.json")
    except Exception:  # noqa: BLE001
        _LOG.info("model list: no credentials file; using fallback")
        return FALLBACK_MODELS
    token = _read_oauth_token(creds)
    if not token:
        return FALLBACK_MODELS
    # Run the HTTPS GET on the host so it shares the session's network context.
    cmd = (
        "curl -fsS https://api.anthropic.com/v1/models "
        f"-H 'authorization: Bearer {token}' "
        "-H 'anthropic-version: 2023-06-01' "
        "-H 'anthropic-beta: oauth-2025-04-20'"
    )
    try:
        body = await host.run_command(cmd)
        return parse_models(json.loads(body))
    except Exception:  # noqa: BLE001
        _LOG.info("model list: live fetch failed; using fallback", exc_info=True)
        return FALLBACK_MODELS
```

NOTE on the two host calls: this uses `host.read_file(path)` and `host.run_command(cmd)` returning stdout. If the Host API spells these differently (e.g. `cat_file`, `run`), match the real method names — read `optio_host` / how `session.py` reads files and runs commands (e.g. the `echo DONE >> optio.log` call uses `host.run_command`). The OAuth-token JSON shape is version-dependent; the multi-probe `_read_oauth_token` is intentionally defensive. Both the token path and the API call are best-effort by design.

- [ ] Commit: `git add packages/optio-claudecode/src/optio_claudecode/models.py && git commit -m "feat(optio-claudecode): best-effort GET /v1/models for the picker"`

---

### Task P3: model-change signal on the conversation

**File:** Modify `packages/optio-claudecode/src/optio_claudecode/conversation.py`

- [ ] In `ClaudeCodeConversation.__init__` (near line 39-44, where other state/events are set), add:

```python
        self.model_change_requested: asyncio.Event = asyncio.Event()
        self.requested_model: str | None = None
```

- [ ] Add the method in the "Conversation protocol surface" region (near `send`, line 211):

```python
    def request_model_change(self, model: str) -> None:
        """Request a model swap. The conversation body observes
        model_change_requested and relaunches claude with this model."""
        if self._closed.is_set():
            raise ConversationClosed(self._close_reason or "conversation closed")
        self.requested_model = model
        self.model_change_requested.set()
```

(Confirm `self._closed` / `self._close_reason` / `ConversationClosed` are the names already used by `send()` — reuse them verbatim.)

- [ ] Commit: `git add packages/optio-claudecode/src/optio_claudecode/conversation.py && git commit -m "feat(optio-claudecode): conversation model-change signal"`

---

### Task P4: listener `POST /model` endpoint

**File:** Modify `packages/optio-claudecode/src/optio_claudecode/conversation_listener.py`

- [ ] Add a handler next to `_handle_interrupt` (after line 196):

```python
    async def _handle_model(self, request: web.Request) -> web.Response:
        if not self._authorized(request):
            return web.json_response({"ok": False}, status=401)
        try:
            payload = await request.json()
        except Exception:  # noqa: BLE001
            return web.json_response({"ok": False, "reason": "bad-json"}, status=400)
        model = payload.get("model")
        if not isinstance(model, str) or not model:
            return web.json_response({"ok": False, "reason": "bad-model"}, status=400)
        try:
            self._conversation.request_model_change(model)
        except ConversationClosed:
            return web.json_response({"ok": False, "reason": "closed"}, status=409)
        return web.json_response({"ok": True})
```

- [ ] Register it in `start()` (after the `/interrupt` route, line 225):

```python
        app.router.add_post("/model", self._handle_model)
```

- [ ] Update the module docstring's route list (lines 6-12) to mention `POST /model — {model} -> conversation.request_model_change`.

- [ ] Commit: `git add packages/optio-claudecode/src/optio_claudecode/conversation_listener.py && git commit -m "feat(optio-claudecode): listener POST /model endpoint"`

---

### Task P5: restart loop + model-list widgetData (session.py)

**File:** Modify `packages/optio-claudecode/src/optio_claudecode/session.py` (the `_conversation_body` function, lines 430-568)

The change: (a) fetch the model list and push it + `showModelSelector` + `currentModel` into `widgetData`; (b) wrap the launch+wait in a loop that catches `conversation.model_change_requested`, kills the process, and relaunches with the new model through the same launch path. Restart stays inside the body, so `run_session`'s teardown captures never fire on a swap.

- [ ] 5a. Add the import at the top of `session.py`:

```python
from optio_claudecode import models as cc_models
```

- [ ] 5b. Extract the per-process launch into an inner helper. Replace the launch block (lines 446-476: from `claude_flags = ...` through `reader_task = asyncio.create_task(conversation.run_reader())`) with a closure that takes the model and whether to `--continue`, and returns the handle + reader task. Define it just before its first use:

```python
        current_model = config.model

        async def _spawn(model: str | None, *, do_continue: bool):
            claude_flags = host_actions.build_claude_flags(
                permission_mode=config.permission_mode,
                allowed_tools=config.allowed_tools,
                disallowed_tools=config.disallowed_tools,
                model=model,
                resuming=do_continue,
            )
            argv = host_actions.build_conversation_argv(
                claude_path, claude_flags=claude_flags,
                permission_gate=config.permission_gate,
                include_partial_messages=_partials_enabled(config),
                replay_user_messages=config.conversation_ui,
            )
            wrap = await _build_claustrum_wrap(host, config, claustrum_path)
            if wrap:
                argv = [*wrap, *argv]
            cmd = " ".join(shlex.quote(a) for a in argv)
            handle = await host.launch_subprocess(
                cmd, env=env, cwd=host.workdir,
                env_remove=config.scrub_env, stdin=True,
            )
            conversation.attach(handle)
            reader = asyncio.create_task(conversation.run_reader())
            return handle, reader

        env = host_actions.conversation_launch_env(
            host.workdir,
            {**(config.env or {}), **focus_env, **(hook_ctx.browser_launch_env or {})},
        )
        ctx.report_progress(None, "Launching Claude Code (conversation)…")
        handle, reader_task = await _spawn(current_model, do_continue=pass_continue)
        launched_handle = handle
```

(Keep `launched_handle = handle` so the outer teardown still targets the live process. `env` is hoisted above `_spawn` since the helper closes over it.)

- [ ] 5c. Extend the `set_widget_data` for conversation_ui (line 500). Fetch the list once and include the new keys:

```python
            model_list = await cc_models.fetch_available_models(host, home_dir=f"{host.workdir}/home")
            await ctx.set_widget_data({
                "protocol": "claudecode",
                "toolVerbosity": config.tool_verbosity,
                "showModelSelector": config.show_model_selector,
                "models": model_list["models"],
                "currentModel": current_model,
            })
```

- [ ] 5d. Replace the wait/try-finally block (lines 523-568) with a restart loop. The loop adds `model_change_requested` to the wait set; on that signal it kills the process, cancels the reader + cred watcher, and relaunches with `current_model = conversation.requested_model` and `do_continue=True` (Claude's transcript now exists), then continues. The clean-close and unexpected-exit branches are unchanged except they `break` out of the loop:

```python
        try:
            while True:
                wait_task = asyncio.create_task(proc_wait(handle))
                close_task = asyncio.create_task(conversation.close_requested.wait())
                model_task = asyncio.create_task(conversation.model_change_requested.wait())
                done, _ = await asyncio.wait(
                    {wait_task, close_task, model_task},
                    return_when=asyncio.FIRST_COMPLETED,
                )
                for t in (wait_task, close_task, model_task):
                    if t not in done:
                        t.cancel()

                if model_task in done and close_task not in done and wait_task not in done:
                    # --- model swap: relaunch in place, keep the task alive ---
                    new_model = conversation.requested_model or current_model
                    conversation.model_change_requested.clear()
                    ctx.report_progress(None, f"Switching model to {new_model}…")
                    await host.terminate_subprocess(handle)
                    reader_task.cancel()
                    try:
                        await reader_task
                    except asyncio.CancelledError:
                        pass
                    current_model = new_model
                    handle, reader_task = await _spawn(current_model, do_continue=True)
                    launched_handle = handle
                    ctx.report_progress(None, f"Claude Code resumed on {new_model}")
                    continue

                if close_task in done and wait_task not in done:
                    if config.host_protocol:
                        log_path = f"{host.workdir}/optio.log"
                        await host.run_command(f"echo DONE >> {shlex.quote(log_path)}")
                        await asyncio.Event().wait()
                    break

                # Subprocess exited on its own.
                try:
                    rc = wait_task.result()
                except Exception:
                    rc = None
                if not conversation.close_requested.is_set() and ctx.should_continue():
                    raise RuntimeError(f"claude exited unexpectedly (exit {rc})")
                break
        finally:
            if cred_watch_task is not None:
                cred_watch_task.cancel()
                try:
                    await cred_watch_task
                except asyncio.CancelledError:
                    pass
                cred_watch_task = None
            reader_task.cancel()
            try:
                await reader_task
            except asyncio.CancelledError:
                pass
```

NOTES for the implementer:
- The credential watcher (`cred_watch_task`, started at lines 513-521) watches the `home/.claude` *file*, not the process, so it does NOT need restarting on a swap — leave it running across relaunches; the outer `finally` still cancels it at task end.
- Coalescing: if a second model-change arrives during a relaunch, `model_change_requested` is simply set again and caught on the next loop iteration — acceptable. (A debounce can be added later, mirroring `restart-on-demand`.)
- Edge — switching before the first turn (no transcript): `do_continue=True` with no transcript makes `--continue` a no-op/fresh start on the new model. This is acceptable for v1; note it. If it proves problematic, gate `do_continue` on `await _has_transcript(host)`.

- [ ] Commit: `git add packages/optio-claudecode/src/optio_claudecode/session.py && git commit -m "feat(optio-claudecode): in-place model-swap restart loop + model-list widgetData"`

---

### Task P6: enable the picker on a demo Claude Code conversation task

**File:** Modify `packages/optio-demo/src/optio_demo/tasks/claudecode.py`

- [ ] Find a `ClaudeCodeTaskConfig(...)` block that sets `mode="conversation"` + `conversation_ui=True` (the conversation demo task, ~lines 235 / 260) and add `show_model_selector=True,` to it. Leave non-conversation tasks unchanged.

- [ ] Commit: `git add packages/optio-demo/src/optio_demo/tasks/claudecode.py && git commit -m "feat(optio-demo): show model selector on claudecode conversation task"`

---

### Task T1: ClaudeCodeView picker

**File:** Modify `packages/optio-conversation-ui/src/claudecode/ClaudeCodeView.tsx`

This mirrors the opencode picker (Phase-1 Task 3) adapted to plain string models. Read the file first; it has the same shape as `OpencodeView` (a widgetData interface, an input bar `<div>` with a textarea + Send/Interrupt buttons, a `post()` helper, a `widgetProxyUrl`).

- [ ] Add `Select` to the antd import.

- [ ] Extend the claudecode widgetData interface with:

```typescript
  showModelSelector?: boolean;
  models?: { id: string; label: string }[];
  currentModel?: string;
```

- [ ] Add state near the other `useState`s:

```typescript
  const [currentModel, setCurrentModel] = useState<string | undefined>(
    (props.process.widgetData as any)?.currentModel ?? undefined,
  );
  const showModelSelector = Boolean((props.process.widgetData as any)?.showModelSelector);
  const models: { id: string; label: string }[] = (props.process.widgetData as any)?.models ?? [];
```

- [ ] Add the picker as the first child of the input bar `<div>` (before the textarea), mirroring OpencodeView's placement:

```tsx
        {showModelSelector && (
          <Select
            data-testid="model-select"
            size="small"
            style={{ minWidth: 180, alignSelf: 'center' }}
            placeholder="Model"
            disabled={busy || closed}
            value={currentModel}
            onChange={(v: string) => {
              setCurrentModel(v);                       // optimistic
              void post('model', { model: v });         // engine relaunches
            }}
            options={models.map((m) => ({ label: m.label, value: m.id }))}
          />
        )}
```

(Use whatever the file calls its busy/closed flags and its POST helper — match `OpencodeView`. The POST path is `model` joined to `widgetProxyUrl`, exactly like `send`/`interrupt`.)

- [ ] Commit: `git add packages/optio-conversation-ui/src/claudecode/ClaudeCodeView.tsx && git commit -m "feat(optio-conversation-ui): model picker in claudecode conversation widget"`

---

### Task V1: Python tests (written against the pinned interfaces)

**File:** Create `packages/optio-claudecode/tests/test_model_switch.py`

- [ ] Write tests covering the file-disjoint units (config validation, model parse, conversation signal, listener endpoint). These don't need a live claude:

```python
import pytest

from optio_claudecode.types import ClaudeCodeTaskConfig
from optio_claudecode.models import parse_models, FALLBACK_MODELS


def test_show_model_selector_requires_conversation_ui():
    with pytest.raises(ValueError, match="show_model_selector"):
        ClaudeCodeTaskConfig(mode="conversation", conversation_ui=False, show_model_selector=True)


def test_show_model_selector_ok_in_conversation_ui():
    cfg = ClaudeCodeTaskConfig(mode="conversation", conversation_ui=True, show_model_selector=True)
    assert cfg.show_model_selector is True


def test_parse_models_maps_id_and_label():
    out = parse_models({"data": [
        {"id": "claude-opus-4-8", "display_name": "Claude Opus 4.8"},
        {"id": "claude-haiku-4-5"},
    ]})
    assert out["models"] == [
        {"id": "claude-opus-4-8", "label": "Claude Opus 4.8"},
        {"id": "claude-haiku-4-5", "label": "claude-haiku-4-5"},
    ]


def test_parse_models_empty_falls_back():
    assert parse_models({"data": []}) == FALLBACK_MODELS


@pytest.mark.asyncio
async def test_conversation_request_model_change_sets_signal():
    from optio_claudecode.conversation import ClaudeCodeConversation
    conv = ClaudeCodeConversation()
    conv.request_model_change("claude-opus-4-8")
    assert conv.requested_model == "claude-opus-4-8"
    assert conv.model_change_requested.is_set()
```

NOTE: match the real `ClaudeCodeTaskConfig` required fields (the constructors above may need extra required args — mirror the existing claudecode tests' construction). If `ClaudeCodeConversation()` needs args, pass the same defaults its other tests use. Adjust the validation `match=` string to the actual message if needed. These are unit tests only — the full restart loop is exercised manually (see V3 step 4).

- [ ] Commit: `git add packages/optio-claudecode/tests/test_model_switch.py && git commit -m "test(optio-claudecode): model-switch unit tests"`

---

### Task V2: TS widget test

**File:** Create `packages/optio-conversation-ui/src/__tests__/claudecode-model-widget.test.tsx`

- [ ] Write a test mirroring the opencode model-widget test (Phase-1), adapted to ClaudeCodeView + string models: picker hidden without the flag; shown with it + `models`; selecting a model POSTs `model` with `{model:"<id>"}`. Model it on the existing `claudecode-widget.test.tsx` for the props/EventSource/fetch mock shape (reuse its MockEventSource + makeProps helpers).

- [ ] Commit: `git add packages/optio-conversation-ui/src/__tests__/claudecode-model-widget.test.tsx && git commit -m "test(optio-conversation-ui): claudecode model picker tests"`

---

### Task V3: Verification sweep (run AFTER P1–P6, T1, V1–V2 land)

**Files:** none (fix small errors in the owning task's file if a check fails).

- [ ] **Python suite:**
Run: `cd /home/csillag/deai/optio && OPTIO_SKIP_PREFLIGHT_TESTS=1 .venv/bin/python -m pytest packages/optio-claudecode/tests/test_model_switch.py packages/optio-claudecode/tests/ -q -k "model or conversation or listener"`
Expected: PASS. Fix any failure in the file owned by the relevant task.

- [ ] **optio-claudecode full suite (no regressions):**
Run: `cd /home/csillag/deai/optio && OPTIO_SKIP_PREFLIGHT_TESTS=1 .venv/bin/python -m pytest packages/optio-claudecode/tests/ -q`
Expected: PASS.

- [ ] **TS suite + typecheck:**
Run: `cd /home/csillag/deai/optio/packages/optio-conversation-ui && node_modules/.bin/vitest run && node_modules/.bin/tsc --noEmit`
Expected: all PASS; tsc clean.

- [ ] **Demo imports:**
Run: `cd /home/csillag/deai/optio && .venv/bin/python -c "import optio_demo.tasks.claudecode; print('ok')"`
Expected: `ok`.

- [ ] **Manual end-to-end (the restart loop — not unit-testable cheaply):** with a Claude Code conversation seed task running and `show_model_selector=True`, pick a different model in the widget; confirm the engine logs "Switching model to …", the stream resumes, and a follow-up turn runs on the new model (the assistant `message.model` / a "what model are you" probe). Confirm the transcript is preserved (the agent recalls earlier context). This is the load-bearing check the unit tests can't cover.

- [ ] **Phase-boundary grep:** `cd /home/csillag/deai/optio && git grep -n "request_model_change\|model_change_requested\|/v1/models" packages/optio-conversation-ui/src/opencode` → expect no matches (opencode adapter untouched).

---

## Self-Review

**Spec coverage** (`docs/2026-06-22-claudecode-conversation-model-switching-design.md`):
- D1 listener `POST /model` → P4. ✓
- D2 engine-fetched list via widgetData → P2 (fetch) + P5 5c (push). ✓
- D3 per-adapter string picker → T1. ✓
- D4 `show_model_selector` only (config.model is default) → P1 + P5 (`current_model = config.model`). ✓
- §1 restart loop (normal launch path → claustrum re-applied; captures don't fire because restart stays in-body) → P5. ✓
- §1 kill/cancel-reader/relaunch/re-attach → P5 5b + loop. ✓
- §2 UI resolution (currentModel from widgetData; optimistic on change) → T1. ✓
- §3 safety (relaunch via normal path only; cred-watcher kept; workdir kept) → P5 NOTES. ✓

**Placeholder scan:** code is concrete; the few "match the real name" notes are integration directives (Host method names, existing field names), not placeholders — each pins the intent and the exact new code.

**Type consistency:** `request_model_change(model:str)` / `model_change_requested` / `requested_model` identical across P3 (def), P4 (call), P5 (read). `fetch_available_models(host, *, home_dir)` + `{"models":[{id,label}],"default"}` identical across P2 (def), P5 (call), V1 (parse test). widgetData keys `showModelSelector/models/currentModel` identical across P5 (push) and T1 (read). `POST model {model}` identical across P4 (route) and T1 (post).

**Parallel-shape check:** every file has exactly one owner (table); P1–P6, T1, V1, V2 are file-disjoint and concurrent; all pytest/vitest/tsc are in V3 only. ✓
