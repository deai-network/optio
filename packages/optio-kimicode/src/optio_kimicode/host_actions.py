"""Kimi-specific actions over a generic Host.

Free functions; each takes a Host or HookContext and uses only generic
primitives (run_command, resolve_host_home, etc.). No isinstance branches.

Ported from ``optio_grok.host_actions``. Carries the host constructor
(:func:`build_host`), the per-task isolation identity (:func:`_isolation_env`),
and the binary install (:func:`ensure_kimicode_installed`): resolve the fork
release via the fork's ``smart-install.sh --check`` and, when missing or stale,
download the zip with optio's own tooling into an evictable cache outside the
workdir, symlinked into ``<workdir>/home/.local/bin/kimi`` (idempotently
re-linked after a resume).
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
    from collections.abc import Awaitable, Callable

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

# The fork's smart-install.sh resolver. In ``--check`` mode it prints exactly one
# line — ``kimi ok`` or ``download <url>`` — deciding, by comparing the on-PATH
# ``kimi --version`` to the latest fork release, whether a fetch is needed. optio
# then downloads ``<url>`` with its OWN tooling (progress/visibility) and unpacks
# the ``kimi`` binary (at the zip root) into the stable cache path
# ``<cache_dir>/kimi``. Replaces the upstream vendor installer: the fork build
# (csillag/kimi-code) carries the iframe-embedding fixes and is version-gated, so
# a stock upstream ``kimi`` on the worker is never adopted.
#
# FORK FLOOR: the live graded reasoning-effort control (the ``reasoning_effort``
# slider projected from the ``thinking`` configOption; see models.parse_all_controls
# + conversation.set_control) needs ``kimi-code >= 0.23.1-csillag.2``
# (``csillag/acp-graded-thinking``), which upgraded the former 2-entry off/on
# thinking toggle into an ordered effort list over ACP. smart-install always
# resolves the latest fork release, so this floor is satisfied by provisioning;
# it is recorded here as the capability gate for the graded thinking surface.
_KIMICODE_SMART_INSTALL_URL = (
    "https://raw.githubusercontent.com/csillag/kimi-code/main/smart-install.sh"
)

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

    ``KIMI_CODE_NO_AUTO_UPDATE=1`` fully disables kimi's update preflight (no
    check, no background install, no prompt — see the fork's
    ``isAutoUpdateDisabledByEnv`` in ``apps/kimi-code/src/cli/update/preflight.ts``).
    A managed wrapper pins the binary itself (Tier-1 install / fork), so a
    self-update would fight our version control and can stall a launch on a
    network probe. Set as a base default; a caller ``extra_env`` may override.
    """
    iso = _isolation_env(workdir)
    home_local_bin = f"{iso['HOME']}/.local/bin"
    extra = dict(extra_env or {})
    base_path = extra.pop("PATH", None) or os.environ.get(
        "PATH", "/usr/local/bin:/usr/bin:/bin",
    )
    return {
        **iso,
        "KIMI_CODE_NO_AUTO_UPDATE": "1",
        "PATH": f"{home_local_bin}:{base_path}",
        **extra,
    }


# Sentinels bracketing the optio-managed ``[[permission.rules]]`` block, so a
# re-run (resume / re-merge) can strip the prior block and re-emit it without
# accumulating duplicate rules or disturbing operator-authored config.
_RULES_BEGIN = "# >>> optio-managed permission rules (do not edit)"
_RULES_END = "# <<< optio-managed permission rules"


def _render_permission_rules(
    allowed_tools: "list[str] | None", disallowed_tools: "list[str] | None",
) -> str:
    """Render the optio-managed ``[[permission.rules]]`` block, or ``""`` when no
    allow/deny tools are configured.

    Each tool becomes one ``[[permission.rules]]`` array-of-tables entry
    (``{decision, pattern}``; a bare tool name matches that tool). Deny rules are
    emitted first so an explicit denial takes precedence over an allow.
    """
    if not allowed_tools and not disallowed_tools:
        return ""
    out: list[str] = [_RULES_BEGIN]
    for tool in disallowed_tools or []:
        out.append("[[permission.rules]]")
        out.append('decision = "deny"')
        out.append(f'pattern = "{tool}"')
    for tool in allowed_tools or []:
        out.append("[[permission.rules]]")
        out.append('decision = "allow"')
        out.append(f'pattern = "{tool}"')
    out.append(_RULES_END)
    return "\n".join(out)


