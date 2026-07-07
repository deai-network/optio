"""Cursor-specific actions over a generic Host.

Free functions; each takes a Host or HookContext and uses only generic
primitives (run_command, resolve_host_home, etc.). No isinstance branches.

Adapted from optio-grok's ``host_actions`` (itself lifted from
optio-claudecode). Stage 2 adds the resume bookkeeping; Stage 5 adds the
optio-owned cursor-agent binary cache (grok's, plus a vendor-installer
population branch and version-dir copy semantics). The tmux/ttyd machinery
and the ttyd installer are copied with only ``grok`` → ``cursor`` renames.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import re
import shlex
from datetime import datetime, timezone
from typing import TYPE_CHECKING

from optio_agents import RESUME_NOTICE, SYSTEM_MESSAGE_PREFIX, claustrum
from optio_agents import tmux_input as _tmux_input
from optio_host.host import proc_wait

if TYPE_CHECKING:
    from optio_agents import HookContextProtocol
    from optio_host import Host
    from optio_host.host import ProcessHandle

_LOG = logging.getLogger(__name__)


# ttyd's ready banner takes a few forms across versions:
#   * 1.7.x with lws logging:  "N:  Listening on port: 33449"
#   * older builds:            "Listening on port 7681"
#   * some forks log a URL:    "[INFO] listening on http://127.0.0.1:7681/"
_TTYD_READY_RE = re.compile(
    r"(?:port[\s:]+(\d+))|(?:http://[^\s]+?:(\d+)(?:/|\s|$))"
)


# Settle (seconds) between pasting a message into the cursor TUI and sending
# Enter. Without it the Enter is glued to the paste and cursor treats the CR
# as a newline inside the input box instead of a submit (see
# send_text_to_cursor). A shell-literal string (used in a `sleep` invocation).
_SUBMIT_SETTLE_S = "1.0"

# Pinned ttyd version. Update with care; the URL pattern below is
# tsl0922/ttyd's release-asset convention as of 1.7.x.
_TTYD_VERSION = "1.7.7"
_TTYD_RELEASE_BASE = (
    f"https://github.com/tsl0922/ttyd/releases/download/{_TTYD_VERSION}"
)

# ttyd installs into the worker home's ``.local/bin``.
_DEFAULT_INSTALL_SUBDIR = ".local/bin"


# The optio-owned cursor-agent binary cache lives on the WORKER, outside every
# task workdir and never the operator's autoupdating
# ``~/.local/share/cursor-agent``. Default:
# ``${XDG_CACHE_HOME:-$HOME/.cache}/optio-cursor``; ``CURSOR_CACHE_DIR``
# overrides. Resolved via a shell echo so RemoteHost gets the remote location,
# and so the cache stays shared + evictable (never snapshotted, re-populated
# on a miss). No ``/bin`` suffix (unlike grok): the cache root carries a whole
# ``versions/<v>/`` Node dist tree, not a single binary.
_CURSOR_CACHE_DIR_SHELL_DEFAULT = (
    "${CURSOR_CACHE_DIR:-${XDG_CACHE_HOME:-$HOME/.cache}/optio-cursor}"
)

# Confirmed vendor bootstrap installer (``curl https://cursor.com/install
# -fsS | bash``); installs under ``$HOME/.local/{bin,share/cursor-agent}``.
_CURSOR_INSTALL_URL = "https://cursor.com/install"


async def _resolve_install_dir(host: "Host", install_dir: str | None) -> str:
    """Return ``install_dir`` if given, else ``<host_home>/.local/bin`` (ttyd)."""
    if install_dir is not None:
        return install_dir
    host_home = await host.resolve_host_home()
    return f"{host_home}/{_DEFAULT_INSTALL_SUBDIR}"


async def _resolve_cursor_cache_dir(host: "Host", override: str | None) -> str:
    """Resolve the optio-owned cursor binary-cache dir as an absolute worker path.

    ``override`` (``config.install_dir``) wins. Otherwise the worker's
    REAL env decides via a shell echo: ``CURSOR_CACHE_DIR`` else
    ``${XDG_CACHE_HOME:-$HOME/.cache}/optio-cursor`` — resolved on the host so
    RemoteHost gets the remote location. Never under a task workdir (so it is
    never snapshotted) and never the operator's ``~/.local/share/cursor-agent``.
    Mirrors grok's ``_resolve_grok_cache_dir`` (the ttyd ``_resolve_install_dir``
    is a separate, home-relative resolver and is intentionally left untouched).
    """
    if override is not None:
        return override.rstrip("/")
    r = await host.run_command(f'printf %s "{_CURSOR_CACHE_DIR_SHELL_DEFAULT}"')
    path = (r.stdout or "").strip()
    if r.exit_code != 0 or not path:
        raise RuntimeError(
            f"failed to resolve cursor cache dir on host "
            f"(exit {r.exit_code}): {(r.stderr or '').strip()[:200]}"
        )
    return path.rstrip("/")


# --- claustrum provisioning (Stage 8) ---------------------------------------
#
# The provisioning logic (engine cross-compile, ELF-guarded build cache,
# functional wrap+exec validation, fail-closed placement) is the shared
# ``optio_agents.claustrum`` module — the single source of truth across every
# wrapper. This wrapper contributes only the cursor-owned cache-dir resolution
# and, in session.py, the grant set (``_build_claustrum_wrap``). Cursor uses
# claustrum — not its own native sandbox — because the native one wraps
# individual shell-exec commands only, leaving cursor-agent's in-process
# Write/Edit tools unconfined; claustrum Landlock-confines the WHOLE process
# tree. See fs_allowlist.py for the probe decision.


async def ensure_claustrum_installed(
    hook_ctx: "HookContextProtocol",
    *,
    install_dir: str | None = None,
) -> str:
    """Ensure a functioning claustrum binary is on the host; return its path.

    Thin wrapper over :func:`optio_agents.claustrum.ensure_claustrum_installed`:
    resolves the cursor-owned target cache dir, pins the engine build cache to
    ``~/.cache/optio-cursor``, and forwards the UI progress callback. All the
    real work (cross-compile, ELF-guarded engine cache, functional wrap+exec
    validation) lives in the shared module. Fail-closed — any failure RAISES, so
    the caller never proceeds to an unconfined launch.
    """
    host = hook_ctx._host
    cache_dir = await _resolve_cursor_cache_dir(host, install_dir)
    return await claustrum.ensure_claustrum_installed(
        host,
        cache_dir=cache_dir,
        engine_cache_dir=os.path.expanduser("~/.cache/optio-cursor"),
        report_progress=hook_ctx.report_progress,
    )


# --- cursor-agent resolution + optio-owned binary cache (Stage 5) -----------


async def resolve_cursor(
    host: "Host",
    *,
    install_dir: str | None = None,
    install_if_missing: bool = True,
) -> str:
    """Host-based ``cursor-agent`` binary resolution (no HookContext).

    Resolved from ``<install_dir>/cursor-agent`` when ``install_dir`` is
    given, otherwise via ``command -v cursor-agent`` in a login shell (so
    worker-profile PATH additions apply, e.g. ``~/.local/bin``). Raises when
    the binary is absent. NEVER resolves the ``cursor`` IDE binary — the
    agent CLI is ``cursor-agent``. Shared by ``ensure_cursor_installed``
    (host-copy cache population) and ``verify`` (engine-free path).
    """
    if install_dir is not None:
        candidate = f"{install_dir.rstrip('/')}/cursor-agent"
        probe = await host.run_command(
            f"[ -x {shlex.quote(candidate)} ] && echo OK || true"
        )
        if "OK" in (probe.stdout or ""):
            return candidate
        raise RuntimeError(
            f"cursor-agent not present at {candidate!r} on host "
            f"(install_dir={install_dir!r})."
        )

    result = await host.run_command("bash -lc 'command -v cursor-agent'")
    path = (result.stdout or "").strip()
    if result.exit_code == 0 and path:
        return path

    if not install_if_missing:
        raise RuntimeError(
            "cursor-agent not found on host and install_if_missing=False; "
            "nothing to do."
        )
    raise RuntimeError(
        "cursor-agent not found on the worker (looked via 'command -v "
        "cursor-agent'). Install cursor-agent manually (e.g. `curl "
        "https://cursor.com/install -fsS | bash` puts it in ~/.local/bin) "
        "or pass install_dir."
    )


def _vendor_install_command(cache_dir: str) -> str:
    """Shell command running the confirmed vendor installer into a staging
    HOME under the cache (``<cache>/staging``), so the install lands in the
    cache's own tree — never the operator's ``~/.local``. Unit-tested for
    construction only; tests never run it (network)."""
    staging = f"{cache_dir.rstrip('/')}/staging"
    installer = f"curl {_CURSOR_INSTALL_URL} -fsS | bash"
    return (
        f"mkdir -p {shlex.quote(staging)} && "
        f"env HOME={shlex.quote(staging)} bash -c {shlex.quote(installer)}"
    )


async def _vendor_install_cursor(host: "Host", cache_dir: str) -> str | None:
    """Populate the cache via the vendor installer. Returns the cached
    entrypoint, or None when the installer fails (offline worker, vendor
    outage) so the caller can fall back to the host-copy route.

    The installer (run with ``HOME=<cache>/staging``) produces
    ``staging/.local/share/cursor-agent/versions/<v>/`` plus a
    ``staging/.local/bin/cursor-agent`` symlink. The version dir is moved to
    ``<cache>/versions/<v>`` and the entry symlink re-pointed (relative) at
    ``versions/<v>/cursor-agent``; the staging tree is then removed."""
    cache = cache_dir.rstrip("/")
    r = await host.run_command(_vendor_install_command(cache))
    if r.exit_code != 0:
        _LOG.info(
            "vendor cursor installer failed (exit %s): %s — falling back to "
            "host copy", r.exit_code, (r.stderr or "").strip()[:200],
        )
        return None
    adopt = (
        "set -e; "
        f"cache={shlex.quote(cache)}; "
        'entry="$(readlink -f "$cache/staging/.local/bin/cursor-agent")"; '
        'ver_dir="$(dirname "$entry")"; ver="$(basename "$ver_dir")"; '
        'mkdir -p "$cache/versions"; rm -rf "$cache/versions/$ver"; '
        'mv "$ver_dir" "$cache/versions/$ver"; '
        'ln -sfn "versions/$ver/cursor-agent" "$cache/cursor-agent"; '
        'rm -rf "$cache/staging"'
    )
    r2 = await host.run_command(f"bash -c {shlex.quote(adopt)}")
    if r2.exit_code != 0:
        _LOG.warning(
            "vendor cursor install succeeded but adopting the staged tree "
            "into the cache failed (exit %s): %s",
            r2.exit_code, (r2.stderr or "").strip()[:200],
        )
        return None
    cached = f"{cache}/cursor-agent"
    verify = await host.run_command(
        f"[ -x {shlex.quote(cached)} ] && echo OK || true"
    )
    return cached if "OK" in (verify.stdout or "") else None


async def _seed_cache_from_host(host: "Host", cache_dir: str, source: str) -> str:
    """Copy the host install into the cache and return the cached entrypoint.

    ``cursor-agent`` is not a single file: the host binary is a symlink into a
    Node dist dir (``.../cursor-agent/versions/<v>/``). When ``source``
    resolves into such a ``versions/<v>`` dir, the WHOLE dir is copied
    (``cp -a``) to ``<cache>/versions/<v>`` and ``<cache>/cursor-agent``
    becomes a relative symlink to its entrypoint — copying only the symlink
    target's file is NOT sufficient. A plain single-file install (e.g. a test
    shim) degrades to grok's deref copy (``cp -L``)."""
    import posixpath

    cache = cache_dir.rstrip("/")
    cached = f"{cache}/cursor-agent"

    rl = await host.run_command(f"readlink -f {shlex.quote(source)}")
    real = (rl.stdout or "").strip()
    if rl.exit_code != 0 or not real:
        raise RuntimeError(
            f"readlink -f {source!r} failed (exit {rl.exit_code}): "
            f"{(rl.stderr or '').strip()[:200]}"
        )

    version_dir = posixpath.dirname(real)
    if posixpath.basename(posixpath.dirname(version_dir)) == "versions":
        ver = posixpath.basename(version_dir)
        entry_rel = f"versions/{ver}/cursor-agent"
        cmd = (
            f"mkdir -p {shlex.quote(f'{cache}/versions')} && "
            f"rm -rf {shlex.quote(f'{cache}/versions/{ver}')} && "
            f"cp -a {shlex.quote(version_dir)} "
            f"{shlex.quote(f'{cache}/versions/{ver}')} && "
            f"ln -sfn {shlex.quote(entry_rel)} {shlex.quote(cached)}"
        )
    else:
        # Not a versions/<v> layout — a real, standalone binary. ``-L``
        # dereferences so the cache holds a stable copy independent of the
        # host binary the operator may autoupdate.
        cmd = (
            f"mkdir -p {shlex.quote(cache)} && "
            f"cp -L {shlex.quote(source)} {shlex.quote(cached)} && "
            f"chmod +x {shlex.quote(cached)}"
        )
    cp = await host.run_command(cmd)
    if cp.exit_code != 0:
        raise RuntimeError(
            f"seeding cursor cache ({source!r} -> {cached!r}) failed "
            f"(exit {cp.exit_code}): {(cp.stderr or '').strip()[:200]}"
        )
    verify = await host.run_command(
        f"[ -x {shlex.quote(cached)} ] && echo OK || true"
    )
    if "OK" not in (verify.stdout or ""):
        raise RuntimeError(
            f"cursor cache seed completed but {cached!r} is still not "
            f"executable on the host. Check the seed source {source!r}."
        )
    return cached


