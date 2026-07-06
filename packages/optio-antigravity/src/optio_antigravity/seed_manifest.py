"""antigravity adopter of the generic optio-agents seed engine.

Defines the antigravity seed manifest (HOME layout + capture-time include
triage), the Mongo collection suffix, a token-store validity gate, a
consume-time rekey, and ergonomic `delete_seed` / `list_seeds` / `purge_seed`
wrappers that bind the suffix for consuming apps.

An antigravity *seed* carries the logged-in Google identity that ``agy``
provisions under its isolated HOME (``<workdir>/home`` — see
``host_actions._isolation_env``). agy keeps its state under ``~/.gemini`` (a tree
shared with the Gemini CLI), so — like grok/kimi — this manifest uses
``home_subdir="home"`` with ``.gemini/`` prefixes on the include paths (the
engine roots capture/extract at ``host.workdir + "/" + home_subdir``).

============================================================================
S1 spike RESULTS (empirical, from a real interactive Google login, 2026-07-06).
Where agy actually writes its login state under the isolated HOME:

- Token store: ``.gemini/antigravity-cli/antigravity-oauth-token`` — JSON
  ``{"auth_method": "consumer", "token": {"access_token", "token_type",
  "refresh_token", "expiry"}}``. This is the ONLY file agy rewrites on OAuth
  refresh → the sole member of the cred (save-back) manifest. (The design's §2
  guess of ``.gemini/oauth_creds.json`` / a keyring blob was WRONG — agy writes
  a plain file here; the system keyring was untouched across login.)
- Settings: ``.gemini/antigravity-cli/settings.json`` — ``{AutoUpdate:false,
  trustedWorkspaces:[<capture workdir>]}``. The trustedWorkspaces path is the
  CAPTURE workdir, so a replant must rekey it to the new workdir (else agy
  treats the restored workspace as untrusted) → ``_rekey_trusted_workspaces``.
- Onboarding + project: ``.gemini/antigravity-cli/cache/onboarding.json``
  (``onboardingComplete:true`` — skips the onboarding flow on replant) and
  ``.gemini/config/`` (``config.json``, ``mcp_config.json``, ``.migrated``,
  ``projects/default-cli-project.json``, ``cache`` pointers).

Excluded (regenerated / device-specific / static / conversation state):
``installation_id`` (per-install id, regenerated), ``log/``,
``conversation_summaries.db``, ``jetski_state.pbtxt``, ``knowledge/``,
``builtin/`` (static skills re-provisioned by agy), ``bin/`` (binary assets),
and all of ``.gemini/antigravity/`` (transcript.jsonl, artifacts — the seed
engine's contract is "no conversation/session data").
============================================================================
"""

from __future__ import annotations

import json
import shlex

from optio_agents import seeds
from optio_host.host import Host

ANTIGRAVITY_SEED_SUFFIX = "_antigravity_seeds"
ANTIGRAVITY_SEED_MANIFEST_VERSION = 1


# The rotating OAuth token store — the single file agy mutates on token refresh
# (sole member of the save-back cred manifest).
_TOKEN_STORE_RELPATH = ".gemini/antigravity-cli/antigravity-oauth-token"

# agy's settings: AutoUpdate + trustedWorkspaces (the latter rekeyed on consume).
_SETTINGS_RELPATH = ".gemini/antigravity-cli/settings.json"

# Onboarding completion flag — without it a fresh replant re-enters onboarding.
_ONBOARDING_RELPATH = ".gemini/antigravity-cli/cache/onboarding.json"

# Login/onboarding-provisioned config tree (account/project/mcp). A directory
# include: the engine captures/extracts every member under it.
_CONFIG_DIR_RELPATH = ".gemini/config"


# Credential-only manifest for in-session save-back (the write-back analog of
# the full ANTIGRAVITY_SEED_MANIFEST; mirrors grok's / kimi's cred manifest).
# ONLY the rotating token store — settings/config are static login-time state.
ANTIGRAVITY_CRED_MANIFEST = seeds.SeedManifest(
    home_subdir="home",
    include=[_TOKEN_STORE_RELPATH],
    version=ANTIGRAVITY_SEED_MANIFEST_VERSION,
    consume_transform=None,
)


async def _rekey_trusted_workspaces(host: Host) -> None:
    """Point settings.json ``trustedWorkspaces`` at the replant workdir.

    The captured settings.json trusts the ORIGINAL (capture-time) workdir path;
    a replant restores under a different workdir, so agy would treat the restored
    workspace as untrusted. Rewrite the list to exactly the new workdir. Parse →
    set → re-serialize (never blind-append), and tolerate an absent/corrupt file
    (agy rewrites settings on start anyway). Best-effort: never raise out of the
    consume path."""
    workdir = host.workdir.rstrip("/")
    settings_abs = f"{workdir}/home/{_SETTINGS_RELPATH}"
    try:
        raw = await host.run_command(f"cat {shlex.quote(settings_abs)} 2>/dev/null")
        doc = json.loads(raw.stdout) if raw.exit_code == 0 and raw.stdout.strip() else {}
        if not isinstance(doc, dict):
            doc = {}
    except (ValueError, UnicodeDecodeError):
        doc = {}
    doc["trustedWorkspaces"] = [workdir]
    payload = json.dumps(doc, indent=2)
    # write via a heredoc through the host (uniform Local/Remote)
    await host.write_text(f"home/{_SETTINGS_RELPATH}", payload)


# Full identity seed = token store PLUS everything login/onboarding provisions
# that a fresh, already-authenticated task needs (settings, onboarding flag,
# config/account/project tree). A token-only replant is NOT enough (kimi's
# "token but no provider -> login screen" lesson), so the full manifest is a
# strict superset of the cred manifest. Consume rekeys trustedWorkspaces.
ANTIGRAVITY_SEED_MANIFEST = seeds.SeedManifest(
    home_subdir="home",
    include=[
        *ANTIGRAVITY_CRED_MANIFEST.include,
        _SETTINGS_RELPATH,
        _ONBOARDING_RELPATH,
        _CONFIG_DIR_RELPATH,
    ],
    version=ANTIGRAVITY_SEED_MANIFEST_VERSION,
    consume_transform=_rekey_trusted_workspaces,
)


def token_capture_is_valid(raw: bytes | None) -> bool:
    """Whether a captured token-store blob holds a usable credential.

    Gates seed CAPTURE and in-session save-back: reject unless the store carries
    a non-empty OAuth ``refresh_token``. An absent / empty / unparseable /
    logged-out store would otherwise poison the seed with a dead identity.

    agy's real store (S1): ``{"auth_method": ..., "token": {"refresh_token":
    "...", "access_token": ..., "expiry": ...}}`` — the refresh token is NESTED
    under ``token``. Accept a top-level ``refresh_token`` too, defensively, in
    case a future agy build flattens it.
    """
    if not raw:
        return False
    try:
        data = json.loads(raw.decode("utf-8"))
    except (ValueError, UnicodeDecodeError):
        return False
    if not isinstance(data, dict) or not data:
        return False
    token = data.get("token")
    if isinstance(token, dict) and token.get("refresh_token"):
        return True
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
