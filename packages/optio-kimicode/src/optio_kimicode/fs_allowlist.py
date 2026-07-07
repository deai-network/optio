"""Build the claustrum filesystem-allowlist flags for a kimi launch.

Ported from ``optio_claudecode.fs_allowlist`` (claude→kimi); the only existing
claustrum implementation. Three parts:

  * a curated static BASELINE of what kimi + its tool subprocesses need
    (system dirs, /dev nodes, /proc, CA certs) — see ``_BASELINE``. Produced by
    tracing a real agent session and distilling the touched paths; a build-time
    artifact, NOT a runtime trace.
  * DYNAMIC per-task paths (the workdir, the kimi binary cache).
  * CALLER extras (``KimiCodeTaskConfig.extra_allowed_dirs``).

Output is the ordered list of claustrum grant flags, e.g.
``["--rox", "/usr", ..., "--rwx", "/wd", "--rox", "/cache", "--ro", "/data"]``.
Non-existent paths are harmless: claustrum ignores missing paths.

``AllowedDir.mode`` is the shared 4-value superset ``ro``/``rw``/``rox``/``rwx``
(``optio_agents.config_types``): extras map to ``--ro`` / ``--rw`` / ``--rox`` /
``--rwx`` (all valid claustrum flags). kimi is Landlock-only, so ``rox``≡``ro``
and ``rwx``≡``rw`` conceptually, but claustrum expresses the execute bit
natively when supplied. The wrapper's own fixed grants (``--rwx`` workdir,
``--rox`` cache) are not caller-driven.
"""

from __future__ import annotations

from .types import AllowedDir

# (flag, path) baseline. --rox = read+execute (binaries/libs), --ro = read-only,
# --rw = read+write. Missing paths are ignored by claustrum.
_BASELINE: list[tuple[str, str]] = [
    ("--rox", "/usr"),
    ("--rox", "/bin"),
    ("--rox", "/sbin"),
    ("--rox", "/lib"),
    ("--rox", "/lib64"),
    ("--rox", "/lib32"),
    ("--ro", "/etc"),
    ("--ro", "/etc/ssl"),
    ("--ro", "/etc/resolv.conf"),
    ("--ro", "/proc"),
    ("--rw", "/dev/null"),
    ("--rw", "/dev/zero"),
    ("--ro", "/dev/urandom"),
    ("--ro", "/dev/random"),
    ("--rw", "/dev/tty"),
    # Pseudo-terminals: kimi's tool subprocesses (bash) may allocate a pty.
    ("--rw", "/dev/pts"),
    ("--rw", "/dev/ptmx"),
]


def build_grant_flags(
    *,
    workdir: str,
    kimi_cache_dir: str,
    extra_allowed_dirs: list[AllowedDir] | None,
    host_home: str | None = None,
) -> list[str]:
    """Return the ordered list of claustrum grant flags for a launch.

    ``workdir`` (the per-task tree, incl. the isolated home) is granted rwx so
    kimi's tools may write and execute scripts. ``kimi_cache_dir`` (where the
    real kimi binary lives, outside the workdir — the launch symlink's target)
    is granted read+exec so kimi can be exec'd under the isolated ``$HOME``.

    Grants reach claustrum verbatim (no shell between), and the kimi process
    runs under an isolated ``$HOME`` — so a caller extra with a leading ``~/``
    is expanded against ``host_home`` (the REAL host home) here.
    """
    flags: list[str] = []
    for flag, path in _BASELINE:
        flags += [flag, path]
    flags += ["--rwx", workdir.rstrip("/")]
    flags += ["--rox", kimi_cache_dir.rstrip("/")]
    for ad in extra_allowed_dirs or []:
        path = ad.path
        if host_home and (path == "~" or path.startswith("~/")):
            path = host_home.rstrip("/") + path[1:]
        flags += [f"--{ad.mode}", path]
    return flags