async def ensure_cursor_installed(
    hook_ctx: "HookContextProtocol",
    *,
    install_if_missing: bool = True,
    install_dir: str | None = None,
    progress_label: str = "Locating cursor-agent…",
) -> str:
    """Provision ``cursor-agent`` for this task from the optio-owned binary cache.

    The cache dir (``_resolve_cursor_cache_dir``) lives on the worker outside
    any task workdir and never the operator's autoupdating
    ``~/.local/share/cursor-agent`` — so it stays shared, evictable, and
    unsnapshotted (re-populated on a miss after eviction). This makes the cache
    the single stable home of the binary; the value RETURNED is the per-task
    launch path ``<workdir>/home/.local/bin/cursor-agent`` — a symlink into the
    cache, on the launch PATH (mirrors grok's ``home/.local/bin/grok`` and
    claudecode's ``home/.local/bin/claude``). Population order:

    1. **cache hit** — ``<cache>/cursor-agent`` is already executable →
       link it into the task path (the stable path every task launches,
       decoupled from the host's autoupdater).
    2. **vendor installer** — cursor HAS a confirmed bootstrap installer
       (unlike grok): run it with ``HOME=<cache>/staging`` and adopt the
       installed ``versions/<v>`` tree into the cache
       (:func:`_vendor_install_cursor`; skipped silently on failure, e.g. no
       network).
    3. **host copy** — copy the resolved host install's whole version dir
       into the cache (:func:`_seed_cache_from_host`).
    4. Nothing worked → raise naming both failed routes. With
       ``install_if_missing=False`` a cache miss raises immediately.

    Uses only generic Host primitives. Idempotent on a re-call (cache hit → it
    just re-links the task path), which is how resume re-establishes the launch
    symlink after ``restore_workdir`` wipes it.
    """
    host = hook_ctx._host
    hook_ctx.report_progress(None, progress_label)

    cache_dir = await _resolve_cursor_cache_dir(host, install_dir)
    cached = f"{cache_dir}/cursor-agent"

    probe = await host.run_command(
        f"[ -x {shlex.quote(cached)} ] && echo OK || true"
    )
    if "OK" in (probe.stdout or ""):
        _LOG.info("ensure_cursor_installed: cache HIT (%s)", cached)
        return await _link_cursor_into_task(host, cached)

    if not install_if_missing:
        raise RuntimeError(
            f"cursor-agent not present in cache at {cached!r} and "
            f"install_if_missing=False; nothing to do."
        )

    hook_ctx.report_progress(None, "Installing cursor-agent (vendor installer)…")
    vendor = await _vendor_install_cursor(host, cache_dir)
    if vendor is not None:
        _LOG.info("ensure_cursor_installed: cache MISS -> vendor install (%s)", vendor)
        return await _link_cursor_into_task(host, vendor)

    # Vendor route unavailable — fall back to copying the host install.
    try:
        source = await resolve_cursor(
            host, install_dir=None, install_if_missing=False,
        )
    except RuntimeError as exc:
        raise RuntimeError(
            f"no cursor-agent available to populate the optio cache "
            f"(cache_dir={cache_dir!r}): the vendor installer "
            f"({_CURSOR_INSTALL_URL}) did not produce a binary and no host "
            f"cursor-agent is on the worker PATH. Install cursor-agent on "
            f"the worker (`curl {_CURSOR_INSTALL_URL} -fsS | bash`) or pass "
            f"install_dir at a pre-populated cache."
        ) from exc

    hook_ctx.report_progress(None, "Seeding cursor-agent cache from host install…")
    cached = await _seed_cache_from_host(host, cache_dir, source)
    _LOG.info("ensure_cursor_installed: cache MISS -> seeded from %s", source)
    return await _link_cursor_into_task(host, cached)


