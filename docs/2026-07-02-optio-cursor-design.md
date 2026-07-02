# Design: `optio-cursor` — a full-featured wrapper for Cursor CLI

- **Date:** 2026-07-02
- **Status:** Approved (goal-driven build)
- **Branch:** `csillag/cursor` (stacked on `csillag/optio-grok`)
- **Guide:** follows `docs/writing-agent-wrappers.md`; this is the agent-specific
  spec that guide mandates.
- **Primary reference:** `optio-grok` (Cursor is its closest shape: ACP-over-stdio
  conversation, TUI-only UI, native sandbox, relocatable config dir).

## 1. Target profile (empirical)

Cursor CLI ("Cursor Agent"), binary `cursor-agent` (2026.07.01-41b2de7,
`~/.local/bin/cursor-agent` → `~/.local/share/cursor-agent/versions/<v>/`), Node
bundle. **Distinct from the `cursor` IDE binary** (the desktop VS-Code fork; its
CLI entry only opens the GUI). Profiled by probing `cursor-agent --help`,
subcommands, a relocated-`$HOME` run, and an unauthenticated ACP handshake on
this host.

| Axis | Cursor mechanism |
|---|---|
| Interactive UI | TUI (default); no web SPA (`worker` = cloud-worker daemon, not a UI) |
| Headless single-turn | `-p/--print`, `--output-format text\|json\|stream-json`, `--stream-partial-output` |
| Headless conversation | **`cursor-agent acp`** (hidden subcommand: "Start the Cursor Agent as an ACP server", JSON-RPC 2.0 over stdio) — **primary**. Verified handshake: `protocolVersion:1`, `loadSession:true`, `sessionCapabilities.list`, image prompts; `session/new`, `session/load`, `session/prompt`, `session/cancel`, `session/update`, `session/set_model`, `session/request_permission` all present in the binary |
| Resume | `--resume [chatId]`, `--continue`, `create-chat`, `ls`; ACP `session/load` (`loadSession:true`) |
| Auth | `login` (browser; `NO_OPEN_BROWSER=1` prints the URL — feeds the `BROWSER:` redirect path), `logout`, `status/whoami`; `--api-key` / `CURSOR_API_KEY`; `CURSOR_AUTH_TOKEN` env exists in the binary. ACP `authMethods=[cursor_login]` ("Run 'agent login' first"). Cred file layout pinned at Stage 3 via a live login |
| Config dir | `~/.cursor` + `~/.cache`, **relocates cleanly with `$HOME`** (verified: fake-HOME run created `<HOME>/.cursor/cli-config.json`, `<HOME>/.cache/cursor-compile-cache`) |
| Instructions file | `AGENTS.md` (Cursor docs: rules + AGENTS.md support) |
| Permissions | `cli-config.json` `permissions.allow/deny` (e.g. `"Shell(ls)"`) + `approvalMode` (`allowlist`); `-f/--force` (aka `--yolo`), `--auto-review`; ACP `session/request_permission` is the conversation-mode gate |
| Model | `--model <m>` (bracket overrides, e.g. `claude-opus-4-8[effort=high]`), `models` / `--list-models`; ACP `session/set_model` (inline switch, probe at Stage 7) |
| Sandbox | native `--sandbox enabled\|disabled` / `CURSOR_SANDBOX`; `cli-config.json` `sandbox.{mode,networkAccess}` |
| Extras | MCP (`mcp`, `--approve-mcps`), `--workspace`/`--add-dir`, `--worktree`, plugins, shell integration, cloud `worker` |

### Capability → optio surface mapping

| Agent capability | optio surface | Cursor mechanism |
|---|---|---|
| headless programmatic API | **conversation mode** (drives conversation-ui) | `cursor-agent acp` |
| TUI only (no web SPA) | **iframe via ttyd** | default TUI |
| interactive login | fallback / seed capture | `cursor-agent login` (`NO_OPEN_BROWSER=1` → `BROWSER:` redirect) |

Conversation mode is the primary surface; ttyd-embedded TUI is the fallback for
operations the headless surface can't do (notably first-time login). API-key auth
(`CURSOR_API_KEY`) is a second headless-login path where the operator has one.

## 2. Key decisions

