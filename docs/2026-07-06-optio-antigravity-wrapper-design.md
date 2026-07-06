# optio-antigravity — Wrapper Design

Goal: a full-featured optio wrapper around **Google Antigravity CLI** (`agy`, the
successor to `@google/gemini-cli`), following `docs/writing-agent-wrappers.md`.
Target = the 30 rows of Appendix A, reached via the staged path (guide Part 3),
**minus the rows the transport makes impossible** — named explicitly in §7 rather
than silently skipped.

This is the 7th wrapper. Unlike the prior six, the target ships **no
bidirectional transport** (no ACP, no stream-json, no HTTP/SSE server). Its only
programmatic surface is one-shot `agy --print`. That single fact reshapes the
conversation-mode design (§5) and drives the honest parity gaps (§7).

Reference: the real binary (`agy` v1.0.16, `antigravity` inside the tarball) was
downloaded and probed directly (`--help`, subcommand help, `strings`, isolated-HOME
runs). Findings below are from that binary unless marked *(web)*.

---

## 1. Target profile (guide Part 1 — empirically established)

| Q | Finding | Source |
|---|---|---|
| Headless API? | **Only one-shot** `agy --print`/`-p` (`--prompt` alias). No ACP / stream-json / HTTP server (ACP requested in their issue #31, unimplemented *(web)*). `--print-timeout` default 5m. | `--help` |
| Non-TTY bug | `--print` **swallows stdout under a non-TTY** (pipe/redirect/subprocess) — issue #76 *(web)*; unauthed `-p` here exited silently with no stdout and no browser-open. → **a PTY is mandatory**, and we read the transcript file (§5) rather than trusting stdout. | isolated-HOME run + *(web)* |
| Own web server? | **No.** The web UI is the separate Antigravity IDE, not the CLI. | tarball probe |
| TUI-only? | **Yes** — the rich interactive surface is a terminal UI → embed via **ttyd** (claudecode pattern). | `--help` (no web flags) |
| Headless login? | Google OAuth (browser) is primary; binary exposes `HeadlessAuthRequired` (device-code-style path for SSH) *(web: prints URL+code)*. **No API-key env** (`GEMINI_API_KEY`/`GOOGLE_API_KEY` not accepted; secondary claims of `ANTIGRAVITY_TOKEN` NOT confirmed in the binary). Sign-in happens in the **interactive TUI**, not `--print`. | strings (`auth.keyringAuth`, `HeadlessAuthRequired`), runs |
| Creds storage | **OS keyring** (Linux Secret Service / libsecret via D-Bus: `auth.keyringAuth`, `KeyringTokenStorage`, `org.freedesktop.Secret`). NOT a plain creds file. | strings |
| Resume? | **Yes** — `--continue`/`-c` (most recent), `--conversation <ID>` (by id). Structured transcript at `~/.gemini/antigravity/transcript.jsonl`. | `--help`, strings |
| Rotating creds? | Google OAuth refresh tokens rotate; but stored in keyring, so save-back ≠ a file diff (see §2). | strings |
| Model selection? | `--model` at launch; `agy models` lists; mid-session `/config`. Gemini-first + BYO Claude/GPT-OSS *(web)*. Switch strategy = restart-with-new-model (claudecode-style). | `--help`, `models` subcmd |
| Sandbox | Native `--sandbox` ("terminal restrictions"); `--dangerously-skip-permissions` auto-approves tools. | `--help` |
| Other | MCP client built in (chrome-devtools browser subagent); `--project`/`--new-project`; `--add-dir`; internals are Windsurf/Cascade-derived (`exa.language_server_pb`, `codeium_common`, `jetski/cli`). | strings |

**Config / state tree** (shares `~/.gemini` with Gemini CLI):
- `~/.gemini/antigravity-cli/settings.json` — settings (color scheme, model, trusted paths; `AutoUpdate`/`AutoUpdateTime` keys → self-update control candidate).
- `~/.gemini/antigravity/transcript.jsonl` — **structured conversation transcript** (the conversation-mode event source, §5).
- `~/.gemini/antigravity/artifacts/` — artifacts/deliverables dir.
- `~/.gemini/config/mcp_config.json` — MCP servers.
- `~/.gemini/jetski/brain/` — internal agent state.
- **Memory file: `AGENTS.md`** (also reads `GEMINI.md`) — where the `optio.log` protocol prompt goes.

---

## 2. Auth + seeds — the hard part (Stage 3/4, top risk)

Every prior wrapper had either an API-key env bypass or a file-based credential we
could capture/replant. Antigravity has **neither**: login is TTY-bound Google OAuth
and the token lands in the **OS keyring**, not a file. This is the single biggest
open question and gets a real-login spike **before** the seed stage is designed in
detail.

**Login story.**
- **Local / interactive:** first login runs in the **ttyd TUI surface** (Google
  OAuth, browser opened on the operator's machine). Then capture a seed.
- **Remote (SSH):** the `HeadlessAuthRequired` device-code path prints a URL + code
  to the terminal (visible in the ttyd/iframe surface) — completes from any machine.
- **No API-key shortcut** — do not design around one; it does not exist.

**Seed strategy (to be proven by the spike).** A seed must carry *everything login
provisions*. Candidates, in preference order:
1. If `agy` honors a **file-based token store** when no Secret Service is present
   (many keyring libs fall back to an encrypted file) — capture that file +
   `~/.gemini/antigravity-cli/`. **Verify empirically.**
2. Otherwise, provision a **Secret Service** (headless `gnome-keyring-daemon` /
   `dbus-run-session`) on the worker and plant the token into it — heavier, but the
   generic seed engine only needs capture/plant functions.
3. Worst case: seeds carry the `~/.gemini` non-secret state and login is re-done
   interactively per pool member (degrades the "fresh already-authed start" row).

Save-back (Stage 4) and host-free verify/refresh (Google OIDC discovery, like grok)
are designed **after** the spike settles where the token actually lives.

---

## 3. Install + self-update (Stage 5)

- **Installer:** `curl -fsSL https://antigravity.google/cli/install.sh | bash`
  (read: downloads a per-platform manifest from an auto-updater Cloud Run host,
  SHA512-verifies, extracts a single **Go binary** named `antigravity`, installs it
  as **`agy`** to `~/.local/bin`, then runs `agy install` for shell setup). Manifest
  URL pattern: `<updater>/manifests/<platform>.json` → `{version,url,sha512}`.
- **Two-tier cache** (guide Stage 5): reuse a worker `agy` on the login-shell PATH
  (fast copy) else fetch the manifest + tarball into the evictable cache; symlink
  into `<workdir>/home/.local/bin/agy`. Verify identity functionally (run a
  known-only subcommand, e.g. `agy models` error-shape or `agy --help` grep), not
  `--version` alone.
- **Self-update MUST be disabled** — the installer states `agy` "self-updates in the
  background during regular runs," which fights the pinned binary and can stall a
  launch on a network probe. Exact mechanism is an **open research item** (§8):
  candidates are a `settings.json` `AutoUpdate:false` key, an env flag, or blocking
  the updater Cloud Run endpoint. Resolve in the Stage-5 spike.

---

## 4. Architecture

A `TaskInstance` factory (`create_antigravity_task(...)`) + an
`AntigravityTaskConfig` dataclass, bundling mode-adapters. Runs `agy` as a managed
subprocess (local or SSH) via the generic `optio_host.Host`, supplying the
backend-specific callbacks to the shared `run_log_protocol_session` driver;
everything generic (workdir lifecycle, `optio.log` loop, hooks, seed/lease,
snapshots) is inherited.

**Module layout** — mirror `optio-grok` exactly
(`packages/optio-antigravity/src/optio_antigravity/`): `__init__.py`, `types.py`,
`session.py`, `conversation.py`, `conversation_listener.py`, `host_actions.py`,
`prompt.py`, `models.py`, `seed_manifest.py`, `snapshots.py`, `cred_watcher.py`,
`verify.py`, `fs_allowlist.py`. Tests mirror grok's `tests/` (fake `agy` shim +
docker-sshd harness + real-binary opt-in gates).

**Two modes** (`mode` field, validated in `__post_init__`):
1. **`iframe`** — `agy`'s TUI embedded via ttyd (claudecode pattern). The
   **login surface** and interactive fallback.
2. **`conversation`** — synthetic one-shot-per-turn (§5). Requires
   `mode="conversation"` for `conversation_ui=True`.

---

## 5. Conversation mode — synthetic, transcript-driven (Part 2B/2C)

No live transport, so the `Conversation` is built from `agy`'s one-shot mode + its
structured transcript:

- **Drive a turn:** `send(text)` spawns `agy -p --conversation <id> --model <m>
  --dangerously-skip-permissions <text>` **under a PTY** (mandatory — §1 non-TTY
  bug). First turn omits `--conversation` (or uses `--new-project`) and **captures
  the conversation id** for subsequent turns / resume.
- **Read events from the transcript, not stdout.** Tail
  `~/.gemini/antigravity/transcript.jsonl` for structured events (assistant text,
  tool calls, reasoning) → map to `ChatItem`s in a pure reducer. This sidesteps the
  #76 stdout-swallow bug and yields tool/reasoning rows, not just a blob. The
  transcript schema must be captured from a real run and pinned as a replay fixture
  (guide Testing Layer 3).
- **`Conversation` protocol:** `send`/`on_event`/`on_message`/`is_pending`/
  `interrupt`/`close`. `on_event` fans out transcript events live; `on_message`
  emits one final answer per completed turn (turn = one `-p` invocation). Synthetic
  optio events use the `x-optio-` prefix.
- **conversation-ui:** a per-engine reducer (`antigravity/events.ts`) +
  `AntigravityView.tsx` + a `protocol` discriminator, feeding the shared
  `ConversationView`. Session controls (model + any `agy` extras) via the generic
  `SessionControl[]` contract; model switch = restart-with-new-model (claudecode
  precedent).

**Data source note:** unlike claudecode (per-task optio-side listener) or opencode
(direct server client), Antigravity's source is a **tailed transcript file** driven
by repeated one-shot invocations — a third valid shape for the same contract.

---

## 6. Staged build path (guide Part 3)

- **Stage 0 — MVP:** `agy` runs as an `iframe`/ttyd task locally, `AGENTS.md`
  carries the `optio.log` protocol, reaches `DONE`. *(Confirm `AGENTS.md` is read
  and the `optio.log` keyword loop works.)*
- **Stage 1 — SSH:** same over `RemoteHost`; only the local-vs-remote bind branch.
- **Stage 2 — Resume/snapshots:** `--conversation <id>`/`--continue` + workdir tar
  (exclude the binary); pull `resume.log` doc **and** pushed `RESUME_NOTICE` in both
  modes.
- **Stage 3 — Seeds:** per the §2 spike outcome (keyring vs file vs Secret Service).
- **Stage 4 — Leases + save-back + verify:** graceful SIGTERM flush for seeded
  sessions; Google OIDC discovery refresh (grok pattern) once token location known.
- **Stage 5 — Binary cache + isolation + self-update off:** §3.
- **Stage 6 — Conversation mode + conversation-ui:** §5.
- **Stage 7 — Frontend parity:** session controls (model + extras), file up/down
  (`optio-file:` sentinel + `artifacts/` dir), tool verbosity. **Permission gating
  is turn-level only** (see §7).
- **Stage 8 — Filesystem isolation:** native `--sandbox` first; shared
  `optio_agents.claustrum` Landlock (functional validation, per-engine cache dir).

---

## 7. Parity gaps — named, not hidden

Inherent to the one-shot transport; tracked, not silent:

- **No live token streaming.** An answer arrives per-turn (one `-p` completes), not
  delta-by-delta. The widget shows a turn appearing, not a live caret.
- **No interactive mid-turn permission gate.** `-p` turns run with
  `--dangerously-skip-permissions` (required for non-interactive). Permission
  surfacing is at most turn-level (approve/deny a whole turn before it runs), not
  per-tool mid-turn. Row 19 is partial.
- **Interrupt is coarse.** `interrupt()` kills the in-flight `-p` process; there is
  no cooperative cancel mid-turn.
- **First-login cannot be headless-silent.** Requires the ttyd surface or a seed;
  no API-key path.

Everything else on the 30-row checklist is reachable.

---

## 8. Open research items (resolve via Stage-0/3/5 spikes, before those stages)

1. **Self-update disable mechanism** (§3) — settings key vs env vs endpoint block.
2. **Where the OAuth token actually lives on a headless worker** (§2) — keyring
   file-fallback vs Secret Service required. Gates the entire seed design.
3. **`transcript.jsonl` schema** (§5) — capture a real run; pin as a replay fixture.
4. **`AGENTS.md` is honored by `agy` and the `optio.log` keyword loop functions**
   (Stage-0 gate).
5. **`--print` output reliability under a PTY once authed** (#76) — confirm the
   transcript-tail approach fully decouples us from stdout.

---

## 9. Non-goals

- No dependency on the third-party `agy-acp` community bridge (fragile external
  dep; we wrap `-p` + transcript directly).
- No API-key auth path (does not exist).
- No attempt to fake per-token streaming the transport can't provide.

---

## Spike results (S1 — real login, 2026-07-06)

Resolved live by driving a real interactive Google login in the seed-setup task
and inspecting the isolated HOME before teardown (a snapshot was taken).

**Where agy persists auth (isolated `<workdir>/home`):**
- **Token store:** `.gemini/antigravity-cli/antigravity-oauth-token` — JSON
  `{"auth_method":"consumer","token":{"access_token","token_type":"Bearer",
  "refresh_token":"1//0…","expiry":"<RFC3339Nano+offset>"}}`. A plain FILE — the
  system keyring (`~/.local/share/keyrings/login.keyring`) was byte-unchanged
  across login, so the design §2 "OS keyring / oauth_creds.json" guesses were
  BOTH wrong. This is the sole file agy rewrites on refresh (cred/save-back).
- **Settings:** `.gemini/antigravity-cli/settings.json` =
  `{AutoUpdate:false, trustedWorkspaces:[<capture workdir>]}` — confirms the S2
  self-update disable works; trustedWorkspaces holds the CAPTURE workdir → a
  replant must **rekey** it to the new workdir (`_rekey_trusted_workspaces`).
- **Provisioned set (seed):** `.gemini/antigravity-cli/cache/onboarding.json`
  (`onboardingComplete:true`), `.gemini/config/` (`config.json`,
  `mcp_config.json`, `.migrated`, `projects/default-cli-project.json`).
- **Auth flow:** hosted-redirect + PKCE (`code_challenge`), manual code paste;
  print-only (no browser-open — the redirect shims never fire; hence the pane
  scraper). Public client, id
  `1071006060591-tmhssin2h21lcre235vtolojh4g403ep.apps.googleusercontent.com`,
  no client_secret (PKCE) for refresh.

**The seed-capture bug this explains:** the manifest's guessed
`.gemini/oauth_creds.json` didn't exist → validity gate saw no token → capture
rejected → 0 seeds → no seed-pinned demo tasks. Fixed: manifest paths + nested
validity gate + provisioned set + trustedWorkspaces rekey (verified: capture
from the real authed home grabs token+settings+onboarding+config, excludes
junk); verify.py updated to the same path/format (nested, ISO expiry, PKCE
client, invalid_grant-only-dead).
