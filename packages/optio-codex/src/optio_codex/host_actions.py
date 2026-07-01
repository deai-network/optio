"""Codex-specific actions over a generic Host."""

from __future__ import annotations

import asyncio
import hashlib
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

_TTYD_READY_RE = re.compile(
    r"(?:port[\s:]+(\d+))|(?:http://[^\s]+?:(\d+)(?:/|\s|$))"
)
_SUBMIT_SETTLE_S = "1.0"
_TTYD_VERSION = "1.7.7"
_TTYD_RELEASE_BASE = (
    f"https://github.com/tsl0922/ttyd/releases/download/{_TTYD_VERSION}"
)
_DEFAULT_INSTALL_SUBDIR = ".local/bin"


async def _resolve_install_dir(host: "Host", install_dir: str | None) -> str:
    if install_dir is not None:
        return install_dir
    host_home = await host.resolve_host_home()
    return f"{host_home}/{_DEFAULT_INSTALL_SUBDIR}"


async def resolve_codex(
    host: "Host",
    *,
    install_dir: str | None = None,
    install_if_missing: bool = True,
) -> str:
    """Resolve the ``codex`` binary on the host."""
    if install_dir is not None:
        candidate = f"{install_dir.rstrip('/')}/codex"
        probe = await host.run_command(
            f"[ -x {shlex.quote(candidate)} ] && echo OK || true"
        )
        if "OK" in (probe.stdout or ""):
            return candidate
        raise RuntimeError(
            f"codex not present at {candidate!r} on host "
            f"(codex_install_dir={install_dir!r})."
        )

    result = await host.run_command("bash -lc 'command -v codex'")
    path = (result.stdout or "").strip()
    if result.exit_code == 0 and path:
        return path

    if not install_if_missing:
        raise RuntimeError(
            "codex not found on host and install_if_missing=False; nothing to do."
        )
    raise RuntimeError(
        "codex not found on the worker (looked via 'command -v codex'). Stage 0 "
        "has no auto-install — install codex manually or pass codex_install_dir."
    )


async def ensure_codex_installed(
    hook_ctx: "HookContextProtocol",
    *,
    install_if_missing: bool = True,
    install_dir: str | None = None,
) -> str:
    """Return the absolute path of the ``codex`` binary for this task."""
    host = hook_ctx._host
    hook_ctx.report_progress(None, "Locating codex…")
    return await resolve_codex(
        host, install_dir=install_dir, install_if_missing=install_if_missing,
    )


def build_host(ssh, taskdir: str) -> "Host":
    from optio_host.host import LocalHost, RemoteHost

    if ssh is None:
        os.makedirs(taskdir, exist_ok=True)
        host: "Host" = LocalHost(taskdir=taskdir)
        os.makedirs(host.workdir, exist_ok=True)
        return host
    return RemoteHost(ssh_config=ssh, taskdir=taskdir)


def _isolation_env(workdir: str) -> dict[str, str]:
    """Per-task HOME / CODEX_HOME / XDG identity rooted at ``<workdir>/home``."""
    home = f"{workdir.rstrip('/')}/home"
    return {
        "HOME": home,
        "CODEX_HOME": f"{home}/.codex",
        "XDG_CONFIG_HOME": f"{home}/.config",
        "XDG_DATA_HOME": f"{home}/.local/share",
        "XDG_CACHE_HOME": f"{home}/.cache",
    }


def _build_codex_shell_command(
    *,
    codex_path: str,
    workdir: str,
    extra_env: dict[str, str] | None,
    codex_flags: list[str],
) -> tuple[list[str], str]:
    workdir_clean = workdir.rstrip("/")
    iso = _isolation_env(workdir_clean)
    home_dir = iso["HOME"]
    home_local_bin = f"{home_dir}/.local/bin"

    extra = dict(extra_env or {})
    base_path = extra.pop("PATH", None) or os.environ.get(
        "PATH", "/usr/local/bin:/usr/bin:/bin",
    )
    env_map = {
        "HOME": home_dir,
        "PATH": f"{home_local_bin}:{base_path}",
        **{k: v for k, v in iso.items() if k != "HOME"},
    }
    env_assignments: list[str] = [f"{k}={v}" for k, v in env_map.items()]
    for k, v in extra.items():
        env_assignments.append(f"{k}={v}")

    codex_argv = " ".join(shlex.quote(c) for c in [codex_path, *codex_flags])
    log_path = f"{workdir_clean}/optio.log"

    bash_payload = (
        f"cd {shlex.quote(workdir_clean)} && {codex_argv}; rc=$?; "
        f'if [ "$rc" = 0 ]; then echo DONE >> {shlex.quote(log_path)}; '
        f"else printf 'ERROR: codex exited %s\\n' \"$rc\" >> {shlex.quote(log_path)}; fi"
    )
    shell_command = "env " + " ".join(
        shlex.quote(x) for x in [*env_assignments, "bash", "-c", bash_payload]
    )
    return env_assignments, shell_command


def build_codex_flags(*, model: str | None) -> list[str]:
    out: list[str] = []
    if model:
        out += ["--model", model]
    return out


AUTO_START_PROMPT = "Read AGENTS.md and execute the task it describes"


def build_auto_start_args(
    *, auto_start: bool, prompt: str = AUTO_START_PROMPT,
) -> list[str]:
    return [prompt] if auto_start else []


