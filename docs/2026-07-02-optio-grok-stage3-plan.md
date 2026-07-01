# optio-grok Stage 3 (Seeds) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: superpowers:subagent-driven-development / executing-plans. Checkbox steps.

**Goal:** Let an `optio-grok` task start a *fresh* session that is already logged-in/configured, by capturing and replanting a reusable **seed** — the answer to headless login.

**Architecture:** Adopt the generic `optio_agents.seeds` engine (as claudecode/opencode do). Add a grok seed manifest, `seed_id`/`on_seed_saved` config, capture-on-fresh-teardown, and merge-on-fresh-launch.

**Tech Stack:** Python, `optio_agents.seeds` (Mongo/GridFS), grok `~/.grok` auth.

## Global Constraints

- Branch `csillag/optio-grok`. Reference = `optio-claudecode/src/optio_claudecode/seed_manifest.py` + the seed branches of `session.py` (`_plant_session_content` merge, teardown capture) and `optio_agents/src/optio_agents/seeds.py` (engine: `SeedManifest`, `capture_seed`, `merge_seed`, `refresh_seed`).
- **Grok seed contents** (verified via `~/.grok` layout): the logged-in identity is `auth.json` (keyed `https://auth.x.ai::<uuid>`, holds `key`/`refresh_token`/`expires_at`/`oidc_*`) plus `config.toml`. Seed `home_subdir=".grok"` (relative to `<workdir>/home`), `include=["auth.json", "config.toml"]`. Optionally include `models_cache.json` (regenerable — exclude to keep seeds lean).
- **No cwd-rekey.** Unlike claude's `.claude.json` projects rekey, grok has no per-project trust file that needs rewriting → `consume_transform=None`.
- Cred-only manifest `GROK_CRED_MANIFEST` = `["auth.json"]` (for Stage-4 save-back + resume overlay). Suffix `_grok_seeds`.
- Seeds encrypted at rest by the engine; ids are opaque ObjectIds.
- Every task: failing test first, minimal impl, commit.

---

### Task 1: `seed_manifest.py`

**Files:** Create `src/optio_grok/seed_manifest.py`; export from `__init__.py`; Test `tests/test_seed_manifest.py`

**Interfaces:**
- Produces: `GROK_SEED_MANIFEST: SeedManifest`, `GROK_CRED_MANIFEST: SeedManifest`, `GROK_SEED_SUFFIX = "_grok_seeds"`, and thin `delete_seed`/`list_seeds`/`purge_seed` wrappers binding the suffix (mirror claudecode).
- Consumed by: Task 2.

- [ ] **Step 1: Failing test** — assert `GROK_SEED_MANIFEST.home_subdir == ".grok"`, `"auth.json" in GROK_SEED_MANIFEST.include`, `"config.toml" in GROK_SEED_MANIFEST.include`, `GROK_CRED_MANIFEST.include == ["auth.json"]`, `GROK_SEED_SUFFIX == "_grok_seeds"`.
- [ ] **Step 2:** Run → FAIL.
- [ ] **Step 3:** Implement adapting claudecode `seed_manifest.py` (drop `_rekey_claude_json_projects` / `consume_transform`; grok needs none).
- [ ] **Step 4:** Run → PASS.
- [ ] **Step 5: Commit** `feat(optio-grok): grok seed manifest (Stage 3)`.

---

### Task 2: Seed wiring in `session.py` + `types.py`

**Files:** Modify `src/optio_grok/session.py`, `types.py`; extend `tests/fake_grok.py`; Test `tests/test_session_seed.py`

**Interfaces:**
- Consumes: Task 1.
- `types.py`: add `seed_id: str | SeedProvider | None = None` (with `SeedProvider = Callable[[str], Awaitable[str]]` and `SeedUnavailableError`, mirroring claudecode — the callable/lease path is exercised in Stage 4), and `on_seed_saved: Callable[[str, str | None], Awaitable[None]] | None = None`.
- `session.py` `_prepare` (fresh only, not resume): if `seed_id`, resolve it (str → itself; callable → await it), then `seeds.merge_seed(host, GROK_SEED_MANIFEST, seed_id, dest=<workdir>/home)` BEFORE writing AGENTS.md, so grok launches already-authed.
- `session.py` teardown (fresh only, not resume): if `on_seed_saved`, capture with `seeds.capture_seed(host, GROK_SEED_MANIFEST, ...)`; then `await on_seed_saved(seed_id, model)` (model resolution can be `None` in Stage 3; the real value comes in a later stage). Guard capture on `auth.json` present (don't capture a logged-out identity).

- [ ] **Step 1: Failing test** (`test_session_seed.py`): (a) capture — run a fresh `seed` fake-grok scenario that writes a fake `home/.grok/auth.json`; assert `on_seed_saved` fired and a seed row exists. (b) consume — start a new fresh task with that `seed_id`; assert `home/.grok/auth.json` was planted before launch (fake-grok records its presence). Extend `fake_grok.py` with a `seed` scenario.
- [ ] **Step 2:** Run → FAIL.
- [ ] **Step 3:** Implement (adapt claudecode seed merge/capture; no rekey).
- [ ] **Step 4:** Run → PASS; full suite green.
- [ ] **Step 5: Commit** `feat(optio-grok): seed consume + capture (Stage 3)`.

---

## Self-Review
- Spec Stage 3 (logged-in fresh start, GROK_SEED_MANIFEST auth.json+config, headless-login answer) ↔ Tasks 1-2.
- No cwd-rekey correctly dropped (grok has no trust file).
- Lease/`SeedProvider` type introduced here but the pool/lease behavior lands in Stage 4 (this stage only supports a static `seed_id` string end-to-end).
- No placeholders; tests + claudecode pointers per task. Names consistent: `GROK_SEED_MANIFEST`, `GROK_CRED_MANIFEST`, `GROK_SEED_SUFFIX`, `seed_id`, `on_seed_saved`.
