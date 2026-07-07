# Agents-All Spec C — Meta-Factory: Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development or superpowers:executing-plans. Steps use checkbox (`- [ ]`) syntax.

**Goal:** A new `optio-agents-all` package exposing `create_task(process_id, name, config, …)` over a tagged discriminated union of the 7 `TaskConfig`s; `optio-demo` migrated to depend only on it and build all 7 demos through it.

**Architecture:** Each engine `TaskConfig` gains an `agent_type: Literal["<slug>"] = "<slug>"` discriminator; `optio-agents-all` unions them (`AgentTaskConfig`), dispatches `create_task` to the per-engine factory via a registry keyed on `agent_type`, and re-exports the whole surface. Typed configs in, no dict bridge.

**Tech Stack:** Python frozen dataclasses + `typing.Literal`; setuptools; pytest-xdist.

## Global Constraints

- **Parallel-shaped:** file-disjoint tasks concurrent; **verification deferred** to the last task. This feature is mostly LINEAR (T1→T2→T3), so waves are small.
- Worktree `/home/csillag/deai/optio/.worktrees/csillag/agents-all`, branch `csillag/agents-all`, `.venv` inside it. pytest-xdist harness (new tests xdist-safe).
- Slugs (fixed): `kimicode`, `grok`, `cursor`, `claudecode`, `codex`, `opencode`, `antigravity`.
- No `Co-Authored-By`.

---

## Execution model

```
Wave 1: T1 agent_type discriminator on 7 TaskConfigs (+ parity CORE)
Wave 2: T2 optio-agents-all package (union + create_task + registry + tests) + tooling registration
Wave 3: T3 optio-demo migration (dep swap + create_task)
Wave 4: T4 full verification
```

Each wave depends on the prior. File-ownership: T1 owns the 7 `types.py` + the parity test; T2 owns the new package + root tooling files; T3 owns `optio-demo`; T4 runs suites.

---

## WAVE 1

### Task 1: `agent_type` discriminator on the 7 TaskConfigs

**Files:** `packages/optio-<engine>/src/optio_<engine>/types.py` ×7; `packages/optio-demo/tests/test_config_parity.py`.

- [ ] **Step 1:** In each engine's `types.py`, add the discriminator field to its `TaskConfig` dataclass, among the defaulted fields (after the required `consumer_instructions`), with the engine's slug:
```python
# Discriminates the engine in the optio-agents-all meta-factory union.
agent_type: Literal["kimicode"] = "kimicode"   # grok/cursor/claudecode/codex/opencode/antigravity per engine
```
(`Literal` is already imported in each `types.py`. Slug per engine: kimicode→`"kimicode"`, grok→`"grok"`, cursor→`"cursor"`, claudecode→`"claudecode"`, codex→`"codex"`, opencode→`"opencode"`, antigravity→`"antigravity"`.)

