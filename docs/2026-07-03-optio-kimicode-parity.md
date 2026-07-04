# optio-kimicode — Appendix A parity checklist

Status of the wrapper against the 30-row capability surface in
`docs/writing-agent-wrappers.md` Appendix A. This is the explicit ledger the
guide demands: *"A wrapper is not full-featured until row 30 passes for every
surface it ships."* Rows 1–29 are covered by the deterministic fake/unit suite
(**153+ passed** across `packages/optio-kimicode/tests/`); row 30 — the
real-`kimi`-binary confirmation of every surface — is scaffolded as an opt-in,
skip-if-no-binary suite (plan Task 6.3) and is a **TRACKED GAP** until run on a
provisioned, authenticated host.

## Update 2026-07-04 — first real-binary run (kimi-code 0.22.x)

kimi-code was installed and the wrapper driven against it for the first time,
which immediately surfaced a bug a green fake suite had hidden: the host's
``kimi`` was the unrelated Python **kimi-cli** (no ``server`` command), and Tier-1
install adopted it with no identity check → ``kimi server run`` exited before its
ready banner. Fixed in ``a4d45fb`` (``_is_kimicode`` identity probe gating
cache-hit + Tier-1; corrected Tier-2 install dir; same fix in ``resolve_real_kimi``)
and covered by a new pre-auth real-binary test (``test_real_server_ready.py``).

**Now REAL-verified** (ran against the real binary, no creds needed):
- Binary install/identity (row 15): Tier-2 vendor-install lands a runnable
  kimi-code; the kimi-cli name-collision is rejected. **DONE (real-verified).**
- iframe server startup (row 1): real ``kimi server run`` reaches its ready banner
  **plain AND under the production claustrum wrap** (ruling out a cursor-style
  ``/tmp`` sandbox regression). Full iframe render/input/DONE over a live task
  still needs an authed run.
- fs-isolation (row 25): server starts under real Landlock enforcement; the
  deny-enforcement leg is still the opt-in ``test_*_sandbox_enforce`` suite.

Everything requiring **auth** (device-code login, a full conversation turn, seed
capture/replant, resume) remains a tracked gap pending operator login.

## Status legend

| Status | Meaning |
|---|---|
| **DONE (fake-verified)** | Logic proven by the deterministic fake harness / unit tests (no real backend). |
| **DONE (real-verified)** | Proven against the real `kimi` binary on a provisioned host. |
| **TRACKED GAP (real binary)** | Implemented + fake-verified; real-`kimi` confirmation is an opt-in test that exists and skips cleanly until a real authed kimi is present. |
| **Inherited** | Provided generically by optio-core, not kimi-specific. |
| **N/A** | Capability not applicable to / not shipped for kimi. |

**Environment note.** No real authenticated kimi exists in this worktree/CI, so
every real-binary test skips with a precise reason (verified by
`tests/test_real_binary_gates.py`, which always runs). Nothing below is asserted
green on fakes alone where the guide requires the real binary — the remaining
real work is enumerated, not silent.

## Appendix A — rows 1–30

