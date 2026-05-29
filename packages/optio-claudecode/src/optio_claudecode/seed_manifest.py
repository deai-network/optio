"""claudecode adopter of the generic optio-host seed engine.

Defines the claudecode seed manifest (HOME layout + capture-time include
triage + consume-time rekey), the Mongo collection suffix, and ergonomic
`delete_seed` / `list_seeds` wrappers that bind the suffix for consuming
apps.
"""

from __future__ import annotations

import json
import logging

from optio_host import seeds
from optio_host.host import Host

_LOG = logging.getLogger(__name__)

CLAUDE_SEED_SUFFIX = "_claudecode_seeds"
CLAUDE_SEED_MANIFEST_VERSION = 1


async def _rekey_claude_json_projects(host: Host) -> None:
    """Rewrite the single `projects` entry in home/.claude.json to the new
    cwd, preserving its value (trust flags, allowedTools, MCP enablement)
    so an autonomous task isn't blocked by claude's trust prompt.

    Empty / multi-entry / missing / malformed -> left as-is (a fresh trust
    prompt is the safe fallback).
    """
    workdir = host.workdir.rstrip("/")
    path = f"{workdir}/home/.claude.json"
    try:
        raw = await host.fetch_bytes_from_host(path)
    except FileNotFoundError:
        return
    try:
        data = json.loads(raw.decode("utf-8"))
    except (ValueError, UnicodeDecodeError):
        _LOG.warning("seed: .claude.json is not valid JSON; leaving projects as-is")
        return
    projects = data.get("projects")
    if not isinstance(projects, dict) or len(projects) != 1:
        return
    (old_value,) = projects.values()
    data["projects"] = {workdir: old_value}
    await host.put_file_to_host(
        json.dumps(data).encode("utf-8"), path,
    )


CLAUDE_SEED_MANIFEST = seeds.SeedManifest(
    home_subdir="home",
    include=[
        ".claude/.credentials.json",
        ".claude/settings.json",
        ".claude/mcp-needs-auth-cache.json",
        ".claude/plugins",
        ".claude.json",
    ],
    version=CLAUDE_SEED_MANIFEST_VERSION,
    consume_transform=_rekey_claude_json_projects,
)


async def delete_seed(db, prefix: str, seed_id: str):
    """Delete a claudecode seed doc; returns its GridFS blobId (or None).

    Ergonomic wrapper binding `CLAUDE_SEED_SUFFIX` so consuming apps don't
    need to know the collection suffix. The caller still removes the
    returned blob from GridFS.
    """
    return await seeds.delete_seed(
        db, prefix=prefix, suffix=CLAUDE_SEED_SUFFIX, seed_id=seed_id,
    )


async def list_seeds(db, prefix: str) -> list[dict]:
    """List claudecode seeds as [{seedId, createdAt}, ...]."""
    return await seeds.list_seeds(db, prefix=prefix, suffix=CLAUDE_SEED_SUFFIX)
