# optio-opencode Multi-User Parity: Binary Cache, HOME/XDG Isolation, Seeding, Auto-Start

This spec was written against the following baseline:

**Base revision:** `35c6fe7404a9e04e579a1f69847871bbe8d3ef10` on branch `main` (as of 2026-05-31T21:05:47Z)

## Summary

Bring optio-opencode to parity with optio-claudecode: (1) a shared, optio-owned
**binary cache** (no host-`~` pollution, not snapshotted, evictable), (2) per-task
**HOME/XDG isolation** so each task has its own opencode auth/config/data, (3)
**seeding** of the logged-in identity via the same generic `optio_agents.seeds`
engine, and (4) **auto-start** of a fresh session via the opencode server API.
The demo gains an opencode seed-setup task plus generated seed-pinned tasks,
mirroring the claudecode demo.

Source inspection of `~/deai/opencode` confirmed the two facts the design rested
on: opencode stores provider auth as a **plain file** (`$XDG_DATA_HOME/opencode/
auth.json`, mode 600 — not an OS keyring, so XDG-isolatable and seed-capturable),
and the server exposes **`POST /api/session/:sessionID/prompt`** (so auto-start is
a clean API call to the pre-created session).

One area is **deliberately deferred to live experiment, not designed up front**:
how opencode's provider login behaves with respect to a browser in `web`/headless
mode, and the resulting tension with our browser-suppression shims. See
"Experiments / open questions". The initial build ships browser **suppress**
everywhere (current behavior) and does not attempt redirect/OAuth handling.

This realizes the "opencode parity" follow-up the seed spec
(`docs/2026-05-29-optio-claudecode-seed-design.md`) named.

## Motivation

opencode today is single-tenant: its provider credentials live in the worker's
real `~/.local/share/opencode/auth.json`, shared across all tasks. A multi-user
web app where each user connects their own provider account is impossible. The
claudecode work already built (a) the generic seed engine, (b) the version-cache
pattern, and (c) the launch-env conventions — opencode adoption is manifest +
wiring + isolation, not a rewrite. Additionally, opencode installs to the host's
real `~/.local/bin` (same host-pollution defect claudecode just fixed), and lacks
unattended kickoff.

What opencode already has (unchanged): per-task config (`opencode.json` planted in
the workdir cwd) and per-task session state (`OPENCODE_DB` → `<taskdir>/
opencode.db`, with resume via export/import). Only the XDG auth/config/data tree
and the binary location are shared today.

## Goals

- opencode binary lives in an optio-owned cache (worker-side, outside any
  workdir, never snapshotted, evictable → re-download if gone); host `~/.local`
  never written.
- Per-task isolation of opencode's auth/config/data via `HOME` + `XDG_*` env so
  each task (hence each user) has its own provider identity.
- Seed capture/consume of the opencode identity (`auth.json` + config/plugins)
  through the **unchanged** generic `optio_agents.seeds` engine, with an
  `OPENCODE_SEED_MANIFEST` + `_opencode_seeds` suffix.
- Auto-start: a fresh session is kicked off unattended via the opencode API.
- Demo: opencode seed-setup task + generated seed-pinned tasks, like claudecode.
- Resume (export/import session DB) keeps working unchanged.

## Non-goals (v1)

- **Designing/building the OAuth-login browser handling.** It is experiment-gated
  (see below). Seed-setup ships with browser suppress; no redirect/hijack in v1.
