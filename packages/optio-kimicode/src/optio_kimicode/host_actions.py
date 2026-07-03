"""Kimi-specific actions over a generic Host.

Free functions; each takes a Host or HookContext and uses only generic
primitives (run_command, resolve_host_home, etc.). No isinstance branches.

Ported from ``optio_grok.host_actions``. This module currently carries the
host constructor (:func:`build_host`) and the per-task isolation identity
(:func:`_isolation_env`). The full two-tier install
(:func:`ensure_kimicode_installed`) is a documented stub — plan group 4
completes it (reuse a worker ``kimi`` on PATH, else the vendor installer,
symlinked into ``<workdir>/home/.local/bin/kimi``).
"""

from __future__ import annotations

import logging
import os
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from optio_agents import HookContextProtocol
    from optio_host import Host

_LOG = logging.getLogger(__name__)


def build_host(ssh, taskdir: str) -> "Host":
    """ssh_config + taskdir -> LocalHost/RemoteHost. Lifted from
    session._build_host so engine-free callers (verify) share it (mirrors
    grok's ``host_actions.build_host``)."""
    from optio_host.host import LocalHost, RemoteHost

    if ssh is None:
        os.makedirs(taskdir, exist_ok=True)
        host: "Host" = LocalHost(taskdir=taskdir)
        os.makedirs(host.workdir, exist_ok=True)
        return host
    return RemoteHost(ssh_config=ssh, taskdir=taskdir)


def _isolation_env(workdir: str) -> dict[str, str]:
    """Single source of truth for a task's HOME/XDG/kimi agent identity.

    Every kimi launch (the ``kimi web`` iframe surface and the ACP conversation
    launch) derives its environment from this map so isolation is identical
    across launch paths. Five explicit keys, all rooted at ``<workdir>/home``:

    - ``HOME`` — general isolation; also the anchor for the per-task
      ``.local/bin`` the launch PATH prepends and for XDG defaults.
    - ``KIMI_CODE_HOME`` — relocates kimi's ENTIRE data root (credentials,
      sessions, global ``AGENTS.md``, skills) into the per-task home, away from
      the operator's ``~/.kimi-code``. Set to ``<workdir>/home`` so the task's
      kimi state lives directly under the isolated home.
    - ``XDG_CONFIG_HOME`` / ``XDG_DATA_HOME`` / ``XDG_CACHE_HOME`` — pin the XDG
      base dirs into the task home so no XDG-respecting tool reaches the
      operator's ``~/.config`` / ``~/.cache``.

    Unlike grok there is no ``CLAUDE_CONFIG_DIR``: kimi has no claude-compat
    layer (it reads ``AGENTS.md``, relocated via ``KIMI_CODE_HOME``).

    PATH is intentionally NOT included: it is layered by the caller (launch adds
    ``<home>/.local/bin`` ahead of the worker PATH)."""
    home = f"{workdir.rstrip('/')}/home"
    return {
        "HOME": home,
        "KIMI_CODE_HOME": home,
        "XDG_CONFIG_HOME": f"{home}/.config",
        "XDG_DATA_HOME": f"{home}/.local/share",
        "XDG_CACHE_HOME": f"{home}/.cache",
    }


async def ensure_kimicode_installed(
    hook_ctx: "HookContextProtocol",
    *,
    install_if_missing: bool = True,
    install_dir: str | None = None,
    progress_label: str = "Preparing Kimi Code…",
) -> str:
    """Provision ``kimi`` for this task (two-tier install) — DEFERRED to group 4.

    The full implementation (ported from grok/claudecode's
    ``ensure_<agent>_installed``) reuses a worker ``kimi`` on the login-shell
    PATH when present (fast copy into an evictable cache outside the workdir),
    else runs the vendor installer ``code.kimi.com/kimi-code/install.sh``, then
    symlinks the cached binary into ``<workdir>/home/.local/bin/kimi`` (re-linked
    idempotently after a resume). Returns that per-task launch path.

    Stubbed for now: raises ``NotImplementedError`` so callers fail loudly rather
    than silently assuming kimi is provisioned. See plan Task 4.1.
    """
    raise NotImplementedError(
        "ensure_kimicode_installed: two-tier kimi install is implemented in "
        "plan group 4 (Task 4.1). Until then, ensure a `kimi` binary is on the "
        "worker PATH or pass an explicit install_dir."
    )
