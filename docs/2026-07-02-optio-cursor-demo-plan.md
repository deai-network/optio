# optio-cursor Demo Tasks Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: superpowers:subagent-driven-development / executing-plans. Checkbox steps.
> **Depends on:** Stage 3 (seeds) for the setup + iframe demos; Stage 6 (conversation) for the conversation demo.

**Goal:** Ship the cursor demo trio in `optio-demo` so the wrapper is exercised in the real dashboard: one **seed-setup** task + two **seed-pinned** run tasks (one iframe, one conversation) driven from the captured identity — matching the claudecode/opencode/grok demo trios (guide Part 5).

**Architecture:** Add `optio_demo/tasks/cursor.py` exposing `async def get_tasks(services) -> list[TaskInstance]`, aggregated in `optio_demo/tasks/__init__.py`. Mirror `optio_demo/tasks/grok.py` exactly, swapping factory/config/seed manifest to cursor's.

**Tech Stack:** Python, `optio_cursor`, `optio_demo` task framework.

## Global Constraints

- Branch `csillag/cursor`. Reference = `packages/optio-demo/src/optio_demo/tasks/grok.py` (the freshest trio; claudecode/opencode secondary). Aggregation in `tasks/__init__.py`.
- Registration was wired in Stage 0 (demo Makefile/pyproject + root `RELEASABLE_PY`/`PY_PACKAGES`) — verify present; add if missing.
- `ssh` defaults to None (local) unless `OPTIO_CURSOR_DEMO_SSH_HOST` is set (mirror the other engines' env-var pattern).
- Seed-pinned tasks appear only after a seed exists (query via `optio_cursor.list_seeds`), mirroring grok's gating.
- **Seed-setup prompt (cursor-specific):** instruct the operator/agent to run `cursor-agent login` in the embedded terminal; with `NO_OPEN_BROWSER=1` (already in the launch env) the login URL is printed → the AGENTS.md protocol section tells the agent to surface it via `BROWSER:` so the operator can complete OAuth in their own browser. Stopping the task captures the seed (`on_seed_saved`). Alternative path documented in the prompt: paste a `CURSOR_API_KEY` into the config instead.
- Non-gated demo runs use `force=True` (cursor's auto-approve; grok used `--always-approve`, claudecode `bypassPermissions`).
- No unit-test requirement for demo wiring (app glue); verify by importing `get_task_definitions` and asserting the cursor tasks appear (with and without a seed present).

---

### Task 1: `optio_demo/tasks/cursor.py` — seed-setup + seed-pinned iframe

**Files:** Create `packages/optio-demo/src/optio_demo/tasks/cursor.py`; modify `tasks/__init__.py`; Test mirroring grok's demo smoke test (same location/shape as `test_grok_tasks.py` if present).

**Interfaces:**
- Produces: `async def get_tasks(services) -> list[TaskInstance]` returning the seed-setup task always, plus the seed-pinned iframe task when a cursor seed exists.
- `_make_on_seed_saved(db, prefix, fw)` — mirror grok.

- [ ] **Step 1:** Read `tasks/grok.py` in full. Copy its structure into `tasks/cursor.py`, swapping: `create_cursor_task`/`CursorTaskConfig` (from `optio_cursor`), cursor `SEED_SETUP_PROMPT` (login instructions above), cursor seed listing (`optio_cursor.list_seeds`). Seed-setup task: `supports_resume=False`, `on_seed_saved=_make_on_seed_saved(...)`. Seed-pinned iframe task: `force=True`, `seed_id=<id>`, `supports_resume=True`, `auto_start=True`, `on_deliverable=…`.
- [ ] **Step 2:** Aggregate in `tasks/__init__.py`.
- [ ] **Step 3:** Smoke test: `get_task_definitions(services)` includes `cursor-seed-setup`; with a fake seed doc inserted, also the seed-pinned iframe task.
- [ ] **Step 4:** Run → PASS.
- [ ] **Step 5: Commit** `feat(optio-demo): cursor seed-setup + seed-pinned iframe demo tasks`.

---

### Task 2: Seed-pinned conversation demo task

**Files:** Modify `packages/optio-demo/src/optio_demo/tasks/cursor.py`; extend the smoke test

**Interfaces:** Add a second seed-pinned task with `mode="conversation"`, `conversation_ui=True`, `tool_verbosity="description-only"`, `seed_id=<id>`, `supports_resume=True` — mirroring grok's conversation demo.

- [ ] **Step 1:** Failing smoke assertion: with a seed present, `get_task_definitions` includes BOTH pinned cursor tasks (trio complete).
- [ ] **Step 2:** Run → FAIL.
- [ ] **Step 3:** Implement.
- [ ] **Step 4:** Run → PASS.
- [ ] **Step 5: Commit** `feat(optio-demo): cursor seed-pinned conversation demo task (completes the trio)`.

---

## Self-Review
- Guide Part 5 demo trio ↔ Tasks 1-2; mirrors grok's demos; registration verified.
- Cursor-specific login flow (NO_OPEN_BROWSER + `BROWSER:` surfacing; API-key alternative) spelled out in the seed-setup prompt.
- Conversation demo sequenced after Stage 6.
- No placeholders; reference pointers per task; names consistent (`get_tasks`, `_make_on_seed_saved`, `cursor-seed-setup`).