- [ ] **Step 2:** In `packages/optio-demo/tests/test_config_parity.py`, add `"agent_type"` to the `CORE` set (it's now on all 7).

- [ ] **Step 3:** Sanity — each `<Engine>TaskConfig()` still constructs and `.agent_type == "<slug>"` (defer running to T4). Commit `feat(agents-all): agent_type discriminator on all 7 TaskConfigs`.

---

## WAVE 2

### Task 2: `optio-agents-all` package

**Files (create):** `packages/optio-agents-all/pyproject.toml`; `.../src/optio_agents_all/{__init__.py,types.py,factory.py}`; `.../tests/test_meta_factory.py`. **Files (modify):** root `Makefile` (`PY_PACKAGES`), `scripts/release/run.py` (publishable list) + `RELEASABLE_PY` (root Makefile).

**Interfaces produced:** `create_task(process_id, name, config, description=None, metadata=None) -> TaskInstance`; `AgentType` Literal; `AgentTaskConfig` union; re-exports of the 7 `<Engine>TaskConfig` + 7 `create_<engine>_task`.

- [ ] **Step 1 — pyproject.** Mirror a reference wrapper's `packages/optio-kimicode/pyproject.toml` (setuptools, `src/` layout, `[dev]` extra with pytest+xdist). Name `optio-agents-all`. `dependencies` = the 7 engine packages (`optio-kimicode`, `optio-grok`, `optio-cursor`, `optio-claudecode`, `optio-codex`, `optio-opencode`, `optio-antigravity`) + `optio-agents`. Match the workspace/editable dependency style the other packages use for in-repo deps.

- [ ] **Step 2 — `types.py`.**
```python
from __future__ import annotations
from typing import Literal

from optio_antigravity.types import AntigravityTaskConfig
from optio_claudecode.types import ClaudeCodeTaskConfig
from optio_codex.types import CodexTaskConfig
from optio_cursor.types import CursorTaskConfig
from optio_grok.types import GrokTaskConfig
from optio_kimicode.types import KimiCodeTaskConfig
from optio_opencode.types import OpencodeTaskConfig

AgentType = Literal[
    "kimicode", "grok", "cursor", "claudecode", "codex", "opencode", "antigravity",
]

AgentTaskConfig = (
    KimiCodeTaskConfig | GrokTaskConfig | CursorTaskConfig | ClaudeCodeTaskConfig
    | CodexTaskConfig | OpencodeTaskConfig | AntigravityTaskConfig
)
```

- [ ] **Step 3 — `factory.py`.**
```python
from __future__ import annotations
from typing import Callable

from optio_antigravity import create_antigravity_task
from optio_claudecode import create_claudecode_task
from optio_codex import create_codex_task
from optio_cursor import create_cursor_task
from optio_grok import create_grok_task
from optio_kimicode import create_kimicode_task
from optio_opencode import create_opencode_task

from optio_agents_all.types import AgentTaskConfig, AgentType

_REGISTRY: dict[AgentType, Callable] = {
    "kimicode": create_kimicode_task,
    "grok": create_grok_task,
    "cursor": create_cursor_task,
    "claudecode": create_claudecode_task,
    "codex": create_codex_task,
    "opencode": create_opencode_task,
    "antigravity": create_antigravity_task,
}


def create_task(process_id, name, config: AgentTaskConfig,
                description=None, metadata=None):
    """Create a task for any wrapped agent, dispatched by config.agent_type."""
    factory = _REGISTRY.get(config.agent_type)
    if factory is None:
        raise ValueError(f"unknown agent_type: {config.agent_type!r}")
    return factory(process_id, name, config, description=description, metadata=metadata)  # type: ignore[arg-type]
```
(Confirm the 7 `create_<engine>_task` names are exported from each `optio_<engine>/__init__.py`; import from `.types` if a factory isn't top-level exported.)

- [ ] **Step 4 — `__init__.py`.** Re-export `create_task`, `AgentType`, `AgentTaskConfig`, the 7 `<Engine>TaskConfig`, and the 7 `create_<engine>_task`; list all in `__all__`.

- [ ] **Step 5 — Tests `test_meta_factory.py`** (xdist-safe):
```python
from unittest.mock import MagicMock
import pytest
import optio_agents_all as aa
from optio_agents_all.factory import _REGISTRY
from optio_agents_all.types import AgentType

def test_every_slug_registered_and_in_union():
    from typing import get_args
    slugs = set(get_args(AgentType))
    assert set(_REGISTRY) == slugs
    # union covers exactly the 7 configs
    from typing import get_args as ga
    union_types = {t.__name__ for t in ga(aa.AgentTaskConfig)}
    assert len(union_types) == 7

def test_create_task_dispatches_by_agent_type(monkeypatch):
    called = {}
    for slug in _REGISTRY:
        monkeypatch.setitem(_REGISTRY, slug,
            lambda p, n, c, description=None, metadata=None, _s=slug: called.setdefault("hit", _s))
    cfg = aa.KimiCodeTaskConfig(consumer_instructions="x")
    aa.create_task("pid", "nm", cfg)
    assert called["hit"] == "kimicode"

def test_unknown_agent_type_raises():
    cfg = aa.GrokTaskConfig(consumer_instructions="x")
    object.__setattr__(cfg, "agent_type", "bogus")
    with pytest.raises(ValueError):
        aa.create_task("pid", "nm", cfg)

def test_import_surface():
    for name in ("create_task", "AgentTaskConfig", "AgentType",
                 "KimiCodeTaskConfig", "GrokTaskConfig", "CursorTaskConfig",
                 "ClaudeCodeTaskConfig", "CodexTaskConfig", "OpencodeTaskConfig",
                 "AntigravityTaskConfig", "create_kimicode_task", "create_grok_task",
                 "create_cursor_task", "create_claudecode_task", "create_codex_task",
                 "create_opencode_task", "create_antigravity_task"):
        assert hasattr(aa, name), name

def test_agent_type_defaults_per_engine():
    assert aa.KimiCodeTaskConfig(consumer_instructions="x").agent_type == "kimicode"
    assert aa.GrokTaskConfig(consumer_instructions="x").agent_type == "grok"
    assert aa.AntigravityTaskConfig(consumer_instructions="x").agent_type == "antigravity"
```

- [ ] **Step 6 — Tooling registration.** Add `optio-agents-all` to `PY_PACKAGES` in the root `Makefile` (it flows into `XDIST_PACKAGES`), to the publishable list in `scripts/release/run.py` + `RELEASABLE_PY`, in dependency order (after the 7 engines + optio-agents). Editable-install it into the worktree `.venv` (`cd packages/optio-agents-all && ../../.venv/bin/pip install -e .`).

- [ ] **Step 7 — Commit** `feat(optio-agents-all): meta-factory create_task over the tagged TaskConfig union`.

---

## WAVE 3

### Task 3: `optio-demo` migration

**Files:** `packages/optio-demo/pyproject.toml`; `packages/optio-demo/src/optio_demo/tasks/<engine>.py` ×7.

- [ ] **Step 1 — pyproject.** In `packages/optio-demo/pyproject.toml`, remove the 7 individual engine deps (`optio-kimicode`, `optio-grok`, `optio-cursor`, `optio-claudecode`, `optio-codex`, `optio-opencode`, `optio-antigravity`) and add a single `optio-agents-all` dependency, using the same in-repo source/editable style the demo already uses for its deps. (optio-agents-all pulls the 7 transitively.)

- [ ] **Step 2 — imports + factory calls.** In each `tasks/<engine>.py`: change the config import from `optio_<engine>.types import <Engine>TaskConfig` → `optio_agents_all import <Engine>TaskConfig`; change `from optio_<engine> import create_<engine>_task` → `from optio_agents_all import create_task`; and replace every `create_<engine>_task(process_id, name, config, …)` call with `create_task(process_id, name, config, …)`. No config-field changes (the `agent_type` default is intrinsic). Update any other `optio_<engine>` import used by the demo to route through `optio_agents_all` where re-exported (e.g. `SSHConfig`/`HookContext` come from `optio_host`/`optio_agents` — leave those as-is; only the engine config + factory move).

- [ ] **Step 3 — Commit** `refactor(optio-demo): build all 7 demos through optio-agents-all.create_task`.

---

## WAVE 4

### Task 4: full verification

- [ ] **Step 1 — reinstall** the worktree `.venv` for the new package + demo dep change: `cd packages/optio-agents-all && ../../.venv/bin/pip install -e .` and `cd packages/optio-demo && ../../.venv/bin/pip install -e .` (picks up the dep swap).
- [ ] **Step 2 — Python suites** (xdist): optio-agents-all + optio-demo + the 7 engines (the `agent_type` field addition — `-m "not serial"` and `-m serial`). Fix fallout (usual: a demo import missed, an engine `test_types.py` needing the new field, a parity-CORE mismatch).
- [ ] **Step 3 — Grep:** `grep -rln "create_kimicode_task\|create_grok_task\|create_cursor_task\|create_claudecode_task\|create_codex_task\|create_opencode_task\|create_antigravity_task" packages/optio-demo/src` → should be **empty** (all demos now use `create_task`).
- [ ] **Step 4 — Import surface smoke:** `.venv/bin/python -c "import optio_agents_all as aa; from optio_agents_all import create_task, AgentTaskConfig; print(aa.KimiCodeTaskConfig(consumer_instructions='x').agent_type)"` → `kimicode`.
- [ ] **Step 5 — Commit** any fixes.

## Self-review notes
- **Spec coverage:** discriminator+union (T1/T2) · dispatcher+registry+import surface (T2) · package+tooling (T2) · testing (T2 + T4) · demo migration (T3) · parity-CORE `agent_type` (T1). Mapped.
- **Linear deps:** T1→T2→T3→T4 (each imports/depends on the prior); no cross-file conflicts.