| # | Capability | Status | Evidence |
|---|---|---|---|
| 1 | iframe mode (`kimi web`) | DONE (fake-verified) | `test_session_iframe.py` |
| 2 | conversation mode (live `Conversation`) | DONE (fake-verified) | `test_session_conversation.py`, `test_conversation.py` |
| 3 | conversation-ui widget | DONE (fake-verified) | `optio-conversation-ui/src/kimicode/`, `__tests__/kimicode-events.test.ts` |
| 4 | `optio.log` keyword protocol | DONE (fake-verified) | `test_session_iframe.py`, `test_prompt.py` |
| 5 | local + remote (SSH) | DONE (fake-verified) | `test_session_remote.py` (docker-sshd) |
| 6 | readiness + monitoring + teardown | DONE (fake-verified) | `test_session_iframe.py`, `test_session_conversation.py` |
| 7 | resume / snapshots | DONE (fake-verified) | `test_snapshots.py`, `test_session_resume.py` |
| 7b | resume awareness (`resume.log` pull + pushed `RESUME_NOTICE`) | DONE (fake-verified) | `test_session_resume.py` |
| 8 | at-rest encryption of session blob | DONE (fake-verified) | `test_session_resume.py` (encrypted round-trip), `test_snapshots.py` |
| 9 | crash-orphan rescue | N/A | claudecode-specific (`_rescue_orphan_if_present`); not shipped for kimi |
| 10 | auto-resume-on-restart | Inherited | optio-core `auto_resume` |
| 11 | seeds (logged-in fresh start) | DONE (fake-verified) | `test_seed_manifest.py`, `test_verify.py` seed fixtures |
| 12 | pool / leases | DONE (fake-verified) | `SeedProvider` path in `session.py` / `cred_watcher.py`; `test_cred_watcher.py` |
| 13 | credential save-back (rotating tokens) | DONE (fake-verified) | `test_cred_watcher.py` |
| 14 | verify / refresh seed (host-free) | DONE (fake-verified) · real-refresh TRACKED | `test_verify.py` (mocked HTTP) → **gap:** `test_verify.py::test_real_seed_live_refresh_rotates_token` |
| 15 | binary cache + auto-install + symlink | **DONE (real-verified)** | `test_install.py` (fake) + Tier-2 vendor-install landed kimi-code 0.22.3 live; identity-check rejects the kimi-cli collision (`test_real_server_ready.py`) |
| 16 | HOME/XDG per-task isolation | DONE (fake-verified) | `test_host_actions.py`, `_isolation_env` |
| 17 | hooks (before/after/on_deliverable) | DONE (fake-verified) | `test_session_iframe.py` (deliverable), `test_host_actions.py` |
| 18 | prompt composition from SSOT | DONE (fake-verified) | `test_prompt.py` |
| 19 | permission gating | DONE (fake-verified) | `test_conversation.py` |
| 20 | model switching (inline `/model`) | DONE (fake-verified) | `test_models.py`, `test_conversation.py` |
| 21 | file upload | DONE (fake-verified) | `test_session_conversation.py` |
| 22 | file download (`optio-file:`) | DONE (fake-verified) | `test_session_conversation.py` |
| 23 | tool verbosity | DONE (fake-verified) | `test_conversation.py` / widgetData; `kimicode-events.test.ts` |
| 24 | session restore / rebase (scripted) | N/A | claudecode `transcript.py`-specific; snapshot-based resume (row 7) is kimi's mechanism |
| 25 | filesystem isolation (Landlock) | DONE (fake-verified) · real enforce TRACKED | `test_fs_allowlist.py` → **gap:** `test_fs_isolation_e2e.py` (+ the sandbox_enforce suites) |
| 26 | browser handling (redirect) | DONE (fake-verified) | `get_protocol(browser="redirect")`; `test_types.py` / session |
| 27 | headless-login strategy | DONE (fake-verified) · device-code TRACKED | seeds + redirect; **gap:** `test_real_session_e2e.py::test_real_first_login_device_code_captures_seed` |
| 28 | packaging + editable/release registration | DONE | `optio-demo/Makefile`, root `Makefile` `RELEASABLE_PY` (Task 6.1) |
| 29 | demo tasks (seed-setup + iframe + conversation) | DONE | `optio-demo/src/optio_demo/tasks/kimicode.py` (Task 6.2) |
| 30 | **real-binary E2E of every surface** | **TRACKED GAP (real binary)** | see breakdown below |

## Row 30 — real-binary E2E breakdown

Each surface has an opt-in, capability-probed test that runs the REAL `kimi`
binary and skips with a precise reason when a prerequisite is missing (no fake
pass). All currently skip (no real authed kimi here). The gate wiring itself is
verified by `tests/test_real_binary_gates.py` (always runs).

