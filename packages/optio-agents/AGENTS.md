# optio-agents — Agent Cheatsheet

The agent-coordination layer shared by every engine wrapper: the optio.log
keyword protocol (parser + session driver), `HookContext`, the abstract
`Conversation` surface, claustrum provisioning, and the engine-neutral
task-config vocabulary described below.

## Shared config vocabulary (`optio_agents.config_types`)

Engine `TaskConfig` dataclasses (all `frozen=True, kw_only=True`) compose
their common field sets from mixins here instead of redeclaring them. Fields
stay top-level on each config — callers write `fs_isolation=` /
`session_blob_encrypt=` verbatim, no nesting.

* `ClaustrumConfigMixin` — the claustrum filesystem-isolation triad
  (`fs_isolation`, `extra_allowed_dirs`, `delivery_type`). Engines call
  `self._validate_claustrum()` from `__post_init__`.
* `BlobCryptoConfigMixin` — at-rest crypto for the two GridFS blob channels,
  four `Callable[[bytes], bytes] | None` fields (default None = plaintext):
  * `session_blob_encrypt` / `session_blob_decrypt` — wrap this process's
    session snapshot (the per-task home tar; ds-scoped key).
  * `seed_blob_encrypt` / `seed_blob_decrypt` — wrap the SEED tar (the shared
    pool account; pool-scoped key). Falls back to the session pair when unset,
    so single-key callers are unaffected.

  Setting one member of a pair without the other is a config error; engines
  call `self._validate_blob_crypto()` from `__post_init__`. The seed→session
  fallback lives ONLY in the `seed_encrypt` / `seed_decrypt` accessor
  properties — engine seed ops must read the transforms through those, never
  the raw `seed_blob_*` fields.

Also here: `AllowedDir`, `ConversationMode`, `ToolVerbosity` (+
`TOOL_VERBOSITIES`, the SSOT validation set), `ThinkingVerbosity`,
`SeedProvider` / `SeedUnavailableError`.

## Dependency direction

Depends on `optio-host` and `optio-core`; consumed by every engine wrapper
(`optio-claudecode`, `optio-opencode`, `optio-codex`, `optio-cursor`,
`optio-grok`, `optio-kimicode`, `optio-antigravity`) and by
`optio-agents-all`.