async def _link_cursor_into_task(host: "Host", cached: str) -> str:
    """Symlink the cached cursor-agent entrypoint into the task's isolated home
    launch dir.

    The cache lives OUTSIDE the workdir (persists across task teardown); the
    launch path ``<workdir>/home/.local/bin/cursor-agent`` is a per-task symlink
    to it, ahead on the launch PATH. Returns that task path. ``ln -sfn`` is
    idempotent — a resume re-call just refreshes the symlink on the restored
    tree. ``cached`` is itself the ``<cache>/cursor-agent`` symlink into the
    Node ``versions/<v>/`` dist tree, so the launch resolves task → cache →
    version-dir entrypoint; the whole chain stays inside the rox-granted cache
    (see fs_allowlist.build_grant_flags). Mirrors grok's ``_link_grok_into_task``.
    """
    workdir = host.workdir.rstrip("/")
    bin_dir = f"{workdir}/home/.local/bin"
    task_cursor = f"{bin_dir}/cursor-agent"
    r = await host.run_command(
        f"mkdir -p {shlex.quote(bin_dir)} && "
        f"ln -sfn {shlex.quote(cached)} {shlex.quote(task_cursor)}"
    )
    if r.exit_code != 0:
        raise RuntimeError(
            f"linking cursor-agent into the task path ({task_cursor!r} -> "
            f"{cached!r}) failed (exit {r.exit_code}): "
            f"{(r.stderr or '').strip()[:200]}"
        )
    return task_cursor


def build_host(ssh, taskdir: str) -> "Host":
    """ssh_config + taskdir -> LocalHost/RemoteHost. Lifted from
    session._build_host so engine-free callers (verify) share it (mirrors
    grok's host_actions.build_host)."""
    from optio_host.host import LocalHost, RemoteHost

    if ssh is None:
        os.makedirs(taskdir, exist_ok=True)
        host: "Host" = LocalHost(taskdir=taskdir)
        os.makedirs(host.workdir, exist_ok=True)
        return host
    return RemoteHost(ssh_config=ssh, taskdir=taskdir)


# Cursor's login refuses to open a browser — falling back to printing the auth
# URL — when its ``isSSH()`` heuristic fires (open-browser.ts). That heuristic
# checks these env vars; when the optio worker is reached over SSH they are
# inherited by the launch and cursor never spawns ``xdg-open``, so the redirect
# browser-shim can never capture the URL. We scrub exactly these from every
# cursor launch (``env -u``) so cursor DOES spawn ``xdg-open`` → the shim (front
# of PATH) captures it → ``BROWSER:`` line → operator. SSH_AUTH_SOCK /
# SSH_AGENT_PID are deliberately NOT scrubbed (not in cursor's detection set;
# needed for git-over-SSH / agent forwarding inside the task).
_CURSOR_SSH_DETECT_VARS = (
    "SSH_CONNECTION", "SSH_CLIENT", "SSH_TTY", "SSH2_CLIENT", "SSH2_TTY",
    "MOSH_CLIENT", "MOSH_SERVER", "MOSH_TTY",
    "VSCODE_SSH_HOST", "VSCODE_SSH_SERVER",
)


def _cursor_data_dir(workdir: str) -> str:
    """Short, per-task path used as cursor's ``CURSOR_DATA_DIR``.

    cursor derives its socket/temp dir from ``CURSOR_DATA_DIR`` (default
    ``~/.cursor``); when that base exceeds ~84 chars it falls back to a hardcoded
    ``/tmp/.cursor`` (a UNIX-socket path-length limit). The task's isolated
    ``$HOME`` sits under a long taskdir, so ``$HOME/.cursor/projects`` blows the
    limit and cursor mkdir's ``/tmp/.cursor/<slug>`` — which the claustrum
    allowlist does not grant, so cursor exits 1 at startup (EACCES).

    Point ``CURSOR_DATA_DIR`` at this SHORT path instead; :func:`link_cursor_data_dir`
    symlinks it back to ``<workdir>/home/.cursor``, so cursor's socket paths stay
    short while its data physically lands inside the granted, snapshot-captured
    workdir (resume-safe). Deterministic in ``workdir`` so a resume re-links the
    same path. (Single-writer host assumption: ``ln -sfn`` owns the link.)"""
    h = hashlib.sha256(workdir.rstrip("/").encode()).hexdigest()[:10]
    return f"/tmp/oc-{h}"


