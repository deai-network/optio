"""Codex-specific actions over a generic Host."""

from __future__ import annotations

import asyncio
import hashlib
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

_TTYD_READY_RE = re.compile(
    r"(?:port[\s:]+(\d+))|(?:http://[^\s]+?:(\d+)(?:/|\s|$))"
)
_SUBMIT_SETTLE_S = "1.0"
_TTYD_VERSION = "1.7.7"
_TTYD_RELEASE_BASE = (
    f"https://github.com/tsl0922/ttyd/releases/download/{_TTYD_VERSION}"
)
_DEFAULT_INSTALL_SUBDIR = ".local/bin"


async def _expand_user_path(host: "Host", path: str) -> str:
    """Expand a leading ``~``/``~/`` against the HOST's home directory.

    Downstream consumers shlex-quote every path, which defeats shell tilde
    expansion — so a documented-valid ``~/bin`` override must be expanded
    here, against the worker's home (never the engine's). ``~user`` forms
    are rejected: resolving another user's home host-side is not supported.
    """
    if path == "~" or path.startswith("~/"):
        home = (await host.resolve_host_home()).rstrip("/")
        return home if path == "~" else f"{home}/{path[2:]}"
    if path.startswith("~"):
        raise ValueError(
            f"install dir {path!r}: '~user' paths are not supported; use an "
            f"absolute path or plain '~/'."
        )
    return path


async def _resolve_install_dir(host: "Host", install_dir: str | None) -> str:
    if install_dir is not None:
        return await _expand_user_path(host, install_dir)
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
        install_dir = await _expand_user_path(host, install_dir)
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
        "codex not found on the worker (looked via 'command -v codex'). "
        "install_if_missing is accepted but Stage 0 ships no auto-install — "
        "the optio-owned binary cache arrives in a later stage. Install codex "
        "manually (npm i -g @openai/codex) or pass codex_install_dir."
    )


async def ensure_codex_installed(
    hook_ctx: "HookContextProtocol",
    *,
    install_if_missing: bool = True,
    install_dir: str | None = None,
) -> str:
    """Return the per-task launch path of the ``codex`` binary.

    Resolves the shared codex binary on the host (raising when absent —
    Stage 0 has no auto-install), provisions the per-task isolation home
    tree, and returns ``<workdir>/home/.local/bin/codex`` — a per-task
    symlink to the shared binary, so teardown's anchored pkill is scoped
    to this task only.
    """
    host = hook_ctx._host
    hook_ctx.report_progress(None, "Locating codex…")
    shared = await resolve_codex(
        host, install_dir=install_dir, install_if_missing=install_if_missing,
    )
    return await _provision_task_home(host, shared_codex_path=shared)


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


def _codex_isolation_env(host: "Host") -> dict[str, str]:
    """Per-task isolation env for a headless probe, derived from
    ``host.workdir`` via :func:`_isolation_env` (the single source of truth)
    — so the probe reads the seed's planted ``home/.codex/auth.json`` under
    the same HOME/CODEX_HOME/XDG identity as the launch.

    ``run_command`` replaces (not merges) the child env, so PATH is carried
    explicitly (the worker's PATH plus the per-task ``.local/bin``) or a
    missing interpreter/bash would break the probe."""
    iso = _isolation_env(host.workdir)
    base_path = os.environ.get("PATH", "/usr/local/bin:/usr/bin:/bin")
    return {**iso, "PATH": f"{iso['HOME']}/.local/bin:{base_path}"}


async def run_codex_probe(
    host: "Host",
    *,
    codex_executable: str,
    prompt: str,
    timeout_s: float = 180.0,
) -> "tuple[str, int]":
    """Headless one-shot ``codex exec --json -s read-only
    --skip-git-repo-check '<prompt>'`` under the per-task isolation env.
    Returns (stdout, exit_code).

    ``exec`` mode has no approvals (hard approval_policy=never) and
    ``-s read-only`` keeps the probe from touching anything; the JSONL
    events land on stdout. The caller's verdict is a challenge-answer match
    on stdout; the exit code is diagnostics only."""
    argv = [
        codex_executable, "exec", "--json", "-s", "read-only",
        "--skip-git-repo-check", prompt,
    ]
    inner = " ".join(shlex.quote(a) for a in argv)
    cmd = f"cd {shlex.quote(host.workdir.rstrip('/'))} && {inner}"
    # Layer the per-task HOME/CODEX_HOME overrides on top of the ambient
    # env, mirroring the session launch (which inherits, not ``env -i``).
    # run_command replaces the child env, so the merge is explicit here. The
    # caller runs this on a host whose environment carries no provider API
    # keys (see verify_and_refresh_seed).
    env = {**os.environ, **_codex_isolation_env(host)}
    result = await asyncio.wait_for(
        host.run_command(f"bash -lc {shlex.quote(cmd)}", env=env),
        timeout=timeout_s,
    )
    return (result.stdout or "", result.exit_code)


