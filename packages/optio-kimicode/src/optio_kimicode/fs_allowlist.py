"""Thin kimicode adapter over the shared claustrum grant-flag builder.

The system baseline + grant-flag composition are engine-neutral and now live in
``optio_agents.fs_grants`` (Universal Claustrum Task 1); this module keeps a
kimicode-named wrapper so callers (the real-binary sandbox-enforce tests) express
the kimi binary cache as ``kimi_cache_dir`` and delegate the rest to the shared
builder. No duplicated ``_BASELINE`` — a single source of truth for the flags.

``AllowedDir.mode`` is the shared 4-value superset ``ro``/``rw``/``rox``/``rwx``:
extras map to ``--ro`` / ``--rw`` / ``--rox`` / ``--rwx``. kimi is Landlock-only,
so ``rox``≡``ro`` and ``rwx``≡``rw`` conceptually, but claustrum expresses the
execute bit natively when supplied.
"""

from __future__ import annotations

from optio_agents import fs_grants

from .types import AllowedDir


def build_grant_flags(
    *,
    workdir: str,
    kimi_cache_dir: str,
    extra_allowed_dirs: "list[AllowedDir] | None",
    host_home: str | None = None,
) -> list[str]:
    """Return the ordered claustrum grant flags for a kimi launch.

    ``workdir`` (the per-task tree, incl. the isolated home) is granted rwx;
    ``kimi_cache_dir`` (where the real kimi binary lives, outside the workdir —
    the launch symlink's target) is granted read+exec so kimi can be exec'd under
    the isolated ``$HOME``. Caller ``~``/``~/`` extras expand against ``host_home``
    (the REAL host home). Delegates to the shared
    :func:`optio_agents.fs_grants.build_grant_flags` (``kimi_cache_dir`` is the
    shared ``engine_cache_dir``)."""
    return fs_grants.build_grant_flags(
        workdir=workdir,
        engine_cache_dir=kimi_cache_dir,
        extra_allowed_dirs=extra_allowed_dirs,
        host_home=host_home,
    )
