# Agents-All Spec A — Config-Harmonization Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Harmonize the 7 wrappers' `TaskConfig` surfaces to an identical common core (shared type aliases, `install_dir` rename, single `model` field, wired missing generic features) so Spec C's discriminated union is clean; leave only justified agent-native deltas.

**Architecture:** A shared `optio_agents/config_types.py` holds the config vocabulary; each wrapper's `types.py` imports (and re-exports) it. Per-engine tasks apply the harmonization checklist to their own package (file-disjoint → parallel). Missing framework features (session-blob encryption, resume-refresh hook, caller-message channel) are ported by copying the proven claudecode/opencode pattern; tool allow/deny is research-gated.

**Tech Stack:** Python 3.11 frozen dataclasses + `typing.Protocol`; pytest + pytest-xdist.

## Global Constraints

- **Parallel-shaped (OVERRIDES per-task RED→GREEN):** every file owned by exactly one task; file-disjoint tasks run concurrently; **ALL verification deferred** to the final task. Engine tasks write code + tests and commit **without** running suites. Tree may be red mid-flight; T11 reconciles.
- **Work in the worktree** `/home/csillag/deai/optio/.worktrees/csillag/agents-all` on branch `csillag/agents-all`. Use a `.venv` **inside the worktree** (never the global Python).
- **Test harness (new):** `make test` runs TS + Python in two phases — parallel xdist `-m "not serial"`, then `-m serial`. New Python tests must be **xdist-safe** (no shared global state / fixed ports / shared cwd); mark `@pytest.mark.serial` only if spawn-heavy or timing-fragile. optio-core always serial. MongoDB via the `mongo_db` fixture (Docker / memory-server), never a local mongod.
- **No `Co-Authored-By`** trailer.
- **Shared `AllowedDir.mode` = `Literal["ro","rw","rox","rwx"]`** (superset). Landlock-only engines treat `rox`≡`ro`, `rwx`≡`rw` (documented).
- **No inert security fields:** a tool allow/deny that does nothing must be **removed**, not shipped (unlike opencode's deliberately-deferred `fs_isolation`).

---

## Execution model

```
Wave 1 (concurrent): T1 config_types foundation  ∥  T2 tool-allow/deny research
Wave 2 (concurrent): T3 kimi ∥ T4 grok ∥ T5 cursor ∥ T6 claudecode ∥ T7 codex ∥ T8 opencode ∥ T9 antigravity
Wave 3 (sequential): T10 cross-engine parity test + grep-clean → T11 full verification
```

Wave 2 depends on T1 (shared aliases must exist) and T2 (the per-engine tool-allow/deny verdict). The 7 Wave-2 tasks are file-disjoint (each owns its `packages/optio-<engine>/` + its `packages/optio-demo/src/optio_demo/tasks/<engine>.py`).

### File-ownership map (no file appears twice)

| Task | Files owned |
|---|---|
| T1 | `optio-agents/src/optio_agents/config_types.py` (new), `.../__init__.py`, `optio-agents/tests/test_config_types.py` (new) |
| T2 | none (research only; records a verdict table in this plan / a note) |
| T3 kimi | `packages/optio-kimicode/**` (`types.py`, `session.py`, `host_actions.py`, tests), `packages/optio-demo/src/optio_demo/tasks/kimicode.py` |
| T4 grok | `packages/optio-grok/**`, `.../tasks/grok.py` |
| T5 cursor | `packages/optio-cursor/**`, `.../tasks/cursor.py` |
| T6 claudecode | `packages/optio-claudecode/**`, `.../tasks/claudecode.py` |
| T7 codex | `packages/optio-codex/**`, `.../tasks/codex.py` |
| T8 opencode | `packages/optio-opencode/**`, `.../tasks/opencode.py` |
| T9 antigravity | `packages/optio-antigravity/**`, `.../tasks/antigravity.py` |
| T10 | `packages/optio-demo/tests/test_config_parity.py` (new) |
| T11 | none (runs suites; fixes fallout wherever it surfaces) |

> T10's parity test lives in **optio-demo** because that package already depends on all 7 wrappers (optio-agents cannot — the wrappers depend on it).

---

## Shared port-pattern reference (Wave-2 tasks consume these)

Three framework features are ported by copying the **claudecode** reference. Exact idioms:

### P1 — Session-blob encryption (add to grok, cursor, codex, antigravity)
**types.py** — add the paired fields + validation (verbatim shape, claudecode `types.py:157-162,272-279`):
```python
session_blob_encrypt: Callable[[bytes], bytes] | None = None
session_blob_decrypt: Callable[[bytes], bytes] | None = None
```
In `__post_init__`:
```python
e = self.session_blob_encrypt is not None
d = self.session_blob_decrypt is not None
if e != d:
    raise ValueError(
        "<Engine>TaskConfig: session_blob_encrypt and session_blob_decrypt "
        "must be set together or both left None; one without the other is a config error.")
```
**session.py** — wrap the session tar at the GridFS write (claudecode `_store_session_blob`, `session.py:1183-1189`):
```python
encrypt = config.session_blob_encrypt or (lambda b: b)
payload = encrypt(session_bytes)
async with ctx.store_blob("session") as swriter:
    await swriter.write(payload); blob_id = swriter.file_id
```
and unwrap on every restore/resume read (claudecode `session.py:251-254`):
```python
decrypt = config.session_blob_decrypt or (lambda b: b)
plain = decrypt(payload)
```
Thread `session_blob_encrypt=config.session_blob_encrypt` through the engine's `_capture_snapshot`/blob-store signature. **optio-agents plumbing: none** (uses generic `ctx.store_blob`).

### P2 — Resume-refresh hook (add to kimi, grok, cursor, codex, antigravity)
**types.py** — module-level identity default + field (claudecode `types.py:79-83,170`):
```python
def _identity_resume_refresh(config: "<Engine>TaskConfig") -> "<Engine>TaskConfig":
    return config
...
on_resume_refresh: "Callable[[<Engine>TaskConfig], <Engine>TaskConfig] | None" = _identity_resume_refresh
```
**session.py** — a `_maybe_refresh_on_resume(host, hook_ctx, config)` helper (claudecode `session.py:1339-1384`): return `[]` if hook is None; else call it in try/except; re-render the engine's instruction file (`AGENTS.md` via that engine's `compose_agents_md`; claude uses `CLAUDE.md`); write back only if changed; return rewritten filenames. Call it on the **resume branch** and feed the result to the resume-log append. **optio-agents plumbing: none.**