async def _provision_task_home(host: "Host", *, shared_codex_path: str) -> str:
    """Create the per-task isolation home tree and the per-task codex path.

    C1: codex must never launch into a nonexistent $HOME/$CODEX_HOME — the
    claudecode reference guarantees the tree via its install step
    (optio-claudecode host_actions.py:328-337); codex has no install step at
    Stage 0, so the tree is created explicitly here.

    C2: teardown pkills an anchored pattern on the codex path. That is only
    safe when the path is unique per task (see claudecode's
    _claude_pgrep_pattern docstring). The shared binary is therefore
    symlinked to <workdir>/home/.local/bin/codex and launched via that
    per-task path; the anchored pkill then reaches only this task's process.

    Returns the per-task launch path.
    """
    workdir = host.workdir.rstrip("/")
    home = f"{workdir}/home"
    bin_dir = f"{home}/.local/bin"
    per_task_codex = f"{bin_dir}/codex"
    dirs = [
        f"{home}/.codex",
        bin_dir,
        f"{home}/.config",
        f"{home}/.local/share",
        f"{home}/.cache",
    ]
    quoted = " ".join(shlex.quote(d) for d in dirs)
    r = await host.run_command(f"mkdir -p {quoted}")
    if r.exit_code != 0:
        raise RuntimeError(
            f"per-task home provisioning (mkdir -p) failed "
            f"(exit {r.exit_code}): {r.stderr.strip()[:200]}"
        )
    r = await host.run_command(
        f"ln -sfn {shlex.quote(shared_codex_path)} {shlex.quote(per_task_codex)}"
    )
    if r.exit_code != 0:
        raise RuntimeError(
            f"per-task codex symlink failed (exit {r.exit_code}): "
            f"{r.stderr.strip()[:200]}"
        )
    return per_task_codex


async def ensure_workdir_trusted(host: "Host") -> None:
    """Ensure ``home/.codex/config.toml`` pre-trusts this task's workdir.

    Codex gates operation on per-directory trust recorded as
    ``[projects."<dir>"] trust_level = "trusted"`` in config.toml. A seeded
    fresh workdir was never trusted by the operator, so the session's
    ``_prepare`` calls this right after ``merge_seed`` (the design doc's
    "post-merge edit" decision — the entry is cwd-dependent, so it cannot
    live in the cwd-independent seed blob or a manifest transform).

    Deliberately minimal and idempotent: append the entry only when the
    exact ``[projects."<workdir>"]`` header is absent; never rewrite or
    reorder the rest of the file (codex itself rewrites config.toml at
    runtime — optio must not fight it). Also safe when the seed carried no
    config.toml at all (the file is created).
    """
    workdir = host.workdir.rstrip("/")
    config_rel = "home/.codex/config.toml"
    config_abs = f"{workdir}/{config_rel}"
    header = f'[projects."{workdir}"]'
    try:
        current = (await host.fetch_bytes_from_host(config_abs)).decode("utf-8")
    except FileNotFoundError:
        current = ""
    if header in current:
        return
    entry = f'{header}\ntrust_level = "trusted"\n'
    if current and not current.endswith("\n"):
        current += "\n"
    # host.write_text is workdir-relative and creates parent dirs itself
    # (LocalHost/RemoteHost both os.makedirs / mkdir -p the parent), so no
    # explicit mkdir is needed; keep the whole-file write (small file).
    await host.write_text(config_rel, current + entry)


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
    # PATH is composed on the HOST inside the bash payload (below), never
    # baked in from the engine's os.environ — the command may run on a
    # remote worker whose PATH differs. Deliberate divergence from the
    # claudecode template, which still bakes the engine PATH in.
    path_override = extra.pop("PATH", None)
    env_assignments: list[str] = [f"{k}={v}" for k, v in iso.items()]
    for k, v in extra.items():
        env_assignments.append(f"{k}={v}")

    codex_argv = " ".join(shlex.quote(c) for c in [codex_path, *codex_flags])
    log_path = f"{workdir_clean}/optio.log"

    if path_override is not None:
        path_expr = (
            f"export PATH={shlex.quote(f'{home_local_bin}:{path_override}')}; "
        )
    else:
        path_expr = f'export PATH={shlex.quote(home_local_bin)}:"$PATH"; '
    bash_payload = (
        f"{path_expr}"
        f"cd {shlex.quote(workdir_clean)} && {codex_argv}; rc=$?; "
        f'if [ "$rc" = 0 ]; then echo DONE >> {shlex.quote(log_path)}; '
        f"else printf 'ERROR: codex exited %s\\n' \"$rc\" >> {shlex.quote(log_path)}; fi"
    )
    shell_command = "env " + " ".join(
        shlex.quote(x) for x in [*env_assignments, "bash", "-c", bash_payload]
    )
    return env_assignments, shell_command


