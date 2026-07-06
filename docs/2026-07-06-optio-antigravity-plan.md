# optio-antigravity Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build `optio-antigravity`, the 7th optio coding-agent wrapper, around
Google's Antigravity CLI (`agy`), following `docs/writing-agent-wrappers.md` and
the design in `docs/2026-07-06-optio-antigravity-wrapper-design.md`.

**Architecture:** A `TaskInstance` factory (`create_antigravity_task`) bundling two
mode-adapters — a ttyd-iframe TUI surface and a **synthetic, transcript-driven
conversation mode** (drive each turn with `agy -p --conversation <id>` under a PTY,
read events from `~/.gemini/antigravity/transcript.jsonl`). Everything generic
(workdir lifecycle, `optio.log` loop, hooks, seeds/leases, snapshots) is inherited
from `optio-agents`' `run_log_protocol_session`. Module layout mirrors `optio-grok`
exactly; only the backend-specific pieces diverge.

**Tech Stack:** Python 3.11+ (setuptools `src/` layout), `optio-core` / `optio-host`
/ `optio-agents`; TypeScript/React for the `optio-conversation-ui` engine view;
`ttyd` for the TUI embed; `agy` (Go binary) as the managed subprocess.

## Global Constraints

- **Reference wrapper = `optio-grok`** (ACP/native-sandbox lineage, closest analog).
  Every "mirror grok's `X`" instruction means: copy that file's structure and
  generic logic verbatim, apply only the divergences named in the task. grok is a
  committed, working reference — read it as you implement.
- **Binary:** `agy` (the Go executable is named `antigravity` inside the tarball;
  installed as `agy`). Installer: `https://antigravity.google/cli/install.sh`
  → manifest `<updater>/manifests/<platform>.json` → `{version,url,sha512}`
  (updater host `https://antigravity-cli-auto-updater-974169037036.us-central1.run.app`).
- **Memory file `agy` reads = `AGENTS.md`** (not `CLAUDE.md`; also reads `GEMINI.md`).
- **State tree** (under `~/.gemini`, shared with Gemini CLI):
  `~/.gemini/antigravity-cli/settings.json` (settings),
  `~/.gemini/antigravity/transcript.jsonl` (conversation events),
  `~/.gemini/antigravity/artifacts/` (deliverables),
  `~/.gemini/config/mcp_config.json`, `~/.gemini/jetski/brain/`.
- **Transport reality:** one-shot `agy --print`/`-p` only. **No ACP/stream-json/HTTP.**
  `--print` **swallows stdout under a non-TTY** → a **PTY is mandatory** and the
  conversation source is the transcript file, not stdout.
- **Resume flags:** `--continue`/`-c` (most recent), `--conversation <ID>` (by id).
- **No API-key auth path** — Google OAuth only, creds in the OS keyring. Do not
  design around `GEMINI_API_KEY`/`ANTIGRAVITY_TOKEN` (unsupported).
- **Config injection discipline:** `settings.json` is JSON — mutate the loaded
  document and assert on the parsed result; never blind-append.
- **Naming:** factory `create_antigravity_task`, session `run_antigravity_session`,
  config `AntigravityTaskConfig`, conversation `AntigravityConversation`, seed
  constants `ANTIGRAVITY_SEED_MANIFEST` / `ANTIGRAVITY_CRED_MANIFEST` /
  `ANTIGRAVITY_SEED_SUFFIX`, UI protocol discriminator `"antigravity"`.
- **TDD:** every task = failing test → verify fail → implement → verify pass →
  commit. Fake-`agy` harness green is the bar for Layer-1 tasks. Real-binary /
  real-auth checks are **opt-in, skip-if-no-binary/no-auth** gates (Layer 2/3),
  authored but not required to pass in CI.
- **Don't use `npx`/global installs;** use `.venv` inside the worktree and
  `node_modules/.bin/tsc` directly. Frontend lives in the pnpm workspace.

---

## Stage -1: Pre-stage spikes (resolve design unknowns before the stages they gate)

These are investigations, not TDD tasks. Each writes its finding into the design
doc (append a "Spike results" section) and commits.

### Task S1: Real-login + credential-location spike (gates Stage 3/4) — REQUIRES USER

**Files:** append findings to `docs/2026-07-06-optio-antigravity-wrapper-design.md`.

- [ ] **Step 1:** With the user present, install `agy` into an isolated `HOME`,
  snapshot the full `~/.gemini` tree + keyring state (`secret-tool search` / dump
  the Secret Service collection) **before** login.
