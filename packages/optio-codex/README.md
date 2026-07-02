# optio-codex

Run OpenAI Codex as an `optio` task — local subprocess with the interactive
TUI embedded in the optio dashboard via an iframe widget served by `ttyd`.

## Install

```bash
pip install optio-codex
```

Requires Python 3.11+. Pulls `optio-core`, `optio-host`, `optio-agents`,
`asyncssh`, and `aiohttp`.

## What it does

optio-codex launches `codex` inside a detached tmux session, serves the
TUI over `ttyd`, and coordinates with the host harness through the
`optio.log` keyword channel (STATUS / DELIVERABLE / DONE / ERROR). The
agent reads its task from an `AGENTS.md` file planted in the workdir.
The tmux+ttyd machinery follows the optio-claudecode pattern; browser
handling deliberately differs (`suppress` — codex login is handled via
env/API key or interactively, not via surfaced browser URLs).

### Isolation

Each task runs under an isolated `HOME` (`<workdir>/home`, created at
prepare time) with `CODEX_HOME` pointing at `<workdir>/home/.codex`, so
the operator's real `~/.codex` identity and config do not leak into the
session. The codex binary is launched via a per-task path
(`<workdir>/home/.local/bin/codex`), so teardown only ever kills this
task's process.

### Authentication

The primary mechanism is **seeds**: log in once, reuse the identity for
every later task. Run the setup task and log into codex interactively in
the embedded terminal (`codex login --device-auth`, or `codex login
--with-api-key`); on teardown the session's `home/.codex` (`auth.json` +
`config.toml`) is captured as a reusable seed and surfaced through the
`on_seed_saved` callback. A later task started with
`CodexTaskConfig(seed_id=…)` merges that stored identity into its fresh
workdir before launch, so codex starts already logged-in — and the new
workdir is pre-trusted automatically (`[projects."<workdir>"] trust_level =
"trusted"` appended to `config.toml`), so codex never prompts about an
untrusted directory. `seed_id` also accepts a `SeedProvider` callable that
leases a seed from a pool (the task's `process_id` is the lease holder).

Store-binding CRUD helpers (`list_seeds` / `delete_seed` / `purge_seed`)
operate over the `{prefix}_codex_seeds` collection.

**Credential rotation (why a seeded session does more than merge-once):**
codex's ChatGPT-mode `auth.json` carries a *single-use rotating* refresh
token (openai/codex#15410) — codex proactively refreshes it after 8 days
and on any 401, rewriting `auth.json` in place, and a used refresh token
invalidates every other copy. So a seeded session runs an in-session
**credential watcher** that saves the rotated `auth.json` back into the
seed (plus a final teardown backstop); pooled seeds take a **lease** (one
live lineage per seed — the watcher renews it and aborts the session on
lease loss); and `verify_and_refresh_seed` probes idle pooled seeds
headlessly to prove liveness and persist a fresh token before the 8-day
cliff.

Fallbacks without a seed: pass an API key into the session env
(`CodexTaskConfig(env={"OPENAI_API_KEY": …})`) or log in interactively
(`codex login`) inside the embedded terminal.

### Binary provisioning

The codex binary is resolved through an optio-owned, evictable cache on the
worker — `OPTIO_CODEX_CACHE_DIR`, else
`${XDG_CACHE_HOME:-~/.cache}/optio-codex/bin` — resolved host-side, so it is
correct on a remote SSH worker and never lives under a task workdir or the
operator's `~/.codex`. On a cache miss the cache is seeded from a host
`codex` on `PATH` (`cp -L` deref → a stable copy), or, when none exists, the
pinned release is auto-downloaded (`rust-v0.142.5`, static musl,
`{x86_64,aarch64}-unknown-linux-musl`). The per-task launch symlink
(`<workdir>/home/.local/bin/codex`) is preserved and points into the cache,
so task-scoped teardown stays unaffected.

## Status — Stages 0–5 (iframe, remote SSH, resume, seeds, binary cache)

Shipped:

- iframe/ttyd mode on the local host
- `optio.log` keyword-protocol coordination + exit-status DONE/ERROR channel
- per-task `HOME` / `CODEX_HOME` isolation (tree provisioned at prepare)
- task-scoped teardown (per-task codex path; orphan-ttyd reap)
- `create_codex_task`, `run_codex_session`, `CodexTaskConfig`
- demo task in optio-demo (`Codex demo — iframe`)
- remote SSH workers (`ssh=SSHConfig(...)` routes to `RemoteHost`; verified
  end-to-end against a docker-sshd harness)
- resume / workdir snapshots: session-id-keyed relaunch (`codex resume <id>`,
  never `resume --last`), Mongo snapshot store (retention 5, single workdir
  GridFS blob carrying `home/.codex/sessions`), `resume.log` + AGENTS.md
  resume section synced to the snapshot exclude list
  (`workdir_exclude`; defaults drop `home/.codex/packages`, `*.sqlite*`,
  caches — never `home/.codex/sessions`)
- seeds: log-in-once capture (`on_seed_saved`) + `seed_id` consume with
  automatic workdir pre-trust; store-binding CRUD (`list_seeds` /
  `delete_seed` / `purge_seed`) over `{prefix}_codex_seeds`
- pool leases + in-session credential save-back (single-use rotating
  refresh token) with a teardown backstop; lease loss aborts the session
- engine-free `verify_and_refresh_seed` (headless `codex exec` probe,
  stdout-only verdict, rotated-token write-back, pool-status stamping)
- optio-owned evictable binary cache (`OPTIO_CODEX_CACHE_DIR`), seeded from a
  host binary (`cp -L`) or real GitHub-release auto-download (pinned
  `rust-v0.142.5`, musl); per-task launch symlink preserved
- demo: seed-setup + seed-pinned iframe tasks (auto-appear via `fw.resync()`)

Still missing (tracked gaps toward Appendix A parity, staged plans D–E):

- crash-orphan rescue (snapshot capture for a crashed engine)
- conversation mode (`codex exec --json` / app-server) + conversation-ui
  widget (Plan D)
- model switching; file upload/download; tool verbosity (Plan D)
- seed-pinned conversation demo (completes the demo trio) (Plan D)
- filesystem isolation (Landlock / claustrum) reconciled with codex's native
  sandbox (Plan E)
- PyPI release (Plan E)