def build_codex_flags(
    *,
    model: str | None,
    ask_for_approval: str = "never",
    sandbox: str = "workspace-write",
) -> list[str]:
    """Translate CodexTaskConfig knobs to an interactive ``codex`` argv list."""
    out: list[str] = [
        "--ask-for-approval", ask_for_approval,
        "--sandbox", sandbox,
    ]
    if model:
        out += ["--model", model]
    return out


AUTO_START_PROMPT = "Read AGENTS.md and execute the task it describes"


def build_auto_start_args(
    *, auto_start: bool, resuming: bool = False, prompt: str = AUTO_START_PROMPT,
) -> list[str]:
    """Trailing positional prompt for an auto-start FRESH launch.

    Returns ``[prompt]`` when ``auto_start`` and not ``resuming``; empty
    otherwise. On resume the session continues via ``codex resume <id>``
    and no positional is appended: re-issuing the kickoff prompt would
    enqueue a duplicate task on top of the resumed conversation.
    """
    return [prompt] if (auto_start and not resuming) else []


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
) -> "tuple[ProcessHandle, str, int, str, str]":
    """Returns ``(ttyd_handle, tmux_path, port, socket_path, session_name)``."""
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
    return handle, tmux_path, port, socket_path, session_name


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


def _socket_pkill_pattern(socket_path: str) -> str:
    """Anchored pkill -f pattern matching the orphan ttyd carrying
    ``socket_path`` in its cmdline (``ttyd … -- tmux -S <socket> attach``).
    ``[t]tyd`` keeps pkill's own argv from self-matching; the verbatim
    socket path scopes the match to this task's private socket."""
    if not socket_path:
        return socket_path
    return f"[t]tyd.*{socket_path}"


async def _kill_ttyd_by_socket(host: "Host", socket_path: str) -> None:
    """Reap a detached orphan ttyd that has no tracked launch handle.

    Normal teardown kills ttyd via ``terminate_subprocess(handle)``; a crash
    orphan's ttyd is re-parented to init with no handle, so it is reaped
    host-side by an anchored ``pkill -f`` on its private socket path.
    Best-effort: pkill exits non-zero when nothing matches."""
    pattern = _socket_pkill_pattern(socket_path)
    await host.run_command(f"pkill -KILL -f {shlex.quote(pattern)} || true")


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


# --- resume bookkeeping (Stage 2; adapted from optio-grok) ------------------


# codex rollout filenames: ``rollout-<timestamp>-<uuid>.jsonl`` under
# ``$CODEX_HOME/sessions/YYYY/MM/DD/``. The UUID (v7 in real codex; any UUID
# shape accepted here) is the session id ``codex resume`` takes.
_ROLLOUT_UUID_RE = re.compile(
    r"rollout-.*-([0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}"
    r"-[0-9a-fA-F]{4}-[0-9a-fA-F]{12})\.jsonl$"
)


async def read_latest_session_id(host: "Host") -> str | None:
    """Session id of the newest rollout under ``<workdir>/home/.codex/sessions``.

    Newest by FILENAME (lexicographic): rollout names embed an ISO-ordered
    timestamp, so a name sort IS a chronological sort — and unlike mtime it
    survives a workdir tar restore. Returns None when no rollout exists yet
    (codex never persisted a session). The derived sqlite index is
    deliberately not consulted: it is excluded from snapshots (absolute
    rollout paths) and codex rebuilds it from the rollout files.
    """
    sessions_dir = f"{host.workdir.rstrip('/')}/home/.codex/sessions"
    r = await host.run_command(
        f"find {shlex.quote(sessions_dir)} -type f -name 'rollout-*.jsonl' "
        f"2>/dev/null | sort | tail -n 1"
    )
    newest = (r.stdout or "").strip()
    if not newest:
        return None
    m = _ROLLOUT_UUID_RE.search(newest)
    if m is None:
        _LOG.warning(
            "read_latest_session_id: unparseable rollout filename %r", newest,
        )
        return None
    return m.group(1)


def build_resume_args(session_id: str | None) -> list[str]:
    """Leading argv for relaunching into a recorded session.

    ``resume`` is a codex SUBCOMMAND: it and the explicit session id must
    PRECEDE every flag — ``codex resume <id> [flags]``. Never
    ``resume --last``: it is cwd-filtered and silently starts a NEW session
    on a miss (design-doc probe), so resume is always by explicit id.
    Returns ``[]`` when ``session_id`` is None (fresh launch).
    """
    return ["resume", session_id] if session_id else []


async def _rotate_optio_log(host: "Host") -> None:
    """Append the restored optio.log to optio.log.old, then truncate it.

    Preserves historical log content across consecutive resumes while
    ensuring the tail driver only sees fresh lines from the resumed run (a
    stale DONE/ERROR carried in the restored log would otherwise be replayed
    and end the session immediately).
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
    The first line is the original launch; each later line marks a resume.
    The caller gates this on ``config.supports_resume``.
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