- [ ] **Step 2:** Run `agy` (no args) → complete Google OAuth interactively. Note:
  browser-open mechanism (subprocess `xdg-open` vs client-side), and whether an SSH
  run prints a device-code URL (`HeadlessAuthRequired`).
- [ ] **Step 3:** Diff the entire `~/.gemini` tree + keyring after login. Record
  **exactly** which files/keyring entries login provisions (token, provider/account
  registration, `settings.json` deltas).
- [ ] **Step 4:** Determine whether `agy` works with **no** Secret Service present
  (unset `DBUS_SESSION_BUS_ADDRESS`, run in a bare env): does it fall back to an
  encrypted file, or hard-fail? This decides the seed mechanism (design §2 options 1/2/3).
- [ ] **Step 5:** Write the finding (token location, provisioned set, headless
  behavior) into the design doc; commit. **Stage 3/4 tasks below branch on this.**

> Until S1 runs, Stage 3/4 tasks are written against the **most likely** outcome
> (encrypted-file fallback) and flagged to be reconciled with S1's result.

### Task S2: Self-update disable spike (gates Stage 5)

**Files:** append findings to the design doc.

- [ ] **Step 1:** Inspect `~/.gemini/antigravity-cli/settings.json` for an
  `AutoUpdate`/`AutoUpdateTime` key; set `AutoUpdate:false` and observe whether a
  launch still probes the updater endpoint (watch with a blocking `/etc/hosts` entry
  or a null-route to the Cloud Run host).
- [ ] **Step 2:** `strings agy | grep -i update` for an env flag; test candidates in
  the launch env.
- [ ] **Step 3:** Record the working disable mechanism (settings key, env var, or
  endpoint block) in the design doc; commit. Stage 5 uses it.

### Task S3: transcript.jsonl schema capture (gates Stage 6)

**Files:** `packages/optio-antigravity/tests/fixtures/transcript_real.jsonl` (once available).

- [ ] **Step 1:** After S1 login, run one real turn: `agy -p --dangerously-skip-permissions
  'read README and reply DONE'` under a PTY (`script -qec`).
- [ ] **Step 2:** Capture `~/.gemini/antigravity/transcript.jsonl` verbatim
  (including tool calls + any reasoning) as the fixture.
- [ ] **Step 3:** Document the event schema (line types, fields for user/assistant/
  tool/reasoning) in the design doc. Stage 6's reducer is written against it.

---

## Stage 0: MVP — iframe/ttyd task reaches DONE locally

**Deliverable:** an `agy` TUI task launches under ttyd, `AGENTS.md` carries the
`optio.log` protocol, the agent emits `DONE`, teardown is clean — locally.

### Task 0.1: Package scaffold + registration

**Files:**
- Create: `packages/optio-antigravity/pyproject.toml`,
  `packages/optio-antigravity/README.md`,
  `packages/optio-antigravity/.gitignore`,
  `packages/optio-antigravity/src/optio_antigravity/__init__.py`,
  `packages/optio-antigravity/tests/test_import.py`.
- Modify: `packages/optio-demo/Makefile` (add to `LOCAL_PKGS` / `install -e` list),
  `packages/optio-demo/pyproject.toml` (`dependencies`), root `Makefile`
  (`RELEASABLE_PY` list).

**Interfaces:**
- Produces: importable `optio_antigravity` package; `create_antigravity_task`,
  `AntigravityTaskConfig` re-exported from `__init__.py` (stubs until Task 0.3).

- [ ] **Step 1: Write the failing test** — `tests/test_import.py`:

```python
def test_package_imports():
    import optio_antigravity
    assert hasattr(optio_antigravity, "create_antigravity_task")
    assert hasattr(optio_antigravity, "AntigravityTaskConfig")
```

- [ ] **Step 2: Run** `cd packages/optio-antigravity && python -m pytest tests/test_import.py -q` → FAIL (ModuleNotFoundError).
- [ ] **Step 3:** Copy `packages/optio-grok/pyproject.toml` → antigravity's, rename
  `optio-grok`→`optio-antigravity`, `optio_grok`→`optio_antigravity`. Mirror grok's
  `README.md` and `.gitignore`. Write `__init__.py` mirroring grok's exports but with
  antigravity names (see Global Constraints); stub `create_antigravity_task` /
  `AntigravityTaskConfig` importing from `.session` / `.types` (created next tasks —
  temporarily define minimal stubs so the import resolves).
- [ ] **Step 4:** Create `.venv` in the worktree, editable-install the package + deps
  (`pip install -e packages/optio-antigravity -e packages/optio-agents ...` per grok's
  dep set). Run the test → PASS.