async def write_kimi_config(
    host: "Host",
    workdir: str,
    *,
    permission_mode: str | None,
    allowed_tools: "list[str] | None" = None,
    disallowed_tools: "list[str] | None" = None,
) -> None:
    """Write kimi's permission surface into ``$KIMI_CODE_HOME/config.toml``.

    Two knobs land here, both applied by the daemon to EVERY session it creates —
    the iframe pre-created session and the ACP conversation session alike
    (kimi-code core-impl ``createSession``: ``options.permission ??
    config.defaultPermissionMode``):

    * ``default_permission_mode`` (from ``permission_mode``) — a ROOT-table key.
      ``"yolo"`` auto-approves every action.
    * ``[[permission.rules]]`` array-of-tables (from ``disallowed_tools`` /
      ``allowed_tools``) — one ``{decision, pattern}`` entry per tool (deny first,
      so a denial wins). kimi matches a bare tool name against ``pattern``.

    No-op only when ALL THREE are None/empty. Merge-safe: the existing config
    (e.g. a resume-restored / seeded config.toml with ``[providers…]`` tables) is
    preserved verbatim except our own managed content, which is stripped and
    re-emitted so a re-run does not accumulate duplicates. The ROOT key is placed
    FIRST (before any ``[table]`` header — a bare ``key = val`` after a table
    would be parsed INTO that table and ignored), and the rules block LAST (a new
    array-of-tables at the end is always valid). Creates the file when absent."""
    if permission_mode is None and not allowed_tools and not disallowed_tools:
        return
    home = f"{workdir.rstrip('/')}/home"
    cfg = f"{home}/config.toml"
    try:
        existing = (await host.fetch_bytes_from_host(cfg)).decode("utf-8")
    except FileNotFoundError:
        existing = ""
    # Strip prior optio-managed content: the ``default_permission_mode`` root
    # line and any previously-written rules block (idempotent on resume/re-merge).
    kept: list[str] = []
    in_rules = False
    for raw in existing.splitlines():
        s = raw.strip()
        if s == _RULES_BEGIN:
            in_rules = True
            continue
        if s == _RULES_END:
            in_rules = False
            continue
        if in_rules:
            continue
        if raw.startswith("default_permission_mode"):
            continue
        kept.append(raw)
    body = "\n".join(kept).strip("\n")

    parts: list[str] = []
    if permission_mode is not None:
        parts.append(f'default_permission_mode = "{permission_mode}"')
    if body:
        parts.append(body)
    rules_block = _render_permission_rules(allowed_tools, disallowed_tools)
    if rules_block:
        parts.append(rules_block)
    content = "\n".join(parts).rstrip("\n") + "\n"

    cmd = (
        f"mkdir -p {shlex.quote(home)} && "
        f"printf '%s' {shlex.quote(content)} > {shlex.quote(cfg)}"
    )
    r = await host.run_command(cmd)
    if r.exit_code != 0:
        raise RuntimeError(
            f"writing kimi config.toml (permission surface) failed "
            f"(exit {r.exit_code}): {(r.stderr or '').strip()[:200]}"
        )


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

    ``override`` (``config.install_dir``) wins. Otherwise the worker's real
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


def _path_augmented(cmd: str, cache_dir: str) -> str:
    """Prefix ``cmd`` with an export that prepends the cache dir to PATH, so
    smart-install's internal ``command -v kimi`` finds a binary a prior install
    placed at ``<cache_dir>/kimi`` (the python process often inherits a slim PATH
    that omits it, which would make smart-install falsely say ``download`` and
    reinstall every run). Mirrors opencode's ``_path_augmented``."""
    return f'export PATH={shlex.quote(cache_dir)}:"$PATH"; {cmd}'


async def _smart_install_check(
    host: "Host", *, cache_dir: str,
) -> "tuple[str, str | None]":
    """Run the fork ``smart-install.sh --check`` on ``host`` and parse its
    one-line contract.

    Returns ``("ok", None)`` when the installed ``kimi`` already matches the
    latest fork release, or ``("download", url)`` when missing/stale (``url`` is
    the release-zip to fetch). ``cache_dir`` is prepended to PATH so the script's
    internal ``command -v kimi`` sees ``<cache_dir>/kimi``. Raises on non-zero
    exit or unparseable output.
    """
    cmd = _path_augmented(
        f"curl -fsSL {_KIMICODE_SMART_INSTALL_URL} | bash -s -- --check",
        cache_dir,
    )
    result = await host.run_command(cmd)
    if result.exit_code != 0:
        raise RuntimeError(
            f"smart-install --check failed on host (exit {result.exit_code}): "
            f"{(result.stderr or '').strip()[:200]}"
        )
    line = (result.stdout or "").strip()
    if line == "kimi ok":
        return ("ok", None)
    if line.startswith("download "):
        url = line[len("download "):].strip()
        if not url:
            raise RuntimeError(
                f"smart-install --check returned an empty URL: {result.stdout!r}"
            )
        return ("download", url)
    raise RuntimeError(
        f"smart-install --check returned unexpected output: {result.stdout!r}"
    )


