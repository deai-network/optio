"""Build the claustrum filesystem-allowlist flags for a claude launch.

Three parts:
  * a curated static BASELINE of what claude + its tool subprocesses need
    (system dirs, /dev nodes, /proc, CA certs) — see _BASELINE. Produced by
    tracing a real claude session (LD_PRELOAD open/stat tracer) and distilling
    the touched paths; it is a build-time artifact, NOT a runtime trace.
  * DYNAMIC per-task paths (the workdir, the claude install/cache tree).
  * CALLER extras (ClaudeCodeTaskConfig.extra_allowed_dirs).

Output is the ordered list of claustrum grant flags, e.g.
``["--rox", "/usr", ..., "--rwx", "/wd", "--rox", "/cache", "--ro", "/data"]``.
Non-existent paths are harmless: claustrum ignores missing paths.
"""

from __future__ import annotations

from .types import AllowedDir

# (flag, path) baseline. --rox = read+execute (binaries/libs), --ro = read-only.
# Extended in Task 7 from the tracer output. Missing paths are ignored by claustrum.
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
    # Pseudo-terminals: claude runs in a TUI inside tmux, which allocates a pty.
    ("--rw", "/dev/pts"),
    ("--rw", "/dev/ptmx"),
]


def build_grant_flags(
    *,
    workdir: str,
    claude_cache_dir: str,
    extra_allowed_dirs: list[AllowedDir] | None,
    host_home: str | None = None,
) -> list[str]:
    """Return the ordered list of claustrum grant flags for a launch.

    ``workdir`` (the per-task tree, incl. the isolated home) is granted rwx so
    claude tools may write and execute scripts. ``claude_cache_dir`` (where the
    real claude+node binaries live, outside the workdir) is granted read+exec.

    Grants reach claustrum verbatim (no shell between), and the claude process
    runs under an isolated $HOME — so a caller extra with a leading ``~/`` is
    expanded against ``host_home`` (the REAL host home) here.
    """
    flags: list[str] = []
    for flag, path in _BASELINE:
        flags += [flag, path]
    flags += ["--rwx", workdir.rstrip("/")]
    flags += ["--rox", claude_cache_dir.rstrip("/")]
    for ad in extra_allowed_dirs or []:
        path = ad.path
        if host_home and (path == "~" or path.startswith("~/")):
            path = host_home.rstrip("/") + path[1:]
        flags += [f"--{ad.mode}", path]
    return flags
