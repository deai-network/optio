# optio-agents-all — Agent Cheatsheet

Meta-factory over every wrapped agent engine: a single entry point dispatching
on `config.agent_type` over the tagged union of the seven engine `TaskConfig`
dataclasses.

## Public surface (`optio_agents_all`)

* `create_task(process_id, name, config, description=None, metadata=None)` —
  dispatches to the matching per-engine factory by `config.agent_type`
  (`ValueError` on an unknown type). The config object is handed through
  **verbatim** (identity) — the dispatcher adds no defaulting, copying, or
  translation, so every engine-config field (crypto transforms included)
  reaches the engine untouched.
* `AgentType` — `Literal` of the 7 slugs; `AgentTaskConfig` — the union of the
  7 engine configs. Every engine config class and `create_<engine>_task`
  factory is re-exported for convenience.
* `analyze_account(agent_type, creds)` / `analyze_accounts(agent_type, creds)`
  — dispatch to the per-engine account analyzer (plural form fans opencode out
  to one `AccountInfo` per configured provider; single-account engines return
  a 0/1-element list).
* `AGENTS` / `get_agent_info(agent_type)` — canonical per-engine metadata
  (`optio_agents.AgentInfo`).

## Config uniformity guarantee

Every member of `AgentTaskConfig` subclasses the shared mixins from
`optio_agents.config_types`, so these field sets are uniform across all 7
engines (guarded by `tests/test_config_uniformity.py`):

* `ClaustrumConfigMixin` — `fs_isolation` / `extra_allowed_dirs` /
  `delivery_type`.
* `BlobCryptoConfigMixin` — `session_blob_encrypt`/`decrypt` (per-process
  session snapshot, ds-scoped key) and `seed_blob_encrypt`/`decrypt` (shared
  pool-account SEED tar, pool-scoped key; falls back to the session pair when
  unset). Asymmetric pairs raise `ValueError` at construction on every engine.

Full mixin semantics: `packages/optio-agents/AGENTS.md`.

## Dependency direction

Depends on `optio-agents` and all seven engine wrappers; consumed by
applications wanting one entry point (e.g. `optio-demo`).
