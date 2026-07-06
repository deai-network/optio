"""Antigravity-specific actions over a generic Host.

Free functions; each takes a Host or HookContext and uses only generic
primitives (run_command, resolve_host_home, etc.). No isinstance branches
(save the local-vs-remote bind in :func:`build_host`).

Adapted from optio-grok's ``host_actions``. Stage 0 is the iframe/ttyd surface
only: resolve ``agy`` → install ``ttyd`` → launch ``agy`` inside a detached
tmux session under ttyd → drive the optio.log protocol → teardown. The binary
cache/download (Stage 5), the ACP/conversation launch (agy has none), resume
bookkeeping (Stage 2), seeds (Stage 3/4), and fs-isolation (Stage 8) land in
their own stages.
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


# Settle (seconds) between pasting a message into the agy TUI and sending
# Enter. Without it the Enter is glued to the paste and agy treats the CR as a
# newline inside the input box instead of a submit (see send_text_to_agy). A
# shell-literal string (used in a `sleep` invocation).
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


# --- agy resolution (Stage 0 stub: no binary cache/download; that is Stage 5) ---


async def resolve_agy(
    host: "Host",
    *,
    install_dir: str | None = None,
    install_if_missing: bool = True,
) -> str:
    """Host-based ``agy`` binary resolution (no HookContext).

    Resolved from ``<install_dir>/agy`` when ``install_dir`` is given, otherwise
    via ``command -v agy`` in a login shell (so worker-profile PATH additions
    apply, e.g. ``~/.local/bin``). Raises when the binary is absent. Stage 0 has
    no auto-install — the two-tier binary cache lands in Stage 5.
    """
    if install_dir is not None:
        candidate = f"{install_dir.rstrip('/')}/agy"
        probe = await host.run_command(
            f"[ -x {shlex.quote(candidate)} ] && echo OK || true"
        )
        if "OK" in (probe.stdout or ""):
            return candidate
        raise RuntimeError(
            f"agy not present at {candidate!r} on host "
            f"(agy_install_dir={install_dir!r})."
        )

    result = await host.run_command("bash -lc 'command -v agy'")
    path = (result.stdout or "").strip()
    if result.exit_code == 0 and path:
        return path

    if not install_if_missing:
        raise RuntimeError(
            "agy not found on host and install_if_missing=False; nothing to do."
        )
    raise RuntimeError(
        "agy not found on the worker (looked via 'command -v agy'). Stage 0 has "
        "no auto-install (the binary cache is Stage 5) — install agy manually "
        "(e.g. ~/.local/bin/agy) or pass agy_install_dir."
    )


async def ensure_antigravity_installed(
    hook_ctx: "HookContextProtocol",
    *,
    install_if_missing: bool = True,
    install_dir: str | None = None,
    progress_label: str = "Preparing Antigravity…",
) -> str:
    """Provision ``agy`` for this task and return its launch path.

    Stage-0 stub: resolves an ``agy`` already on the worker (Tier-1 — an
    operator/CI-installed binary at ``install_dir`` or on the login-shell PATH)
    and returns it. The real two-tier evictable cache with vendor auto-install
    (Tier-2, the ``https://antigravity.google/cli/install.sh`` manifest+tarball
    path with self-update disabled) lands in Stage 5, which replaces this
    resolver body. Uses only generic Host primitives.
    """
    host = hook_ctx._host
    hook_ctx.report_progress(None, progress_label)
    return await resolve_agy(
        host, install_dir=install_dir, install_if_missing=install_if_missing,
    )


def build_host(ssh, taskdir: str) -> "Host":
    """ssh_config + taskdir -> LocalHost/RemoteHost. Shared with engine-free
    callers (verify) — mirrors grok/opencode's host_actions.build_host. The
    Local-vs-Remote bind is the one permitted isinstance-shaped branch."""
    from optio_host.host import LocalHost, RemoteHost

    if ssh is None:
        os.makedirs(taskdir, exist_ok=True)
        host: "Host" = LocalHost(taskdir=taskdir)
        os.makedirs(host.workdir, exist_ok=True)
        return host
    return RemoteHost(ssh_config=ssh, taskdir=taskdir)


def _isolation_env(workdir: str) -> dict[str, str]:
    """Single source of truth for a task's HOME/XDG agent identity.

    Every agy launch derives its environment from this map so isolation is
    identical across launch paths. All keys are rooted at ``<workdir>/home``:

    - ``HOME`` — agy's own state (its ``~/.gemini`` tree: transcript, artifacts,
      ``antigravity-cli/settings.json``) lands in the per-task home.
    - ``XDG_CONFIG_HOME`` / ``XDG_DATA_HOME`` / ``XDG_CACHE_HOME`` — pin the XDG
      base dirs into the task home so no XDG-respecting tool reaches the
      operator's ``~/.config`` / ``~/.cache``.

    PATH is intentionally NOT included: it is layered by the caller (launch adds
    ``<home>/.local/bin`` ahead of the worker PATH)."""
    home = f"{workdir.rstrip('/')}/home"
    return {
        "HOME": home,
        "XDG_CONFIG_HOME": f"{home}/.config",
        "XDG_DATA_HOME": f"{home}/.local/share",
        "XDG_CACHE_HOME": f"{home}/.cache",
    }


# --- ttyd install (copied verbatim from optio-grok) -------------------------


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

    When missing and ``install_if_missing=True``, downloads the appropriate
    static prebuilt asset from ``tsl0922/ttyd`` GitHub Releases via
    ``hook_ctx.download_file`` (so byte-progress shows in the dashboard).

    Returns the absolute path of the ``ttyd`` binary on the host.
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


def _build_agy_shell_command(
    *,
    agy_path: str,
    workdir: str,
    extra_env: dict[str, str] | None,
    agy_flags: list[str],
) -> tuple[list[str], str]:
    """Return (env_assignments, shell_command).

    ``env_assignments`` is the list of ``KEY=VALUE`` strings (HOME, PATH, XDG_*,
    extras). ``shell_command`` is the full ``env <assignments> bash -c <payload>``
    string that runs agy under HOME-isolation and appends DONE/ERROR to optio.log
    when agy exits. Consumed by build_tmux_session_argv (agy runs inside the
    detached tmux session, not as a direct ttyd child).
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
    # the isolation identity (XDG_*) from the SSOT.
    env_map = {
        "HOME": home_dir,
        "PATH": f"{home_local_bin}:{base_path}",
        **{k: v for k, v in iso.items() if k != "HOME"},
    }
    env_assignments: list[str] = [f"{k}={v}" for k, v in env_map.items()]
    for k, v in extra.items():
        env_assignments.append(f"{k}={v}")

    agy_argv = " ".join(shlex.quote(c) for c in [agy_path, *agy_flags])
    log_path = f"{workdir_clean}/optio.log"

    bash_payload = (
        f"cd {shlex.quote(workdir_clean)} && {agy_argv}; rc=$?; "
        f'if [ "$rc" = 0 ]; then echo DONE >> {shlex.quote(log_path)}; '
        f"else printf 'ERROR: agy exited %s\\n' \"$rc\" >> {shlex.quote(log_path)}; fi"
    )
    shell_command = "env " + " ".join(
        shlex.quote(x) for x in [*env_assignments, "bash", "-c", bash_payload]
    )
    return env_assignments, shell_command


