"""Cursor-specific actions over a generic Host.

Free functions; each takes a Host or HookContext and uses only generic
primitives (run_command, resolve_host_home, etc.). No isinstance branches.

Adapted from optio-grok's ``host_actions`` (itself lifted from
optio-claudecode). Stage 0 drops the binary cache (Stage 5), seeds,
conversation mode, and resume bookkeeping; the tmux/ttyd machinery and the
ttyd installer are copied with only ``grok`` → ``cursor`` renames.
"""

from __future__ import annotations

import asyncio
import logging
import os
import re
import shlex
from typing import TYPE_CHECKING

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


async def _resolve_install_dir(host: "Host", install_dir: str | None) -> str:
    """Return ``install_dir`` if given, else ``<host_home>/.local/bin`` (ttyd)."""
    if install_dir is not None:
        return install_dir
    host_home = await host.resolve_host_home()
    return f"{host_home}/{_DEFAULT_INSTALL_SUBDIR}"


# --- cursor-agent resolution (Stage 0: no binary cache/download; Stage 5) ---


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
    agent CLI is ``cursor-agent``.
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
            f"(cursor_install_dir={install_dir!r})."
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
        "cursor-agent'). Stage 0 has no auto-install (the binary cache is a "
        "later stage) — install cursor-agent manually (e.g. `curl "
        "https://cursor.com/install -fsS | bash` puts it in ~/.local/bin) "
        "or pass cursor_install_dir."
    )


async def ensure_cursor_installed(
    hook_ctx: "HookContextProtocol",
    *,
    install_if_missing: bool = True,
    install_dir: str | None = None,
) -> str:
    """Resolve ``cursor-agent`` for this task (Stage 0: no install/cache).

    Delegates to :func:`resolve_cursor` on the host behind ``hook_ctx``.
    Returns the absolute path of the ``cursor-agent`` binary. The
    optio-owned binary cache (grok/claudecode style) is a later stage.
    """
    host = hook_ctx._host
    hook_ctx.report_progress(None, "Locating cursor-agent…")
    return await resolve_cursor(
        host, install_dir=install_dir, install_if_missing=install_if_missing,
    )


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


def _isolation_env(workdir: str) -> dict[str, str]:
    """Single source of truth for a task's HOME/XDG agent identity.

    Every cursor launch (tmux iframe via ``_build_cursor_shell_command``)
    derives its environment from this map so isolation is identical across
    launch paths. Four explicit keys, all rooted at ``<workdir>/home``:

    - ``HOME`` — cursor derives ``~/.cursor`` and ``~/.cache`` from ``$HOME``
      (verified), so the per-task home captures all of its state.
    - ``XDG_CONFIG_HOME`` / ``XDG_DATA_HOME`` / ``XDG_CACHE_HOME`` — pin the
      XDG base dirs into the task home so no XDG-respecting tool reaches the
      operator's ``~/.config`` / ``~/.cache``.

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
    }


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
) -> tuple[list[str], str]:
    """Return (env_assignments, shell_command).

    ``env_assignments`` is the list of ``KEY=VALUE`` strings (HOME, PATH,
    XDG_*, NO_OPEN_BROWSER, extras). ``shell_command`` is the full
    ``env <assignments> bash -c <payload>`` string that runs cursor-agent
    under HOME-isolation and appends DONE/ERROR to optio.log when it exits.
    Consumed by build_tmux_session_argv (cursor runs inside the detached tmux
    session, not as a direct ttyd child).

    ``NO_OPEN_BROWSER=1`` makes cursor's login flow PRINT its auth URL
    instead of spawning a host browser; the printed URL is surfaced to the
    operator via the ``BROWSER:`` protocol keyword (browser="redirect").
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
    # of the isolation identity (XDG_*) from the SSOT, then NO_OPEN_BROWSER.
    env_map = {
        "HOME": home_dir,
        "PATH": f"{home_local_bin}:{base_path}",
        **{k: v for k, v in iso.items() if k != "HOME"},
        "NO_OPEN_BROWSER": "1",
    }
    env_assignments: list[str] = [f"{k}={v}" for k, v in env_map.items()]
    for k, v in extra.items():
        env_assignments.append(f"{k}={v}")

    cursor_argv = " ".join(shlex.quote(c) for c in [cursor_path, *cursor_flags])
    log_path = f"{workdir_clean}/optio.log"

    bash_payload = (
        f"cd {shlex.quote(workdir_clean)} && {cursor_argv}; rc=$?; "
        f'if [ "$rc" = 0 ]; then echo DONE >> {shlex.quote(log_path)}; '
        f"else printf 'ERROR: cursor-agent exited %s\\n' \"$rc\" >> {shlex.quote(log_path)}; fi"
    )
    shell_command = "env " + " ".join(
        shlex.quote(x) for x in [*env_assignments, "bash", "-c", bash_payload]
    )
    return env_assignments, shell_command


# --- flags + config-planted permissions --------------------------------------


def build_cursor_flags(
    *,
    force: bool,
    auto_review: bool,
    sandbox: str | None,
    model: str | None,
    resuming: bool = False,
) -> list[str]:
    """Translate CursorTaskConfig knobs to an argv list.

    cursor-agent has NO permission argv (``--allow``/``--deny`` do not
    exist); permission rules are config-planted via :func:`build_cli_config`.
    ``--continue`` is appended when ``resuming`` (always False in Stage 0).
    Validation of ``sandbox`` lives in ``CursorTaskConfig.__post_init__``.
    """
    out: list[str] = []
    if force:
        out += ["--force"]
    if auto_review:
        out += ["--auto-review"]
    if sandbox is not None:
        out += ["--sandbox", sandbox]
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
    *, auto_start: bool, prompt: str = AUTO_START_PROMPT,
) -> list[str]:
    """Trailing positional prompt for an auto-start launch.

    Returns ``[prompt]`` when ``auto_start``; empty otherwise. (grok's
    ``resuming`` suppression arrives with resume in a later stage — Stage 0
    tasks never resume.)
    """
    return [prompt] if auto_start else []


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
) -> list[str]:
    """Argv for the detached ``tmux new-session`` that starts cursor-agent.

    tmux runs its command argument via ``/bin/sh -c``, so the env + cursor
    wrapper is a single trailing shell-string element. The private socket
    (``-S socket_path``) isolates this task's tmux server. ``-x/-y`` give the
    detached pane a sane initial size before any viewer attaches.
    """
    _, shell_command = _build_cursor_shell_command(
        cursor_path=cursor_path,
        workdir=workdir,
        extra_env=extra_env,
        cursor_flags=cursor_flags,
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
) -> "tuple[ProcessHandle, int, str, str]":
    """Start cursor-agent in a detached tmux session, then ttyd attaching to it.

    Returns ``(ttyd_handle, port, socket_path, session_name)``. cursor runs in
    the tmux session independent of ttyd; the caller awaits tmux-session
    liveness for completion and tears down BOTH the tmux session and ttyd.
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

    Uses ``set-buffer`` + ``paste-buffer`` (robust for arbitrary text incl.
    spaces), then — after a brief settle — a single ``Enter`` to submit. The
    settle is essential: an ``Enter`` sent in the same burst as the paste
    lands while the TUI is still settling the (bracketed) paste, so it
    consumes the CR as a literal newline inside the input box rather than a
    submit. Raises on a tmux failure."""
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
            f"send_text_to_cursor: tmux injection failed "
            f"(exit {result.exit_code}): {result.stderr!r}"
        )