async def link_cursor_data_dir(host: "Host") -> str:
    """Create the short ``CURSOR_DATA_DIR`` symlink -> ``<workdir>/home/.cursor``
    and return it. Idempotent (``ln -sfn``); a resume re-links on the restored
    tree. Keeps cursor's socket/temp paths short while its data stays inside the
    granted workdir (see :func:`_cursor_data_dir`)."""
    target = f"{host.workdir.rstrip('/')}/home/.cursor"
    link = _cursor_data_dir(host.workdir)
    r = await host.run_command(
        f"mkdir -p {shlex.quote(target)} && "
        f"ln -sfn {shlex.quote(target)} {shlex.quote(link)}"
    )
    if r.exit_code != 0:
        raise RuntimeError(
            f"linking CURSOR_DATA_DIR ({link!r} -> {target!r}) failed "
            f"(exit {r.exit_code}): {(r.stderr or '').strip()[:200]}"
        )
    return link


async def purge_cursor_session(host: "Host", session_id: str) -> None:
    """Remove a cursor ACP session's on-disk records from the task home.

    The startup model probe runs on a throwaway session that cursor persists
    under ``$HOME/.config/cursor/acp-sessions/<id>/`` (plus chat/transcript dirs
    named by the same id). Left in place they are snapshot-captured and, on
    resume, rediscovered by the agent — which cites the probe's "capital of
    Hungary" turns and spelunks the databases (a cascade of permission prompts).
    Purge every dir named exactly ``session_id`` under cursor's two state roots.
    Best-effort; ``session_id`` is validated as a plain token so it can never
    traverse out of the home."""
    if not re.fullmatch(r"[A-Za-z0-9_-]{8,64}", session_id or ""):
        return
    home = f"{host.workdir.rstrip('/')}/home"
    roots = (
        f"{shlex.quote(home + '/.config/cursor')} "
        f"{shlex.quote(home + '/.cursor')}"
    )
    await host.run_command(
        f"find {roots} -depth -type d -name {shlex.quote(session_id)} "
        f"-exec rm -rf {{}} + 2>/dev/null || true"
    )


def workspace_trust_marker(workdir: str) -> tuple[str, str]:
    """``(relative_path, json_content)`` for cursor's workspace-trust marker.

    cursor-agent gates a fresh workspace behind an interactive "Do you trust the
    contents of this directory?" prompt (``--force`` does NOT bypass it; the
    ``--trust`` flag only works with ``--print``). That blocks an UNATTENDED
    ``auto_start`` launch — cursor never reads AGENTS.md and the task dies. Cursor
    records trust at ``$HOME/.cursor/projects/<slug>/.workspace-trusted`` where
    ``<slug>`` is the workspace ABSOLUTE path (the cwd) with every run of
    non-alphanumerics collapsed to ``-``; its ``isTrusted()`` check is
    existence-only. Pre-planting the marker pre-authorizes the workspace so the
    launch proceeds. ``$HOME`` is ``<workdir>/home`` (see :func:`_isolation_env`),
    so the path is workdir-relative. The slug uses the absolute workdir, matching
    what cursor computes from its cwd on both local and remote hosts."""
    clean = workdir.rstrip("/")
    slug = re.sub(r"[^A-Za-z0-9]+", "-", clean).strip("-")
    rel = f"home/.cursor/projects/{slug}/.workspace-trusted"
    content = json.dumps(
        {
            "trustedAt": "1970-01-01T00:00:00.000Z",
            "workspacePath": clean,
            "trustMethod": "pre-authorized",
        },
        indent=2,
    ) + "\n"
    return rel, content


def _isolation_env(workdir: str) -> dict[str, str]:
    """Single source of truth for a task's HOME/XDG agent identity.

    Every cursor launch (tmux iframe via ``_build_cursor_shell_command``)
    derives its environment from this map so isolation is identical across
    launch paths. HOME + the three XDG keys are rooted at ``<workdir>/home``;
    ``CURSOR_DATA_DIR`` is a short external symlink into it (see
    :func:`_cursor_data_dir`):

    - ``HOME`` — cursor derives ``~/.cursor`` and ``~/.cache`` from ``$HOME``
      (verified), so the per-task home captures all of its state.
    - ``XDG_CONFIG_HOME`` / ``XDG_DATA_HOME`` / ``XDG_CACHE_HOME`` — pin the
      XDG base dirs into the task home so no XDG-respecting tool reaches the
      operator's ``~/.config`` / ``~/.cache``.

    ``NO_OPEN_BROWSER`` is intentionally NOT set. Cursor's login opens the auth
    URL by spawning ``xdg-open`` (it resolves the name on PATH; it ignores
    ``$BROWSER``). The redirect browser-shim shadows ``xdg-open`` at the front of
    PATH (``<workdir>/bin``), so cursor's open attempt is captured — the shim
    writes ``BROWSER: "<url>"`` to ``optio.log`` and the tail parser surfaces it
    to the operator (``ctx.request_browser_open``). Setting NO_OPEN_BROWSER would
    short-circuit that: cursor would only print the link to its TUI, invisible to
    a headless/conversation launch. (In an SSH session cursor refuses to open and
    prints the link regardless — remote tasks fall back to the TUI.)

    No claude-compat neutralization is needed (cursor does not ingest claude
    config) and cursor has no dedicated home env var beyond ``$HOME``.

    PATH is intentionally NOT included: it is layered by the caller (launch
    adds ``<home>/.local/bin`` ahead of the worker PATH)."""
    home = f"{workdir.rstrip('/')}/home"
    return {
        "HOME": home,
        "XDG_CONFIG_HOME": f"{home}/.config",
        "XDG_DATA_HOME": f"{home}/.local/share",
        "XDG_CACHE_HOME": f"{home}/.cache",
        # Short external symlink (into <workdir>/home/.cursor) so cursor's
        # socket/temp paths stay under the length limit — see _cursor_data_dir.
        "CURSOR_DATA_DIR": _cursor_data_dir(workdir),
    }


async def run_cursor_probe(
    host: "Host",
    *,
    cursor_executable: str,
    prompt: str,
    wrap: "list[str] | None" = None,
    timeout_s: float = 180.0,
) -> "tuple[str, int]":
    """Headless one-shot ``cursor-agent -p "<prompt>" --trust`` under the
    per-task isolation env. Returns (stdout, exit_code). ``wrap`` is an argv
    prefix seam (future claustrum fs-isolation). ``--trust`` grants workspace
    trust in ``--print`` mode on an unseen dir (without it the headless run
    stalls on the trust prompt). The caller's verdict is a challenge-answer
    match on stdout; the exit code is diagnostics only.

    Mirrors grok's ``run_grok_probe`` (grok → cursor renames + ``--trust``).
    """
    argv = [*(wrap or []), cursor_executable, "-p", prompt, "--trust"]
    cmd = " ".join(shlex.quote(a) for a in argv)
    # Layer the per-task HOME/XDG_* overrides on top of the ambient env,
    # mirroring the session launch's ``env HOME=… XDG_CONFIG_HOME=… bash -c …``
    # (which inherits, not ``env -i``). run_command replaces the child env, so
    # the merge is explicit here. The caller runs this on a host whose
    # environment carries no provider API keys (see verify_and_refresh_seed).
    env = {**os.environ, **_isolation_env(host.workdir)}
    result = await asyncio.wait_for(
        host.run_command(f"bash -lc {shlex.quote(cmd)}", env=env),
        timeout=timeout_s,
    )
    return (result.stdout or "", result.exit_code)


# --- ttyd install (copied verbatim from optio-grok) --------------------------


async def _ttyd_present(host: "Host", ttyd_path: str) -> bool:
    cmd = f"[ -x {shlex.quote(ttyd_path)} ] && {shlex.quote(ttyd_path)} --version"
    result = await host.run_command(cmd)
    # ttyd writes its version banner to stdout OR stderr depending on
    # version — accept either.
    blob = (result.stdout or "") + (result.stderr or "")
    return result.exit_code == 0 and "ttyd" in blob.lower()