# --- flags -----------------------------------------------------------------


# agy's native permission surface is binary: normal prompting or
# ``--dangerously-skip-permissions`` (auto-approve every tool). The generic
# callers pass the claudecode-style ``bypassPermissions``; the config validates
# it and this map resolves the alias to agy's real flag.
_PERMISSION_MODE_ALIASES = {"bypassPermissions": "dangerously-skip-permissions"}


def build_agy_flags(
    *,
    permission_mode: str | None,
    model: str | None,
    resuming: bool = False,
) -> list[str]:
    """Translate AntigravityTaskConfig knobs to an argv list.

    ``permission_mode`` accepts agy's ``default`` / ``dangerously-skip-permissions``
    plus the claudecode-style ``bypassPermissions`` alias; only the skip flag has
    an argv effect (``default`` emits nothing — agy prompts by default and has no
    ``--permission-mode`` option). ``--model`` passes the model through.
    ``--continue`` is appended when ``resuming`` (Stage 2; always False in
    Stage 0). Validation of ``permission_mode`` lives in
    ``AntigravityTaskConfig.__post_init__``.
    """
    out: list[str] = []
    resolved_perm = _PERMISSION_MODE_ALIASES.get(permission_mode, permission_mode)
    if resolved_perm == "dangerously-skip-permissions":
        out += ["--dangerously-skip-permissions"]
    if model:
        out += ["--model", model]
    if resuming:
        out += ["--continue"]
    return out


