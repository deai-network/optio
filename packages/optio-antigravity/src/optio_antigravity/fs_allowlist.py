"""Claustrum filesystem-allowlist flags for an agy launch (thin over shared).

The system baseline + the workdir/cache/extras composition are engine-neutral and
now live in :mod:`optio_agents.fs_grants` (single source of truth for every
wrapper). This module is a thin, agy-named delegator: it maps the agy-specific
``agy_cache_dir`` argument onto the shared ``engine_cache_dir`` and forwards the
rest verbatim.

agy runs under an ISOLATED ``$HOME`` (``<workdir>/home``), so its ``~/.gemini``
config/transcript/artifacts tree lives INSIDE the workdir and is already covered
by the shared ``--rwx`` workdir grant — no separate ``~/.gemini`` flag is needed.
The per-task temp likewise lives under the workdir home (XDG_* pinned there). A
caller extra with a leading ``~/`` is expanded against the REAL host home (the
shared builder does this against ``host_home``), because grants reach claustrum
verbatim and the agy process's own ``$HOME`` is isolated.
"""

from __future__ import annotations

from optio_agents import fs_grants

from .types import AllowedDir  # noqa: F401  (re-exported for existing importers)


def build_grant_flags(
    *,
    workdir: str,
    agy_cache_dir: str,
    extra_allowed_dirs: list[AllowedDir] | None,
    host_home: str | None = None,
) -> list[str]:
    """Return the ordered claustrum grant flags for an agy launch.

    Delegates to :func:`optio_agents.fs_grants.build_grant_flags`; ``agy_cache_dir``
    (where the real agy binary lives, outside the workdir — the launch symlink's
    target) is the shared builder's ``engine_cache_dir`` (granted read+exec so agy
    can be exec'd under the isolated ``$HOME``). ``workdir`` (the per-task tree
    incl. the isolated home and agy's ``~/.gemini`` state) is granted rwx.
    """
    return fs_grants.build_grant_flags(
        workdir=workdir,
        engine_cache_dir=agy_cache_dir,
        extra_allowed_dirs=extra_allowed_dirs,
        host_home=host_home,
    )
