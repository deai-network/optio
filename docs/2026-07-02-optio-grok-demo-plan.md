# optio-grok Demo Tasks Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: superpowers:subagent-driven-development / executing-plans. Checkbox steps.
> **Depends on:** Stage 3 (seeds — done) and Stage 6 (conversation — done) before the conversation demo can be added. The seed-setup + iframe demo can land as soon as Stage 3 is in.

**Goal:** Ship the grok demo trio in `optio-demo` so the wrapper is exercised in the real dashboard: one **seed-setup** task + two **seed-pinned** run tasks (one iframe, one conversation) driven from the captured identity — matching the claudecode/opencode demo trio (guide Part 5).

**Architecture:** Add `optio_demo/tasks/grok.py` exposing `async def get_tasks(services) -> list[TaskInstance]`, aggregated in `optio_demo/tasks/__init__.py`. Mirror `optio_demo/tasks/claudecode.py` exactly, swapping the factory/config/seed manifest to grok's.

**Tech Stack:** Python, `optio_grok`, `optio_demo` task framework.

## Global Constraints

- Branch `csillag/optio-grok`. Reference = `packages/optio-demo/src/optio_demo/tasks/claudecode.py` (seed-setup task, seed-pinned iframe + conversation tasks, `_make_on_seed_saved`), and `tasks/opencode.py` for the conversation demo shape. Aggregation in `tasks/__init__.py`.
- Registration (from the guide Part 5) is already partly done — Stage 0 added `optio-grok` to the demo Makefile/pyproject + root `RELEASABLE_PY`. Verify those are present; add if missing.
- `ssh` defaults to None (local) unless `OPTIO_GROK_DEMO_SSH_HOST` is set (mirror claudecode's `OPTIO_CLAUDECODE_DEMO_SSH_HOST`).
- Seed-pinned tasks appear only after a seed exists (query the grok seed collection via `list_seeds`), mirroring claudecode's gating.
- No unit-test requirement for demo wiring (it's app glue); verify by importing `get_task_definitions` and asserting the grok tasks appear (with and without a seed present).

---

### Task 1: `optio_demo/tasks/grok.py` — seed-setup + seed-pinned iframe

**Files:** Create `packages/optio-demo/src/optio_demo/tasks/grok.py`; modify `tasks/__init__.py`; Test `packages/optio-demo/tests/test_grok_tasks.py` (if the demo package has a tests dir; else a smoke import check)

**Interfaces:**
- Produces: `async def get_tasks(services) -> list[TaskInstance]` returning the seed-setup task always, plus the seed-pinned iframe task when a grok seed exists.
- `_make_on_seed_saved(db, prefix, fw)` — mirror claudecode.

- [ ] **Step 1:** Read `tasks/claudecode.py` in full. Copy its structure into `tasks/grok.py`, swapping: `create_grok_task`/`GrokTaskConfig` (from `optio_grok`), grok `SEED_SETUP_PROMPT`, grok seed listing (`optio_grok.list_seeds` bound to the grok suffix). Seed-setup task: `supports_resume=False`, `on_seed_saved=_make_on_seed_saved(...)`, `consumer_instructions=SEED_SETUP_PROMPT`. Seed-pinned iframe task: `permission_mode="bypassPermissions"`, `seed_id=<id>`, `supports_resume=True`, `auto_start=True`, `on_deliverable=…`.
- [ ] **Step 2:** Aggregate: add `from optio_demo.tasks.grok import get_tasks as grok_tasks` and `*await grok_tasks(services)` in `tasks/__init__.py`.
- [ ] **Step 3:** Smoke test: `get_task_definitions(services)` includes `grok-seed-setup`; with a seed present (insert a fake seed doc), it also includes the seed-pinned iframe task.
- [ ] **Step 4:** Run the smoke test → PASS.
- [ ] **Step 5: Commit** `feat(optio-demo): grok seed-setup + seed-pinned iframe demo tasks`.

---

### Task 2: Seed-pinned conversation demo task

**Files:** Modify `packages/optio-demo/src/optio_demo/tasks/grok.py`; extend the smoke test

**Interfaces:** Add a second seed-pinned task with `mode="conversation"`, `conversation_ui=True`, `tool_verbosity="description-only"`, `seed_id=<id>`, `supports_resume=True` — mirroring `tasks/claudecode.py`'s conversation demo. Reference `tasks/opencode.py` conversation demo for any engine-neutral bits.

- [ ] **Step 1:** Failing smoke assertion: with a seed present, `get_task_definitions` includes BOTH the iframe and the conversation seed-pinned grok tasks (the trio: setup + 2 pinned).
- [ ] **Step 2:** Run → FAIL.
- [ ] **Step 3:** Implement the conversation demo task.
- [ ] **Step 4:** Run → PASS.
- [ ] **Step 5: Commit** `feat(optio-demo): grok seed-pinned conversation demo task (completes the trio)`.

---

## Self-Review
- Guide Part 5 demo trio (seed-setup + iframe + conversation seed-pinned) ↔ Tasks 1-2.
- Mirrors claudecode/opencode demos; registration verified (Stage 0 wired Makefile/pyproject).
- Conversation demo depends on Stage 6 — sequence accordingly.
- No placeholders; reference pointers per task; names consistent (`get_tasks`, `_make_on_seed_saved`, `grok-seed-setup`).