### P3 — Caller-message channel (add to kimi, grok, cursor, codex, antigravity)
**types.py** — add fields (claudecode `types.py:145-152`; import `CallerMessageCallback` from `optio_agents`):
```python
use_client_messages: bool = False
on_caller_message: CallerMessageCallback | None = None
```
**session.py** — change the protocol build (claudecode `session.py:145-149`):
```python
protocol = get_protocol(
    browser="redirect",
    client_messages=config.use_client_messages,
    caller_messages=config.on_caller_message is not None,
)
```
and add `on_caller_message=config.on_caller_message` to the `run_log_protocol_session(...)` call (claudecode `session.py:673-683`). **optio-agents plumbing: none** — `get_protocol`/`ProtocolFeatures`/`run_log_protocol_session` already support the flags; the target engines just build a bare `get_protocol(browser="redirect")` today (kimi `session.py:203`, grok `:112`, cursor `:199`, codex `:85`, antigravity `:98`).

---

## WAVE 1

### Task 1: `optio_agents.config_types` — shared config vocabulary

**Files:** Create `packages/optio-agents/src/optio_agents/config_types.py`; modify `.../__init__.py`; create `.../tests/test_config_types.py`.

**Produces:** `ConversationMode`, `ToolVerbosity`, `ThinkingVerbosity`, `SeedProvider`, `SeedUnavailableError`, `AllowedDir` (frozen dataclass; `mode: Literal["ro","rw","rox","rwx"]`, validated in `__post_init__`), importable as `from optio_agents import ...`.