async def _install_kimicode_from_zip(
    host: "Host",
    download: "Callable[[str, str], Awaitable[None]]",
    url: str,
    *,
    cached: str,
) -> None:
    """Fetch the fork release zip from ``url`` with optio's ``download`` and
    unpack the ``kimi`` binary (at the zip ROOT — kimi-code zips carry the
    executable directly, unlike opencode's ``bin/opencode``) into ``<cached>``.

    Engine callers pass ``hook_ctx.download_file`` (a child download task with
    progress in the UI); engine-less callers pass :func:`curl_downloader`.
    Uniform on Local/RemoteHost; cleans up the tempdir in ``finally``.
    """
    cache_dir = os.path.dirname(cached)
    r = await host.run_command("mktemp -d -t optio-kimicode-XXXXXX")
    if r.exit_code != 0:
        raise RuntimeError(
            f"mktemp -d failed (exit {r.exit_code}): {(r.stderr or '').strip()[:200]}"
        )
    tmpdir = r.stdout.strip()
    zip_path = f"{tmpdir}/kimi-code.zip"
    try:
        await download(url, zip_path)

        r = await host.run_command(
            f"unzip -o -q {shlex.quote(zip_path)} -d {shlex.quote(tmpdir)}"
        )
        if r.exit_code != 0:
            raise RuntimeError(
                f"unzip failed (exit {r.exit_code}): {(r.stderr or '').strip()[:200]}"
            )
        r = await host.run_command(f"mkdir -p {shlex.quote(cache_dir)}")
        if r.exit_code != 0:
            raise RuntimeError(
                f"mkdir -p {cache_dir!r} failed (exit {r.exit_code}): "
                f"{(r.stderr or '').strip()[:200]}"
            )
        src = f"{tmpdir}/kimi"
        r = await host.run_command(f"mv -f {shlex.quote(src)} {shlex.quote(cached)}")
        if r.exit_code != 0:
            raise RuntimeError(
                f"mv {src!r} -> {cached!r} failed (exit {r.exit_code}): "
                f"{(r.stderr or '').strip()[:200]}"
            )
        r = await host.run_command(f"chmod +x {shlex.quote(cached)}")
        if r.exit_code != 0:
            raise RuntimeError(
                f"chmod +x {cached!r} failed (exit {r.exit_code}): "
                f"{(r.stderr or '').strip()[:200]}"
            )
    finally:
        # Best-effort cleanup; don't mask a primary exception.
        await host.run_command(f"rm -rf {shlex.quote(tmpdir)}")


async def _seed_cache_from_path(
    host: "Host", *, cache_dir: str, cached: str,
) -> None:
    """smart-install said ``kimi ok`` but ``<cached>`` is absent — a
    fork-versioned ``kimi`` is on PATH elsewhere (e.g. a manual install). Copy it
    (deref) into the cache so the launch symlink has a stable, snapshot-independent
    target. Rare; the common ``ok`` path already has the binary at ``<cached>``."""
    lookup = _path_augmented("command -v kimi", cache_dir)
    r = await host.run_command(f"bash -lc {shlex.quote(lookup)}")
    src = (r.stdout or "").strip()
    if r.exit_code != 0 or not src:
        raise RuntimeError(
            "smart-install reported 'kimi ok' but 'command -v kimi' failed on the "
            f"host (exit {r.exit_code}): {(r.stderr or '').strip()[:200]}"
        )
    r = await host.run_command(
        f"mkdir -p {shlex.quote(cache_dir)} && "
        f"cp -L {shlex.quote(src)} {shlex.quote(cached)} && "
        f"chmod +x {shlex.quote(cached)}"
    )
    if r.exit_code != 0:
        raise RuntimeError(
            f"seeding kimi cache from {src!r} -> {cached!r} failed "
            f"(exit {r.exit_code}): {(r.stderr or '').strip()[:200]}"
        )


