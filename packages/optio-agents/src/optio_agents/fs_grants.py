"""Shared claustrum filesystem-allowlist flags for every wrapper launch.

Lifted from the (previously duplicated) per-wrapper fs_allowlist.py. The system
baseline is engine-neutral; the workdir + engine binary cache + caller extras are
appended per launch."""
from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .config_types import AllowedDir

# Ordered (flag, path) system baseline. --rox = read+execute (binaries/libs),
# --ro = read-only, --rw = read-write.
BASELINE: tuple[tuple[str, str], ...] = (
    ("--rox", "/usr"), ("--rox", "/bin"), ("--rox", "/sbin"),
    ("--rox", "/lib"), ("--rox", "/lib64"), ("--rox", "/lib32"),
    ("--ro", "/etc"), ("--ro", "/etc/ssl"), ("--ro", "/etc/resolv.conf"),
    ("--ro", "/proc"),
    ("--rw", "/dev/null"), ("--rw", "/dev/zero"),
    ("--ro", "/dev/urandom"), ("--ro", "/dev/random"),
    ("--rw", "/dev/tty"), ("--rw", "/dev/pts"), ("--rw", "/dev/ptmx"),
)

_MODE_FLAG = {"ro": "--ro", "rw": "--rw", "rox": "--rox", "rwx": "--rwx"}


def build_grant_flags(
    *,
    workdir: str,
    engine_cache_dir: str,
    extra_allowed_dirs: "list[AllowedDir] | None" = None,
    host_home: str | None = None,
    extra_baseline: "list[tuple[str, str]] | None" = None,
) -> list[str]:
    """Return the ordered claustrum grant flags for a launch.

    ``workdir`` (the per-task tree incl. the isolated home) is granted rwx.
    ``engine_cache_dir`` (where the real agent binary lives, outside the workdir)
    is granted read+exec. ``extra_baseline`` lets an engine add its own always-on
    dirs (e.g. opencode's config tree). Caller ``~``/``~/`` extras expand against
    ``host_home`` (the REAL host home)."""
    flags: list[str] = []
    for flag, path in (*BASELINE, *(extra_baseline or [])):
        flags += [flag, path]
    flags += ["--rwx", workdir.rstrip("/")]
    flags += ["--rox", engine_cache_dir.rstrip("/")]
    for ad in extra_allowed_dirs or []:
        path = ad.path
        if host_home and (path == "~" or path.startswith("~/")):
            path = host_home.rstrip("/") + path[1:]
        flags += [_MODE_FLAG[ad.mode], path.rstrip("/")]
    return flags
