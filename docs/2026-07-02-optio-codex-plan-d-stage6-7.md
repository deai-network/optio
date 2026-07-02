# optio-codex Plan D — Stages 6–7 (conversation mode + frontend parity) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Give optio-codex a live conversation mode: a `CodexConversation` implementing the `optio_agents.conversation.Conversation` protocol over `codex app-server` (JSON-RPC 2.0 over stdio JSONL), the per-task SSE `ConversationListener`, the dashboard chat widget (`optio-conversation-ui/src/codex/`), full frontend parity (permission gate, inline model switching, file upload/download, tool verbosity), and the seed-pinned conversation demo task that completes the guide's demo trio.

**Architecture:** Codex's headless conversation transport is **`codex app-server`** — bidirectional JSON-RPC 2.0 over stdio, newline-delimited JSON, with the `"jsonrpc"` field OMITTED on the wire (probed live against codex-cli 0.142.5). Threads → turns → items. `CodexConversation` ports optio-grok's `GrokConversation` skeleton 1:1 (attach/run_reader/bootstrap, `_write_lock`, two-tier `_event_queue` fan-out, queue-permissions-until-handler, `_finish` drain guarantee, clean-close DONE park) with the framing swapped ACP→app-server. The listener ports ~verbatim (engine-agnostic; permission correlation key = the server request's JSON-RPC `id`). The UI mirrors grok's newest pattern: a pure reducer (`src/codex/events.ts`) mapping app-server notifications → the shared `ChatItem` model, plus a near-copy transport view (`CodexView.tsx`). Model switching is **INLINE without any wire write**: a `model` override on `turn/start` becomes the default for subsequent turns (app-server contract), so `request_model_change` just pins the model used by the next `turn/start`.

**Tech Stack:** Python ≥3.11 (asyncio subprocess, aiohttp SSE listener, pytest + pytest-asyncio `asyncio_mode=auto`), TypeScript/React (vitest + @testing-library/react, antd 5), optio-core/host/agents driver stack, MongoDB via the existing test fixtures.

## Global Constraints

- Worktree: `/home/csillag/deai/optio/.worktrees/csillag/optio-codex` — branch `csillag/optio-codex`. All relative paths below are relative to this worktree root.
- Python env: the worktree venv **only**: `.venv/bin/python` / `.venv/bin/pip`. NEVER `pip install` against the global interpreter. Test command shape: `.venv/bin/python -m pytest packages/optio-codex/tests/ -q` (needs MongoDB on `localhost:27017`; if down: `cd packages/optio-demo && make deps-up`).
- Node tooling: pnpm workspace. **Never `npx`** — invoke binaries directly: `packages/optio-conversation-ui/node_modules/.bin/tsc`, `…/node_modules/.bin/vitest`. If `node_modules` is absent in the worktree, bootstrap once with `pnpm install --frozen-lockfile` at the worktree root (and if a stray non-frozen install ever polluted `node_modules`, nuke it and re-run the frozen install — known scripter-pollution failure mode).
- Commit style: conventional commits (`feat(optio-codex): …`), one commit per task where marked. **NO `Co-Authored-By` lines** (user rule).
- SSOT rule: protocol documentation, shared prompt framing, and the `Conversation` contract are *imported* from `optio-agents`, never copied. The reducer/view/listener are per-engine by design (each speaks a different wire); the shared renderer (`ConversationView`) and `ChatItem` model are NOT touched.
- **Prerequisites / moving baseline.** This plan assumes **Plan A (Stage-0 hardening) has landed** — it relies on `_isolation_env`, `_provision_task_home` (per-task `<workdir>/home/.local/bin/codex`), and the Plan-A `prompt.py` (delegating to `optio_agents.prompt.compose_agents_md`, with `_SYSTEM_PREFIX_EXPLAINER` for `host_protocol=False`). Plans B (Stages 1–2) and C (Stages 3–5) are being written/executed in parallel on the same branch; Task 0 reconciles against whatever has actually landed. The only *hard* dependencies on B/C are: (a) Task 5's `resuming`/`supports_resume` integration points, (b) Task 9 (demo) which needs Plan C's seed store + demo module structure. Every task states its reconciliation rule where the baseline can differ.
- Config-field ownership (overlap contract with Plans B/C): Plan D adds **exactly** these `CodexTaskConfig` fields — `mode` (widened), `permission_gate`, `conversation_ui`, `tool_verbosity`, `default_model`, `show_model_selector`, `show_file_upload`, `max_upload_bytes`, `file_download`, `max_download_bytes` — and relaxes the `host_protocol=False` validation. `seed_id`/`on_seed_saved`/`supports_resume`/`workdir_exclude` belong to Plans B/C; `fs_isolation`/`extra_allowed_dirs` to Plan E. Do not add fields owned by another plan; if one is missing at execution time, follow the per-task reconciliation notes.
- Every task leaves the whole codex suite green before its commit. Python tasks (0–6) come before and are independent of the TS tasks (7–8).
- Reference implementations (read, port, do not invent): grok sources at the MAIN checkout `/home/csillag/deai/optio/packages/optio-grok/` (branch `csillag/optio-grok` content — `conversation.py`, `conversation_listener.py`, `session.py`, `models.py`, `types.py`, `tests/…`) and grok UI at `/home/csillag/deai/optio/packages/optio-conversation-ui/src/grok/` + `src/__tests__/grok-*.test.*`. The grok reducer/view is the NEWEST pattern; it is NOT on this branch — this plan adds `src/codex/` fresh.

## Pinned app-server wire facts (evidence: live probe + generated schemas, codex-cli 0.142.5)

Everything below was verified against the scratchpad artifacts: `probe.py` (working JSONL handshake against the real binary), `app-server-README.md` (full upstream doc), and `schema/` (output of `codex app-server generate-json-schema` from the exact 0.142.5 binary: `ClientRequest.json`, `ServerRequest.json`, `ServerNotification.json`, `ClientNotification.json`, `v2/…` per-method). Scratchpad: `/tmp/claude-1000/-home-csillag-deai-optio/e46ce9b4-db17-45cb-bf18-038271a8a8ea/scratchpad/`. **If those files are missing, regenerate**: `codex app-server generate-json-schema --out <scratchpad>/schema2` + the upstream README; do not code from memory.

- **Framing:** newline-delimited JSON over stdio; JSON-RPC 2.0 semantics with the `"jsonrpc"` field **omitted** on the wire (README "Protocol"; probe confirms requests without the field are accepted and replies omit it). Write without it; the reader must not require it.
- **Handshake:** `initialize` request `{clientInfo:{name,title,version}, capabilities?}` → result `{userAgent, codexHome, platformFamily, platformOs}`; then the `initialized` **notification** (the only `ClientNotification`). Any other request first → `"Not initialized"` error. We do **NOT** set `capabilities.experimentalApi` (stay on the stable surface). `capabilities.optOutNotificationMethods: [string]` suppresses exact-match notification methods per connection.
- **Auth sanity:** `account/read` `{refreshToken:false}` (v2 `GetAccountParams`) → `{account: …|null, requiresOpenaiAuth: bool}`.
- **Model list:** `model/list` `{}` → `{data:[Model], nextCursor}`; `Model` requires `{id, displayName, description, hidden, isDefault, model, defaultReasoningEffort, supportedReasoningEfforts}`.
- **Thread:** `thread/start` params (all optional per v2 `ThreadStartParams`): `{cwd, model, approvalPolicy, sandbox, …}`. **CAUTION — schema catch:** on `thread/start` the field is **`sandbox`** with the kebab-case `SandboxMode` enum `"read-only"|"workspace-write"|"danger-full-access"` (the README's `"workspaceWrite"` example and the camelCase `sandboxPolicy` object exist only on `turn/start`; the 0.142.5 schema + the working probe used `sandbox:"read-only"`). `approvalPolicy` (`AskForApproval`): `"untrusted"|"on-failure"|"on-request"|"never"`. Result: `{thread:{id,…}, model, approvalPolicy, sandbox, cwd, …}` + `thread/started` notification. NOT ephemeral — the rollout file is Plan B's resume source. `thread/resume` `{threadId, …same overrides}` continues a stored session; response shape matches `thread/start`.
- **Turn:** `turn/start` `{threadId, input:[{type:"text",text}], model?, effort?, …}` (v2 `TurnStartParams`, required: `threadId`, `input`) → immediate ACK result `{turn:{id, status:"inProgress", items:[]}}`. Per-turn overrides (`model`, …) **become the default for subsequent turns on the same thread** — this is the inline model-switch seam. The turn then streams notifications and ends with `turn/completed`.
- **Turn-scoped notifications** (no `id`): `turn/started` `{threadId, turn}`; `item/started`/`item/completed` `{threadId, turnId, item, startedAtMs|completedAtMs}` where `item.type` ∈ `userMessage|agentMessage|plan|reasoning|commandExecution|fileChange|mcpToolCall|webSearch|imageView|sleep|…` (camelCase tags, v2 `ThreadItem`); `item/agentMessage/delta` `{threadId, turnId, itemId, delta}`; `item/reasoning/summaryTextDelta` + `item/reasoning/textDelta`; `item/commandExecution/outputDelta`; `turn/completed` `{threadId, turn:{id, status, error?}}` with `status` ∈ `"completed"|"interrupted"|"failed"` (`TurnStatus` also has `inProgress`, never terminal); `thread/tokenUsage/updated`; `error` `{threadId, turnId, error:{message, codexErrorInfo?, additionalDetails?}, willRetry}` may precede a failed `turn/completed`.
- **Approvals (server→client JSON-RPC requests, have `id` AND `method`; we must respond):** `item/commandExecution/requestApproval` `{threadId, turnId, itemId, command?, cwd?, reason?, commandActions?, …}` and `item/fileChange/requestApproval` `{threadId, turnId, itemId, reason?, grantRoot?}`. Respond with result `{"decision": …}` — command enum: `"accept" | "acceptForSession" | {acceptWithExecpolicyAmendment} | {applyNetworkPolicyAmendment} | "decline" | "cancel"`; fileChange enum: `"accept" | "acceptForSession" | "decline" | "cancel"`. optio mapping: allow→`"accept"`, deny→`"decline"` (`"decline"` = the agent continues the turn, matching optio deny semantics; `"cancel"` would also interrupt the turn — not what `PermissionDecision(behavior="deny")` means). A deny *message* is not transmittable on this wire (documented divergence vs claudecode). Other server→client requests exist (`item/tool/requestUserInput`, `item/permissions/requestApproval`, `mcpServer/elicitation/request`, `account/chatgptAuthTokens/refresh`, `attestation/generate`, legacy `applyPatchApproval`/`execCommandApproval`, `item/tool/call`) — answer all of them with a defensive JSON-RPC error `-32601` (they only fire for capabilities we don't advertise / flows we don't start).
- **Interrupt:** `turn/interrupt` `{threadId, turnId}` (both required) → ACK `{}`; the actual completion signal is the subsequent `turn/completed` with `status:"interrupted"`.
- **Backpressure:** `-32001` "Server overloaded; retry later." is retryable (documented in the conversation docstring; no special engine handling in this plan).

---

### Task 0: Baseline — environment sanity, prerequisite reconciliation (no commit)

**Files:** none (verification only).

- [ ] **Step 1: venv + suite baseline**

Run: `.venv/bin/python -c "import optio_codex, optio_agents; print(optio_codex.__file__)"` — must print a path inside this worktree. If not: `.venv/bin/pip install -e packages/optio-codex`.
Run: `.venv/bin/python -m pytest packages/optio-codex/tests/ -q` — must be green (whatever count Plans A–C left). Do not proceed on red.

- [ ] **Step 2: record which prerequisite plans have landed**

Run: `git -C . log --oneline -15` and `grep -n "supports_resume\|seed_id" packages/optio-codex/src/optio_codex/types.py; grep -n "resuming\|launch_subprocess\|proc_wait" packages/optio-codex/src/optio_codex/session.py`.
Record three booleans for later tasks: **B-landed** (`supports_resume`/`workdir_exclude` fields + `resuming` in session.py), **C-landed** (`seed_id`/`on_seed_saved` fields, `seed_manifest.py`, cred watcher blocks in the session `finally`), **demo-C-landed** (`packages/optio-demo/src/optio_demo/tasks/codex.py` has a seed loop). Task 5 and Task 9 branch on these.

- [ ] **Step 3: verify the protocol evidence exists**

Run: `ls /tmp/claude-1000/-home-csillag-deai-optio/e46ce9b4-db17-45cb-bf18-038271a8a8ea/scratchpad/schema/ClientRequest.json`. If missing AND a real `codex` ≥0.142.x binary is available: `codex app-server generate-json-schema --out <scratchpad>/schema2` and use that. If neither, the "Pinned app-server wire facts" section above is the fallback authority (it embeds everything the code needs).

*(No commit — nothing changed.)*

---

### Task 1: `models.py` — model-list mapping with static fallback

Port grok's `models.py` **shrunk**: codex has a clean in-session `model/list` request, so there is no CLI-listing middle tier. One pure function mapping the raw `model/list` result to the widget shape, with a static fallback (`gpt-5.5`, `gpt-5.4-mini`) that never raises and never empties the picker.

**Files:**
- Create: `packages/optio-codex/src/optio_codex/models.py`
- Create: `packages/optio-codex/tests/test_models.py`

**Interfaces:**
- Produces: `parse_model_list(result: dict | None) -> dict` returning `{"models": [{"id","label","disabled"}...], "default": str | None}`; module constant `FALLBACK_MODELS`.
- Consumed by: Task 5's session body (`widgetData.models`) via the `CodexConversation.model_list` captured at bootstrap (Task 2).

- [ ] **Step 1: Write the failing tests**

Create `packages/optio-codex/tests/test_models.py`:

```python
"""model/list → widget-shape mapping (Stage 7 model picker).

The conversation captures the raw ``model/list`` result at bootstrap
(``{data:[Model], nextCursor}`` — v2 ModelListResponse, codex-cli 0.142.5);
``parse_model_list`` maps it to the widget shape ``{models:[{id,label,
disabled}], default}``. Missing/malformed input falls back to the static
list — the picker is never falsely emptied and the parser never raises.
"""

from optio_codex.models import FALLBACK_MODELS, parse_model_list


def _entry(mid, name, *, default=False, hidden=False):
    # Only the fields the parser reads + the schema-required discriminators.
    return {
        "id": mid, "displayName": name, "description": "",
        "hidden": hidden, "isDefault": default, "model": mid,
        "defaultReasoningEffort": "medium", "supportedReasoningEfforts": [],
    }


def test_parse_maps_data_entries_to_widget_shape():
    out = parse_model_list({"data": [
        _entry("gpt-5.5", "GPT-5.5", default=True),
        _entry("gpt-5.4-mini", "GPT-5.4 Mini"),
    ], "nextCursor": None})
    assert out == {
        "models": [
            {"id": "gpt-5.5", "label": "GPT-5.5", "disabled": False},
            {"id": "gpt-5.4-mini", "label": "GPT-5.4 Mini", "disabled": False},
        ],
        "default": "gpt-5.5",
    }


def test_parse_skips_hidden_models():
    out = parse_model_list({"data": [
        _entry("gpt-5.5", "GPT-5.5", default=True),
        _entry("gpt-internal", "Internal", hidden=True),
    ]})
    assert [m["id"] for m in out["models"]] == ["gpt-5.5"]


def test_parse_default_none_when_no_isdefault_entry():
    out = parse_model_list({"data": [_entry("gpt-5.4-mini", "GPT-5.4 Mini")]})
    assert out["default"] is None
    assert out["models"][0]["id"] == "gpt-5.4-mini"


def test_parse_falls_back_on_none_and_malformed():
    assert parse_model_list(None) == FALLBACK_MODELS
    assert parse_model_list({}) == FALLBACK_MODELS
    assert parse_model_list({"data": "nope"}) == FALLBACK_MODELS
    assert parse_model_list({"data": []}) == FALLBACK_MODELS
    assert parse_model_list({"data": [{"displayName": "no id"}]}) == FALLBACK_MODELS


def test_fallback_is_copied_not_shared():
    out = parse_model_list(None)
    out["models"].append({"id": "x", "label": "x", "disabled": False})
    assert parse_model_list(None) == FALLBACK_MODELS  # untouched


def test_fallback_contents():
    ids = [m["id"] for m in FALLBACK_MODELS["models"]]
    assert ids == ["gpt-5.5", "gpt-5.4-mini"]
    assert FALLBACK_MODELS["default"] == "gpt-5.5"
```

- [ ] **Step 2: Run to verify failure**

Run: `.venv/bin/python -m pytest packages/optio-codex/tests/test_models.py -q`
Expected: FAIL — `ModuleNotFoundError: optio_codex.models`.

- [ ] **Step 3: Implement `packages/optio-codex/src/optio_codex/models.py`**

```python
"""Available-model list for the codex conversation widget's model picker.

============================================================================
MODEL-SWITCH MECHANISM — pinned from the app-server contract (codex-cli
0.142.5 schema dump + upstream README; see the conversation docstring).
============================================================================

Decision: **INLINE** (opencode/grok-style), NOT restart (claudecode-style) —
and codex needs NO dedicated set-model request at all: a ``model`` override
on ``turn/start`` "become[s] the default for subsequent turns" (README,
turn/start). So ``CodexConversation.request_model_change()`` just pins the
model sent with the next ``turn/start``; the session body needs no
model_change_requested restart loop.

MODEL LIST source: the ``model/list`` request, answered in-session
(``{data:[Model], nextCursor}``; ``Model`` carries ``id``, ``displayName``,
``hidden``, ``isDefault``). The conversation captures the raw result at
bootstrap; this module maps it to the widget shape. There is no CLI listing
tier (unlike grok) — live result → static fallback, nothing in between.
"""
from __future__ import annotations

# Shown when the live model/list is unavailable (fake agents, offline, error
# response). Version-sensitive vendor strings; update alongside the pinned
# codex-cli version.
FALLBACK_MODELS: dict = {
    "models": [
        {"id": "gpt-5.5", "label": "GPT-5.5", "disabled": False},
        {"id": "gpt-5.4-mini", "label": "GPT-5.4 Mini", "disabled": False},
    ],
    "default": "gpt-5.5",
}


def parse_model_list(result: "dict | None") -> dict:
    """Map a raw ``model/list`` result to the widget shape
    ``{models:[{id,label,disabled}], default}``.

    Hidden models are omitted; ``default`` is the ``isDefault`` entry's id
    (None when absent). Missing / malformed input returns the static
    fallback (never raises, never falsely empties the picker).
    """
    if not isinstance(result, dict):
        return _copy_fallback()
    data = result.get("data")
    if not isinstance(data, list):
        return _copy_fallback()
    out: list[dict] = []
    default: str | None = None
    for m in data:
        if not isinstance(m, dict):
            continue
        mid = m.get("id")
        if not isinstance(mid, str) or not mid or m.get("hidden"):
            continue
        out.append({"id": mid, "label": m.get("displayName") or mid, "disabled": False})
        if m.get("isDefault"):
            default = mid
    if not out:
        return _copy_fallback()
    return {"models": out, "default": default}


def _copy_fallback() -> dict:
    return {
        "models": [dict(m) for m in FALLBACK_MODELS["models"]],
        "default": FALLBACK_MODELS["default"],
    }
```

- [ ] **Step 4: Run to green**

Run: `.venv/bin/python -m pytest packages/optio-codex/tests/test_models.py -q` → all pass.
Run: `.venv/bin/python -m pytest packages/optio-codex/tests/ -q` → whole suite green.

- [ ] **Step 5: Commit**

```bash
git add packages/optio-codex/src/optio_codex/models.py packages/optio-codex/tests/test_models.py
git commit -m "feat(optio-codex): model-list mapping for the conversation picker

model/list result -> widget shape with a static gpt-5.5/gpt-5.4-mini
fallback; inline model switching needs no set-model request (turn/start
model override is sticky), so this is the whole Stage-7 model seam."
```

---

### Task 2: `CodexConversation` — app-server stdio client (+ unit tests)

Port grok's `conversation.py` skeleton EXACTLY (state fields, `attach`/`run_reader`/`bootstrap` split, `_write_lock`, two-tier `_event_queue` fan-out via a single `_dispatch_loop`, queue-permissions-until-handler, `_finish` drain guarantee, `close_requested` cooperative shutdown, `ConversationClosed` semantics) with the framing swapped to app-server. Codex-specific deltas vs grok: turn end = the `turn/completed` **notification** (grok: the `session/prompt` response); a `turn/start` **ACK response** exists and is NOT the turn end; answer accumulation is per-`itemId` `item/agentMessage/delta` with `item/completed` as the authoritative text; permission = the two `requestApproval` server requests answered `{"decision": "accept"|"decline"}`; interrupt = the `turn/interrupt` request (completion still signalled by `turn/completed{status:"interrupted"}`); model switch = pin the next `turn/start`'s `model` (no wire write at all); `thread_id` is captured and exposed for Plan B's snapshot `sessionId` seam, with `resume_thread_id` driving `thread/resume` at bootstrap.

**Files:**
- Create: `packages/optio-codex/src/optio_codex/conversation.py`
- Create: `packages/optio-codex/tests/test_conversation.py`

**Interfaces:**
- Produces: `class CodexConversation` implementing `optio_agents.conversation.Conversation` (runtime-checkable: `send`, `on_event`, `on_message`, `on_permission_request`, `is_pending`, `interrupt`, `close`, `closed`), plus the engine seams: `attach(handle)`, `run_reader()`, `bootstrap()`, `close_requested: asyncio.Event`, `thread_id: str | None`, `current_turn_id: str | None`, `current_model_id: str | None`, `model_list: dict | None` (raw `model/list` result), `account: dict | None`, `request_model_change(model: str) -> None` (sync).
- Constructor: `CodexConversation(*, cwd: str, permission_gate: bool = False, model: str | None = None, sandbox: str = "workspace-write", resume_thread_id: str | None = None)`.
- Consumes: `optio_agents.conversation.{ConversationClosed, PermissionDecision, PermissionRequest}`; a `ProcessHandle`-shaped object with `.stdin` (write/drain) and async-iterable `.stdout`.

- [ ] **Step 1: Write the failing tests**

Create `packages/optio-codex/tests/test_conversation.py`:

```python
"""CodexConversation unit tests against an in-process fake app-server handle.

The fake handle feeds JSONL lines (like `codex app-server` would emit on
stdout) and captures what the driver writes to stdin. Mirrors optio-grok's
test_conversation.py, but frames the codex app-server protocol (JSON-RPC 2.0
with the "jsonrpc" field omitted) instead of ACP.
"""

import asyncio
import json

import pytest

from optio_agents.conversation import (
    Conversation,
    ConversationClosed,
    PermissionDecision,
)
from optio_codex.conversation import CodexConversation


class _FakeStdin:
    def __init__(self):
        self.lines: asyncio.Queue[dict] = asyncio.Queue()

    def write(self, data: bytes) -> None:
        self.lines.put_nowait(json.loads(data.decode()))

    async def drain(self) -> None:
        pass


class _FakeStdout:
    def __init__(self):
        self.queue: asyncio.Queue[bytes | None] = asyncio.Queue()

    def feed(self, obj: dict) -> None:
        self.queue.put_nowait((json.dumps(obj) + "\n").encode())

    def eof(self) -> None:
        self.queue.put_nowait(None)

    def __aiter__(self):
        return self

    async def __anext__(self):
        item = await self.queue.get()
        if item is None:
            raise StopAsyncIteration
        return item


class _FakeHandle:
    def __init__(self):
        self.stdin = _FakeStdin()
        self.stdout = _FakeStdout()


MODEL_LIST = {
    "data": [
        {"id": "gpt-5.5", "displayName": "GPT-5.5", "description": "",
         "hidden": False, "isDefault": True, "model": "gpt-5.5",
         "defaultReasoningEffort": "medium", "supportedReasoningEfforts": []},
        {"id": "gpt-5.4-mini", "displayName": "GPT-5.4 Mini", "description": "",
         "hidden": False, "isDefault": False, "model": "gpt-5.4-mini",
         "defaultReasoningEffort": "medium", "supportedReasoningEfforts": []},
    ],
    "nextCursor": None,
}


async def _bootstrap(c, handle, thread_id="t1", resume=False):
    """Drive the app-server handshake by answering the driver's requests.

    Wire order (pinned by the live probe): initialize (request) ->
    initialized (notification) -> account/read -> model/list ->
    thread/start | thread/resume.
    """
    boot = asyncio.create_task(c.bootstrap())
    req = await asyncio.wait_for(handle.stdin.lines.get(), 1)
    assert req["method"] == "initialize"
    assert "jsonrpc" not in req                      # omitted on the wire
    assert req["params"]["clientInfo"]["name"] == "optio_codex"
    assert "experimentalApi" not in json.dumps(req)  # stable surface only
    handle.stdout.feed({"id": req["id"], "result": {
        "userAgent": "codex/0.142.5-fake", "codexHome": "/h",
        "platformFamily": "fake", "platformOs": "fake"}})
    note = await asyncio.wait_for(handle.stdin.lines.get(), 1)
    assert note == {"method": "initialized"}         # notification, no id
    req = await asyncio.wait_for(handle.stdin.lines.get(), 1)
    assert req["method"] == "account/read"
    assert req["params"] == {"refreshToken": False}
    handle.stdout.feed({"id": req["id"], "result": {
        "account": {"type": "apikey"}, "requiresOpenaiAuth": False}})
    req = await asyncio.wait_for(handle.stdin.lines.get(), 1)
    assert req["method"] == "model/list"
    handle.stdout.feed({"id": req["id"], "result": MODEL_LIST})
    req = await asyncio.wait_for(handle.stdin.lines.get(), 1)
    if resume:
        assert req["method"] == "thread/resume"
        assert req["params"]["threadId"] == thread_id
    else:
        assert req["method"] == "thread/start"
        assert req["params"]["cwd"] == "/w"
        # 0.142.5 schema: the field is `sandbox` (kebab-case enum), NOT
        # `sandboxPolicy` (that object exists only on turn/start).
        assert req["params"]["sandbox"] == "workspace-write"
        assert req["params"]["approvalPolicy"] in (
            "never", "on-request", "untrusted", "on-failure")
    handle.stdout.feed({"id": req["id"], "result": {
        "thread": {"id": thread_id}, "model": "gpt-5.5"}})
    await asyncio.wait_for(boot, 1)


def _delta(item_id: str, delta: str, turn_id="turn-1"):
    return {"method": "item/agentMessage/delta", "params": {
        "threadId": "t1", "turnId": turn_id, "itemId": item_id, "delta": delta}}


def _item_completed(item: dict, turn_id="turn-1"):
    return {"method": "item/completed", "params": {
        "threadId": "t1", "turnId": turn_id, "item": item, "completedAtMs": 0}}


def _turn_completed(turn_id="turn-1", status="completed"):
    return {"method": "turn/completed", "params": {
        "threadId": "t1", "turn": {"id": turn_id, "status": status, "items": []}}}


def _cmd_approval(req_id: int, command="echo hi"):
    return {"id": req_id, "method": "item/commandExecution/requestApproval",
            "params": {"threadId": "t1", "turnId": "turn-1", "itemId": "i-1",
                       "command": command, "cwd": "/w", "reason": None,
                       "startedAtMs": 0}}


@pytest.fixture
def convo():
    handle = _FakeHandle()
    c = CodexConversation(cwd="/w", permission_gate=True)
    c.attach(handle)
    return c, handle


def test_satisfies_conversation_protocol(convo):
    c, _ = convo
    assert isinstance(c, Conversation)


@pytest.mark.asyncio
async def test_send_receive_and_on_event_transparent(convo):
    c, handle = convo
    reader = asyncio.create_task(c.run_reader())
    await _bootstrap(c, handle)

    events, texts = [], []
    c.on_event(events.append)
    c.on_message(texts.append)

    assert not c.is_pending()
    await c.send("say PONG")
    assert c.is_pending()
    turn_req = await asyncio.wait_for(handle.stdin.lines.get(), 1)
    assert turn_req["method"] == "turn/start"
    assert turn_req["params"]["threadId"] == "t1"
    assert turn_req["params"]["input"] == [{"type": "text", "text": "say PONG"}]

    # ACK is NOT the turn end.
    handle.stdout.feed({"id": turn_req["id"], "result": {
        "turn": {"id": "turn-1", "status": "inProgress", "items": []}}})
    await _wait_for(lambda: c.is_pending())

    for piece in ("PO", "NG"):
        handle.stdout.feed(_delta("i-msg", piece))
    handle.stdout.feed(_item_completed(
        {"type": "agentMessage", "id": "i-msg", "text": "PONG"}))
    handle.stdout.feed(_turn_completed())

    reply = await asyncio.wait_for(_first(texts), 2)
    assert reply == "PONG"
    await _wait_for(lambda: not c.is_pending())

    methods = [e.get("method") for e in events]
    assert methods.count("item/agentMessage/delta") >= 2  # raw objects, unmodified

    handle.stdout.eof()
    await reader


@pytest.mark.asyncio
async def test_reasoning_deltas_not_folded_into_answer(convo):
    c, handle = convo
    reader = asyncio.create_task(c.run_reader())
    await _bootstrap(c, handle)
    texts = []
    c.on_message(texts.append)
    await c.send("think then answer")
    turn_req = await asyncio.wait_for(handle.stdin.lines.get(), 1)
    handle.stdout.feed({"id": turn_req["id"], "result": {
        "turn": {"id": "turn-1", "status": "inProgress", "items": []}}})
    handle.stdout.feed({"method": "item/reasoning/summaryTextDelta", "params": {
        "threadId": "t1", "turnId": "turn-1", "itemId": "i-r",
        "delta": "hmm", "summaryIndex": 0}})
    handle.stdout.feed(_delta("i-msg", "ANSWER"))
    handle.stdout.feed(_turn_completed())
    reply = await asyncio.wait_for(_first(texts), 2)
    assert reply == "ANSWER"
    handle.stdout.eof()
    await reader


@pytest.mark.asyncio
async def test_item_completed_text_is_authoritative(convo):
    # A replay/drop gap in deltas is healed by item/completed's full text.
    c, handle = convo
    reader = asyncio.create_task(c.run_reader())
    await _bootstrap(c, handle)
    texts = []
    c.on_message(texts.append)
    await c.send("x")
    await asyncio.wait_for(handle.stdin.lines.get(), 1)
    handle.stdout.feed(_delta("i-msg", "PO"))  # "NG" delta lost
    handle.stdout.feed(_item_completed(
        {"type": "agentMessage", "id": "i-msg", "text": "PONG"}))
    handle.stdout.feed(_turn_completed())
    assert await asyncio.wait_for(_first(texts), 2) == "PONG"
    handle.stdout.eof()
    await reader


@pytest.mark.asyncio
async def test_two_agent_messages_in_one_turn_concatenate(convo):
    c, handle = convo
    reader = asyncio.create_task(c.run_reader())
    await _bootstrap(c, handle)
    texts = []
    c.on_message(texts.append)
    await c.send("x")
    await asyncio.wait_for(handle.stdin.lines.get(), 1)
    handle.stdout.feed(_delta("i-1", "part1 "))
    handle.stdout.feed(_item_completed(
        {"type": "agentMessage", "id": "i-1", "text": "part1 "}))
    handle.stdout.feed(_delta("i-2", "part2"))
    handle.stdout.feed(_item_completed(
        {"type": "agentMessage", "id": "i-2", "text": "part2"}))
    handle.stdout.feed(_turn_completed())
    assert await asyncio.wait_for(_first(texts), 2) == "part1 part2"
    handle.stdout.eof()
    await reader


@pytest.mark.asyncio
async def test_permission_request_roundtrip_deny(convo):
    c, handle = convo
    reader = asyncio.create_task(c.run_reader())
    await _bootstrap(c, handle)

    seen = {}

    async def handler(req):
        seen["tool"] = req.tool_name
        seen["input"] = req.input
        seen["raw_id"] = req.raw.get("id")
        return PermissionDecision(behavior="deny", message="nope")

    c.on_permission_request(handler)
    handle.stdout.feed(_cmd_approval(99))
    resp = await asyncio.wait_for(handle.stdin.lines.get(), 2)
    assert resp["id"] == 99
    assert resp["result"] == {"decision": "decline"}
    assert seen["tool"] == "echo hi"          # command string as the tool name
    assert seen["input"]["command"] == "echo hi"
    assert seen["raw_id"] == 99               # correlation key for the listener
    handle.stdout.eof()
    await reader


@pytest.mark.asyncio
async def test_permission_request_allow_answers_accept(convo):
    c, handle = convo
    reader = asyncio.create_task(c.run_reader())
    await _bootstrap(c, handle)

    async def handler(req):
        return PermissionDecision(behavior="allow")

    c.on_permission_request(handler)
    handle.stdout.feed(_cmd_approval(7))
    resp = await asyncio.wait_for(handle.stdin.lines.get(), 2)
    assert resp == {"id": 7, "result": {"decision": "accept"}}
    handle.stdout.eof()
    await reader


@pytest.mark.asyncio
async def test_file_change_approval_maps_too(convo):
    c, handle = convo
    reader = asyncio.create_task(c.run_reader())
    await _bootstrap(c, handle)

    async def handler(req):
        assert req.tool_name == "file change"
        return PermissionDecision(behavior="allow")

    c.on_permission_request(handler)
    handle.stdout.feed({"id": 8, "method": "item/fileChange/requestApproval",
                        "params": {"threadId": "t1", "turnId": "turn-1",
                                   "itemId": "i-2", "reason": None,
                                   "startedAtMs": 0}})
    resp = await asyncio.wait_for(handle.stdin.lines.get(), 2)
    assert resp == {"id": 8, "result": {"decision": "accept"}}
    handle.stdout.eof()
    await reader


@pytest.mark.asyncio
async def test_permission_queued_until_handler_registered(convo):
    # The request arrives BEFORE on_permission_request — it must be queued,
    # not dropped/denied (closes the publish/registration race).
    c, handle = convo
    reader = asyncio.create_task(c.run_reader())
    await _bootstrap(c, handle)
    handle.stdout.feed(_cmd_approval(55))
    await asyncio.sleep(0.05)                  # let the reader route it
    assert handle.stdin.lines.empty()          # nothing answered yet

    async def handler(req):
        return PermissionDecision(behavior="allow")

    c.on_permission_request(handler)
    resp = await asyncio.wait_for(handle.stdin.lines.get(), 2)
    assert resp == {"id": 55, "result": {"decision": "accept"}}
    handle.stdout.eof()
    await reader


@pytest.mark.asyncio
async def test_gate_off_denies_permission_defensively():
    handle = _FakeHandle()
    c = CodexConversation(cwd="/w")  # permission_gate=False (default)
    c.attach(handle)
    reader = asyncio.create_task(c.run_reader())
    await _bootstrap(c, handle)
    handle.stdout.feed(_cmd_approval(5))
    resp = await asyncio.wait_for(handle.stdin.lines.get(), 2)
    assert resp == {"id": 5, "result": {"decision": "decline"}}
    handle.stdout.eof()
    await reader


@pytest.mark.asyncio
async def test_unknown_server_request_gets_method_not_found(convo):
    c, handle = convo
    reader = asyncio.create_task(c.run_reader())
    await _bootstrap(c, handle)
    handle.stdout.feed({"id": 12, "method": "item/tool/requestUserInput",
                        "params": {"threadId": "t1"}})
    resp = await asyncio.wait_for(handle.stdin.lines.get(), 2)
    assert resp["id"] == 12
    assert resp["error"]["code"] == -32601
    handle.stdout.eof()
    await reader


@pytest.mark.asyncio
async def test_interrupt_sends_turn_interrupt(convo):
    c, handle = convo
    reader = asyncio.create_task(c.run_reader())
    await _bootstrap(c, handle)
    await c.send("count to 100")
    turn_req = await asyncio.wait_for(handle.stdin.lines.get(), 1)
    handle.stdout.feed({"id": turn_req["id"], "result": {
        "turn": {"id": "turn-1", "status": "inProgress", "items": []}}})
    await _wait_for(lambda: c.current_turn_id == "turn-1")
    assert c.is_pending()

    intr_task = asyncio.create_task(c.interrupt())
    intr = await asyncio.wait_for(handle.stdin.lines.get(), 1)
    assert intr["method"] == "turn/interrupt"
    assert intr["params"] == {"threadId": "t1", "turnId": "turn-1"}
    handle.stdout.feed({"id": intr["id"], "result": {}})  # ACK, not completion
    await asyncio.wait_for(intr_task, 1)
    assert c.is_pending()                                  # still in flight
    handle.stdout.feed(_turn_completed(status="interrupted"))
    await _wait_for(lambda: not c.is_pending())
    handle.stdout.eof()
    await reader


@pytest.mark.asyncio
async def test_interrupt_idle_is_noop(convo):
    c, handle = convo
    reader = asyncio.create_task(c.run_reader())
    await _bootstrap(c, handle)
    await c.interrupt()                       # no pending turn -> no write
    assert handle.stdin.lines.empty()
    handle.stdout.eof()
    await reader


@pytest.mark.asyncio
async def test_unparseable_line_becomes_synthetic_event(convo):
    c, handle = convo
    events = []
    c.on_event(events.append)
    reader = asyncio.create_task(c.run_reader())
    await _bootstrap(c, handle)
    handle.stdout.queue.put_nowait(b"this is not json\n")
    handle.stdout.eof()
    await reader
    assert any(e.get("type") == "x-optio-unparseable" for e in events)


@pytest.mark.asyncio
async def test_eof_closes_and_emits_synthetic_closed(convo):
    c, handle = convo
    events = []
    c.on_event(events.append)
    reader = asyncio.create_task(c.run_reader())
    await _bootstrap(c, handle)
    handle.stdout.eof()
    await reader
    assert c.closed
    assert events[-1].get("type") == "x-optio-closed"  # drain guarantee
    with pytest.raises(ConversationClosed):
        await c.send("too late")
    with pytest.raises(ConversationClosed):
        await c.interrupt()


@pytest.mark.asyncio
async def test_close_sets_close_requested(convo):
    c, handle = convo
    await c.close()
    assert c.close_requested.is_set()


@pytest.mark.asyncio
async def test_bootstrap_captures_account_models_and_thread_id(convo):
    c, handle = convo
    reader = asyncio.create_task(c.run_reader())
    await _bootstrap(c, handle)
    assert c.thread_id == "t1"
    assert c.account == {"type": "apikey"}
    assert c.model_list["data"][1]["id"] == "gpt-5.4-mini"
    assert c.current_model_id == "gpt-5.5"    # thread/start result.model
    handle.stdout.eof()
    await reader


@pytest.mark.asyncio
async def test_bootstrap_resume_uses_thread_resume():
    handle = _FakeHandle()
    c = CodexConversation(cwd="/w", resume_thread_id="t9")
    c.attach(handle)
    reader = asyncio.create_task(c.run_reader())
    await _bootstrap(c, handle, thread_id="t9", resume=True)
    assert c.thread_id == "t9"
    handle.stdout.eof()
    await reader


@pytest.mark.asyncio
async def test_request_model_change_applies_on_next_turn(convo):
    # INLINE switch, no wire write: the next turn/start pins the model and it
    # sticks for subsequent turns (app-server contract).
    c, handle = convo
    reader = asyncio.create_task(c.run_reader())
    await _bootstrap(c, handle)
    c.request_model_change("gpt-5.4-mini")
    assert c.current_model_id == "gpt-5.4-mini"   # optimistic
    assert handle.stdin.lines.empty()              # nothing on the wire yet
    await c.send("hello")
    turn_req = await asyncio.wait_for(handle.stdin.lines.get(), 1)
    assert turn_req["params"]["model"] == "gpt-5.4-mini"
    handle.stdout.eof()
    await reader


@pytest.mark.asyncio
async def test_request_model_change_after_close_raises(convo):
    c, handle = convo
    reader = asyncio.create_task(c.run_reader())
    await _bootstrap(c, handle)
    handle.stdout.eof()
    await reader
    with pytest.raises(ConversationClosed):
        c.request_model_change("gpt-5.4-mini")


@pytest.mark.asyncio
async def test_turn_start_error_response_unwinds_pending(convo):
    c, handle = convo
    reader = asyncio.create_task(c.run_reader())
    await _bootstrap(c, handle)
    await c.send("x")
    turn_req = await asyncio.wait_for(handle.stdin.lines.get(), 1)
    handle.stdout.feed({"id": turn_req["id"], "error": {
        "code": -32001, "message": "Server overloaded; retry later."}})
    await _wait_for(lambda: not c.is_pending())   # no turn will ever complete
    handle.stdout.eof()
    await reader


# --- tiny polling helpers ---------------------------------------------------

async def _first(bucket: list, timeout: float = 2.0):
    end = asyncio.get_event_loop().time() + timeout
    while asyncio.get_event_loop().time() < end:
        if bucket:
            return bucket[0]
        await asyncio.sleep(0.01)
    raise AssertionError("no item arrived")


async def _wait_for(pred, timeout: float = 2.0):
    end = asyncio.get_event_loop().time() + timeout
    while asyncio.get_event_loop().time() < end:
        if pred():
            return
        await asyncio.sleep(0.01)
    raise AssertionError("condition not met")
```

- [ ] **Step 2: Run to verify failure**

Run: `.venv/bin/python -m pytest packages/optio-codex/tests/test_conversation.py -q`
Expected: FAIL — `ModuleNotFoundError: optio_codex.conversation`.

- [ ] **Step 3: Implement `packages/optio-codex/src/optio_codex/conversation.py`**

```python
"""CodexConversation — engine-side driver for one headless Codex session over
the app-server protocol: JSON-RPC 2.0 over the stdin/stdout of
``codex app-server``.

The session body launches ``codex app-server`` via
``host.launch_subprocess(stdin=True, merge_stderr=False)``, attaches the
handle here, starts ``run_reader()``, runs ``bootstrap()`` (the handshake),
publishes this object via ``ctx.publish_result``, and waits until the
subprocess ends.

Event payloads are transparent: every parsed stdout JSON-RPC object is fanned
out to ``on_event`` subscribers as a dict, unmodified. Synthetic events use
the ``x-optio-`` type prefix. Structurally mirrors optio-grok's
GrokConversation, but frames the codex app-server protocol instead of ACP.

============================================================================
APP-SERVER WIRE FACTS — pinned by a LIVE PROBE of the real ``codex
app-server`` (codex-cli 0.142.5) plus the schema dump generated by that exact
binary (``codex app-server generate-json-schema``). Evidence: the scratchpad
``probe.py`` transcript + ``schema/{ClientRequest,ServerRequest,
ServerNotification,ClientNotification}.json``. See the design doc
"Conversation transport (Stage 6)".
============================================================================

FRAMING: newline-delimited JSON (JSONL); JSON-RPC 2.0 semantics with the
``"jsonrpc"`` field OMITTED on the wire (both directions; probed). NO
Content-Length headers. Backpressure: error ``-32001`` "Server overloaded;
retry later." is retryable.

Client -> server REQUESTS (have ``id``, expect a ``result``):
  * ``initialize`` {clientInfo:{name,title,version}, capabilities?} ->
    {userAgent, codexHome, platformFamily, platformOs}. We do NOT set
    ``capabilities.experimentalApi`` (stable surface only);
    ``optOutNotificationMethods`` suppresses exact-match notifications
    (we opt out of the unrendered ``item/commandExecution/outputDelta``).
    Followed by the ``initialized`` NOTIFICATION (no id). Any other request
    first -> "Not initialized" error.
  * ``account/read`` {refreshToken:false} -> {account|null,
    requiresOpenaiAuth} — bootstrap auth sanity (warn-only, never fatal:
    fakes and API-key setups may report no account).
  * ``model/list`` {} -> {data:[{id, displayName, hidden, isDefault, …}],
    nextCursor} — captured raw for the widget picker (models.py maps it).
  * ``thread/start`` {cwd, sandbox, approvalPolicy, model?} ->
    {thread:{id,…}, model, …} (+ a ``thread/started`` notification).
    SCHEMA CATCH: on thread/start the field is ``sandbox`` with the
    kebab-case enum "read-only"|"workspace-write"|"danger-full-access";
    the camelCase ``sandboxPolicy`` OBJECT exists only on turn/start
    (0.142.5 ThreadStartParams; the probe used sandbox:"read-only").
    NOT ephemeral — the rollout file is the resume source (Plan B).
  * ``thread/resume`` {threadId, …same overrides} — resume path; response
    shape matches thread/start.
  * ``turn/start`` {threadId, input:[{type:"text",text}], model?} ->
    IMMEDIATE ACK {turn:{id, status:"inProgress", items:[]}} — the ACK is
    NOT the turn end. Per-turn overrides (``model``) become the default for
    subsequent turns on the thread == the INLINE model-switch seam
    (request_model_change pins the next turn's model; no wire write).
  * ``turn/interrupt`` {threadId, turnId} -> {} ACK; the completion signal
    is the subsequent ``turn/completed`` with status "interrupted".

Server -> client NOTIFICATIONS (no ``id``):
  * ``turn/started`` {threadId, turn:{id,…}} — tracks current_turn_id.
  * ``item/agentMessage/delta`` {threadId, turnId, itemId, delta} —
    concatenated per itemId -> the turn's answer text.
  * ``item/completed`` {…, item:{type,…}} — for item.type "agentMessage"
    the item's ``text`` is authoritative for that itemId.
  * ``item/started`` / ``item/reasoning/summaryTextDelta`` /
    ``item/reasoning/textDelta`` / ``thread/tokenUsage/updated`` /
    ``error`` {error:{message, codexErrorInfo?}, willRetry} / … — passed
    through untouched to on_event (the UI reducer renders them).
  * ``turn/completed`` {threadId, turn:{id, status, error?}} — THE TURN-END
    SIGNAL, status ∈ "completed" | "interrupted" | "failed".

Server -> client REQUESTS (have ``id`` AND ``method``, WE must respond):
  * ``item/commandExecution/requestApproval`` {threadId, turnId, itemId,
    command?, cwd?, reason?, …} and ``item/fileChange/requestApproval``
    {threadId, turnId, itemId, reason?, grantRoot?} — the permission gate
    seam. ANSWER with result {"decision": D}: optio allow -> "accept";
    optio deny -> "decline" (the agent continues the turn — "cancel" would
    also interrupt it, which is NOT what PermissionDecision deny means).
    The full decision enums also carry "acceptForSession" and (commands
    only) execpolicy/network amendment objects — unused here. A deny
    *message* is not transmittable on this wire.
  * Anything else (``item/tool/requestUserInput``, ``item/permissions/
    requestApproval``, ``mcpServer/elicitation/request``, ``account/
    chatgptAuthTokens/refresh``, ``attestation/generate``, legacy
    ``applyPatchApproval``/``execCommandApproval``, ``item/tool/call``) —
    answered with a JSON-RPC ``-32601`` error defensively (they only fire
    for capabilities we don't advertise / flows we don't start).
============================================================================
"""

from __future__ import annotations

import asyncio
import json
import logging

from optio_agents.conversation import (
    ConversationClosed,
    PermissionDecision,
    PermissionRequest,
)

_LOG = logging.getLogger(__name__)

# Server->client requests that ARE the permission gate; everything else with
# an id+method gets a defensive -32601.
_APPROVAL_METHODS = (
    "item/commandExecution/requestApproval",
    "item/fileChange/requestApproval",
)


class CodexConversation:
    """Implements optio_agents.conversation.Conversation for Codex
    (app-server)."""

    def __init__(
        self,
        *,
        cwd: str,
        agent_label: str = "codex",
        permission_gate: bool = False,
        model: str | None = None,
        sandbox: str = "workspace-write",
        resume_thread_id: str | None = None,
    ) -> None:
        self._cwd = cwd
        self._agent_label = agent_label
        # When False, requestApproval is answered with a defensive deny
        # instead of being queued for a handler.
        self._permission_gate = permission_gate
        self._model = model
        self._sandbox = sandbox
        # With the gate on codex must ask (on-request); without it, never.
        self._approval_policy = "on-request" if permission_gate else "never"
        self._resume_thread_id = resume_thread_id
        self._handle = None
        # Captured at bootstrap; Plan B's snapshot sessionId seam reads
        # thread_id at capture time.
        self.thread_id: str | None = None
        self.current_turn_id: str | None = None
        self.server_info: dict | None = None
        self.account: dict | None = None
        # Raw model/list result from bootstrap (models.parse_model_list maps
        # it) so the session can populate the picker without a second probe.
        self.model_list: dict | None = None
        self.current_model_id: str | None = None
        self._requested_model: str | None = None
        self._pending = 0                    # user turns awaiting turn/completed
        self._closed = asyncio.Event()
        self._close_reason: str | None = None
        # Cooperative-shutdown request towards the owning task body.
        self.close_requested = asyncio.Event()
        self._write_lock = asyncio.Lock()
        self._event_queue: asyncio.Queue[dict] = asyncio.Queue()
        self._event_handlers: list = []
        self._message_handlers: list = []
        self._permission_handler = None
        self._queued_permission_requests: list[dict] = []
        # JSON-RPC id bookkeeping.
        self._next_id = 0
        self._req_futures: dict[int, asyncio.Future] = {}   # handshake/interrupt
        self._turn_req_ids: set[int] = set()                # turn/start ACKs
        # Accumulates agentMessage text for the current turn, per itemId.
        self._answer_order: list[str] = []
        self._answer_texts: dict[str, str] = {}
        self._dispatcher_task: asyncio.Task | None = None

    # -- wiring ------------------------------------------------------------

    def attach(self, handle) -> None:
        """Attach the live ProcessHandle (must have been launched with
        stdin=True)."""
        if handle.stdin is None:
            raise ValueError(
                "CodexConversation.attach: handle has no stdin writer; launch "
                "the subprocess with stdin=True"
            )
        self._handle = handle

    async def bootstrap(self) -> None:
        """Run the app-server handshake: ``initialize`` + ``initialized``,
        then ``account/read`` (auth sanity, warn-only), ``model/list`` (the
        picker source), and ``thread/start`` / ``thread/resume``.

        Requires ``run_reader()`` to already be running (it routes the
        responses back to the futures created here).
        """
        resp = await self._request("initialize", {
            "clientInfo": {
                "name": "optio_codex", "title": "Optio", "version": "0.1.0",
            },
            # Stable surface only (no experimentalApi). Opt out of the one
            # high-volume stream nothing downstream renders.
            "capabilities": {
                "optOutNotificationMethods": [
                    "item/commandExecution/outputDelta",
                ],
            },
        })
        self.server_info = (resp or {}).get("result") or {}
        await self._write_json({"method": "initialized"})

        resp = await self._request("account/read", {"refreshToken": False})
        self.account = ((resp or {}).get("result") or {}).get("account")
        if self.account is None:
            _LOG.warning(
                "codex conversation: account/read reports no active auth; "
                "turns may fail until a seed/login provides credentials",
            )

        resp = await self._request("model/list", {})
        self.model_list = (
            (resp or {}).get("result") if "error" not in (resp or {}) else None
        )

        if self._resume_thread_id is not None:
            method = "thread/resume"
            params: dict = {"threadId": self._resume_thread_id}
        else:
            method = "thread/start"
            params = {"cwd": self._cwd}
        # thread/start takes `sandbox` (kebab-case enum) — NOT the turn-level
        # `sandboxPolicy` object (see the module docstring's schema catch).
        params["sandbox"] = self._sandbox
        params["approvalPolicy"] = self._approval_policy
        if self._model:
            params["model"] = self._model
        resp = await self._request(method, params)
        if "error" in (resp or {}):
            raise RuntimeError(
                f"codex {method} failed: {(resp or {}).get('error')!r}"
            )
        result = (resp or {}).get("result") or {}
        thread = result.get("thread") or {}
        self.thread_id = thread.get("id")
        if not self.thread_id:
            raise RuntimeError(
                f"codex {method} returned no thread id: {result!r}"
            )
        self.current_model_id = result.get("model") or self._model

    async def run_reader(self) -> None:
        """Drain stdout until EOF; route JSON-RPC messages. Owned by the
        session body; ends when the subprocess ends."""
        self._dispatcher_task = asyncio.create_task(self._dispatch_loop())
        try:
            async for raw in self._handle.stdout:
                line = (
                    raw.decode("utf-8", errors="replace")
                    if isinstance(raw, bytes) else str(raw)
                ).strip()
                if not line:
                    continue
                try:
                    obj = json.loads(line)
                except ValueError:
                    _LOG.warning("codex conversation: unparseable line: %.200s", line)
                    self._event_queue.put_nowait(
                        {"type": "x-optio-unparseable", "line": line},
                    )
                    continue
                self._route(obj)
        finally:
            await self._finish("process ended")

    def _route(self, obj: dict) -> None:
        rid = obj.get("id")
        method = obj.get("method")
        if method is None and rid is not None and ("result" in obj or "error" in obj):
            # Response to one of OUR requests.
            if rid in self._req_futures:
                fut = self._req_futures.pop(rid)
                if not fut.done():
                    fut.set_result(obj)
            elif rid in self._turn_req_ids:
                # turn/start ACK — NOT the turn end (that is turn/completed).
                self._turn_req_ids.discard(rid)
                if "error" in obj:
                    # The turn never started; nothing further will arrive.
                    _LOG.warning(
                        "codex conversation: turn/start rejected: %r",
                        obj.get("error"),
                    )
                    self._pending = max(0, self._pending - 1)
                else:
                    turn = ((obj.get("result") or {}).get("turn")) or {}
                    if turn.get("id"):
                        self.current_turn_id = turn["id"]
        elif method is not None and rid is not None:
            # Server -> client REQUEST that we must answer.
            if method in _APPROVAL_METHODS:
                self._on_permission(obj)
            else:
                asyncio.ensure_future(self._write_json({
                    "id": rid,
                    "error": {"code": -32601,
                              "message": f"optio codex client does not implement {method}"},
                }))
        elif method is not None:
            self._on_notification(method, obj)
        self._event_queue.put_nowait(obj)

    def _on_notification(self, method: str, obj: dict) -> None:
        params = obj.get("params") or {}
        if method == "item/agentMessage/delta":
            item_id = str(params.get("itemId") or "")
            delta = params.get("delta") or ""
            if delta:
                if item_id not in self._answer_texts:
                    self._answer_order.append(item_id)
                    self._answer_texts[item_id] = ""
                self._answer_texts[item_id] += delta
        elif method == "item/completed":
            item = params.get("item") or {}
            if item.get("type") == "agentMessage":
                item_id = str(item.get("id") or "")
                if item_id not in self._answer_texts:
                    self._answer_order.append(item_id)
                # The completed item's text is authoritative for its itemId
                # (heals any lost/duplicated deltas).
                self._answer_texts[item_id] = (
                    item.get("text") or self._answer_texts.get(item_id, "")
                )
        elif method == "turn/started":
            turn = params.get("turn") or {}
            if turn.get("id"):
                self.current_turn_id = turn["id"]
        elif method == "turn/completed":
            # THE turn-end signal (status completed | interrupted | failed).
            self._pending = max(0, self._pending - 1)
            self.current_turn_id = None
            text = "".join(self._answer_texts[i] for i in self._answer_order)
            self._answer_order = []
            self._answer_texts = {}
            self._fire_message(text)
        # else: thread/started, item/started, reasoning deltas, tokenUsage,
        # error, … — pass through to on_event only.

    # -- event fan-out -----------------------------------------------------

    async def _dispatch_loop(self) -> None:
        while True:
            obj = await self._event_queue.get()
            for handler in list(self._event_handlers):
                await self._call_handler(handler, obj, "on_event")

    async def _call_handler(self, handler, arg, label: str) -> None:
        try:
            result = handler(arg)
            if asyncio.iscoroutine(result):
                await result
        except Exception:  # noqa: BLE001 — subscriber bugs never kill the driver
            _LOG.exception("codex conversation: %s handler raised", label)

    def _fire_message(self, text: str) -> None:
        for handler in list(self._message_handlers):
            asyncio.ensure_future(self._call_handler(handler, text, "on_message"))

    # -- permission gate ----------------------------------------------------

    def _on_permission(self, obj: dict) -> None:
        if not self._permission_gate:
            _LOG.warning(
                "codex conversation: %s received with permission_gate off; "
                "denying defensively", obj.get("method"),
            )
            asyncio.ensure_future(self._answer_permission_decision(
                obj, PermissionDecision(
                    behavior="deny",
                    message="optio harness: permission gate not enabled",
                ),
            ))
            return
        if self._permission_handler is None:
            # Queue until a handler is registered; the turn blocks
            # server-side, which closes the publish/registration race.
            self._queued_permission_requests.append(obj)
            return
        asyncio.ensure_future(self._answer_permission(obj))

    async def _answer_permission(self, obj: dict) -> None:
        params = obj.get("params") or {}
        if obj.get("method") == "item/commandExecution/requestApproval":
            tool_name = str(params.get("command") or "command execution")
        else:
            tool_name = "file change"
        request = PermissionRequest(
            tool_name=tool_name,
            input=params,
            raw=obj,
        )
        try:
            decision = await self._permission_handler(request)
        except Exception:  # noqa: BLE001
            _LOG.exception("codex conversation: permission handler raised; denying")
            decision = PermissionDecision(
                behavior="deny",
                message="optio harness: permission handler failed",
            )
        await self._answer_permission_decision(obj, decision)

    async def _answer_permission_decision(
        self, obj: dict, decision: PermissionDecision,
    ) -> None:
        # allow -> accept; deny -> decline (the agent continues the turn —
        # "cancel" would also interrupt it, which deny does not mean). The
        # deny message is not transmittable on this wire.
        decision_str = "accept" if decision.behavior == "allow" else "decline"
        await self._write_json({
            "id": obj.get("id"),
            "result": {"decision": decision_str},
        })

    # -- Conversation protocol surface --------------------------------------

    async def send(self, text: str) -> None:
        if self._closed.is_set():
            raise ConversationClosed(self._close_reason or "conversation closed")
        if self.thread_id is None:
            raise RuntimeError("CodexConversation.send before bootstrap() completed")
        self._next_id += 1
        rid = self._next_id
        self._turn_req_ids.add(rid)
        self._pending += 1
        params: dict = {
            "threadId": self.thread_id,
            "input": [{"type": "text", "text": text}],
        }
        if self._requested_model is not None:
            # Inline model switch: the override becomes the thread default
            # for subsequent turns (app-server contract).
            params["model"] = self._requested_model
        try:
            await self._write_json({
                "id": rid, "method": "turn/start", "params": params,
            })
        except Exception:
            self._turn_req_ids.discard(rid)
            self._pending = max(0, self._pending - 1)
            await self._finish("stdin write failed")
            raise

    def on_event(self, handler):
        self._event_handlers.append(handler)
        return lambda: self._event_handlers.remove(handler)

    def on_message(self, handler):
        self._message_handlers.append(handler)
        return lambda: self._message_handlers.remove(handler)

    def on_permission_request(self, handler):
        self._permission_handler = handler
        queued, self._queued_permission_requests = (
            self._queued_permission_requests, [],
        )
        for obj in queued:
            asyncio.ensure_future(self._answer_permission(obj))

        def _unsub() -> None:
            if self._permission_handler is handler:
                self._permission_handler = None
        return _unsub

    def is_pending(self) -> bool:
        return self._pending > 0

    async def interrupt(self) -> None:
        if self._closed.is_set():
            raise ConversationClosed(self._close_reason or "conversation closed")
        if (
            self._pending == 0
            or self.thread_id is None
            or self.current_turn_id is None
        ):
            return
        # turn/interrupt is a normal request; its {} ACK is NOT the
        # completion signal — the in-flight turn ends via turn/completed
        # with status "interrupted".
        await self._request("turn/interrupt", {
            "threadId": self.thread_id, "turnId": self.current_turn_id,
        })

    def request_model_change(self, model: str) -> None:
        """Switch model mid-conversation INLINE — with NO wire write: a
        ``model`` override on the next ``turn/start`` becomes the thread's
        default for subsequent turns (app-server contract; see models.py).
        Synchronous surface (the listener calls it without await)."""
        if self._closed.is_set():
            raise ConversationClosed(self._close_reason or "conversation closed")
        if self.thread_id is None:
            raise RuntimeError(
                "CodexConversation.request_model_change before bootstrap() completed"
            )
        self._requested_model = model
        self.current_model_id = model  # optimistic; the next turn pins it

    async def close(self) -> None:
        self.close_requested.set()

    @property
    def closed(self) -> bool:
        return self._closed.is_set()

    # -- internals -----------------------------------------------------------

    async def _request(self, method: str, params: dict) -> dict:
        """Send a client->server request and await its response (handshake +
        turn/interrupt only; turn/start is tracked via _turn_req_ids)."""
        self._next_id += 1
        rid = self._next_id
        fut: asyncio.Future = asyncio.get_event_loop().create_future()
        self._req_futures[rid] = fut
        await self._write_json({"id": rid, "method": method, "params": params})
        return await fut

    async def _write_json(self, obj: dict) -> None:
        # The app-server wire omits the "jsonrpc" field (probed; README).
        await self._write_bytes((json.dumps(obj) + "\n").encode("utf-8"))

    async def _write_bytes(self, data: bytes) -> None:
        async with self._write_lock:
            stdin = self._handle.stdin
            stdin.write(data)
            drain = getattr(stdin, "drain", None)
            if drain is not None:
                await drain()

    async def _finish(self, reason: str) -> None:
        if self._closed.is_set():
            return
        self._closed.set()
        self._close_reason = reason
        # Fail any in-flight handshake/interrupt requests.
        for fut in self._req_futures.values():
            if not fut.done():
                fut.set_exception(ConversationClosed(reason))
        self._req_futures.clear()
        self._event_queue.put_nowait({"type": "x-optio-closed", "reason": reason})
        # Stop the dispatcher, then drain whatever it left so subscribers are
        # guaranteed to see the final x-optio-closed event.
        if self._dispatcher_task is not None:
            self._dispatcher_task.cancel()
            try:
                await self._dispatcher_task
            except asyncio.CancelledError:
                pass
            self._dispatcher_task = None
        while not self._event_queue.empty():
            obj = self._event_queue.get_nowait()
            for handler in list(self._event_handlers):
                await self._call_handler(handler, obj, "on_event")
```

- [ ] **Step 4: Run to green**

Run: `.venv/bin/python -m pytest packages/optio-codex/tests/test_conversation.py -q` → all pass.
Run: `.venv/bin/python -m pytest packages/optio-codex/tests/ -q` → whole suite green.

- [ ] **Step 5: Commit**

```bash
git add packages/optio-codex/src/optio_codex/conversation.py packages/optio-codex/tests/test_conversation.py
git commit -m "feat(optio-codex): CodexConversation app-server stdio client (Stage 6)

Ports the GrokConversation skeleton (two-tier fan-out, write lock,
queue-permissions-until-handler, _finish drain guarantee) onto the codex
app-server wire: JSONL JSON-RPC without the jsonrpc field, thread/start,
turn/start ACK + turn/completed turn-end, requestApproval accept/decline,
turn/interrupt, inline model pinning on the next turn, thread_id exposed
for the Plan-B snapshot seam. Wire facts pinned from the 0.142.5 live
probe + generated schemas in the module docstring."
```

---

### Task 3: `ConversationListener` port (+ unit tests)

Port grok's `conversation_listener.py` **~verbatim** — it is engine-agnostic by construction: it observes `conversation.on_event`, forwards `send`/`interrupt`/`request_model_change`, and correlates permissions by the JSON-RPC `id` found in `PermissionRequest.raw` (which `CodexConversation` fills with the whole `requestApproval` request object, exactly like grok). Only the module docstring changes. All invariants preserved: `BUFFER_MAXLEN=1000`, SSE `id:` = monotonic seq + `Last-Event-ID` resume, subscribe-before-replay + seq dedupe, `PING_INTERVAL_S=15`, `SHUTDOWN_TIMEOUT_S=2.0`, `_STOP` sentinel per subscriber on `stop()`, idempotent `stop()` resolving pending permissions with deny "session ending", port read back from `site._server.sockets[0]`, upload 413-per-part, download `ValueError("forbidden")→403` / `("too-large")→413`.

**Files:**
- Create: `packages/optio-codex/src/optio_codex/conversation_listener.py`
- Create: `packages/optio-codex/tests/test_conversation_listener.py`

**Interfaces:**
- Produces: `class ConversationListener(conversation, *, password, upload_writer=None, max_upload_bytes=10_000_000, download_reader=None, max_download_bytes=10_000_000)` with `async start(bind_iface) -> int` (port) and `async stop()`. HTTP surface: `GET /events` (SSE), `POST /send {text}`, `POST /interrupt {}`, `POST /model {model}`, `POST /permission {request_id, behavior, updated_input?, message?}`, `POST /upload` (multipart `file` parts), `GET /download?path=…`.
- Consumes: any `Conversation` with `on_event` / `on_permission_request` / `send` / `interrupt` / `request_model_change`; `optio_agents.conversation.{ConversationClosed, PermissionDecision}`; aiohttp.

- [ ] **Step 1: Write the failing tests**

Create `packages/optio-codex/tests/test_conversation_listener.py` — grok's `tests/test_conversation_listener.py` ported with codex event shapes (the listener passes events through untouched, so only the *fixture payloads* change):

```python
"""ConversationListener unit tests against a fake Codex Conversation.

Ported from optio-grok's test_conversation_listener.py. The listener is
engine-agnostic; permissions are correlated by the JSON-RPC ``id`` of the
``item/*/requestApproval`` server request (CodexConversation hands the whole
JSON-RPC object to the handler as ``PermissionRequest.raw``).
"""

import asyncio
import base64
import json

import aiohttp
import pytest

from optio_agents.conversation import ConversationClosed, PermissionDecision
from optio_codex.conversation_listener import ConversationListener


class FakeConversation:
    def __init__(self):
        self.handlers = []
        self.perm_handler = None
        self.sent = []
        self.interrupts = 0
        self.model_changes = []
        self.closed = False

    def on_event(self, h):
        self.handlers.append(h)
        return lambda: self.handlers.remove(h)

    def on_permission_request(self, h):
        self.perm_handler = h
        return lambda: None

    async def send(self, text):
        if self.closed:
            raise ConversationClosed("closed")
        self.sent.append(text)

    async def interrupt(self):
        if self.closed:
            raise ConversationClosed("closed")
        self.interrupts += 1

    def request_model_change(self, model):
        if self.closed:
            raise ConversationClosed("closed")
        self.model_changes.append(model)

    def fire(self, event):
        for h in list(self.handlers):
            h(event)


def _auth(pw):
    token = base64.b64encode(f"optio:{pw}".encode()).decode()
    return {"Authorization": f"Basic {token}"}


@pytest.fixture
async def listener():
    conv = FakeConversation()
    lst = ConversationListener(conv, password="pw")
    port = await lst.start("127.0.0.1")
    yield conv, lst, f"http://127.0.0.1:{port}"
    await lst.stop()


async def _read_events(resp, n, timeout=5):
    """Parse n SSE data frames from an open aiohttp response."""
    out = []
    buf = b""

    async def _go():
        nonlocal buf
        while len(out) < n:
            chunk = await resp.content.read(1024)
            if not chunk:
                break
            buf += chunk
            while b"\n\n" in buf:
                frame, buf = buf.split(b"\n\n", 1)
                data = [l[5:] for l in frame.split(b"\n") if l.startswith(b"data:")]
                if data:
                    out.append(json.loads(b"".join(data).strip()))

    await asyncio.wait_for(_go(), timeout)
    return out


async def test_replay_to_late_subscriber(listener):
    # A viewer that attaches AFTER events were fired still sees the buffered
    # history (the replay buffer), in order.
    conv, lst, url = listener
    conv.fire({"method": "item/agentMessage/delta", "params": {
        "threadId": "t1", "turnId": "turn-1", "itemId": "i1", "delta": "hi"}})
    conv.fire({"method": "turn/completed", "params": {
        "threadId": "t1", "turn": {"id": "turn-1", "status": "completed", "items": []}}})
    async with aiohttp.ClientSession() as s:
        async with s.get(f"{url}/events", headers=_auth("pw")) as resp:
            assert resp.status == 200
            replay = await _read_events(resp, 2)
            assert replay[0]["params"]["delta"] == "hi"
            assert replay[1]["method"] == "turn/completed"
            # Live tail continues after the replay.
            conv.fire({"method": "item/agentMessage/delta", "params": {
                "threadId": "t1", "turnId": "turn-2", "itemId": "i2",
                "delta": "more"}})
            live = await _read_events(resp, 1)
            assert live[0]["params"]["delta"] == "more"


async def test_last_event_id_resume(listener):
    conv, lst, url = listener
    conv.fire({"n": 1})    # seq 1
    conv.fire({"n": 2})    # seq 2
    async with aiohttp.ClientSession() as s:
        headers = {**_auth("pw"), "Last-Event-ID": "1"}
        async with s.get(f"{url}/events", headers=headers) as resp:
            events = await _read_events(resp, 1)
            assert events[0]["n"] == 2  # seq 1 skipped


async def test_send_forwards_to_conversation(listener):
    conv, lst, url = listener
    async with aiohttp.ClientSession() as s:
        r = await s.post(f"{url}/send", json={"text": "hi"}, headers=_auth("pw"))
        assert r.status == 200 and conv.sent == ["hi"]
        r = await s.post(f"{url}/interrupt", json={}, headers=_auth("pw"))
        assert r.status == 200 and conv.interrupts == 1
        conv.closed = True
        r = await s.post(f"{url}/send", json={"text": "x"}, headers=_auth("pw"))
        assert r.status == 409


async def test_model_route_forwards_to_conversation(listener):
    conv, lst, url = listener
    async with aiohttp.ClientSession() as s:
        r = await s.post(f"{url}/model", json={"model": "gpt-5.4-mini"}, headers=_auth("pw"))
        assert r.status == 200 and conv.model_changes == ["gpt-5.4-mini"]
        # bad payloads
        r = await s.post(f"{url}/model", json={}, headers=_auth("pw"))
        assert r.status == 400
        conv.closed = True
        r = await s.post(f"{url}/model", json={"model": "gpt-5.4-mini"}, headers=_auth("pw"))
        assert r.status == 409


async def test_permission_roundtrip_by_jsonrpc_id(listener):
    # PermissionRequest.raw is the full requestApproval JSON-RPC object; the
    # listener correlates the operator's answer by its `id`.
    conv, lst, url = listener

    class Req:
        raw = {"id": 99, "method": "item/commandExecution/requestApproval"}
        tool_name = "echo hi"
        input = {"command": "echo hi"}

    task = asyncio.create_task(conv.perm_handler(Req()))
    await asyncio.sleep(0.05)
    async with aiohttp.ClientSession() as s:
        r = await s.post(f"{url}/permission",
                         json={"request_id": "99", "behavior": "allow"},
                         headers=_auth("pw"))
        assert r.status == 200
        decision = await asyncio.wait_for(task, 2)
        assert isinstance(decision, PermissionDecision)
        assert decision.behavior == "allow"
        # A second answer for the resolved request is a 404.
        r = await s.post(f"{url}/permission",
                         json={"request_id": "99", "behavior": "deny"},
                         headers=_auth("pw"))
        assert r.status == 404
    # answered broadcast landed in the buffer
    assert any(e.get("type") == "x-optio-permission-answered"
               and e.get("request_id") == "99"
               for _, e in lst._buffer)


async def test_auth_rejected(listener):
    conv, lst, url = listener
    async with aiohttp.ClientSession() as s:
        r = await s.get(f"{url}/events", headers=_auth("WRONG"))
        assert r.status == 401
        r = await s.post(f"{url}/send", json={"text": "x"})
        assert r.status == 401


async def test_stop_returns_promptly_with_open_sse(listener):
    conv, lst, url = listener
    async with aiohttp.ClientSession() as s:
        async with s.get(f"{url}/events", headers=_auth("pw")) as resp:
            conv.fire({"method": "item/agentMessage/delta", "params": {
                "threadId": "t1", "turnId": "turn-1", "itemId": "i1",
                "delta": "x"}})
            await _read_events(resp, 1)  # handler is now in its live loop
            await asyncio.wait_for(lst.stop(), timeout=5)


async def test_stop_resolves_pending_permission_with_deny(listener):
    conv, lst, url = listener

    class Req:
        raw = {"id": 41, "method": "item/commandExecution/requestApproval"}
        tool_name = "echo hi"
        input = {}

    task = asyncio.create_task(conv.perm_handler(Req()))
    await asyncio.sleep(0.05)
    await lst.stop()
    decision = await asyncio.wait_for(task, 2)
    assert decision.behavior == "deny"
```

- [ ] **Step 2: Run to verify failure**

Run: `.venv/bin/python -m pytest packages/optio-codex/tests/test_conversation_listener.py -q`
Expected: FAIL — `ModuleNotFoundError: optio_codex.conversation_listener`.

- [ ] **Step 3: Implement `packages/optio-codex/src/optio_codex/conversation_listener.py`**

Copy `/home/csillag/deai/optio/packages/optio-grok/src/optio_grok/conversation_listener.py` **verbatim**, then apply ONLY these deltas (the class body — every handler, constant, and lifecycle method — stays byte-identical; that is the point of the port):

1. Replace the module docstring with:

```python
"""Per-task conversation listener — the opt-in dashboard gate for optio-codex.

Exposes one running CodexConversation over HTTP, reached through the optio-api
widget proxy (which injects the basic-auth credential):

  GET  /events     — SSE: replay buffer first, then live tail. SSE id: is a
                     monotonic seq; Last-Event-ID resumes without dupes.
  POST /send       — {text}                 -> conversation.send
  POST /interrupt  — {}                     -> conversation.interrupt
  POST /model      — {model}                -> conversation.request_model_change
                     (INLINE: pins the next turn/start's model — no restart)
  POST /upload     — multipart {file} parts -> upload_writer; returns
                     {ok, files:[{filename, path}]}
  GET  /download   — ?path=<relpath>        -> download_reader; returns the
                     bytes with Content-Disposition: attachment
  POST /permission — {request_id, behavior, updated_input?, message?}
                     resolves the pending requestApproval future.

Structurally mirrors optio-grok's ConversationListener (itself from
optio-claudecode's). Permissions are correlated by the JSON-RPC ``id`` of the
``item/commandExecution/requestApproval`` / ``item/fileChange/requestApproval``
server request — CodexConversation hands the whole JSON-RPC object to the
handler as ``PermissionRequest.raw``.

Projection principle: this listener only observes and forwards; attaching or
detaching viewers never influences task state.
"""
```

2. In `_on_permission_request`, update the inline comment to name the codex request (`# The raw requestApproval request already reached viewers via _on_event; …  CodexConversation stores the whole JSON-RPC request object as PermissionRequest.raw, so `id` is the correlation key.`). No code change.
3. No other changes — imports (`from optio_agents.conversation import ConversationClosed, PermissionDecision`), constants (`BUFFER_MAXLEN = 1000`, `PING_INTERVAL_S = 15.0`, `SHUTDOWN_TIMEOUT_S = 2.0`, `_STOP`), and all handlers/`start`/`stop` are engine-neutral already.

- [ ] **Step 4: Run to green**

Run: `.venv/bin/python -m pytest packages/optio-codex/tests/test_conversation_listener.py -q` → all pass.
Run: `.venv/bin/python -m pytest packages/optio-codex/tests/ -q` → whole suite green.

- [ ] **Step 5: Commit**

```bash
git add packages/optio-codex/src/optio_codex/conversation_listener.py packages/optio-codex/tests/test_conversation_listener.py
git commit -m "feat(optio-codex): conversation SSE listener (Stage 6)

Verbatim port of grok's engine-agnostic ConversationListener: SSE replay
buffer + Last-Event-ID, send/interrupt/model/permission/upload/download
endpoints, _STOP-sentinel bounded shutdown, permission correlation by the
requestApproval JSON-RPC id."
```

---

### Task 4: Config surface — conversation + frontend-parity fields with the validation matrix

Widen `CodexTaskConfig` for Stages 6–7, replicating grok's centralized `__post_init__` cross-validation matrix. `IframeMode` becomes `ConversationMode = Literal["iframe", "conversation"]` (rename, not alias — the export is hours old on this same branch, no external consumers). New fields per the ownership contract in Global Constraints. Codex-specific note: the existing `ask_for_approval`/`sandbox` fields keep governing the iframe launch flags; in conversation mode `sandbox` threads into `thread/start` and the approval policy is derived from `permission_gate` (never/on-request) — `ask_for_approval` is iframe-only, documented as such.

**Files:**
- Modify: `packages/optio-codex/src/optio_codex/types.py`
- Modify: `packages/optio-codex/src/optio_codex/__init__.py` (exports)
- Modify: `packages/optio-codex/tests/test_config.py`

**Interfaces:**
- Produces: `ConversationMode`, `ToolVerbosity` type aliases; `CodexTaskConfig` fields `mode: ConversationMode = "iframe"`, `permission_gate: bool = False`, `conversation_ui: bool = False`, `tool_verbosity: ToolVerbosity = "description-only"`, `default_model: str | None = None`, `show_model_selector: bool = False`, `show_file_upload: bool = False`, `max_upload_bytes: int = 10_000_000`, `file_download: bool = False`, `max_download_bytes: int = 10_000_000`.
- Validation matrix (grok parity): `mode ∈ {iframe, conversation}`; `host_protocol=False` ⇒ `mode="conversation"`; `permission_gate=True` ⇒ conversation; `conversation_ui=True` ⇒ conversation; `tool_verbosity ∈ {silent, description-only, verbose}`; each of `default_model`/`show_model_selector`/`show_file_upload`/`file_download` ⇒ conversation AND `conversation_ui=True`.
- Reconciliation: if Plans B/C already added their fields, keep them untouched and slot the new fields after the conversation comment blocks; the matrix additions are purely additive.

- [ ] **Step 1: Write the failing tests**

Append to `packages/optio-codex/tests/test_config.py`:

```python
import pytest

from optio_codex.types import CodexTaskConfig


def _cfg(**kw):
    base = dict(consumer_instructions="do things")
    base.update(kw)
    return CodexTaskConfig(**base)


def test_conversation_mode_accepted():
    cfg = _cfg(mode="conversation")
    assert cfg.mode == "conversation"


def test_host_protocol_false_now_legal_in_conversation_mode():
    cfg = _cfg(mode="conversation", host_protocol=False)
    assert cfg.host_protocol is False


def test_host_protocol_false_still_rejected_in_iframe_mode():
    with pytest.raises(ValueError, match="host_protocol"):
        _cfg(mode="iframe", host_protocol=False)


def test_permission_gate_requires_conversation_mode():
    with pytest.raises(ValueError, match="permission_gate"):
        _cfg(permission_gate=True)
    assert _cfg(mode="conversation", permission_gate=True).permission_gate


def test_conversation_ui_requires_conversation_mode():
    with pytest.raises(ValueError, match="conversation_ui"):
        _cfg(conversation_ui=True)
    assert _cfg(mode="conversation", conversation_ui=True).conversation_ui


def test_tool_verbosity_validated():
    with pytest.raises(ValueError, match="tool_verbosity"):
        _cfg(mode="conversation", tool_verbosity="loud")
    assert _cfg(mode="conversation", tool_verbosity="verbose").tool_verbosity == "verbose"


@pytest.mark.parametrize("field,value", [
    ("default_model", "gpt-5.5"),
    ("show_model_selector", True),
    ("show_file_upload", True),
    ("file_download", True),
])
def test_frontend_flags_require_conversation_ui(field, value):
    # Rejected without conversation_ui …
    with pytest.raises(ValueError, match=field):
        _cfg(mode="conversation", **{field: value})
    # … and accepted with it.
    cfg = _cfg(mode="conversation", conversation_ui=True, **{field: value})
    assert getattr(cfg, field) == value


def test_upload_download_byte_limits_default():
    cfg = _cfg(mode="conversation", conversation_ui=True,
               show_file_upload=True, file_download=True)
    assert cfg.max_upload_bytes == 10_000_000
    assert cfg.max_download_bytes == 10_000_000
```

- [ ] **Step 2: Run to verify failure**

Run: `.venv/bin/python -m pytest packages/optio-codex/tests/test_config.py -q`
Expected: FAIL — `mode="conversation"` rejected; unknown kwargs.

- [ ] **Step 3: Implement in `packages/optio-codex/src/optio_codex/types.py`**

Replace the `IframeMode` block with:

```python
# "iframe" = ttyd TUI in the browser. "conversation" = a headless
# ``codex app-server`` session; the task publishes a live CodexConversation
# via ctx.publish_result (Stage 6).
ConversationMode = Literal["iframe", "conversation"]
_VALID_MODES = {"iframe", "conversation"}

# Verbosity of tool-call rendering in the conversation widget
# (conversation_ui only). Mirrors optio-claudecode/grok; consumed by the
# dashboard reducer.
ToolVerbosity = Literal["silent", "description-only", "verbose"]
_VALID_TOOL_VERBOSITY = {"silent", "description-only", "verbose"}
```

Change the dataclass field `mode: IframeMode = "iframe"` to `mode: ConversationMode = "iframe"`, and add after the existing `host_protocol` field (keeping the docstring comments):

```python
    # --- conversation surface (Stage 6) ---------------------------------
    # Conversation mode only: route codex's item/*/requestApproval server
    # requests to the published conversation's on_permission_request handler
    # (the caller registers one). When False, the thread is started with
    # approvalPolicy="never" so tools run without prompting.
    permission_gate: bool = False
    # Opt-in dashboard conversation UI: the task starts a per-task listener
    # and publishes a live chat widget. Conversation mode only.
    conversation_ui: bool = False
    # How much tool-call detail the conversation widget renders; only
    # affects conversation_ui rendering.
    tool_verbosity: ToolVerbosity = "description-only"

    # --- conversation frontend parity (Stage 7) -------------------------
    # Model preselected in the widget's model picker. Requires
    # mode="conversation" and conversation_ui=True. Defaults to the live
    # thread model when unset. (config.model still drives thread/start;
    # this only controls the picker's initial value.)
    default_model: str | None = None
    # Show the model picker. Codex switches INLINE: the chosen model rides
    # the next turn/start and sticks — no process restart.
    show_model_selector: bool = False
    # Show the file-upload control. Uploaded bytes land under
    # <workdir>/uploads and are referenced to codex via a System: path line.
    show_file_upload: bool = False
    # Upper bound (bytes) on a single uploaded file; the listener rejects
    # larger with HTTP 413. Mirrored to the widget via widgetData.
    max_upload_bytes: int = 10_000_000
    # Offer download links for files codex marks with the optio-file:
    # sentinel. The listener serves GET /download confined to <workdir>.
    file_download: bool = False
    # Upper bound (bytes) on a single downloaded file (HTTP 413 above).
    max_download_bytes: int = 10_000_000
```

In `__post_init__`, replace the iframe/host_protocol validation block with the grok matrix (adapted names — `CodexTaskConfig`, and keep the existing `ask_for_approval`/`sandbox`/install-dir checks untouched):

```python
        if self.mode not in _VALID_MODES:
            raise ValueError(
                f"CodexTaskConfig.mode={self.mode!r} is not one of "
                f"{sorted(_VALID_MODES)}"
            )
        if self.mode == "iframe" and not self.host_protocol:
            raise ValueError(
                "CodexTaskConfig: host_protocol=False requires "
                "mode='conversation' (in iframe mode the optio.log keyword "
                "channel is the only completion signal)."
            )
        if self.permission_gate and self.mode != "conversation":
            raise ValueError(
                "CodexTaskConfig: permission_gate=True requires "
                "mode='conversation'."
            )
        if self.conversation_ui and self.mode != "conversation":
            raise ValueError(
                "CodexTaskConfig: conversation_ui=True requires "
                "mode='conversation'."
            )
        if self.tool_verbosity not in _VALID_TOOL_VERBOSITY:
            raise ValueError(
                f"CodexTaskConfig.tool_verbosity={self.tool_verbosity!r} "
                f"is not one of {sorted(_VALID_TOOL_VERBOSITY)}"
            )
        # Frontend-parity features are opt-in flags that only make sense
        # with the conversation UI wired (mirrors claudecode/grok).
        conv_ui = self.mode == "conversation" and self.conversation_ui
        if self.default_model is not None and not conv_ui:
            raise ValueError(
                "CodexTaskConfig: default_model requires mode='conversation' "
                "and conversation_ui=True."
            )
        if self.show_model_selector and not conv_ui:
            raise ValueError(
                "CodexTaskConfig: show_model_selector=True requires "
                "mode='conversation' and conversation_ui=True."
            )
        if self.show_file_upload and not conv_ui:
            raise ValueError(
                "CodexTaskConfig: show_file_upload=True requires "
                "mode='conversation' and conversation_ui=True."
            )
        if self.file_download and not conv_ui:
            raise ValueError(
                "CodexTaskConfig: file_download=True requires "
                "mode='conversation' and conversation_ui=True."
            )
```

Update `__all__` in `types.py` and the imports/`__all__` in `packages/optio-codex/src/optio_codex/__init__.py`: replace `IframeMode` with `ConversationMode`, add `ToolVerbosity`. Also amend the `ask_for_approval` field comment in `types.py` to state it is **iframe-only** (conversation mode derives the thread `approvalPolicy` from `permission_gate`).

- [ ] **Step 4: Run to green**

Run: `.venv/bin/python -m pytest packages/optio-codex/tests/ -q` → whole suite green (existing iframe validation tests must still pass unchanged).

- [ ] **Step 5: Commit**

```bash
git add packages/optio-codex/src/optio_codex/types.py packages/optio-codex/src/optio_codex/__init__.py packages/optio-codex/tests/test_config.py
git commit -m "feat(optio-codex): conversation + frontend-parity config surface (Stages 6-7)

mode gains 'conversation' (IframeMode -> ConversationMode);
permission_gate / conversation_ui / tool_verbosity / default_model /
show_model_selector / show_file_upload / max_upload_bytes /
file_download / max_download_bytes with grok's centralized
__post_init__ cross-validation matrix."
```

---

### Task 5: Conversation session mode — `session.py` body + fake app-server responder + real-engine tests

Port grok's `_conversation_body` (`optio-grok/session.py:293-443`) and its teardown deltas: launch `codex app-server` via `host.launch_subprocess` with the isolation env, `merge_stderr=False` (load-bearing — keeps codex diagnostics off the JSONL stdout), `stdin=True`; attach/reader/bootstrap (cancel reader on bootstrap failure); `ctx.publish_result(conversation)`; optional `ConversationListener` + widget publication when `conversation_ui`; auto-start kickoff as the first turn; wait race `proc_wait` vs `close_requested.wait()`; clean-close DONE park under `host_protocol=True`; unexpected exit → `RuntimeError`. Test vehicle: `fake_codex.py` gains an app-server stdio responder mode (structural port of fake_grok's ACP responder section).

**Files:**
- Modify: `packages/optio-codex/tests/fake_codex.py` (app-server responder mode)
- Modify: `packages/optio-codex/src/optio_codex/session.py`
- Create: `packages/optio-codex/tests/test_session_conversation.py`

**Interfaces:**
- `fake_codex.py`: argv containing `app-server` → `_run_app_server() -> int` (detected before argparse). Env knobs: `FAKE_CODEX_EXIT_AFTER=N` (exit 7 after N turns — crash injection).
- `session.py`: `_conversation_body(host, hook_ctx)` selected via `body = _conversation_body if config.mode == "conversation" else _codex_body`; `create_codex_task` ui_widget mapping `conversation_ui→"conversation"`, bare conversation→`None`, else `"iframe"`; teardown gains listener-stop + subprocess-terminate steps.
- Consumes: `CodexConversation` (Task 2), `ConversationListener` (Task 3), `parse_model_list` (Task 1), `host_actions._isolation_env`, `optio_host.host.proc_wait`, `optio_core.models.BasicAuth`.
- **Reconciliation (B/C landed?):** if Plan B landed, gate the kickoff on `not resuming` and pass `resume_thread_id` from the restored snapshot's `sessionId` into the `CodexConversation` constructor (grok parity; the seam is already exposed). If Plan C landed, the conversation-subprocess terminate step goes BEFORE the cred-watcher-cancel/save-back/lease steps in the `finally` (grok ordering: listener → subprocess/iframe teardown → watcher-cancel → save-back → lease release → captures). If neither landed, the `finally` additions simply precede the existing iframe teardown block, and `supports_resume=False` stays hardcoded on `TaskInstance` until Plan B changes it.

- [ ] **Step 1: Write the failing tests**

Create `packages/optio-codex/tests/test_session_conversation.py`:

```python
"""End-to-end conversation-mode session tests (local host, fake app-server).

Each test bootstraps a real optio engine (Mongo via Docker), defines the task
via ``adhoc_define``, and obtains the live ``CodexConversation`` through
``launch_and_await_result``. The shim fixtures point the session at
``codex-shim.sh`` → ``fake_codex.py``, which runs its app-server stdio
responder when argv contains ``app-server`` (no tmux/ttyd in this mode).
"""

from __future__ import annotations

import asyncio
import pathlib
import time as _time

import pytest

from optio_core.lifecycle import Optio

from optio_codex import CodexTaskConfig, create_codex_task


_TERMINAL = {"done", "failed", "cancelled"}


async def _make_optio(mongo_db, prefix: str) -> Optio:
    optio = Optio()
    await optio.init(mongo_db=mongo_db, prefix=prefix)
    return optio


async def _wait_terminal(optio: Optio, process_id: str, timeout: float = 30.0) -> dict:
    end = _time.monotonic() + timeout
    while _time.monotonic() < end:
        proc = await optio.get_process(process_id)
        if proc is not None and proc["status"]["state"] in _TERMINAL:
            return proc
        await asyncio.sleep(0.05)
    raise AssertionError(f"{process_id} did not reach terminal state in {timeout}s")


async def _wait_for(predicate, timeout: float = 10.0) -> None:
    end = _time.monotonic() + timeout
    while _time.monotonic() < end:
        if predicate():
            return
        await asyncio.sleep(0.02)
    raise AssertionError(f"condition not met within {timeout}s")


def _conversation_config(shim_install_dir: pathlib.Path, **kw) -> CodexTaskConfig:
    base = dict(
        consumer_instructions="Converse with the test.",
        mode="conversation",
        codex_install_dir=str(shim_install_dir),
        ttyd_install_dir=str(shim_install_dir),
        auto_start=False,
    )
    # Reconciliation: pass supports_resume=False here IF Plan B has added the
    # field (keeps these tests snapshot-free); omit otherwise.
    base.update(kw)
    return CodexTaskConfig(**base)


@pytest.mark.asyncio
async def test_publish_send_receive_and_pending(shim_install_dir, task_root, mongo_db):
    """launch_and_await_result hands out the live conversation; one full
    send → reply turn works and is_pending flips around it."""
    optio = await _make_optio(mongo_db, "cxconv1")
    try:
        task = create_codex_task(
            process_id="cx-conv-roundtrip",
            name="Conversation roundtrip",
            config=_conversation_config(shim_install_dir),
        )
        await optio.adhoc_define(task)
        conv = await optio.launch_and_await_result(
            "cx-conv-roundtrip", session_id=None, timeout=60,
        )
        assert optio.get_published_result("cx-conv-roundtrip") is conv
        assert conv.thread_id  # Plan B's snapshot sessionId seam

        msgs: asyncio.Queue[str] = asyncio.Queue()
        conv.on_message(msgs.put_nowait)
        assert not conv.is_pending()
        await conv.send("hello")
        assert conv.is_pending()
        reply = await asyncio.wait_for(msgs.get(), 10)
        assert reply == "reply-1"
        await _wait_for(lambda: not conv.is_pending())

        await conv.close()
        proc = await _wait_terminal(optio, "cx-conv-roundtrip")
        assert proc["status"]["state"] == "done"
        await _wait_for(lambda: conv.closed)
    finally:
        await optio.shutdown(grace_seconds=1.0)


@pytest.mark.asyncio
async def test_permission_gate_denies_when_configured(shim_install_dir, task_root, mongo_db):
    """permission_gate=True publishes a conversation whose caller-registered
    handler answers requestApproval; a deny yields 'tool-denied'."""
    optio = await _make_optio(mongo_db, "cxconv2")
    try:
        task = create_codex_task(
            process_id="cx-conv-perm",
            name="Conversation permission",
            config=_conversation_config(shim_install_dir, permission_gate=True),
        )
        await optio.adhoc_define(task)
        conv = await optio.launch_and_await_result(
            "cx-conv-perm", session_id=None, timeout=60,
        )

        from optio_agents.conversation import PermissionDecision
        seen: dict = {}

        async def deny_handler(req):
            seen["tool"] = req.tool_name
            return PermissionDecision(behavior="deny", message="not allowed")

        conv.on_permission_request(deny_handler)

        msgs: asyncio.Queue[str] = asyncio.Queue()
        conv.on_message(msgs.put_nowait)
        await conv.send("please use a TOOL to do it")
        reply = await asyncio.wait_for(msgs.get(), 10)
        assert reply == "tool-denied"
        assert seen["tool"] == "echo hi"  # the handler saw the command

        await conv.close()
        proc = await _wait_terminal(optio, "cx-conv-perm")
        assert proc["status"]["state"] == "done"
    finally:
        await optio.shutdown(grace_seconds=1.0)


@pytest.mark.asyncio
async def test_unexpected_exit_fails_task(shim_install_dir, task_root, mongo_db, monkeypatch):
    """The fake exits (7) after its first prompt turn → task 'failed' with the
    'exited unexpectedly' message; the conversation flips closed."""
    monkeypatch.setenv("FAKE_CODEX_EXIT_AFTER", "1")
    optio = await _make_optio(mongo_db, "cxconv3")
    try:
        task = create_codex_task(
            process_id="cx-conv-dies",
            name="Conversation dies",
            config=_conversation_config(shim_install_dir),
        )
        await optio.adhoc_define(task)
        conv = await optio.launch_and_await_result(
            "cx-conv-dies", session_id=None, timeout=60,
        )
        msgs: asyncio.Queue[str] = asyncio.Queue()
        conv.on_message(msgs.put_nowait)
        await conv.send("trigger the last turn")
        assert await asyncio.wait_for(msgs.get(), 10) == "reply-1"

        proc = await _wait_terminal(optio, "cx-conv-dies")
        assert proc["status"]["state"] == "failed"
        assert "exited unexpectedly" in (proc["status"]["error"] or "")
        await _wait_for(lambda: conv.closed)
    finally:
        await optio.shutdown(grace_seconds=1.0)


@pytest.mark.asyncio
async def test_auto_start_sends_kickoff_first(shim_install_dir, task_root, mongo_db):
    """auto_start=True → the body sends the kickoff prompt first, so the
    caller's first send returns 'reply-2' (kickoff was turn #1)."""
    optio = await _make_optio(mongo_db, "cxconv4")
    try:
        task = create_codex_task(
            process_id="cx-conv-kickoff",
            name="Conversation kickoff",
            config=_conversation_config(shim_install_dir, auto_start=True),
        )
        await optio.adhoc_define(task)
        conv = await optio.launch_and_await_result(
            "cx-conv-kickoff", session_id=None, timeout=60,
        )
        msgs: asyncio.Queue[str] = asyncio.Queue()
        conv.on_message(msgs.put_nowait)
        await conv.send("ping")
        seen: list[str] = []
        while "reply-2" not in seen:
            seen.append(await asyncio.wait_for(msgs.get(), 10))

        await conv.close()
        proc = await _wait_terminal(optio, "cx-conv-kickoff")
        assert proc["status"]["state"] == "done"
    finally:
        await optio.shutdown(grace_seconds=1.0)


@pytest.mark.asyncio
async def test_conversation_ui_publishes_widget(shim_install_dir, task_root, mongo_db):
    """conversation_ui=True starts the listener and publishes protocol=codex
    widget data with the model list from the fake's model/list."""
    optio = await _make_optio(mongo_db, "cxconv5")
    try:
        task = create_codex_task(
            process_id="cx-conv-ui",
            name="Conversation UI",
            config=_conversation_config(
                shim_install_dir, conversation_ui=True,
                show_model_selector=True, tool_verbosity="verbose",
            ),
        )
        assert task.ui_widget == "conversation"
        await optio.adhoc_define(task)
        conv = await optio.launch_and_await_result(
            "cx-conv-ui", session_id=None, timeout=60,
        )

        async def _widget_data():
            proc = await optio.get_process("cx-conv-ui")
            return (proc or {}).get("widgetData") or {}

        end = _time.monotonic() + 10
        wd: dict = {}
        while _time.monotonic() < end:
            wd = await _widget_data()
            if wd.get("protocol") == "codex":
                break
            await asyncio.sleep(0.05)
        assert wd.get("protocol") == "codex"
        assert wd.get("toolVerbosity") == "verbose"
        assert wd.get("showModelSelector") is True
        assert [m["id"] for m in wd.get("models", [])] == ["gpt-5.5", "gpt-5.4-mini"]
        assert wd.get("currentModel") == "gpt-5.5"

        await conv.close()
        await _wait_terminal(optio, "cx-conv-ui")
    finally:
        await optio.shutdown(grace_seconds=1.0)


def test_ui_widget_per_mode():
    """Conversation tasks carry no widget unless conversation_ui; iframe
    tasks keep 'iframe'."""
    conv_task = create_codex_task(
        process_id="cx-widget-conv",
        name="Widget conv",
        config=CodexTaskConfig(consumer_instructions="x", mode="conversation"),
    )
    assert conv_task.ui_widget is None

    ui_task = create_codex_task(
        process_id="cx-widget-conv-ui",
        name="Widget conv ui",
        config=CodexTaskConfig(
            consumer_instructions="x", mode="conversation", conversation_ui=True,
        ),
    )
    assert ui_task.ui_widget == "conversation"

    iframe_task = create_codex_task(
        process_id="cx-widget-iframe",
        name="Widget iframe",
        config=CodexTaskConfig(consumer_instructions="x"),
    )
    assert iframe_task.ui_widget == "iframe"
```

Note on `test_conversation_ui_publishes_widget`: it assumes `optio.get_process` surfaces the stored `widgetData` document field (written by `ctx.set_widget_data`). Verify the field name against `optio_core` before running (`grep -rn "widgetData" packages/optio-core/src/ | head`); if the engine stores it under a different key or a sub-document, adjust the accessor in the test — the assertion targets (protocol/models/currentModel) stay.

- [ ] **Step 2: Run to verify failure**

Run: `.venv/bin/python -m pytest packages/optio-codex/tests/test_session_conversation.py -q`
Expected: FAIL — `CodexTaskConfig(mode="conversation")` is accepted (Task 4) but the session still runs the iframe body and the fake has no app-server mode; the roundtrip test times out or errors on launch. (If failures are slow timeouts, `-x` on the first test is enough evidence.)

- [ ] **Step 3a: Implement the fake app-server responder**

In `packages/optio-codex/tests/fake_codex.py`, add near the top (after the imports — also add `import json` and `import sys` to the import block):

```python
def _as_send(obj: dict) -> None:
    # The real wire omits the "jsonrpc" field (probed against 0.142.5).
    sys.stdout.write(json.dumps(obj) + "\n")
    sys.stdout.flush()


def _notify(method: str, params: dict) -> None:
    _as_send({"method": method, "params": params})


def _run_app_server() -> int:
    """Fake ``codex app-server`` — a minimal JSONL JSON-RPC responder.

    Implements the wire pinned by the Stage-6 probe/schemas:
      * ``initialize``/``initialized`` + ``account/read`` + ``model/list`` +
        ``thread/start``/``thread/resume`` handshake.
      * ``turn/start`` → ACK, ``turn/started``, an agentMessage item with two
        ``item/agentMessage/delta`` halves + ``item/completed``, then
        ``turn/completed``. Replies are numbered ``reply-N`` per turn (so an
        auto-start kickoff shifts the caller's first message to ``reply-2``).
      * Permission scenario: a turn whose text contains ``TOOL`` emits a
        commandExecution ``item/started`` + an
        ``item/commandExecution/requestApproval`` REQUEST, blocks for the
        client's answer, then reports ``tool-ran`` (decision accept*) or
        ``tool-denied`` (decline/cancel).
      * ``turn/interrupt`` → {} ACK + ``turn/completed`` status interrupted.

    ``FAKE_CODEX_EXIT_AFTER=N`` makes the process exit non-zero (7) after N
    turns, modelling an unexpected crash for the session-failure test.
    """
    thread_id = "fake-codex-thread"
    exit_after = int(os.environ.get("FAKE_CODEX_EXIT_AFTER", "0") or "0")
    turn = 0
    next_req_id = 1000
    while True:
        line = sys.stdin.readline()
        if not line:
            return 0
        line = line.strip()
        if not line:
            continue
        try:
            msg = json.loads(line)
        except ValueError:
            continue
        method = msg.get("method")
        mid = msg.get("id")
        if method == "initialize":
            _as_send({"id": mid, "result": {
                "userAgent": "codex/0.142.5-fake",
                "codexHome": os.environ.get("CODEX_HOME", ""),
                "platformFamily": "fake", "platformOs": "fake"}})
        elif method == "initialized":
            continue
        elif method == "account/read":
            _as_send({"id": mid, "result": {
                "account": {"type": "apikey"}, "requiresOpenaiAuth": False}})
        elif method == "model/list":
            _as_send({"id": mid, "result": {"data": [
                {"id": "gpt-5.5", "displayName": "GPT-5.5", "description": "",
                 "hidden": False, "isDefault": True, "model": "gpt-5.5",
                 "defaultReasoningEffort": "medium",
                 "supportedReasoningEfforts": []},
                {"id": "gpt-5.4-mini", "displayName": "GPT-5.4 Mini",
                 "description": "", "hidden": False, "isDefault": False,
                 "model": "gpt-5.4-mini", "defaultReasoningEffort": "medium",
                 "supportedReasoningEfforts": []},
            ], "nextCursor": None}})
        elif method in ("thread/start", "thread/resume"):
            params = msg.get("params") or {}
            if method == "thread/resume" and params.get("threadId"):
                thread_id = params["threadId"]
            _as_send({"id": mid, "result": {
                "thread": {"id": thread_id},
                "model": params.get("model") or "gpt-5.5"}})
            _notify("thread/started", {"thread": {"id": thread_id}})
        elif method == "turn/start":
            turn += 1
            turn_id = f"turn-{turn}"
            params = msg.get("params") or {}
            text = " ".join(
                p.get("text", "") for p in (params.get("input") or [])
                if isinstance(p, dict)
            )
            _as_send({"id": mid, "result": {"turn": {
                "id": turn_id, "status": "inProgress", "items": []}}})
            _notify("turn/started", {"threadId": thread_id, "turn": {
                "id": turn_id, "status": "inProgress", "items": []}})
            if "TOOL" in text:
                item = {"type": "commandExecution", "id": f"item-{turn}-tool",
                        "command": "echo hi", "cwd": "/w",
                        "status": "inProgress"}
                _notify("item/started", {"threadId": thread_id,
                                         "turnId": turn_id, "item": item,
                                         "startedAtMs": 0})
                next_req_id += 1
                _as_send({"id": next_req_id,
                          "method": "item/commandExecution/requestApproval",
                          "params": {"threadId": thread_id, "turnId": turn_id,
                                     "itemId": item["id"], "command": "echo hi",
                                     "cwd": "/w", "reason": None,
                                     "startedAtMs": 0}})
                # Block for the client's approval answer (next stdin line).
                answer_line = sys.stdin.readline()
                decision = None
                if answer_line.strip():
                    try:
                        decision = (json.loads(answer_line).get("result")
                                    or {}).get("decision")
                    except ValueError:
                        decision = None
                allowed = decision in ("accept", "acceptForSession")
                _notify("item/completed", {
                    "threadId": thread_id, "turnId": turn_id,
                    "item": dict(item, status="completed" if allowed else "declined"),
                    "completedAtMs": 0})
                reply = "tool-ran" if allowed else "tool-denied"
            else:
                reply = f"reply-{turn}"
            msg_item_id = f"item-{turn}-msg"
            _notify("item/started", {"threadId": thread_id, "turnId": turn_id,
                                     "item": {"type": "agentMessage",
                                              "id": msg_item_id, "text": ""},
                                     "startedAtMs": 0})
            half = max(1, len(reply) // 2)
            for piece in (reply[:half], reply[half:]):
                if piece:
                    _notify("item/agentMessage/delta", {
                        "threadId": thread_id, "turnId": turn_id,
                        "itemId": msg_item_id, "delta": piece})
            _notify("item/completed", {"threadId": thread_id,
                                       "turnId": turn_id,
                                       "item": {"type": "agentMessage",
                                                "id": msg_item_id,
                                                "text": reply},
                                       "completedAtMs": 0})
            _notify("turn/completed", {"threadId": thread_id, "turn": {
                "id": turn_id, "status": "completed", "items": []}})
            if exit_after and turn >= exit_after:
                return 7
        elif method == "turn/interrupt":
            _as_send({"id": mid, "result": {}})
            _notify("turn/completed", {"threadId": thread_id, "turn": {
                "id": (msg.get("params") or {}).get("turnId") or "turn-0",
                "status": "interrupted", "items": []}})
        elif mid is not None:
            _as_send({"id": mid, "error": {
                "code": -32601,
                "message": f"fake codex: unknown method {method}"}})
```

And at the top of `main()`, before argparse:

```python
    # app-server conversation mode: `codex app-server`. Detected before
    # argparse so the positional doesn't trip the option parser.
    if "app-server" in sys.argv[1:]:
        return _run_app_server()
```

(If `main()` doesn't already import `sys`/`os` at module level after Plans A–C evolutions, hoist them.)

- [ ] **Step 3b: Implement the conversation body in `session.py`**

Add imports: `import os`, `import secrets`, `import re`, `import mimetypes`, `import shlex`; `from optio_core.models import BasicAuth`; `from optio_host.host import proc_wait`; `from optio_codex import models as codex_models`; `from optio_codex.conversation import CodexConversation`; `from optio_codex.conversation_listener import ConversationListener`.

Inside `run_codex_session`, after `await host.connect()` (and after any Plan-B/C prepare state), construct the conversation and the listener slot:

```python
    conversation: CodexConversation | None = None
    if config.mode == "conversation":
        conversation = CodexConversation(
            cwd=host.workdir,
            permission_gate=config.permission_gate,
            model=config.model,
            sandbox=config.sandbox,
            # Plan-B reconciliation: pass resume_thread_id=<restored sessionId>
            # here when resuming (thread/resume instead of thread/start).
        )
    conv_listener: ConversationListener | None = None
```

Add the body (sibling of `_codex_body`):

```python
    async def _conversation_body(host: Host, hook_ctx: HookContext) -> None:
        nonlocal launched_handle, conv_listener

        # Launch `codex app-server` directly (no tmux/ttyd). Model, sandbox
        # and approval policy travel in thread/start params — no CLI flags.
        # merge_stderr=False keeps codex diagnostics off the JSONL stdout.
        argv = [codex_path, "app-server"]
        cmd = " ".join(shlex.quote(a) for a in argv)
        env = {
            **host_actions._isolation_env(host.workdir),
            **(config.env or {}),
            **(hook_ctx.browser_launch_env or {}),
        }
        ctx.report_progress(None, "Launching Codex (conversation)…")
        handle = await host.launch_subprocess(
            cmd, env=env, cwd=host.workdir,
            env_remove=config.scrub_env, stdin=True, merge_stderr=False,
        )
        launched_handle = handle
        conversation.attach(handle)
        reader_task = asyncio.create_task(conversation.run_reader())
        try:
            await conversation.bootstrap()
        except Exception:
            reader_task.cancel()
            raise

        ctx.publish_result(conversation)
        ctx.report_progress(None, "Codex conversation is live")

        # Opt-in dashboard chat widget: per-task SSE listener over the
        # published conversation, reached via the widget proxy (which
        # injects the basic-auth credential).
        if config.conversation_ui:
            listener_password = secrets.token_urlsafe(32)
            bind_addr = os.environ.get("OPTIO_WIDGET_TUNNEL_BIND", "127.0.0.1")
            upstream_host = os.environ.get("OPTIO_WIDGET_TUNNEL_HOST", "127.0.0.1")
            # File upload: bytes land under <workdir>/uploads with a
            # sanitized name; the view injects a System: path reference so
            # codex reads them with its own tools.
            uploads_dir = f"{host.workdir}/uploads"

            async def _write_upload(name: str, data: bytes) -> str:
                safe = re.sub(
                    r"[^A-Za-z0-9._-]", "_", (name.split("/")[-1] or "file"),
                )[:200] or "file"
                await host.put_file_to_host(data, f"{uploads_dir}/{safe}")
                return f"uploads/{safe}"

            # File download: serve workdir-confined bytes for the
            # optio-file: sentinel links codex emits. realpath guards
            # against ../ escapes.
            async def _read_download(relpath: str) -> tuple[bytes, str]:
                workdir = host.workdir.rstrip("/")
                real = os.path.realpath(os.path.join(workdir, relpath))
                if real != workdir and not real.startswith(workdir + os.sep):
                    raise ValueError("forbidden")       # outside the workdir
                data = await host.fetch_bytes_from_host(real)
                if len(data) > config.max_download_bytes:
                    raise ValueError("too-large")
                mime = mimetypes.guess_type(real)[0] or "application/octet-stream"
                return data, mime

            conv_listener = ConversationListener(
                conversation, password=listener_password,
                upload_writer=_write_upload,
                max_upload_bytes=config.max_upload_bytes,
                download_reader=_read_download,
                max_download_bytes=config.max_download_bytes,
            )
            # In-process aiohttp app: binds directly on the widget-tunnel
            # interface, no host tunnel needed.
            listener_port = await conv_listener.start(bind_addr)
            await ctx.set_widget_upstream(
                f"http://{upstream_host}:{listener_port}",
                inner_auth=BasicAuth(username="optio", password=listener_password),
            )
            # Model picker options come from the model/list captured at
            # bootstrap (authed, exact ids), else the static fallback.
            model_list = codex_models.parse_model_list(conversation.model_list)
            current_model = (
                config.default_model
                or conversation.current_model_id
                or model_list.get("default")
            )
            await ctx.set_widget_data({
                "protocol": "codex",
                "toolVerbosity": config.tool_verbosity,
                "showModelSelector": config.show_model_selector,
                "models": model_list["models"],
                "currentModel": current_model,
                "showFileUpload": config.show_file_upload,
                "maxUploadBytes": config.max_upload_bytes,
                "fileDownload": config.file_download,
                "maxDownloadBytes": config.max_download_bytes,
            })
            ctx.report_progress(None, "Conversation UI is live")

        # Kickoff prompt as the first turn (headless: no positional prompt
        # path). Plan-B reconciliation: gate on `and not resuming`.
        if config.auto_start:
            await conversation.send(host_actions.AUTO_START_PROMPT)

        try:
            while True:
                wait_task = asyncio.create_task(proc_wait(handle))
                close_task = asyncio.create_task(
                    conversation.close_requested.wait())
                done, _ = await asyncio.wait(
                    {wait_task, close_task},
                    return_when=asyncio.FIRST_COMPLETED,
                )
                for t in (wait_task, close_task):
                    if t not in done:
                        t.cancel()

                if close_task in done and wait_task not in done:
                    # Caller asked to close: cooperative clean end.
                    if config.host_protocol:
                        # The keyword driver treats a body return without
                        # DONE as a premature exit; a caller-requested close
                        # IS the clean end, so emit DONE ourselves and park
                        # until the driver observes it and cancels this body.
                        log_path = f"{host.workdir}/optio.log"
                        await host.run_command(
                            f"echo DONE >> {shlex.quote(log_path)}"
                        )
                        await asyncio.Event().wait()  # cancelled by the driver
                    break

                # Subprocess exited on its own.
                try:
                    rc = wait_task.result()
                except Exception:
                    rc = None
                if (
                    not conversation.close_requested.is_set()
                    and ctx.should_continue()
                ):
                    raise RuntimeError(f"codex exited unexpectedly (exit {rc})")
                break
        finally:
            reader_task.cancel()
            try:
                await reader_task
            except asyncio.CancelledError:
                pass
```

Route the body and the agent sender:

```python
    async def _agent_sender(message: str) -> None:
        if config.mode == "conversation":
            await conversation.send(message)
            return
        await host_actions.send_text_to_codex(
            host, tmux_path, tmux_socket, tmux_session, message,
        )

    body = _conversation_body if config.mode == "conversation" else _codex_body
```

(pass `body=body` to `run_log_protocol_session`). In `_prepare`, skip `ensure_ttyd_installed` when `config.mode == "conversation"` (no ttyd in this mode; keep `ttyd_path = None`).

In the `finally` block, BEFORE the existing iframe `teardown_session_tree` step (and before any Plan-C cred-watcher/save-back/lease steps), insert:

```python
        # Stop the conversation listener first so its long-lived SSE loops
        # are woken (bounded shutdown) before the subprocess teardown below.
        if conv_listener is not None:
            try:
                await conv_listener.stop()
            except Exception:
                _LOG.exception("conversation listener cleanup failed")
        # Conversation mode has no tmux/ttyd tree — terminate the app-server
        # subprocess directly. Its EOF drives the conversation to closed.
        if config.mode == "conversation" and launched_handle is not None:
            try:
                await host.terminate_subprocess(
                    launched_handle, aggressive=cancelled)
            except Exception:
                _LOG.exception("terminate codex conversation subprocess failed")
```

And in `create_codex_task`, replace `ui_widget="iframe"` with the grok mapping:

```python
    # iframe → the ttyd TUI widget. Conversation mode carries the live chat
    # widget only when conversation_ui is on; otherwise no widget (the
    # published Conversation is driven programmatically).
    if config.conversation_ui:
        ui_widget: str | None = "conversation"
    elif config.mode == "conversation":
        ui_widget = None
    else:
        ui_widget = "iframe"
```

(thread `ui_widget=ui_widget` into the `TaskInstance`).

- [ ] **Step 4: Run to green**

Run: `.venv/bin/python -m pytest packages/optio-codex/tests/test_session_conversation.py -q` → all pass.
Run: `.venv/bin/python -m pytest packages/optio-codex/tests/ -q` → whole suite green (iframe tests unaffected).

- [ ] **Step 5: Commit**

```bash
git add packages/optio-codex/src/optio_codex/session.py packages/optio-codex/tests/fake_codex.py packages/optio-codex/tests/test_session_conversation.py
git commit -m "feat(optio-codex): conversation session mode publishing a live Conversation (Stage 6)

_conversation_body launches codex app-server (isolation env,
merge_stderr=False, stdin), attach/reader/bootstrap, publish_result,
opt-in SSE listener + protocol=codex widget data, auto-start kickoff,
clean-close DONE park, crash -> RuntimeError; fake_codex gains the
app-server stdio responder (handshake, scripted turns, blocking TOOL
approval, FAKE_CODEX_EXIT_AFTER crash injection)."
```

---

### Task 6: File upload/download tests + downloadables prompt wiring

The upload/download *endpoints* shipped with the listener (Task 3) and the session plumbing (Task 5). This task ports grok's dedicated test files (listener-level HTTP units + config validation) and wires the `optio-file:` downloadables instruction block into AGENTS.md when `file_download=True` — the prompt side of the feature. The `System:`-explainer for `host_protocol=False` is already in place from Plan A; this task also adds the conversation-mode composition check for it.

**Files:**
- Modify: `packages/optio-codex/src/optio_codex/prompt.py` (add `file_download` kwarg)
- Modify: `packages/optio-codex/src/optio_codex/session.py` (thread `file_download` into the `_prepare` compose call)
- Create: `packages/optio-codex/tests/test_file_upload.py`
- Create: `packages/optio-codex/tests/test_file_download.py`

**Interfaces:**
- Produces: `compose_agents_md(consumer_instructions, *, documentation=None, host_protocol=True, file_download=False, **existing_kwargs) -> str` — when `file_download=True`, append `optio_agents.prompt.downloadables_block(comparative=host_protocol)` to the consumer instructions before delegation (grok's composition rule; SSOT block imported, never copied).
- Reconciliation: Plan B may have added `resume_section`-related kwargs to this same signature — the `file_download` kwarg is additive and orthogonal; do not touch B's params.

- [ ] **Step 1: Write the failing tests**

Create `packages/optio-codex/tests/test_file_upload.py` — grok's file ported (imports/`GrokTaskConfig`→`CodexTaskConfig`, `optio_grok`→`optio_codex`; content otherwise identical):

```python
"""Conversation-mode file-upload tests (Stage 7).

Two file-disjoint units that don't need a live codex:
  * the listener's POST /upload endpoint, driven over real HTTP with a fake
    upload_writer (no Host) — mirrors test_conversation_listener's harness;
  * CodexTaskConfig.show_file_upload validation.

The end-to-end (bytes landing in <workdir>/uploads, the agent reading them via
the System: reference) is verified manually.
"""

import base64

import aiohttp
import pytest

from optio_codex.conversation_listener import ConversationListener
from optio_codex.types import CodexTaskConfig


class FakeConversation:
    def on_event(self, h):
        return lambda: None

    def on_permission_request(self, h):
        return lambda: None

    async def send(self, text):
        pass

    async def interrupt(self):
        pass


def _auth(pw):
    token = base64.b64encode(f"optio:{pw}".encode()).decode()
    return {"Authorization": f"Basic {token}"}


def _cfg(**kw):
    base = dict(consumer_instructions="do things")
    base.update(kw)
    return CodexTaskConfig(**base)


@pytest.fixture
async def upload_listener():
    conv = FakeConversation()
    calls: list[tuple[str, bytes]] = []

    async def writer(name: str, data: bytes) -> str:
        calls.append((name, data))
        return f"uploads/{name}"

    lst = ConversationListener(
        conv, password="pw", upload_writer=writer, max_upload_bytes=16
    )
    port = await lst.start("127.0.0.1")
    yield calls, f"http://127.0.0.1:{port}"
    await lst.stop()


async def test_upload_calls_writer_and_returns_path(upload_listener):
    calls, url = upload_listener
    form = aiohttp.FormData()
    form.add_field("file", b"hello", filename="note.txt", content_type="text/plain")
    async with aiohttp.ClientSession() as s:
        r = await s.post(f"{url}/upload", data=form, headers=_auth("pw"))
        assert r.status == 200
        body = await r.json()
    assert body["ok"] is True
    assert body["files"] == [{"filename": "note.txt", "path": "uploads/note.txt"}]
    assert calls == [("note.txt", b"hello")]


async def test_upload_too_large_returns_413(upload_listener):
    _calls, url = upload_listener
    form = aiohttp.FormData()
    form.add_field("file", b"x" * 100, filename="big.bin",
                   content_type="application/octet-stream")
    async with aiohttp.ClientSession() as s:
        r = await s.post(f"{url}/upload", data=form, headers=_auth("pw"))
        assert r.status == 413
        body = await r.json()
    assert body == {"ok": False, "reason": "too-large"}


async def test_upload_unauthorized_returns_401(upload_listener):
    _calls, url = upload_listener
    form = aiohttp.FormData()
    form.add_field("file", b"hi", filename="x.txt", content_type="text/plain")
    async with aiohttp.ClientSession() as s:
        r = await s.post(f"{url}/upload", data=form, headers=_auth("WRONG"))
        assert r.status == 401


async def test_upload_no_writer_returns_409():
    conv = FakeConversation()
    lst = ConversationListener(conv, password="pw")  # no upload_writer
    port = await lst.start("127.0.0.1")
    try:
        form = aiohttp.FormData()
        form.add_field("file", b"hi", filename="x.txt", content_type="text/plain")
        async with aiohttp.ClientSession() as s:
            r = await s.post(
                f"http://127.0.0.1:{port}/upload", data=form, headers=_auth("pw")
            )
            assert r.status == 409
            body = await r.json()
        assert body == {"ok": False, "reason": "no-writer"}
    finally:
        await lst.stop()


# --- config validation -----------------------------------------------------


def test_show_file_upload_requires_conversation_ui():
    with pytest.raises(ValueError, match="show_file_upload"):
        _cfg(mode="conversation", conversation_ui=False, show_file_upload=True)


def test_show_file_upload_ok_in_conversation_ui():
    cfg = _cfg(mode="conversation", conversation_ui=True, show_file_upload=True)
    assert cfg.show_file_upload is True
    assert cfg.max_upload_bytes == 10_000_000
```

Create `packages/optio-codex/tests/test_file_download.py` — grok's file ported the same way (all seven `/download` endpoint tests: 200+disposition, 400 bad-path, 409 no-reader, 404 not-found, 403 forbidden, 413 too-large, 401 unauthorized — plus config validation and the two prompt-injection tests). Identical to grok's `test_file_download.py` with these substitutions: `optio_grok`→`optio_codex`, `GrokTaskConfig`→`CodexTaskConfig`, and the prompt tests exactly:

```python
# --- prompt injection ------------------------------------------------------


def test_compose_injects_downloadables_when_file_download():
    body = compose_agents_md("Do the thing.", file_download=True, host_protocol=False)
    assert "optio-file:" in body
    assert "System:" in body        # host_protocol=False explainer intact


def test_compose_omits_downloadables_by_default():
    body = compose_agents_md("Do the thing.", host_protocol=False)
    assert "optio-file:" not in body


def test_compose_downloadables_comparative_with_host_protocol():
    # With the keyword protocol active the block contrasts DELIVERABLE vs
    # downloadable (comparative wording from the optio-agents SSOT).
    body = compose_agents_md("Do the thing.", file_download=True, host_protocol=True)
    assert "optio-file:" in body
    assert "DELIVERABLE" in body
```

(with `from optio_codex.prompt import compose_agents_md` in the import block.)

- [ ] **Step 2: Run to verify failure**

Run: `.venv/bin/python -m pytest packages/optio-codex/tests/test_file_upload.py packages/optio-codex/tests/test_file_download.py -q`
Expected: upload/download endpoint tests PASS already (Task 3 shipped them — they are ported as regression anchors); the three prompt tests FAIL (`compose_agents_md` has no `file_download` kwarg). If anything besides the prompt tests fails, stop and fix the listener port first.

- [ ] **Step 3: Implement the prompt wiring**

In `packages/optio-codex/src/optio_codex/prompt.py`: add `from optio_agents.prompt import downloadables_block` to the imports, add the `file_download: bool = False` keyword parameter to `compose_agents_md`, document it in the docstring ("append the downloadables instruction block so codex offers files to the human via the ``optio-file:`` sentinel link — conversation_ui file-download feature"), and as the FIRST statement of the function body add:

```python
    if file_download:
        consumer_instructions = (
            consumer_instructions.rstrip()
            + downloadables_block(comparative=host_protocol)
        )
```

In `packages/optio-codex/src/optio_codex/session.py`, thread the flag through `_prepare`'s compose call by adding `file_download=config.file_download,` to the existing `compose_agents_md(...)` arguments.

- [ ] **Step 4: Run to green**

Run: `.venv/bin/python -m pytest packages/optio-codex/tests/test_file_upload.py packages/optio-codex/tests/test_file_download.py packages/optio-codex/tests/test_prompt.py -q` → all pass.
Run: `.venv/bin/python -m pytest packages/optio-codex/tests/ -q` → whole suite green.

- [ ] **Step 5: Commit**

```bash
git add packages/optio-codex/src/optio_codex/prompt.py packages/optio-codex/src/optio_codex/session.py packages/optio-codex/tests/test_file_upload.py packages/optio-codex/tests/test_file_download.py
git commit -m "feat(optio-codex): file upload/download coverage + downloadables prompt (Stage 7)

Ports grok's upload/download listener tests as regression anchors and
wires optio_agents.prompt.downloadables_block into AGENTS.md when
file_download=True (comparative wording follows host_protocol)."
```

---

### Task 7 (TS): `src/codex/events.ts` reducer + unit tests

Pure reducer mapping raw app-server notifications → the shared `ChatItem` model, porting the grok reducer's idioms exactly: adapter-private state rides on `ChatState` (`turn` counter for synthetic per-turn `msgId`s, `toolSeqs` for row refresh), ephemeral tool rows (`dropTools` on new text/permission/close), pending-bubble tail rule, coalesced activity rows for reasoning deltas, permission cards keep `busy=true`, `turn/completed` → finalize + `busy=false` + `turn++`, `x-optio-*` synthetics. Codex-specific vocabulary per the design-doc table: `item/agentMessage/delta` (bubble), `item/reasoning/summaryTextDelta`+`textDelta` (activity), `item/started`/`item/completed` for `commandExecution`/`fileChange`/`mcpToolCall`/`webSearch` (tool rows keyed by `item.id`), the two `requestApproval` server requests (permission, correlated by JSON-RPC id), `error` notification and JSON-RPC error responses (error items), `item/completed` `agentMessage` prefix-upgrade (authoritative text).

**Files:**
- Create: `packages/optio-conversation-ui/src/codex/events.ts`
- Create: `packages/optio-conversation-ui/src/__tests__/codex-events.test.ts`

**Interfaces:**
- Produces: `reduceCodexEvent(state: ChatState, ev: any, seq: number): ChatState`; re-export `initialChatState`.
- Consumes: `../chat.js` (`ChatItem`, `ChatState`), `../apiError.js` (`explainApiError`).

- [ ] **Step 0: Node tooling bootstrap (once)**

Run at the worktree root: `ls packages/optio-conversation-ui/node_modules/.bin/vitest || pnpm install --frozen-lockfile`.

- [ ] **Step 1: Write the failing tests**

Create `packages/optio-conversation-ui/src/__tests__/codex-events.test.ts`:

```typescript
import { describe, expect, it } from 'vitest';
import { initialChatState, type ChatState } from '../chat.js';
import { reduceCodexEvent } from '../codex/events.js';

// The codex reducer consumes the RAW app-server JSON-RPC objects the listener
// fans out over SSE: item/turn notifications, the item/*/requestApproval
// server requests, JSON-RPC error responses, plus the synthetic x-optio-*
// events. Shapes mirror the wire pinned in optio-codex's conversation.py
// (codex-cli 0.142.5 probe + schemas). The "jsonrpc" field is omitted on the
// wire — the fixtures omit it too.

function play(events: any[], from: ChatState = initialChatState): ChatState {
  return events.reduce((s, ev, i) => reduceCodexEvent(s, ev, i), from);
}

const delta = (text: string, itemId = 'i-msg', turnId = 'turn-1') => ({
  method: 'item/agentMessage/delta',
  params: { threadId: 't1', turnId, itemId, delta: text },
});
const reasoning = (text: string) => ({
  method: 'item/reasoning/summaryTextDelta',
  params: { threadId: 't1', turnId: 'turn-1', itemId: 'i-r', delta: text, summaryIndex: 0 },
});
const itemStarted = (item: any, turnId = 'turn-1') => ({
  method: 'item/started',
  params: { threadId: 't1', turnId, item, startedAtMs: 0 },
});
const itemCompleted = (item: any, turnId = 'turn-1') => ({
  method: 'item/completed',
  params: { threadId: 't1', turnId, item, completedAtMs: 0 },
});
const turnCompleted = (status = 'completed', error?: any) => ({
  method: 'turn/completed',
  params: { threadId: 't1', turn: { id: 'turn-1', status, items: [], error: error ?? null } },
});
const cmdItem = { type: 'commandExecution', id: 'i-cmd', command: 'echo hi', cwd: '/w', status: 'inProgress' };

describe('codex app-server event reducer', () => {
  it('agentMessage deltas accumulate into one pending bubble', () => {
    const s = play([delta('PO'), delta('NG')]);
    const b = s.items.find((i) => i.kind === 'assistant');
    expect(b && b.kind === 'assistant' && b.text).toBe('PONG');
    expect(b && b.kind === 'assistant' && b.pending).toBe(true);
    expect(s.busy).toBe(true);
  });

  it('turn/completed finalizes the bubble and clears busy', () => {
    const s = play([delta('done'), turnCompleted()]);
    const b = s.items.find((i) => i.kind === 'assistant');
    expect(b && b.kind === 'assistant' && b.pending).toBe(false);
    expect(s.busy).toBe(false);
  });

  it('a second turn opens a fresh bubble instead of appending to the first', () => {
    const s = play([delta('first'), turnCompleted(), delta('second', 'i-2', 'turn-2'), turnCompleted()]);
    const bubbles = s.items.filter((i) => i.kind === 'assistant');
    expect(bubbles.map((b) => (b as any).text)).toEqual(['first', 'second']);
  });

  it('item/completed agentMessage text is authoritative (heals delta gaps)', () => {
    const s = play([
      delta('PO'), // "NG" delta lost
      itemCompleted({ type: 'agentMessage', id: 'i-msg', text: 'PONG' }),
      turnCompleted(),
    ]);
    const b = s.items.find((i) => i.kind === 'assistant');
    expect(b && b.kind === 'assistant' && b.text).toBe('PONG');
  });

  it('reasoning deltas render as one coalesced activity row, not in the answer', () => {
    const s = play([reasoning('thinking'), reasoning(' more'), delta('ANSWER'), turnCompleted()]);
    const acts = s.items.filter((i) => i.kind === 'activity');
    expect(acts).toHaveLength(1);
    expect(acts[0].kind === 'activity' && acts[0].text).toBe('thinking more');
    const b = s.items.find((i) => i.kind === 'assistant');
    expect(b && b.kind === 'assistant' && b.text).toBe('ANSWER');
  });

  it('item/started commandExecution renders a tool row named by the command', () => {
    const s = play([itemStarted(cmdItem)]);
    const t = s.items.find((i) => i.kind === 'tool');
    expect(t && t.kind === 'tool' && t.name).toBe('echo hi');
    expect(t && t.kind === 'tool' && (t.input as any).command).toBe('echo hi');
    expect(t && t.kind === 'tool' && (t.input as any).cwd).toBe('/w');
    expect(s.busy).toBe(true);
  });

  it('item/completed updates the same tool row by item id (status merged)', () => {
    const s = play([
      itemStarted(cmdItem),
      itemCompleted({ ...cmdItem, status: 'completed', exitCode: 0 }),
    ]);
    const tools = s.items.filter((i) => i.kind === 'tool');
    expect(tools).toHaveLength(1);
    expect(tools[0].kind === 'tool' && (tools[0].input as any).status).toBe('completed');
    expect(tools[0].kind === 'tool' && (tools[0].input as any).exitCode).toBe(0);
    // prior fields preserved for verbose KV rendering
    expect(tools[0].kind === 'tool' && (tools[0].input as any).command).toBe('echo hi');
  });

  it('fileChange / mcpToolCall / webSearch items render tool rows', () => {
    const s = play([
      itemStarted({ type: 'fileChange', id: 'i-fc', status: 'inProgress',
        changes: [{ path: 'a.txt', kind: 'edit', diff: '' }] }),
      itemStarted({ type: 'mcpToolCall', id: 'i-mcp', server: 'srv', tool: 'fetch',
        status: 'inProgress', arguments: { url: 'https://x' } }),
      itemStarted({ type: 'webSearch', id: 'i-ws', query: 'codex docs' }),
    ]);
    const names = s.items.filter((i) => i.kind === 'tool').map((t) => (t as any).name);
    expect(names).toEqual(['file change', 'srv.fetch', 'web search']);
  });

  it('tool rows are ephemeral: new assistant text drops them', () => {
    const s = play([itemStarted(cmdItem), delta('done running')]);
    expect(s.items.some((i) => i.kind === 'tool')).toBe(false);
    expect(s.items.some((i) => i.kind === 'assistant')).toBe(true);
  });

  it('requestApproval creates a card; x-optio-permission-answered flips it; busy stays true', () => {
    const ask = {
      id: 99, method: 'item/commandExecution/requestApproval',
      params: { threadId: 't1', turnId: 'turn-1', itemId: 'i-cmd',
        command: 'echo hi', cwd: '/w', reason: null, startedAtMs: 0 },
    };
    const s = play([itemStarted(cmdItem), ask]);
    const card = s.items.find((i) => i.kind === 'permission');
    expect(card && card.kind === 'permission' && card.requestId).toBe('99');
    expect(card && card.kind === 'permission' && card.toolName).toBe('echo hi');
    expect(card && card.kind === 'permission' && card.answered).toBe(null);
    expect(s.busy).toBe(true); // parked on the gate
    expect(s.items.some((i) => i.kind === 'tool')).toBe(false); // superseded

    const s2 = play([{ type: 'x-optio-permission-answered', request_id: '99', behavior: 'deny' }], s);
    const card2 = s2.items.find((i) => i.kind === 'permission');
    expect(card2 && card2.kind === 'permission' && card2.answered).toBe('deny');
  });

  it('fileChange requestApproval names the card "file change"', () => {
    const s = play([{
      id: 41, method: 'item/fileChange/requestApproval',
      params: { threadId: 't1', turnId: 'turn-1', itemId: 'i-fc', reason: null, startedAtMs: 0 },
    }]);
    const card = s.items.find((i) => i.kind === 'permission');
    expect(card && card.kind === 'permission' && card.toolName).toBe('file change');
  });

  it('x-optio-local-user renders an optimistic user bubble and sets busy', () => {
    const s = play([{ type: 'x-optio-local-user', text: 'hello' }]);
    const u = s.items.find((i) => i.kind === 'user');
    expect(u && u.kind === 'user' && u.text).toBe('hello');
    expect(u && u.kind === 'user' && u.local).toBe(true);
    expect(s.busy).toBe(true);
  });

  it('x-optio-closed appends a closed divider and ends the session', () => {
    const s = play([delta('bye'), turnCompleted(), { type: 'x-optio-closed', reason: 'process ended' }]);
    expect(s.closed).toBe(true);
    expect(s.busy).toBe(false);
    expect(s.items.some((i) => i.kind === 'closed')).toBe(true);
  });

  it('a JSON-RPC error response surfaces an error item and clears busy', () => {
    const s = play([{ type: 'x-optio-local-user', text: 'go' },
      { id: 3, error: { code: -32001, message: 'Server overloaded; retry later.' } }]);
    const e = s.items.find((i) => i.kind === 'error');
    expect(e && e.kind === 'error' && e.text).toContain('overloaded');
    expect(s.busy).toBe(false);
  });

  it('an error notification surfaces an error item; turn/completed failed carries the message too', () => {
    const s = play([
      { method: 'error', params: { threadId: 't1', turnId: 'turn-1',
        error: { message: 'quota exceeded', codexErrorInfo: 'UsageLimitExceeded' }, willRetry: false } },
      turnCompleted('failed', { message: 'quota exceeded' }),
    ]);
    expect(s.items.filter((i) => i.kind === 'error').length).toBeGreaterThanOrEqual(1);
    expect(s.busy).toBe(false);
  });

  it('handshake responses and unrendered notifications are no-ops', () => {
    const s = play([
      { id: 1, result: { userAgent: 'codex/0.142.5' } },
      { method: 'thread/started', params: { thread: { id: 't1' } } },
      { method: 'turn/started', params: { threadId: 't1', turn: { id: 'turn-1', status: 'inProgress', items: [] } } },
      { method: 'thread/tokenUsage/updated', params: { threadId: 't1' } },
    ]);
    expect(s.items).toHaveLength(0);
    expect(s.busy).toBe(false);
  });

  it('a full turn: local echo → reasoning → tool → answer → turn end', () => {
    const s = play([
      { type: 'x-optio-local-user', text: 'say PONG' },
      reasoning('let me think'),
      itemStarted(cmdItem),
      itemCompleted({ ...cmdItem, status: 'completed', exitCode: 0 }),
      delta('PO'), delta('NG'),
      turnCompleted(),
    ]);
    const kinds = s.items.map((i) => i.kind);
    expect(kinds).toContain('user');
    expect(kinds).toContain('activity');
    const b = s.items.find((i) => i.kind === 'assistant');
    expect(b && b.kind === 'assistant' && b.text).toBe('PONG');
    expect(b && b.kind === 'assistant' && b.pending).toBe(false);
    expect(s.busy).toBe(false);
  });
});
```

- [ ] **Step 2: Run to verify failure**

Run: `cd packages/optio-conversation-ui && node_modules/.bin/vitest run src/__tests__/codex-events.test.ts`
Expected: FAIL — cannot resolve `../codex/events.js`.

- [ ] **Step 3: Implement `packages/optio-conversation-ui/src/codex/events.ts`**

```typescript
// Pure event reducer: raw codex app-server JSON-RPC messages -> ChatState.
//
// The listener and the widget transport pass the objects through untouched;
// all codex-specific interpretation lives here (testable without a DOM).
// Wire shapes are pinned in optio-codex's conversation.py (codex-cli 0.142.5
// probe + generated schemas). The "jsonrpc" field is omitted on the wire:
//   * notifications — item/agentMessage/delta {itemId, delta},
//     item/reasoning/summaryTextDelta|textDelta {delta}, item/started /
//     item/completed {item:{type,…}}, turn/completed {turn:{status,error}},
//     error {error:{message}}.
//   * server requests (id + method) — item/commandExecution/requestApproval
//     {command, cwd, …} and item/fileChange/requestApproval {reason, …}; the
//     listener correlates the operator's answer by the JSON-RPC id.
//   * responses (id, no method) — turn/start ACKs and handshake results (no
//     rendering); error responses surface as error items.
//   * synthetic x-optio-* events (permission-answered / closed / local-user).

import type { ChatItem, ChatState } from '../chat.js';
import { explainApiError } from '../apiError.js';
export { initialChatState } from '../chat.js';

// Adapter-private memory threaded through the shared ChatState (structural
// superset — the extra fields ride along unseen by the generic widget):
//  - turn: monotonic turn counter, so each turn's cumulative answer bubble
//    carries a distinct synthetic msgId (one bubble coalesces a turn even
//    when codex splits it across several agentMessage items).
//  - toolSeqs: seq of the rendered tool row per item.id, so item/completed
//    can find and refresh the row it belongs to.
interface CodexChatState extends ChatState {
  turn?: number;
  toolSeqs?: Record<string, number>;
}

const PERMISSION_METHODS = new Set([
  'item/commandExecution/requestApproval',
  'item/fileChange/requestApproval',
]);

function pendingIndex(items: ChatItem[]): number {
  return items.findIndex((i) => i.kind === 'assistant' && i.pending);
}

// The pending bubble may keep absorbing the in-flight turn only while it is
// the tail (ephemeral tool rows don't count — they are dropped by the next
// text).
function isTail(items: ChatItem[], idx: number): boolean {
  return items.slice(idx + 1).every((i) => i.kind === 'tool');
}

const dropTools = (items: ChatItem[]) => items.filter((i) => i.kind !== 'tool');

// Append a text delta to the in-flight assistant bubble, creating it (with
// the current turn's synthetic msgId) if absent or no longer the tail.
function appendPending(items: ChatItem[], seq: number, text: string, msgId: string): ChatItem[] {
  const idx = pendingIndex(items);
  if (idx !== -1 && isTail(items, idx)) {
    const cur = items[idx] as Extract<ChatItem, { kind: 'assistant' }>;
    const next: ChatItem = { ...cur, text: cur.text + text };
    return [...items.slice(0, idx), next, ...items.slice(idx + 1)];
  }
  return [...items, { kind: 'assistant', text, pending: true, seq, msgId }];
}

// Finalize the in-flight assistant bubble (pending -> false), if any.
function finalizePending(items: ChatItem[]): ChatItem[] {
  const idx = pendingIndex(items);
  if (idx === -1) return items;
  const cur = items[idx] as Extract<ChatItem, { kind: 'assistant' }>;
  return [...items.slice(0, idx), { ...cur, pending: false }, ...items.slice(idx + 1)];
}

// Reasoning is never folded into the answer. Coalesce contiguous reasoning
// deltas into a single muted activity row.
function appendThought(items: ChatItem[], seq: number, text: string): ChatItem[] {
  const last = items[items.length - 1];
  if (last && last.kind === 'activity') {
    const next: ChatItem = { ...last, text: last.text + text };
    return [...items.slice(0, -1), next];
  }
  return [...items, { kind: 'activity', text, seq }];
}

// item.type -> tool-row shape (name + KV input for verbose rendering).
function toolRow(item: any): { name: string; input: unknown } | null {
  switch (item?.type) {
    case 'commandExecution':
      return { name: String(item.command ?? 'command'), input: { command: item.command, cwd: item.cwd } };
    case 'fileChange':
      return { name: 'file change', input: { changes: item.changes } };
    case 'mcpToolCall':
      return { name: `${item.server ?? 'mcp'}.${item.tool ?? 'tool'}`, input: item.arguments ?? {} };
    case 'webSearch':
      return { name: 'web search', input: { query: item.query } };
    default:
      return null; // agentMessage/reasoning/userMessage/… — not tool rows
  }
}

export function reduceCodexEvent(state: ChatState, ev: any, seq: number): ChatState {
  return reduce(state as CodexChatState, ev, seq);
}

function reduce(st: CodexChatState, ev: any, seq: number): CodexChatState {
  // Synthetic, widget/engine-emitted events (bare `type`, no JSON-RPC frame).
  const synthetic = ev?.type as string | undefined;
  if (synthetic === 'x-optio-local-user') {
    const text = typeof ev.text === 'string' ? ev.text : '';
    if (text === '') return st;
    return { ...st, busy: true, items: [...st.items, { kind: 'user', text, seq, local: true }] };
  }
  if (synthetic === 'x-optio-permission-answered') {
    const requestId = String(ev.request_id);
    const behavior: 'allow' | 'deny' = ev.behavior === 'allow' ? 'allow' : 'deny';
    let changed = false;
    const items = st.items.map((i) => {
      if (i.kind !== 'permission' || i.requestId !== requestId || i.answered !== null) return i;
      changed = true;
      return { ...i, answered: behavior };
    });
    return changed ? { ...st, items } : st;
  }
  if (synthetic === 'x-optio-closed') {
    const item: ChatItem = { kind: 'closed', reason: String(ev.reason ?? ''), seq };
    return { ...st, items: [...dropTools(st.items), item], busy: false, closed: true };
  }
  if (synthetic !== undefined) return st; // x-optio-unparseable, forward compat

  const method = ev?.method as string | undefined;
  const hasId = ev?.id !== undefined && ev?.id !== null;

  // Server -> client REQUEST we must answer: the permission gate. The
  // listener correlates the operator's reply by this JSON-RPC id.
  if (method !== undefined && hasId && PERMISSION_METHODS.has(method)) {
    const params = ev.params ?? {};
    const item: ChatItem = {
      kind: 'permission',
      requestId: String(ev.id),
      toolName:
        method === 'item/commandExecution/requestApproval'
          ? String(params.command ?? 'command execution')
          : 'file change',
      input: params,
      answered: null,
      seq,
    };
    // busy stays true — the agent is parked on the gate. The request
    // supersedes any in-flight tool announcement.
    return { ...st, busy: true, items: [...dropTools(st.items), item] };
  }
  if (method !== undefined && hasId) return st; // other server requests: engine answers -32601

  // Server -> client NOTIFICATIONS.
  if (method !== undefined) {
    const params = ev.params ?? {};
    const msgId = `turn-${st.turn ?? 0}`;

    if (method === 'item/agentMessage/delta') {
      const text = params.delta ?? '';
      if (text === '') return st;
      // The agent is answering now — clear any in-flight tool announcement.
      return { ...st, busy: true, items: appendPending(dropTools(st.items), seq, text, msgId) };
    }

    if (method === 'item/reasoning/summaryTextDelta' || method === 'item/reasoning/textDelta') {
      const text = params.delta ?? '';
      if (text === '') return st;
      return { ...st, busy: true, items: appendThought(st.items, seq, text) };
    }

    if (method === 'item/started') {
      const item = params.item ?? {};
      const row = toolRow(item);
      if (!row) return st;
      const id = String(item.id ?? '');
      const chat: ChatItem = { kind: 'tool', name: row.name, input: row.input, seq };
      return {
        ...st, busy: true,
        items: [...dropTools(st.items), chat],
        toolSeqs: { ...st.toolSeqs, [id]: seq },
      };
    }

    if (method === 'item/completed') {
      const item = params.item ?? {};
      if (item.type === 'agentMessage') {
        // The completed item's text is authoritative for the turn's bubble
        // when it is a pure upgrade of what the deltas built (heals replay
        // gaps in the common single-item case).
        const idx = pendingIndex(st.items);
        const full = String(item.text ?? '');
        if (idx !== -1 && full) {
          const cur = st.items[idx] as Extract<ChatItem, { kind: 'assistant' }>;
          if (full.startsWith(cur.text) && full !== cur.text) {
            const items = [...st.items.slice(0, idx), { ...cur, text: full }, ...st.items.slice(idx + 1)];
            return { ...st, items };
          }
          return st;
        }
        if (full) {
          return { ...st, busy: true, items: appendPending(dropTools(st.items), seq, full, msgId) };
        }
        return st;
      }
      const row = toolRow(item);
      if (!row) return st;
      const id = String(item.id ?? '');
      const at = st.toolSeqs?.[id];
      const idx = at === undefined ? -1 : st.items.findIndex((i) => i.kind === 'tool' && i.seq === at);
      const finalInput = {
        ...(row.input as object),
        status: item.status,
        ...(item.exitCode !== undefined && item.exitCode !== null ? { exitCode: item.exitCode } : {}),
      };
      if (idx !== -1) {
        const cur = st.items[idx] as Extract<ChatItem, { kind: 'tool' }>;
        const next: ChatItem = { ...cur, name: row.name, input: { ...(cur.input as object), ...finalInput } };
        return { ...st, items: [...st.items.slice(0, idx), next, ...st.items.slice(idx + 1)] };
      }
      // Completion for an untracked item (e.g. replay gap): render it.
      const chat: ChatItem = { kind: 'tool', name: row.name, input: finalInput, seq };
      return {
        ...st, busy: true,
        items: [...dropTools(st.items), chat],
        toolSeqs: { ...st.toolSeqs, [id]: seq },
      };
    }

    if (method === 'turn/completed') {
      const turn = params.turn ?? {};
      let items = finalizePending(dropTools(st.items));
      if (turn.status === 'failed') {
        const msg = explainApiError(String(turn.error?.message ?? 'turn failed'), null);
        items = [...items, { kind: 'error', text: msg, seq }];
      }
      // Turn complete — close the bubble and open the next turn's bubble id.
      return { ...st, items, busy: false, turn: (st.turn ?? 0) + 1 };
    }

    if (method === 'error') {
      const msg = explainApiError(String(params.error?.message ?? 'error'), null);
      // May precede a failed turn/completed (which clears busy); dedupe on
      // an identical trailing error to avoid a double row from that pair.
      const last = st.items[st.items.length - 1];
      if (last && last.kind === 'error' && last.text === msg) return st;
      return { ...st, items: [...dropTools(st.items), { kind: 'error', text: msg, seq }] };
    }

    // thread/started, turn/started, thread/tokenUsage/updated, plan/… — no
    // dedicated rendering; passed through as no-ops.
    return st;
  }

  // Response to one of our requests (id, no method): turn/start ACKs and
  // handshake results need no rendering; an error response surfaces.
  if (hasId) {
    if (ev.error) {
      const msg = explainApiError(String(ev.error?.message ?? JSON.stringify(ev.error)), null);
      return { ...st, busy: false, items: [...dropTools(st.items), { kind: 'error', text: msg, seq }] };
    }
    return st;
  }

  return st;
}
```

Note on the `turn/completed failed` + `error` pair: the reducer dedupes only the *identical trailing* message; the test feeds `error` first then `turn/completed failed` with the same message and asserts `>= 1` error item — both orderings render at least one row and never crash.

- [ ] **Step 4: Run to green**

Run: `cd packages/optio-conversation-ui && node_modules/.bin/vitest run src/__tests__/codex-events.test.ts` → all pass.
Run: `cd packages/optio-conversation-ui && node_modules/.bin/vitest run` → the whole UI suite stays green.

- [ ] **Step 5: Commit**

```bash
git add packages/optio-conversation-ui/src/codex/events.ts packages/optio-conversation-ui/src/__tests__/codex-events.test.ts
git commit -m "feat(optio-conversation-ui): codex app-server reducer (Stage 6)

Pure reducer mapping app-server notifications onto the shared ChatItem
model, porting the grok idioms: turn-counter msgIds, toolSeqs row
refresh, ephemeral dropTools, coalesced reasoning activity, permission
cards keyed by JSON-RPC id with busy staying true, turn/completed as
the turn-end, item/completed agentMessage prefix-upgrade."
```

---

### Task 8 (TS): `CodexView.tsx` + `ConversationWidget` dispatch + widget tests + typecheck

Near-copy of `GrokView` (the listener transport is identical by design): SSE from `{widgetProxyUrl}events` (trailing slash load-bearing), `useReducer(reduceCodexEvent)`, POST `send`/`interrupt`/`permission`/`model`, multipart uploads with the `System: upload received, stored in <path>` prompt preamble, optimistic local echo with negative seqs, `optio-file:` download via blob save, antd `Select` model picker. Then the `widgetData.protocol === "codex"` dispatch case and the `index.ts` exports.

**Files:**
- Create: `packages/optio-conversation-ui/src/codex/CodexView.tsx`
- Modify: `packages/optio-conversation-ui/src/ConversationWidget.tsx`
- Modify: `packages/optio-conversation-ui/src/index.ts`
- Modify: `packages/optio-conversation-ui/package.json` (description: "… (claudecode + opencode + codex protocols)")
- Create: `packages/optio-conversation-ui/src/__tests__/codex-widget.test.tsx`

**Interfaces:**
- Produces: `CodexView(props: WidgetProps)`; `ConversationWidget` dispatches `protocol === 'codex'`; exports `reduceCodexEvent`, `CodexView`.
- Consumes: `./codex/events.js`, `../ConversationView.js` (`ConversationViewProps` incl. `toolVerbosity`, `showFileUpload`, `maxUploadBytes`, `fileDownload`, `onFileDownload`, `modelSelector`), `../attachments.js` (`Attachment`), `../FileDownloadContext.js` (`blobDownload`), antd `Select`.
- widgetData consumed (matches Task 5's `set_widget_data` exactly): `protocol`, `toolVerbosity`, `showModelSelector`, `models`, `currentModel`, `showFileUpload`, `maxUploadBytes`, `fileDownload`, `maxDownloadBytes`.

- [ ] **Step 1: Write the failing tests**

Create `packages/optio-conversation-ui/src/__tests__/codex-widget.test.tsx` — grok-widget.test.tsx ported to the codex wire (MockEventSource harness identical):

```tsx
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent, waitFor, act } from '@testing-library/react';
import { ConversationWidget } from '../ConversationWidget.js';

// CodexView parity (Stage 7): the model picker, file upload (System:
// reference), file download, and tool-verbosity all funnel through the shared
// ConversationView, driven by codex's app-server wire over the listener SSE.

class MockEventSource {
  static last: MockEventSource | null = null;
  url: string;
  onmessage: ((e: MessageEvent) => void) | null = null;
  constructor(url: string) {
    this.url = url;
    MockEventSource.last = this;
  }
  addEventListener() {}
  removeEventListener() {}
  close() {}
  emit(ev: unknown, seq: number) {
    this.onmessage?.(new MessageEvent('message', { data: JSON.stringify(ev), lastEventId: String(seq) }));
  }
  static reset() {
    MockEventSource.last = null;
  }
}

function makeProps(widgetData: any = { protocol: 'codex' }) {
  return {
    process: { _id: 'p1', name: 'n', widgetData, status: { state: 'running' } },
    apiBaseUrl: '',
    widgetProxyUrl: '/api/widget/db/cx/p1/',
    prefix: 'cx',
    database: 'db',
  };
}

let seq = 0;
function fire(ev: unknown) {
  act(() => MockEventSource.last!.emit(ev, ++seq));
}

const cmdStarted = (command: string, cwd = '/w') => ({
  method: 'item/started',
  params: {
    threadId: 't1', turnId: 'turn-1', startedAtMs: 0,
    item: { type: 'commandExecution', id: 'i-cmd', command, cwd, status: 'inProgress' },
  },
});

describe('CodexView (Stages 6-7 parity)', () => {
  beforeEach(() => {
    vi.restoreAllMocks();
    MockEventSource.reset();
    (globalThis as any).EventSource = MockEventSource as any;
    seq = 0;
  });

  it('model selector POSTs the chosen model to /model', async () => {
    const fetchMock = vi.fn(async () => new Response(JSON.stringify({ ok: true }), { status: 200 }));
    vi.stubGlobal('fetch', fetchMock);
    render(
      <ConversationWidget
        {...makeProps({
          protocol: 'codex',
          showModelSelector: true,
          currentModel: 'gpt-5.5',
          models: [
            { id: 'gpt-5.5', label: 'GPT-5.5' },
            { id: 'gpt-5.4-mini', label: 'GPT-5.4 Mini' },
          ],
        })}
      />,
    );
    // antd Select: open the dropdown, then pick the second option.
    const combo = document.querySelector('[data-testid="model-select"] .ant-select-selector') as HTMLElement;
    fireEvent.mouseDown(combo);
    await waitFor(() => expect(screen.getByText('GPT-5.4 Mini')).toBeTruthy());
    fireEvent.click(screen.getByText('GPT-5.4 Mini'));

    await waitFor(() => expect(fetchMock).toHaveBeenCalled());
    const calls = fetchMock.mock.calls as any[];
    const modelCall = calls.find((c) => String(c[0]).endsWith('/model'));
    expect(modelCall).toBeTruthy();
    expect(JSON.parse((modelCall[1] as RequestInit).body as string)).toEqual({ model: 'gpt-5.4-mini' });
  });

  it('upload attaches a System: reference to the next prompt', async () => {
    const fetchMock = vi.fn(async (...args: any[]) => {
      if (String(args[0]).endsWith('/upload')) {
        return new Response(JSON.stringify({ ok: true, files: [{ filename: 'note.txt', path: 'uploads/note.txt' }] }), { status: 200 });
      }
      return new Response(JSON.stringify({ ok: true }), { status: 200 });
    });
    vi.stubGlobal('fetch', fetchMock as any);
    render(<ConversationWidget {...makeProps({ protocol: 'codex', showFileUpload: true, maxUploadBytes: 1000 })} />);

    const fileInput = screen.getByTestId('file-input') as HTMLInputElement;
    const file = new File([new Uint8Array([104, 105])], 'note.txt', { type: 'text/plain' });
    await act(async () => {
      fireEvent.change(fileInput, { target: { files: [file] } });
    });
    const box = screen.getByTestId('conversation-input-box') as HTMLTextAreaElement;
    fireEvent.change(box, { target: { value: 'summarize this' } });
    await act(async () => {
      fireEvent.click(screen.getByTestId('conversation-send'));
    });

    const calls = fetchMock.mock.calls as any[];
    await waitFor(() => expect(calls.some((c) => String(c[0]).endsWith('/send'))).toBe(true));
    const sendCall = calls.find((c) => String(c[0]).endsWith('/send'));
    const sentText = JSON.parse((sendCall[1] as RequestInit).body as string).text as string;
    expect(sentText).toContain('uploads/note.txt');
    expect(sentText).toContain('summarize this');
    // The optimistic echo shows the operator's text, not the System: preamble.
    expect(screen.getByText('summarize this')).toBeTruthy();
  });

  it('an optio-file: link fetches /download and triggers a blob save', async () => {
    const bytes = new Uint8Array([1, 2, 3]);
    const fetchMock = vi.fn(async () => new Response(bytes, { status: 200, headers: { 'content-type': 'text/markdown' } }));
    vi.stubGlobal('fetch', fetchMock as any);
    const createObjectURL = vi.fn(() => 'blob:x');
    const revokeObjectURL = vi.fn();
    (globalThis.URL as any).createObjectURL = createObjectURL;
    (globalThis.URL as any).revokeObjectURL = revokeObjectURL;
    const clickSpy = vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(() => {});

    render(<ConversationWidget {...makeProps({ protocol: 'codex', fileDownload: true })} />);
    // codex answer carrying an optio-file: sentinel markdown link.
    fire({
      method: 'item/agentMessage/delta',
      params: { threadId: 't1', turnId: 'turn-1', itemId: 'i-msg', delta: 'Here: [report](optio-file:out/r.md)' },
    });
    fire({
      method: 'turn/completed',
      params: { threadId: 't1', turn: { id: 'turn-1', status: 'completed', items: [] } },
    });

    const link = await screen.findByText(/report/);
    await act(async () => {
      fireEvent.click(link);
    });
    await waitFor(() => expect(fetchMock).toHaveBeenCalled());
    const dlCall = (fetchMock.mock.calls as any[]).find((c) => String(c[0]).includes('/download'));
    expect(dlCall).toBeTruthy();
    expect(String(dlCall[0])).toContain('path=out%2Fr.md');
    expect(clickSpy).toHaveBeenCalled();
  });

  it('verbose tool verbosity renders the command item as a key-value table', () => {
    render(<ConversationWidget {...makeProps({ protocol: 'codex', toolVerbosity: 'verbose' })} />);
    fire(cmdStarted('ls -la', '/w'));
    expect(screen.getByText('command')).toBeTruthy();
    expect(screen.getByText('ls -la')).toBeTruthy();
    expect(screen.getByText('cwd')).toBeTruthy();
  });

  it('a permission request renders a card and answering POSTs /permission', async () => {
    const fetchMock = vi.fn(async () => new Response(JSON.stringify({ ok: true }), { status: 200 }));
    vi.stubGlobal('fetch', fetchMock as any);
    render(<ConversationWidget {...makeProps({ protocol: 'codex' })} />);
    fire({
      id: 99, method: 'item/commandExecution/requestApproval',
      params: { threadId: 't1', turnId: 'turn-1', itemId: 'i-cmd', command: 'echo hi', cwd: '/w', reason: null, startedAtMs: 0 },
    });
    const allow = await screen.findByRole('button', { name: /allow/i });
    await act(async () => {
      fireEvent.click(allow);
    });
    await waitFor(() => expect(fetchMock).toHaveBeenCalled());
    const permCall = (fetchMock.mock.calls as any[]).find((c) => String(c[0]).endsWith('/permission'));
    expect(permCall).toBeTruthy();
    expect(JSON.parse((permCall[1] as RequestInit).body as string)).toEqual({ request_id: '99', behavior: 'allow' });
  });
});
```

(Before running, confirm the permission-card button accessible name against `ConversationView.tsx` — the claudecode/grok widget tests show the established queries; if the card exposes different test ids/labels, use those. The assertion target — POST `/permission` with `{request_id:'99', behavior:'allow'}` — stays.)

- [ ] **Step 2: Run to verify failure**

Run: `cd packages/optio-conversation-ui && node_modules/.bin/vitest run src/__tests__/codex-widget.test.tsx`
Expected: FAIL — `protocol: 'codex'` falls through to `ClaudeCodeView`, which never renders codex events / the model select.

- [ ] **Step 3: Implement**

Create `packages/optio-conversation-ui/src/codex/CodexView.tsx` — a near-copy of the main checkout's `src/grok/GrokView.tsx` with these substitutions (transport identical, only names/comments/wire labels change):

- `GrokView` → `CodexView`; `reduceGrokEvent` → `reduceCodexEvent` (import from `./events.js`);
- header comment: "Conversation view for codex tasks: speaks the codex app-server stream through the per-task conversation listener (SSE from `{widgetProxyUrl}events`) … Model switching is INLINE (the chosen model rides the next turn/start — no restart)";
- the `console.info` tag: `'[optio-conversation-ui] codex conversation widget activated:'`;
- the busy comment: "the turn/completed notification clears it";
- everything else byte-identical: `chatReducer`, widgetData reads (`toolVerbosity`, `currentModel`, `showModelSelector`, `models`, `showFileUpload`, `maxUploadBytes`, `fileDownload`), the `post`/`uploadFiles`/`onFileDownload` helpers, the `onSend` System:-preamble + negative-seq local echo, `onInterrupt`, `onPermission` (deny carries `message: 'Denied by the operator.'`), the antd `Select` model picker posting `/model`, `themeMode`/`onToggleTheme` passthrough.

Modify `packages/optio-conversation-ui/src/ConversationWidget.tsx`:

```tsx
import { CodexView } from './codex/CodexView.js';
```

and in `dispatchedView`, before the claudecode fallback:

```tsx
    if (protocol === 'codex') return <CodexView {...viewProps} />;
```

Modify `packages/optio-conversation-ui/src/index.ts` — add:

```typescript
export { reduceCodexEvent } from './codex/events.js';
export { CodexView } from './codex/CodexView.js';
```

Update the `description` field in `packages/optio-conversation-ui/package.json` to `"Engine-neutral conversation widget for optio tasks (claudecode + opencode + codex protocols)"`.

- [ ] **Step 4: Run to green + typecheck**

Run: `cd packages/optio-conversation-ui && node_modules/.bin/vitest run` → whole UI suite green (codex + existing claudecode/opencode tests).
Run: `cd packages/optio-conversation-ui && node_modules/.bin/tsc --noEmit` → no errors. (Never `npx`.)

- [ ] **Step 5: Commit**

```bash
git add packages/optio-conversation-ui/src/codex/ packages/optio-conversation-ui/src/ConversationWidget.tsx packages/optio-conversation-ui/src/index.ts packages/optio-conversation-ui/package.json packages/optio-conversation-ui/src/__tests__/codex-widget.test.tsx
git commit -m "feat(optio-conversation-ui): CodexView + codex protocol dispatch (Stages 6-7)

Near-copy of GrokView over the identical listener transport: SSE
events, send/interrupt/permission/model posts, System:-prefixed upload
references, negative local seqs, optio-file: blob downloads, inline
model Select; ConversationWidget dispatches widgetData.protocol=codex."
```

---

### Task 9: Demo — seed-pinned conversation task (completes the guide's trio)

Add the third leg of the codex demo trio to `packages/optio-demo/src/optio_demo/tasks/codex.py`: one seed-pinned conversation task per captured seed, mirroring grok's `grok-conversation-seed-*` exactly. **Depends on Plan C's demo structure** (seed-setup task, `list_seeds` gating, sidecar name map, `_resolve_ssh_config`); Task 0 recorded whether it landed. If it has NOT landed yet, park this task until it does (blocker: Plan C demo loop absent; everything below slots into its per-seed loop).

**Files:**
- Modify: `packages/optio-demo/src/optio_demo/tasks/codex.py`

**Interfaces:**
- Consumes: `optio_codex.create_codex_task`, `CodexTaskConfig`, plus Plan C's in-module helpers (`_seed_name_map`, seed loop, `ssh`).
- Produces: task ids `codex-conversation-seed-<seed_id>`.

- [ ] **Step 1: Failing check**

Run: `.venv/bin/python - <<'EOF'
import asyncio, inspect
from optio_demo.tasks import codex as demo_codex
src = inspect.getsource(demo_codex)
assert "codex-conversation-seed-" in src, "conversation demo leg missing"
print("ok")
EOF`
Expected: AssertionError (the leg does not exist yet). If `optio_demo` is not importable in this venv: `.venv/bin/pip install -e packages/optio-demo` first.

- [ ] **Step 2: Implement**

Inside the existing per-seed loop in `get_tasks` (directly after the seed-pinned iframe task's `tasks.append(...)`), add — mirroring grok's conversation demo verbatim with codex naming:

```python
        tasks.append(
            create_codex_task(
                process_id=f"codex-conversation-seed-{seed_id}",
                name=f"Codex conversation — {name}",
                description=(
                    "Conversation-mode Codex session from a captured "
                    f"seed ({name}): chat with the agent in the dashboard, "
                    "approve tool permissions interactively."
                ),
                config=CodexTaskConfig(
                    consumer_instructions="",   # defaulted conversation prompt
                    mode="conversation",
                    conversation_ui=True,
                    tool_verbosity="description-only",
                    show_model_selector=True,
                    show_file_upload=True,
                    file_download=True,
                    permission_gate=True,       # exercises the approve/deny UI
                    host_protocol=False,        # pure conversation gate
                    ssh=ssh,
                    seed_id=seed_id,
                    supports_resume=True,
                ),
            )
        )
```

(Keep the module's existing import list; `CodexTaskConfig`/`create_codex_task` are already imported by Plan A/C's version of this file.)

- [ ] **Step 3: Verify**

Run the Step-1 snippet again → prints `ok`.
Run: `.venv/bin/python -c "import asyncio; from optio_demo.tasks import get_task_definitions; print('ok')"` → `ok` (module still imports; with zero seeds recorded the new leg simply doesn't materialize, which is the correct gating).
Run: `.venv/bin/python -m pytest packages/optio-codex/tests/ -q` → still green.

- [ ] **Step 4: Commit**

```bash
git add packages/optio-demo/src/optio_demo/tasks/codex.py
git commit -m "feat(optio-demo): codex seed-pinned conversation demo task (completes the trio)

conversation_ui + permission gate + model selector + file up/download,
host_protocol=False — the third leg next to seed-setup and the
seed-pinned iframe demo, gated on the real codex seed store."
```

---

### Task 10: Final verification sweep (no new code)

- [ ] **Step 1: Python suite**

Run: `.venv/bin/python -m pytest packages/optio-codex/tests/ -q`
Expected: all green — the Stage-6/7 files (`test_models`, `test_conversation`, `test_conversation_listener`, `test_session_conversation`, `test_file_upload`, `test_file_download`) plus every pre-existing test. Flake note: if an unrelated `optio-core` cancel-timing test is in the run scope, re-run before suspecting a regression.

- [ ] **Step 2: UI suite + typecheck**

Run: `cd packages/optio-conversation-ui && node_modules/.bin/vitest run && node_modules/.bin/tsc --noEmit`
Expected: all green, no type errors.

- [ ] **Step 3: Cross-checks (grep-level, no execution)**

- `grep -rn "sandboxPolicy" packages/optio-codex/src/` → must appear ONLY in comments/docstrings (never as a `thread/start` param key).
- `grep -rn '"jsonrpc"' packages/optio-codex/src/` → no hits (the wire omits it).
- `grep -rn "protocol.*codex" packages/optio-codex/src/optio_codex/session.py packages/optio-conversation-ui/src/ConversationWidget.tsx` → the `set_widget_data` literal and the dispatch case agree on `"codex"`.
- `git log -12 --format=%B | grep -c Co-Authored-By` → `0` (no self-credit lines in any Plan-D commit).

- [ ] **Step 4: Commit (only if the sweep fixed anything)** — otherwise nothing to commit.

---

## Self-Review (performed while writing this plan)

**Scope coverage vs the assignment:**
- conversation.py with grok skeleton invariants (docstring pinning the probed wire, attach/run_reader/bootstrap, `_write_lock`, two-tier fan-out, queue-permissions-until-handler, `_finish` drain, `ConversationClosed`) → Task 2. Codex framing (initialize without experimentalApi + initialized; thread/start NOT ephemeral; turn/start input list; turn/completed status enum; agentMessage delta accumulation + item.completed authority; requestApproval accept/decline with JSON-RPC-id correlation; turn/interrupt with tracked turnId; inline model switch on next turn/start; model/list + account/read at bootstrap; thread_id exposed + thread/resume seam) → Tasks 1–2. session.py `_conversation_body` port + clean-close DONE park + crash→RuntimeError → Task 5. types.py matrix (Stage 6 + Stage 7 fields) → Task 4. Listener ~verbatim → Task 3. conversation-ui reducer + view + dispatch + widgetData.protocol → Tasks 7–8 (+ the `protocol: "codex"` producer in Task 5). prompt downloadables + host_protocol=False composition check → Task 6. Demo trio completion → Task 9. fake_codex app-server responder incl. TOOL blocking + `FAKE_CODEX_EXIT_AFTER` → Task 5. All six ported test files present; reducer tests mirror grok's coverage (bubble/turn/tool/permission/synthetics/error) with codex vocabulary plus codex-only cases (authoritative item text, failed-turn error).
- **Method names cross-checked against the schema dump** (`ClientRequest`/`ServerRequest`/`ServerNotification`/`ClientNotification`, 0.142.5): every request/notification string used in code or tests appears verbatim in the dump — `initialize`, `initialized`, `account/read`, `model/list`, `thread/start`, `thread/resume`, `turn/start`, `turn/interrupt`, `thread/started`, `turn/started`, `turn/completed`, `item/started`, `item/completed`, `item/agentMessage/delta`, `item/reasoning/summaryTextDelta`, `item/reasoning/textDelta`, `item/commandExecution/requestApproval`, `item/fileChange/requestApproval`, `error`. Two deliberate catches documented in-code: `thread/start` takes **`sandbox`** (kebab-case enum), not the README-example `sandboxPolicy`/camelCase (that object is turn/start-only); decision enum values are `accept`/`decline` strings (deny message not transmittable). `ThreadItem` type tags are camelCase (`agentMessage`, `commandExecution`, `fileChange`, `mcpToolCall`, `webSearch`) — used consistently in the fake, the engine, and the reducer.
- **Type consistency:** `PermissionDecision.behavior` `allow|deny` maps to `accept|decline` at exactly one place (`_answer_permission_decision`); `PermissionRequest.raw` is always the full JSON-RPC request so the listener's `str(raw["id"])` correlation works unchanged; `ChatItem` union fields match `chat.ts` exactly (`msgId` present on assistant items, `requestId: string` on permission items); widgetData keys produced in Task 5 == keys consumed in Task 8.
- **No placeholders:** every test and implementation block is complete code; the only conditional instructions are the explicitly-scoped B/C reconciliation notes (kickoff `resuming` gate, `resume_thread_id`, teardown ordering, `supports_resume` kwarg, demo dependency) — each names the exact integration point and the grok line it mirrors, and Task 9 has an explicit park rule instead of a stub.
- **Tree green per task:** each task ends with the full codex suite (and, for TS tasks, the full UI suite) before its commit; Python tasks 0–6 precede and are independent of TS tasks 7–8; Task 9 is Python again but depends only on Plan C, not on the TS tasks.
- **Known judgment calls (flagged, not hidden):** (1) reducer coalesces a whole turn into one bubble via turn-counter msgIds even though codex has itemIds — grok idiom kept deliberately, with the prefix-upgrade rule reconciling authoritative item text; (2) `item/commandExecution/outputDelta` is opted out at initialize (nothing renders it; keeps the SSE replay buffer from flooding) — remove the opt-out if a later stage renders live command output; (3) `test_conversation_ui_publishes_widget` carries an explicit pre-run check of the engine's widgetData accessor; (4) `ask_for_approval` stays iframe-only — conversation mode derives `approvalPolicy` from `permission_gate`, documented in types.py.