async def ensure_kimicode_installed(
    host: "Host",
    workdir: str | None = None,
    *,
    download: "Callable[[str, str], Awaitable[None]] | None" = None,
    report_progress: "Callable | None" = None,
    install_dir: str | None = None,
    install_if_missing: bool = True,
) -> str:
    """Provision the fork ``kimi`` for this task from the optio-owned binary cache.

    The cache dir (:func:`_resolve_kimicode_cache_dir`) lives on the worker
    OUTSIDE any task workdir and never the operator's autoupdating
    ``~/.kimi-code`` — so it stays shared, evictable, and unsnapshotted (it
    survives task teardown; ``Host.archive_workdir`` only tars ``host.workdir``).
    The cache is the single stable home of the binary; the value RETURNED is the
    per-task launch path ``<workdir>/home/.local/bin/kimi`` — a symlink into the
    cache, on the launch PATH (mirrors grok's ``home/.local/bin/grok``).

    Staleness/correctness is delegated entirely to the fork's
    ``smart-install.sh --check`` (:func:`_smart_install_check`), run on EVERY
    call so a newer fork release upgrades the cache. On ``download`` the zip is
    fetched with ``download`` (engine callers pass ``hook_ctx.download_file`` for
    UI progress; when None it defaults to :func:`curl_downloader`) and unpacked
    into ``<cache_dir>/kimi``. Because ``--check`` version-gates on the fork
    version string, a stock upstream ``kimi`` on the worker is never adopted (it
    would lack the iframe-embedding fixes).

    ``workdir`` defaults to ``host.workdir``. Host-based (no HookContext) —
    usable from the engine-free ``verify`` path. Idempotent on a re-call (``ok``
    → just re-links the task path), which is how resume re-establishes the launch
    symlink after ``restore_snapshot`` wipes it. Raises when an install is needed
    but ``install_if_missing=False``.
    """
    workdir = (workdir if workdir is not None else host.workdir).rstrip("/")
    cache_dir = await _resolve_kimicode_cache_dir(host, install_dir)
    cached = f"{cache_dir}/kimi"
    if download is None:
        download = curl_downloader(host)

    if report_progress is not None:
        report_progress(None, "Checking kimi-code installation…")
    kind, url = await _smart_install_check(host, cache_dir=cache_dir)

    if kind == "ok":
        # Normally the current binary IS <cached>; only seed when a fork-versioned
        # kimi is on PATH elsewhere and the cache is empty.
        probe = await host.run_command(
            f"[ -x {shlex.quote(cached)} ] && echo OK || true"
        )
        if "OK" not in (probe.stdout or ""):
            await _seed_cache_from_path(host, cache_dir=cache_dir, cached=cached)
        _LOG.info("ensure_kimicode_installed: up-to-date (%s)", cached)
    else:  # "download"
        if not install_if_missing:
            raise RuntimeError(
                "kimi-code is missing or stale on the host and "
                "install_if_missing=False was requested."
            )
        assert url is not None  # _smart_install_check guarantees
        if report_progress is not None:
            report_progress(None, "Installing kimi-code…")
        await _install_kimicode_from_zip(host, download, url, cached=cached)
        _LOG.info("ensure_kimicode_installed: installed fork binary into %s", cached)

    return await _link_kimicode_into_task(host, workdir, cached)


def curl_downloader(host: "Host") -> "Callable[[str, str], Awaitable[None]]":
    """Context-free downloader for engine-less callers (verify / host-only): fetch
    a URL to a host path via curl on the host itself, vs the engine's child-task
    ``download_file``. Mirrors opencode's ``curl_downloader``."""
    async def download(url: str, dest: str) -> None:
        r = await host.run_command(
            f"curl -fsSL {shlex.quote(url)} -o {shlex.quote(dest)}"
        )
        if r.exit_code != 0:
            raise RuntimeError(
                f"curl download failed (exit {r.exit_code}): "
                f"{(r.stderr or '').strip()[:200]}"
            )
    return download


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
    from optio_agents import fs_grants

    cache_dir = await _resolve_kimicode_cache_dir(host, config.install_dir)
    # ``~/`` caller extras expand against the REAL host home (the kimi process
    # runs under an isolated $HOME, and grants reach claustrum verbatim).
    host_home = (
        await host.resolve_host_home() if config.extra_allowed_dirs else None
    )
    grants = fs_grants.build_grant_flags(
        workdir=host.workdir,
        engine_cache_dir=cache_dir,
        extra_allowed_dirs=config.extra_allowed_dirs,
        host_home=host_home,
    )
    return claustrum.build_claustrum_wrap(claustrum_path, grants)


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