async def _detect_ttyd_asset_name(host: "Host") -> str:
    """Return the upstream release-asset filename for the host's arch/OS.

    Raises RuntimeError on unsupported (OS, arch) combinations.
    """
    r_arch = await host.run_command("uname -m")
    if r_arch.exit_code != 0:
        raise RuntimeError(
            f"uname -m failed on host (exit {r_arch.exit_code}): "
            f"{r_arch.stderr.strip()[:200]}"
        )
    arch = r_arch.stdout.strip()
    r_os = await host.run_command("uname -s")
    if r_os.exit_code != 0:
        raise RuntimeError(
            f"uname -s failed on host (exit {r_os.exit_code}): "
            f"{r_os.stderr.strip()[:200]}"
        )
    os_name = r_os.stdout.strip()
    if os_name != "Linux":
        raise RuntimeError(
            f"unsupported host OS {os_name!r} for ttyd auto-install "
            f"(v1 supports Linux only; macOS support requires uploading "
            f"a Darwin binary or pre-installing ttyd manually)."
        )
    if arch not in {"x86_64", "aarch64", "armv7l"}:
        raise RuntimeError(
            f"unsupported host arch {arch!r} for ttyd auto-install. "
            f"See https://github.com/tsl0922/ttyd/releases for available "
            f"prebuilt assets."
        )
    return f"ttyd.{arch}"


async def ensure_ttyd_installed(
    hook_ctx: "HookContextProtocol",
    *,
    install_if_missing: bool = True,
    install_dir: str | None = None,
) -> str:
    """Ensure ``ttyd`` is present on the host behind ``hook_ctx``.

    When missing and ``install_if_missing=True``, downloads the
    appropriate static prebuilt asset from ``tsl0922/ttyd`` GitHub
    Releases via ``hook_ctx.download_file`` (so byte-progress shows in
    the dashboard).

    Returns the absolute path of the ``ttyd`` binary on the host.

    Raises RuntimeError on (a) absent binary with
    ``install_if_missing=False``; (b) unsupported (OS, arch); (c) any
    install sub-step failing.
    """
    host = hook_ctx._host
    resolved_install_dir = await _resolve_install_dir(host, install_dir)
    ttyd_path = f"{resolved_install_dir}/ttyd"

    hook_ctx.report_progress(None, "Checking ttyd installation…")
    if await _ttyd_present(host, ttyd_path):
        return ttyd_path

    if not install_if_missing:
        raise RuntimeError(
            f"ttyd not present at {ttyd_path!r} on host and "
            f"install_ttyd_if_missing=False; nothing to do."
        )

    hook_ctx.report_progress(None, "Detecting ttyd release asset…")
    asset = await _detect_ttyd_asset_name(host)
    url = f"{_TTYD_RELEASE_BASE}/{asset}"

    r = await host.run_command(f"mkdir -p {shlex.quote(resolved_install_dir)}")
    if r.exit_code != 0:
        raise RuntimeError(
            f"mkdir -p {resolved_install_dir!r} failed (exit {r.exit_code}): "
            f"{r.stderr.strip()[:200]}"
        )

    hook_ctx.report_progress(None, f"Downloading ttyd ({asset})…")
    await hook_ctx.download_file(url, ttyd_path)

    r = await host.run_command(f"chmod +x {shlex.quote(ttyd_path)}")
    if r.exit_code != 0:
        raise RuntimeError(
            f"chmod +x {ttyd_path!r} failed (exit {r.exit_code}): "
            f"{r.stderr.strip()[:200]}"
        )

    if not await _ttyd_present(host, ttyd_path):
        raise RuntimeError(
            f"ttyd install completed but {ttyd_path!r} is still not "
            f"executable on the host. Check the downloaded asset and "
            f"chmod result."
        )
    return ttyd_path


# --- launch env + DONE/ERROR wrapper ---------------------------------------


def _build_cursor_shell_command(
    *,
    cursor_path: str,
    workdir: str,
    extra_env: dict[str, str] | None,
    cursor_flags: list[str],
    local_mode: bool = False,
    claustrum_wrap: list[str] | None = None,
) -> tuple[list[str], str]:
    """Return (env_assignments, shell_command).

    ``env_assignments`` is the list of ``KEY=VALUE`` strings (the full
    :func:`_isolation_env` identity — HOME, XDG_* — plus PATH and extras;
    the redirect browser-shim rides in via ``extra_env``/PATH, not here).
    ``shell_command`` is the full
    ``env <assignments> bash -c <payload>`` string that runs cursor-agent
    under HOME-isolation and appends DONE/ERROR to optio.log when it exits.
    Consumed by build_tmux_session_argv (cursor runs inside the detached tmux
    session, not as a direct ttyd child).

    ``claustrum_wrap`` (Stage 8) is the argv prefix that Landlock-confines the
    WHOLE cursor process tree; when set it is prepended to the cursor
    invocation (``claustrum … -- cursor-agent …``), so claustrum execs
    cursor-agent inside the ruleset. None launches unconfined (fs_isolation
    off). Cursor has no netns/pasta seal (its login uses a device-code flow —
    ``redirectTarget=cli`` — not an OAuth loopback server), so the claustrum
    wrap is the outermost layer.
    """
    workdir_clean = workdir.rstrip("/")
    iso = _isolation_env(workdir_clean)
    home_dir = iso["HOME"]
    home_local_bin = f"{home_dir}/.local/bin"

    extra = dict(extra_env or {})
    base_path = extra.pop("PATH", None) or os.environ.get(
        "PATH", "/usr/local/bin:/usr/bin:/bin",
    )
    # HOME + PATH first (PATH prepends the per-task .local/bin), then the rest
    # of the isolation identity (XDG_*) from the SSOT.
    env_map = {
        "HOME": home_dir,
        "PATH": f"{home_local_bin}:{base_path}",
        **{k: v for k, v in iso.items() if k != "HOME"},
    }
    env_assignments: list[str] = [f"{k}={v}" for k, v in env_map.items()]
    for k, v in extra.items():
        env_assignments.append(f"{k}={v}")

    # claustrum (Stage 8) is the OUTERMOST wrapper: it confines cursor-agent
    # and every tool/subprocess it spawns. When absent the invocation is
    # cursor-agent alone (fs_isolation off).
    if claustrum_wrap:
        _LOG.info("claustrum filesystem isolation active (wrap=%r)", claustrum_wrap)
    confined = [*(claustrum_wrap or []), cursor_path, *cursor_flags]
    cursor_argv = " ".join(shlex.quote(c) for c in confined)
    log_path = f"{workdir_clean}/optio.log"

    bash_payload = (
        f"cd {shlex.quote(workdir_clean)} && {cursor_argv}; rc=$?; "
        f'if [ "$rc" = 0 ]; then echo DONE >> {shlex.quote(log_path)}; '
        f"else printf 'ERROR: cursor-agent exited %s\\n' \"$rc\" >> {shlex.quote(log_path)}; fi"
    )
    # Scrub cursor's SSH-detection vars (see _CURSOR_SSH_DETECT_VARS) so its
    # login opens the browser via xdg-open (captured by the redirect shim)
    # instead of falling back to printing the URL. `env -u VAR` is a no-op when
    # VAR is unset, so this is safe on a non-SSH worker too.
    unset_flags: list[str] = []
    for var in _CURSOR_SSH_DETECT_VARS:
        unset_flags += ["-u", var]
    shell_command = "env " + " ".join(
        shlex.quote(x)
        for x in [*unset_flags, *env_assignments, "bash", "-c", bash_payload]
    )
    return env_assignments, shell_command


