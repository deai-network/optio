# Design: `optio-grok` — a full-featured wrapper for Grok Build

- **Date:** 2026-07-02
- **Status:** Approved (goal-driven build)
- **Branch:** `csillag/optio-grok`
- **Guide:** follows `docs/writing-agent-wrappers.md`; this is the agent-specific
  spec that guide mandates.
- **Primary reference:** `optio-claudecode` (Grok is its closest shape).

## 1. Target profile (empirical)

Grok Build, binary `grok` (v0.2.77, `~/.grok/bin/grok`), on PATH. Profiled by
probing `grok --help` and subcommands on this host.

**Grok ≈ Claude Code**, with extra surfaces:

| Axis | Grok mechanism |
|---|---|
| Interactive UI | TUI (default); `dashboard` subcommand = agent-native session overview (TUI, not web) |
| Headless single-turn | `-p/--single`, `--output-format plain\|json\|streaming-json`, `--prompt-file`, `--json-schema` |
| Headless conversation | `grok agent stdio` (bidirectional over stdio) — **primary**; `grok agent serve` (WebSocket server, `127.0.0.1:2419`, `--secret`/`GROK_AGENT_SECRET`) — alternative; `grok agent headless` (over xAI relay) |
| Shared backend | `leader` daemon (`~/.grok/leader.sock`, `--leader-socket`); `[cli] use_leader` config |
| Resume | `--continue`, `--resume [ID]`, `--session-id`, `--fork-session`, `--restore-code`; `sessions` / `export` / `import`; `~/.grok/sessions/*.sqlite` |
| Auth | `login --oauth` / `login --device-auth` (device-code, headless/remote); `~/.grok/auth.json` keyed `https://auth.x.ai::<uuid>` with `key`, **`refresh_token`**, `expires_at`, `oidc_*`, `principal_id`, `email` |
| Config dir | `~/.grok` (relocatable via **`GROK_HOME`**); `config.toml` |
| Instructions file | **`AGENTS.md`** (`fileType: agents_md`); also claude-compat (reads `~/.claude/CLAUDE.md`, claude `settings.json`, claude hooks) |
| Permissions | `--permission-mode {default,acceptEdits,auto,dontAsk,bypassPermissions,plan}`, `--allow`/`--deny`, `--tools`/`--disallowed-tools`, `--always-approve` |
| Model | `--model`, `--effort`, `--reasoning-effort`; `models` subcommand; `~/.grok/models_cache.json` |
| Sandbox | native `--sandbox <PROFILE>` / `GROK_SANDBOX` |
| Extras | MCP, skills, plugins/marketplace, cross-session memory, `--worktree`, `--best-of-n`, `--check`, `--system-prompt-override`, `--rules` |

### Capability → optio surface mapping

| Agent capability | optio surface | Grok mechanism |
|---|---|---|
| headless programmatic API | **conversation mode** (drives conversation-ui) | `grok agent stdio` |
| TUI only (no web SPA) | **iframe via ttyd** | default TUI |
| interactive login | fallback / seed capture | `grok login --device-auth` |

