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

from optio_agents import claustrum

if TYPE_CHECKING:
    from optio_agents import HookContextProtocol
    from optio_host import Host
    from optio_host.host import ProcessHandle

    from .types import KimiCodeTaskConfig

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

# Official kimi vendor installer (confirmed live against the served install.sh).
# The script downloads the latest single-binary kimi-code release into
# ``$KIMI_INSTALL_DIR`` (default ``$HOME/.kimi-code``) and drops the executable at
# ``$KIMI_INSTALL_DIR/bin/kimi``. optio drives it with an explicit
# ``KIMI_INSTALL_DIR`` = a staging dir under the cache root (and ``HOME`` there
# too, and ``KIMI_NO_MODIFY_PATH=1`` so it never edits the operator's shell rc),
# then copies ``<staging>/bin/kimi`` to the stable cache path ``<cache_dir>/kimi``.
_KIMICODE_INSTALL_URL = "https://code.kimi.com/kimi-code/install.sh"

# Staging KIMI_INSTALL_DIR name (under the cache root) + the binary's path within
# it. Confirmed live: install.sh honours ``KIMI_INSTALL_DIR`` and places the
# binary at ``$KIMI_INSTALL_DIR/bin/kimi``. (The earlier ``.local/bin/kimi``
# assumption was WRONG — it was never exercised because Tier-1 silently adopted a
# name-colliding ``kimi`` off PATH; see ``_is_kimicode``.)
_KIMICODE_STAGING_DIRNAME = ".kimi-code"
_KIMICODE_INSTALL_REL = ".kimi-code/bin/kimi"

# claustrum: standalone Landlock filesystem-sandbox CLI, vendored by pinned tag
# (Stage 8). Bumping is deliberate. Mirrors optio-claudecode; kimi has no native
# fail-closed sandbox flag (unlike grok), so claustrum wraps the launch. The
# provisioning (detect arch, cross-compile on the engine, place + functionally
# validate) lives in the shared ``optio_agents.claustrum`` module; the pinned
# tag / repo constants come from there too.


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


