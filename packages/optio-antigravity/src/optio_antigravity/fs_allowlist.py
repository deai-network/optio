"""Build the claustrum filesystem-allowlist flags for an agy launch.

Ported from ``optio_kimicode.fs_allowlist`` (kimi→agy); the claustrum lineage is
claudecode → kimicode → antigravity. Three parts:

  * a curated static BASELINE of what agy + its tool subprocesses need
    (system dirs, /dev nodes, /proc, CA certs) — see ``_BASELINE``. A build-time
    artifact distilled from a traced agent session, NOT a runtime trace.
  * DYNAMIC per-task paths (the workdir, the agy binary cache).
  * CALLER extras (``AntigravityTaskConfig.extra_allowed_dirs``).

Output is the ordered list of claustrum grant flags, e.g.
``["--rox", "/usr", ..., "--rwx", "/wd", "--rox", "/cache", "--ro", "/data"]``.
Non-existent paths are harmless: claustrum ignores missing paths.

agy runs under an ISOLATED ``$HOME`` (``<workdir>/home``), so its ``~/.gemini``
config/transcript/artifacts tree lives INSIDE the workdir and is already covered
by the ``--rwx`` workdir grant — no separate ``~/.gemini`` flag is needed. The
per-task temp likewise lives under the workdir home (XDG_* pinned there).

``AllowedDir.mode`` is the 2-value ``ro``/``rw`` enum: extras map to ``--ro`` /
``--rw`` (both valid claustrum flags). The execute-bearing grants (``--rwx``
workdir, ``--rox`` cache) are the wrapper's own fixed grants, not caller-driven.
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
    # Pseudo-terminals: agy's TUI (inside tmux) and each conversation ``agy -p``
    # turn run under a pty (`script -qec`); agy's tool subprocesses (bash) may
    # allocate their own.
    ("--rw", "/dev/pts"),
    ("--rw", "/dev/ptmx"),
]


def build_grant_flags(
    *,
    workdir: str,
    agy_cache_dir: str,
    extra_allowed_dirs: list[AllowedDir] | None,
    host_home: str | None = None,
) -> list[str]:
    """Return the ordered list of claustrum grant flags for a launch.

    ``workdir`` (the per-task tree, incl. the isolated home and agy's ``~/.gemini``
    state) is granted rwx so agy's tools may write and execute scripts.
    ``agy_cache_dir`` (where the real agy binary lives, outside the workdir — the
    launch symlink's target) is granted read+exec so agy can be exec'd under the
    isolated ``$HOME``.

    Grants reach claustrum verbatim (no shell between), and the agy process runs
    under an isolated ``$HOME`` — so a caller extra with a leading ``~/`` is
    expanded against ``host_home`` (the REAL host home) here.
    """
    flags: list[str] = []
    for flag, path in _BASELINE:
        flags += [flag, path]
    flags += ["--rwx", workdir.rstrip("/")]
    flags += ["--rox", agy_cache_dir.rstrip("/")]
    for ad in extra_allowed_dirs or []:
        path = ad.path
        if host_home and (path == "~" or path.startswith("~/")):
            path = host_home.rstrip("/") + path[1:]
        flags += [f"--{ad.mode}", path]
    return flags
