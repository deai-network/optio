# optio-codex — full-parity design

**Date:** 2026-07-02. **Target:** full Appendix-A parity per `docs/writing-agent-wrappers.md`,
starting from the reviewed Stage-0 wrapper (review: `docs/2026-07-02-optio-codex-stage0-review.md`).
**Primary porting template:** `optio-grok` (newest full wrapper; branch `csillag/optio-grok`) —
structure, teardown ordering, seed/lease/watcher wiring, listener, demo trio, test inventory.
`optio-claudecode`/`optio-opencode` remain the guide's canonical references.
All codex facts below were live-probed against codex-cli **0.142.5** (2026-07-02) unless marked
from docs/source; version-sensitive.

## Part-1 profile (probed answers)

1. **Headless API:** yes, two surfaces.
   - `codex app-server` — bidirectional JSON-RPC 2.0 (JSONL over stdio, `jsonrpc` field omitted,
     NO Content-Length framing). Threads → turns → items. Experimental label, but it is the
     production transport of OpenAI's own VS Code extension. **Chosen conversation transport.**
   - `codex exec --json` — one-shot JSONL turn; multi-turn via `codex exec resume <thread-id> --json`
     (verified live). No approvals (hard `approval_policy=never`), no steering. Degraded/batch
     mode + verify-probe surface only.
2. **Own web server:** no. 3. **TUI:** yes → iframe/ttyd mode stays (Stage 0 shipped).
4. **Headless login:** `codex login --device-auth` is fully headless (URL + one-time code,
   15-min expiry, no local browser/callback). Browser OAuth loopback is hardcoded port
   1455 (fallback 1457), not configurable. `OPENAI_API_KEY` env is NOT respected at runtime;
   API-key auth = `printenv OPENAI_API_KEY | codex login --with-api-key` (writes auth.json).
5. **Resume:** session-id (UUIDv7) keyed rollout JSONLs at
   `$CODEX_HOME/sessions/YYYY/MM/DD/rollout-<ts>-<uuid>.jsonl`. **Path-portable** (probed:
   sessions/ copied to a different CODEX_HOME path resumes fine; sqlite index is derived and
   rebuilt — exclude it). **Trap:** `resume --last` is cwd-filtered and silently starts a NEW
   session on miss — always resume by explicit id.