| Checklist item | Test | Opt-in flag(s) + probe | Status |
|---|---|---|---|
| 1 — iframe launch / render / DONE | `test_real_session_e2e.py::test_real_iframe_reaches_done` | `OPTIO_KIMICODE_REAL_E2E=1` + real kimi + creds | TRACKED GAP |
| 2 — conversation ACP handshake / stream / tool / turn | `test_conversation_sandbox_enforce.py` (handshake, non-billable) · `test_real_session_e2e.py::test_real_conversation_stream_tool_turn` (full turn) | `OPTIO_KIMICODE_SANDBOX_ENFORCE_TEST=1` (handshake) · `OPTIO_KIMICODE_REAL_E2E=1` (full) | TRACKED GAP |
| 3 — each surface with fs-isolation ON (real Landlock) | `test_fs_isolation_e2e.py` (allowlist confines, no kimi needed) · `test_sandbox_enforce.py` (iframe under Landlock) · `test_conversation_sandbox_enforce.py` (acp under Landlock) | `OPTIO_KIMICODE_FS_ENFORCE_TEST=1` (claustrum only) · `OPTIO_KIMICODE_SANDBOX_ENFORCE_TEST=1` (with kimi); both skip-if-no-Landlock/claustrum | TRACKED GAP |
| 4 — first-login device-code → creds land → seed captured | `test_real_session_e2e.py::test_real_first_login_device_code_captures_seed` | `OPTIO_KIMICODE_REAL_E2E=1` + `OPTIO_KIMICODE_REAL_DEVICE_LOGIN=1` (interactive: human completes the login URL) | TRACKED GAP |
| 5 — seed replant → fresh task already authed | `test_real_session_e2e.py::test_real_seed_replant_starts_authed` | `OPTIO_KIMICODE_REAL_E2E=1` + `OPTIO_KIMICODE_REAL_SEED_ID=<id>` | TRACKED GAP |
| 6 — resume picks up prior session | `test_real_session_e2e.py::test_real_resume_picks_up_prior_session` | `OPTIO_KIMICODE_REAL_E2E=1` + real kimi + creds | TRACKED GAP |
| 7 — remote SSH one surface | `test_real_session_e2e.py::test_real_remote_ssh_one_surface` | `OPTIO_KIMICODE_REAL_E2E=1` + `OPTIO_KIMICODE_REAL_SSH_HOST=<host>` (+ `_USER`/`_KEY_PATH`/`_PORT`) | TRACKED GAP |

### Deferred opt-in checks noted by earlier tasks (folded into row 30)

| Deferred check | Test | Opt-in flag(s) | Status |
|---|---|---|---|
| verify.py real-seed live-refresh (request shape round-trips against auth.kimi.com) | `test_verify.py::test_real_seed_live_refresh_rotates_token` | `OPTIO_KIMICODE_REAL_SEED_REFRESH=1` + real creds + online. **DESTRUCTIVE:** spends the single-use refresh token. | TRACKED GAP |
| Task 4.1 real vendor `install.sh` HOME-respect (live download lands a runnable kimi) | `test_install.py::test_real_vendor_install_lands_runnable_kimi` | `OPTIO_KIMICODE_REAL_INSTALL=1` + online | TRACKED GAP |
| conversation-ui real-ACP-wire capture-replay (real interleaved turn → reducer) | `optio-conversation-ui/src/__tests__/kimicode-real-wire.test.ts` | skip-if-fixture-absent (`fixtures/kimicode-acp-turn.json`, captured off a live kimi) | TRACKED GAP |

## Running the real-binary suite (on a provisioned host)

Prerequisites: a real `kimi` on PATH (or `~/.local/bin/kimi` / the optio cache),
an authenticated `~/.kimi-code/credentials/kimi-code.json`, and — for the
Landlock legs — a Linux kernel with the Landlock LSM plus a claustrum binary
(engine cache or `~/deai/claustrum` + a Go toolchain).

```bash
cd packages/optio-kimicode
# Landlock enforcement of the allowlist (no kimi/creds needed):
OPTIO_KIMICODE_FS_ENFORCE_TEST=1 ../../.venv/bin/python -m pytest tests/test_fs_isolation_e2e.py
# Real kimi under Landlock (iframe render + acp handshake; non-billable):
OPTIO_KIMICODE_SANDBOX_ENFORCE_TEST=1 ../../.venv/bin/python -m pytest \
  tests/test_sandbox_enforce.py tests/test_conversation_sandbox_enforce.py
# Full authed surfaces (BILLABLE — real model turns):
OPTIO_KIMICODE_REAL_E2E=1 ../../.venv/bin/python -m pytest tests/test_real_session_e2e.py
```

Each flag is required in addition to the probed capability, so a run is
reproducible and a missing prerequisite skips with a message naming exactly what
to supply. As surfaces are confirmed on a real host, flip their row from
**TRACKED GAP** to **DONE (real-verified)** here.