- [ ] **Step 1: Write `config_types.py`**
```python
"""Engine-neutral task-config vocabulary shared by every wrapper's TaskConfig.

Lifted from the (previously duplicated) per-wrapper types.py. AllowedDir uses
the 4-value superset mode; Landlock-only sandboxes treat rox==ro, rwx==rw."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Awaitable, Callable, Literal

ConversationMode = Literal["iframe", "conversation"]
ToolVerbosity = Literal["silent", "description-only", "verbose"]
ThinkingVerbosity = Literal["hidden", "visible"]
SeedProvider = Callable[[str], Awaitable[str]]


class SeedUnavailableError(Exception):
    """Raised by a SeedProvider when no seed is available for the task."""


@dataclass
class AllowedDir:
    """A filesystem grant beyond the workdir. ``rox``/``rwx`` add an execute
    bit; Landlock-only engines treat them as ``ro``/``rw`` (exec implied)."""
    path: str
    mode: Literal["ro", "rw", "rox", "rwx"]

    def __post_init__(self) -> None:
        if self.mode not in ("ro", "rw", "rox", "rwx"):
            raise ValueError(f"AllowedDir.mode={self.mode!r} must be ro/rw/rox/rwx")
```

- [ ] **Step 2: Export from `__init__.py`** (Pattern A — re-export + `__all__`)
Add `from optio_agents.config_types import (AllowedDir, ConversationMode, SeedProvider, SeedUnavailableError, ThinkingVerbosity, ToolVerbosity)` and add those 6 names to `__all__`.

- [ ] **Step 3: Write `tests/test_config_types.py`** (xdist-safe, pure)
```python
import pytest
from optio_agents import (AllowedDir, ConversationMode, SeedProvider,
                          SeedUnavailableError, ThinkingVerbosity, ToolVerbosity)

def test_alloweddir_accepts_superset_and_rejects_junk():
    for m in ("ro", "rw", "rox", "rwx"):
        assert AllowedDir("/w", m).mode == m
    with pytest.raises(ValueError):
        AllowedDir("/w", "wx")

def test_aliases_importable_from_top_level():
    # smoke: the Literals/aliases are exported for wrappers to import
    assert SeedProvider is not None and issubclass(SeedUnavailableError, Exception)
```

- [ ] **Step 4: Commit** `feat(optio-agents): shared config_types (ConversationMode/ToolVerbosity/ThinkingVerbosity/SeedProvider/AllowedDir)`

### Task 2: Tool-allow/deny reachability research (kimi, antigravity, codex, opencode)

**Files:** none (research). **Produces:** a per-engine verdict consumed by T3/T7/T8/T9.

- [ ] **Step 1: For each of kimi, antigravity, codex, opencode**, determine whether the agent exposes a native **per-tool allow/deny** mechanism the wrapper can drive:
  - **kimi**: inspect the fork `~/deai/kimi-code` config schema + `kimi acp`/`kimi server run` flags — is there a per-tool permission grammar beyond `default_permission_mode`? (config.toml `[permissions]`? an ACP verb?)
  - **antigravity**: `agy --help` + `~/.gemini` settings — any tool allow/deny beyond `--dangerously-skip-permissions`?
  - **codex**: codex app-server config / `~/deai/codex` schema — a `tools`/allow list?
  - **opencode**: `opencode.json` `permission`/tools schema (the wrapper already writes `opencode_config` verbatim).
