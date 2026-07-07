# Agents-All — Spec A: Config-Surface Harmonization

**Date:** 2026-07-07
**Branch:** `csillag/agents-all` (worktree)
**Status:** Approved design (brainstorming output); implementation plan to follow.
**Program:** This is **Spec A of three**. The goal of the program is a single
meta-factory entry point (`optio-agents-all`) that creates any of the 7 agent
tasks from one dict with an `agent_type` discriminator. That requires the 7
`TaskConfig` surfaces to be uniform except where a real agent-behavior
difference justifies divergence. The three specs:

- **Spec A (this doc)** — harmonize the config surfaces + wire the missing
  generic features + extract shared types. No new package, no meta-factory yet.
- **Spec B** — reasoning-effort as a live `SessionControl` (new `slider` kind),
  wired across the 6 reachable engines.
- **Spec C** — `optio-agents-all` package + the discriminated-union
  `create_task(agent_type, params)` entry point. Depends on A and B.

## The 7 wrappers

kimicode, grok, cursor, claudecode, codex, opencode, antigravity. Each exposes
`create_<engine>_task(process_id, name, config, description=None, metadata=None)
-> TaskInstance`, taking a fully-constructed `<Engine>TaskConfig` frozen
dataclass (validated in `__post_init__`).

## Goal

After Spec A, the 7 `TaskConfig` classes share an **identical common core**
(same field names, types, defaults, validation, imported from shared type
aliases), and every remaining difference is a **justified, agent-native delta**
that Spec C's union will type per variant. No field diverges for cosmetic or
historical reasons.

## Non-goals

- The meta-factory itself (Spec C).
- Reasoning-effort wiring / the `slider` control (Spec B). Kimi's `effort`
  field is therefore **left in place** here (Spec B repurposes it); only
  antigravity's genuinely-dead effort fields are removed.
- Actually enforcing opencode filesystem isolation (claustrum port is a
  deferred follow-up; Spec A only adds the accepted-but-inert surface).

## Current-state summary (the survey)

- **Parity core already identical across all 7 (26 fields):**
  `consumer_instructions`, `env`, `scrub_env`, `ssh`, `install_if_missing`,
  `before_execute`, `after_execute`, `on_deliverable`, `seed_id`,
  `on_seed_saved`, `supports_resume`, `workdir_exclude`, `mode`,
  `host_protocol`, `conversation_ui`, `tool_verbosity`, `thinking_verbosity`,
  `default_model`, `show_session_controls`, `show_file_upload`, `on_upload`,
  `max_upload_bytes`, `file_download`, `max_download_bytes`, `auto_start`,
  `native_spinner`.
- **Divergences addressed by this spec** are itemized in "Changes" below.
- **Justified native deltas** (left per-engine, typed by Spec C's union) are in
  "What stays native."

## Changes

### 1. Rename `<agent>_install_dir` → `install_dir` (all 7)

`kimi_install_dir` / `grok_install_dir` / `cursor_install_dir` /
`claude_install_dir` / `codex_install_dir` / `opencode_install_dir` /
`agy_install_dir` → a single **`install_dir: str | None = None`** (absolute-path
validation unchanged). Update each engine's `session.py` / `host_actions.py`
read site. `ttyd_install_dir` is a **separate** binary and stays as-is (only on
the ttyd engines — see native deltas).

### 2. `model` → one field; drop `default_model`

Today there are two inconsistently-covered fields: `model` (launch/initial
model, 6 engines, absent in opencode) and `default_model` (conversation-picker
preselect, 6 engines, absent in claudecode). They both mean "the initial /
active model." Collapse to a single **`model: str | None = None`** on all 7:

- **opencode**: `default_model` → `model`. opencode has no launch `--model`
  flag; `model` maps to opencode's existing initial-model path (server
  `defaultModel` config + seeded `opencode.json` `cfg.model`), and live
  switching stays per-prompt.
- **The 5 with both** (kimi/grok/cursor/codex/antigravity): drop
  `default_model`, keep `model`. The conversation picker's initial value is
  sourced from `model`.