6. **Rotating credentials: YES — Stage 4 mandatory.** ChatGPT-mode `auth.json` holds
   `tokens{id_token(1h), access_token(10d), refresh_token(single-use)}` + `last_refresh`.
   Proactive refresh after 8 days (`TOKEN_REFRESH_INTERVAL=8d`, manager.rs) + refresh-on-401;
   auth.json rewritten in place; a used refresh token invalidates all other copies
   (openai/codex#15410 — by design). Official CI/CD guidance is exactly the optio save-back
   pattern: restore → run → persist rewritten auth.json; one live lineage per seed.
7. **Model selection:** inline. app-server: `model/list` + per-`turn/start` `model`/`effort`
   (sticky). exec: `-m` incl. on `exec resume`. TUI: `/model`. → grok-style inline switching;
   models.py shrinks to app-server `model/list` + static fallback (`gpt-5.5`, `gpt-5.4-mini`).

## Mode decisions

- **Modes:** `iframe` (tmux+ttyd TUI — shipped) + `conversation` (app-server) — both, like grok.
- **Headless-login strategy (App. A #27):** seeds (Stage 3) as primary; seed-setup demo task
  captures a logged-in identity via interactive TUI login in the iframe OR `--device-auth`
  (device-auth URL surfaced via `BROWSER:`/`ATTENTION:` — decide in Stage-3 plan). Interim
  (pre-seeds, documented in README): interactive login in iframe, or pipe API key.
- **Browser mode:** stays `suppress` for now; revisit at Stage 3 if the device-auth URL should
  ride the `redirect` channel like claudecode's login URLs.

## Conversation transport (Stage 6) — app-server method map

Handshake `initialize`(clientInfo; stay on STABLE surface, no `experimentalApi`) + `initialized`.
`thread/start{cwd, sandboxPolicy, approvalPolicy, model}` (NOT ephemeral — rollout file is the
resume source) → `turn/start{threadId, input[], model?, effort?}` → notifications:
`turn/started`, `item/started`, `item/agentMessage/delta`, `item/reasoning/summaryTextDelta`,
`item/commandExecution/outputDelta`, `item/completed`, `turn/completed{status}`,
`thread/tokenUsage/updated`, `error{codexErrorInfo}`. Permission gating = **server→client
JSON-RPC requests** `item/commandExecution/requestApproval` / `item/fileChange/requestApproval`
→ respond `{decision: accept|acceptForSession|decline|cancel}`. Interrupt = `turn/interrupt`
(turn ends `status:"interrupted"`). Resume = `thread/resume`. Auth = `account/read`.
Bonus over grok: `turn/steer` (mid-turn injection).
Backpressure: `-32001` retryable. Version pinning: vendor `codex app-server generate-json-schema`
output for the supported version; assert `initialize.result` at startup; use
`optOutNotificationMethods` for unrendered streams.
GrokConversation skeleton ports 1:1 (attach/reader/bootstrap/route/dispatch/_finish-drain,
queue-permissions-until-handler, close-requested + clean-close-DONE park); framing swapped
ACP→app-server; permission correlation key = the server request's JSON-RPC id.
ConversationListener ports ~verbatim (engine-agnostic).

## Seeds (Stage 3)

- `CODEX_SEED_SUFFIX = "_codex_seeds"`, `home_subdir="home"`.
- `CODEX_SEED_MANIFEST` include: `.codex/auth.json`, `.codex/config.toml`.
  `CODEX_CRED_MANIFEST` (save-back, write-only): `.codex/auth.json` only.
  `consume_transform=None` (auth is cwd-independent) — BUT plant/merge must pre-trust the
  workdir: ensure `[projects."<workdir>"] trust_level = "trusted"` in config.toml at consume
  time (cwd-dependent → done as a plant-time transform or post-merge edit; decide in plan).
- Exclude always: `packages/` (286MB binary cache), `*.sqlite*` (absolute rollout_path poison;
  rebuilt from rollouts), `cache/`, `models_cache.json`, `tmp/`, `.tmp/`, `shell_snapshots/`,
  `version.json`, `installation_id`, `skills/.system/`, logs.

## Snapshots/resume (Stage 2)

Grok's single-workdir-blob scheme + **recorded session id** (claudecode-shaped): snapshot doc
`{processId, capturedAt, endState, workdirBlobId, sessionId}`. CODEX_HOME lives under
`<workdir>/home` so sessions/ ride the workdir tar; `workdir_exclude` must NOT exclude
`home/.codex/sessions` but SHOULD exclude the seed-excluded junk above (esp. `home/.codex/packages`).
Session id capture: iframe mode — newest rollout file under `home/.codex/sessions` at snapshot
time (or `state` query; prefer file scan, sqlite is derived); conversation mode — `thread/started`
event. Relaunch: iframe `codex resume <id>`; conversation `thread/resume`. Never `--last`.
Port grok invariants: restore-failure fails loud; `_rotate_optio_log`; AGENTS.md planted after
restore; auto-start positional suppressed on resume; `resume.log` entries; reached-live gates.

## Leases + cred watcher + verify (Stage 4)

Port grok cred_watcher wholesale: path `home/.codex/auth.json`; fingerprint = sha256, invalid/
missing → None gate; `capture_gate_ok` = valid auth.json with non-null tokens or OPENAI_API_KEY;
10s tick = save-back + renew_lease; lease loss → cancellation_flag. Teardown ordering discipline
verbatim (watcher-cancel → backstop save-back → lease release). verify.py: challenge probe
`codex exec --json --skip-git-repo-check -s read-only '<capital-of-France>'` in throwaway
CODEX_HOME planted from seed, stdout-only verdict, write back rotated auth.json, mark status.

## Binary cache (Stage 5)

Grok pattern + real auto-download (grok's documented gap — codex has a clean URL):
`https://github.com/openai/codex/releases/download/rust-v<ver>/codex-<triple>.tar.gz`,
triples `{x86_64,aarch64}-unknown-linux-musl` (+darwin). Single static musl binary (~286MB).
Cache dir `${OPTIO_CODEX_CACHE_DIR:-${XDG_CACHE_HOME:-$HOME/.cache}/optio-codex/bin}` resolved
host-side. Seed-by-copy (`cp -L`) from host binary when present, download when not.
Per-task launch path stays `<workdir>/home/.local/bin/codex` symlink (Plan A's kill-scoping fix)
→ pkill scoping keeps working. `install_if_missing` becomes real here. Pin ttyd 1.7.7 (existing).

## Filesystem isolation (Stage 8): codex-native, not claustrum

Grok precedent: native sandbox. Codex Linux mechanism: bundled bubblewrap primary,
Landlock+seccomp fallback (this host exercises the Landlock path — bwrap/userns fail here, per
claustrum findings). Modes: read-only / workspace-write (network OFF by default, `.git/` RO,
/tmp writable) / danger-full-access. Extra grants: `--add-dir` (writable roots) /
`-c sandbox_workspace_write.writable_roots=[...]`; NO read-only grant vocabulary (read side is
open in workspace-write) — `AllowedDir(mode="ro")` semantics need a decision in the Stage-8 plan
(document divergence vs grok's ro/rw split). Reconcile existing `sandbox: SandboxMode` config
field with `fs_isolation`/`extra_allowed_dirs` (no duplicate knobs). Fail-open/fail-closed
analysis required (grok lesson: built-ins failing open → custom profile); probe
`codex doctor` "filesystem sandbox restricted" + enforcement test gated behind
`OPTIO_CODEX_SANDBOX_ENFORCE_TEST=1` (grok's env-gating style). Pre-trust workdir via
config.toml projects entry (codex writes trust entries otherwise — also a test-pollution trap:
per-task CODEX_HOME already contains it).

### Stage-8 probe verdict (2026-07-02, codex-cli 0.142.5)

**Verdict: FAIL-CLOSED** when no sandbox mechanism (bubblewrap or Landlock) is
available. codex never runs the model's shell command unconfined as a result
of a sandbox-setup failure — it errors/panics and the command does not run.
The only unconfined path is the explicit opt-out flag
`--dangerously-bypass-approvals-and-sandbox`, which optio-codex never emits.
Evidence:

- `codex sandbox -c sandbox_mode=workspace-write -- touch $HOME/probe`
  (mechanism available; this host has unprivileged userns enabled —
  `/proc/sys/kernel/unprivileged_userns_clone=1`, `max_user_namespaces=160248`,
  bundled bwrap runs rc=0): rc=1, "Read-only file system" (Hungarian locale:
  "Írásvédett fájlrendszer"), outside file **absent**; inside-workspace
  `touch ./inside.txt` rc=0. `codex doctor`: `✓ sandbox  restricted fs +
  restricted network · approval OnRequest` / `filesystem sandbox  restricted` /
  `linux helper  …codex-linux-sandbox`.
- Same under **no mechanism**, bundled bwrap **present** but non-runnable —
  docker `--security-opt no-new-privileges` (blocks unprivileged userns → bwrap
  cannot create a namespace) + a seccomp profile returning ENOSYS for
  `landlock_create_ruleset`/`landlock_add_rule`/`landlock_restrict_self`,
  the full codex release tree bind-mounted so `codex-resources/bwrap` sits
  next to the executable: rc=1, "bwrap: Creating new namespace failed:
  Permission denied", outside file **absent** (command never executed).
- Same under **no mechanism**, bundled bwrap **absent** (bare musl binary
  only, landlock ENOSYS): **panic** rc=101, "bubblewrap is unavailable: no
  system bwrap was found on PATH and no bundled codex-resources/bwrap binary
  was found next to the Codex executable", outside file **absent**.
- Read-only `CODEX_HOME` (helper-bin materialization blocked): rc=1, WARNING
  "Refusing to create helper binaries under temporary dir …" — yet codex
  **still enforced** (bwrap needs no materialized helper): outside write
  denied "Read-only file system", file **absent**. Helper-failure branch is
  fail-closed too — it does not disagree with the no-mechanism branch.
- `codex doctor`, **both environments** (Task 5B pin): mechanism-available on
  this host reports `✓ sandbox  restricted fs + restricted network · approval
  OnRequest` / `filesystem sandbox  restricted`. Under the SAME no-mechanism
  docker restriction (seccomp ENOSYS on `landlock_*` + `no-new-privileges`
  blocking userns) `codex doctor` STILL reports `✓ sandbox  restricted fs +
  restricted network` / `filesystem sandbox  restricted` / `network sandbox
  restricted` — doctor is an OPTIMISTIC capability report (it materializes the
  landlock helper and does not attempt a live namespace), so it is **not** a
  fail-open signal and must not be trusted as an enforcement gate. In the very
  same container, `codex sandbox -c sandbox_mode=workspace-write -- touch
  /outside/canary` still fails closed ("bwrap: Creating new namespace failed:
  Permission denied", canary **absent**). Load-bearing conclusion: the
  command-level touch probe — not `codex doctor` — is the enforcement evidence.
- Binary strings: the fail-closed panic string above; Landlock/Seccomp
  machinery present (`SandboxLandlock`, `SeccompInstall`, `CreateRuleset`,
  `RestrictSelf`); "execute commands without sandboxing. EXTREMELY DANGEROUS"
  is reachable **only** via `--dangerously-bypass-approvals-and-sandbox`, not
  as a silent failure fallback.

**Backend caveat (does not change the verdict):** the `codex sandbox`
*subcommand* launcher is bubblewrap-based (see `linux-sandbox/src/launcher.rs`
panic), distinct from the doctor-reported agent-tool helper
(`codex-linux-sandbox` = Landlock+seccomp). On hosts with userns enabled
`codex sandbox` uses bwrap; where userns is blocked it would fall to Landlock.
Fail-closed holds even when bwrap is entirely absent AND landlock is ENOSYS,
so the verdict is robust regardless of which backend a given host selects.

**Pinned `codex sandbox` invocation** (used by the Task-6 enforcement test):
`codex sandbox -c sandbox_mode=<read-only|workspace-write|danger-full-access>
-- <cmd…>`. The `codex sandbox` **subcommand has no `-s/--sandbox` flag** —
mode is set only via `-c sandbox_mode=…`; `-c` overrides ARE accepted (parsed
as TOML). (The launch surfaces `codex`/`codex exec` DO take `-s/--sandbox
<mode>` — that flag is for the agent launch, not the `sandbox` subcommand.)
Consequence: **Task 5A (launch-time enforcement guard) is NOT required** —
codex fails closed; Task 5B (evidence-only) applies.

## exec-surface facts (verify probes, degraded mode, fake agent)

Events: `thread.started{thread_id}`, `turn.started`, `turn.completed{usage}`, `turn.failed`,
`item.started|updated|completed` (item types: agent_message, reasoning, command_execution,
file_change, mcp_tool_call, web_search, todo_list), top-level `error`. Items arrive whole
(no text deltas in exec mode). Always: stdin closed, `--skip-git-repo-check`, `-C <dir>`,
`-s <mode>`. `--ephemeral` for no-persistence probes. `-o`/`--output-schema` for structured
finals.

## Config surface (parity target; delta from grok's GrokTaskConfig)

Keep Stage-0 fields; add grok's parity fields (`seed_id`/`SeedProvider`, `on_seed_saved`,
`supports_resume=True`, `workdir_exclude`, `mode: "iframe"|"conversation"`, `permission_gate`,
`conversation_ui`, `tool_verbosity`, `default_model`, `show_model_selector`,
`show_file_upload`/`max_upload_bytes`, `file_download`/`max_download_bytes`, `fs_isolation`,
`extra_allowed_dirs`, `ssh` goes live at Stage 1). Codex-specific vocab: `ask_for_approval`
(exec/TUI `untrusted|on-failure|on-request|never`; note config-level `on-failure` deprecated),
`sandbox` (3 modes), `effort` (app-server reasoning effort). Drop nothing shipped; no `no_leader`
analog. Replicate grok's `__post_init__` cross-validation matrix.

## conversation-ui (Stage 6/7)

`widgetData.protocol = "codex"` → `CodexView` dispatch in `ConversationWidget.tsx`.
Reducer `src/codex/events.ts`: app-server notification vocabulary → ChatItem
(`item/agentMessage/delta` → pending assistant bubble; `item/reasoning/summaryTextDelta` →
activity; `item/started|completed` command_execution/file_change/mcp_tool_call → tool rows;
`requestApproval` server-requests → permission items, correlation by JSON-RPC id;
`turn/completed` → turn end/busy=false; `x-optio-*` synthetics per grok). View = near-copy of
GrokView (listener transport identical): SSE `/events`, POST send/interrupt/permission/model,
upload/download, model selector fed by `model/list`.

## Demo trio (Part 5)

`optio_demo/tasks/codex.py`: seed-setup (`codex-seed-setup`, interactive login in ttyd iframe,
on_seed_saved capture) + seed-pinned iframe (`codex-demo-seed-<id>`, full hook walkthrough) +
seed-pinned conversation (`codex-conversation-seed-<id>`, conversation_ui + gate + selector +
files). Sidecar `{prefix}_demo_codex_seeds`, `fw.resync()` auto-appear, `OPTIO_CODEX_DEMO_SSH_*`.
Plan A ships a plain iframe demo first; trio completes at Stages 3/6.

## Plan sequence

- **A — Stage-0 hardening** (in flight): review criticals/majors/minors, per-task binary path,
  iframe demo, test hardening. `docs/2026-07-02-optio-codex-plan-a-stage0-hardening.md`.
- **B — Stages 1–2**: remote SSH + docker-sshd harness; snapshots + session-id resume.
- **C — Stages 3–5**: seeds (+ pre-trust transform), leases + cred watcher + verify, binary
  cache with real download.
- **D — Stages 6–7**: app-server conversation + listener + conversation-ui + frontend parity
  + conversation demo.
- **E — Stage 8 + release**: native-sandbox isolation, enforcement test, final parity audit,
  README/versions.

Grok test-suite inventory is the coverage bar (~3.7k lines; pattern-vs-specific tagging in the
porting analysis). Real-agent tests env-gated (`OPTIO_CODEX_*_TEST=1`), never in default suite.
