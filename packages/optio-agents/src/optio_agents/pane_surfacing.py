"""Shared launch-failure surfacing for the tmux/ttyd engines.

When an agent launch fails, the operator otherwise sees only ``<engine> exited
N`` in optio.log — the real reason (an exec denial, a startup crash, a claustrum
"permission denied") is painted to the tmux PTY and lost.

These helpers mirror the live PTY to a pane-log via ``tmux pipe-pane`` and, on
abnormal exit, tail that mirror (ANSI-stripped) into optio.log after the terminal
ERROR line.

``pipe-pane`` is used INSTEAD of a stdout/stderr redirect on purpose: redirecting
the agent's streams makes ``isatty()`` false and the TUI never renders (the launch
then fails for a different reason, with no output at all). ``pipe-pane`` copies the
PTY output stream and leaves the agent's PTY intact.

The pane-log lives OUTSIDE the workdir (the workdir is torn down on failure,
taking any in-workdir log with it) and is read by the launch wrapper's bash
payload, which runs UNCONFINED (claustrum wraps only the agent argv, not the tmux
server or the surrounding bash), so ``/tmp`` is reachable for the tail.
"""
from __future__ import annotations

import os
import shlex

# A sed program that strips ANSI escapes (CSI sequences + OSC strings) so a
# captured TUI-pane mirror reads as plain text when surfaced into optio.log.
# Real ESC/BEL bytes are embedded directly (not \x1b text) so the program works
# regardless of whether the runtime sed interprets hex escapes.
#  - '/' is the s/// delimiter, so no pattern may contain a literal '/' (the CSI
#    intermediate-byte range 0x20-0x2F is omitted — colour/cursor codes are
#    ESC[...<final-byte> with no intermediates).
#  - LC_ALL=C is REQUIRED: under a UTF-8 locale GNU sed treats [@-~] as a
#    collation range (which excludes the CSI final bytes); byte semantics fix it.
_ESC = "\x1b"
_BEL = "\x07"
ANSI_STRIP_SED = (
    "LC_ALL=C sed -E 's/" + _ESC + r"\[[0-9;?]*[@-~]//g; "
    "s/" + _ESC + r"[()][0-9A-Za-z]//g; "
    "s/" + _ESC + r"\][^" + _BEL + "]*(" + _BEL + "|" + _ESC + r"\\)//g'"
)


def pane_log_path(workdir: str, engine: str, *, root_env: str | None = None,
                  default_root: str = "/tmp/optio-panes") -> str:
    """Absolute pane-log path OUTSIDE the workdir, stable across a task's calls.

    Keyed by the task folder name (the parent of ``<workdir>``) so the mirror set
    up at launch and the tail read on failure resolve to the same file. Override
    the root dir with ``root_env`` (an env var name), else ``default_root``."""
    root = (
        (os.environ.get(root_env, "").strip() if root_env else "")
        or default_root
    ).rstrip("/")
    name = os.path.basename(os.path.dirname(workdir.rstrip("/"))) or "session"
    return f"{root}/{name}/{engine}-pane.log"


def pipe_pane_cmd(tmux_path: str, socket_path: str, session_name: str,
                  pane_log: str) -> str:
    """The ``tmux pipe-pane`` command that mirrors ``session_name``'s live pane
    to ``pane_log``. The caller must ``mkdir -p`` the pane-log's dir first (see
    :func:`mkdir_pane_dir_cmd`)."""
    return (
        f"{shlex.quote(tmux_path)} -S {shlex.quote(socket_path)} pipe-pane "
        f"-t {shlex.quote(session_name)} "
        f"-o {shlex.quote('cat >> ' + shlex.quote(pane_log))}"
    )


def mkdir_pane_dir_cmd(pane_log: str) -> str:
    """Shell command to create the pane-log's parent dir (idempotent)."""
    return f"mkdir -p {shlex.quote(os.path.dirname(pane_log))}"


def error_tail_snippet(log_path: str, pane_log: str, engine: str, *,
                       lines: int = 150) -> str:
    """A bash fragment (for the launch payload's failure branch) that appends the
    tail of the ANSI-stripped pane mirror to ``log_path``, so the real launch
    failure is surfaced in optio.log after the ``ERROR: <engine> exited N`` line.
    Best-effort: silent when the mirror is absent."""
    return (
        f"echo '--- {engine} tmux pane (tail, ANSI-stripped) ---' "
        f">> {shlex.quote(log_path)}; "
        f"tail -n {lines} {shlex.quote(pane_log)} 2>/dev/null | {ANSI_STRIP_SED} "
        f">> {shlex.quote(log_path)} 2>/dev/null; "
    )