- [ ] **Step 2: Record the verdict** per engine: **REACHABLE** (name the mechanism: flag / config-key / RPC) or **NOT REACHABLE**.
- [ ] **Step 3: Decision rule for Wave 2:**
  - REACHABLE → the engine task wires `allowed_tools`/`disallowed_tools` to it + adds a round-trip test.
  - NOT REACHABLE → kimi/antigravity **remove** the dead field; codex/opencode **do not add** it. Record as a native gap in the engine's `types.py` docstring.
- [ ] **Step 4:** Write the verdict table into this plan (edit the "T2 verdict" block below) so the Wave-2 tasks are unambiguous. No commit (no code).

**T2 verdict (resolved):**
- **kimi = REACHABLE** — config.toml `[[permission.rules]]` (`{decision, pattern}`; bare tool name matches). Extend `write_kimi_config` (`host_actions.py:193`) to emit one deny rule per `disallowed_tools`, one allow rule per `allowed_tools`. → **WIRE** (do not remove).
- **opencode = REACHABLE** — `opencode.json` `permission` map (tool→`allow`/`deny`), written via the existing `opencode_config` passthrough (`session.py:248`). → **ADD + WIRE** convenience fields that fold into `permission`.
- **antigravity = NOT REACHABLE** — only `--dangerously-skip-permissions`; no per-tool grammar. → **REMOVE** the dead fields.
- **codex = NOT REACHABLE** — permission profiles cover fs/network only; `[tools]` are boolean capability toggles, not an allow/deny list. → **DO NOT ADD**; record the native gap in `types.py` docstring.

`allowed_tools`/`disallowed_tools` therefore stays OUT of `CORE` (reachable on 5/7: kimi/grok/cursor/claude/opencode).

---

## WAVE 2 — per-engine harmonization (T3–T9, concurrent)

Every Wave-2 task shares this **common checklist** (C1–C3), then applies its **engine-specific** items. Each task ends with a commit; **no suite run** (deferred to T11). Each task also writes/updates tests (xdist-safe unless spawn-heavy) and updates its demo file if any owned field changed (per the demo audit, none of the 7 demos currently pass a renamed/removed field, so demo edits are usually just a re-verify; add a feature showcase only where noted).

