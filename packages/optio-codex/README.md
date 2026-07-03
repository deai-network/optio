# optio-codex

Run OpenAI Codex as an `optio` task — either as the interactive TUI embedded
in the optio dashboard via an iframe widget served by `ttyd`, or in
conversation mode (codex app-server over stdio) rendered by the
`optio-conversation-ui` widget. Local or remote (SSH) workers.

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

### Filesystem sandbox

Beyond the per-task `HOME`, every codex tool subprocess is confined by
codex's **own native sandbox** — kernel-enforced (bundled bubblewrap
primary, Landlock+seccomp fallback on Linux), covering all shell/tool
commands the agent runs. optio-codex does **not** vendor claustrum for
this; it renders one resolved sandbox posture (`fs_allowlist.py` SSOT)
onto every launch surface: the interactive TUI argv, the `codex exec`
probe flags, and the `codex app-server` command line (`-c
sandbox_workspace_write.*` overrides; the sandbox *mode* is selected
out-of-band via `thread/start`'s `sandbox` enum — the 0.142.5 app-server
schema has no `thread/start.sandboxPolicy` object).

`fs_isolation=True` (the default) selects codex `workspace-write`: writes
are confined to the task workdir, `/tmp`, and any `rw` grants; **reads are
not restricted**. That read-open behaviour is a deliberate divergence from
optio-grok/optio-claudecode (whose sandboxes also deny reads) — so
`AllowedDir("…", "ro")` is a *documented no-op* on codex (an additive grant
that is already trivially satisfied), kept only for cross-wrapper config
portability. Only `AllowedDir("…", "rw")` changes behaviour, becoming a
`sandbox_workspace_write.writable_roots` entry (`~/` expands against the
real host home at launch). Network access is **OFF** by default (stricter
than the other wrappers, whose fs sandboxes never touch the network);
`network_access=True` relaxes it. `fs_isolation=False` runs codex
unconfined (`danger-full-access`).

Extra grants and the mode are cross-validated at config time — e.g.
`fs_isolation=True` with `sandbox="danger-full-access"`, or an `rw` grant
under an explicit `read-only` mode, raise `ValueError` rather than silently
mis-configuring the sandbox.

**No optio-side enforcement guard is needed.** codex fails **closed**: on a
host with no working sandbox mechanism (bubblewrap or Landlock), codex
errors or panics and the model's command never runs — it never falls back
to running unconfined (the only unconfined path is the explicit
`--dangerously-bypass-approvals-and-sandbox` opt-out, which optio-codex
never emits). This was verified empirically against codex-cli 0.142.5 (see
the Stage-8 probe verdict in `docs/2026-07-02-optio-codex-design.md`), so
optio-codex relies on that fail-closed guarantee instead of a launch-time
probe. As a free hardening bonus, `.codex/` and `.git/` under a writable
root stay read-only to the agent's shell, so the sandboxed agent cannot
rewrite its own per-task `auth.json` even though `CODEX_HOME` lives inside
the workdir.

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
lease loss); and `verify_and_refresh_seed` refreshes idle pooled seeds
**host-free** — a direct OpenAI OIDC `refresh_token` grant (no codex
process, no model turn, non-billable) that persists a fresh token before
the 8-day cliff, falling back to a headless `codex exec` probe only when
OIDC discovery is unreachable.

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

## Status — Stages 0–8 (feature-complete against the Appendix-A parity bar)

Verified against `docs/writing-agent-wrappers.md` Appendix A — 28 of 29 items
green (see `docs/2026-07-02-optio-codex-parity-audit.md` for per-item
`file:line` evidence). Suite: 188 passed, 4 skipped (the skips are the opt-in
real-binary tests, env-gated, never in the default suite).

Shipped:

- **Two run modes** — iframe/ttyd interactive TUI *and* conversation mode
  (codex app-server over stdio) with a conversation-ui widget
  (`optio-conversation-ui`, `widgetData.protocol = "codex"` → `CodexView`)
- `optio.log` keyword-protocol coordination + exit-status DONE/ERROR channel
- per-task `HOME` / `CODEX_HOME` isolation (tree provisioned at prepare)
- **filesystem isolation** via codex's native sandbox — default-ON
  `fs_isolation` (`workspace-write`), `extra_allowed_dirs` (`rw` grants →
  `writable_roots`), `network_access` (OFF by default); fail-closed, no
  optio-side guard needed (see the Filesystem sandbox section above)
- task-scoped teardown (per-task codex path; orphan-ttyd reap = crash-orphan
  rescue)
- `create_codex_task`, `run_codex_session`, `CodexTaskConfig`
- remote SSH workers (`ssh=SSHConfig(...)` routes to `RemoteHost`; verified
  end-to-end against a docker-sshd harness)
- resume / workdir snapshots: session-id-keyed relaunch (`codex resume <id>`,
  never `resume --last`), Mongo snapshot store (retention 5, single workdir
  GridFS blob carrying `home/.codex/sessions`), `resume.log` + AGENTS.md
  resume section synced to the snapshot exclude list
  (`workdir_exclude`; defaults drop `home/.codex/packages`, `*.sqlite*`,
  caches — never `home/.codex/sessions`); auto-resume-on-restart via
  optio-core (`supports_resume=True`)
- seeds: log-in-once capture (`on_seed_saved`) + `seed_id` consume with
  automatic workdir pre-trust; store-binding CRUD (`list_seeds` /
  `delete_seed` / `purge_seed`) over `{prefix}_codex_seeds`
- pool leases + in-session credential save-back (single-use rotating
  refresh token) with a teardown backstop; lease loss aborts the session
- host-free `verify_and_refresh_seed` — primary path is a direct OpenAI OIDC
  `refresh_token` grant (non-billable, no codex process) with rotated-token
  write-back and pool-status stamping; falls back to a headless `codex exec`
  probe (stdout-only verdict) only when OIDC discovery is unreachable
- optio-owned evictable binary cache (`OPTIO_CODEX_CACHE_DIR`), seeded from a
  host binary (`cp -L`) or real GitHub-release auto-download (pinned
  `rust-v0.142.5`, musl); per-task launch symlink preserved
- conversation-ui surface: permission gate, **inline** model switching, file
  upload/download (`optio-file:`), tool verbosity
- demo trio: seed-setup + seed-pinned iframe + seed-pinned conversation tasks
  (auto-appear via `fw.resync()`)

Remaining opt gaps (deliberate — see the parity audit):

- **session restore / rebase (scripted transcript reconstruction):** not
  shipped. codex's resume story is snapshot + `codex resume <id>` (shipped
  above); claudecode's scripted `transcript.py` rebase is engine-specific and
  has no codex analogue. optio-grok and optio-opencode also omit it — parity,
  not a regression.
- **at-rest encryption of the session blob:** the `encrypt`/`decrypt` seam is
  plumbed through but not activated (sessions pass `encrypt=None`) — identical
  posture to every other optio wrapper.

Not yet published to PyPI (first release is a separate, user-approved step).