- focus_mode (a Claude Code TUI concept; opencode's UI is the web widget).
- Version pinning / multi-version opencode cache (single current binary).
- Cache GC, cross-worker cache.
- Changing the resume mechanism (export/import stays).

## Architecture

### Layer 1 — Binary cache

- **Location (resolved on the worker, before isolation is applied):**
  `OPENCODE_CACHE_DIR` if set, else `${XDG_CACHE_HOME:-$HOME/.cache}/optio-opencode/
  bin`. Resolved via a shell echo on the host (RemoteHost-correct). The existing
  `opencode_install_dir` config field is repurposed as the cache-dir override.
- Holds the single `opencode` executable (optionally version-suffixed by the
  downloader; the cache just needs a working binary).
- `ensure_opencode_installed`: if a working binary exists in the cache → return
  its path; else download it into the cache (the existing manual-download path,
  retargeted from `~/.local/bin` to the cache). Evictable: absence → re-download.
- The binary is exec'd by **absolute path**; it is independent of the XDG
  isolation (no symlink-into-home trick needed — opencode is one binary with no
  autoupdate, unlike claude).
- Because the cache is outside any workdir, it is never in a snapshot.

### Layer 2 — HOME/XDG isolation

At launch (and for the export/import + any auth operation), set:
```
HOME=<workdir>/home
XDG_CONFIG_HOME=<workdir>/home/.config
XDG_DATA_HOME=<workdir>/home/.local/share
XDG_CACHE_HOME=<workdir>/home/.cache
```
Then opencode's config (`~/.config/opencode`), data (`~/.local/share/opencode`
incl `auth.json`), and caches resolve per-task. The binary cache dir is resolved
from the **worker's** real env *before* this isolation is applied (so the cache
stays shared/outside the home). `OPENCODE_DB` stays explicit (`<taskdir>/
opencode.db`) as today; `opencode.json` stays planted in the workdir cwd.

These env vars are added to the launch env built in `launch_opencode` (alongside
`OPENCODE_DB` + the suppress-shim env), and to the env passed to
`opencode_export`/`opencode_import` (so they read/write the isolated data/db).

### Layer 3 — Seeding (generic engine, unchanged)

- `OPENCODE_SEED_MANIFEST` (in optio-opencode):
  - `home_subdir = "home"`
  - `include = [".local/share/opencode/auth.json", ".config/opencode/opencode.json",
    ".config/opencode/plugins"]` (auth is essential; config/plugins make the
    environment self-contained)
  - `version = 1`, `consume_transform = None` (no cwd-rekey needed).
