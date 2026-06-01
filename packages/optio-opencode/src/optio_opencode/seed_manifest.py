"""opencode adopter of the generic optio-agents seed engine.

Defines the opencode seed manifest (HOME layout + capture-time include
triage), the Mongo collection suffix, and ergonomic `delete_seed` /
`list_seeds` / `purge_seed` wrappers that bind the suffix for consuming
apps.

Unlike claudecode, opencode needs no consume-time rekey: its auth/config
are cwd-independent, so `consume_transform` is None.
"""

from __future__ import annotations

from optio_agents import seeds

OPENCODE_SEED_SUFFIX = "_opencode_seeds"
OPENCODE_SEED_MANIFEST_VERSION = 1


OPENCODE_SEED_MANIFEST = seeds.SeedManifest(
    home_subdir="home",
    include=[
        ".local/share/opencode/auth.json",
        ".config/opencode/opencode.json",
        ".config/opencode/plugins",
    ],
    version=OPENCODE_SEED_MANIFEST_VERSION,
    consume_transform=None,  # no cwd-rekey for opencode
)


async def delete_seed(db, prefix: str, seed_id: str):
    """Delete an opencode seed doc; returns its GridFS blobId (or None).

    Ergonomic wrapper binding `OPENCODE_SEED_SUFFIX` so consuming apps don't
    need to know the collection suffix. The caller still removes the
    returned blob from GridFS.
    """
    return await seeds.delete_seed(
        db, prefix=prefix, suffix=OPENCODE_SEED_SUFFIX, seed_id=seed_id,
    )


async def list_seeds(db, prefix: str) -> list[dict]:
    """List opencode seeds as [{seedId, createdAt}, ...]."""
    return await seeds.list_seeds(db, prefix=prefix, suffix=OPENCODE_SEED_SUFFIX)


async def purge_seed(db, prefix: str, seed_id: str):
    """Purge an opencode seed (doc + its GridFS blob) in one call.

    Ergonomic wrapper binding `OPENCODE_SEED_SUFFIX`, per the frozen
    Shared-contracts surface. Mirrors `optio_claudecode.purge_seed`; both
    are thin re-exports of the `optio_agents.seeds.purge_seed` engine, which
    expunges the seed doc and its GridFS blob and raises KeyError if absent.
    """
    return await seeds.purge_seed(
        db, prefix=prefix, suffix=OPENCODE_SEED_SUFFIX, seed_id=seed_id,
    )