- [ ] **Step 5:** Add antigravity to `optio-demo/Makefile` `LOCAL_PKGS`, demo
  `pyproject.toml` deps, and root `Makefile` `RELEASABLE_PY`. Commit.

### Task 0.2: `types.py` — config dataclass + validation

**Files:** Create `src/optio_antigravity/types.py`; Test `tests/test_config.py`.

**Interfaces:**
- Produces: `AntigravityTaskConfig` (dataclass), `PermissionMode`, `ConversationMode`
  (`Literal["iframe","conversation"]`), `ToolVerbosity`, `ThinkingVerbosity`,
  `SeedProvider`, `SeedUnavailableError`, `AllowedDir`. Field names mirror grok's
  `GrokTaskConfig` (design Appendix D is the canonical field list).

- [ ] **Step 1: Write failing tests** — `tests/test_config.py`:

```python
import pytest
from optio_antigravity.types import AntigravityTaskConfig

def test_conversation_ui_requires_conversation_mode():
    with pytest.raises(ValueError, match="conversation_ui"):
        AntigravityTaskConfig(consumer_instructions="x", mode="iframe",
                              conversation_ui=True)

def test_default_mode_is_iframe_and_auto_start_false():
    c = AntigravityTaskConfig(consumer_instructions="x")
    assert c.mode == "iframe"
    assert c.auto_start is False   # a conversation task must not auto-fire

def test_invalid_permission_mode_rejected():
    with pytest.raises(ValueError):
        AntigravityTaskConfig(consumer_instructions="x", permission_mode="bogus")
```

- [ ] **Step 2: Run** `pytest tests/test_config.py -q` → FAIL.
- [ ] **Step 3:** Mirror grok's `types.py` verbatim, renaming `Grok`→`Antigravity`.
  Divergences: `PermissionMode` values for `agy` = `Literal["default",
  "dangerously-skip-permissions"]` (map `bypassPermissions`→the skip flag); keep
  `AllowedDir`, `SeedProvider`, `ConversationMode`, verbosity types identical. Keep
  `__post_init__` cross-field validation (`conversation_ui` requires
  `mode=="conversation"`; validate enums).
- [ ] **Step 4: Run** → PASS. **Step 5:** Commit.

### Task 0.3: `host_actions.py` (host bind + install) + `prompt.py` + `session.py` iframe body

**Files:** Create `src/optio_antigravity/host_actions.py`, `prompt.py`, `session.py`;
Test `tests/test_prompt.py`, `tests/test_host_actions.py`, plus the fake harness
`tests/fake_agy.py`, `tests/agy-shim.sh`, `tests/ttyd-shim.sh`, `tests/conftest.py`.

**Interfaces:**
- Produces: `build_host(cfg) -> Host`; `ensure_antigravity_installed(host, ...) -> str`
  (per-task binary path); `compose_agents_md(features, consumer_instructions,
  resume, host_protocol) -> str`; `create_antigravity_task(...) -> TaskInstance`;
  `run_antigravity_session(...)`.
- Consumes: `run_log_protocol_session`, `get_protocol`, `HookContext` from
  `optio_agents`; `LocalHost`/`RemoteHost` from `optio_host`.

- [ ] **Step 1: Fake harness.** Mirror grok's `tests/fake_grok.py` +
  `grok-shim.sh` + `ttyd-shim.sh` + `conftest.py` → `fake_agy.py` etc. The fake
  `agy` must honor: `--help`, `models`, `-p/--print <prompt>` (emit a canned reply
  AND append a canned line to `$HOME/.gemini/antigravity/transcript.jsonl`),
  `--continue`/`--conversation <id>` (echo the id back), and a TUI mode that writes
  `optio.log` lines (`STATUS:`/`DELIVERABLE:`/`DONE`). Model it on how grok's fake
  drives the log protocol.
- [ ] **Step 2: Write failing test** — `tests/test_prompt.py`:

```python
from optio_antigravity.prompt import compose_agents_md
from optio_agents.protocol.protocol import get_protocol

def test_agents_md_has_log_protocol_and_resume_pull():
    proto = get_protocol(host_protocol=True)
    md = compose_agents_md(proto.features, consumer_instructions="Do the thing",
                           resume=True, host_protocol=True)
    assert "optio.log" in md
    assert "DONE" in md
    assert "resume.log" in md            # pull half of resume awareness
    assert "Do the thing" in md          # verbatim consumer instructions
```