- **claudecode**: already single `model` — no change.
- **Validation:** `model` is valid in **all** modes (it is the launch/initial
  model), so the old `default_model` "requires conversation_ui" gate is dropped.
- Live model-switching (`set_control("model", …)`) is unchanged in every engine
  and is out of scope here.

### 3. `AllowedDir.mode` → superset `ro/rw/rox/rwx` (all engines)

Unify the grant-mode enum to the 4-value superset. kimi/grok/codex/antigravity
currently accept only `ro/rw`; they gain `rox/rwx`, and — being Landlock-only
sandboxes with no separate execute bit — treat `rox`≡`ro` and `rwx`≡`rw`
(documented on the shared type). cursor/claudecode already use the 4-value set.

### 4. Extract shared type aliases into `optio-agents`

Each wrapper currently redefines identical `Literal`/callable aliases. Move the
**shared** ones to `optio_agents` and have all 7 import them:

- `ConversationMode = Literal["iframe","conversation"]`
- `ToolVerbosity = Literal["silent","description-only","verbose"]`
- `ThinkingVerbosity = Literal["hidden","visible"]`
- `SeedProvider = Callable[[str], Awaitable[str]]`
- `AllowedDir` (frozen dataclass, `mode: Literal["ro","rw","rox","rwx"]`, with
  its `__post_init__` validation) — one definition, imported by all 7.

`permission_mode` is **not** shared (4 different value-sets — see native
deltas). Callback aliases already sourced from `optio_agents`
(`HookCallback`, `DeliverableCallback`, `UploadCallback`,
`CallerMessageCallback`) stay there.

### 5. Add + WIRE the missing generic features

Each of these is a framework-level feature already implemented on some engines;
Spec A ports the working pattern to the engines lacking it. **Adding the field
means implementing the behavior, not just declaring it.**

| Feature | Fields | Has today | Port to |
|---|---|---|---|
| At-rest session-blob encryption | `session_blob_encrypt` / `session_blob_decrypt` (paired; asymmetric → ValueError) | kimi, claude, opencode | grok, cursor, codex, antigravity — apply the encrypt/decrypt callables in each engine's snapshot capture/restore path |
| Resume-refresh hook | `on_resume_refresh` (default: identity callable, matching claude/opencode) | claude, opencode | kimi, grok, cursor, codex, antigravity — fire the hook on resume |
| Caller-message channel | `use_client_messages` (bool) + `on_caller_message` (callback) | claude, opencode | kimi, grok, cursor, codex, antigravity — port the `CLIENT_MESSAGE`/`CALLER_MESSAGE` keyword channel (log-protocol level) |
| Tool allow/deny | `allowed_tools` / `disallowed_tools` | **wired** in claude (`--allowed-tools`/`--disallowed-tools`), grok (`--allow`×N + `--disallowed-tools`), cursor (plants `.cursor/cli-config.json`) | **kimi + antigravity fields are DEAD** (declared, never consumed); **codex + opencode absent.** All four are **RESEARCH-GATED** (see Open research): wire to the engine's native tool-gating where one exists, else remove the dead field (kimi/antigravity) / don't add (codex/opencode) and record as a native gap. Do NOT ship an inert allow/deny field — a security control that silently does nothing is worse than absent (the antigravity-effort mistake). |

Defaults for the added fields match the reference engines
(`session_blob_*`=None, `on_resume_refresh`=identity, `use_client_messages`=False,
`on_caller_message`=None, `allowed_tools`/`disallowed_tools`=None).

### 6. opencode: add inert `fs_isolation` + `extra_allowed_dirs`

opencode currently launches `opencode web` **unsandboxed** (no claustrum). Add
the fs-isolation surface now for config parity, **accepted but not yet
enforced**:

