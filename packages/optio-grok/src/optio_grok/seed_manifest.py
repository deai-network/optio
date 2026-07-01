"""grok adopter of the generic optio-agents seed engine.

Defines the grok seed manifest (HOME layout + capture-time include triage),
the Mongo collection suffix, and ergonomic `delete_seed` / `list_seeds` /
`purge_seed` wrappers that bind the suffix for consuming apps.

A grok *seed* carries the logged-in identity that lives under ``GROK_HOME``
(``<workdir>/home/.grok``): ``auth.json`` (keyed ``https://auth.x.ai::<uuid>``,
holding ``key`` / ``refresh_token`` / ``expires_at`` / ``oidc_*``) plus
``config.toml``. Replanting it into a fresh workdir is the answer to
headless login.

Like opencode (and unlike claudecode), grok needs no consume-time rekey:
its auth/config are cwd-independent, so ``consume_transform`` is None.

Path note: the engine roots capture/extract at ``host.workdir + "/" +
home_subdir`` (see ``SeedManifest.home_subdir`` — "HOME relative to the
workdir, e.g. 'home'"). GROK_HOME is ``<workdir>/home/.grok``, so the
manifest uses ``home_subdir="home"`` with ``.grok/`` prefixes on the
include paths (mirroring opencode's ``.local/share/opencode/...`` shape).
"""

from __future__ import annotations

from optio_agents import seeds

GROK_SEED_SUFFIX = "_grok_seeds"
GROK_SEED_MANIFEST_VERSION = 1


# Credential-only manifest for in-session save-back (the write-back analog
# of the full GROK_SEED_MANIFEST; mirrors claudecode's CLAUDE_CRED_MANIFEST
# and opencode's OPENCODE_CRED_MANIFEST). Only auth.json is re-captured —
# the seed's config.toml is never touched by save-back.
GROK_CRED_MANIFEST = seeds.SeedManifest(
    home_subdir="home",
    include=[".grok/auth.json"],
    version=GROK_SEED_MANIFEST_VERSION,
    consume_transform=None,
)


GROK_SEED_MANIFEST = seeds.SeedManifest(
    home_subdir="home",
    include=GROK_CRED_MANIFEST.include + [
        ".grok/config.toml",
    ],
    version=GROK_SEED_MANIFEST_VERSION,
    consume_transform=None,  # no cwd-rekey for grok
)


async def delete_seed(store, seed_id: str):
    """Delete a grok seed doc; returns its GridFS blobId (or None).

    Takes an optio store binding (``optio.mongo_store`` — exposes ``db`` and
    ``prefix``) as-is, so consuming apps hand over the whole namespace handle
    instead of threading db+prefix (or knowing the collection suffix). The
    caller still removes the returned blob from GridFS.
    """
    return await seeds.delete_seed(
        store.db, prefix=store.prefix, suffix=GROK_SEED_SUFFIX, seed_id=seed_id,
    )


async def list_seeds(store) -> list[dict]:
    """List grok seeds as [{seedId, createdAt}, ...]. Takes an optio store
    binding (``optio.mongo_store``) as-is."""
    return await seeds.list_seeds(store.db, prefix=store.prefix, suffix=GROK_SEED_SUFFIX)


async def purge_seed(store, seed_id: str) -> None:
    """Fully expunge a grok seed (doc + its GridFS blob); raises KeyError if
    absent. Takes an optio store binding (``optio.mongo_store``) as-is.

    Mirrors ``optio_claudecode.purge_seed`` / ``optio_opencode.purge_seed``;
    a thin re-export of the ``optio_agents.seeds.purge_seed`` engine."""
    return await seeds.purge_seed(
        store.db, prefix=store.prefix, suffix=GROK_SEED_SUFFIX, seed_id=seed_id,
    )