1. **Adapt `optio-grok`.** Cursor matches it on nearly every axis: ACP JSON-RPC 2.0
   over stdio (grok's exact conversation transport — same method names verified in
   the cursor binary and by live handshake), TUI-embedded-via-ttyd fallback, native
   sandbox, `$HOME`-relocatable config. The grok wrapper is the file-by-file
   reference; claudecode/opencode secondary. The conversation-ui side should reuse
   or thinly extend the grok ACP reducer if the wire shape matches (both speak
   `session/update` notifications).
2. **Conversation transport = `cursor-agent acp`.** Verified live: unauthenticated
   `initialize` succeeds and advertises `loadSession`, session list, and the
   `cursor_login` auth method. Permission gating via `session/request_permission`
   capability (non-advertisement = the permission seam, as with grok);
   interrupt = `session/cancel`; turn-end = `session/prompt` response.
3. **Isolation = per-task `$HOME`.** Verified: cursor-agent derives `~/.cursor` and
   `~/.cache` from `$HOME`. Set `HOME=<workdir>/home` (+ `XDG_*` for hygiene) as
   claudecode/grok do. No claude-compat ingestion observed for cursor — but Stage 5
   verifies no operator-config leak explicitly.
4. **Instructions file = `AGENTS.md`.** Prompt composition mirrors grok's
   `compose_agents_md`, reusing the shared `optio_agents.prompt` SSOT.
5. **Headless login = seeds first; two capture paths.** (a) Interactive ttyd
   seed-setup task running `cursor-agent login` with `NO_OPEN_BROWSER=1` so the URL
   surfaces through the `BROWSER:` keyword; (b) `CURSOR_API_KEY` planted via seed
   for operators with API keys. No OAuth-loopback rewrite unless the login URL
   proves to need it (probe at Stage 3).
6. **Filesystem isolation: probe cursor's native `--sandbox` first** (grok
   precedent: native Landlock sandbox replaced claustrum and custom profiles were
   fail-closed). If cursor's sandbox proves fail-open or not
   allowlist-configurable (config only exposes `mode` + `networkAccess` so far),
   fall back to claustrum via grok/claudecode's `fs_allowlist.py` pattern. Decide
   at Stage 8 with a live enforcement probe.
7. **Model switching: inline via ACP `session/set_model`** (method present in the
   binary; verify live at Stage 7). Fallback: restart-based relaunch with
   `--resume <chatId> --model <m>` (both `--resume` and `create-chat` exist).

## 3. Package shape

