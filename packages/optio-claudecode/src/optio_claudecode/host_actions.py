"""Claudecode-specific actions over a generic Host.

Free functions; each takes a Host or HookContext and uses only generic
primitives (run_command, resolve_host_home, etc.). No isinstance branches.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import shlex
from typing import TYPE_CHECKING, Any

from optio_agents import RESUME_NOTICE, SYSTEM_MESSAGE_PREFIX

if TYPE_CHECKING:
    from optio_agents import HookContextProtocol
    from optio_host import Host
    from optio_host.host import ProcessHandle

_LOG = logging.getLogger(__name__)


# ttyd's ready banner takes a few forms across versions:
#   * 1.7.x with lws logging:  "N:  Listening on port: 33449"
#   * older builds:            "Listening on port 7681"
#   * some forks log a URL:    "[INFO] listening on http://127.0.0.1:7681/"
# The `port[\s:]+` branch covers the first two (colon OR whitespace
# between "port" and the digits). The URL branch covers the third.
# Both expose the captured port number as the first non-None group.
_TTYD_READY_RE = re.compile(
    r"(?:port[\s:]+(\d+))|(?:http://[^\s]+?:(\d+)(?:/|\s|$))"
)


_CLAUDE_INSTALL_URL = "https://claude.ai/install.sh"

# Settle (seconds) between pasting a message into the claude TUI and sending
# Enter. Without it the Enter is glued to the paste and claude treats the CR
# as a newline inside the input box instead of a submit (see
# send_text_to_claude). A shell-literal string (used in a `sleep` invocation).
_SUBMIT_SETTLE_S = "1.0"

# The optio-owned claude version cache lives on the WORKER, never in the host
# user's ~/.local/~/.claude. Default: ${XDG_CACHE_HOME:-$HOME/.cache}/optio-claudecode/versions.
_CACHE_DIR_SHELL_DEFAULT = (
    '${OPTIO_CLAUDECODE_CACHE_DIR:-${XDG_CACHE_HOME:-$HOME/.cache}/optio-claudecode/versions}'
)

# Pinned ttyd version. Update with care; the URL pattern below is
# tsl0922/ttyd's release-asset convention as of 1.7.x.
_TTYD_VERSION = "1.7.7"
_TTYD_RELEASE_BASE = (
    f"https://github.com/tsl0922/ttyd/releases/download/{_TTYD_VERSION}"
)

# ttyd still installs into the worker home's ``.local/bin`` (only claude moved
# to the optio version cache). Isolating ttyd the same way is a follow-up.
_DEFAULT_INSTALL_SUBDIR = ".local/bin"


async def _resolve_install_dir(host: "Host", install_dir: str | None) -> str:
    """Return ``install_dir`` if given, else ``<host_home>/.local/bin`` (ttyd)."""
    if install_dir is not None:
        return install_dir
    host_home = await host.resolve_host_home()
    return f"{host_home}/{_DEFAULT_INSTALL_SUBDIR}"


async def _resolve_cache_dir(host: "Host", override: str | None) -> str:
    """Resolve the claude version-cache dir as an absolute path on the worker.

    ``override`` (``config.claude_install_dir``) wins. Otherwise the worker's
    ``OPTIO_CLAUDECODE_CACHE_DIR`` / ``XDG_CACHE_HOME`` / ``$HOME`` decide it —
    resolved via a shell echo so RemoteHost gets the remote location.
    """
    if override is not None:
        return override.rstrip("/")
    r = await host.run_command(f'printf %s "{_CACHE_DIR_SHELL_DEFAULT}"')
    path = r.stdout.strip()
    if r.exit_code != 0 or not path:
        raise RuntimeError(
            f"failed to resolve claude cache dir on host "
            f"(exit {r.exit_code}): {r.stderr.strip()[:200]}"
        )
    return path.rstrip("/")


async def _claude_version_ok(host: "Host", claude_path: str) -> bool:
    """True iff ``claude_path`` is executable and prints a Claude Code version."""
    cmd = f"[ -x {shlex.quote(claude_path)} ] && {shlex.quote(claude_path)} --version"
    result = await host.run_command(cmd)
    return result.exit_code == 0 and "Claude Code" in result.stdout


async def _newest_cached_version(host: "Host", cache_dir: str) -> str | None:
    """Return the highest-semver *valid* version filename in the cache, or None.

    Only non-empty, executable regular files count: claude's autoupdater (active
    in a session) can leave a 0-byte partial when killed at teardown, and a stub
    named like the newest version must not be picked as a cache hit (it fails
    `claude --version` and triggers a full reinstall every launch)."""
    r = await host.run_command(
        f"find {shlex.quote(cache_dir)} -maxdepth 1 -type f -size +0c -perm -u+x "
        f"-printf '%f\\n' 2>/dev/null | sort -V | tail -1"
    )
    name = r.stdout.strip()
    return name or None


async def ensure_claude_installed(
    hook_ctx: "HookContextProtocol",
    *,
    install_if_missing: bool = True,
    install_dir: str | None = None,
    progress_label: str = "Preparing Claude Code…",
) -> str:
    """Provision claude for this task from the shared, optio-owned version cache.

    The binary lives in an optio cache dir on the worker (never the host
    ~/.local/~/.claude). Per task we symlink the isolated home's
    ``.local/share/claude/versions`` at that cache, so claude's installer and
    autoupdater write version binaries *through* the symlink into the cache.

    - cache miss (+ install_if_missing) → run vendor install.sh with
      HOME=<workdir>/home, which writes through the symlink into the cache and
      creates home/.local/bin/claude.
    - cache hit → point home/.local/bin/claude at the newest cached version
      (no reinstall).
    - cache miss + install disabled → raise.

    ``install_dir`` is the cache-dir override (config.claude_install_dir).
    Returns the per-task launch path ``<workdir>/home/.local/bin/claude``.
    """
    host = hook_ctx._host
    workdir = host.workdir.rstrip("/")
    home = f"{workdir}/home"
    bin_dir = f"{home}/.local/bin"
    bin_claude = f"{bin_dir}/claude"
    share_claude = f"{home}/.local/share/claude"
    versions_link = f"{share_claude}/versions"

    cache_dir = await _resolve_cache_dir(host, install_dir)
    _LOG.info(
        "ensure_claude_installed: resolved cache_dir=%s (override=%r) workdir=%s",
        cache_dir, install_dir, workdir,
    )

    hook_ctx.report_progress(None, progress_label)
    setup = await host.run_command(
        f"mkdir -p {shlex.quote(cache_dir)} {shlex.quote(share_claude)} "
        f"{shlex.quote(bin_dir)} && "
        f"ln -sfn {shlex.quote(cache_dir)} {shlex.quote(versions_link)}"
    )
    if setup.exit_code != 0:
        raise RuntimeError(
            f"claude runtime prep (mkdir/symlink) failed (exit {setup.exit_code}): "
            f"{setup.stderr.strip()[:200]}"
        )

    newest = await _newest_cached_version(host, cache_dir)
    _LOG.info("ensure_claude_installed: newest cached version in %s = %r", cache_dir, newest)
    if newest is not None:
        # Cache hit — point the per-task bin at the newest cached version.
        # Path goes through the versions symlink so it resolves into the cache.
        await host.run_command(
            f"ln -sfn {shlex.quote(versions_link + '/' + newest)} {shlex.quote(bin_claude)}"
        )
        if await _claude_version_ok(host, bin_claude):
            _LOG.info("ensure_claude_installed: cache HIT (%s) -> no download", newest)
            return bin_claude
        _LOG.warning(
            "ensure_claude_installed: cached version %s present but _claude_version_ok "
            "FAILED -> falling through to reinstall", newest,
        )
        # Fall through to (re)install if the cached version is unusable.

    if not install_if_missing:
        raise RuntimeError(
            f"claude not present in cache {cache_dir!r} on host and "
            f"install_if_missing=False; nothing to do."
        )

    _LOG.warning(
        "ensure_claude_installed: cache MISS (cache_dir=%s newest=%r) -> running vendor "
        "install.sh (downloads, ~1min). If this repeats every launch, the cache_dir is "
        "wrong/ephemeral or being wiped.", cache_dir, newest,
    )
    hook_ctx.report_progress(None, "Installing Claude Code…")
    install_cmd = (
        f"env HOME={shlex.quote(home)} sh -c "
        f"{shlex.quote(f'curl -fsSL {_CLAUDE_INSTALL_URL} | bash')}"
    )
    result = await host.run_command(install_cmd)
    if result.exit_code != 0:
        raise RuntimeError(
            f"claude install failed on host (exit {result.exit_code}): "
            f"{result.stderr.strip()[:300]}"
        )
    if not await _claude_version_ok(host, bin_claude):
        raise RuntimeError(
            f"claude install reported success but {bin_claude!r} is still not "
            f"executable. Inspect the cache {cache_dir!r} and "
            f"{versions_link!r} on the host for diagnostics."
        )
    return bin_claude


async def _ttyd_present(host: "Host", ttyd_path: str) -> bool:
    cmd = f"[ -x {shlex.quote(ttyd_path)} ] && {shlex.quote(ttyd_path)} --version"
    result = await host.run_command(cmd)
    # ttyd writes its version banner to stdout OR stderr depending on
    # version — accept either.
    blob = (result.stdout or "") + (result.stderr or "")
    return result.exit_code == 0 and "ttyd" in blob.lower()


async def _require_tmux(host: "Host") -> str:
    """Return the absolute path to tmux on the host, or raise a clear error.

    claudecode runs claude inside a detached tmux session (so the agent
    survives viewer disconnects); tmux is a worker prerequisite. Resolved via a
    login shell so PATH additions from the worker profile apply. No
    auto-install: a missing tmux fails fast with an actionable message.
    """
    result = await host.run_command("bash -lc 'command -v tmux'")
    path = (result.stdout or "").strip()
    if result.exit_code != 0 or not path:
        raise RuntimeError(
            "tmux is required on the worker for optio-claudecode (claude runs "
            "inside a detached tmux session). Install tmux (e.g. apt-get install "
            "tmux) or add it to the worker/container image."
        )
    return path


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


async def plant_home_files(
    host: "Host",
    *,
    credentials_json: dict[str, Any] | bytes | str | None,
    claude_config: dict[str, Any] | None,
) -> None:
    """Plant per-task claude state under <workdir>/home/.claude/.

    Creates <workdir>/home/.claude/ (mkdir -p), writes the credentials
    payload and settings.json when supplied, and chmod-600s the
    credentials file. ``credentials_json`` accepts a dict (re-encoded as
    JSON), bytes (decoded as UTF-8 verbatim), or a string (written
    verbatim).
    """
    workdir = host.workdir.rstrip("/")
    home_claude_rel = "home/.claude"
    home_claude_abs = f"{workdir}/{home_claude_rel}"

    r = await host.run_command(f"mkdir -p {shlex.quote(home_claude_abs)}")
    if r.exit_code != 0:
        raise RuntimeError(
            f"mkdir -p {home_claude_abs!r} failed (exit {r.exit_code}): "
            f"{r.stderr.strip()[:200]}"
        )

    if credentials_json is not None:
        if isinstance(credentials_json, dict):
            payload = json.dumps(credentials_json)
        elif isinstance(credentials_json, bytes):
            payload = credentials_json.decode("utf-8")
        else:
            payload = credentials_json
        cred_rel = f"{home_claude_rel}/.credentials.json"
        await host.write_text(cred_rel, payload)
        cred_abs = f"{workdir}/{cred_rel}"
        r = await host.run_command(f"chmod 600 {shlex.quote(cred_abs)}")
        if r.exit_code != 0:
            raise RuntimeError(
                f"chmod 600 {cred_abs!r} failed (exit {r.exit_code}): "
                f"{r.stderr.strip()[:200]}"
            )

    if claude_config is not None:
        settings_rel = f"{home_claude_rel}/settings.json"
        await host.write_text(settings_rel, json.dumps(claude_config, indent=2))


def _build_claude_shell_command(
    *,
    claude_path: str,
    workdir: str,
    extra_env: dict[str, str] | None,
    claude_flags: list[str],
    local_mode: bool = False,
) -> tuple[list[str], str]:
    """Return (env_assignments, shell_command).

    ``env_assignments`` is the list of ``KEY=VALUE`` strings (HOME, PATH,
    extras). ``shell_command`` is the full ``env <assignments> bash -c
    <payload>`` string that runs claude under HOME-isolation, optionally
    applies the OPTIO_CLAUDECODE_NETNS seal, and appends DONE/ERROR to
    optio.log when claude exits. Consumed by build_tmux_session_argv (claude
    now runs inside the detached tmux session, not as a direct ttyd child).

    The netns seal is applied ONLY when ``local_mode`` is True. The seal
    isolates claude's OAuth loopback callback inside a private network
    namespace — meaningful only for a local session. Over SSH there is no
    localhost to seal and the netns tools (pasta/unshare) may be absent on the
    remote, so the seal is skipped even if OPTIO_CLAUDECODE_NETNS is set in the
    engine env.
    """
    workdir_clean = workdir.rstrip("/")
    home_dir = f"{workdir_clean}/home"
    home_local_bin = f"{home_dir}/.local/bin"

    extra = dict(extra_env or {})
    base_path = extra.pop("PATH", None) or os.environ.get(
        "PATH", "/usr/local/bin:/usr/bin:/bin",
    )
    env_assignments: list[str] = [
        f"HOME={home_dir}",
        f"PATH={home_local_bin}:{base_path}",
    ]
    for k, v in extra.items():
        env_assignments.append(f"{k}={v}")

    netns_wrap = os.environ.get("OPTIO_CLAUDECODE_NETNS", "").strip()
    if netns_wrap and local_mode:
        inner = "IS_SANDBOX=1 " + " ".join(
            shlex.quote(c) for c in [claude_path, *claude_flags]
        )
        claude_cmd = [*shlex.split(netns_wrap), "bash", "-c", inner]
        _LOG.info(
            "OPTIO_CLAUDECODE_NETNS active (local mode) — claude wrapped via %r "
            "(bash -c, IS_SANDBOX=1, flags kept)", netns_wrap,
        )
    else:
        claude_cmd = [claude_path, *claude_flags]
        if netns_wrap and not local_mode:
            _LOG.info(
                "OPTIO_CLAUDECODE_NETNS set (value=%r) but host is remote — seal "
                "skipped (no localhost to seal over SSH; netns tools may be "
                "absent on the remote)", netns_wrap,
            )
    claude_argv = " ".join(shlex.quote(c) for c in claude_cmd)
    log_path = f"{workdir_clean}/optio.log"
    bash_payload = (
        f"cd {shlex.quote(workdir_clean)} && {claude_argv}; rc=$?; "
        f'if [ "$rc" = 0 ]; then echo DONE >> {shlex.quote(log_path)}; '
        f"else printf 'ERROR: claude exited %s\\n' \"$rc\" >> {shlex.quote(log_path)}; fi"
    )
    shell_command = "env " + " ".join(
        shlex.quote(x) for x in [*env_assignments, "bash", "-c", bash_payload]
    )
    return env_assignments, shell_command


def build_tmux_session_argv(
    *,
    tmux_path: str,
    claude_path: str,
    workdir: str,
    socket_path: str,
    session_name: str,
    extra_env: dict[str, str] | None,
    claude_flags: list[str],
    local_mode: bool = False,
) -> list[str]:
    """Argv for the detached ``tmux new-session`` that starts claude.

    tmux runs its command argument via ``/bin/sh -c``, so the env + claude
    wrapper is a single trailing shell-string element. The private socket
    (``-S socket_path``) isolates this task's tmux server. ``-x/-y`` give the
    detached pane a sane initial size before any viewer attaches.

    ``local_mode`` gates the OPTIO_CLAUDECODE_NETNS seal (local sessions only;
    see _build_claude_shell_command).
    """
    _, shell_command = _build_claude_shell_command(
        claude_path=claude_path,
        workdir=workdir,
        extra_env=extra_env,
        claude_flags=claude_flags,
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

    ttyd no longer runs claude — it runs ``tmux attach``. ``-m 1`` is dropped so
    multiple viewers can attach to the same session simultaneously (the agent's
    life is owned by the tmux session, not by any connection).

    ``-t disableLeaveAlert=true`` turns off ttyd's web client ``beforeunload``
    prompt ("leave? data may be lost"). With tmux persistence that warning is
    false — leaving the page only detaches a viewer; the session keeps running.
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


async def launch_ttyd_with_claude(
    host: "Host",
    *,
    ttyd_path: str,
    claude_path: str,
    bind_iface: str,
    extra_env: dict[str, str] | None,
    claude_flags: list[str],
    ready_timeout_s: float = 30.0,
    env_remove: list[str] | None = None,
    session_name: str = "optio",
) -> "tuple[ProcessHandle, int, str, str]":
    """Start claude in a detached tmux session, then ttyd attaching to it.

    Returns ``(ttyd_handle, port, socket_path, session_name)``. claude runs in
    the tmux session independent of ttyd; the caller awaits tmux-session
    liveness for completion and tears down BOTH the tmux session and ttyd.
    """
    tmux_path = await _require_tmux(host)
    socket_path = f"{host.workdir.rstrip('/')}/tmux.sock"

    # The netns OAuth-loopback seal applies to LOCAL sessions only — over SSH
    # there is no localhost to seal and the netns tools may be absent remotely.
    from optio_host.host import LocalHost
    local_mode = isinstance(host, LocalHost)

    # 1) Start claude detached in tmux. The env scrub (env_remove) must apply
    #    here so the tmux server — which holds claude — does not inherit
    #    scrubbed vars. ``run_command`` has no ``env_remove`` kwarg (only
    #    ``cwd``/``env``), so we go through ``launch_subprocess`` (which does)
    #    and await its exit — ``new-session -d`` returns immediately.
    session_argv = build_tmux_session_argv(
        tmux_path=tmux_path,
        claude_path=claude_path,
        workdir=host.workdir,
        socket_path=socket_path,
        session_name=session_name,
        extra_env=extra_env,
        claude_flags=claude_flags,
        local_mode=local_mode,
    )
    session_cmd = " ".join(shlex.quote(a) for a in session_argv)
    start_handle = await host.launch_subprocess(session_cmd, env_remove=env_remove)
    # new-session -d returns at once; drain stdout to await the process exit.
    start_output: list[str] = []
    async for raw in start_handle.stdout:
        line = (
            raw.decode("utf-8", errors="replace")
            if isinstance(raw, bytes) else str(raw)
        )
        start_output.append(line)

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
    """Best-effort kill of the per-task tmux session (stops claude)."""
    try:
        await host.run_command(
            f"{shlex.quote(tmux_path)} -S {shlex.quote(socket_path)} "
            f"kill-session -t {shlex.quote(session_name)}"
        )
    except Exception:  # noqa: BLE001
        _LOG.exception("tmux kill-session failed (socket=%s)", socket_path)


def _claude_pgrep_pattern(claude_path: str) -> str:
    """Anchored pgrep/pkill pattern matching ONLY the real claude.

    The real claude execs with the path as the FIRST token of its cmdline
    (argv[0]), whereas the tmux server, pasta, and the bash/env wrappers all
    carry the same path only as a LATER argument (the tmux server's argv embeds
    the whole launch command string). An unanchored ``pgrep -f <path>`` matched
    those long-lived wrappers and false-waited the full timeout under netns.
    ``^`` excludes them; only a process whose cmdline starts with the path
    matches. ``[c]laude`` keeps pgrep/pkill's own cmdline from self-matching.
    """
    body = (
        claude_path[:-6] + "[c]laude" if claude_path.endswith("claude") else claude_path
    )
    return "^" + body


async def kill_claude_processes(
    host: "Host", claude_path: str, *, signal: str = "KILL",
) -> None:
    """Force the per-task claude process tree to exit.

    Teardown SIGKILLs ttyd and kill-sessions tmux, but claude runs under pasta
    in its own process group and ignores the tmux pane's SIGHUP, so it is
    orphaned and survives — ``await_claude_gone`` then waits for a process
    nothing kills, blowing the cancel grace. pasta isolates only the network
    namespace (not PID), so a host-side ``pkill`` on the anchored argv[0] path
    reaches it. Best-effort: pkill exits non-zero when nothing matches.
    """
    pattern = _claude_pgrep_pattern(claude_path)
    await host.run_command(f"pkill -{signal} -f {shlex.quote(pattern)} || true")


async def await_claude_gone(
    host: "Host", claude_path: str, *, timeout_s: float = 15.0, poll_s: float = 1.0,
) -> bool:
    """Block (polling once per ``poll_s``) until no process matching the
    per-task ``claude_path`` remains.

    Called after the claude tree is killed and before snapshot capture so the
    tar of ``home/.claude`` reads a quiescent tree. Scoped to ``claude_path``
    (unique per task workdir) so it ignores unrelated claude processes. Bounded
    by ``timeout_s``: on timeout it logs a warning and returns False (the strict
    tar exit check in ``_archive_home_claude`` is the backstop). Returns True
    once claude is gone.
    """
    pattern = _claude_pgrep_pattern(claude_path)
    waited = 0.0
    while True:
        r = await host.run_command(f"pgrep -f {shlex.quote(pattern)} || true")
        if not (r.stdout or "").strip():
            return True
        if waited >= timeout_s:
            _LOG.warning(
                "await_claude_gone: claude still running after %.0fs (path=%s); "
                "proceeding to capture anyway", timeout_s, claude_path,
            )
            return False
        await asyncio.sleep(poll_s)
        waited += poll_s


async def tmux_session_alive(
    host: "Host", tmux_path: str, socket_path: str, session_name: str,
) -> bool:
    """True while the claude-bearing tmux session exists."""
    r = await host.run_command(
        f"{shlex.quote(tmux_path)} -S {shlex.quote(socket_path)} "
        f"has-session -t {shlex.quote(session_name)}"
    )
    return r.exit_code == 0


def build_claude_flags(
    *,
    permission_mode: str | None,
    allowed_tools: list[str] | None,
    disallowed_tools: list[str] | None,
    resuming: bool = False,
) -> list[str]:
    """Translate ClaudeCodeTaskConfig permission knobs to an argv list.

    Empty lists are treated as None: no flag is emitted.
    When ``resuming`` is True, ``--continue`` is appended so claude picks
    up the most recent conversation in ``home/.claude/projects/<cwd>/``.
    Validation of ``permission_mode`` values lives in
    ``ClaudeCodeTaskConfig.__post_init__``.
    """
    out: list[str] = []
    if permission_mode is not None:
        out += ["--permission-mode", permission_mode]
    if allowed_tools:
        out += ["--allowed-tools", ",".join(allowed_tools)]
    if disallowed_tools:
        out += ["--disallowed-tools", ",".join(disallowed_tools)]
    if resuming:
        out += ["--continue"]
    return out


# Positional prompt appended to the claude launch when ``auto_start`` is set —
# kicks the agent off without the operator typing anything.
AUTO_START_PROMPT = "Read CLAUDE.md and execute the task it describes"


def build_auto_start_args(*, auto_start: bool, resuming: bool) -> list[str]:
    """Trailing positional prompt for an auto-start fresh launch.

    Returns ``[AUTO_START_PROMPT]`` only on a genuine fresh launch (``auto_start``
    set and NOT resuming); empty otherwise. Gated on ``resuming`` (snapshot
    restored), NOT on ``--continue``/transcript presence: a no-transcript resume
    still launches without ``--continue`` (D3 safety), but must NOT re-issue the
    kickoff — doing so would restart the task instead of leaving the restored
    session as-is.
    """
    if auto_start and not resuming:
        return [AUTO_START_PROMPT]
    return []


def build_resume_notice_args(*, resuming: bool, pass_continue: bool) -> list[str]:
    """Trailing positional prompt that notifies a resumed claude session.

    Returns ``[f"{SYSTEM_MESSAGE_PREFIX}{RESUME_NOTICE}"]`` ONLY when the
    session is both resuming AND continuing a transcript (``pass_continue``).
    The positional only *appends* to the restored conversation when claude is
    launched with ``--continue`` (verified: ``claude --continue '<text>'``
    resumes and processes the text as a new turn). On a no-transcript resume
    there is nothing to append to, so no notice is sent. Empty otherwise."""
    if resuming and pass_continue:
        return [f"{SYSTEM_MESSAGE_PREFIX}{RESUME_NOTICE}"]
    return []


async def send_text_to_claude(
    host: "Host", tmux_path: str, tmux_socket: str, tmux_session: str, text: str,
) -> None:
    """Fake-type a message into the claude TUI and submit it.

    Uses ``set-buffer`` + ``paste-buffer`` (robust for arbitrary text incl.
    spaces, which ``send-keys -l`` would mistreat), then — after a brief
    settle — a single ``Enter`` to submit. The settle is essential: an
    ``Enter`` sent in the same burst as the paste lands while claude is still
    settling the (bracketed) paste, so claude consumes the CR as a literal
    newline *inside* the input box rather than a submit — the message then
    sits unsent. Decoupling the Enter by ``_SUBMIT_SETTLE_S`` makes it a
    distinct keypress that submits. Raises on a tmux failure (the caller
    treats that as 'agent unreachable', which ``send_to_agent`` converts to
    False)."""
    s = shlex.quote(tmux_socket)
    sess = shlex.quote(tmux_session)
    tp = shlex.quote(tmux_path)
    buf = "optio-feedback"
    cmd = (
        f"{tp} -S {s} set-buffer -b {buf} -- {shlex.quote(text)} && "
        f"{tp} -S {s} paste-buffer -d -b {buf} -t {sess} && "
        f"sleep {_SUBMIT_SETTLE_S} && "
        f"{tp} -S {s} send-keys -t {sess} Enter"
    )
    result = await host.run_command(cmd)
    if result.exit_code != 0:
        raise RuntimeError(
            f"send_text_to_claude: tmux injection failed "
            f"(exit {result.exit_code}): {result.stderr!r}"
        )


def build_focus_mode(
    *, focus_mode: bool, claude_config: "dict[str, Any] | None",
) -> "tuple[dict[str, Any] | None, dict[str, str]]":
    """Layer the quiet-TUI knobs onto settings + launch env when focus_mode is set.

    Returns ``(effective_claude_config, env_additions)``. When on:
      - settings.json gains ``tui=fullscreen`` + ``viewMode=focus`` (focus view
        collapses tool calls to one-line summaries; requires fullscreen TUI),
      - the launch env gains ``CLAUDE_CODE_NO_FLICKER=1`` (enables fullscreen
        rendering, the prerequisite for focus view).
    Off → passthrough (config unchanged, no env additions).
    """
    if not focus_mode:
        return claude_config, {}
    merged = {**(claude_config or {}), "tui": "fullscreen", "viewMode": "focus"}
    return merged, {"CLAUDE_CODE_NO_FLICKER": "1"}
