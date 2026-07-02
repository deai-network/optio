"""codex adopter of the generic optio-agents seed engine.

Defines the codex seed manifest (HOME layout + capture-time include triage),
the Mongo collection suffix, and ergonomic ``delete_seed`` / ``list_seeds`` /
``purge_seed`` wrappers that bind the suffix for consuming apps.

A codex *seed* carries the logged-in identity that lives under ``CODEX_HOME``
(``<workdir>/home/.codex``): ``auth.json`` (ChatGPT mode: ``auth_mode`` +
``tokens{id_token, access_token, refresh_token}`` + ``last_refresh``; API-key
mode: ``OPENAI_API_KEY``) plus ``config.toml``. Replanting it into a fresh
workdir is the answer to headless login.

The include list is an allowlist, which is also the exclusion mechanism:
``packages/`` (the ~286MB binary cache), ``*.sqlite*`` (absolute
rollout-path poison; rebuilt from rollouts), ``sessions/``, ``cache/``,
``tmp/``, logs etc. are simply never members.

Like grok/opencode (and unlike claudecode), codex needs no consume-time
rekey: auth/config are cwd-independent, so ``consume_transform`` is None.
The one cwd-dependent consume step — pre-trusting the new workdir via a
``[projects."<workdir>"]`` entry in config.toml — is deliberately a
post-merge edit in the session's ``_prepare`` (see
``host_actions.ensure_workdir_trusted``), NOT a manifest transform: codex
rewrites config.toml itself at runtime, so optio's edit must stay a
minimal, idempotent append against the *planted* file, applied exactly at
the point the workdir is known.

Path note: the engine roots capture/extract at ``host.workdir + "/" +
home_subdir`` (see ``SeedManifest.home_subdir``). CODEX_HOME is
``<workdir>/home/.codex``, so the manifest uses ``home_subdir="home"`` with
``.codex/`` prefixes on the include paths (mirroring grok's ``.grok/…``).
"""

from __future__ import annotations

from optio_agents import seeds

CODEX_SEED_SUFFIX = "_codex_seeds"
CODEX_SEED_MANIFEST_VERSION = 1


# Credential-only manifest for in-session save-back (the write-back analog
# of the full CODEX_SEED_MANIFEST; mirrors grok's GROK_CRED_MANIFEST and
# opencode's OPENCODE_CRED_MANIFEST). Only auth.json is re-captured — the
# seed's config.toml is never touched by save-back.
CODEX_CRED_MANIFEST = seeds.SeedManifest(
    home_subdir="home",
    include=[".codex/auth.json"],
    version=CODEX_SEED_MANIFEST_VERSION,
    consume_transform=None,
)


CODEX_SEED_MANIFEST = seeds.SeedManifest(
    home_subdir="home",
    include=CODEX_CRED_MANIFEST.include + [
        ".codex/config.toml",
    ],
    version=CODEX_SEED_MANIFEST_VERSION,
    consume_transform=None,  # no cwd-rekey for codex (pre-trust is in _prepare)
)


async def delete_seed(store, seed_id: str):
    """Delete a codex seed doc; returns its GridFS blobId (or None).

    Takes an optio store binding (``optio.mongo_store`` — exposes ``db`` and
    ``prefix``) as-is, so consuming apps hand over the whole namespace handle
    instead of threading db+prefix (or knowing the collection suffix). The
    caller still removes the returned blob from GridFS.
    """
    return await seeds.delete_seed(
        store.db, prefix=store.prefix, suffix=CODEX_SEED_SUFFIX, seed_id=seed_id,
    )


async def list_seeds(store) -> list[dict]:
    """List codex seeds as [{seedId, createdAt}, ...]. Takes an optio store
    binding (``optio.mongo_store``) as-is."""
    return await seeds.list_seeds(store.db, prefix=store.prefix, suffix=CODEX_SEED_SUFFIX)


async def purge_seed(store, seed_id: str) -> None:
    """Fully expunge a codex seed (doc + its GridFS blob); raises KeyError if
    absent. Takes an optio store binding (``optio.mongo_store``) as-is.

    Mirrors ``optio_grok.purge_seed`` / ``optio_claudecode.purge_seed``; a
    thin re-export of the ``optio_agents.seeds.purge_seed`` engine."""
    return await seeds.purge_seed(
        store.db, prefix=store.prefix, suffix=CODEX_SEED_SUFFIX, seed_id=seed_id,
    )
