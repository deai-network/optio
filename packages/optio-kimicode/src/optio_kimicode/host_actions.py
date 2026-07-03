"""Kimi-specific actions over a generic Host.

Free functions; each takes a Host or HookContext and uses only generic
primitives (run_command, resolve_host_home, etc.). No isinstance branches.

Ported from ``optio_grok.host_actions``. Carries the host constructor
(:func:`build_host`), the per-task isolation identity (:func:`_isolation_env`),
and the two-tier binary install (:func:`ensure_kimicode_installed`): reuse a
worker ``kimi`` on the login-shell PATH, else run the vendor installer, seeding
an evictable cache outside the workdir and symlinking it into
``<workdir>/home/.local/bin/kimi`` (idempotently re-linked after a resume).
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


# The optio-owned kimi binary cache lives on the WORKER, outside every task
# workdir and never the operator's autoupdating ``~/.kimi-code``. Default:
# ``${XDG_CACHE_HOME:-$HOME/.cache}/optio-kimicode/bin``; ``OPTIO_KIMICODE_CACHE_DIR``
# overrides. Resolved via a shell echo so RemoteHost gets the remote location and
# the cache stays shared + evictable — it lives OUTSIDE any task workdir, so it is
# never captured by the resume snapshot (``Host.archive_workdir`` = ``cd
# host.workdir && tar czf - .``) and survives task teardown. Mirrors grok's
# ``_GROK_CACHE_DIR_SHELL_DEFAULT`` (using the optio-prefixed override name, as
# claudecode does).
_KIMICODE_CACHE_DIR_SHELL_DEFAULT = (
    "${OPTIO_KIMICODE_CACHE_DIR:-${XDG_CACHE_HOME:-$HOME/.cache}/optio-kimicode/bin}"
)

# Official kimi vendor installer (confirmed against ``.kimi-src/kimi-code``:
# README.md + docs/en/guides/getting-started.md). The script downloads the latest
# single-binary release, verifies its checksum, and "places the ``kimi``
# executable on your PATH" — under ``$HOME/.local/bin`` (the docs' IDE examples,
# docs/en/guides/ides.md, show ``~/.local/bin/kimi``). optio drives it with
# ``HOME`` = a staging root under the cache so the binary lands in
# ``<home>/.local/bin/kimi`` — never the operator's real ``~`` and never a task
# workdir — then copies it to the stable cache path ``<cache_dir>/kimi``.
_KIMICODE_INSTALL_URL = "https://code.kimi.com/kimi-code/install.sh"

# Where the vendor install.sh drops the binary, relative to the ``HOME`` it runs
# under. NOTE (tracked real-binary follow-up, plan group 6 / row 30): the
# install.sh is fetched over the network and is not vendored, so only its
# documented behaviour is available offline. ``test_install.py``'s opt-in
# ``test_real_vendor_install_lands_runnable_kimi`` confirms this layout live.
_KIMICODE_INSTALL_REL = ".local/bin/kimi"


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


async def _resolve_kimicode_cache_dir(host: "Host", override: str | None) -> str:
    """Resolve the optio-owned kimi binary-cache dir as an absolute worker path.

    ``override`` (``config.kimi_install_dir``) wins. Otherwise the worker's real
    env decides via a shell echo: ``OPTIO_KIMICODE_CACHE_DIR`` else
    ``${XDG_CACHE_HOME:-$HOME/.cache}/optio-kimicode/bin`` — resolved on the host
    so RemoteHost gets the remote location. Deliberately mirrors grok's
    ``_resolve_grok_cache_dir``. The result lives OUTSIDE any task workdir, so the
    binary it points at is never captured by the resume snapshot.
    """
    if override is not None:
        return override.rstrip("/")
    r = await host.run_command(f'printf %s "{_KIMICODE_CACHE_DIR_SHELL_DEFAULT}"')
    path = (r.stdout or "").strip()
    if r.exit_code != 0 or not path:
        raise RuntimeError(
            f"failed to resolve kimi cache dir on host "
            f"(exit {r.exit_code}): {(r.stderr or '').strip()[:200]}"
        )
    return path.rstrip("/")


async def ensure_kimicode_installed(
    host: "Host",
    workdir: str | None = None,
    *,
    install_dir: str | None = None,
    install_if_missing: bool = True,
) -> str:
    """Provision ``kimi`` for this task from the optio-owned binary cache.

    The cache dir (:func:`_resolve_kimicode_cache_dir`) lives on the worker
    OUTSIDE any task workdir and never the operator's autoupdating
    ``~/.kimi-code`` — so it stays shared, evictable, and unsnapshotted (it
    survives task teardown; ``Host.archive_workdir`` only tars ``host.workdir``).
    The cache is the single stable home of the binary; the value RETURNED is the
    per-task launch path ``<workdir>/home/.local/bin/kimi`` — a symlink into the
    cache, on the launch PATH (mirrors grok's ``home/.local/bin/grok``).

    ``workdir`` defaults to ``host.workdir`` (the launch home is
    ``<workdir>/home``); it is accepted explicitly to match the Task 4.1 contract.

    Cache population (only on a miss, and only when ``install_if_missing``) is
    two-tier (:func:`_populate_kimicode_cache`):

    - **TIER 1 — seed from a worker kimi**: if a ``kimi`` is already on the worker
      login-shell PATH (:func:`resolve_kimi`) copy it into the cache (deref +
      chmod +x); fast, no download, matches the operator's version.
    - **TIER 2 — vendor auto-install**: otherwise run kimi's official installer
      (:data:`_KIMICODE_INSTALL_URL`) into the persistent cache, so a fresh or
      remote worker with no worker kimi still bootstraps itself.

    Host-based (no HookContext) — symmetric with :func:`resolve_kimi` and usable
    from the engine-free ``verify`` path. Raises only when the cache is empty AND
    ``install_if_missing=False``. Idempotent on a re-call (cache hit → it just
    re-links the task path), which is how resume re-establishes the launch symlink
    after ``restore_snapshot`` wipes it.
    """
    workdir = (workdir if workdir is not None else host.workdir).rstrip("/")
    cache_dir = await _resolve_kimicode_cache_dir(host, install_dir)
    cached = f"{cache_dir}/kimi"

    probe = await host.run_command(
        f"[ -x {shlex.quote(cached)} ] && echo OK || true"
    )
    if "OK" in (probe.stdout or ""):
        _LOG.info("ensure_kimicode_installed: cache HIT (%s)", cached)
    elif not install_if_missing:
        raise RuntimeError(
            f"kimi not present in cache at {cached!r} and "
            f"install_if_missing=False; nothing to do."
        )
    else:
        await _populate_kimicode_cache(host, cache_dir=cache_dir, cached=cached)

    return await _link_kimicode_into_task(host, workdir, cached)


async def _populate_kimicode_cache(
    host: "Host", *, cache_dir: str, cached: str,
) -> None:
    """Fill an empty cache: prefer seeding from a pre-existing worker kimi (TIER
    1 — fast, no download); fall back to the vendor auto-installer when the worker
    has none (TIER 2). Leaves an executable ``<cache_dir>/kimi`` on success;
    raises otherwise. Mirrors grok's ``_populate_grok_cache``.
    """
    # TIER 1 — a kimi already on the worker (login-shell PATH).
    source: "str | None"
    try:
        source = await resolve_kimi(host, install_dir=None, install_if_missing=False)
    except RuntimeError:
        source = None

    if source is None:
        # No worker kimi anywhere — bootstrap via the vendor installer (TIER 2).
        await _install_kimicode_into_cache(host, cache_dir=cache_dir, cached=cached)
        _LOG.info("ensure_kimicode_installed: cache MISS -> vendor-installed into %s", cached)
        return

    mk = await host.run_command(f"mkdir -p {shlex.quote(cache_dir)}")
    if mk.exit_code != 0:
        raise RuntimeError(
            f"mkdir -p {cache_dir!r} failed (exit {mk.exit_code}): "
            f"{(mk.stderr or '').strip()[:200]}"
        )
    # ``-L`` dereferences: a symlinked worker kimi becomes a real, stable copy in
    # the cache (independent of the worker binary the operator may autoupdate).
    cp = await host.run_command(
        f"cp -L {shlex.quote(source)} {shlex.quote(cached)}"
    )
    if cp.exit_code != 0:
        raise RuntimeError(
            f"seeding kimi cache (cp {source!r} -> {cached!r}) failed "
            f"(exit {cp.exit_code}): {(cp.stderr or '').strip()[:200]}"
        )
    ch = await host.run_command(f"chmod +x {shlex.quote(cached)}")
    if ch.exit_code != 0:
        raise RuntimeError(
            f"chmod +x {cached!r} failed (exit {ch.exit_code}): "
            f"{(ch.stderr or '').strip()[:200]}"
        )
    verify = await host.run_command(
        f"[ -x {shlex.quote(cached)} ] && echo OK || true"
    )
    if "OK" not in (verify.stdout or ""):
        raise RuntimeError(
            f"kimi cache seed completed but {cached!r} is still not executable "
            f"on the host. Check the seed source {source!r} and chmod result."
        )
    _LOG.info("ensure_kimicode_installed: cache MISS -> seeded from worker %s", source)


async def _install_kimicode_into_cache(
    host: "Host", *, cache_dir: str, cached: str,
) -> None:
    """Vendor auto-install kimi into the persistent cache (TIER 2: miss + no
    worker kimi).

    Runs the official installer with ``HOME`` = the cache ROOT (``dirname`` of
    ``cache_dir``) so the single-binary release lands at
    ``<cache_root>/.local/bin/kimi`` — a persistent location OUTSIDE any task
    workdir and never the operator's ``~`` — then copies it to the stable cache
    path ``<cache_dir>/kimi``. HOME is the cache root (never the real ``~``), so
    nothing touches the operator's ``~/.kimi-code`` or ``~/.local``. Unlike grok
    (whose installer honours ``GROK_BIN_DIR``) kimi's install.sh exposes no
    bin-dir override, so the produced binary is located under the staging HOME and
    copied into place.
    """
    cache_root = os.path.dirname(cache_dir.rstrip("/")) or "/"
    produced = f"{cache_root}/{_KIMICODE_INSTALL_REL}"
    installer = f"curl -fsSL {shlex.quote(_KIMICODE_INSTALL_URL)} | bash"
    cmd = (
        f"mkdir -p {shlex.quote(cache_root)} {shlex.quote(cache_dir)} && "
        f"env HOME={shlex.quote(cache_root)} sh -c {shlex.quote(installer)} && "
        f"cp -L {shlex.quote(produced)} {shlex.quote(cached)} && "
        f"chmod +x {shlex.quote(cached)}"
    )
    result = await host.run_command(cmd)
    if result.exit_code != 0:
        raise RuntimeError(
            f"kimi vendor auto-install failed on host (exit {result.exit_code}): "
            f"{(result.stderr or '').strip()[:300]}"
        )
    verify = await host.run_command(
        f"[ -x {shlex.quote(cached)} ] && echo OK || true"
    )
    if "OK" not in (verify.stdout or ""):
        raise RuntimeError(
            f"kimi vendor install reported success but {cached!r} is still not "
            f"executable. The installer may place the binary somewhere other than "
            f"{produced!r}; inspect the installer output and the cache {cache_dir!r}."
        )


async def _link_kimicode_into_task(host: "Host", workdir: str, cached: str) -> str:
    """Symlink the cached kimi binary into the task's isolated home launch dir.

    The cache lives OUTSIDE the workdir (persists across task teardown); the
    launch path ``<workdir>/home/.local/bin/kimi`` is a per-task symlink to it,
    ahead on the launch PATH (:func:`build_launch_env`). Returns that task path.
    ``ln -sfn`` is idempotent — a resume re-call just refreshes the symlink on the
    restored tree. Mirrors grok's ``_link_grok_into_task``.
    """
    workdir = workdir.rstrip("/")
    bin_dir = f"{workdir}/home/.local/bin"
    task_kimi = f"{bin_dir}/kimi"
    r = await host.run_command(
        f"mkdir -p {shlex.quote(bin_dir)} && "
        f"ln -sfn {shlex.quote(cached)} {shlex.quote(task_kimi)}"
    )
    if r.exit_code != 0:
        raise RuntimeError(
            f"linking kimi into the task path ({task_kimi!r} -> {cached!r}) "
            f"failed (exit {r.exit_code}): {(r.stderr or '').strip()[:200]}"
        )
    return task_kimi
