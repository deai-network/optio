# Agents-All — Spec C: `optio-agents-all` Meta-Factory

**Date:** 2026-07-07
**Branch:** `csillag/agents-all` (worktree)
**Status:** Approved design (brainstorming output); implementation plan to follow.
**Program:** Spec **C of three** (final). A = config-surface harmonization (done),
B = reasoning-effort live control (done). C = this doc.

## Goal

One entry point that creates any of the 7 agent tasks from a single typed
config, dispatched by an `agent_type` discriminator — a discriminated union of
the 7 `TaskConfig` dataclasses. A new `optio-agents-all` package provides
`create_task(...)` + the union type + a one-stop import surface; `optio-demo` is
migrated to depend only on it and build all 7 demos through it.

## Why a dataclass union (not a dict)

Input is a **typed config dataclass**, not a raw dict. A dataclass validates at
construction (`__post_init__` — bad enums, cross-field constraints, misspelled
kwargs) and is statically typed; a dict defers every such error to runtime. The
tagged-dataclass union is the idiomatic Python discriminated union (the TS
equivalent of `type T = A | B` narrowed on a literal field) and reuses the
surfaces Spec A/B just harmonized instead of mirroring ~35 fields × 7 into
TypedDicts. No dict bridge / `config_from_dict` — callers construct the
dataclass.

## Section 1 — Discriminator + union

- Add **`agent_type: Literal["<slug>"] = "<slug>"`** to each of the 7 `TaskConfig`
  dataclasses. Slugs: `kimicode`, `grok`, `cursor`, `claudecode`, `codex`,
  `opencode`, `antigravity`. A fixed default — callers never set it; it is
  intrinsic to the type and serves as the discriminator + a stable string id
  (logging / serialization / UI).
  - Dataclass ordering: `agent_type` has a default, so it goes among the
    defaulted fields (after the required `consumer_instructions`). Freeze/immutability
    unchanged.
- In `optio-agents-all`:
  - `AgentType = Literal["kimicode","grok","cursor","claudecode","codex","opencode","antigravity"]`.
  - `AgentTaskConfig = KimiCodeTaskConfig | GrokTaskConfig | CursorTaskConfig | ClaudeCodeTaskConfig | CodexTaskConfig | OpencodeTaskConfig | AntigravityTaskConfig`
    — a discriminated union that narrows on `config.agent_type`.
- `agent_type` becomes a new member of the harmonized common core (all 7 have
  it) → add it to the Spec-A parity guard's `CORE` set.

## Section 2 — Dispatcher + registry + import surface

- **`create_task(process_id: str, name: str, config: AgentTaskConfig,
  description: str | None = None, metadata: dict | None = None) -> TaskInstance`**
  — mirrors the 7 per-engine factory signatures (already identical). Body:
  ```python
  return _REGISTRY[config.agent_type](
      process_id, name, config, description=description, metadata=metadata,
  )
  ```
- **Registry** `_REGISTRY: dict[AgentType, Callable[..., TaskInstance]]` mapping
  each slug to its per-engine factory (`create_kimicode_task`, `create_grok_task`,
  …). An unknown slug raises `ValueError` (defensive; the union should preclude it).
- **Typing:** the registry is heterogeneous (each factory wants its own config
  type), so the dispatch line needs one internal `cast`/`type: ignore` — the
  `agent_type ↔ config-class` invariant makes it runtime-safe. No per-engine
  `@overload`s (the return is uniformly `TaskInstance`, so they add nothing).
- **One import surface:** `optio_agents_all/__init__.py` re-exports `create_task`,
  `AgentTaskConfig`, `AgentType`, **plus all 7 `<Engine>TaskConfig` classes and
  all 7 `create_<engine>_task` factories**, so a consumer imports one package
  instead of seven.

## Section 3 — Package + wiring

- New `packages/optio-agents-all/`:
  - `pyproject.toml` (setuptools, mirroring a reference wrapper's), declaring
    dependencies on all 7 engine packages + `optio-agents`.
  - `src/optio_agents_all/factory.py` — `create_task` + `_REGISTRY`.
  - `src/optio_agents_all/types.py` — `AgentType`, `AgentTaskConfig`.
  - `src/optio_agents_all/__init__.py` — the re-export surface (Section 2).
  - `tests/` — Section 4.
- The `agent_type` field is added to each engine's `types.py` (7 engine-owned
  edits); the union + factory live in `optio-agents-all`.
- **Register in monorepo tooling** like any package: add to `PY_PACKAGES` (and
  therefore `XDIST_PACKAGES`) in the `Makefile`; add to the release tooling
  (`scripts/release/run.py` publishable list + `RELEASABLE_PY` / the root
  `Makefile`); add to the demo install list. (See the release-cookbook +
  [[reference_release_clean_tree]] conventions.)

## Section 4 — Testing

- **Dispatch:** `create_task` routes each `agent_type` to the correct per-engine
  factory — mock the 7 factories, assert the right one is called with
  `(process_id, name, config, description=…, metadata=…)`; an unknown slug →
  `ValueError`.
- **Discriminator:** each `<Engine>TaskConfig().agent_type` defaults to its slug;
  a constructed config is an instance of the expected type and the union narrows
  (isinstance / runtime check).
- **Completeness guard** (mirrors Spec A's parity test): assert every `AgentType`
  slug is a key in `_REGISTRY` **and** every engine's `TaskConfig` appears in the
  `AgentTaskConfig` union — so a future engine cannot be silently omitted.
- **Import surface:** assert the 7 config classes + 7 factories + `create_task` +
  the union/`AgentType` are all importable from `optio_agents_all`.
- **Spec-A parity guard:** add `agent_type` to its `CORE` field set.
- Standard: `.venv` in the worktree; pytest-xdist harness (new tests xdist-safe).

## Section 5 — Demo migration (`optio-demo`)

- **Dependency:** drop `optio-demo`'s 7 individual engine deps
  (`optio-kimicode`/`optio-grok`/…); add a single **source/workspace dependency
  on `optio-agents-all`** (editable, like the existing in-repo deps — not
  published). The 7 engines still install transitively (via `optio-agents-all`);
  the demo just no longer names them.
- **Imports:** each `tasks/<engine>.py` imports its `<Engine>TaskConfig` from
  `optio_agents_all` (re-exported) instead of `optio_<engine>.types`.
- **Factory calls:** replace every `create_<engine>_task(process_id, name,
  config, …)` with the unified `create_task(process_id, name, config, …)` — the
  `config` is the same `<Engine>TaskConfig` (now carrying its `agent_type`
  default), so no field changes, only the import + call swap.
- **Net:** `optio-demo` depends on exactly one optio agent package
  (`optio-agents-all`) and builds all 7 demos through the single meta-factory — a
  working end-to-end acceptance test that the unified surface reaches every
  engine. ([[project_optio_demo_consumes_opencode]] — features verified in-repo
  via the demo.)

## Dependencies & rollout

Depends on **Spec A** (harmonized configs, incl. the `TaskConfig` surfaces the
union references) and **Spec B** (the `reasoning_effort` field on 5 of them) —
both done. Parallel-shaped rollout: add the `agent_type` field per engine
(file-disjoint), scaffold `optio-agents-all` (union + factory + tests), migrate
`optio-demo`, register in tooling; verification deferred to the end.