- `fs_isolation: bool = True` — **defaults True for parity**, but since opencode
  cannot enforce it yet, this is a **known no-op**. It MUST warn loudly: a code
  comment on the field AND a **runtime warning on the server console** at launch
  ("fs_isolation requested but not yet enforced for opencode — claustrum port
  pending"). Wiring the actual claustrum wrap is a **deferred follow-up** after
  this program lands.
- `extra_allowed_dirs: list[AllowedDir] | None = None` — accepted, inert until
  the claustrum port.

### 7. Remove antigravity's dead effort fields

`effort` and `reasoning_effort` on `AntigravityTaskConfig` are dead (zero
readers; agy's thinking level is baked server-side into the model id, with no
flag/config/env lever). Remove both. (Kimi's `effort` stays — reachable via
`config.toml [thinking].effort`; Spec B repurposes it.)

## What stays native (typed per-variant by Spec C's union)

- **Permission / safety model** — 4 distinct `permission_mode` value-sets (kimi
  `manual/auto/yolo`; grok & claude claude-style; antigravity
  `default/dangerously-skip-permissions`), plus cursor `sandbox`/`force`/
  `auto_review`, codex `ask_for_approval`/`sandbox`/`network_access`. opencode
  has none (native config passthrough).
- **`permission_gate`** — a bool on 6 engines; opencode's gate is always-on
  passthrough (policy lives in `opencode_config`), so no bool. Structural.
- **ttyd** — `install_ttyd_if_missing` + `ttyd_install_dir` on the 5 ttyd
  engines (grok/cursor/claude/codex/antigravity); kimi/opencode are web SPAs.
- **effort** — grok's general `--effort`, grok's `reasoning_effort`, kimi's
  `effort` — handled/harmonized in Spec B, not here.
- **Agent config blobs** — claude (`claude_config`, `focus_mode`,
  `include_partial_messages`, `credentials_json`, `session_restore_from/until`,
  `on_session_saved`, `delivery_type`); `opencode_config`; cursor `api_key`.
- **grok** `no_leader`.

## Open research (resolve in the plan phase)

**Tool allow-deny reachability for FOUR engines (change 5, tool row):** kimi,
antigravity (fields declared but dead), codex, opencode (fields absent). For
each, verify whether the agent exposes a native per-tool allow/deny mechanism
the wrapper can drive (kimi's `config.toml` only has `default_permission_mode`;
antigravity only `--dangerously-skip-permissions`; codex app-server config;
opencode `opencode.json` permissions). **If reachable:** wire it (flags like
grok/claude, or a planted config like cursor) + a round-trip test. **If not
reachable:** remove the dead field (kimi/antigravity) and leave it unset
(codex/opencode) — record tool allow/deny as a native gap for those engines,
matching how antigravity effort was removed. Unlike opencode's inert
`fs_isolation` (a deferred-but-planned feature), a tool allow/deny that does
nothing is a **security-misleading no-op** and must not ship — remove rather
than accept-inert.

## Testing

- Each wrapper's existing config tests (`test_types.py` / `test_config.py`)
  updated for the renamed/folded/added fields and the new shared imports.
- **New feature tests per port** (change 5): a round-trip test that the ported
  feature actually works — e.g. grok/cursor/codex/antigravity encrypt-then-
  decrypt a session blob; `on_resume_refresh` fires on resume; a
  `CALLER_MESSAGE` round-trips.
- A cross-engine **parity test**: assert the common-core field set (names +
  defaults) is identical across all 7 `TaskConfig` classes (a single test that
  introspects the dataclasses), so future drift is caught.
- Shared-type extraction: assert all 7 import `AllowedDir` etc. from
  `optio_agents` (no local redefinition).
- Standard: `.venv` in the worktree; MongoDB via the `mongo_db` fixture; per-
  package `pytest`; defer full verification to a final task (parallel-shaped
  plan).

## Rollout

Parallel-shaped: most changes are per-engine and file-disjoint (each wrapper's
`types.py` + its consuming `session.py`/`host_actions.py`). The shared-type
extraction (change 4) lands first in `optio-agents` (one task), then the 7
engines migrate to import it. The missing-feature ports (change 5) fan out per
engine × feature. Verification deferred to the end.
