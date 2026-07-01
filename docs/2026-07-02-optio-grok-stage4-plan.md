# optio-grok Stage 4 (Leases + Credential Save-back + Verify) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: superpowers:subagent-driven-development / executing-plans. Checkbox steps.

**Goal:** Make grok seeds durable and safely shareable: (a) a pool/lease so N seeds serve concurrent sessions without token-rotation collisions, (b) an in-session credential watcher that saves rotated `auth.json` back into the seed, and (c) a host-free `verify_and_refresh_seed` probe.

**Architecture:** Grok uses **rotating xAI refresh tokens** (single-use) — the same failure mode opencode was built for. So the **primary reference is `optio-opencode`** (`cred_watcher.py`, `verify.py`), with the generic pool/lease engine from `optio_agents.seeds` (`acquire`/`renew_lease`/`release`). claudecode is a secondary reference for the `SeedProvider` config plumbing.

**Tech Stack:** Python, `optio_agents.seeds` lease engine, grok binary probe, `auth.json` hashing.

## Global Constraints

- Branch `csillag/optio-grok`. Primary reference `optio-opencode/src/optio_opencode/{cred_watcher.py,verify.py}` + its seed/lease session wiring; generic `optio_agents/seeds.py` lease fns.
- Credential fingerprint = SHA-256 of `<workdir>/home/.grok/auth.json` (None if missing/empty/invalid) — mirror opencode `cred_fingerprint`.
- Watcher polls every 10s; on changed fingerprint, `seeds.refresh_seed`/`overwrite_seed_member` writes rotated `auth.json` back into the seed; renews the lease each tick; on lease loss, set `ctx.cancellation_flag` and abort.
- Final backstop save-back at teardown (load-bearing — grok's own auth write-back may be best-effort). Release lease AFTER the final save-back (opencode's deliberate ordering).
- `verify_and_refresh_seed`: engine-free, db-first; plant seed into a throwaway workdir + `GROK_HOME`, run one headless `grok -p "<probe>"` challenge-answer, verdict from stdout only (exit code diagnostic), write back rotated `auth.json` via `seeds.overwrite_seed_member`, stamp verify metadata, mark pool status alive/dead. Must run on a free/lease-held seed.
- `seed_id` may now be a `SeedProvider` callable holding a lease (acquire in `_prepare`, renew in watcher, release in teardown).
- Every task: failing test first, minimal impl, commit. Use `fake_grok.py` (extend with a probe/challenge-answer mode + a rotate-auth mode) — do NOT require the real grok binary or network.

---

### Task 1: `cred_watcher.py` — fingerprint + save-back + lease renewal

**Files:** Create `src/optio_grok/cred_watcher.py`; Test `tests/test_cred_watcher.py`

**Interfaces (mirror opencode):**
- `def cred_fingerprint(auth_json_bytes: bytes | None) -> str | None`
- `async def save_back_if_changed(host, seeds_engine, seed_id, *, workdir, last_fp) -> str | None` (returns new fp when it wrote back)
- `async def run_credential_watcher(host, ctx, *, seed_id, workdir, interval_s=10.0, lease=...)` — poll loop; renew lease; abort on lease loss.
- `def capture_gate_ok(...)` if opencode has one (auth.json present + non-empty).

- [ ] **Step 1: Failing test** — `cred_fingerprint(None) is None`; changing auth.json bytes changes the fingerprint; `save_back_if_changed` calls the engine's refresh exactly once when bytes change and not when unchanged (use a fake seeds engine + a temp `home/.grok/auth.json`).
- [ ] **Step 2:** Run → FAIL.
- [ ] **Step 3:** Implement adapting opencode `cred_watcher.py` (rename opencode→grok; auth.json path `home/.grok/auth.json`).
- [ ] **Step 4:** Run → PASS.
- [ ] **Step 5: Commit** `feat(optio-grok): credential watcher + save-back (Stage 4)`.

---

### Task 2: Lease wiring in `session.py` + `types.py`

**Files:** Modify `src/optio_grok/session.py`, `types.py`; Test extend `tests/test_session_seed.py` or new `tests/test_session_lease.py`

**Interfaces:**
- Consumes: Task 1.
- `_prepare`: if `seed_id` is callable, `acquire` a lease (holder=`ctx.process_id`) and resolve the concrete seed id; store the lease handle.
- Body/session: start `run_credential_watcher` as a background task (fresh sessions with a seed); cancel it at teardown.
- Teardown order: cancel watcher → final `save_back_if_changed` → `seeds.release(lease)` → seed capture/snapshot → cleanup. On lost lease mid-session, watcher sets `ctx.cancellation_flag`.

- [ ] **Step 1: Failing test** — a fresh seeded session with a rotate-auth `fake_grok` scenario: after the run, assert the seed's `auth.json` was updated (save-back fired) and the lease was released. Use a fake seeds/lease engine (no real Mongo lease needed if the generic engine is used with the `mongo_db` fixture — prefer the real engine).
- [ ] **Step 2:** Run → FAIL.
- [ ] **Step 3:** Implement lease acquire/renew/release + watcher lifecycle (adapt opencode session wiring).
- [ ] **Step 4:** Run → PASS; full suite green.
- [ ] **Step 5: Commit** `feat(optio-grok): pooled-lease seeds + save-back lifecycle (Stage 4)`.

---

### Task 3: `verify.py` — host-free verify/refresh

**Files:** Create `src/optio_grok/verify.py`; export `verify_and_refresh_seed` from `__init__.py`; Test `tests/test_verify.py`

**Interfaces:** `async def verify_and_refresh_seed(db, prefix, seed_id, *, ...) -> bool` (mirror opencode).

- [ ] **Step 1: Failing test** — plant a seed (auth.json) in Mongo; run `verify_and_refresh_seed` with a `fake_grok` probe that answers the challenge and rotates auth.json; assert verdict True, the seed's auth.json was overwritten with the rotated bytes, and `verify` metadata stamped. A probe that answers wrong → verdict False, pool status dead.
- [ ] **Step 2:** Run → FAIL.
- [ ] **Step 3:** Implement adapting opencode `verify.py` (probe = `grok -p "<PROBE_PROMPT>"`; `PROBE_ANSWER_RE`; write-back via `seeds.overwrite_seed_member`).
- [ ] **Step 4:** Run → PASS; full suite green.
- [ ] **Step 5: Commit** `feat(optio-grok): host-free verify_and_refresh_seed (Stage 4)`.

---

## Self-Review
- Spec Stage 4 (leases + save-back for rotating refresh_token + verify) ↔ Tasks 1-3.
- opencode chosen as primary reference (matching rotating-token model) — correct per the guide's "pick the closest reference per capability."
- Release-after-save-back ordering preserved; lease-loss aborts the session.
- No placeholders; tests + reference pointers per task; names consistent (`cred_fingerprint`, `save_back_if_changed`, `run_credential_watcher`, `verify_and_refresh_seed`).