# --- flags + config-planted permissions --------------------------------------


def _effective_sandbox(sandbox: str | None, fs_isolation: bool) -> str | None:
    """Resolve cursor-agent's own ``--sandbox`` value.

    An explicit caller ``sandbox`` always wins. Otherwise, when
    ``fs_isolation`` is on, the value defaults to ``"disabled"``: the whole
    process tree is already Landlock-confined by claustrum, so cursor's own
    per-shell-command native sandbox must NOT nest a helper inside the outer
    ruleset (see fs_allowlist.py). With neither set, the flag is omitted and
    cursor's default applies."""
    if sandbox is not None:
        return sandbox
    return "disabled" if fs_isolation else None


def build_cursor_flags(
    *,
    force: bool,
    auto_review: bool,
    sandbox: str | None,
    model: str | None,
    resuming: bool = False,
    fs_isolation: bool = False,
) -> list[str]:
    """Translate CursorTaskConfig knobs to an argv list.

    cursor-agent has NO permission argv (``--allow``/``--deny`` do not
    exist); permission rules are config-planted via :func:`build_cli_config`.
    ``--continue`` is appended when ``resuming`` (a restored snapshot means
    cursor persisted a chat for this cwd; ``--continue`` resumes the most
    recent one). ``fs_isolation`` couples to the native ``--sandbox`` toggle
    (see :func:`_effective_sandbox`). Validation of ``sandbox`` lives in
    ``CursorTaskConfig.__post_init__``.
    """
    out: list[str] = []
    if force:
        out += ["--force"]
    if auto_review:
        out += ["--auto-review"]
    effective_sandbox = _effective_sandbox(sandbox, fs_isolation)
    if effective_sandbox is not None:
        out += ["--sandbox", effective_sandbox]
    if model:
        out += ["--model", model]
    if resuming:
        out += ["--continue"]
    return out


def build_cli_config(
    *,
    allowed_tools: list[str] | None,
    disallowed_tools: list[str] | None,
) -> dict | None:
    """Payload for ``<home>/.cursor/cli-config.json`` — cursor's permission
    channel (there is no ``--allow``/``--deny`` argv).

    Returns the config dict when any rules are set, else None (nothing to
    plant; cursor's defaults apply). Empty lists are treated as None.
    ``approvalMode: "allowlist"`` makes the planted rules authoritative.
    Planted by session ``_prepare`` at ``<workdir>/home/.cursor/cli-config.json``.
    """
    if not allowed_tools and not disallowed_tools:
        return None
    return {
        "version": 1,
        "permissions": {
            "allow": list(allowed_tools or []),
            "deny": list(disallowed_tools or []),
        },
        "approvalMode": "allowlist",
    }


# Positional prompt appended to the cursor launch when ``auto_start`` is set —
# kicks the agent off without the operator typing anything.
AUTO_START_PROMPT = "Read AGENTS.md and execute the task it describes"


def build_auto_start_args(
    *, auto_start: bool, resuming: bool = False, prompt: str = AUTO_START_PROMPT,
) -> list[str]:
    """Trailing positional prompt for an auto-start FRESH launch.

    Returns ``[prompt]`` when ``auto_start`` and not ``resuming``; empty
    otherwise. On resume the session is continued with ``--continue`` and no
    positional is appended: re-issuing the kickoff prompt would start a new
    task instead of resuming the existing conversation.
    """
    return [prompt] if (auto_start and not resuming) else []


def build_resume_notice_args(*, resuming: bool) -> list[str]:
    """Trailing positional that notifies a resumed cursor TUI session.

    Returns ``[f"{SYSTEM_MESSAGE_PREFIX}{RESUME_NOTICE}"]`` on resume (cursor
    continues with ``--continue``, so a trailing positional is processed as a
    new turn in the continued session — mirrors claudecode's ``claude
    --continue '<text>'``). Empty on a fresh launch. This is the PUSH half of
    resume awareness — it makes the agent notice the resume promptly;
    ``resume.log`` remains the pull-based source of truth. Mutually exclusive
    with :func:`build_auto_start_args` (auto_start only fires on a FRESH
    launch). Adapted from optio-grok's build_resume_notice_args (grok's
    ``System:`` convention is always taught in the prompt, so no host_protocol
    gate is needed).
    """
    return [f"{SYSTEM_MESSAGE_PREFIX}{RESUME_NOTICE}"] if resuming else []


def build_conversation_argv(
    cursor_path: str,
    *,
    model: str | None = None,
    force: bool = False,
    sandbox: str | None = None,
    fs_isolation: bool = False,
) -> list[str]:
    """Argv for a headless ACP conversation: ``cursor-agent [opts] acp``.

    Options are TOP-LEVEL cursor-agent flags (``cursor-agent [OPTIONS]
    [COMMAND]``), so they precede the ``acp`` subcommand: ``--model``,
    ``--sandbox`` (native toggle — ``disabled`` under claustrum fs-isolation,
    same coupling as the iframe path; see :func:`_effective_sandbox`), and
    ``--force`` (skip interactive confirmations — cursor's auto-approve
    analogue of grok's ``--always-approve``; used when no permission gate is
    wired, so cursor never blocks on a prompt nobody answers). Acceptance of
    ``--force`` by the acp subcommand is runtime-unverified (host not logged
    in — see the wire-facts block in conversation.py); the fallback seam is
    answering session/request_permission allow-all client-side. No tmux/ttyd:
    the subprocess IS the agent. Adapted from optio-grok's
    build_conversation_argv.
    """
    argv = [cursor_path]
    if model:
        argv += ["--model", model]
    effective_sandbox = _effective_sandbox(sandbox, fs_isolation)
    if effective_sandbox is not None:
        argv += ["--sandbox", effective_sandbox]
    if force:
        argv += ["--force"]
    argv += ["acp"]
    return argv


# --- tmux / ttyd machinery (adapted verbatim from optio-grok) ----------------


def build_tmux_session_argv(
    *,
    tmux_path: str,
    cursor_path: str,
    workdir: str,
    socket_path: str,
    session_name: str,
    extra_env: dict[str, str] | None,
    cursor_flags: list[str],
    local_mode: bool = False,
    claustrum_wrap: list[str] | None = None,
) -> list[str]:
    """Argv for the detached ``tmux new-session`` that starts cursor-agent.

    tmux runs its command argument via ``/bin/sh -c``, so the env + cursor
    wrapper is a single trailing shell-string element. The private socket
    (``-S socket_path``) isolates this task's tmux server. ``-x/-y`` give the
    detached pane a sane initial size before any viewer attaches.

    ``claustrum_wrap`` (Stage 8) threads the fs-isolation prefix through to
    :func:`_build_cursor_shell_command`.
    """
    _, shell_command = _build_cursor_shell_command(
        cursor_path=cursor_path,
        workdir=workdir,
        extra_env=extra_env,
        cursor_flags=cursor_flags,
        local_mode=local_mode,
        claustrum_wrap=claustrum_wrap,
    )
    return [
        tmux_path, "-S", socket_path, "new-session", "-d",
        "-s", session_name, "-x", "200", "-y", "50",
        shell_command,
    ]