**Common checklist (all 7):**
- **C1 — Shared aliases.** In `types.py`, delete the local `ConversationMode`/`ToolVerbosity`/`ThinkingVerbosity`/`SeedProvider`/`SeedUnavailableError`/`AllowedDir` definitions and `from optio_agents import (AllowedDir, ConversationMode, SeedProvider, SeedUnavailableError, ThinkingVerbosity, ToolVerbosity)`. **Re-export them** (keep in this module's `__all__`) so existing `from optio_<engine>.types import ConversationMode, AllowedDir, …` sites in `fs_allowlist.py`/`session.py`/`__init__.py` keep working unchanged.
- **C2 — `install_dir` rename.** Rename the `<engine>_install_dir` field → **`install_dir`** (keep the absolute-path validation) and update every read site in `host_actions.py`/`session.py`.
- **C3 — `model` single field.** Where the engine has both `model` and `default_model`: **drop `default_model`**, keep `model`, remove the `default_model`-requires-conversation_ui gate, and source the conversation picker's initial value from `model`. (Engine-specific notes below.)

### Task 3: kimi
- [ ] C1, C2 (`kimi_install_dir`→`install_dir`), C3 (drop `default_model`, keep `model`).
- [ ] **P2** on_resume_refresh — add + wire (kimi lacks it). Instruction file = `AGENTS.md`.
- [ ] **P3** caller-message — add `use_client_messages`/`on_caller_message` + wire (`session.py:203` get_protocol).
- [ ] session-blob encryption: **already present** — no change.
- [ ] `allowed_tools`/`disallowed_tools`: **REACHABLE (T2) → WIRE.** Extend `write_kimi_config` (`host_actions.py:193`) to emit `[[permission.rules]]` tables — `{decision="deny", pattern="<tool>"}` per `disallowed_tools`, `{decision="allow", pattern="<tool>"}` per `allowed_tools` — alongside the existing `default_permission_mode`. Add a round-trip test asserting the rules land in config.toml. Do NOT remove the fields.
- [ ] `effort`: **leave in place** (Spec B repurposes it). Do not remove.
- [ ] Tests: encrypt already tested; add a resume-refresh unit test + a caller-message pairing test (xdist-safe with fakes; mirror claudecode's tests). Update `test_types.py` for the renamed/dropped fields.
- [ ] Demo `tasks/kimicode.py`: re-verify (no renamed field used).
- [ ] Commit `refactor(optio-kimicode): harmonize config surface (shared aliases, install_dir, model, +resume-refresh/caller-message)`.

### Task 4: grok
- [ ] C1, C2 (`grok_install_dir`→`install_dir`), C3 (drop `default_model`).
- [ ] **P1** session-blob encryption — add + wire.
- [ ] **P2** on_resume_refresh — add + wire (`AGENTS.md`).
- [ ] **P3** caller-message — add + wire (`session.py:112`).
- [ ] `allowed_tools`: already wired (`--allow`×N + `--disallowed-tools`) — keep.
- [ ] Native kept: `effort`/`reasoning_effort`/`no_leader`/permission_mode/ttyd.
- [ ] Tests: encrypt/decrypt round-trip, resume-refresh, caller-message. Update `test_types.py`/`test_models.py`.
- [ ] Commit `refactor(optio-grok): harmonize config surface (+session-encrypt/resume-refresh/caller-message)`.

### Task 5: cursor
- [ ] C1, C2 (`cursor_install_dir`→`install_dir`), C3 (drop `default_model`).
- [ ] **P1**, **P2** (`AGENTS.md`), **P3** (`session.py:199`) — add + wire all three.
- [ ] `allowed_tools`: already wired (cli-config.json plant) — keep.
- [ ] Native kept: `sandbox`/`force`/`auto_review`/`api_key`/ttyd.
- [ ] Tests: three round-trips + `test_types.py` update.
- [ ] Commit `refactor(optio-cursor): harmonize config surface (+session-encrypt/resume-refresh/caller-message)`.

### Task 6: claudecode
- [ ] C1 — shared aliases; **also add `ToolVerbosity`/`ThinkingVerbosity` to claudecode's `__all__`** (currently omitted — widen for parity). AllowedDir: switch to shared; note claudecode's local `AllowedDir` had no `__post_init__` — the shared one validates at construction (stricter; keep the existing `TaskConfig.__post_init__` loop too, harmless).
- [ ] C2 (`claude_install_dir`→`install_dir`).
- [ ] C3 — **N/A** (claudecode already has a single `model`, no `default_model`).
- [ ] session-blob / on_resume_refresh / caller-message: **all already present** — no change.
- [ ] `allowed_tools`: wired — keep.
- [ ] Native kept: `claude_config`/`focus_mode`/`include_partial_messages`/`credentials_json`/`session_restore_*`/`delivery_type`.
- [ ] Tests: `test_types.py` update for `install_dir` + `__all__`.
- [ ] Commit `refactor(optio-claudecode): harmonize config surface (shared aliases, install_dir)`.

### Task 7: codex
- [ ] C1, C2 (`codex_install_dir`→`install_dir`), C3 (drop `default_model`).
- [ ] **P1**, **P2** (`AGENTS.md`), **P3** (`session.py:85`) — add + wire all three.
- [ ] `allowed_tools`/`disallowed_tools`: **NOT REACHABLE (T2) → DO NOT ADD.** Record the native gap in a `types.py` docstring note (codex permission profiles cover fs/network only; `[tools]` are boolean capability toggles, not an allow/deny list).
- [ ] Native kept: `ask_for_approval`/`sandbox`/`network_access`/ttyd.
- [ ] Tests: three round-trips + `test_config.py` update.
- [ ] Commit `refactor(optio-codex): harmonize config surface (+session-encrypt/resume-refresh/caller-message)`.

### Task 8: opencode
- [ ] C1, C2 (`opencode_install_dir`→`install_dir`).
- [ ] C3 — `default_model`→`model` (opencode has only `default_model`; rename it to `model`; `model` maps to opencode's server `defaultModel` config + seeded `opencode.json` path; drop the conversation_ui gate so `model` is valid in all modes). Update `session.py:92` (`"defaultModel": config.default_model` → `config.model`) and the seeded-config path.
- [ ] session-blob / on_resume_refresh / caller-message: **all already present** — no change.
- [ ] **fs_isolation + extra_allowed_dirs (inert, change 6):** add `fs_isolation: bool = True` and `extra_allowed_dirs: list[AllowedDir] | None = None` (shared `AllowedDir`). Since opencode has no claustrum yet, this is a **known no-op**: (a) a field docstring stating "NOT YET ENFORCED — claustrum port pending"; (b) a **runtime warning on the server console** at launch when `fs_isolation` is True (e.g. `_LOG.warning("opencode fs_isolation requested but not yet enforced (claustrum pending)")` in the launch path).
- [ ] `allowed_tools`/`disallowed_tools`: **REACHABLE (T2) → ADD + WIRE.** Add the two `list[str] | None = None` fields; fold into the seeded `opencode.json` `permission` map (`session.py:248` `opencode_config` path) — `permission[<tool>]="deny"` per `disallowed_tools`, `"allow"` per `allowed_tools`; **merge**, don't clobber operator-supplied `opencode_config["permission"]`. Round-trip test.
- [ ] Native kept: `opencode_config`.
- [ ] Tests: `test_types.py` update (rename, new fs fields); a test asserting the not-enforced warning fires when `fs_isolation=True`.
- [ ] Commit `refactor(optio-opencode): harmonize config surface (model rename, inert fs_isolation)`.

### Task 9: antigravity
- [ ] C1, C2 (`agy_install_dir`→`install_dir`), C3 (drop `default_model`).
- [ ] **P1**, **P2** (`AGENTS.md`), **P3** (`session.py:98`) — add + wire all three.
- [ ] `allowed_tools`/`disallowed_tools`: **NOT REACHABLE (T2) → REMOVE the dead fields** (agy has only `--dangerously-skip-permissions`; no per-tool grammar).
- [ ] **Remove dead `effort` + `reasoning_effort`** (unreachable — agy bakes thinking into the model id). Update `build_agy_flags` call sites (they don't read effort anyway) + `test_types.py`.
- [ ] Native kept: permission_mode/ttyd.
- [ ] Tests: three round-trips + `test_types.py` update (removed effort, renamed install_dir).
- [ ] Commit `refactor(optio-antigravity): harmonize config surface; remove dead effort`.

---

## WAVE 3 — cross-cutting + verification (sequential)

### Task 10: cross-engine parity test + grep-clean

**Files:** Create `packages/optio-demo/tests/test_config_parity.py`.

- [ ] **Step 1: Write the parity test** (introspect all 7 dataclasses; xdist-safe, pure):
```python
import dataclasses
from optio_kimicode.types import KimiCodeTaskConfig
from optio_grok.types import GrokTaskConfig
from optio_cursor.types import CursorTaskConfig
from optio_claudecode.types import ClaudeCodeTaskConfig
from optio_codex.types import CodexTaskConfig
from optio_opencode.types import OpencodeTaskConfig
from optio_antigravity.types import AntigravityTaskConfig

CONFIGS = [KimiCodeTaskConfig, GrokTaskConfig, CursorTaskConfig, ClaudeCodeTaskConfig,
           CodexTaskConfig, OpencodeTaskConfig, AntigravityTaskConfig]

# The harmonized common core: every engine MUST expose these with matching defaults.
CORE = {
    "consumer_instructions", "env", "scrub_env", "ssh", "install_if_missing",
    "before_execute", "after_execute", "on_deliverable", "seed_id", "on_seed_saved",
    "supports_resume", "workdir_exclude", "mode", "host_protocol", "conversation_ui",
    "tool_verbosity", "thinking_verbosity", "model", "show_session_controls",
    "show_file_upload", "on_upload", "max_upload_bytes", "file_download",
    "max_download_bytes", "auto_start", "native_spinner", "install_dir",
    "session_blob_encrypt", "session_blob_decrypt", "on_resume_refresh",
    "use_client_messages", "on_caller_message",
}

def _fields(cls):
    return {f.name: f for f in dataclasses.fields(cls)}

def test_every_engine_has_the_core():
    for cls in CONFIGS:
        missing = CORE - set(_fields(cls))
        assert not missing, f"{cls.__name__} missing core fields: {sorted(missing)}"

def test_no_default_model_field_remains():
    for cls in CONFIGS:
        assert "default_model" not in _fields(cls), f"{cls.__name__} still has default_model"

def test_no_per_engine_install_dir_name():
    for cls in CONFIGS:
        names = set(_fields(cls))
        assert not any(n.endswith("_install_dir") and n != "install_dir" for n in names)
```
> Note: `allowed_tools`/`disallowed_tools` is **not** in `CORE` (research-gated — coverage may legitimately vary). If T2 found all four reachable, add them to `CORE`.

- [ ] **Step 2: Grep-clean** (must be empty):
```bash
grep -rn "default_model\|kimi_install_dir\|grok_install_dir\|cursor_install_dir\|claude_install_dir\|codex_install_dir\|opencode_install_dir\|agy_install_dir" packages/optio-*/src || echo CLEAN
grep -rn "reasoning_effort\|effort" packages/optio-antigravity/src || echo CLEAN
```
Fix any stragglers in the owning engine's files.
- [ ] **Step 3: Commit** `test(agents-all): cross-engine config-parity guard`.

### Task 11: full verification

**Files:** none up front.

- [ ] **Step 1: Python suites via the new harness.** From the worktree root, run `make test` (or per-package: optio-core serial, then `cd packages/<pkg> && .venv/bin/pytest -m "not serial"` xdist + `-m serial`). Cover: optio-agents + all 7 engines + optio-demo. Fix every failure (usual causes: a missed `install_dir` read site, a `default_model` reference, a ported-feature signature mismatch, a stale `test_types.py` assertion).
- [ ] **Step 2: TS.** `cd packages/optio-conversation-ui && ./node_modules/.bin/tsc --noEmit && pnpm test` (untouched here, but confirm no incidental break).
- [ ] **Step 3: Final grep** (empty): `grep -rn "ConversationMode = Literal\|ToolVerbosity = Literal" packages/optio-*/src/optio_*/types.py` should show **re-exports only** (imported from optio_agents), not fresh local `Literal` definitions. Confirm each engine imports from `optio_agents`.
- [ ] **Step 4: Commit** any fixes `fix(agents-all): resolve harmonization verification fallout`.

---

## Self-review notes

- **Spec coverage:** change 1 (C2, all engines) · change 2 (C3 + opencode/T8) · change 3 (shared AllowedDir superset, T1) · change 4 (T1 + C1) · change 5 features (P1/P2/P3 per engine + T2 research for tool-allow/deny) · change 6 (T8) · change 7 (T9) · testing (per-task round-trips + T10 parity + T11 harness). All mapped.
- **Parallel shape:** every file owned once (map); Wave-2 file-disjoint by engine; verification in T11.
- **Known-hard / research:** T2 tool-allow/deny reachability (4 engines) gates T3/T7/T8/T9's allow-deny step; claudecode `AllowedDir` gains construction validation; opencode `fs_isolation` ships inert with a runtime warning (change 6).
- **Demos:** no demo passes a renamed/removed field (audited), so demo edits are re-verify only unless a task adds a feature showcase.
