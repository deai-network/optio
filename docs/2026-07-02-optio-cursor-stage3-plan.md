# optio-cursor Stage 3 (Seeds) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: superpowers:subagent-driven-development / executing-plans. Checkbox steps.

**Goal:** Let an `optio-cursor` task start a *fresh* session that is already logged-in/configured, by capturing and replanting a reusable **seed** — the answer to headless login.

**Architecture:** Adopt the generic `optio_agents.seeds` engine (as claudecode/opencode/grok do). Add a cursor seed manifest, `seed_id`/`on_seed_saved` config, capture-on-fresh-teardown, and merge-on-fresh-launch.

**Tech Stack:** Python, `optio_agents.seeds` (Mongo/GridFS), cursor auth file.

## Global Constraints

- Branch `csillag/cursor`. Reference = `optio-grok/src/optio_grok/seed_manifest.py` + the seed branches of grok's `session.py` (merge in `_prepare`, capture in teardown) and `optio_agents/src/optio_agents/seeds.py` (engine: `SeedManifest`, `capture_seed`, `merge_seed`, `refresh_seed`).
- **Cursor seed contents** (pinned empirically — planted-file + logout probe): the logged-in identity is `${XDG_CONFIG_HOME:-~/.config}/cursor/auth.json` (JSON, `accessToken`/`refreshToken`; `status` reads it, `logout` deletes it). Our launch env sets `XDG_CONFIG_HOME=<workdir>/home/.config`, so in-task the file is `<workdir>/home/.config/cursor/auth.json`. Also seed `.cursor/cli-config.json` (user prefs + permission rules).
- **Manifest layout (engine roots at `host.workdir + "/" + home_subdir`):** `home_subdir="home"`, includes prefixed relative to it: `CURSOR_CRED_MANIFEST.include = [".config/cursor/auth.json"]`; `CURSOR_SEED_MANIFEST.include = CURSOR_CRED_MANIFEST.include + [".cursor/cli-config.json"]`. This mirrors grok's reconciled layout (see grok design §7) — do NOT use `home_subdir=".cursor"`.
- **No cwd-rekey** (like grok/opencode): cursor auth/config are cwd-independent → `consume_transform=None`. (Cursor workspace-trust is bypassed headlessly with `--trust`; if a probe later shows a per-project trust store, add a transform then — not now.)
- Suffix `_cursor_seeds`. Seeds encrypted at rest by the engine; ids are opaque ObjectIds.
- Every task: failing test first, minimal impl, commit (no Co-Authored-By).

---

### Task 1: `seed_manifest.py`

**Files:** Create `src/optio_cursor/seed_manifest.py`; export from `__init__.py`; Test `tests/test_seed_manifest.py`

**Interfaces:**
- Produces: `CURSOR_SEED_MANIFEST: SeedManifest`, `CURSOR_CRED_MANIFEST: SeedManifest`, `CURSOR_SEED_SUFFIX = "_cursor_seeds"`, and thin `delete_seed`/`list_seeds`/`purge_seed` wrappers binding the suffix (mirror grok).
- Consumed by: Task 2.

- [ ] **Step 1: Failing test** — assert `CURSOR_SEED_MANIFEST.home_subdir == "home"`, `".config/cursor/auth.json" in CURSOR_SEED_MANIFEST.include`, `".cursor/cli-config.json" in CURSOR_SEED_MANIFEST.include`, `CURSOR_CRED_MANIFEST.include == [".config/cursor/auth.json"]`, `CURSOR_SEED_SUFFIX == "_cursor_seeds"`.
- [ ] **Step 2:** Run → FAIL.
- [ ] **Step 3:** Implement adapting grok `seed_manifest.py` verbatim (rename, adjust includes; `consume_transform=None`).
- [ ] **Step 4:** Run → PASS.
- [ ] **Step 5: Commit** `feat(optio-cursor): cursor seed manifest (Stage 3)`.

---

### Task 2: Seed wiring in `session.py` + `types.py`

**Files:** Modify `src/optio_cursor/session.py`, `types.py`; extend `tests/fake_cursor.py`; Test `tests/test_session_seed.py`

**Interfaces:**
- Consumes: Task 1.
- `types.py`: add `seed_id: str | SeedProvider | None = None` (with `SeedProvider = Callable[[str], Awaitable[str]]` and `SeedUnavailableError`, mirroring grok — the callable/lease path is exercised in Stage 4), and `on_seed_saved: Callable[[str, str | None], Awaitable[None]] | None = None`.
- `session.py` `_prepare` (fresh only, not resume): if `seed_id`, resolve it (str → itself; callable → await it), then `seeds.merge_seed(host, CURSOR_SEED_MANIFEST, seed_id, ...)` BEFORE writing AGENTS.md, so cursor launches already-authed. NOTE the interplay with Stage-0 cli-config planting: seed merge first, then only plant a generated `cli-config.json` if the config sets permission rules AND the seed didn't provide one (or deep-merge the permission rules into the seeded file — follow whichever grok does for its planted config).
- `session.py` teardown (fresh only, not resume): if `on_seed_saved`, capture with `seeds.capture_seed(host, CURSOR_SEED_MANIFEST, ...)`; then `await on_seed_saved(seed_id, model)` (model may be `None` in Stage 3). Guard capture on `.config/cursor/auth.json` present (don't capture a logged-out identity).

- [ ] **Step 1: Failing test** (`test_session_seed.py`): (a) capture — run a fresh `seed` fake-cursor scenario that writes a fake `home/.config/cursor/auth.json`; assert `on_seed_saved` fired and a seed row exists. (b) consume — start a new fresh task with that `seed_id`; assert `home/.config/cursor/auth.json` was planted before launch (fake-cursor records its presence). Extend `fake_cursor.py` with a `seed` scenario.
- [ ] **Step 2:** Run → FAIL.
- [ ] **Step 3:** Implement (adapt grok seed merge/capture; no rekey).
- [ ] **Step 4:** Run → PASS; full suite green.
- [ ] **Step 5: Commit** `feat(optio-cursor): seed consume + capture (Stage 3)`.

---

## Self-Review
- Spec Stage 3 (logged-in fresh start, CURSOR_SEED_MANIFEST auth.json+cli-config, headless-login answer) ↔ Tasks 1-2.
- Cred path empirically pinned (design probe-point 1 resolved); manifest uses the reconciled `home_subdir="home"` layout from day one.
- Lease/`SeedProvider` type introduced here but pool/lease behavior lands in Stage 4 (this stage only supports a static `seed_id` string end-to-end).
- No placeholders; tests + grok pointers per task. Names consistent: `CURSOR_SEED_MANIFEST`, `CURSOR_CRED_MANIFEST`, `CURSOR_SEED_SUFFIX`, `seed_id`, `on_seed_saved`.