`packages/optio-cursor/`, Python `optio-cursor`, `src/optio_cursor/`. Deps:
`optio-core`, `optio-host`, `optio-agents`, `asyncssh`, `aiohttp`. Module layout
mirrors `optio-grok` (adapt, don't copy blindly):

- `session.py` — `create_cursor_task` factory + `run_cursor_session`; iframe (ttyd)
  and conversation bodies; resume/snapshot/seed wiring.
- `host_actions.py` — cursor-agent binary cache/install (`curl cursor.com/install`,
  install root relocated into the optio cache; symlink target
  `~/.local/share/cursor-agent/versions/<v>/`), ttyd install, tmux/ttyd argv,
  launch env (`HOME`, `XDG_*`), `send_text_to_cursor` (tmux), teardown.
- `types.py` — `CursorTaskConfig` (mirror `GrokTaskConfig`; cursor-specific:
  `api_key`, `sandbox`, `auto_review`, `force`).
- `conversation.py` — `CursorConversation` implementing
  `optio_agents.conversation.Conversation` over `cursor-agent acp` (adapt
  `GrokConversation`'s ACP client).
- `conversation_listener.py` — dashboard SSE listener (adapt grok's).
- `prompt.py` — `compose_agents_md` (AGENTS.md, SSOT docs).
- `seed_manifest.py` — `CURSOR_SEED_MANIFEST` / `CURSOR_CRED_MANIFEST`
  (`home_subdir="home"`, `.cursor/`-prefixed includes: `cli-config.json` + the
  cred file pinned at Stage 3), suffix `_cursor_seeds`.
- `cred_watcher.py` — token save-back + lease renewal (rotating-token behavior
  pinned at Stage 4; may be a no-op watcher if cursor tokens are long-lived).
- `verify.py` — host-free `verify_and_refresh_seed` (auth probe; API-key probe).
- `snapshots.py` — Mongo `{prefix}_cursor_session_snapshots` (chat state + workdir
  tar; cursor session-store location pinned at Stage 2).
- `models.py` — model catalogue via `cursor-agent models` / ACP.
- `fs_allowlist.py` — only if Stage 8 lands on claustrum (Decision 6).
- UI: `optio-conversation-ui/src/cursor/` reducer + view, gated by
  `widgetData.protocol = "cursor"`; start from the grok ACP reducer.

## 4. Staged build path (per the guide)

| Stage | Goal | Cursor specifics |
|---|---|---|
| 0 MVP | task runs one mode + DONE/ERROR, local | ttyd-embedded TUI; `AGENTS.md`; per-task `HOME` |
| 1 Remote/SSH | same over SSH | generic Host; no new work |
| 2 Resume | relaunch picks up session | `--resume <chatId>` / ACP `session/load`; snapshots; pin session-store path |
| 3 Seeds | logged-in fresh start | `CURSOR_SEED_MANIFEST`; pin cred file via live login; API-key seed variant |
| 4 Leases + save-back + verify | token durability | `cred_watcher` on the cred file; `verify_and_refresh_seed` |
| 5 Cache + HOME isolation | evictable binary cache; per-task identity | installer-root relocation; leak-free verified |
| 6 Conversation + UI | live `Conversation` + chat widget | `cursor-agent acp`; `src/cursor/` reducer+view (share grok ACP plumbing) |
| 7 Frontend parity | permissions, model switch, file up/down, verbosity | `session/request_permission`; `session/set_model` (probe); upload/download endpoints |
| 8 fs-isolation | sandbox | native `--sandbox` probe → else claustrum |

Demo (per the guide's Part 5): a cursor **seed-setup** task + **two seed-pinned**
run tasks — one iframe, one conversation — in `optio-demo`, mirroring the
claudecode/opencode/grok demo trio. Registration: `optio-demo` install list +
`pyproject.toml` deps + root `Makefile` `RELEASABLE_PY` + `PY_PACKAGES`.

## 5. Non-goals (v1)

- Cursor cloud `worker` / remote-agent integration — optio owns its own hosts.
- `--worktree` / `--workspace` multi-root — optio's workdir is the workspace.
- MCP server management, plugins, shell integration — leave to the agent's config.
- Bedrock env surface (`CURSOR_BEDROCK_*`) — enterprise auth variant, out of scope.
- `-p/--print` single-turn mode as a task surface — conversation mode supersedes it.

## 6. Success criteria

- Parity with `optio-grok` across Appendix A of the guide (staged; gaps tracked,
  not silent).
- A demo cursor task runs locally and over SSH, in both ttyd-iframe and
  conversation modes, resumes correctly, and uses a seeded logged-in identity.
- No operator config leaks into a task (verified: a task's cursor sees only the
  per-task `HOME`, never the host `~/.cursor`).

## 7. Open probe-points (resolved during staged build, then reconciled here)

1. ~~Cred-file path~~ **RESOLVED** (empirical, planted-file + logout probe):
   `${XDG_CONFIG_HOME:-~/.config}/cursor/auth.json`, JSON with
   `accessToken`/`refreshToken`; `status` reads it, `logout` deletes it.
   Rotation behavior still to pin at Stage 4.
2. Chat/session store location under `$HOME` for snapshots (Stage 2).
3. ACP `session/update` wire shape vs grok's — decides reducer reuse vs new
   (Stage 6). **GAP (Stage 6 Task 0):** live prompt-cycle probe skipped —
   host `cursor-agent status` = "Not logged in". Only the unauthenticated
   `initialize` result + binary method list are cursor-verified; all payload
   shapes coded `[grok-pinned, cursor runtime-unverified]` (see the pinned
   block in `optio-cursor/src/optio_cursor/conversation.py`). Runtime
   confirmation deferred to the demo stage.
4. `session/set_model` live behavior (Stage 7).
5. Native sandbox enforcement semantics: fail-open vs fail-closed, allowlist
   configurability (Stage 8).
6. Whether ACP works with `CURSOR_API_KEY` alone (no `cursor_login` state) —
   affects the API-key seed variant (Stage 3).
