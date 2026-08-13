# optio-cursor — Agent Cheatsheet

Run Cursor CLI (`cursor-agent`) as an optio task: ttyd/tmux iframe surface
(with the iframe-input widget), or headless ACP conversation mode publishing a
live `CursorConversation`. Local subprocess or remote host over SSH; claustrum
(Landlock, fail-closed) is the filesystem-isolation guarantee — cursor's own
`--sandbox` is NOT used for isolation (per-shell-command wrapper only).

The wrapper-porting playbook is `docs/writing-agent-wrappers.md`.

## Public API

```python
from optio_cursor import CursorTaskConfig, create_cursor_task
```

`CursorTaskConfig` is a frozen, keyword-only dataclass. Its full field
inventory lives in `src/optio_cursor/types.py` — every field carries an
explanatory comment there; that file is the reference, not this cheatsheet.

## Shared config mixins (from `optio_agents.config_types`)

`CursorTaskConfig(ClaustrumConfigMixin, BlobCryptoConfigMixin)` — both mixins
keep their fields top-level, so callers write them verbatim:

* `ClaustrumConfigMixin` — the filesystem-isolation triad `fs_isolation` /
  `extra_allowed_dirs` / `delivery_type` (delivery_type mandatory while
  fs_isolation is on).
* `BlobCryptoConfigMixin` — at-rest crypto for the two GridFS blob channels:
  * `session_blob_encrypt` / `session_blob_decrypt` — the resume snapshot's
    single workdir tar (it carries `home/.cursor` chat state, so it IS the
    session blob; ds-scoped key).
  * `seed_blob_encrypt` / `seed_blob_decrypt` — seed tars (shared pool
    account; pool-scoped key). Falls back to the session pair when unset.

  Setting one member of a pair without the other raises `ValueError`. All
  SEED ops in `session.py` (`merge_seed`, `run_credential_watcher`,
  `save_back_if_changed`, `capture_seed`) read the transforms through the
  mixin's `seed_encrypt` / `seed_decrypt` accessors — the ONLY home of the
  seed→session fallback; snapshot capture/restore keeps `session_blob_*`.

## Seed lifecycle + permission rules

Capture: fresh session + `on_seed_saved` set + valid
`home/.config/cursor/auth.json` on teardown → the manifest paths (auth.json +
`.cursor/cli-config.json`) are tarred into a seed row
(`{prefix}_cursor_seeds`). Consume: `seed_id` (str or `SeedProvider`) merges
the stored identity into the fresh workdir BEFORE launch; a leased seed is
renewed by the in-session credential watcher and released after the final
auth.json save-back (cursor's refresh token is single-use — seeded teardown is
always graceful).

Permission rules (`allowed_tools` / `disallowed_tools`) are config-planted —
cursor-agent has no `--allow`/`--deny` argv. They land in
`home/.cursor/cli-config.json` via `host_actions.apply_cli_config`, a
read-modify-write deep-merge applied AFTER the seed merge / snapshot restore:
the caller's `permissions`/`approvalMode` keys win, the seed's other keys
(editor prefs, …) survive. Routing test:
`tests/test_session_seed.py::test_seed_ops_use_seed_pair_snapshot_ops_session_pair`.
