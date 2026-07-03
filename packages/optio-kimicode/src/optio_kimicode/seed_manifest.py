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

Delta from grok: grok's full seed adds a ``config.toml`` member alongside its
rotating ``auth.json``; kimi keeps no config in the identity seed (model choice
is a launch flag / env var, not persisted auth), so the full
``KIMI_SEED_MANIFEST`` and the save-back ``KIMI_CRED_MANIFEST`` coincide — both
carry only the creds dir. Both are kept as distinct names to preserve grok's
public API shape (session plant reads SEED; in-session save-back reads CRED).

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


# kimi has no config member in the identity seed, so the full seed is exactly
# the credential set. (grok appends ``.grok/config.toml`` here; kimi has no
# analog to append.)
KIMI_SEED_MANIFEST = seeds.SeedManifest(
    home_subdir="home",
    include=list(KIMI_CRED_MANIFEST.include),
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
