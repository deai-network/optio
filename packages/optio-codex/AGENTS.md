# optio-codex — Agent Cheatsheet

Run OpenAI Codex CLI as an optio task — local subprocess or remote host via
SSH — either as the interactive TUI in a ttyd-served iframe or as a headless
`codex app-server` conversation session (`mode="conversation"`).

Porting playbook (shared by all wrappers): `docs/writing-agent-wrappers.md`.

## Public API

```python
from optio_codex import CodexTaskConfig, create_codex_task
```

`CodexTaskConfig` is frozen + kw_only; full field semantics live in its
docstrings (`src/optio_codex/types.py`).

## Shared config mixins

`CodexTaskConfig` inherits two shared field sets from
`optio_agents.config_types`; all fields stay top-level on the config:

* `ClaustrumConfigMixin` — `fs_isolation` / `extra_allowed_dirs` /
  `delivery_type`. Claustrum (Landlock, fail-closed) is the fs-isolation
  layer; codex's native bubblewrap sandbox cannot nest inside it, so under
  claustrum the native mode resolves to `danger-full-access` (and network is
  unconfined until the shared pasta/netns layer lands).
* `BlobCryptoConfigMixin` — the at-rest GridFS crypto quartet:
  `session_blob_encrypt`/`session_blob_decrypt` wrap the resume workdir tar
  (per-task snapshot; ds-scoped key); `seed_blob_encrypt`/`seed_blob_decrypt`
  wrap the shared pool-account SEED tar (pool-scoped key) and fall back to
  the session pair when unset. Seed ops (`merge_seed`, the credential
  watcher + final save-back, `capture_seed`) read the transforms ONLY through
  the `seed_encrypt`/`seed_decrypt` accessors; snapshot capture/restore uses
  the session pair directly. Per pair, setting one member without the other
  is a `ValueError` at construction (`_validate_blob_crypto`).

## Seed surface

`seed_id` (str, or a leasing `SeedProvider`) merges a stored codex identity
(`auth.json` + `config.toml`) into a fresh workdir pre-launch and pre-trusts
the workdir; `on_seed_saved` enables capture at teardown (gated on a valid
`auth.json`). A leased seed runs the in-session credential watcher
(save-back of rotated tokens + lease renewal) plus a final teardown
save-back. All of these ride the seed crypto pair above.
