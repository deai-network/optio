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

import asyncio
import logging
import os
import re
import shlex
from datetime import datetime, timezone
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from optio_agents import HookContextProtocol
    from optio_host import Host
    from optio_host.host import ProcessHandle

_LOG = logging.getLogger(__name__)


# kimi's server ready banner carries an access line with the origin and (when a
# bearer token is resolvable) the ``#token=<token>`` fragment, e.g.:
#   Local:    http://127.0.0.1:58627/#token=abc123
# ``kimi server run``'s onReady hook prints it AFTER the socket is listening, so
# the banner IS the readiness signal (mirrors grok reading ttyd's "Listening
# on" line). Group 1 = the actual port; group 2 = the optional bearer token
# (the token rides in a client-side fragment, never sent to the server).
_KIMI_READY_RE = re.compile(r"https?://[^\s/]+:(\d+)/(?:#token=(\S+))?")


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


# --- kimi resolution (Stage 0: no binary cache/download; that is Stage 5) ---


async def resolve_kimi(
    host: "Host",
    *,
    install_dir: str | None = None,
    install_if_missing: bool = True,
) -> str:
    """Host-based ``kimi`` binary resolution (no HookContext).

    Resolved from ``<install_dir>/kimi`` when ``install_dir`` is given,
    otherwise via ``command -v kimi`` in a login shell (so worker-profile PATH
    additions apply). Raises when the binary is absent. This is the Stage-0
    resolver; the two-tier install cache (:func:`ensure_kimicode_installed`)
    lands in plan group 4 and supersedes this in ``session._prepare``. Mirrors
    grok's ``resolve_grok``.
    """
    if install_dir is not None:
        candidate = f"{install_dir.rstrip('/')}/kimi"
        probe = await host.run_command(
            f"[ -x {shlex.quote(candidate)} ] && echo OK || true"
        )
        if "OK" in (probe.stdout or ""):
            return candidate
        raise RuntimeError(
            f"kimi not present at {candidate!r} on host "
            f"(kimi_install_dir={install_dir!r})."
        )

    result = await host.run_command("bash -lc 'command -v kimi'")
    path = (result.stdout or "").strip()
    if result.exit_code == 0 and path:
        return path

    if not install_if_missing:
        raise RuntimeError(
            "kimi not found on host and install_if_missing=False; nothing to do."
        )
    raise RuntimeError(
        "kimi not found on the worker (looked via 'command -v kimi'). Stage 0 "
        "has no auto-install (the binary cache is a later stage) — install "
        "kimi manually or pass kimi_install_dir."
    )


# --- kimi web (server) launch ----------------------------------------------


def build_launch_env(
    workdir: str, extra_env: dict[str, str] | None = None,
) -> dict[str, str]:
    """Full environment for a kimi launch: the per-task isolation identity
    (:func:`_isolation_env`) + ``PATH`` (the per-task ``home/.local/bin``
    prepended ahead of the worker PATH) + caller extras.

    kimi serves its own web SPA (no tmux/bash wrapper needed — the server IS
    the launched process), so this is returned as a plain dict for
    ``host.launch_subprocess`` rather than baked into a shell string the way
    grok's ``_build_grok_shell_command`` must for tmux. ``PATH`` is layered
    here (not in the isolation SSOT) so the per-task ``kimi`` symlink resolves
    first.
    """
    iso = _isolation_env(workdir)
    home_local_bin = f"{iso['HOME']}/.local/bin"
    extra = dict(extra_env or {})
    base_path = extra.pop("PATH", None) or os.environ.get(
        "PATH", "/usr/local/bin:/usr/bin:/bin",
    )
    return {**iso, "PATH": f"{home_local_bin}:{base_path}", **extra}


def build_kimi_server_argv(
    kimi_path: str, *, bind_iface: str, port: int = 0,
) -> list[str]:
    """Argv for the foreground kimi web server.

    ``kimi server run --foreground`` is the non-opening form of ``kimi web``
    (``kimi web`` defaults ``--open`` true, which would spawn a browser ON THE
    WORKER — the operator opens the iframe instead). ``--foreground`` keeps the
    server in THIS process (attached, killable) rather than spawning a detached
    daemon and exiting, so the wrapper owns its lifecycle and can tear it down.
    ``--port 0`` binds an ephemeral port; the actual port is read back from the
    ready banner.
    """
    return [
        kimi_path, "server", "run", "--foreground",
        "--host", bind_iface, "--port", str(port),
    ]


