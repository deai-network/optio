"""kimi adopter of the generic optio-agents seed engine.

Defines the kimi seed manifest (HOME layout + capture-time include triage),
the Mongo collection suffix, and ergonomic `delete_seed` / `list_seeds` /
`purge_seed` wrappers that bind the suffix for consuming apps.

A kimi *seed* carries the logged-in identity that lives under
``KIMI_CODE_HOME`` (``<workdir>/home``): the credentials directory
``credentials/`` holding ``kimi-code.json`` (``access_token`` /
``refresh_token`` / ``expires_at`` / ``scope`` / ``token_type`` /
``expires_in``). Replanting it into a fresh workdir is the answer to headless
login.

Like grok (and opencode, and unlike claudecode), kimi needs no consume-time
rekey: its credential file is a cwd-independent JSON blob, so
``consume_transform`` is None.

Like grok, the full seed carries a ``config.toml`` member ALONGSIDE the rotating
credential. This is load-bearing, not optional: ``kimi login`` provisions the
managed OAuth provider (``[providers."managed:kimi-code"]`` + models +
``default_model``) into ``config.toml`` — provisioning is login-only, kimi never
re-derives the provider from the credential at startup. A seed with only
``credentials/`` replants a token but NO provider, so the daemon reports
``providers_count: 0`` and shows the login screen despite a perfectly valid token.
Hence ``KIMI_SEED_MANIFEST`` = creds dir + ``config.toml``, while the save-back
``KIMI_CRED_MANIFEST`` carries ONLY the creds dir (config.toml is static provider
config; only ``kimi-code.json`` rotates, so only it is written back mid-session).

Path note: the engine roots capture/extract at ``host.workdir + "/" +
home_subdir`` (see ``SeedManifest.home_subdir`` — "HOME relative to the
workdir, e.g. 'home'"). KIMI_CODE_HOME is ``<workdir>/home``, so the manifest
uses ``home_subdir="home"`` with the ``credentials`` creds dir as its include
member (mirroring the ``home/credentials/kimi-code.json`` layout).
"""

from __future__ import annotations

from optio_agents import seeds

KIMI_SEED_SUFFIX = "_kimicode_seeds"
KIMI_SEED_MANIFEST_VERSION = 1


# Credential-only manifest for in-session save-back (the write-back analog of
# the full KIMI_SEED_MANIFEST; mirrors grok's GROK_CRED_MANIFEST and opencode's
# OPENCODE_CRED_MANIFEST). Carries the creds dir, which holds the rotating
# single-use ``kimi-code.json`` — the only file kimi mutates on token refresh.
KIMI_CRED_MANIFEST = seeds.SeedManifest(
    home_subdir="home",
    include=["credentials"],
    version=KIMI_SEED_MANIFEST_VERSION,
    consume_transform=None,
)


# Full identity seed = the rotating credential dir PLUS ``config.toml`` (the
# managed:kimi-code provider registration that login provisions; without it a
# replant has a token but no provider -> login screen). Mirrors grok appending
# ``.grok/config.toml`` to its full seed.
KIMI_SEED_MANIFEST = seeds.SeedManifest(
    home_subdir="home",
    include=[*KIMI_CRED_MANIFEST.include, "config.toml"],
    version=KIMI_SEED_MANIFEST_VERSION,
    consume_transform=None,  # no cwd-rekey for kimi
)


async def delete_seed(store, seed_id: str):
    """Delete a kimi seed doc; returns its GridFS blobId (or None).

    Takes an optio store binding (``optio.mongo_store`` — exposes ``db`` and
    ``prefix``) as-is, so consuming apps hand over the whole namespace handle
    instead of threading db+prefix (or knowing the collection suffix). The
    caller still removes the returned blob from GridFS.
    """
    return await seeds.delete_seed(
        store.db, prefix=store.prefix, suffix=KIMI_SEED_SUFFIX, seed_id=seed_id,
    )


async def list_seeds(store) -> list[dict]:
    """List kimi seeds as [{seedId, createdAt}, ...]. Takes an optio store
    binding (``optio.mongo_store``) as-is."""
    return await seeds.list_seeds(store.db, prefix=store.prefix, suffix=KIMI_SEED_SUFFIX)


async def purge_seed(store, seed_id: str) -> None:
    """Fully expunge a kimi seed (doc + its GridFS blob); raises KeyError if
    absent. Takes an optio store binding (``optio.mongo_store``) as-is.

    Mirrors ``optio_grok.purge_seed`` / ``optio_opencode.purge_seed``; a thin
    re-export of the ``optio_agents.seeds.purge_seed`` engine."""
    return await seeds.purge_seed(
        store.db, prefix=store.prefix, suffix=KIMI_SEED_SUFFIX, seed_id=seed_id,
    )