async def _is_kimicode(host: "Host", path: str) -> bool:
    """True iff the binary at ``path`` is **kimi-code** (Moonshot's Node/SEA CLI),
    NOT the unrelated Python ``kimi-cli`` that shares the ``kimi`` command name.

    Both products install a binary called ``kimi``; a worker that has kimi-cli on
    PATH (e.g. a leftover ``uv tool install kimi-cli``) would otherwise be adopted
    by the Tier-1 install and every launch would fail — kimi-cli has no ``server``
    (nor the ``kimi web`` / REST surface) this wrapper drives. kimi-code ships the
    ``server run`` subcommand; kimi-cli answers "No such command 'server'". So
    ``kimi server run --help`` cleanly discriminates: it is local (no auth, no
    network, no tty), and exits 0 only on kimi-code. Used to (a) reject a
    name-colliding worker kimi in Tier-1 and (b) invalidate a cache that a prior
    run poisoned with the wrong binary.
    """
    # Bounded with ``timeout``: real kimi-code answers ``--help`` in well under a
    # second and exits. A binary that instead HANGS on the probe (e.g. a launcher
    # that starts serving on ``server run`` and ignores ``--help``) is, for our
    # purposes, not a usable kimi-code — the timeout makes it a clean False rather
    # than a hang.
    probe = await host.run_command(
        f"timeout 10 {shlex.quote(path)} server run --help >/dev/null 2>&1 "
        f"&& echo OK || true"
    )
    return "OK" in (probe.stdout or "")


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
    claustrum_wrap: list[str] | None = None,
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
    # targets), matching grok's conversation launch rationale. Under fs
    # isolation the claustrum wrap goes ahead of kimi: exec replaces /bin/sh
    # with claustrum, which applies Landlock then execve's kimi (so kimi still
    # becomes the session leader, and its tool subprocesses inherit the sandbox).
    cmd = build_wrapped_exec_cmd(argv, claustrum_wrap=claustrum_wrap)
    env = build_launch_env(host.workdir, extra_env)
    handle = await host.launch_subprocess(
        cmd, env=env, cwd=host.workdir, env_remove=env_remove,
    )

    # Accumulate what kimi actually prints (stderr is merged into stdout by
    # launch_subprocess) so an early exit is NOT a black box — its complaint is
    # surfaced in the raised error instead of the useless "exited before banner".
    seen: list[str] = []

    async def _read_ready() -> "tuple[int, str | None]":
        async for raw in handle.stdout:
            line = (
                raw.decode("utf-8", errors="replace").rstrip()
                if isinstance(raw, bytes) else str(raw).rstrip()
            )
            if line:
                seen.append(line)
            m = _KIMI_READY_RE.search(line)
            if m:
                return int(m.group(1)), m.group(2)
        rc = None
        try:
            rc = await asyncio.wait_for(handle.wait(), timeout=2.0)
        except (asyncio.TimeoutError, Exception):
            pass
        tail = "\n".join(seen[-40:]) or "(no output)"
        raise RuntimeError(
            f"kimi server exited before printing a ready banner "
            f"(exit {rc}). Output:\n{tail}"
        )

    try:
        server_port, token = await asyncio.wait_for(
            _read_ready(), timeout=ready_timeout_s,
        )
    except asyncio.TimeoutError:
        await host.terminate_subprocess(handle, aggressive=True)
        tail = "\n".join(seen[-40:]) or "(no output)"
        raise TimeoutError(
            f"kimi server did not print a ready banner within {ready_timeout_s}s. "
            f"Output:\n{tail}"
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
    hit = "OK" in (probe.stdout or "")
    # A cache HIT is only valid if the cached binary is really kimi-code. A prior
    # run may have poisoned the cache by seeding it from a name-colliding kimi-cli
    # (Tier-1 pre-fix); treat that as a miss and repopulate rather than return a
    # binary whose launch will fail with "kimi server exited before ready banner".
    if hit and not await _is_kimicode(host, cached):
        _LOG.warning(
            "ensure_kimicode_installed: cached %s is NOT kimi-code (kimi-cli "
            "name-collision) — discarding and repopulating", cached,
        )
        hit = False
    if hit:
        _LOG.info("ensure_kimicode_installed: cache HIT (%s)", cached)
    elif not install_if_missing:
        raise RuntimeError(
            f"no valid kimi-code binary in cache at {cached!r} and "
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
    # TIER 1 — a kimi already on the worker (login-shell PATH) — BUT only if it is
    # actually kimi-code. A name-colliding kimi-cli must NOT be adopted (its launch
    # has no ``server`` command); fall through to the vendor installer instead.
    source: "str | None"
    try:
        source = await resolve_kimi(host, install_dir=None, install_if_missing=False)
    except RuntimeError:
        source = None

    if source is not None and not await _is_kimicode(host, source):
        _LOG.warning(
            "worker kimi at %s is not kimi-code (kimi-cli name-collision) — "
            "vendor-installing kimi-code instead of seeding from it", source,
        )
        source = None

    if source is None:
        # No kimi-code on the worker — bootstrap via the vendor installer (TIER 2).
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

    Runs the official installer with an explicit ``KIMI_INSTALL_DIR`` =
    ``<cache_root>/.kimi-code`` (and ``HOME`` = the cache root, and
    ``KIMI_NO_MODIFY_PATH=1`` so it never edits a shell rc), so the single-binary
    release lands at ``<cache_root>/.kimi-code/bin/kimi`` — a persistent location
    OUTSIDE any task workdir and never the operator's ``~`` — then copies it to the
    stable cache path ``<cache_dir>/kimi``. install.sh honours ``KIMI_INSTALL_DIR``
    and drops the binary at ``$KIMI_INSTALL_DIR/bin/kimi`` (confirmed live).
    """
    cache_root = os.path.dirname(cache_dir.rstrip("/")) or "/"
    staging = f"{cache_root}/{_KIMICODE_STAGING_DIRNAME}"
    produced = f"{staging}/bin/kimi"
    installer = f"curl -fsSL {shlex.quote(_KIMICODE_INSTALL_URL)} | bash"
    cmd = (
        f"mkdir -p {shlex.quote(cache_root)} {shlex.quote(cache_dir)} && "
        f"env HOME={shlex.quote(cache_root)} "
        f"KIMI_INSTALL_DIR={shlex.quote(staging)} KIMI_NO_MODIFY_PATH=1 "
        f"sh -c {shlex.quote(installer)} && "
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


# --- Stage 8: claustrum filesystem isolation --------------------------------
#
# Ported from optio_claudecode.host_actions (the only existing claustrum impl).
# claustrum is a standalone Landlock sandbox CLI: it applies a fs allowlist to
# itself, then execve's the wrapped target, so kimi + every tool subprocess it
# spawns inherit the confinement. Default-on, fail-closed (provisioning raises
# rather than launching unconfined), local and remote (Host primitives only).


def build_wrapped_exec_cmd(
    argv: list[str], *, claustrum_wrap: list[str] | None = None,
) -> str:
    """Compose the ``exec``-prefixed shell command for a launch, optionally
    confined by a claustrum wrap prepended ahead of ``argv``.

    Unlike claudecode there is no pasta/network-namespace bash hop (kimicode
    has no netns): ``exec`` replaces /bin/sh directly with claustrum (or, when
    unconfined, with kimi), preserving session-leader/pgid teardown semantics.
    """
    full = [*(claustrum_wrap or []), *argv]
    return "exec " + " ".join(shlex.quote(a) for a in full)


async def ensure_claustrum_installed(
    hook_ctx: "HookContextProtocol",
    *,
    install_dir: str | None = None,
) -> str:
    """Ensure a claustrum binary (pinned tag, host arch) is on the host.

    Thin wrapper-specific shim over :func:`optio_agents.claustrum.ensure_claustrum_installed`
    (the shared provisioner: detect arch, cross-compile on the engine cached by
    (tag, arch), place on the target host, and FUNCTIONALLY validate). This layer
    only resolves the two wrapper-specific paths:

    - the TARGET-host cache dir (:func:`_resolve_kimicode_cache_dir`), beside the
      kimi binary cache and outside every task workdir; and
    - the ENGINE-local build cache root ``~/.cache/optio-kimicode`` (a parameter of
      the shared function, never hardcoded inside it, so tests isolate it).

    Returns the claustrum path on the target host. Any failure RAISES (fail-closed):
    an fs-isolated session never launches unconfined.
    """
    host = hook_ctx._host
    cache_dir = await _resolve_kimicode_cache_dir(host, install_dir)
    return await claustrum.ensure_claustrum_installed(
        host,
        cache_dir=cache_dir,
        engine_cache_dir=os.path.expanduser("~/.cache/optio-kimicode"),
        report_progress=hook_ctx.report_progress,
    )


async def _build_claustrum_wrap(
    host: "Host", config: "KimiCodeTaskConfig", claustrum_path: str | None,
) -> list[str] | None:
    """claustrum argv prefix for an fs-isolated launch, or None when
    ``fs_isolation`` is off. Shared by the iframe (kimi web) and conversation
    (kimi acp) launch paths. Host-type agnostic (workdir + generic primitives
    only), so the wrap is identical local and remote."""
    if not config.fs_isolation:
        return None
    from . import fs_allowlist

    cache_dir = await _resolve_kimicode_cache_dir(host, config.kimi_install_dir)
    # ``~/`` caller extras expand against the REAL host home (the kimi process
    # runs under an isolated $HOME, and grants reach claustrum verbatim).
    host_home = (
        await host.resolve_host_home() if config.extra_allowed_dirs else None
    )
    grants = fs_allowlist.build_grant_flags(
        workdir=host.workdir,
        kimi_cache_dir=cache_dir,
        extra_allowed_dirs=config.extra_allowed_dirs,
        host_home=host_home,
    )
    return [claustrum_path, "--best-effort", "--abi-min", "1", *grants, "--"]


async def claustrum_newer_tag() -> str | None:
    """Return the newest claustrum tag if it is newer than the pinned one, else None.

    Engine-side egress only. Best-effort: network failure returns None.
    """
    try:
        p = await asyncio.create_subprocess_exec(
            "git", "ls-remote", "--tags", "--refs", claustrum.CLAUSTRUM_REPO,
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL,
        )
        out, _ = await p.communicate()
        if p.returncode != 0:
            return None
    except Exception:  # noqa: BLE001
        return None
    tags = []
    for line in out.decode().splitlines():
        ref = line.rsplit("/", 1)[-1].strip()
        if ref.startswith("v"):
            tags.append(ref)

    def key(t: str) -> tuple:
        return tuple(int(x) for x in t.lstrip("v").split(".") if x.isdigit())

    if not tags:
        return None
    newest = max(tags, key=key)
    return newest if key(newest) > key(claustrum.CLAUSTRUM_PINNED_TAG) else None