- [ ] **Step 3: Run** → FAIL. **Step 4:** Implement `prompt.py` mirroring grok's
  `compose_agents_md` (uses `build_log_channel_prompt(features)` SSOT); divergence:
  target file is `AGENTS.md`; honor `host_protocol=False` (omit keyword docs, add the
  `System:` explainer). Implement `host_actions.build_host` (mirror grok's — only the
  Local-vs-Remote bind branch may `isinstance`). Implement
  `ensure_antigravity_installed` as a **Stage-0 stub** that copies a worker `agy` on
  PATH (Tier-1); real vendor-install lands in Stage 5. Implement `session.py`
  `create_antigravity_task` + `run_antigravity_session` with the **iframe/ttyd body**
  mirroring grok's ttyd branch (`body`/`_prepare`/`_agent_sender` → `run_log_protocol_session`).
- [ ] **Step 5: Run** `pytest tests/ -q` → PASS. Commit.

### Task 0.4: Stage-0 local end-to-end (fake) + DONE

**Files:** Test `tests/test_session_local.py` (mirror grok's).

- [ ] **Step 1:** Write a test that builds a task with the fake `agy`, runs it
  locally, and asserts it reaches completion (`DONE` → clean return) and tears down.
- [ ] **Step 2–4:** Run → fix → PASS. **Step 5:** Commit.
- [ ] **Real gate (opt-in):** `tests/test_real_binary_gates.py::test_iframe_reaches_done`
  — skip-if-no-`agy`; drives the real TUI to `DONE`. Authored now, run in S1/later.

---

## Stage 1: Remote / SSH

**Deliverable:** the Stage-0 task runs identically over SSH.

### Task 1.1: SSH parity

**Files:** Modify `host_actions.py` (RemoteHost branch); Test
`tests/test_session_remote.py`, `tests/docker-compose.sshd.yml`, `tests/Dockerfile.sshd`.

- [ ] **Step 1:** Mirror grok's docker-sshd harness + `test_session_remote.py`.
- [ ] **Step 2:** Write a test running the fake-`agy` task with an `ssh` config
  against the sshd container; assert identical DONE behavior.
- [ ] **Step 3:** Ensure `build_host` selects `RemoteHost` on `cfg.ssh`; all agy I/O
  goes through generic `Host` primitives (no new `isinstance`).
- [ ] **Step 4:** Run → PASS. **Step 5:** Commit.

---

## Stage 2: Resume / snapshots

**Deliverable:** a terminated task relaunches and picks up the conversation +
workdir; agent receives the pushed resume notice.

### Task 2.1: `snapshots.py` + resume wiring

**Files:** Create `src/optio_antigravity/snapshots.py`; Modify `session.py`,
`prompt.py`; Test `tests/test_snapshots.py`, `tests/test_session_resume.py`.

**Interfaces:**
- Produces: `_capture_snapshot` / restore (workdir tar + session-state blob);
  `supports_resume` config field; `workdir_exclude` (exclude the binary + large caches).

- [ ] **Step 1:** Mirror grok's `snapshots.py` + resume tests. Divergences:
  resume identity is `agy`'s `--conversation <id>` / `--continue`; the session-state
  blob captures the captured conversation id + `~/.gemini/antigravity/transcript.jsonl`
  + `~/.gemini/antigravity-cli/settings.json`. **workdir must restore to the identical
  path** (agy keys artifacts under the workspace).
- [ ] **Step 2:** Add **both halves of resume awareness** to `prompt.py`: the
  `resume.log` pull doc AND `build_resume_notice_args` for the iframe positional
  (`agy --continue '<RESUME_NOTICE>'`) + the conversation-body `RESUME_NOTICE` send
  (Stage 6). Test that a relaunch restores state AND the notice is delivered.
- [ ] **Step 3–4:** Run → PASS. **Step 5:** Commit.
- [ ] **Real gate (opt-in):** `test_real_binary_gates.py::test_resume_picks_up_prior`.

---

## Stage 3: Seeds  *(branch on Task S1 result)*

**Deliverable:** a seed captured from a logged-in session launches a fresh task
already authenticated — verified functionally (never shows the login screen).

### Task 3.1: `seed_manifest.py`

**Files:** Create `src/optio_antigravity/seed_manifest.py`; Test
`tests/test_seed_manifest.py`.

**Interfaces:**
- Produces: `ANTIGRAVITY_SEED_MANIFEST`, `ANTIGRAVITY_CRED_MANIFEST`,
  `ANTIGRAVITY_SEED_SUFFIX`, `delete_seed`/`list_seeds`/`purge_seed` (mirror grok).

- [ ] **Step 1:** Mirror grok's `seed_manifest.py` adopting the generic
  `optio_agents.seeds` engine. **Manifest contents branch on S1:**
  - Full **seed** manifest: `~/.gemini/antigravity-cli/settings.json` + provider/
    account registration + the token store location S1 found (encrypted file **or**
    a keyring-export blob) + `~/.gemini/antigravity/` non-secret state.
  - **cred** (save-back) manifest: only the file(s) that rotate mid-session (the
    token store).
- [ ] **Step 2:** Write tests asserting the manifest captures the provisioned set
  (not just the token) and that a creds-only capture is rejected without a valid token.
- [ ] **Step 3–4:** Run → PASS. **Step 5:** Commit.
- [ ] **Real gate (opt-in):** `test_seed_replant` — a fresh task from a captured seed
  reaches a working state without the login screen (functional check).

---

## Stage 4: Leases + credential save-back + verify

**Deliverable:** concurrent sessions share a seed pool safely; rotated tokens
persist and survive a cancelled session; a stale seed is refreshed offline.

### Task 4.1: `cred_watcher.py` + graceful-flush teardown

**Files:** Create `src/optio_antigravity/cred_watcher.py`; Modify `session.py`
(teardown); Test `tests/test_cred_watcher.py`, `tests/test_conversation_teardown.py`.

- [ ] **Step 1:** Mirror grok's `cred_watcher.py` (in-session watcher saving rotated
  tokens back into the seed) + the `finally` backstop. Divergence: watch the token
  store S1 identified. **Gate save-back on a valid (non-empty refresh) token.**
