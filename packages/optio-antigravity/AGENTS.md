# optio-antigravity — Agent Cheatsheet

Run Google Antigravity (`agy`) as an optio task: ttyd/tmux iframe surface, or
synthetic transcript-driven conversation mode (`agy` has no streaming server —
turns are replayed from its `~/.gemini` state tree). Local subprocess or remote
host over SSH. The config surface deliberately mirrors `optio_grok.types`.

Full design: `docs/2026-07-06-optio-antigravity-wrapper-design.md`; the
wrapper-porting playbook is `docs/writing-agent-wrappers.md`.

## Public API

```python
from optio_antigravity import AntigravityTaskConfig, create_antigravity_task
```

`AntigravityTaskConfig` is a frozen, keyword-only dataclass. Its full field
inventory lives in `src/optio_antigravity/types.py` — every field carries an
explanatory comment there; that file is the reference, not this cheatsheet.

## Shared config mixins (from `optio_agents.config_types`)

`AntigravityTaskConfig(BlobCryptoConfigMixin, ClaustrumConfigMixin)` — both
mixins keep their fields top-level, so callers write them verbatim:

* `ClaustrumConfigMixin` — the filesystem-isolation triad `fs_isolation` /
  `extra_allowed_dirs` / `delivery_type` (Landlock-only claustrum here:
  `rox`≡`ro`, `rwx`≡`rw`).
* `BlobCryptoConfigMixin` — at-rest crypto for the two GridFS blob channels:
  * `session_blob_encrypt` / `session_blob_decrypt` — the resume workdir tar
    (it carries agy's `~/.gemini` conversation state, so it IS the session
    blob; ds-scoped key).
  * `seed_blob_encrypt` / `seed_blob_decrypt` — seed tars (shared pool
    account; pool-scoped key). Falls back to the session pair when unset.

  Setting one member of a pair without the other raises `ValueError`. All
  SEED ops in `session.py` (`merge_seed`, `run_credential_watcher`,
  `save_back_if_changed`, `capture_seed`) read the transforms through the
  mixin's `seed_encrypt` / `seed_decrypt` accessors — the ONLY home of the
  seed→session fallback; snapshot save/restore keeps `session_blob_*`.

## Seed lifecycle

Capture: fresh session + `on_seed_saved` set + valid token store on teardown →
the manifest paths under `home/.gemini` are tarred into a seed row
(`{prefix}_antigravity_seeds`). Consume: `seed_id` (str or `SeedProvider`)
merges the stored Google identity into the fresh workdir BEFORE launch; a
leased seed is renewed by the in-session credential watcher and released after
the final token save-back. Routing test:
`tests/test_session_seed.py::test_seed_ops_use_seed_pair_snapshot_ops_use_session_pair`.
