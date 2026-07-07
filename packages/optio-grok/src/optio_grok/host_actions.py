"""Grok-specific actions over a generic Host.

Free functions; each takes a Host or HookContext and uses only generic
primitives (run_command, resolve_host_home, etc.). No isinstance branches.

Adapted from optio-claudecode's ``host_actions``. Stage 0 drops the
claustrum fs-isolation, netns OAuth seal, debug pane-mirror, and
credential-planting branches; the tmux/ttyd machinery and the ttyd
installer are copied with only ``claude`` → ``grok`` renames.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import shlex
from datetime import datetime, timezone
from typing import TYPE_CHECKING

from optio_agents import RESUME_NOTICE, SYSTEM_MESSAGE_PREFIX
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


# Settle (seconds) between pasting a message into the grok TUI and sending
# Enter. Without it the Enter is glued to the paste and grok treats the CR
# as a newline inside the input box instead of a submit (see
# send_text_to_grok). A shell-literal string (used in a `sleep` invocation).
_SUBMIT_SETTLE_S = "1.0"

# Pinned ttyd version. Update with care; the URL pattern below is
# tsl0922/ttyd's release-asset convention as of 1.7.x.
_TTYD_VERSION = "1.7.7"
_TTYD_RELEASE_BASE = (
    f"https://github.com/tsl0922/ttyd/releases/download/{_TTYD_VERSION}"
)

# ttyd installs into the worker home's ``.local/bin``.
_DEFAULT_INSTALL_SUBDIR = ".local/bin"

# The optio-owned grok binary cache lives on the WORKER, outside every task
# workdir and never the operator's autoupdating ``~/.grok``. Default:
# ``${XDG_CACHE_HOME:-$HOME/.cache}/optio-grok/bin``; ``GROK_CACHE_DIR`` overrides.
# Resolved via a shell echo so RemoteHost gets the remote location, and so the
# cache stays shared + evictable (never snapshotted, re-seeded on a miss).
_GROK_CACHE_DIR_SHELL_DEFAULT = (
    "${GROK_CACHE_DIR:-${XDG_CACHE_HOME:-$HOME/.cache}/optio-grok/bin}"
)

# Official grok vendor installer (confirmed: ``~/.grok/README.md``). HOME-driven:
# writes the heavy versioned binary to ``$HOME/.grok/downloads`` and a stable
# ``grok`` symlink to ``${GROK_BIN_DIR:-$HOME/.grok/bin}``. optio drives it with
# HOME = the cache ROOT so the install lands in the persistent cache, never the
# operator's real ``~/.grok`` and never a task workdir.
_GROK_INSTALL_URL = "https://x.ai/cli/install.sh"


async def _resolve_install_dir(host: "Host", install_dir: str | None) -> str:
    """Return ``install_dir`` if given, else ``<host_home>/.local/bin`` (ttyd)."""
    if install_dir is not None:
        return install_dir
    host_home = await host.resolve_host_home()
    return f"{host_home}/{_DEFAULT_INSTALL_SUBDIR}"


async def _resolve_grok_cache_dir(host: "Host", override: str | None) -> str:
    """Resolve the optio-owned grok binary-cache dir as an absolute worker path.

    ``override`` (``config.install_dir``) wins. Otherwise the worker's real
    env decides via a shell echo: ``GROK_CACHE_DIR`` else
    ``${XDG_CACHE_HOME:-$HOME/.cache}/optio-grok/bin`` — resolved on the host so
    RemoteHost gets the remote location. Deliberately mirrors claudecode's
    ``_resolve_cache_dir`` (the ttyd ``_resolve_install_dir`` is a separate,
    home-relative resolver and is intentionally left untouched)."""
    if override is not None:
        return override.rstrip("/")
    r = await host.run_command(f'printf %s "{_GROK_CACHE_DIR_SHELL_DEFAULT}"')
    path = (r.stdout or "").strip()
    if r.exit_code != 0 or not path:
        raise RuntimeError(
            f"failed to resolve grok cache dir on host "
            f"(exit {r.exit_code}): {(r.stderr or '').strip()[:200]}"
        )
    return path.rstrip("/")


# --- grok resolution (Stage 0: no binary cache/download; that is Stage 5) ---


async def resolve_grok(
    host: "Host",
    *,
    install_dir: str | None = None,
    install_if_missing: bool = True,
) -> str:
    """Host-based ``grok`` binary resolution (no HookContext).

    Resolved from ``<install_dir>/grok`` when ``install_dir`` is given,
    otherwise via ``command -v grok`` in a login shell (so worker-profile PATH
    additions apply, e.g. ``~/.grok/bin``). Raises when the binary is absent.
    Shared by ``ensure_grok_installed`` (engine path) and ``verify`` (engine-
    free path)."""
    if install_dir is not None:
        candidate = f"{install_dir.rstrip('/')}/grok"
        probe = await host.run_command(
            f"[ -x {shlex.quote(candidate)} ] && echo OK || true"
        )
        if "OK" in (probe.stdout or ""):
            return candidate
        raise RuntimeError(
            f"grok not present at {candidate!r} on host "
            f"(install_dir={install_dir!r})."
        )

    result = await host.run_command("bash -lc 'command -v grok'")
    path = (result.stdout or "").strip()
    if result.exit_code == 0 and path:
        return path

    if not install_if_missing:
        raise RuntimeError(
            "grok not found on host and install_if_missing=False; nothing to do."
        )
    raise RuntimeError(
        "grok not found on the worker (looked via 'command -v grok'). Stage 0 "
        "has no auto-install (the binary cache is a later stage) — install "
        "grok manually (e.g. ~/.grok/bin/grok) or pass install_dir."
    )


async def ensure_grok_installed(
    hook_ctx: "HookContextProtocol",
    *,
    install_if_missing: bool = True,
    install_dir: str | None = None,
    progress_label: str = "Preparing Grok Build…",
    check_update: bool = True,
) -> str:
    """Provision ``grok`` for this task from the optio-owned binary cache.

    The cache dir (``_resolve_grok_cache_dir``) lives on the worker outside any
    task workdir and never the operator's autoupdating ``~/.grok`` — so it stays
    shared, evictable, and unsnapshotted (it survives task teardown). This makes
    the cache the single stable home of the binary; the value RETURNED is the
    per-task launch path ``<workdir>/home/.local/bin/grok`` — a symlink into the
    cache, on the launch PATH (mirrors claudecode's ``home/.local/bin/claude``).

    Cache population (only on a miss, and only when ``install_if_missing``):

    - **seed from a host grok** — if a ``grok`` is already on the worker
      (login-shell ``command -v grok`` via :func:`resolve_grok`) copy it into the
      cache (deref + chmod +x); fast, no download, matches the operator's version.
    - **vendor auto-install** — otherwise run grok's official installer
      (:data:`_GROK_INSTALL_URL`) into the persistent cache, so a fresh or remote
      worker with no host grok still bootstraps itself.

    On a cache HIT the cached binary is version-checked (``grok update --check
    --json``) and, when stale, refreshed to the latest release BEFORE it is
    linked — keeping the cache current so grok never self-downloads a fresh
    binary into the workdir at runtime. ``check_update=False`` skips that probe:
    the resume flow calls this twice (once up front, once to re-link after
    ``restore_workdir`` wipes the symlink), and the second call passes it so a
    resume runs the network probe once, not twice.

    Uses only generic Host primitives. Raises only when the cache is empty AND
    ``install_if_missing=False``. Idempotent on a re-call (cache hit → it just
    re-links the task path), which is how resume re-establishes the launch symlink
    after ``restore_workdir`` wipes it.
    """
    host = hook_ctx._host
    hook_ctx.report_progress(None, progress_label)

    cache_dir = await _resolve_grok_cache_dir(host, install_dir)
    cached = f"{cache_dir}/grok"

    probe = await host.run_command(
        f"[ -x {shlex.quote(cached)} ] && echo OK || true"
    )
    if "OK" in (probe.stdout or ""):
        _LOG.info("ensure_grok_installed: cache HIT (%s)", cached)
        # Refresh a STALE cache to the latest release. A cache left behind the
        # current grok release makes grok self-download a fresh ~150 MB binary
        # into home/.grok/downloads at runtime — which bloats the resume
        # snapshot enough that capture_snapshot overruns the cancel grace and
        # the task force-fails. Keeping the cache current removes that impulse.
        # Best-effort + gated on install_if_missing (an offline/pinned worker
        # keeps the binary it has; the update probe never blocks a launch).
        target = None
        if check_update and install_if_missing:
            target = await _grok_update_target(host, cached, cache_dir=cache_dir)
        if target:
            hook_ctx.report_progress(None, f"Updating Grok Build to {target}…")
            await _install_grok_into_cache(
                hook_ctx, host, cache_dir=cache_dir, cached=cached,
            )
            _LOG.info(
                "ensure_grok_installed: cache stale -> refreshed to %s (%s)",
                target, cached,
            )
    elif not install_if_missing:
        raise RuntimeError(
            f"grok not present in cache at {cached!r} and "
            f"install_if_missing=False; nothing to do."
        )
    else:
        await _populate_grok_cache(hook_ctx, host, cache_dir=cache_dir, cached=cached)

    return await _link_grok_into_task(host, cached)


async def _grok_update_target(
    host: "Host", cached: str, *, cache_dir: str,
) -> str | None:
    """Best-effort: the version to upgrade the cached binary to, or ``None``.

    Runs the CACHED binary's own updater in ``--check`` mode (a version compare,
    NO download) under ``HOME`` = the cache ROOT so it never touches the
    operator's ``~/.grok``. The CLI prints a one-line JSON object, e.g.
    ``{"currentVersion":"0.2.82","latestVersion":"0.2.87","updateAvailable":true}``.

    Returns the ``latestVersion`` string when ``updateAvailable`` (so the caller
    can NAME the target in its progress label), falling back to ``"latest"`` if
    the CLI omits the version. Returns ``None`` when the cache is current, or on
    a non-zero exit (offline) / unparseable output — the probe must never block a
    launch, and a stale-but-working cache is preferable to a failed start.
    """
    cache_root = os.path.dirname(cache_dir.rstrip("/")) or "/"
    r = await host.run_command(
        f"env HOME={shlex.quote(cache_root)} {shlex.quote(cached)} "
        f"update --check --json"
    )
    if r.exit_code != 0:
        _LOG.warning(
            "grok update --check failed (exit %s); keeping cached binary: %s",
            r.exit_code, (r.stderr or "").strip()[:200],
        )
        return None
    try:
        data = json.loads((r.stdout or "").strip())
    except (ValueError, TypeError):
        _LOG.warning(
            "grok update --check returned unparseable output; keeping cache: %r",
            (r.stdout or "")[:200],
        )
        return None
    if not data.get("updateAvailable"):
        return None
    return str(data.get("latestVersion") or "").strip() or "latest"


async def _populate_grok_cache(
    hook_ctx: "HookContextProtocol",
    host: "Host",
    *,
    cache_dir: str,
    cached: str,
) -> None:
    """Fill an empty cache: prefer seeding from a pre-existing host grok (fast,
    no download); fall back to the vendor auto-installer when the worker has
    none. Leaves an executable ``<cache_dir>/grok`` on success; raises otherwise.
    """
    # Fast path — a grok already on the worker (login-shell PATH).
    source: "str | None"
    try:
        source = await resolve_grok(host, install_dir=None, install_if_missing=False)
    except RuntimeError:
        source = None

    if source is None:
        # No host grok anywhere — bootstrap via the vendor installer.
        await _install_grok_into_cache(hook_ctx, host, cache_dir=cache_dir, cached=cached)
        _LOG.info("ensure_grok_installed: cache MISS -> vendor-installed into %s", cached)
        return

    hook_ctx.report_progress(None, "Seeding Grok Build cache…")
    mk = await host.run_command(f"mkdir -p {shlex.quote(cache_dir)}")
    if mk.exit_code != 0:
        raise RuntimeError(
            f"mkdir -p {cache_dir!r} failed (exit {mk.exit_code}): "
            f"{(mk.stderr or '').strip()[:200]}"
        )
    # ``-L`` dereferences: a symlinked host grok becomes a real, stable copy in
    # the cache (independent of the host binary the operator may autoupdate).
    cp = await host.run_command(
        f"cp -L {shlex.quote(source)} {shlex.quote(cached)}"
    )
    if cp.exit_code != 0:
        raise RuntimeError(
            f"seeding grok cache (cp {source!r} -> {cached!r}) failed "
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
            f"grok cache seed completed but {cached!r} is still not executable "
            f"on the host. Check the seed source {source!r} and chmod result."
        )
    _LOG.info("ensure_grok_installed: cache MISS -> seeded from host %s", source)


async def _install_grok_into_cache(
    hook_ctx: "HookContextProtocol",
    host: "Host",
    *,
    cache_dir: str,
    cached: str,
) -> None:
    """Vendor auto-install grok into the persistent cache (miss + no host grok).

    Runs the official installer with ``HOME`` = the cache ROOT (``dirname`` of
    ``cache_dir``) so the heavy versioned binary lands in
    ``<cache_root>/.grok/downloads`` — a persistent location OUTSIDE any task
    workdir — and ``GROK_BIN_DIR`` = ``cache_dir`` so the stable ``grok`` symlink
    is created at ``<cache_dir>/grok``. HOME is the cache root (never the real
    ``~``), so nothing touches the operator's ``~/.grok``.
    """
    cache_root = os.path.dirname(cache_dir.rstrip("/")) or "/"
    hook_ctx.report_progress(None, "Installing Grok Build (vendor installer)…")
    installer = f"curl -fsSL {shlex.quote(_GROK_INSTALL_URL)} | bash"
    cmd = (
        f"mkdir -p {shlex.quote(cache_root)} {shlex.quote(cache_dir)} && "
        f"env HOME={shlex.quote(cache_root)} GROK_BIN_DIR={shlex.quote(cache_dir)} "
        f"sh -c {shlex.quote(installer)}"
    )
    result = await host.run_command(cmd)
    if result.exit_code != 0:
        raise RuntimeError(
            f"grok vendor auto-install failed on host (exit {result.exit_code}): "
            f"{(result.stderr or '').strip()[:300]}"
        )
    verify = await host.run_command(
        f"[ -x {shlex.quote(cached)} ] && echo OK || true"
    )
    if "OK" not in (verify.stdout or ""):
        raise RuntimeError(
            f"grok vendor install reported success but {cached!r} is still not "
            f"executable. Inspect the installer output and the cache {cache_dir!r}."
        )


async def _link_grok_into_task(host: "Host", cached: str) -> str:
    """Symlink the cached grok binary into the task's isolated home launch dir.

    The cache lives OUTSIDE the workdir (persists across task teardown); the
    launch path ``<workdir>/home/.local/bin/grok`` is a per-task symlink to it,
    ahead on the launch PATH. Returns that task path. ``ln -sfn`` is idempotent —
    a resume re-call just refreshes the symlink on the restored tree.
    """
    workdir = host.workdir.rstrip("/")
    bin_dir = f"{workdir}/home/.local/bin"
    task_grok = f"{bin_dir}/grok"
    r = await host.run_command(
        f"mkdir -p {shlex.quote(bin_dir)} && "
        f"ln -sfn {shlex.quote(cached)} {shlex.quote(task_grok)}"
    )
    if r.exit_code != 0:
        raise RuntimeError(
            f"linking grok into the task path ({task_grok!r} -> {cached!r}) "
            f"failed (exit {r.exit_code}): {(r.stderr or '').strip()[:200]}"
        )
    return task_grok


def build_host(ssh, taskdir: str) -> "Host":
    """ssh_config + taskdir -> LocalHost/RemoteHost. Lifted from
    session._build_host so engine-free callers (verify) share it (mirrors
    opencode's host_actions.build_host)."""
    from optio_host.host import LocalHost, RemoteHost

    if ssh is None:
        os.makedirs(taskdir, exist_ok=True)
        host: "Host" = LocalHost(taskdir=taskdir)
        os.makedirs(host.workdir, exist_ok=True)
        return host
    return RemoteHost(ssh_config=ssh, taskdir=taskdir)


def _isolation_env(workdir: str) -> dict[str, str]:
    """Single source of truth for a task's HOME/XDG/GROK agent identity.

    Every grok launch (the tmux iframe via ``_build_grok_shell_command`` and the
    ACP conversation launch) derives its environment from this map so isolation
    is identical across launch paths. Six explicit keys, all rooted at
    ``<workdir>/home``:

    - ``HOME`` / ``GROK_HOME`` — grok's own state lands in the per-task home.
    - ``XDG_CONFIG_HOME`` / ``XDG_DATA_HOME`` / ``XDG_CACHE_HOME`` — pin the XDG
      base dirs into the task home so no XDG-respecting tool (or grok's own
      config lookup) reaches the operator's ``~/.config`` / ``~/.cache``.
    - ``CLAUDE_CONFIG_DIR`` — neutralizes grok's claude-compat layer: without it
      the operator's global ``~/.claude`` (CLAUDE.md, settings, hooks) leaks
      into the sandboxed task.

    PATH is intentionally NOT included: it is layered by the caller (launch adds
    ``<home>/.local/bin`` ahead of the worker PATH)."""
    home = f"{workdir.rstrip('/')}/home"
    return {
        "HOME": home,
        "GROK_HOME": f"{home}/.grok",
        "XDG_CONFIG_HOME": f"{home}/.config",
        "XDG_DATA_HOME": f"{home}/.local/share",
        "XDG_CACHE_HOME": f"{home}/.cache",
        "CLAUDE_CONFIG_DIR": f"{home}/.claude",
    }


# Host-side line editor that forces ``auto_update = false`` inside the ``[cli]``
# table of a grok config.toml, preserving all other content. Placement is
# TOML-correct: the key is dropped/re-emitted only WITHIN ``[cli]`` (a bare key
# appended after a foreign ``[table]`` header would be parsed as a member of
# that table, so grok would ignore it and keep auto-updating). Runs via the
# worker's ``python3`` (already a grok-worker dependency — see the ctty wrap).
_GROK_DISABLE_AUTOUPDATE_PY = (
    "import sys\n"
    "p=sys.argv[1]\n"
    "try:\n"
    "    lines=open(p).read().splitlines()\n"
    "except FileNotFoundError:\n"
    "    lines=[]\n"
    "out=[]\n"
    "in_cli=False\n"
    "done=False\n"
    "def hdr(l):\n"
    "    s=l.strip()\n"
    "    return s.startswith('[') and s.endswith(']')\n"
    "for l in lines:\n"
    "    if hdr(l):\n"
    "        if in_cli and not done:\n"
    "            out.append('auto_update = false')\n"
    "            done=True\n"
    "        in_cli = l.strip()=='[cli]'\n"
    "        out.append(l)\n"
    "        continue\n"
    "    if in_cli and l.strip().replace(' ','').startswith('auto_update='):\n"
    "        continue\n"
    "    out.append(l)\n"
    "if in_cli and not done:\n"
    "    out.append('auto_update = false')\n"
    "    done=True\n"
    "if not done:\n"
    "    out.append('[cli]')\n"
    "    out.append('auto_update = false')\n"
    "open(p,'w').write('\\n'.join(out)+'\\n')\n"
)


async def write_grok_config(host: "Host", workdir: str) -> None:
    """Force ``[cli] auto_update = false`` in the task's ``$GROK_HOME/config.toml``.

    GROK_HOME is ``<workdir>/home/.grok`` (see :func:`_isolation_env`), so the
    config lives at ``<workdir>/home/.grok/config.toml``. Grok otherwise
    self-downloads a fresh ~150 MB versioned binary into ``home/.grok/downloads``
    at runtime, which inflates the resume snapshot so much that
    ``capture_snapshot`` overruns the cancel grace and the task force-fails.
    Setting ``auto_update = false`` (empirically the ONLY switch that stops it —
    the ``GROK_AUTO_UPDATE`` env var does not) removes that behaviour.

    Merge-safe: replaces an existing ``auto_update`` line inside ``[cli]``, adds
    the key under an existing ``[cli]`` table, or appends a new ``[cli]`` table —
    never clobbering other config (auth, seeded keys, other tables) and never
    leaking the key into a foreign table. Idempotent. Called on every launch
    (fresh, seeded, resumed) AFTER any restore/seed so it overrides a config.toml
    those carried. Complements the cache version-check in
    :func:`ensure_grok_installed`: the check keeps the cache at the latest release
    (nothing to update to), and this covers a release landing mid-session.
    """
    grok_home = f"{workdir.rstrip('/')}/home/.grok"
    cfg = f"{grok_home}/config.toml"
    cmd = (
        f"mkdir -p {shlex.quote(grok_home)} && "
        f"python3 -c {shlex.quote(_GROK_DISABLE_AUTOUPDATE_PY)} {shlex.quote(cfg)}"
    )
    r = await host.run_command(cmd)
    if r.exit_code != 0:
        raise RuntimeError(
            f"disabling grok auto_update in {cfg!r} failed "
            f"(exit {r.exit_code}): {(r.stderr or '').strip()[:200]}"
        )


# --- ttyd install (copied verbatim from optio-claudecode) -------------------


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


def _build_grok_shell_command(
    *,
    grok_path: str,
    workdir: str,
    extra_env: dict[str, str] | None,
    grok_flags: list[str],
    local_mode: bool = False,
) -> tuple[list[str], str]:
    """Return (env_assignments, shell_command).

    ``env_assignments`` is the list of ``KEY=VALUE`` strings (HOME, PATH,
    GROK_HOME, CLAUDE_CONFIG_DIR, extras). ``shell_command`` is the full
    ``env <assignments> bash -c <payload>`` string that runs grok under
    HOME-isolation and appends DONE/ERROR to optio.log when grok exits.
    Consumed by build_tmux_session_argv (grok runs inside the detached tmux
    session, not as a direct ttyd child).

    ``GROK_HOME`` points grok's own state at the per-task home. ``CLAUDE_CONFIG_DIR``
    neutralizes grok's claude-compat layer: without it the host operator's
    global ``~/.claude/CLAUDE.md``, settings, and hooks leak into the
    sandboxed task. Point it at the empty per-task dir.
    """
    workdir_clean = workdir.rstrip("/")
    iso = _isolation_env(workdir_clean)
    home_dir = iso["HOME"]
    home_local_bin = f"{home_dir}/.local/bin"

    extra = dict(extra_env or {})
    base_path = extra.pop("PATH", None) or os.environ.get(
        "PATH", "/usr/local/bin:/usr/bin:/bin",
    )
    # HOME + PATH first (PATH prepends the per-task .local/bin), then the rest of
    # the isolation identity (GROK_HOME, XDG_*, CLAUDE_CONFIG_DIR) from the SSOT.
    env_map = {
        "HOME": home_dir,
        "PATH": f"{home_local_bin}:{base_path}",
        **{k: v for k, v in iso.items() if k != "HOME"},
    }
    env_assignments: list[str] = [f"{k}={v}" for k, v in env_map.items()]
    for k, v in extra.items():
        env_assignments.append(f"{k}={v}")

    grok_argv = " ".join(shlex.quote(c) for c in [grok_path, *grok_flags])
    log_path = f"{workdir_clean}/optio.log"

    bash_payload = (
        f"cd {shlex.quote(workdir_clean)} && {grok_argv}; rc=$?; "
        f'if [ "$rc" = 0 ]; then echo DONE >> {shlex.quote(log_path)}; '
        f"else printf 'ERROR: grok exited %s\\n' \"$rc\" >> {shlex.quote(log_path)}; fi"
    )
    shell_command = "env " + " ".join(
        shlex.quote(x) for x in [*env_assignments, "bash", "-c", bash_payload]
    )
    return env_assignments, shell_command


# --- flags -----------------------------------------------------------------


# Name of the fail-closed custom sandbox profile optio plants + launches under
# (Stage 8). A CUSTOM profile fails-CLOSED (grok refuses to start if it can't
# apply it); built-in profiles fail-OPEN, so they are unusable for optio.
SANDBOX_PROFILE_NAME = "optio"


def build_grok_flags(
    *,
    permission_mode: str | None,
    allowed_tools: list[str] | None,
    disallowed_tools: list[str] | None,
    model: str | None,
    effort: str | None,
    reasoning_effort: str | None,
    no_leader: bool,
    resuming: bool = False,
    fs_isolation: bool = False,
) -> list[str]:
    """Translate GrokTaskConfig knobs to an argv list.

    Empty lists are treated as None: no flag is emitted. ``--allow`` is
    repeated once per allowed-tools rule (grok's spelling); disallowed tools
    are comma-joined. ``--no-leader`` is emitted when ``no_leader`` so tasks
    never share a grok backend. ``-c`` (continue) is appended when
    ``resuming`` (always False in Stage 0). ``--sandbox optio`` is appended
    when ``fs_isolation`` so grok launches under the fail-closed custom
    Landlock profile (Stage 8). Validation of ``permission_mode`` lives in
    ``GrokTaskConfig.__post_init__``.
    """
    out: list[str] = []
    if permission_mode is not None:
        out += ["--permission-mode", permission_mode]
    if allowed_tools:
        for rule in allowed_tools:
            out += ["--allow", rule]
    if disallowed_tools:
        out += ["--disallowed-tools", ",".join(disallowed_tools)]
    if model:
        out += ["--model", model]
    if effort:
        out += ["--effort", effort]
    if reasoning_effort:
        out += ["--reasoning-effort", reasoning_effort]
    if no_leader:
        out += ["--no-leader"]
    if fs_isolation:
        out += ["--sandbox", SANDBOX_PROFILE_NAME]
    if resuming:
        out += ["-c"]
    return out


# Positional prompt appended to the grok launch when ``auto_start`` is set —
# kicks the agent off without the operator typing anything.
AUTO_START_PROMPT = "Read AGENTS.md and execute the task it describes"


def build_auto_start_args(
    *, auto_start: bool, resuming: bool = False, prompt: str = AUTO_START_PROMPT,
) -> list[str]:
    """Trailing positional prompt for an auto-start FRESH launch.

    Returns ``[prompt]`` when ``auto_start`` and not ``resuming``; empty
    otherwise. On resume the session is continued with ``-c`` and no positional
    is appended: re-issuing the kickoff prompt would start a new task instead of
    resuming the existing conversation.
    """
    return [prompt] if (auto_start and not resuming) else []


def build_resume_notice_args(*, resuming: bool) -> list[str]:
    """Trailing positional that notifies a resumed grok TUI session.

    Returns ``[f"{SYSTEM_MESSAGE_PREFIX}{RESUME_NOTICE}"]`` on resume (grok
    continues with ``-c``, so a trailing positional is processed as a new turn in
    the continued session — mirrors claudecode's ``claude --continue '<text>'``).
    Empty on a fresh launch. This is the PUSH half of resume awareness — it makes
    the agent notice the resume promptly; ``resume.log`` remains the pull-based
    source of truth. Unlike claudecode, grok always teaches the ``System:``
    convention (``_SYSTEM_PREFIX_EXPLAINER`` even when ``host_protocol`` is off),
    so no host_protocol gate is needed. Mutually exclusive with
    :func:`build_auto_start_args` (auto_start only fires on a FRESH launch).
    """
    return [f"{SYSTEM_MESSAGE_PREFIX}{RESUME_NOTICE}"] if resuming else []


# Controlling-tty wrapper for the conversation launch under fs-isolation.
#
# Grok's CUSTOM (fail-closed) Landlock profile applier opens ``/dev/tty`` and
# refuses to start without a controlling terminal. But conversation mode launches
# grok over PIPES with ``start_new_session=True`` (so the ACP JSON-RPC stream on
# stdin/stdout stays byte-clean — a pty would corrupt the framing), which leaves
# grok session-detached with no ``/dev/tty`` → the sandbox fails closed → grok
# exits immediately ("could not apply the 'optio' sandbox profile; refusing to
# start").
#
# This helper is exec'd AS the launched process. It acquires a controlling pty
# WITHOUT routing stdio through it: open a pty purely to ``TIOCSCTTY`` it, then
# ``execvp`` grok with fd 0/1/2 (the JSON-RPC pipes) untouched. ``setsid`` is
# best-effort — under ``start_new_session`` the process is already a session
# leader (raises, caught); over SSH it may not be (succeeds). The pty fds are
# left inheritable/open so the controlling terminal stays valid for grok's
# ``/dev/tty`` open. A command wrapper (not a ``preexec_fn``) is the portable
# seam: it works identically for LocalHost and RemoteHost (a preexec_fn can't run
# on the remote host). Requires ``python3`` on the worker (already an optio-worker
# dependency; the same interpreter that runs the engine).
_CTTY_WRAP_PYTHON = "python3"
_CTTY_WRAP_HELPER = (
    "import os,sys,fcntl,termios,pty\n"
    "try:\n os.setsid()\n"
    "except OSError:\n pass\n"
    "m,s=pty.openpty()\n"
    "os.set_inheritable(m,True)\n"
    "os.set_inheritable(s,True)\n"
    "fcntl.ioctl(s,termios.TIOCSCTTY,0)\n"
    "os.execvp(sys.argv[1],sys.argv[1:])\n"
)


def build_conversation_argv(
    grok_path: str,
    *,
    model: str | None = None,
    reasoning_effort: str | None = None,
    no_leader: bool = True,
    always_approve: bool = False,
    fs_isolation: bool = False,
) -> list[str]:
    """Argv for a headless ACP conversation: ``grok agent [opts] stdio``.

    ``--sandbox`` is a TOP-LEVEL grok flag (``grok [OPTIONS] [COMMAND]``), so
    it precedes the ``agent`` subcommand when ``fs_isolation`` is set (Stage 8
    fail-closed custom Landlock profile). The remaining options belong to
    ``grok agent`` and MUST precede the ``stdio`` subcommand (verified against
    the real CLI ``grok agent --help``): ``--model``, ``--no-leader`` (start a
    fresh agent, never share the leader socket), ``--always-approve``
    (auto-approve every tool — used when no permission gate is wired). No
    tmux/ttyd: the subprocess IS the agent.

    ``--reasoning-effort`` (when set) seeds the INITIAL graded effort at launch,
    mirroring ``--model``; it is then switched live over ACP by
    ``set_control("reasoning_effort", …)``. LIVE-VERIFY: the iframe launch
    (:func:`build_grok_flags`) accepts ``--reasoning-effort`` at the top level;
    the ``grok agent`` subcommand's acceptance of it is a real-binary probe item
    (fold the flag in only if ``grok agent --help`` lists it). Omitted when None,
    so a probe mismatch is a no-op for the common (unset) path.

    When ``fs_isolation`` is set, the whole command is wrapped in the
    controlling-tty helper (:data:`_CTTY_WRAP_HELPER`) — grok's fail-closed
    sandbox needs a ``/dev/tty`` that the piped, session-detached conversation
    launch otherwise lacks. The wrap is gated on ``fs_isolation`` so it appears
    exactly when ``--sandbox`` does.
    """
    argv = [grok_path]
    if fs_isolation:
        argv += ["--sandbox", SANDBOX_PROFILE_NAME]
    argv += ["agent"]
    if model:
        argv += ["--model", model]
    if reasoning_effort:
        argv += ["--reasoning-effort", reasoning_effort]
    if always_approve:
        argv += ["--always-approve"]
    if no_leader:
        argv += ["--no-leader"]
    argv += ["stdio"]
    if fs_isolation:
        argv = [_CTTY_WRAP_PYTHON, "-c", _CTTY_WRAP_HELPER, *argv]
    return argv


# --- tmux / ttyd machinery (adapted verbatim from optio-claudecode) ---------


def build_tmux_session_argv(
    *,
    tmux_path: str,
    grok_path: str,
    workdir: str,
    socket_path: str,
    session_name: str,
    extra_env: dict[str, str] | None,
    grok_flags: list[str],
    local_mode: bool = False,
) -> list[str]:
    """Argv for the detached ``tmux new-session`` that starts grok.

    tmux runs its command argument via ``/bin/sh -c``, so the env + grok
    wrapper is a single trailing shell-string element. The private socket
    (``-S socket_path``) isolates this task's tmux server. ``-x/-y`` give the
    detached pane a sane initial size before any viewer attaches.
    """
    _, shell_command = _build_grok_shell_command(
        grok_path=grok_path,
        workdir=workdir,
        extra_env=extra_env,
        grok_flags=grok_flags,
        local_mode=local_mode,
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

    ttyd does not run grok — it runs ``tmux attach``. ``-m 1`` is dropped so
    multiple viewers can attach to the same session simultaneously (the
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
    return f"/tmp/optio-gk-{digest}.sock"


async def _require_tmux(host: "Host") -> str:
    """Return the absolute path to tmux on the host, or raise a clear error.

    grok runs inside a detached tmux session (so the agent survives viewer
    disconnects); tmux is a worker prerequisite. Resolved via a login shell
    so PATH additions from the worker profile apply. No auto-install: a
    missing tmux fails fast with an actionable message.
    """
    result = await host.run_command("bash -lc 'command -v tmux'")
    path = (result.stdout or "").strip()
    if result.exit_code != 0 or not path:
        raise RuntimeError(
            "tmux is required on the worker for optio-grok (grok runs inside a "
            "detached tmux session). Install tmux (e.g. apt-get install tmux) "
            "or add it to the worker/container image."
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


async def launch_ttyd_with_grok(
    host: "Host",
    *,
    ttyd_path: str,
    grok_path: str,
    bind_iface: str,
    extra_env: dict[str, str] | None,
    grok_flags: list[str],
    ready_timeout_s: float = 30.0,
    env_remove: list[str] | None = None,
    session_name: str = "optio",
) -> "tuple[ProcessHandle, int, str, str]":
    """Start grok in a detached tmux session, then ttyd attaching to it.

    Returns ``(ttyd_handle, port, socket_path, session_name)``. grok runs in
    the tmux session independent of ttyd; the caller awaits tmux-session
    liveness for completion and tears down BOTH the tmux session and ttyd.
    """
    tmux_path = await _require_tmux(host)
    socket_path = _tmux_socket_path(host)

    from optio_host.host import LocalHost
    local_mode = isinstance(host, LocalHost)

    # 1) Start grok detached in tmux. The env scrub (env_remove) must apply
    #    here so the tmux server — which holds grok — does not inherit
    #    scrubbed vars. ``new-session -d`` returns immediately; its exit code
    #    IS checked (via ``_launch_detached_checked``).
    session_argv = build_tmux_session_argv(
        tmux_path=tmux_path,
        grok_path=grok_path,
        workdir=host.workdir,
        socket_path=socket_path,
        session_name=session_name,
        extra_env=extra_env,
        grok_flags=grok_flags,
        local_mode=local_mode,
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
    """Best-effort kill of the per-task tmux session (stops grok)."""
    try:
        await host.run_command(
            f"{shlex.quote(tmux_path)} -S {shlex.quote(socket_path)} "
            f"kill-session -t {shlex.quote(session_name)}"
        )
    except Exception:  # noqa: BLE001
        _LOG.exception("tmux kill-session failed (socket=%s)", socket_path)


def _grok_pgrep_pattern(grok_path: str) -> str:
    """Anchored pgrep/pkill pattern matching ONLY the real grok.

    The real grok execs with the path as the FIRST token of its cmdline
    (argv[0]), whereas the tmux server and the bash/env wrappers carry the
    same path only as a LATER argument. ``^`` excludes them; only a process
    whose cmdline starts with the path matches. ``[g]rok`` keeps pgrep/pkill's
    own cmdline from self-matching.
    """
    body = (
        grok_path[:-4] + "[g]rok" if grok_path.endswith("grok") else grok_path
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


async def kill_grok_processes(
    host: "Host", grok_path: str, *, signal: str = "KILL",
) -> None:
    """Kill the per-task grok via an anchored host-side ``pkill``.

    grok ignores the tmux pane SIGHUP. Best-effort: pkill exits non-zero when
    nothing matches."""
    pattern = _grok_pgrep_pattern(grok_path)
    await host.run_command(f"pkill -{signal} -f {shlex.quote(pattern)} || true")


async def await_grok_gone(
    host: "Host", grok_path: str, *, timeout_s: float = 15.0, poll_s: float = 1.0,
) -> bool:
    """Block (polling once per ``poll_s``) until no process matching the
    per-task ``grok_path`` remains. Bounded by ``timeout_s`` (logs a warning
    and returns False on timeout). Returns True once grok is gone."""
    pattern = _grok_pgrep_pattern(grok_path)
    waited = 0.0
    while True:
        r = await host.run_command(f"pgrep -f {shlex.quote(pattern)} || true")
        if not (r.stdout or "").strip():
            return True
        if waited >= timeout_s:
            _LOG.warning(
                "await_grok_gone: grok still running after %.0fs (path=%s); "
                "proceeding anyway", timeout_s, grok_path,
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
    grok_path: str,
    ttyd_handle: "ProcessHandle | None" = None,
    aggressive: bool,
) -> None:
    """Kill a full grok session tree (ttyd + tmux + grok).

    Four best-effort steps, each isolated so one failure does not abort the
    rest: (1) ttyd via the tracked handle or an anchored socket pkill;
    (2) ``kill-session`` SIGHUPs the tmux pane; (3) ``kill_grok_processes``
    (grok ignores the pane SIGHUP); (4) ``await_grok_gone`` waits for
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
        await kill_grok_processes(host, grok_path)
    except Exception:
        _LOG.exception("kill_grok_processes failed")

    try:
        await await_grok_gone(host, grok_path)
    except Exception:
        _LOG.exception("await_grok_gone failed; proceeding")


async def tmux_session_alive(
    host: "Host", tmux_path: str, socket_path: str, session_name: str,
) -> bool:
    """True while the grok-bearing tmux session exists."""
    r = await host.run_command(
        f"{shlex.quote(tmux_path)} -S {shlex.quote(socket_path)} "
        f"has-session -t {shlex.quote(session_name)}"
    )
    return r.exit_code == 0


async def send_text_to_grok(
    host: "Host", tmux_path: str, tmux_socket: str, tmux_session: str, text: str,
) -> None:
    """Fake-type a message into the grok TUI and submit it.

    Thin wrapper over the shared
    :func:`optio_agents.tmux_input.send_text_to_tmux`, pinned to grok's buffer name
    (``optio-feedback``) and settle (``_SUBMIT_SETTLE_S``). Raises on a tmux
    failure."""
    await _tmux_input.send_text_to_tmux(
        host, tmux_path, tmux_socket, tmux_session, text,
        buffer="optio-feedback", submit_settle=_SUBMIT_SETTLE_S,
    )


async def send_key_to_grok(
    host: "Host", tmux_path: str, tmux_socket: str, tmux_session: str, key: str,
) -> None:
    """Send a single navigation keystroke into the grok TUI (iframe-input empty-box
    TUI nav). Thin wrapper over :func:`optio_agents.tmux_input.send_key_to_tmux`."""
    await _tmux_input.send_key_to_tmux(host, tmux_path, tmux_socket, tmux_session, key)


# --- resume bookkeeping (adapted from optio-claudecode/opencode) ------------


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
