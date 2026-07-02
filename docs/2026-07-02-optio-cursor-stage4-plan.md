# optio-cursor Stage 4 (Leases + Credential Save-back + Verify) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: superpowers:subagent-driven-development / executing-plans. Checkbox steps.

**Goal:** Make cursor seeds durable and safely shareable: (a) a pool/lease so N seeds serve concurrent sessions without token-rotation collisions, (b) an in-session credential watcher that saves a rotated `auth.json` back into the seed, and (c) a host-free `verify_and_refresh_seed` probe.

**Architecture:** Cursor stores `accessToken`/`refreshToken` in `auth.json`; treat the refresh token as potentially rotating (the safe assumption — same failure mode opencode/grok were built for; actual rotation cadence is a probe-point, but save-back is correct either way). **Primary reference is `optio-grok`** (`cred_watcher.py`, `verify.py` — itself the opencode pattern), with the generic pool/lease engine from `optio_agents.seeds` (`acquire`/`renew_lease`/`release`).

**Tech Stack:** Python, `optio_agents.seeds` lease engine, fake-cursor probe, `auth.json` hashing.

## Global Constraints

- Branch `csillag/cursor`. Primary reference `optio-grok/src/optio_grok/{cred_watcher.py,verify.py}` + its lease session wiring; generic `optio_agents/seeds.py` lease fns.
- Credential fingerprint = SHA-256 of `<workdir>/home/.config/cursor/auth.json` (None if missing/empty/invalid) — mirror grok `cred_fingerprint`.
- Watcher polls every 10s; on changed fingerprint, writes rotated `auth.json` back into the seed via the engine (`overwrite_seed_member`); renews the lease each tick; on lease loss, set `ctx.cancellation_flag` and abort.
- Final backstop save-back at teardown. Release lease AFTER the final save-back (deliberate ordering, from opencode via grok).
- `verify_and_refresh_seed`: engine-free, db-first; plant seed into a throwaway workdir (per-task `HOME`/`XDG_CONFIG_HOME`), run one headless `cursor-agent -p "<probe>" --trust` challenge-answer, verdict from stdout only (exit code diagnostic), write back rotated `auth.json`, stamp verify metadata, mark pool status alive/dead. Must run on a free/lease-held seed.
- `seed_id` may now be a `SeedProvider` callable holding a lease (acquire in `_prepare`, renew in watcher, release in teardown).
- Every task: failing test first, minimal impl, commit (no Co-Authored-By). Use `fake_cursor.py` (extend with a probe/challenge-answer mode + a rotate-auth mode) — do NOT require the real cursor-agent binary or network.

---

### Task 1: `cred_watcher.py` — fingerprint + save-back + lease renewal

**Files:** Create `src/optio_cursor/cred_watcher.py`; Test `tests/test_cred_watcher.py`

**Interfaces (mirror grok):**
- `def cred_fingerprint(auth_json_bytes: bytes | None) -> str | None`
- `async def save_back_if_changed(host, seeds_engine, seed_id, *, workdir, last_fp) -> str | None` (returns new fp when it wrote back)
- `async def run_credential_watcher(host, ctx, *, seed_id, workdir, interval_s=10.0, lease=...)` — poll loop; renew lease; abort on lease loss.
- Capture gate helper if grok has one (auth.json present + non-empty).

- [ ] **Step 1: Failing test** — `cred_fingerprint(None) is None`; changing auth.json bytes changes the fingerprint; `save_back_if_changed` writes back exactly once when bytes change and not when unchanged (fake seeds engine + temp `home/.config/cursor/auth.json`).
- [ ] **Step 2:** Run → FAIL.
- [ ] **Step 3:** Implement adapting grok `cred_watcher.py` (rename; auth path `home/.config/cursor/auth.json`).
- [ ] **Step 4:** Run → PASS.
- [ ] **Step 5: Commit** `feat(optio-cursor): credential watcher + save-back (Stage 4)`.

---

### Task 2: Lease wiring in `session.py` + `types.py`

**Files:** Modify `src/optio_cursor/session.py`, `types.py`; Test `tests/test_session_lease.py`

**Interfaces:**
- Consumes: Task 1.
- `_prepare`: if `seed_id` is callable, `acquire` a lease (holder=`ctx.process_id`) and resolve the concrete seed id; store the lease handle.
- Body/session: start `run_credential_watcher` as a background task (fresh sessions with a seed); cancel it at teardown.
- Teardown order: cancel watcher → final `save_back_if_changed` → `seeds.release(lease)` → seed capture/snapshot → cleanup. On lost lease mid-session, watcher sets `ctx.cancellation_flag`.

- [ ] **Step 1: Failing test** — a fresh seeded session with a rotate-auth `fake_cursor` scenario: after the run, assert the seed's `auth.json` was updated (save-back fired) and the lease was released. Prefer the real generic engine with the `mongo_db` fixture.
- [ ] **Step 2:** Run → FAIL.
- [ ] **Step 3:** Implement lease acquire/renew/release + watcher lifecycle (adapt grok session wiring).
- [ ] **Step 4:** Run → PASS; full suite green.
- [ ] **Step 5: Commit** `feat(optio-cursor): pooled-lease seeds + save-back lifecycle (Stage 4)`.

---

### Task 3: `verify.py` — host-free verify/refresh

**Files:** Create `src/optio_cursor/verify.py`; export `verify_and_refresh_seed` from `__init__.py`; Test `tests/test_verify.py`

**Interfaces:** `async def verify_and_refresh_seed(db, prefix, seed_id, *, ...) -> bool` (mirror grok).

- [ ] **Step 1: Failing test** — plant a seed (auth.json) in Mongo; run `verify_and_refresh_seed` with a `fake_cursor` probe that answers the challenge and rotates auth.json; assert verdict True, the seed's auth.json overwritten with rotated bytes, verify metadata stamped. Wrong answer → verdict False, pool status dead.
- [ ] **Step 2:** Run → FAIL.
- [ ] **Step 3:** Implement adapting grok `verify.py` (probe = `cursor-agent -p "<PROBE_PROMPT>" --trust`; `PROBE_ANSWER_RE`; write-back via `seeds.overwrite_seed_member`).
- [ ] **Step 4:** Run → PASS; full suite green.
- [ ] **Step 5: Commit** `feat(optio-cursor): host-free verify_and_refresh_seed (Stage 4)`.

---

## Self-Review
- Spec Stage 4 (leases + save-back + verify) ↔ Tasks 1-3. Rotation cadence unpinned but save-back is rotation-agnostic (fires only on observed change); design probe-point noted, reconcile in the design doc if a live probe shows non-rotating tokens.
- Release-after-save-back ordering preserved; lease-loss aborts the session.
- `--trust` on the headless probe (cursor requires workspace trust in `--print` mode on an unseen dir).
- No placeholders; tests + grok pointers per task; names consistent (`cred_fingerprint`, `save_back_if_changed`, `run_credential_watcher`, `verify_and_refresh_seed`).