async def _ttyd_present(host: "Host", ttyd_path: str) -> bool:
    cmd = f"[ -x {shlex.quote(ttyd_path)} ] && {shlex.quote(ttyd_path)} --version"
    result = await host.run_command(cmd)
    blob = (result.stdout or "") + (result.stderr or "")
    return result.exit_code == 0 and "ttyd" in blob.lower()


async def _detect_ttyd_asset_name(host: "Host") -> str:
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
            f"(v1 supports Linux only)."
        )
    if arch not in {"x86_64", "aarch64", "armv7l"}:
        raise RuntimeError(
            f"unsupported host arch {arch!r} for ttyd auto-install."
        )
    return f"ttyd.{arch}"


async def ensure_ttyd_installed(
    hook_ctx: "HookContextProtocol",
    *,
    install_if_missing: bool = True,
    install_dir: str | None = None,
) -> str:
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
            f"executable on the host."
        )
    return ttyd_path


def build_tmux_session_argv(
    *,
    tmux_path: str,
    codex_path: str,
    workdir: str,
    socket_path: str,
    session_name: str,
    extra_env: dict[str, str] | None,
    codex_flags: list[str],
) -> list[str]:
    _, shell_command = _build_codex_shell_command(
        codex_path=codex_path,
        workdir=workdir,
        extra_env=extra_env,
        codex_flags=codex_flags,
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
    digest = hashlib.sha256(host.workdir.encode("utf-8")).hexdigest()[:16]
    return f"/tmp/optio-cx-{digest}.sock"


async def _require_tmux(host: "Host") -> str:
    result = await host.run_command("bash -lc 'command -v tmux'")
    path = (result.stdout or "").strip()
    if result.exit_code != 0 or not path:
        raise RuntimeError(
            "tmux is required on the worker for optio-codex (codex runs inside a "
            "detached tmux session). Install tmux or add it to the worker image."
        )
    return path


async def _launch_detached_checked(
    host: "Host", cmd: str, *, env_remove: list[str] | None, what: str,
) -> list[str]:
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


async def launch_ttyd_with_codex(
    host: "Host",
    *,
    ttyd_path: str,
    codex_path: str,
    bind_iface: str,
    extra_env: dict[str, str] | None,
    codex_flags: list[str],
    ready_timeout_s: float = 30.0,
    env_remove: list[str] | None = None,
    session_name: str = "optio",
) -> "tuple[ProcessHandle, int, str, str]":
    tmux_path = await _require_tmux(host)
    socket_path = _tmux_socket_path(host)

    session_argv = build_tmux_session_argv(
        tmux_path=tmux_path,
        codex_path=codex_path,
        workdir=host.workdir,
        socket_path=socket_path,
        session_name=session_name,
        extra_env=extra_env,
        codex_flags=codex_flags,
    )
    session_cmd = " ".join(shlex.quote(a) for a in session_argv)
    await _launch_detached_checked(
        host, session_cmd, env_remove=env_remove, what="tmux new-session",
    )

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
    try:
        await host.run_command(
            f"{shlex.quote(tmux_path)} -S {shlex.quote(socket_path)} "
            f"kill-session -t {shlex.quote(session_name)}"
        )
    except Exception:  # noqa: BLE001
        _LOG.exception("tmux kill-session failed (socket=%s)", socket_path)


def _codex_pgrep_pattern(codex_path: str) -> str:
    body = (
        codex_path[:-5] + "[c]odex" if codex_path.endswith("codex") else codex_path
    )
    return "^" + body


async def kill_codex_processes(
    host: "Host", codex_path: str, *, signal: str = "KILL",
) -> None:
    pattern = _codex_pgrep_pattern(codex_path)
    await host.run_command(f"pkill -{signal} -f {shlex.quote(pattern)} || true")


async def await_codex_gone(
    host: "Host", codex_path: str, *, timeout_s: float = 15.0, poll_s: float = 1.0,
) -> bool:
    pattern = _codex_pgrep_pattern(codex_path)
    waited = 0.0
    while True:
        r = await host.run_command(f"pgrep -f {shlex.quote(pattern)} || true")
        if not (r.stdout or "").strip():
            return True
        if waited >= timeout_s:
            _LOG.warning(
                "await_codex_gone: codex still running after %.0fs (path=%s); "
                "proceeding anyway", timeout_s, codex_path,
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
    codex_path: str,
    ttyd_handle: "ProcessHandle | None" = None,
    aggressive: bool,
) -> None:
    if ttyd_handle is not None:
        try:
            await host.terminate_subprocess(ttyd_handle, aggressive=aggressive)
        except Exception:
            _LOG.exception("terminate_subprocess (ttyd) failed")

    try:
        await _kill_tmux_session(host, tmux_path, tmux_socket, tmux_session)
    except Exception:
        _LOG.exception("tmux session teardown failed")

    try:
        await kill_codex_processes(host, codex_path)
    except Exception:
        _LOG.exception("kill_codex_processes failed")

    try:
        await await_codex_gone(host, codex_path)
    except Exception:
        _LOG.exception("await_codex_gone failed; proceeding")


async def tmux_session_alive(
    host: "Host", tmux_path: str, socket_path: str, session_name: str,
) -> bool:
    r = await host.run_command(
        f"{shlex.quote(tmux_path)} -S {shlex.quote(socket_path)} "
        f"has-session -t {shlex.quote(session_name)}"
    )
    return r.exit_code == 0


async def send_text_to_codex(
    host: "Host", tmux_path: str, tmux_socket: str, tmux_session: str, text: str,
) -> None:
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
            f"send_text_to_codex: tmux injection failed "
            f"(exit {result.exit_code}): {result.stderr!r}"
        )