# Positional prompt appended to the agy launch when ``auto_start`` is set —
# kicks the agent off without the operator typing anything.
AUTO_START_PROMPT = "Read AGENTS.md and execute the task it describes"


def build_auto_start_args(
    *, auto_start: bool, resuming: bool = False, prompt: str = AUTO_START_PROMPT,
) -> list[str]:
    """Trailing positional prompt for an auto-start FRESH launch.

    Returns ``[prompt]`` when ``auto_start`` and not ``resuming``; empty
    otherwise. On resume the session is continued with ``--continue`` and no
    positional is appended: re-issuing the kickoff prompt would start a new task
    instead of resuming the existing conversation.
    """
    return [prompt] if (auto_start and not resuming) else []


# --- tmux / ttyd machinery (adapted verbatim from optio-grok) ---------------


def build_tmux_session_argv(
    *,
    tmux_path: str,
    agy_path: str,
    workdir: str,
    socket_path: str,
    session_name: str,
    extra_env: dict[str, str] | None,
    agy_flags: list[str],
) -> list[str]:
    """Argv for the detached ``tmux new-session`` that starts agy.

    tmux runs its command argument via ``/bin/sh -c``, so the env + agy wrapper
    is a single trailing shell-string element. The private socket
    (``-S socket_path``) isolates this task's tmux server. ``-x/-y`` give the
    detached pane a sane initial size before any viewer attaches.
    """
    _, shell_command = _build_agy_shell_command(
        agy_path=agy_path,
        workdir=workdir,
        extra_env=extra_env,
        agy_flags=agy_flags,
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

    ttyd does not run agy — it runs ``tmux attach``. ``-t disableLeaveAlert=true``
    turns off ttyd's web-client ``beforeunload`` prompt (with tmux persistence,
    leaving the page only detaches a viewer; the session keeps running).
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
    long processId can push ``${workdir}/tmux.sock`` past the Linux ``sun_path``
    limit (108 bytes). ``sha256(workdir)`` keys the socket per task
    (deterministic across the task's calls, collision-safe); ``/tmp`` always
    exists so no mkdir is needed."""
    import hashlib

    digest = hashlib.sha256(host.workdir.encode("utf-8")).hexdigest()[:16]
    return f"/tmp/optio-ag-{digest}.sock"


async def _require_tmux(host: "Host") -> str:
    """Return the absolute path to tmux on the host, or raise a clear error.

    agy runs inside a detached tmux session (so the agent survives viewer
    disconnects); tmux is a worker prerequisite. Resolved via a login shell so
    PATH additions from the worker profile apply. No auto-install: a missing
    tmux fails fast with an actionable message.
    """
    result = await host.run_command("bash -lc 'command -v tmux'")
    path = (result.stdout or "").strip()
    if result.exit_code != 0 or not path:
        raise RuntimeError(
            "tmux is required on the worker for optio-antigravity (agy runs "
            "inside a detached tmux session). Install tmux (e.g. apt-get "
            "install tmux) or add it to the worker/container image."
        )
    return path


async def _launch_detached_checked(
    host: "Host", cmd: str, *, env_remove: list[str] | None, what: str,
) -> list[str]:
    """Launch a detached command, drain its (stderr-merged) stdout, then check
    the exit code. Non-zero raises ``RuntimeError`` carrying the output.

    ``launch_subprocess`` returns a streaming handle with no ``exit_code``, so
    the code is recovered via ``proc_wait``. Silently swallowing it is what
    turned tmux's clear "File name too long" into a misleading downstream error.
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


async def launch_ttyd_with_agy(
    host: "Host",
    *,
    ttyd_path: str,
    agy_path: str,
    bind_iface: str,
    extra_env: dict[str, str] | None,
    agy_flags: list[str],
    ready_timeout_s: float = 30.0,
    env_remove: list[str] | None = None,
    session_name: str = "optio",
) -> "tuple[ProcessHandle, int, str, str]":
    """Start agy in a detached tmux session, then ttyd attaching to it.

    Returns ``(ttyd_handle, port, socket_path, session_name)``. agy runs in the
    tmux session independent of ttyd; the caller awaits tmux-session liveness
    for completion and tears down BOTH the tmux session and ttyd.
    """
    tmux_path = await _require_tmux(host)
    socket_path = _tmux_socket_path(host)

    # 1) Start agy detached in tmux. The env scrub (env_remove) must apply here
    #    so the tmux server — which holds agy — does not inherit scrubbed vars.
    session_argv = build_tmux_session_argv(
        tmux_path=tmux_path,
        agy_path=agy_path,
        workdir=host.workdir,
        socket_path=socket_path,
        session_name=session_name,
        extra_env=extra_env,
        agy_flags=agy_flags,
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
    """Best-effort kill of the per-task tmux session (stops agy)."""
    try:
        await host.run_command(
            f"{shlex.quote(tmux_path)} -S {shlex.quote(socket_path)} "
            f"kill-session -t {shlex.quote(session_name)}"
        )
    except Exception:  # noqa: BLE001
        _LOG.exception("tmux kill-session failed (socket=%s)", socket_path)


def _agy_pgrep_pattern(agy_path: str) -> str:
    """Anchored pgrep/pkill pattern matching ONLY the real agy.

    The real agy execs with the path as the FIRST token of its cmdline
    (argv[0]), whereas the tmux server and the bash/env wrappers carry the same
    path only as a LATER argument. ``^`` excludes them; only a process whose
    cmdline starts with the path matches. ``[a]gy`` keeps pgrep/pkill's own
    cmdline from self-matching.
    """
    body = agy_path[:-3] + "[a]gy" if agy_path.endswith("agy") else agy_path
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


async def kill_agy_processes(
    host: "Host", agy_path: str, *, signal: str = "KILL",
) -> None:
    """Kill the per-task agy via an anchored host-side ``pkill``.

    agy ignores the tmux pane SIGHUP. Best-effort: pkill exits non-zero when
    nothing matches."""
    pattern = _agy_pgrep_pattern(agy_path)
    await host.run_command(f"pkill -{signal} -f {shlex.quote(pattern)} || true")


async def await_agy_gone(
    host: "Host", agy_path: str, *, timeout_s: float = 15.0, poll_s: float = 1.0,
) -> bool:
    """Block (polling once per ``poll_s``) until no process matching the per-task
    ``agy_path`` remains. Bounded by ``timeout_s`` (logs a warning and returns
    False on timeout). Returns True once agy is gone."""
    pattern = _agy_pgrep_pattern(agy_path)
    waited = 0.0
    while True:
        r = await host.run_command(f"pgrep -f {shlex.quote(pattern)} || true")
        if not (r.stdout or "").strip():
            return True
        if waited >= timeout_s:
            _LOG.warning(
                "await_agy_gone: agy still running after %.0fs (path=%s); "
                "proceeding anyway", timeout_s, agy_path,
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
    agy_path: str,
    ttyd_handle: "ProcessHandle | None" = None,
    aggressive: bool,
) -> None:
    """Kill a full agy session tree (ttyd + tmux + agy).

    Four best-effort steps, each isolated so one failure does not abort the
    rest: (1) ttyd via the tracked handle or an anchored socket pkill;
    (2) ``kill-session`` SIGHUPs the tmux pane; (3) ``kill_agy_processes``
    (agy ignores the pane SIGHUP); (4) ``await_agy_gone`` waits for quiescence.
    """
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
        await kill_agy_processes(host, agy_path)
    except Exception:
        _LOG.exception("kill_agy_processes failed")

    try:
        await await_agy_gone(host, agy_path)
    except Exception:
        _LOG.exception("await_agy_gone failed; proceeding")


async def tmux_session_alive(
    host: "Host", tmux_path: str, socket_path: str, session_name: str,
) -> bool:
    """True while the agy-bearing tmux session exists."""
    r = await host.run_command(
        f"{shlex.quote(tmux_path)} -S {shlex.quote(socket_path)} "
        f"has-session -t {shlex.quote(session_name)}"
    )
    return r.exit_code == 0


async def send_text_to_agy(
    host: "Host", tmux_path: str, tmux_socket: str, tmux_session: str, text: str,
) -> None:
    """Fake-type a message into the agy TUI and submit it.

    Uses ``set-buffer`` + ``paste-buffer`` (robust for arbitrary text incl.
    spaces), then — after a brief settle — a single ``Enter`` to submit. The
    settle is essential: an ``Enter`` sent in the same burst as the paste lands
    while the TUI is still settling the (bracketed) paste, so the CR is consumed
    as a literal newline inside the input box rather than a submit. Raises on a
    tmux failure."""
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
            f"send_text_to_agy: tmux injection failed "
            f"(exit {result.exit_code}): {result.stderr!r}"
        )
