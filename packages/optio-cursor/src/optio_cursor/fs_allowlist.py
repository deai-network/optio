"""Build the claustrum filesystem-allowlist flags for a cursor-agent launch.

STAGE-8 MECHANISM DECISION (Task 0 probe, 2026-07-02) — CLAUSTRUM, not native.
================================================================================
Host was NOT logged into cursor, so the live enforcement half was skipped; the
decision rests on binary/schema analysis of the cursor-agent dist bundle
(cursorsandbox --help, ``strings cursorsandbox``, and the Node ``index.js``
bundle).

Cursor DOES ship a native sandbox, and it clears two of the three bars:
  * (a) Allowlist-configurable — YES. ``cursorsandbox --policy <json>`` takes a
        unified policy file: PolicyFile{version, logFormat, policy}; ``policy``
        is an internally-tagged SandboxPolicy enum
        (workspace_readwrite | workspace_readonly | insecure_none) with
        ``cwd``, ``additionalReadwritePaths``, ``additionalReadonlyPaths``,
        ``networkAccess``, ``disableTmpWrite``, ``blockGitWrites``,
        ``ignoreMapping``, plus ``networkPolicy`` (allow/deny/default) and
        ``networkPolicyStrict``.
  * (c) Fail-closed — YES, for shell-exec. In index.js, te() gates the
        sandboxed spawn: for any non-``insecure_none`` policy it calls
        isSandboxHelperSupported() (which runs
        ``cursorsandbox --policy … --preflight-only -- /bin/true``; exit 0 =
        supported, exit 2 = unsupported kernel), and THROWS "Sandbox policy '…'
        is not supported on this system" when unsupported. It never falls back
        to an unconfined run.

But it FAILS the decisive architectural bar: WHOLE-PROCESS confinement.
  * cursorsandbox is self-described as "Sandboxing helper for Everysphere
    shell-exec". It wraps each SHELL command the agent runs (oe() -> te() -> the
    ``cursorsandbox --policy … -- <cmd>`` spawn, env CURSOR_SANDBOX=native).
  * The Node AGENT process is never Landlock-confined: there is no restrict_self
    / prctl / seccomp / landlock in the agent bundle (index.js) — those symbols
    exist ONLY inside the standalone ``cursorsandbox`` binary, applied to the
    wrapped command. So the agent's OWN in-process file tools (Write/Edit, via
    the native ``file_service.*.node`` module + Node fs) write UNCONFINED and
    escape the path allowlist entirely.

That is the material difference from optio-grok, whose native sandbox qualified
precisely because grok Landlock-confines its ENTIRE process at startup (its own
writes included). Cursor has no equivalent whole-process self-sandbox.

The Stage-8 goal is to confine the cursor agent AND every tool/subprocess,
kernel-enforced, fail-closed. A shell-exec-only sandbox leaves the agent's
primary file-mutation capability outside kernel enforcement, so native is
insufficient. We therefore port claudecode's claustrum: wrap the WHOLE
cursor-agent launch argv in the claustrum Landlock CLI (confines the Node agent
process and all descendants), fail-closed, and launch cursor-agent with
``--sandbox disabled`` so its own per-command helper does not nest under the
outer Landlock ruleset (wiring is Stage-8 Task 2).

--------------------------------------------------------------------------------
This module is a file-by-file port of optio-claudecode's ``fs_allowlist.py``.
Three parts:
  * a curated static BASELINE of what cursor-agent + its tool subprocesses need
    (system dirs, /dev nodes, /proc, CA certs) — see _BASELINE. Mirrors the
    claudecode baseline distilled from a real session trace; missing paths are
    harmless (claustrum ignores them).
  * DYNAMIC per-task paths (the workdir, the cursor install/cache tree at
    ``~/.local/share/cursor-agent/versions/<v>/``).
  * CALLER extras (CursorTaskConfig.extra_allowed_dirs).

Output is the ordered list of claustrum grant flags, e.g.
``["--rox", "/usr", ..., "--rwx", "/wd", "--rox", "/cache", "--ro", "/data"]``.
Non-existent paths are harmless: claustrum ignores missing paths.
"""

from __future__ import annotations

from .types import AllowedDir

# (flag, path) baseline. --rox = read+execute (binaries/libs), --ro = read-only.
# Ported from optio-claudecode. Missing paths are ignored by claustrum.
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
    # Pseudo-terminals: cursor-agent runs in a TUI inside tmux, which allocates
    # a pty.
    ("--rw", "/dev/pts"),
    ("--rw", "/dev/ptmx"),
]


def build_grant_flags(
    *,
    workdir: str,
    cursor_cache_dir: str,
    extra_allowed_dirs: list[AllowedDir] | None,
    host_home: str | None = None,
) -> list[str]:
    """Return the ordered list of claustrum grant flags for a launch.

    ``workdir`` (the per-task tree, incl. the isolated home) is granted rwx so
    cursor-agent tools may write and execute scripts. ``cursor_cache_dir``
    (where the real cursor-agent binaries live, outside the workdir — the
    ``~/.local/share/cursor-agent/versions/<v>/`` tree) is granted read+exec.

    Grants reach claustrum verbatim (no shell between), and the cursor-agent
    process runs under an isolated $HOME — so a caller extra with a leading
    ``~/`` is expanded against ``host_home`` (the REAL host home) here.
    """
    flags: list[str] = []
    for flag, path in _BASELINE:
        flags += [flag, path]
    flags += ["--rwx", workdir.rstrip("/")]
    flags += ["--rox", cursor_cache_dir.rstrip("/")]
    for ad in extra_allowed_dirs or []:
        path = ad.path
        if host_home and (path == "~" or path.startswith("~/")):
            path = host_home.rstrip("/") + path[1:]
        flags += [f"--{ad.mode}", path]
    return flags