Conversation mode is the primary surface; ttyd-embedded TUI is the fallback for
operations the headless surface can't do (notably first-time login). No web-SPA
surface (grok's `serve` is a raw WebSocket, not a browsable UI).

## 2. Key decisions

1. **Adapt `optio-claudecode`.** Grok matches it on nearly every axis (stdio
   stream conversation, ttyd TUI, `~/.grok`≈`~/.claude` config dir with rotating
   refresh tokens, claude-style permission modes, resume via `--continue`). The
   claudecode wrapper is the file-by-file reference; opencode is the secondary
   reference where grok's `AGENTS.md` prompt convention matches opencode's.
2. **Conversation transport = `grok agent stdio`.** Simplest, matches
   `ClaudeCodeConversation`'s proven stdio pattern. The exact wire framing
   (message JSON shape, permission/interrupt control messages) is not exposed by
   `--help` and will be pinned against a live `grok agent stdio` probe during
   Stage 6, then documented. `agent serve` (WebSocket) is a documented alternative
   if stdio proves insufficient.
3. **Isolation must neutralize claude-compat.** Setting `GROK_HOME=<workdir>/home/.grok`
   is necessary but **not sufficient**: `grok inspect` proved grok also ingests the
   operator's `~/.claude/CLAUDE.md`, claude `settings.json`, and claude hooks. The
   wrapper must also point/blank the claude-compat sources (e.g. `CLAUDE_CONFIG_DIR`
   into the workdir, as claudecode already does) so no operator config leaks into a
   task. This is a first-class isolation requirement, verified at Stage 5.
4. **Force per-task leader isolation.** Pass `--no-leader` (or a per-task
   `--leader-socket` under the workdir) so concurrent tasks never share one grok
   backend. Never touch the host default `~/.grok/leader.sock`.
5. **Instructions file = `AGENTS.md`.** Prompt composition mirrors opencode's
   `compose_agents_md` (grok reads `AGENTS.md`), reusing the shared
   `optio_agents.prompt` SSOT for the keyword-protocol docs + resume section.
6. **Headless login = device-auth + seeds.** `grok login --device-auth` gives a
   device code for headless/remote login — no OAuth-loopback-redirect rewrite
   needed (unlike claudecode). Primary path stays: seed a logged-in identity so
   headless never logs in.
7. **Filesystem isolation: start with claustrum** (uniform optio-level guarantee,
   reuse claudecode's `fs_allowlist.py`/`_build_claustrum_wrap`); grok's native
   `--sandbox` is noted as a future alternative/defense-in-depth, not the primary.

## 3. Package shape

`packages/optio-grok/`, Python `optio-grok`, `src/optio_grok/`. Deps:
`optio-core`, `optio-host`, `optio-agents`, `asyncssh`, `aiohttp`. Module layout
mirrors `optio-claudecode` (adapt, don't copy blindly):

- `session.py` — `create_grok_task` factory + `run_grok_session`; iframe (ttyd) and
  conversation bodies; resume/snapshot/seed wiring.
- `host_actions.py` — grok binary cache/install, ttyd install, tmux/ttyd argv,
  launch env (`GROK_HOME`, claude-compat neutralization, `--no-leader`),
  `send_text_to_grok` (tmux), teardown.
- `types.py` — `GrokTaskConfig` (mirror `ClaudeCodeTaskConfig` fields; grok-specific:
  `effort`/`reasoning_effort`, `sandbox`, `no_leader`).
- `conversation.py` — `GrokConversation` implementing `optio_agents.conversation.Conversation`
  over `grok agent stdio`.
- `conversation_listener.py` — dashboard SSE listener (adapt claudecode's).
- `prompt.py` — `compose_agents_md` (AGENTS.md, opencode-style, SSOT docs).
- `seed_manifest.py` — `GROK_SEED_MANIFEST` / `GROK_CRED_MANIFEST` (auth.json +
  config.toml + relevant `~/.grok` state), suffix `_grok_seeds`.
- `cred_watcher.py` — refresh-token save-back + lease renewal.
- `verify.py` — host-free `verify_and_refresh_seed` (device/refresh probe).
- `snapshots.py` — Mongo `{prefix}_grok_session_snapshots` (session export/import +
  workdir tar).
- `fs_allowlist.py` — claustrum grants (adapt from claudecode).
- UI: add a `grok` reducer + view to `optio-conversation-ui` (`src/grok/`), gated by
  `widgetData.protocol = "grok"`. Reducer maps `grok agent stdio` events →
  `ChatItem`; may start by reusing claudecode's reducer if the wire shape is close.

## 4. Staged build path (per the guide)

| Stage | Goal | Grok specifics |
|---|---|---|
| 0 MVP | task runs one mode + DONE/ERROR, local | ttyd-embedded TUI OR `-p` batch; `AGENTS.md`; `GROK_HOME` |
| 1 Remote/SSH | same over SSH | generic Host; no new work |
| 2 Resume | relaunch picks up session | `--continue` + `export`/`import`; snapshots |
| 3 Seeds | logged-in fresh start | `GROK_SEED_MANIFEST` (auth.json+config); solves headless login |
| 4 Leases + save-back + verify | rotating refresh_token durability | `cred_watcher` on auth.json; `verify_and_refresh_seed` |
| 5 Cache + HOME/XDG isolation | evictable binary cache; per-task identity | grok binary cache; `GROK_HOME` + **claude-compat neutralization** |
| 6 Conversation + UI | live `Conversation` + chat widget | `grok agent stdio` transport; `src/grok/` reducer+view; `--no-leader` |
| 7 Frontend parity | permissions, model switch, file up/down, verbosity | claude-style permission modes; `--model` restart or stdio control |
| 8 fs-isolation | Landlock sandbox | claustrum (native `--sandbox` as alt) |

Demo (per the guide's Part 5): a grok **seed-setup** task + **two seed-pinned** run
tasks — one iframe, one conversation — in `optio-demo`, mirroring the
claudecode/opencode demo trio. The iframe demo lands with Stage 0/3; the
conversation demo with Stage 6.

## 5. Non-goals (v1)

- `agent serve` (WebSocket) conversation transport — stdio first; revisit only if
  stdio is insufficient.
- `leader`-shared-backend multiplexing — forced off per task.
- grok-native `--sandbox` as the primary isolation — claustrum first.
- `--best-of-n`, `--worktree`, cross-session `memory`, plugins/marketplace — grok
  features not required for the optio task surface; leave to the agent's own config.

## 6. Success criteria

- Parity with `optio-claudecode` across Appendix A of the guide (staged; gaps
  tracked, not silent).
- A demo grok task runs locally and over SSH, in both ttyd-iframe and conversation
  modes, resumes correctly, and uses a seeded logged-in identity with refresh-token
  save-back.
- No operator config leaks into a task (verified: `grok inspect` inside a task sees
  only workdir + planted config, never `~/.claude` or the host `~/.grok`).

## 7. Implementation reconciliation (as shipped)

Deviations from the design above, decided during the staged build (all verified,
tests green — grok wrapper 108 passed/1 skipped, conversation-ui 110 TS passed):

- **Filesystem isolation: grok NATIVE `--sandbox`, not claustrum** (reverses §2
  Decision 7 / the §5 non-goal). Grok ships its own Landlock sandbox. optio-grok
  plants a **custom** `[profiles.optio]` in `<workdir>/home/.grok/sandbox.toml`
  (`extends="strict"`, `read_write=[workdir,/tmp,/var/tmp,+extras]`, no `deny` →
  Landlock-only, no bubblewrap) and launches `--sandbox optio`. Custom profiles
  fail-CLOSED (built-ins fail-open), giving the required guarantee with zero
  claustrum cross-compile/install machinery. See Stage 8 plan.
- **Model switching: INLINE, not restart.** Grok's ACP supports
  `session/set_model {sessionId, modelId}` mid-session (verified by live probe),
  so `GrokConversation.request_model_change` switches in place — no `--continue`
  relaunch (opencode-style, not claudecode-style).
- **Seed manifest layout.** The engine roots at `host.workdir + "/" + home_subdir`,
  so the manifest uses `home_subdir="home"` with `.grok/`-prefixed includes
  (`[".grok/auth.json", ".grok/config.toml"]`), not `home_subdir=".grok"` — the
  latter would have missed `GROK_HOME` entirely.
- **Conversation transport = ACP (JSON-RPC 2.0 over stdio)**, `grok agent stdio` —
  a third transport pattern (claudecode=claude stream-json, opencode=HTTP+SSE).
  `session/prompt` response = turn-end; `session/request_permission` = the
  permission gate; `session/cancel` = interrupt; capability non-advertisement is
  the permission seam.
- **Non-gated conversation uses `--always-approve`** (grok has no headless-safe
  permission-mode analogue to claudecode's).
- **Stage 5 binary cache: real vendor auto-install + task-path symlink (post-ship
  fix).** The initial Stage 5 impl was a stub — it only seeded the cache from a
  host `grok` already on PATH and raised ("future refinement") when none existed,
  violating the guide's Stage 5 "must auto-install on a bare worker" contract
  (the installer URL was unconfirmed at build time; it since was —
  `https://x.ai/cli/install.sh`, per `~/.grok/README.md`). `ensure_grok_installed`
  now, on a cache miss with no host grok, runs the vendor installer with
  `HOME=<cache_root>` (persistent, OUTSIDE any workdir) + `GROK_BIN_DIR=<cache_dir>`,
  then **symlinks** the cached binary into the per-task launch path
  `<workdir>/home/.local/bin/grok` and returns *that* (was: the raw cache path) —
  so the heavy binary survives workdir teardown and resume re-links idempotently
  (mirrors claudecode). Supersedes the Stage 5 plan's "future refinement" note.
