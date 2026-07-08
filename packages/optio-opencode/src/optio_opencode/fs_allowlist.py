"""Build the claustrum filesystem-allowlist flags for an ``opencode web`` launch.

Thin delegator over the shared ``optio_agents.fs_grants`` builder (the system
baseline + workdir + engine-cache + caller-extras logic lives there, one SSOT
across every wrapper). opencode contributes ONE engine-specific grant on top:

  * opencode's home/config/data/cache all live under ``<workdir>/home`` — already
    covered by ``--rwx <workdir>``.
  * BUT ``OPENCODE_DB`` = ``<taskdir>/opencode.db`` sits ONE LEVEL ABOVE the
    workdir (the ``taskdir/workdir`` layout). Under-granting there breaks
    opencode's live DB, so the whole taskdir is granted rwx via ``extra_baseline``.
  * the opencode binary cache (``_resolve_install_dir``, outside every workdir)
    is granted read+exec as the engine cache.

Non-existent paths are harmless: claustrum ignores missing paths.
"""

from __future__ import annotations

from optio_agents import fs_grants

# Re-exported so tests / callers can build extra grants without reaching into
# optio_agents directly (mirrors the other wrappers' fs_allowlist surface).
from .types import AllowedDir  # noqa: F401


def build_grant_flags(
    *,
    workdir: str,
    taskdir: str,
    opencode_cache_dir: str,
    extra_allowed_dirs: "list[AllowedDir] | None",
    host_home: str | None = None,
) -> list[str]:
    """Return the ordered claustrum grant flags for an opencode launch.

    ``workdir`` (the per-task tree incl. the isolated ``home``) is granted rwx.
    ``taskdir`` (the workdir's parent, holding the live ``opencode.db``) is
    granted rwx as an engine ``extra_baseline`` so the server can write its DB.
    ``opencode_cache_dir`` (where the real opencode binary lives, outside every
    workdir) is granted read+exec. ``~``/``~/`` caller extras expand against
    ``host_home`` (the REAL host home)."""
    return fs_grants.build_grant_flags(
        workdir=workdir,
        engine_cache_dir=opencode_cache_dir,
        extra_allowed_dirs=extra_allowed_dirs,
        host_home=host_home,
        extra_baseline=[("--rwx", taskdir.rstrip("/"))],
    )
