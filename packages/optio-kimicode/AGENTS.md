# optio-kimicode — Agent Cheatsheet

Task wrapper for Moonshot's Kimi Code CLI (`kimi`): iframe mode (the embedded
`kimi web` SPA) or headless ACP conversation mode, with resume snapshots, seed
lifecycle, and claustrum fs-isolation. Entry point:
`session.run_kimicode_session(ctx, config)` with a `KimiCodeTaskConfig` (see
`types.py`).

## Config surface (`KimiCodeTaskConfig`)

Frozen kw-only dataclass composing the shared mixins from
`optio_agents.config_types` — inherited fields stay top-level:

* `ClaustrumConfigMixin` — the fs-isolation triad (`fs_isolation`,
  `extra_allowed_dirs`, `delivery_type`; delivery_type is MANDATORY while
  isolation is on). kimi is Landlock-only.
* `BlobCryptoConfigMixin` — the GridFS blob-crypto quartet:
  * `session_blob_encrypt` / `session_blob_decrypt` wrap the kimi session
    subtree tar (the two-blob resume snapshot's `sessionBlobId`; ds-scoped
    key).
  * `seed_blob_encrypt` / `seed_blob_decrypt` wrap the shared pool-account
    seed tar (pool-scoped key); they fall back to the session pair when
    unset. All seed ops in `session.py` / `cred_watcher.py` (the pre-merge
    `verify_and_refresh_seed` token refresh, merge_seed, the credential
    watcher in BOTH the iframe and conversation bodies, the teardown
    save-back, capture_seed) read the seed transforms ONLY through the
    `config.seed_encrypt` / `seed_decrypt` accessors; snapshot ops
    (`capture_snapshot` / `restore_snapshot`) keep `config.session_blob_*`.

  Per pair, setting one member without the other is a `ValueError` at
  construction (`_validate_blob_crypto` in `__post_init__`).

## Seed lifecycle

`seed_id` (str or leased `SeedProvider`) merges the stored kimi identity
(`credentials/kimi-code.json` + `config.toml`, manifest in
`seed_manifest.py`) into the workdir pre-launch — on BOTH fresh and resume
paths, since kimi's access token is short-lived (~15 min) the merge is
preceded by a host-free `verify.verify_and_refresh_seed` (refresh_token grant;
a spoiled seed aborts the launch). `cred_watcher` saves rotated tokens back
into the seed in-session and at teardown, renewing the lease; `on_seed_saved`
enables capture at teardown of a fresh authed session.

## Testing

`make test` in the package root (needs the Docker Mongo on :27017). Sessions
run against `tests/fake_kimi.py` via the shim script; crypto-routing
callable-identity coverage lives in `tests/test_seed_refresh.py`, config
validation in `tests/test_types.py`.