- `OPENCODE_SEED_SUFFIX = "_opencode_seeds"`.
- Re-export ergonomic `delete_seed`/`list_seeds`/`purge_seed` wrappers binding the
  suffix (mirrors optio-claudecode's `seed_manifest.py`).
- `OpencodeTaskConfig` gains `seed_id: str | None = None` and
  `on_seed_saved: Callable[[str], Awaitable[None] | None] | None = None`.
- Wire `optio_agents.seeds.merge_seed` (seeded-fresh, before launch) and
  `capture_seed` (in the teardown `finally`, before snapshot capture, gated on
  `on_seed_saved`) into `run_opencode_session` — same brackets/semantics as
  claudecode. Encryption reuses `session_blob_encrypt`/`_decrypt`.
- The seed merges into the isolated home (`<workdir>/home`) so the seeded auth
  lands at the isolated `.local/share/opencode/auth.json`.

### Layer 4 — auto-start

- `OpencodeTaskConfig.auto_start: bool = False`.
- On a **fresh** launch (not resume), after the server is ready and the session
  is pre-created, POST the kickoff prompt to the opencode API:
  `POST /api/session/<sessionID>/prompt` with the message **"Read AGENTS.md and
  execute the task it describes"**, using the existing server base URL + the
  BasicAuth token the session already computes. Suppressed on resume (the
  conversation continues; re-issuing would re-trigger).
- A small implementation spike confirms the exact request body shape for
  `POST /api/session/:id/prompt` against the cached opencode version.

### Layer 5 — Demo

- A demo-owned registry collection `{prefix}_demo_opencode_seeds`
  (`{seedId, name, createdAt}`), mirroring the claudecode demo.
- A static **"Setup opencode seed"** task — vanilla (no `seed_id`),
  `on_seed_saved` wired (records the seed + `resync`), browser **suppress** (v1).
  Operator connects a provider in the opencode web TUI, then stops the task; on
  teardown the identity is captured.
- Per recorded seed, a generated **"opencode demo — {name}"** task with `seed_id`
  baked in and `auto_start=True`.
- Crypto hooks left None (plaintext), matching the existing demo.

## Experiments / open questions (NOT in the initial build)

Resolve by live experiment during implementation; do not design up front:

1. **Does opencode attempt to open a browser during provider login when running
   in `web` (possibly headless) mode?** Provider auth methods are typed
   `["oauth", "api"]` (`~/deai/opencode/packages/opencode/src/provider/auth.ts`):
   `api` = token paste (no browser), `oauth` = an authorize-URL + code/callback
   flow. Observe whether the binary tries to spawn a browser or merely surfaces a
   URL in the TUI.
2. **Does our browser **suppress** shim interfere with login?** Suppress exists to
   swallow opencode's attempt to open *its own* web URL on launch. If login also
   routes through the same opener, suppress would swallow a login URL too.
3. **suppress ↔ redirect tension.** Redirecting (surfacing) opener calls would
   also surface the useless localhost-web URL. The shim mode is fixed at launch,
   not trivially switchable mid-session.

Method: launch opencode web in an isolated XDG home; perform a real provider login
(both an `api` provider and, if available, an `oauth` one) via the web TUI;
observe browser-spawn attempts, where `auth.json` lands, and whether suppress
blocks anything. **Outcome:** if login needs a surfaced URL, design redirect (or a
launch-time-suppress / login-time-redirect split) as a follow-up. v1 seed-setup
ships suppress and is validated for at least the token-paste path.

## File structure

**optio-opencode — modify:**
- `src/optio_opencode/host_actions.py` — binary-cache resolution + rewritten
  `ensure_opencode_installed` (cache, not `~/.local/bin`); `launch_opencode`
  adds the `HOME`/`XDG_*` isolation env; `opencode_export`/`opencode_import` get
  the isolation env.
- `src/optio_opencode/session.py` — wire seed merge/capture, `auto_start` (API
  POST after session pre-create, fresh-only), and thread the isolated env.
- `src/optio_opencode/types.py` — `seed_id`, `on_seed_saved`, `auto_start`;
  document `opencode_install_dir` as the binary-cache override.
- `src/optio_opencode/seed_manifest.py` — NEW: `OPENCODE_SEED_MANIFEST`,
  `OPENCODE_SEED_SUFFIX`, `delete_seed`/`list_seeds`/`purge_seed` wrappers.
- `src/optio_opencode/__init__.py` — export the seed surface.

**optio-demo — modify:**
- `src/optio_demo/tasks/opencode.py` — "Setup opencode seed" task +
  `on_seed_saved` (records `{prefix}_demo_opencode_seeds`, in-process `resync`) +
  generated seed-pinned `auto_start` tasks (mirror `tasks/claudecode.py`).

**Tests (optio-opencode):**
- Binary-cache unit tests (resolution; cache-hit reuse vs miss-download) against a
  fake host (mirror claudecode's `test_host_actions` cache tests).
- Isolation: assert `launch_opencode`'s env carries `HOME` + the three `XDG_*`
  pointing under `<workdir>/home`, and export/import env likewise.
- Seed config defaults; seed manifest shape; seeded-fresh merge + capture
  (reuse the fake_opencode + session-test infra, MongoDB-via-Docker).
- auto_start: fresh launch issues the prompt POST; resume does not (unit on the
  gating helper + an integration assertion via a recording fake).
- Snapshot excludes the cached binary (it's outside the workdir).

## Testing strategy notes

- Reuse the existing opencode session-test harness (`fake_opencode.py`, the
  `_supply_scenario` substitution, `OPTIO_SKIP_PREFLIGHT_TESTS=1`,
  MongoDB-via-Docker). The binary cache for tests is pointed at a fake via
  `opencode_install_dir` (a pre-populated cache), so no real download.
- The auto_start POST is exercised against the fake server / a recording stub;
  the real-endpoint body shape is confirmed by the implementation spike.

## Out of scope (deferred)

- OAuth-login browser handling (experiment first; follow-up).
- focus_mode for opencode.
- opencode version pinning / multi-version cache / cache GC.
