"""Claudecode-specific actions over a generic Host.

Free functions; each takes a Host or HookContext and uses only generic
primitives (run_command, resolve_host_home, etc.). No isinstance branches.
"""

from __future__ import annotations

import asyncio
import json
import re
import shlex
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from optio_host import HookContextProtocol, Host
    from optio_host.host import ProcessHandle


# ttyd prints something like "Listening on port 7681" or
# "[INFO] tty.c:131 listening on http://127.0.0.1:7681/" depending on
# build. Match either a `port N` token or a `:N/` token in a URL.
_TTYD_READY_RE = re.compile(
    r"(?:port\s+(\d+))|(?:http://[^\s]+?:(\d+)(?:/|\s|$))"
)


_DEFAULT_INSTALL_SUBDIR = ".local/bin"

_CLAUDE_INSTALL_URL = "https://claude.ai/install.sh"

# Pinned ttyd version. Update with care; the URL pattern below is
# tsl0922/ttyd's release-asset convention as of 1.7.x.
_TTYD_VERSION = "1.7.7"
_TTYD_RELEASE_BASE = (
    f"https://github.com/tsl0922/ttyd/releases/download/{_TTYD_VERSION}"
)


async def _resolve_install_dir(host: "Host", install_dir: str | None) -> str:
    """Return ``install_dir`` if given, else ``<host_home>/<DEFAULT_INSTALL_SUBDIR>``."""
    if install_dir is not None:
        return install_dir
    host_home = await host.resolve_host_home()
    return f"{host_home}/{_DEFAULT_INSTALL_SUBDIR}"


async def _claude_present(host: "Host", claude_path: str) -> bool:
    """Return True iff ``claude_path`` is an executable file on the host
    that produces version output when invoked with --version."""
    cmd = f"[ -x {shlex.quote(claude_path)} ] && {shlex.quote(claude_path)} --version"
    result = await host.run_command(cmd)
    return result.exit_code == 0 and "Claude Code" in result.stdout


async def ensure_claude_installed(
    hook_ctx: "HookContextProtocol",
    *,
    install_if_missing: bool = True,
    install_dir: str | None = None,
) -> str:
    """Ensure the ``claude`` binary is present on the host behind ``hook_ctx``.

    The framework looks for a symlink at ``<install_dir>/claude``. When
    missing and ``install_if_missing=True``, it runs the vendor install
    script (``curl -fsSL https://claude.ai/install.sh | bash``) on the
    host. The script downloads + checksum-verifies + places the native
    binary under ``~/.local/share/claude/versions/<v>/`` and creates a
    symlink at ``~/.local/bin/claude``. The framework re-checks for the
    symlink after the install runs.

    Returns the absolute path of the ``claude`` symlink on the host.

    Raises RuntimeError when the binary is absent and either
    ``install_if_missing=False`` or the install fails.
    """
    host = hook_ctx._host
    resolved_install_dir = await _resolve_install_dir(host, install_dir)
    claude_path = f"{resolved_install_dir}/claude"

    hook_ctx.report_progress(None, "Checking claude installation…")
    if await _claude_present(host, claude_path):
        return claude_path

    if not install_if_missing:
        raise RuntimeError(
            f"claude not present at {claude_path!r} on host and "
            f"install_if_missing=False; nothing to do."
        )

    hook_ctx.report_progress(None, "Installing claude (vendor install.sh)…")
    install_cmd = f"curl -fsSL {shlex.quote(_CLAUDE_INSTALL_URL)} | bash"
    result = await host.run_command(install_cmd)
    if result.exit_code != 0:
        raise RuntimeError(
            f"claude install failed on host (exit {result.exit_code}): "
            f"{result.stderr.strip()[:300]}"
        )

    if not await _claude_present(host, claude_path):
        raise RuntimeError(
            f"claude install reported success but {claude_path!r} is still "
            f"not executable on the host. Inspect the host's "
            f"~/.local/bin and ~/.local/share/claude/versions for diagnostics."
        )
    return claude_path


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


def build_ttyd_argv(
    *,
    ttyd_path: str,
    claude_path: str,
    workdir: str,
    bind_iface: str,
    port: int,
    extra_env: dict[str, str] | None,
    claude_flags: list[str],
) -> list[str]:
    """Construct the full argv for the ttyd subprocess.

    Layout:
      <ttyd_path> -W -i <iface> -p <port> -m 1 -T xterm-256color --
      env HOME=<workdir>/home [<extra-env...>]
      bash -c 'cd <workdir> && exec <claude_path> [<claude_flags...>]'
    """
    workdir_clean = workdir.rstrip("/")
    home_dir = f"{workdir_clean}/home"
    env_assignments: list[str] = [f"HOME={home_dir}"]
    if extra_env:
        for k, v in extra_env.items():
            env_assignments.append(f"{k}={v}")
    claude_argv = " ".join(shlex.quote(c) for c in [claude_path, *claude_flags])
    bash_payload = f"cd {shlex.quote(workdir_clean)} && exec {claude_argv}"
    return [
        ttyd_path,
        "-W",
        "-i", bind_iface,
        "-p", str(port),
        "-m", "1",
        "-T", "xterm-256color",
        "--",
        "env",
        *env_assignments,
        "bash", "-c", bash_payload,
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
) -> "tuple[ProcessHandle, int]":
    """Spawn ttyd wrapping claude under HOME-isolation. Wait for ready.

    Always passes ``-p 0`` so the OS picks a free port; the actual port
    is parsed from ttyd's stdout/stderr ready banner.

    Returns ``(handle, port)``. Caller is responsible for terminating
    the handle.
    """
    argv = build_ttyd_argv(
        ttyd_path=ttyd_path,
        claude_path=claude_path,
        workdir=host.workdir,
        bind_iface=bind_iface,
        port=0,
        extra_env=extra_env,
        claude_flags=claude_flags,
    )
    handle = await host.launch_subprocess(argv)

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
        raise TimeoutError(
            f"ttyd did not print a listening URL within {ready_timeout_s}s"
        )
    except BaseException:
        await host.terminate_subprocess(handle, aggressive=True)
        raise
    return handle, port


def build_claude_flags(
    *,
    permission_mode: str | None,
    allowed_tools: list[str] | None,
    disallowed_tools: list[str] | None,
) -> list[str]:
    """Translate ClaudeCodeTaskConfig permission knobs to an argv list.

    Empty lists are treated as None: no flag is emitted.
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
    return out
