# optio-grok — Agent Cheatsheet

Task wrapper for xAI's Grok Build CLI (`grok`): ttyd iframe mode (TUI) or
headless ACP conversation mode, with resume snapshots, seed lifecycle, and
claustrum fs-isolation. Entry point: `session.run_grok_session(ctx, config)`
with a `GrokTaskConfig` (see `types.py`). In conversation mode an ACP
`plan` update, or a `todo_write` tool call, sets the process percent and
status line (`optio_agents.todos`).

## Config surface (`GrokTaskConfig`)

Frozen kw-only dataclass composing the shared mixins from
`optio_agents.config_types` — inherited fields stay top-level:

* `ClaustrumConfigMixin` — the fs-isolation triad (`fs_isolation`,
  `extra_allowed_dirs`, `delivery_type`; delivery_type is MANDATORY while
  isolation is on).
* `BlobCryptoConfigMixin` — the GridFS blob-crypto quartet:
  * `session_blob_encrypt` / `session_blob_decrypt` wrap the resume
    snapshot's workdir tar (grok's session store lives under `home/.grok`
    INSIDE the workdir, so that one tar IS the session blob — no separate
    home blob like claudecode's).
  * `seed_blob_encrypt` / `seed_blob_decrypt` wrap the shared pool-account
    seed tar (pool-scoped key); they fall back to the session pair when
    unset. All seed ops in `session.py` / `cred_watcher.py` (merge_seed,
    credential watcher + teardown save-back, capture_seed) read the seed
    transforms ONLY through the `config.seed_encrypt` / `seed_decrypt`
    accessors; snapshot ops keep `config.session_blob_*`.

  Per pair, setting one member without the other is a `ValueError` at
  construction (`_validate_blob_crypto` in `__post_init__`).
* `SessionControlsConfigMixin` — the conversation widget's controls bar:
  `show_session_controls` (requires conversation mode + `conversation_ui`) and
  the optional `session_controls` allowlist of control ids (None: all). grok's
  only control is `model` (switched inline via `session/set_model`). Every
  controls snapshot — widgetData `controls` and the `x-optio-control-update`
  re-emitted after a model switch — comes from the session's
  `_build_controls`, which goes through `filter_controls`; the listener's
  `/control` refuses an id outside `settable_controls` (403 `not-allowed`;
  nothing is settable while the bar is off).

## Seed lifecycle

`seed_id` (str or leased `SeedProvider`) merges the stored grok identity
(`auth.json` + `config.toml`, manifest in `seed_manifest.py`) into a fresh
workdir pre-launch; `cred_watcher` saves rotated tokens back into the seed
in-session and at teardown, renewing the lease; `on_seed_saved` enables
capture at teardown of a fresh authed session. All ignored on resume.

## Testing

`make test` in the package root (needs the Docker Mongo on :27017). Sessions
run against `tests/fake_grok.py` via the shim scripts; crypto-routing
callable-identity coverage lives in `tests/test_session_seed.py`, config
validation in `tests/test_config.py`.