- [ ] **Step 2:** Implement `_teardown_aggressive` gating — SIGTERM-and-wait (flush)
  for a **seeded** session even on cancel; fast kill only for non-seeded.
- [ ] **Step 3:** Tests: rotated token persists; a SIGKILL-on-cancel of a seeded
  session does NOT persist a stale token (graceful path taken).
- [ ] **Step 4:** Run → PASS. **Step 5:** Commit.

### Task 4.2: `verify.py` — host-free Google OIDC refresh

**Files:** Create `src/optio_antigravity/verify.py`; Test `tests/test_verify.py`.

**Interfaces:**
- Produces: `verify_and_refresh_seed(seed_id, ...) -> status` (fail-closed:
  `invalid_grant`→dead; transport/discovery failure→inconclusive; valid→confirmed).

- [ ] **Step 1:** Mirror grok's `verify.py` OIDC-discovery pattern. Divergence:
  Google issuer `https://accounts.google.com/.well-known/openid-configuration`;
  public CLI `client_id` discovered from the seed (record it in S1). Standard
  `refresh_token` grant; write rotated tokens back. **Host-free, non-billable.**
- [ ] **Step 2:** Tests with a mocked token endpoint: confirmed/dead/inconclusive
  paths; a healthy seed is never retired on a transport failure.
- [ ] **Step 3–4:** Run → PASS. **Step 5:** Commit.

### Task 4.3: Lease wiring + `test_session_lease.py`

- [ ] Mirror grok's lease wiring (`acquire`/`renew_lease`/`release` via
  `optio_agents.seeds`); test two concurrent sessions on one pool don't strand each
  other. Commit.

---

## Stage 5: Binary cache + HOME/XDG isolation + self-update off  *(uses Task S2)*

**Deliverable:** evictable binary cache with real auto-install on miss; per-task
identity; self-update disabled; snapshots exclude the binary.

### Task 5.1: Real two-tier install + isolation env

**Files:** Modify `host_actions.py`; Test `tests/test_antigravity_cache.py`.

**Interfaces:**
- Produces: `_resolve_install_dir`, `_isolation_env`, `ensure_antigravity_installed`
  (Tier-1 copy worker `agy`; Tier-2 fetch manifest+tarball into evictable cache;
  symlink into `<workdir>/home/.local/bin/agy`), `build_launch_env` (self-update off).

