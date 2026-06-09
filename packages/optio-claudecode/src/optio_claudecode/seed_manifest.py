"""claudecode adopter of the generic optio-agents seed engine.

Defines the claudecode seed manifest (HOME layout + capture-time include
triage + consume-time rekey), the Mongo collection suffix, and ergonomic
`delete_seed` / `list_seeds` wrappers that bind the suffix for consuming
apps.
"""

from __future__ import annotations

import json
import logging

from optio_agents import seeds
from optio_host.host import Host

_LOG = logging.getLogger(__name__)

CLAUDE_SEED_SUFFIX = "_claudecode_seeds"
# v2: dropped ".claude/plugins" from the seed (was the official marketplace
# tree -- ~376 files / 4.2 MB, 99% of the seed). Claude re-installs the
# marketplace on launch (network is available in analyze), so the seed stays
# ~26 KB and save-back repacks are trivial.
CLAUDE_SEED_MANIFEST_VERSION = 2


async def _rekey_claude_json_projects(host: Host) -> None:
    """Collapse `.claude.json` `projects` to a SINGLE trusted entry keyed at the
    launch workdir, so an autonomous task is never blocked by claude's
    folder-trust prompt ("Is this a project you trust?", which
    `--permission-mode bypassPermissions` does NOT suppress -> the session exits
    in tmux).

    With CLAUDE_CONFIG_DIR=<home>/.claude, claude uses `<home>/.claude/.claude.json`.
    A seed captured before that change has the file at the old `<home>/.claude.json`
    (home root); normalize it into `.claude/` first so old seeds keep working
    (seeds are caller-encrypted and cannot be migrated offline — this is the
    consume-time equivalent).

    Reuse an existing entry's value (preserving trust flags / allowedTools / MCP
    enablement) else synthesize one, force `hasTrustDialogAccepted: true`, and drop
    every other (stale, foreign) workdir entry. Missing / malformed .claude.json
    -> left as-is.
    """
    workdir = host.workdir.rstrip("/")
    new_path = f"{workdir}/home/.claude/.claude.json"
    old_path = f"{workdir}/home/.claude.json"

    moved_from_old = False
    try:
        raw = await host.fetch_bytes_from_host(new_path)
    except FileNotFoundError:
        try:
            raw = await host.fetch_bytes_from_host(old_path)
        except FileNotFoundError:
            return
        moved_from_old = True  # old-layout seed: relocate into .claude/

    try:
        data = json.loads(raw.decode("utf-8"))
    except (ValueError, UnicodeDecodeError):
        _LOG.warning("seed: .claude.json is not valid JSON; leaving projects as-is")
        return
    if not isinstance(data, dict):
        return
    projects = data.get("projects")
    value: dict = {}
    if isinstance(projects, dict) and projects:
        chosen = next(
            (v for v in projects.values()
             if isinstance(v, dict) and v.get("hasTrustDialogAccepted")),
            next(iter(projects.values())),
        )
        if isinstance(chosen, dict):
            value = dict(chosen)
    value["hasTrustDialogAccepted"] = True
    data["projects"] = {workdir: value}
    await host.put_file_to_host(json.dumps(data).encode("utf-8"), new_path)
    if moved_from_old:
        await host.remove_file(old_path)


CLAUDE_CRED_MANIFEST = seeds.SeedManifest(
    home_subdir="home",
    include=[".claude/.credentials.json"],
    version=CLAUDE_SEED_MANIFEST_VERSION,
)


CLAUDE_SEED_MANIFEST = seeds.SeedManifest(
    home_subdir="home",
    include=CLAUDE_CRED_MANIFEST.include + [
        ".claude/settings.json",
        ".claude/mcp-needs-auth-cache.json",
        ".claude/.claude.json",  # new (CLAUDE_CONFIG_DIR layout)
        ".claude.json",          # old layout — kept so pre-existing seeds still extract
    ],
    version=CLAUDE_SEED_MANIFEST_VERSION,
    consume_transform=_rekey_claude_json_projects,
)


async def delete_seed(store, seed_id: str):
    """Delete a claudecode seed doc; returns its GridFS blobId (or None).

    Takes an optio store binding (``optio.mongo_store`` — exposes ``db`` and
    ``prefix``) as-is, so consuming apps hand over the whole namespace handle
    instead of threading db+prefix (or knowing the collection suffix). The
    caller still removes the returned blob from GridFS.
    """
    return await seeds.delete_seed(
        store.db, prefix=store.prefix, suffix=CLAUDE_SEED_SUFFIX, seed_id=seed_id,
    )


async def purge_seed(store, seed_id: str) -> None:
    """Fully expunge a claudecode seed (doc + GridFS blob); raises KeyError if
    absent. Takes an optio store binding (``optio.mongo_store``) as-is."""
    await seeds.purge_seed(store.db, prefix=store.prefix, suffix=CLAUDE_SEED_SUFFIX, seed_id=seed_id)


async def list_seeds(store) -> list[dict]:
    """List claudecode seeds as [{seedId, createdAt}, ...]. Takes an optio
    store binding (``optio.mongo_store``) as-is."""
    return await seeds.list_seeds(store.db, prefix=store.prefix, suffix=CLAUDE_SEED_SUFFIX)