def build_ttyd_attach_argv(
    *,
    ttyd_path: str,
    tmux_path: str,
    socket_path: str,
    session_name: str,
    bind_iface: str,
    port: int,
) -> list[str]:
    """Argv for ttyd attaching viewers to the live tmux session.

    ttyd does not run cursor — it runs ``tmux attach``. ``-m 1`` is dropped
    so multiple viewers can attach to the same session simultaneously (the
    agent's life is owned by the tmux session, not by any connection).

    ``-t disableLeaveAlert=true`` turns off ttyd's web client ``beforeunload``
    prompt. With tmux persistence that warning is false — leaving the page
    only detaches a viewer; the session keeps running.
    """
    return [
        ttyd_path, "-W",
        "-i", bind_iface,
        "-p", str(port),
        "-t", "disableLeaveAlert=true",
        "-T", "xterm-256color",
        "--",
        tmux_path, "-S", socket_path, "attach", "-t", session_name,
    ]


def _tmux_socket_path(host: "Host") -> str:
    """Short, bounded, per-task tmux socket path under ``/tmp``.

    The socket must NOT live under ``host.workdir``: a deep ``$HOME`` plus a
    long processId can push ``${workdir}/tmux.sock`` past the Linux
    ``sun_path`` limit (108 bytes). ``sha256(workdir)`` keys the socket per
    task (deterministic across the task's calls, collision-safe); ``/tmp``
    always exists so no mkdir is needed. The result is ~35 bytes regardless
    of workdir length.
    """
    import hashlib

    digest = hashlib.sha256(host.workdir.encode("utf-8")).hexdigest()[:16]
    return f"/tmp/optio-cu-{digest}.sock"


async def _require_tmux(host: "Host") -> str:
    """Return the absolute path to tmux on the host, or raise a clear error.

    cursor-agent runs inside a detached tmux session (so the agent survives
    viewer disconnects); tmux is a worker prerequisite. Resolved via a login
    shell so PATH additions from the worker profile apply. No auto-install: a
    missing tmux fails fast with an actionable message.
    """
    result = await host.run_command("bash -lc 'command -v tmux'")
    path = (result.stdout or "").strip()
    if result.exit_code != 0 or not path:
        raise RuntimeError(
            "tmux is required on the worker for optio-cursor (cursor-agent "
            "runs inside a detached tmux session). Install tmux (e.g. "
            "apt-get install tmux) or add it to the worker/container image."
        )
    return path


async def _launch_detached_checked(
    host: "Host", cmd: str, *, env_remove: list[str] | None, what: str,
) -> list[str]:
    """Launch a detached command, drain its (stderr-merged) stdout, then check
    the exit code. Non-zero raises ``RuntimeError`` carrying the output.

    ``launch_subprocess`` returns a streaming handle with no ``exit_code``, so
    the code is recovered via ``proc_wait``. Silently swallowing it is what
    turned tmux's clear "File name too long" into the misleading "body
    returned before DONE was observed" downstream.
    """
    handle = await host.launch_subprocess(cmd, env_remove=env_remove)
    out: list[str] = []
    async for raw in handle.stdout:
        out.append(
            raw.decode("utf-8", errors="replace")
            if isinstance(raw, bytes) else str(raw)
        )
    code = await proc_wait(handle)
    if code != 0:
        raise RuntimeError(f"{what} failed (exit {code}): {''.join(out).strip()[:500]}")
    return out


async def launch_ttyd_with_cursor(
    host: "Host",
    *,
    ttyd_path: str,
    cursor_path: str,
    bind_iface: str,
    extra_env: dict[str, str] | None,
    cursor_flags: list[str],
    ready_timeout_s: float = 30.0,
    env_remove: list[str] | None = None,
    session_name: str = "optio",
    claustrum_wrap: list[str] | None = None,
) -> "tuple[ProcessHandle, int, str, str]":
    """Start cursor-agent in a detached tmux session, then ttyd attaching to it.

    Returns ``(ttyd_handle, port, socket_path, session_name)``. cursor runs in
    the tmux session independent of ttyd; the caller awaits tmux-session
    liveness for completion and tears down BOTH the tmux session and ttyd.

    ``claustrum_wrap`` (Stage 8) Landlock-confines cursor-agent + its whole
    subprocess tree inside the tmux pane (None launches unconfined).
    """
    tmux_path = await _require_tmux(host)
    socket_path = _tmux_socket_path(host)

    from optio_host.host import LocalHost
    local_mode = isinstance(host, LocalHost)

    # 1) Start cursor detached in tmux. The env scrub (env_remove) must apply
    #    here so the tmux server — which holds cursor — does not inherit
    #    scrubbed vars. ``new-session -d`` returns immediately; its exit code
    #    IS checked (via ``_launch_detached_checked``).
    session_argv = build_tmux_session_argv(
        tmux_path=tmux_path,
        cursor_path=cursor_path,
        workdir=host.workdir,
        socket_path=socket_path,
        session_name=session_name,
        extra_env=extra_env,
        cursor_flags=cursor_flags,
        local_mode=local_mode,
        claustrum_wrap=claustrum_wrap,
    )
    session_cmd = " ".join(shlex.quote(a) for a in session_argv)
    await _launch_detached_checked(
        host, session_cmd, env_remove=env_remove, what="tmux new-session",
    )

    # 2) Start ttyd attaching to the live session.
    ttyd_argv = build_ttyd_attach_argv(
        ttyd_path=ttyd_path,
        tmux_path=tmux_path,
        socket_path=socket_path,
        session_name=session_name,
        bind_iface=bind_iface,
        port=0,
    )
    command = " ".join(shlex.quote(a) for a in ttyd_argv)
    handle = await host.launch_subprocess(command)

    async def _read_port() -> int:
        async for raw in handle.stdout:
            line = raw.decode("utf-8", errors="replace").rstrip() if isinstance(raw, bytes) else str(raw).rstrip()
            m = _TTYD_READY_RE.search(line)
            if m:
                port_str = m.group(1) or m.group(2)
                return int(port_str)
        raise RuntimeError("ttyd exited before printing a listening URL")

    try:
        port = await asyncio.wait_for(_read_port(), timeout=ready_timeout_s)
    except asyncio.TimeoutError:
        await host.terminate_subprocess(handle, aggressive=True)
        await _kill_tmux_session(host, tmux_path, socket_path, session_name)
        raise TimeoutError(
            f"ttyd did not print a listening URL within {ready_timeout_s}s"
        )
    except BaseException:
        await host.terminate_subprocess(handle, aggressive=True)
        await _kill_tmux_session(host, tmux_path, socket_path, session_name)
        raise
    return handle, port, socket_path, session_name


async def _kill_tmux_session(
    host: "Host", tmux_path: str, socket_path: str, session_name: str,
) -> None:
    """Best-effort kill of the per-task tmux session (stops cursor-agent)."""
    try:
        await host.run_command(
            f"{shlex.quote(tmux_path)} -S {shlex.quote(socket_path)} "
            f"kill-session -t {shlex.quote(session_name)}"
        )
    except Exception:  # noqa: BLE001
        _LOG.exception("tmux kill-session failed (socket=%s)", socket_path)


def _cursor_pgrep_pattern(cursor_path: str) -> str:
    """Anchored pgrep/pkill pattern matching ONLY the real cursor-agent.

    The real cursor-agent execs with the path as the FIRST token of its
    cmdline (argv[0]), whereas the tmux server and the bash/env wrappers
    carry the same path only as a LATER argument. ``^`` excludes them; only
    a process whose cmdline starts with the path matches.
    ``[c]ursor-agent`` keeps pgrep/pkill's own cmdline from self-matching.
    """
    suffix = "cursor-agent"
    body = (
        cursor_path[: -len(suffix)] + "[c]ursor-agent"
        if cursor_path.endswith(suffix) else cursor_path
    )
    return "^" + body