- [ ] **Step 1:** Mirror grok's `_resolve_install_dir`/`_isolation_env`/
  `ensure_*_installed`. Divergences: Tier-2 downloads `<updater>/manifests/
  <platform>.json` → tarball, SHA512-verifies (reproduce the installer's logic),
  extracts the `antigravity` binary → cache → symlink as `agy`. Per-task
  `HOME`/`XDG_*` under the workdir (so `~/.gemini` is per-task).
- [ ] **Step 2:** Functional identity check — run a known-only subcommand (e.g.
  `agy models` returns the sign-in error string, not "unknown command") with a
  `timeout`; reject a mismatched worker binary and invalidate a poisoned cache.
- [ ] **Step 3:** `build_launch_env` sets the **self-update disable** mechanism from
  S2 (settings key write and/or env flag) on **every** launch path.
- [ ] **Step 4:** Tests (fake): cache miss triggers install; hit relinks; snapshot
  excludes the binary; isolation env points `HOME` into the workdir.
- [ ] **Step 5:** Run → PASS. Commit.
- [ ] **Real gate (opt-in):** `test_real_binary_gates.py::test_bare_worker_installs`
  — no worker `agy` present → Tier-2 provisions it.

---

## Stage 6: Conversation mode + conversation-ui  *(uses Task S3)*

**Deliverable:** a caller drives a live (synthetic) conversation; the same task
renders in the dashboard chat widget.

### Task 6.1: `conversation.py` — synthetic transcript-driven `Conversation`

**Files:** Create `src/optio_antigravity/conversation.py`; Test
`tests/test_conversation.py`.

**Interfaces:**
- Produces: `AntigravityConversation` implementing the `optio_agents.conversation`
  Protocol (`send`, `on_event`, `on_message`, `on_permission_request`, `is_pending`,
  `interrupt`, `close`, `closed`).

- [ ] **Step 1: Write failing tests** — `tests/test_conversation.py`:

```python
import pytest
from optio_antigravity.conversation import AntigravityConversation

@pytest.mark.asyncio
async def test_send_runs_print_turn_and_emits_final_message(fake_agy_conv):
    conv = fake_agy_conv           # fixture: AntigravityConversation over fake agy
    events = []
    conv.on_event(lambda e: events.append(e))
    msgs = []
    conv.on_message(lambda m: msgs.append(m))
    await conv.send("say PONG")
    assert any(m.text.strip() == "PONG" for m in msgs)      # one final answer/turn
    assert conv.closed is False

@pytest.mark.asyncio
async def test_second_turn_uses_conversation_id(fake_agy_conv):
    conv = fake_agy_conv
    await conv.send("first")
    cid = conv.conversation_id
    assert cid                     # captured from turn 1
    await conv.send("second")
    assert conv.last_argv_contains(f"--conversation {cid}")  # resumed, not new

@pytest.mark.asyncio
async def test_interrupt_kills_inflight_turn(fake_agy_slow):
    conv = fake_agy_slow
    task = __import__("asyncio").ensure_future(conv.send("slow"))
    await conv.interrupt()
    with pytest.raises(Exception):
        await task
```

- [ ] **Step 2: Run** → FAIL. **Step 3: Implement** `AntigravityConversation`:
  - `send(text)`: spawn `agy -p [--conversation <id>|--new-project] --model <m>
    --dangerously-skip-permissions <text>` via the host under a **PTY**; on turn 1,
    capture the conversation id (from `transcript.jsonl` header or `agy` output).
  - Tail `~/.gemini/antigravity/transcript.jsonl` during the turn → parse each new
    line into a raw event; `on_event` fans out live (synthetic optio events use the
    `x-optio-` prefix); at turn end emit one `on_message` (coalesced answer).
  - `is_pending` true while a turn's process is live; `interrupt()` kills it;
    `close()` sets `closed`; `send`/`interrupt` after close raise `ConversationClosed`.
  - `on_permission_request` is a no-op (turns run skip-permissions — design §7).
- [ ] **Step 4: Run** → PASS. **Step 5:** Commit.

### Task 6.2: `conversation_listener.py` + `session.py` conversation branch

**Files:** Create `src/optio_antigravity/conversation_listener.py`; Modify
`session.py`; Test `tests/test_conversation_listener.py`, `tests/test_session_conversation.py`.

- [ ] **Step 1:** Mirror grok's `conversation_listener.py` (SSE/route surface the
  dashboard consumes) + the conversation branch of `session.py` (`mode="conversation"`,
  `host_protocol` toggle, `conversation_ui`, `publish_result` of the Conversation).
- [ ] **Step 2:** Tests: a task in conversation mode publishes a Conversation; the
  listener streams `on_event`/`on_message` over SSE; `/control` route exists (Stage 7).
- [ ] **Step 3–4:** Run → PASS. **Step 5:** Commit.

### Task 6.3: conversation-ui reducer + view (TypeScript)

**Files:** Create `packages/optio-conversation-ui/src/antigravity/events.ts`,
`.../antigravity/AntigravityView.tsx`; Modify `.../src/ConversationWidget.tsx`
(dispatch on `protocol==="antigravity"`), `.../src/index.ts`; Test
`.../src/__tests__/antigravity-events.test.ts`,
`.../src/__tests__/antigravity-widget.test.tsx`,
`.../src/__tests__/antigravity-real-wire.test.ts`.

