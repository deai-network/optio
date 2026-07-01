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
import logging
import os
import re
import shlex
from datetime import datetime, timezone
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


async def _resolve_install_dir(host: "Host", install_dir: str | None) -> str:
    """Return ``install_dir`` if given, else ``<host_home>/.local/bin`` (ttyd)."""
    if install_dir is not None:
        return install_dir
    host_home = await host.resolve_host_home()
    return f"{host_home}/{_DEFAULT_INSTALL_SUBDIR}"


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
            f"(grok_install_dir={install_dir!r})."
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
        "grok manually (e.g. ~/.grok/bin/grok) or pass grok_install_dir."
    )


async def ensure_grok_installed(
    hook_ctx: "HookContextProtocol",
    *,
    install_if_missing: bool = True,
    install_dir: str | None = None,
) -> str:
    """Resolve the ``grok`` binary on the host and return its absolute path.

    Stage 0: no binary cache or auto-download (that is Stage 5). Thin engine-
    side wrapper over :func:`resolve_grok` that also reports progress.
    """
    hook_ctx.report_progress(None, "Locating grok…")
    return await resolve_grok(
        hook_ctx._host,
        install_dir=install_dir,
        install_if_missing=install_if_missing,
    )


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


def _grok_isolation_env(host: "Host") -> dict[str, str]:
    """Per-task HOME/GROK_HOME isolation env for a headless probe, derived from
    ``host.workdir`` — mirrors the launch env (``_build_grok_shell_command``) so
    the probe reads the seed's planted ``home/.grok/auth.json``.

    ``run_command`` replaces (not merges) the child env, so PATH is carried
    explicitly (the worker's PATH plus the per-task ``.local/bin``) or a missing
    interpreter/bash would break the probe."""
    home = f"{host.workdir.rstrip('/')}/home"
    base_path = os.environ.get("PATH", "/usr/local/bin:/usr/bin:/bin")
    return {
        "HOME": home,
        "PATH": f"{home}/.local/bin:{base_path}",
        "GROK_HOME": f"{home}/.grok",
        "CLAUDE_CONFIG_DIR": f"{home}/.claude",
    }


async def run_grok_probe(
    host: "Host",
    *,
    grok_executable: str,
    prompt: str,
    wrap: "list[str] | None" = None,
    timeout_s: float = 180.0,
) -> "tuple[str, int]":
    """Headless one-shot ``grok -p "<prompt>"`` under the per-task isolation
    env. Returns (stdout, exit_code). ``wrap`` is an argv prefix seam (future
    claustrum fs-isolation). The caller's verdict is a challenge-answer match
    on stdout; the exit code is diagnostics only."""
    argv = [*(wrap or []), grok_executable, "-p", prompt]
    cmd = " ".join(shlex.quote(a) for a in argv)
    # Layer the per-task HOME/GROK_HOME overrides on top of the ambient env,
    # mirroring the session launch's ``env HOME=… GROK_HOME=… bash -c …`` (which
    # inherits, not ``env -i``). run_command replaces the child env, so the
    # merge is explicit here. The caller runs this on a host whose environment
    # carries no provider API keys (see verify_and_refresh_seed).
    env = {**os.environ, **_grok_isolation_env(host)}
    result = await asyncio.wait_for(
        host.run_command(f"bash -lc {shlex.quote(cmd)}", env=env),
        timeout=timeout_s,
    )
    return (result.stdout or "", result.exit_code)


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
    home_dir = f"{workdir_clean}/home"
    home_local_bin = f"{home_dir}/.local/bin"

    extra = dict(extra_env or {})
    base_path = extra.pop("PATH", None) or os.environ.get(
        "PATH", "/usr/local/bin:/usr/bin:/bin",
    )
    env_assignments: list[str] = [
        f"HOME={home_dir}",
        f"PATH={home_local_bin}:{base_path}",
        f"GROK_HOME={home_dir}/.grok",
        f"CLAUDE_CONFIG_DIR={home_dir}/.claude",
    ]
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
) -> list[str]:
    """Translate GrokTaskConfig knobs to an argv list.

    Empty lists are treated as None: no flag is emitted. ``--allow`` is
    repeated once per allowed-tools rule (grok's spelling); disallowed tools
    are comma-joined. ``--no-leader`` is emitted when ``no_leader`` so tasks
    never share a grok backend. ``-c`` (continue) is appended when
    ``resuming`` (always False in Stage 0). Validation of ``permission_mode``
    lives in ``GrokTaskConfig.__post_init__``.
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

    Uses ``set-buffer`` + ``paste-buffer`` (robust for arbitrary text incl.
    spaces), then — after a brief settle — a single ``Enter`` to submit. The
    settle is essential: an ``Enter`` sent in the same burst as the paste
    lands while grok is still settling the (bracketed) paste, so grok consumes
    the CR as a literal newline inside the input box rather than a submit.
    Raises on a tmux failure."""
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
            f"send_text_to_grok: tmux injection failed "
            f"(exit {result.exit_code}): {result.stderr!r}"
        )


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