async def launch_kimi_web(
    host: "Host",
    *,
    kimi_path: str,
    bind_iface: str,
    extra_env: dict[str, str] | None,
    env_remove: list[str] | None = None,
    ready_timeout_s: float = 30.0,
    port: int = 0,
) -> "tuple[ProcessHandle, int, str | None]":
    """Start ``kimi server run --foreground`` and wait for it to be ready.

    Returns ``(handle, port, token)`` where ``port`` is the actual listening
    port (read from the ready banner) and ``token`` is the bearer token from
    the banner's ``#token=`` fragment (``None`` when the server reported none).
    The caller establishes a tunnel to ``port`` and injects ``token`` into the
    iframe URL fragment.

    Readiness = the server printed its access banner, which kimi does only
    AFTER the socket is listening (mirrors grok reading ttyd's listening line).
    On timeout / early exit the server subprocess is killed before raising.
    """
    argv = build_kimi_server_argv(kimi_path, bind_iface=bind_iface, port=port)
    # ``exec`` so the launched /bin/sh is REPLACED by kimi (kimi becomes the
    # session leader in the launcher's process group — the pgid teardown
    # targets), matching grok's conversation launch rationale.
    cmd = "exec " + " ".join(shlex.quote(a) for a in argv)
    env = build_launch_env(host.workdir, extra_env)
    handle = await host.launch_subprocess(
        cmd, env=env, cwd=host.workdir, env_remove=env_remove,
    )

    async def _read_ready() -> "tuple[int, str | None]":
        async for raw in handle.stdout:
            line = (
                raw.decode("utf-8", errors="replace").rstrip()
                if isinstance(raw, bytes) else str(raw).rstrip()
            )
            m = _KIMI_READY_RE.search(line)
            if m:
                return int(m.group(1)), m.group(2)
        raise RuntimeError("kimi server exited before printing a ready banner")

    try:
        server_port, token = await asyncio.wait_for(
            _read_ready(), timeout=ready_timeout_s,
        )
    except asyncio.TimeoutError:
        await host.terminate_subprocess(handle, aggressive=True)
        raise TimeoutError(
            f"kimi server did not print a ready banner within {ready_timeout_s}s"
        )
    except BaseException:
        await host.terminate_subprocess(handle, aggressive=True)
        raise
    return handle, server_port, token


# --- resume-awareness PULL helpers (ported from opencode session.py) --------


async def rotate_optio_log(host: "Host") -> None:
    """Append the restored ``optio.log`` to ``optio.log.old``, then truncate it.

    Called on resume AFTER the snapshot restore repopulates the workdir (which
    carries the previous run's ``optio.log``) and BEFORE the protocol driver
    subscribes its ``tail -F -n +1``. Without this, the tail would re-emit every
    stale ``DELIVERABLE`` / ``DONE`` / ``ERROR`` line and the resumed session
    would terminate within seconds of launch. Historical content is preserved by
    appending it to ``optio.log.old`` rather than discarding it.

    Ported verbatim from ``optio_opencode.session._rotate_optio_log``.
    """
    workdir = host.workdir.rstrip("/")
    log_abs = f"{workdir}/optio.log"
    old_abs = f"{workdir}/optio.log.old"
    try:
        current = (await host.fetch_bytes_from_host(log_abs)).decode("utf-8")
    except FileNotFoundError:
        current = ""
    if not current:
        # Nothing to rotate. Still ensure optio.log exists empty so the tail
        # process has something to follow.
        await host.write_text("optio.log", "")
        return
    try:
        existing_old = (await host.fetch_bytes_from_host(old_abs)).decode("utf-8")
    except FileNotFoundError:
        existing_old = ""
    await host.write_text("optio.log.old", existing_old + current)
    await host.write_text("optio.log", "")


async def append_resume_log_entry(
    host: "Host", *, refreshed: "list[str] | None" = None,
) -> None:
    """Append one line to ``<workdir>/resume.log``.

    Line format: ``<ISO 8601 UTC timestamp>[ REFRESHED:<comma-separated names>]``.
    The optional ``REFRESHED:`` suffix signals that the harness rewrote the
    listed files on this session start; agents re-read tagged files (per the
    resume section of ``AGENTS.md``). Creates the file if missing (shell ``>>``).
    Caller gates this on ``config.supports_resume``.

    Ported verbatim from ``optio_opencode.session._append_resume_log_entry``.
    """
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    line = ts
    if refreshed:
        line = f"{ts} REFRESHED:{','.join(refreshed)}"
    target = f"{host.workdir}/resume.log"
    result = await host.run_command(
        f"echo {shlex.quote(line)} >> {shlex.quote(target)}"
    )
    if result.exit_code != 0:
        raise RuntimeError(
            f"failed to append to resume.log: exit {result.exit_code}: "
            f"{result.stderr!r}"
        )


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