**Interfaces:**
- Produces: pure reducer `(state, rawEvent, seq) => ChatState` mapping transcript
  events → `ChatItem` union; `AntigravityView` transport-adapter wiring
  `ConversationViewProps` (`onSend`/`onInterrupt`/`onPermission`/`onFileDownload`/
  `onControlChange`).

- [ ] **Step 1: Write failing reducer test** — feed a small transcript-event
  sequence (user, assistant deltas, one tool call, turn-end) and assert the reducer
  yields: one coalesced assistant bubble, a tool row, `busy` cleared at turn end.
  (Model on grok's `events.test.ts`.)
- [ ] **Step 2: Run** (`node_modules/.bin/vitest run`) → FAIL.
- [ ] **Step 3:** Implement `events.ts` reducer against the S3 transcript schema;
  `AntigravityView.tsx` mirroring grok's view (opens the listener SSE, feeds the
  reducer, hands rendering to shared `ConversationView`). Add the `"antigravity"`
  discriminator to `ConversationWidget` + `index.ts` export.
- [ ] **Step 4: Run** → PASS. **Step 5:** Commit.
- [ ] **Real-wire fixture (Layer 3, from S3):** `antigravity-real-wire.test.ts`
  replays the captured real transcript through the reducer, asserting a human-correct
  `ChatState` (coalesced answer, reasoning in its own rows). Skip-if-no-fixture.

---

## Stage 7: Frontend parity

**Deliverable:** session controls (model + extras), file up/down, tool/thinking
verbosity work in the widget; `set_control("model", …)` switches the model.

### Task 7.1: Session controls (model, restart-based switch)

**Files:** Modify `conversation.py` (`set_control`), create
`src/optio_antigravity/models.py`; Modify `conversation_listener.py` (`/control`
route), `AntigravityView.tsx`; Test `tests/test_models.py`,
`tests/test_conversation_controls.py`,
`.../src/__tests__/antigravity-controls.test.tsx`.

**Interfaces:**
- Produces: `models.py` `list_models()` / model id mapping (from `agy models`);
  `AntigravityConversation.set_control(id, value)` (model switch = restart the
  session with `--model <new>` + `--continue`, claudecode precedent); `SessionControl[]`
  emitted in `widgetData.controls`.

- [ ] **Step 1:** Mirror grok's `models.py` + `test_models.py`. Divergence: parse
  `agy models` output; expose model ids (Gemini + BYO Claude/GPT).
- [ ] **Step 2:** Implement `set_control("model", v)` = restart-with-new-model.
  Emit the generic `model` `SessionControl`; auto-mark a ≤1-option control `disabled`
  with a reason. Add the `/control` listener route + `onControlChange` wiring.
- [ ] **Step 3:** Tests: `set_control("model", …)` restarts with the new model +
  `--continue`; the control round-trips through the listener to the UI.
- [ ] **Step 4:** Run (py + vitest) → PASS. **Step 5:** Commit.

### Task 7.2: File upload/download + tool/thinking verbosity

**Files:** Modify `conversation_listener.py` (upload/download routes),
`session.py`/`types.py` (config fields), `AntigravityView.tsx`; Test
`tests/test_file_upload.py`, `tests/test_file_download.py`.

- [ ] **Step 1:** Mirror grok's upload/download (`optio-file:` sentinel;
  agy deliverables land in `~/.gemini/antigravity/artifacts/` → map to downloads).
- [ ] **Step 2:** Forward `tool_verbosity` + `thinking_verbosity` as
  `widgetData.<camelCase>`; the reducer renders reasoning rows distinctly (default
  `thinking_verbosity="hidden"`).
- [ ] **Step 3–4:** Run → PASS. **Step 5:** Commit.

---

## Stage 8: Filesystem isolation

**Deliverable:** the agent is sandboxed to workdir + explicit grants; default-on,
fail-closed, local and remote.

### Task 8.1: `fs_allowlist.py` + claustrum wrap

**Files:** Create `src/optio_antigravity/fs_allowlist.py`; Modify `session.py`,
`host_actions.py`; Test `tests/test_fs_allowlist.py`,
`tests/test_sandbox_enforce.py`, `tests/test_conversation_sandbox_enforce.py`.

**Interfaces:**
- Consumes: `optio_agents.claustrum.ensure_claustrum_installed` (shared; pass the
  **per-engine cache dir** as a param; functional validation + ELF guard).
- Produces: `_build_claustrum_wrap(cfg, ...)`; `fs_isolation`/`extra_allowed_dirs`/
  `delivery_type` config fields.

