"""cursor adopter of the generic optio-agents seed engine.

Defines the cursor seed manifest (HOME layout + capture-time include triage),
the Mongo collection suffix, and ergonomic `delete_seed` / `list_seeds` /
`purge_seed` wrappers that bind the suffix for consuming apps.

A cursor *seed* carries the logged-in identity that lives under the isolated
HOME (``<workdir>/home``): ``.config/cursor/auth.json`` (JSON holding
``accessToken`` / ``refreshToken``; ``status`` reads it, ``logout`` deletes
it — our launch env sets ``XDG_CONFIG_HOME=<workdir>/home/.config``) plus
``.cursor/cli-config.json`` (user prefs + permission rules). Replanting it
into a fresh workdir is the answer to headless login.

Like grok/opencode (and unlike claudecode), cursor needs no consume-time
rekey: its auth/config are cwd-independent, so ``consume_transform`` is None.

Path note: the engine roots capture/extract at ``host.workdir + "/" +
home_subdir`` (see ``SeedManifest.home_subdir`` — "HOME relative to the
workdir, e.g. 'home'"). The cursor auth file is
``<workdir>/home/.config/cursor/auth.json``, so the manifest uses
``home_subdir="home"`` with include paths relative to it (mirroring grok's
reconciled layout).
"""

from __future__ import annotations

from optio_agents import seeds

CURSOR_SEED_SUFFIX = "_cursor_seeds"
CURSOR_SEED_MANIFEST_VERSION = 1


# Credential-only manifest for in-session save-back (the write-back analog
# of the full CURSOR_SEED_MANIFEST; mirrors grok's GROK_CRED_MANIFEST and
# claudecode's CLAUDE_CRED_MANIFEST). Only auth.json is re-captured —
# the seed's cli-config.json is never touched by save-back.
CURSOR_CRED_MANIFEST = seeds.SeedManifest(
    home_subdir="home",
    include=[".config/cursor/auth.json"],
    version=CURSOR_SEED_MANIFEST_VERSION,
    consume_transform=None,
)


CURSOR_SEED_MANIFEST = seeds.SeedManifest(
    home_subdir="home",
    include=CURSOR_CRED_MANIFEST.include + [
        ".cursor/cli-config.json",
    ],
    version=CURSOR_SEED_MANIFEST_VERSION,
    consume_transform=None,  # no cwd-rekey for cursor
)


async def delete_seed(store, seed_id: str):
    """Delete a cursor seed doc; returns its GridFS blobId (or None).

    Takes an optio store binding (``optio.mongo_store`` — exposes ``db`` and
    ``prefix``) as-is, so consuming apps hand over the whole namespace handle
    instead of threading db+prefix (or knowing the collection suffix). The
    caller still removes the returned blob from GridFS.
    """
    return await seeds.delete_seed(
        store.db, prefix=store.prefix, suffix=CURSOR_SEED_SUFFIX, seed_id=seed_id,
    )


async def list_seeds(store) -> list[dict]:
    """List cursor seeds as [{seedId, createdAt}, ...]. Takes an optio store
    binding (``optio.mongo_store``) as-is."""
    return await seeds.list_seeds(store.db, prefix=store.prefix, suffix=CURSOR_SEED_SUFFIX)


async def purge_seed(store, seed_id: str) -> None:
    """Fully expunge a cursor seed (doc + its GridFS blob); raises KeyError if
    absent. Takes an optio store binding (``optio.mongo_store``) as-is.

    Mirrors ``optio_grok.purge_seed`` / ``optio_opencode.purge_seed``;
    a thin re-export of the ``optio_agents.seeds.purge_seed`` engine."""
    return await seeds.purge_seed(
        store.db, prefix=store.prefix, suffix=CURSOR_SEED_SUFFIX, seed_id=seed_id,
    )