def _socket_pkill_pattern(socket_path: str) -> str:
    """Anchored pkill -f pattern matching the orphan ttyd that carries
    ``socket_path`` in its cmdline (``ttyd ... -- tmux -S <socket> attach``).

    The ``ttyd`` binary token is bracket-escaped (``[t]tyd``) so pkill's own
    argv does not self-match. The full ``socket_path`` is kept verbatim so the
    match is scoped to this task's private socket."""
    if not socket_path:
        return socket_path
    return f"[t]tyd.*{socket_path}"


async def _kill_ttyd_by_socket(host: "Host", socket_path: str) -> None:
    """Reap a detached orphan ttyd that has no tracked launch handle.

    Best-effort: pkill exits non-zero when nothing matches."""
    pattern = _socket_pkill_pattern(socket_path)
    await host.run_command(f"pkill -KILL -f {shlex.quote(pattern)} || true")


async def kill_cursor_processes(
    host: "Host", cursor_path: str, *, signal: str = "KILL",
) -> None:
    """Kill the per-task cursor-agent via an anchored host-side ``pkill``.

    cursor-agent may ignore the tmux pane SIGHUP. Best-effort: pkill exits
    non-zero when nothing matches."""
    pattern = _cursor_pgrep_pattern(cursor_path)
    await host.run_command(f"pkill -{signal} -f {shlex.quote(pattern)} || true")


async def await_cursor_gone(
    host: "Host", cursor_path: str, *, timeout_s: float = 15.0, poll_s: float = 1.0,
) -> bool:
    """Block (polling once per ``poll_s``) until no process matching the
    per-task ``cursor_path`` remains. Bounded by ``timeout_s`` (logs a warning
    and returns False on timeout). Returns True once cursor is gone."""
    pattern = _cursor_pgrep_pattern(cursor_path)
    waited = 0.0
    while True:
        r = await host.run_command(f"pgrep -f {shlex.quote(pattern)} || true")
        if not (r.stdout or "").strip():
            return True
        if waited >= timeout_s:
            _LOG.warning(
                "await_cursor_gone: cursor-agent still running after %.0fs "
                "(path=%s); proceeding anyway", timeout_s, cursor_path,
            )
            return False
        await asyncio.sleep(poll_s)
        waited += poll_s


async def teardown_session_tree(
    host: "Host",
    *,
    tmux_path: str,
    tmux_socket: str,
    tmux_session: str,
    cursor_path: str,
    ttyd_handle: "ProcessHandle | None" = None,
    aggressive: bool,
) -> None:
    """Kill a full cursor session tree (ttyd + tmux + cursor-agent).

    Four best-effort steps, each isolated so one failure does not abort the
    rest: (1) ttyd via the tracked handle or an anchored socket pkill;
    (2) ``kill-session`` SIGHUPs the tmux pane; (3) ``kill_cursor_processes``
    (cursor may ignore the pane SIGHUP); (4) ``await_cursor_gone`` waits for
    quiescence."""
    if ttyd_handle is not None:
        try:
            await host.terminate_subprocess(ttyd_handle, aggressive=aggressive)
        except Exception:
            _LOG.exception("terminate_subprocess (ttyd) failed")
    else:
        try:
            await _kill_ttyd_by_socket(host, tmux_socket)
        except Exception:
            _LOG.exception("orphan ttyd reap failed (socket=%s)", tmux_socket)

    try:
        await _kill_tmux_session(host, tmux_path, tmux_socket, tmux_session)
    except Exception:
        _LOG.exception("tmux session teardown failed")

    try:
        await kill_cursor_processes(host, cursor_path)
    except Exception:
        _LOG.exception("kill_cursor_processes failed")

    try:
        await await_cursor_gone(host, cursor_path)
    except Exception:
        _LOG.exception("await_cursor_gone failed; proceeding")


async def tmux_session_alive(
    host: "Host", tmux_path: str, socket_path: str, session_name: str,
) -> bool:
    """True while the cursor-bearing tmux session exists."""
    r = await host.run_command(
        f"{shlex.quote(tmux_path)} -S {shlex.quote(socket_path)} "
        f"has-session -t {shlex.quote(session_name)}"
    )
    return r.exit_code == 0


async def send_text_to_cursor(
    host: "Host", tmux_path: str, tmux_socket: str, tmux_session: str, text: str,
) -> None:
    """Fake-type a message into the cursor TUI and submit it.

    Thin wrapper over the shared
    :func:`optio_agents.tmux_input.send_text_to_tmux`, pinned to cursor's buffer
    name (``optio-feedback``) and settle (``_SUBMIT_SETTLE_S``) so the behavior
    is identical. Raises on a tmux failure."""
    await _tmux_input.send_text_to_tmux(
        host, tmux_path, tmux_socket, tmux_session, text,
        buffer="optio-feedback", submit_settle=_SUBMIT_SETTLE_S,
    )


async def send_key_to_cursor(
    host: "Host", tmux_path: str, tmux_socket: str, tmux_session: str, key: str,
) -> None:
    """Send a single navigation keystroke into the cursor TUI (iframe-input
    empty-box TUI nav). Thin wrapper over
    :func:`optio_agents.tmux_input.send_key_to_tmux`."""
    await _tmux_input.send_key_to_tmux(host, tmux_path, tmux_socket, tmux_session, key)


# --- resume bookkeeping (adapted verbatim from optio-grok) -------------------


async def _rotate_optio_log(host: "Host") -> None:
    """Append the restored optio.log to optio.log.old, then truncate it.

    Preserves historical log content across consecutive resumes while ensuring
    the tail driver only sees fresh lines from the resumed run (a stale DONE/
    ERROR carried in the restored log would otherwise be replayed and end the
    session immediately).
    """
    workdir = host.workdir.rstrip("/")
    log_abs = f"{workdir}/optio.log"
    old_abs = f"{workdir}/optio.log.old"
    try:
        current = (await host.fetch_bytes_from_host(log_abs)).decode("utf-8")
    except FileNotFoundError:
        current = ""
    if not current:
        await host.write_text("optio.log", "")
        return
    try:
        existing_old = (await host.fetch_bytes_from_host(old_abs)).decode("utf-8")
    except FileNotFoundError:
        existing_old = ""
    await host.write_text("optio.log.old", existing_old + current)
    await host.write_text("optio.log", "")


async def _append_resume_log_entry(
    host: "Host", *, refreshed: list[str] | None = None,
) -> None:
    """Append one line to ``<workdir>/resume.log``.

    Line format: ``<ISO 8601 UTC timestamp>[ REFRESHED:<comma-separated names>]``.
    The first line is the original launch; each later line marks a resume. The
    caller gates this on ``config.supports_resume``.
    """
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    line = f"{ts} REFRESHED:{','.join(refreshed)}" if refreshed else ts
    target = f"{host.workdir.rstrip('/')}/resume.log"
    result = await host.run_command(
        f"echo {shlex.quote(line)} >> {shlex.quote(target)}"
    )
    if result.exit_code != 0:
        raise RuntimeError(
            f"failed to append to resume.log: exit {result.exit_code}: "
            f"{result.stderr!r}"
        )