- [ ] **Step 1:** Mirror claudecode/kimicode `fs_allowlist.py` + `_build_claustrum_wrap`.
  Note: `agy` has a **native `--sandbox`** — decide (design) to use claustrum as the
  outer kernel enforcement and optionally combine with `--sandbox`. Default-on,
  fail-closed. Grants: workdir + temp + `~/.gemini` (agy needs its config tree) +
  `extra_allowed_dirs`.
- [ ] **Step 2:** Tests mirror grok's `test_sandbox_enforce.py` /
  `test_conversation_sandbox_enforce.py` (opt-in, skip-if-no-claustrum).
- [ ] **Step 3–4:** Run → PASS. **Step 5:** Commit.

---

## Stage 9: Demo tasks + packaging finalization (guide Part 5)

**Deliverable:** the wrapper is demonstrated end-to-end in the dashboard; seed
lifecycle demonstrable.

### Task 9.1: Demo trio

**Files:** Create `packages/optio-demo/src/optio_demo/tasks/antigravity.py`;
Modify `packages/optio-demo/src/optio_demo/tasks/__init__.py`; Test
`packages/optio-demo/tests/test_antigravity_tasks.py`.

**Interfaces:**
- Produces: `async def get_tasks(services) -> list[TaskInstance]` — one
  **seed-setup** task (log in / configure once, stop to capture a seed) + **two
  seed-pinned** run tasks (one **iframe**, one **conversation**), aggregated in
  `get_task_definitions`.

- [ ] **Step 1:** Mirror `optio-demo/.../tasks/grok.py` (seed-setup + the
  `_make_on_seed_saved` capture wiring + the two seed-pinned tasks). **Copy the
  reference `CONSUMER_PROMPT` verbatim** — the "read `context.txt`, ask the human for
  their favorite color, ship a deliverable, signal `DONE`" prompt. Diff against
  grok's to confirm identical (the two-way dialogue clause is load-bearing).
- [ ] **Step 2:** `before_execute` hook ships `context.txt`. Aggregate `get_tasks`
  in `tasks/__init__.py`.
- [ ] **Step 3:** Test (mirror `test_kimicode_tasks.py`): tasks build, the DONE-only
  test prompt is used for automated runs, the trio is present.
- [ ] **Step 4:** Run → PASS. **Step 5:** Commit.

---

## Stage 10: Real-binary acceptance (guide Testing Layer 2/3) — REQUIRES USER AUTH

**Deliverable:** every surface exercised against the **real** `agy` (row 30). These
are the opt-in gates authored throughout; run them once the user can log in.

### Task 10.1: Run the real-binary checklist

- [ ] iframe/ttyd — launches, renders, accepts input, reaches `DONE`.
- [ ] conversation — real `agy -p` turn under PTY, transcript tailed, one coalesced
      answer bubble, `busy` cleared.
- [ ] fs-isolation ON on each surface — claustrum actually applies.
- [ ] first-login end-to-end (browser-intercept / device-code) → creds land → seed
      captured (functional: no login screen on replant).
- [ ] seed replant — a fresh task starts already-authenticated.
- [ ] resume — a relaunch picks up the prior conversation.
- [ ] remote (SSH) — at least one surface end-to-end, plus SSH device-code login.
- [ ] Record results; mark any surface that fails as a tracked gap. Commit.

---

## Self-Review (against the design spec)

- **§1 profile** → Stages 0/6 (modes), 2 (resume), 5 (install), 7 (models). ✔
- **§2 auth/seeds** → Task S1 + Stages 3/4. ✔ (branches on S1; flagged).
- **§3 install/self-update** → Task S2 + Stage 5. ✔
- **§5 conversation (transcript-driven)** → Task S3 + Stage 6. ✔
- **§6 stages** → Stages 0–8 map 1:1. ✔
- **§7 parity gaps** → encoded: no streaming (6.1 one-message-per-turn), turn-level
  permissions (6.1 no-op `on_permission_request`), coarse interrupt (6.1). ✔
- **§8 open items** → Tasks S1–S3 + Stage-5 self-update. ✔
- **Guide Part 5 (packaging/demo/real-binary)** → Task 0.1 + Stages 9/10. ✔

**Placeholder scan:** the "mirror grok's `X`" instructions reference a committed,
real file — not a placeholder. Antigravity-specific logic (transcript reducer,
print-turn driver, seed branch, self-update, models) has concrete test code.

**Type consistency:** names fixed in Global Constraints
(`create_antigravity_task`/`AntigravityTaskConfig`/`AntigravityConversation`/
`ANTIGRAVITY_*`/`"antigravity"` discriminator) used consistently across tasks.

**Known dependency on user:** Tasks S1, S3, Stage 10 (and functional seed checks in
3/4) require a real Google login. All other tasks reach fake-harness-green without it.
