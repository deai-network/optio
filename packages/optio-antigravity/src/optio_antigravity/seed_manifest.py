"""antigravity adopter of the generic optio-agents seed engine.

Defines the antigravity seed manifest (HOME layout + capture-time include
triage), the Mongo collection suffix, a token-store validity gate, and
ergonomic `delete_seed` / `list_seeds` / `purge_seed` wrappers that bind the
suffix for consuming apps.

An antigravity *seed* carries the logged-in Google identity that ``agy``
provisions under its isolated HOME (``<workdir>/home`` — see
``host_actions._isolation_env``). All of agy's state lives under
``~/.gemini`` (a tree shared with the Gemini CLI), so — like grok/kimi — this
manifest uses ``home_subdir="home"`` with ``.gemini/`` prefixes on the include
paths (the engine roots capture/extract at ``host.workdir + "/" + home_subdir``).

Like grok/kimi (and unlike claudecode) the credential is a cwd-independent OAuth
blob, so the likely-outcome is no consume-time rekey (``consume_transform=None``).

============================================================================
TODO(S1): the real credential-location spike (plan Task S1) has NOT run yet.
Per the design (§2), agy stores its OAuth token in the **OS keyring**
(libsecret / Secret Service), NOT a plain file — so where a *headless* worker's
token actually lands is unproven. This module is written against the design's
**most-likely** outcome (§2 option 1: a keyring **file fallback** when no Secret
Service is present) and every path/format assumption below is flagged for
reconciliation once S1 records the empirical login diff. The seed *shape* pinned
here (full provisioned set ⊋ token-only cred manifest + a validity gate) is
invariant across the S1 outcomes; only the concrete relpaths move.
============================================================================
"""

from __future__ import annotations

import json

from optio_agents import seeds

ANTIGRAVITY_SEED_SUFFIX = "_antigravity_seeds"
ANTIGRAVITY_SEED_MANIFEST_VERSION = 1


# --- credential-location assumptions (all TODO(S1)) ------------------------
#
# The rotating OAuth token store. agy shares ``~/.gemini`` with the Gemini CLI,
# whose OAuth credentials live at ``~/.gemini/oauth_creds.json``; the design's
# §2-option-1 file fallback is expected to land here (or, per option 1, beside
# the settings under ``antigravity-cli/``). This is the ONLY member of the
# save-back (cred) manifest — the single file agy mutates on token refresh.
# TODO(S1): confirm the real file path (and whether it exists at all, vs. a
# keyring-export blob that the seed must synthesize).
_TOKEN_STORE_RELPATH = ".gemini/oauth_creds.json"

# The login-provisioned account/provider registration (the Gemini-CLI shared
# tree records the signed-in Google account + selected project here). Without it
# a replant has a token but no account binding and can drop to the login screen
# (the kimi "token but no provider" failure mode). TODO(S1): confirm path/name.
_ACCOUNT_REGISTRATION_RELPATH = ".gemini/google_accounts.json"

# agy's own settings (color scheme, model, trusted paths, AutoUpdate keys).
# Confirmed in the design's state-tree table; non-secret, login-independent.
_SETTINGS_RELPATH = ".gemini/antigravity-cli/settings.json"


# Credential-only manifest for in-session save-back (the write-back analog of
# the full ANTIGRAVITY_SEED_MANIFEST; mirrors grok's GROK_CRED_MANIFEST and
# kimi's KIMI_CRED_MANIFEST). Carries ONLY the rotating token store — the single
# file agy mutates on OAuth-token refresh; settings/account registration are
# static login-time state and are never written back mid-session.
ANTIGRAVITY_CRED_MANIFEST = seeds.SeedManifest(
    home_subdir="home",
    include=[_TOKEN_STORE_RELPATH],
    version=ANTIGRAVITY_SEED_MANIFEST_VERSION,
    consume_transform=None,
)


# Full identity seed = the rotating token store PLUS everything login provisions
# that a fresh, already-authenticated task needs: the account/provider
# registration and agy's settings. A token-only replant is NOT enough (kimi's
# "token but no provider -> login screen" lesson), so the full manifest is a
# strict superset of the cred manifest.
#
# TODO(S1): once S1 enumerates exactly which files login writes, add any further
# non-secret provisioned state here (e.g. ``.gemini/config/mcp_config.json`` if
# login-provisioned). The conversation/session state under
# ``.gemini/antigravity/`` (transcript.jsonl, artifacts/) is deliberately
# EXCLUDED — the seed engine's contract is "no conversation/session data".
ANTIGRAVITY_SEED_MANIFEST = seeds.SeedManifest(
    home_subdir="home",
    include=[
        *ANTIGRAVITY_CRED_MANIFEST.include,
        _ACCOUNT_REGISTRATION_RELPATH,
        _SETTINGS_RELPATH,
    ],
    version=ANTIGRAVITY_SEED_MANIFEST_VERSION,
    consume_transform=None,  # no cwd-rekey (likely-outcome; TODO(S1))
)


def token_capture_is_valid(raw: bytes | None) -> bool:
    """Whether a captured token-store blob holds a usable credential.

    Gates seed CAPTURE and in-session save-back: a creds-only capture must be
    rejected unless the token store actually carries a non-empty OAuth refresh
    token. An absent / empty / unparseable / logged-out store would otherwise
    poison the seed (or an existing seed's blob) with a dead identity — the
    antigravity analog of grok's ``cred_fingerprint`` / ``capture_gate_ok`` gate.

    TODO(S1): reconcile the store format with the real login spike. The
    likely-outcome (§2 option 1) is a JSON object carrying a Google OAuth token
    with a ``refresh_token`` field; if S1 finds a keyring-export blob or a
    different schema, update the parse/field check here (the *reject-empty*
    contract is invariant regardless).
    """
    if not raw:
        return False
    try:
        data = json.loads(raw.decode("utf-8"))
    except (ValueError, UnicodeDecodeError):
        return False
    if not isinstance(data, dict) or not data:
        return False
    return bool(data.get("refresh_token"))


async def delete_seed(store, seed_id: str):
    """Delete an antigravity seed doc; returns its GridFS blobId (or None).

    Takes an optio store binding (``optio.mongo_store`` — exposes ``db`` and
    ``prefix``) as-is, so consuming apps hand over the whole namespace handle
    instead of threading db+prefix (or knowing the collection suffix). The
    caller still removes the returned blob from GridFS.
    """
    return await seeds.delete_seed(
        store.db, prefix=store.prefix, suffix=ANTIGRAVITY_SEED_SUFFIX, seed_id=seed_id,
    )


async def list_seeds(store) -> list[dict]:
    """List antigravity seeds as [{seedId, createdAt}, ...]. Takes an optio
    store binding (``optio.mongo_store``) as-is."""
    return await seeds.list_seeds(
        store.db, prefix=store.prefix, suffix=ANTIGRAVITY_SEED_SUFFIX,
    )


async def purge_seed(store, seed_id: str) -> None:
    """Fully expunge an antigravity seed (doc + its GridFS blob); raises KeyError
    if absent. Takes an optio store binding (``optio.mongo_store``) as-is.

    Mirrors ``optio_grok.purge_seed`` / ``optio_kimicode.purge_seed``; a thin
    re-export of the ``optio_agents.seeds.purge_seed`` engine."""
    return await seeds.purge_seed(
        store.db, prefix=store.prefix, suffix=ANTIGRAVITY_SEED_SUFFIX, seed_id=seed_id,
    )
