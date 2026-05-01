"""Opencode-specific actions over a generic Host.

Each function takes ``Host`` (from optio_host) as the first argument and
uses only generic primitives (``run_command``, ``put_file_to_host``,
``write_text``, ``launch_subprocess``, etc.) plus opencode-shaped
state-passing.

Per the optio-host split spec, opencode actions become free functions
rather than Host methods so optio-host's Host interface stays generic.
The asymmetric ones (``ensure_opencode_installed``,
``install_opencode_binary``) carry an internal ``isinstance(host, LocalHost)``
branch — local never installs; remote may. All others are uniform via
host primitives.
"""

from __future__ import annotations

import asyncio
import os
import re
import shlex
from typing import TYPE_CHECKING, Callable

from optio_host.host import LocalHost, ProcessHandle
from optio_opencode.install import (
    OpencodeTarget,
    make_target,
    normalize_arch,
    normalize_os,
)

if TYPE_CHECKING:
    from optio_host.host import Host


_READY_RE = re.compile(r"(http://[^\s]+)")


async def detect_target(host: "Host") -> OpencodeTarget:
    """Detect the opencode build target (os/arch/musl/baseline) for ``host``.

    Uniform local + remote implementation via ``host.run_command`` —
    shells out to ``uname -s``, ``uname -m``, /etc/alpine-release test,
    ``ldd --version``, /proc/cpuinfo, and macOS ``sysctl`` probes. Mirrors
    opencode's upstream installer logic so the resulting triple maps
    directly to a build-output subdirectory name.
    """
    uname_s = (await host.run_command("uname -s")).stdout
    uname_m = (await host.run_command("uname -m")).stdout
    os_ = normalize_os(uname_s)
    arch = normalize_arch(uname_m)

    musl = False
    rosetta = False
    baseline = False

    if os_ == "linux":
        alpine = await host.run_command("test -f /etc/alpine-release")
        if alpine.exit_code == 0:
            musl = True
        else:
            ldd = await host.run_command("ldd --version 2>&1 || true")
            if "musl" in ldd.stdout.lower():
                musl = True
        if arch == "x64":
            cpuinfo = await host.run_command(
                "cat /proc/cpuinfo 2>/dev/null || true",
            )
            haystack = " " + cpuinfo.stdout.lower() + " "
            if cpuinfo.exit_code != 0 or " avx2 " not in haystack:
                baseline = True
    elif os_ == "darwin":
        if arch == "x64":
            ros = await host.run_command(
                "sysctl -n sysctl.proc_translated 2>/dev/null || echo 0",
            )
            rosetta = ros.stdout.strip() == "1"
            avx2 = await host.run_command(
                "sysctl -n hw.optional.avx2_0 2>/dev/null || echo 0",
            )
            baseline = avx2.stdout.strip() != "1"

    return make_target(os_, arch, rosetta=rosetta, musl=musl, baseline=baseline)


async def ensure_opencode_installed(
    host: "Host", install_if_missing: bool,
) -> None:
    """Ensure opencode is available on ``host``.

    Local: ``command -v opencode`` via the host. Raises RuntimeError if
    missing — local never installs.

    Remote: ``command -v opencode`` (login-shell PATH so ``~/.local/bin``
    is visible). If missing and ``install_if_missing`` is True, runs the
    upstream curl installer; otherwise raises RuntimeError.
    """
    if isinstance(host, LocalHost):
        # Local mode: always expects pre-install.
        result = await host.run_command("command -v opencode")
        if result.exit_code != 0:
            raise RuntimeError(
                "opencode is not available on this local host.  "
                "Install it first (e.g. `curl -fsSL opencode.ai/install | bash`); "
                "optio-opencode does not install opencode in local mode."
            )
        return

    # Remote: use bash -lc so that $HOME/.local/bin (where the opencode
    # install script puts the binary) is on PATH.
    check = await host.run_command("bash -lc 'command -v opencode'")
    if check.exit_code == 0:
        return
    if not install_if_missing:
        raise RuntimeError(
            "opencode is not installed on the remote host and "
            "install_if_missing=False was requested."
        )
    install = await host.run_command(
        "curl -fsSL https://opencode.ai/install | bash",
    )
    if install.exit_code != 0:
        raise RuntimeError(
            f"opencode install on the remote host failed "
            f"(exit {install.exit_code}): {install.stderr}"
        )


async def install_opencode_binary(
    host: "Host",
    local_binary_path: str,
    progress: "Callable[[int, int], None] | None" = None,
) -> str:
    """Install ``local_binary_path`` onto ``host``; return its absolute path there.

    Local: validates the file exists, returns the path verbatim — no copy.

    Remote: SFTP-uploads to ``<remote_home>/.local/bin/opencode`` (atomic
    rename + skip-if-unchanged), chmods +x, returns that absolute path.
    Idempotent across runs.

    If ``progress`` is given and a real upload happens, it is invoked
    periodically with ``(bytes_transferred, total_bytes)``.
    """
    if not os.path.isfile(local_binary_path):
        raise RuntimeError(
            f"opencode binary not found at {local_binary_path!r}"
        )

    if isinstance(host, LocalHost):
        # Local installs are effectively instant; no progress callback.
        return local_binary_path

    # Remote: copy under ~/.local/bin, chmod +x, return absolute path.
    home = await host.resolve_host_home()
    install_path = f"{home}/.local/bin/opencode"

    # Adapt the public progress(transferred, total) signature to the
    # progress_cb(pct_or_None, msg_or_None) interface used internally.
    progress_cb = None
    if progress is not None:
        _file_size = os.path.getsize(local_binary_path)

        def progress_cb(pct, _msg):
            if pct is not None and _file_size > 0:
                progress(int(pct * _file_size / 100), _file_size)

    await host.put_file_to_host(
        local_binary_path,
        install_path,
        skip_if_unchanged=True,
        progress_cb=progress_cb,
    )
    result = await host.run_command(f"chmod +x {shlex.quote(install_path)}")
    if result.exit_code != 0:
        raise RuntimeError(
            f"chmod +x {install_path} failed: exit {result.exit_code}: "
            f"{result.stderr!r}"
        )
    return install_path


async def opencode_version(
    host: "Host", *, opencode_executable: str = "opencode",
) -> str | None:
    """Return ``<opencode_executable> --version`` stripped stdout, or None.

    Best-effort — used only for status messages. Returns None on any
    failure (exec error, non-zero exit, empty output).
    """
    try:
        result = await host.run_command(
            f"bash -lc {shlex.quote(opencode_executable + ' --version')}",
        )
    except Exception:
        return None
    if result.exit_code != 0:
        return None
    text = (result.stdout or "").strip()
    return text or None


async def opencode_import(
    host: "Host",
    opencode_db_path: str,
    session_json: bytes,
    *, opencode_executable: str = "opencode",
) -> None:
    """Import ``session_json`` into ``opencode_db_path`` on ``host``.

    Stages the JSON to a scratch file (workdir/snapshot.json) via
    ``put_file_to_host``, runs ``<exec> import <scratch>`` with
    ``OPENCODE_DB`` set, then removes the scratch.
    """
    scratch = f"{host.workdir}/snapshot.json"
    await host.put_file_to_host(bytes(session_json), scratch)
    try:
        result = await host.run_command(
            f"bash -lc {shlex.quote(opencode_executable + ' import ' + shlex.quote(scratch))}",
            env={"OPENCODE_DB": opencode_db_path},
        )
        if result.exit_code != 0:
            raise RuntimeError(
                f"opencode import failed (exit {result.exit_code}): "
                f"{result.stderr}"
            )
    finally:
        await host.remove_file(scratch)


async def opencode_export(
    host: "Host",
    opencode_db_path: str,
    session_id: str,
    *, opencode_executable: str = "opencode",
) -> bytes:
    """Export session ``session_id`` from ``opencode_db_path`` on ``host``.

    Redirects ``<exec> export <id>`` to a scratch file in the workdir
    then ``fetch_bytes_from_host`` returns the contents. The redirect
    avoids a cancellation-truncation bug seen with stdout-via-asyncssh
    captures (see RemoteHost.opencode_export's original comment): under
    cancellation, partial recv-buffer bytes were being committed as a
    snapshot. With the redirect, an aborted run either leaves no file
    (we see exit_code != 0) or a complete one.
    """
    scratch = f"{host.workdir}/.opencode-export.json"
    try:
        result = await host.run_command(
            f"bash -lc "
            f"{shlex.quote(opencode_executable + ' export ' + shlex.quote(session_id) + ' > ' + shlex.quote(scratch))}",
            env={"OPENCODE_DB": opencode_db_path},
        )
        if result.exit_code != 0:
            raise RuntimeError(
                f"opencode export failed (exit {result.exit_code}): "
                f"{result.stderr}"
            )
        return await host.fetch_bytes_from_host(scratch)
    finally:
        await host.remove_file(scratch)


async def launch_opencode(
    host: "Host",
    password: str,
    *,
    ready_timeout_s: float = 30.0,
    opencode_executable: str = "opencode",
) -> tuple[ProcessHandle, int]:
    """Launch ``opencode web`` on ``host``; wait for the listening URL.

    Writes the password to ``<workdir>/.opencode-password`` (mode 600)
    and references it via ``$(cat ...)`` in the launch command so the
    literal value never appears on the remote process's argv.

    Lays down no-op browser-opener stubs (xdg-open, gio, open,
    sensible-browser) under ``<workdir>/bin`` and prepends that
    directory to PATH so opencode's automatic browser-launch is
    suppressed.

    Returns ``(handle, opencode_port)``. Caller is responsible for
    eventually terminating the handle via ``host.terminate_subprocess``.
    """
    pw_file = ".opencode-password"
    await host.write_text(pw_file, password)
    await host.run_command(f"chmod 600 {host.workdir}/{pw_file}")

    # Browser-suppression bin shadow.
    for noop in ("xdg-open", "gio", "open", "sensible-browser"):
        await host.write_text(f"bin/{noop}", "#!/bin/sh\nexit 0\n")
    chmod_result = await host.run_command(f"chmod +x {host.workdir}/bin/*")
    if chmod_result.exit_code != 0:
        # Non-fatal: the noop scripts may fail to be executable, but worst
        # case opencode tries to open a browser and we just live with it.
        pass

    # Build cmd: read password from file via $(cat), set BROWSER=true,
    # cd to workdir so opencode picks up opencode.json. Use bash -lc so
    # ~/.local/bin (where the upstream installer puts the binary) is on
    # PATH for the remote case; harmless locally.
    inner = (
        f"OPENCODE_SERVER_PASSWORD=\"$(cat {shlex.quote(host.workdir + '/' + pw_file)})\" "
        f"BROWSER=true "
        f"{opencode_executable} web --port=0 --hostname=127.0.0.1"
    )
    cmd = f"bash -lc {shlex.quote(inner)}"

    # Prepend the noop-browsers bin dir to PATH via env on launch_subprocess.
    workdir_bin = f"{host.workdir}/bin"
    extra_path = workdir_bin + ":" + os.environ.get("PATH", "/usr/local/bin:/usr/bin:/bin")
    env = {"PATH": extra_path}

    handle = await host.launch_subprocess(cmd, env=env, cwd=host.workdir)

    async def _read_url() -> int:
        async for raw in handle.stdout:
            if isinstance(raw, bytes):
                line = raw.decode("utf-8", errors="replace").rstrip()
            else:
                line = str(raw).rstrip()
            m = _READY_RE.search(line)
            if m:
                m2 = re.search(r":(\d+)", m.group(1))
                if not m2:
                    raise RuntimeError(f"could not find port in URL: {line}")
                return int(m2.group(1))
        raise RuntimeError("opencode exited before printing a URL")

    try:
        port = await asyncio.wait_for(_read_url(), timeout=ready_timeout_s)
    except asyncio.TimeoutError:
        await host.terminate_subprocess(handle, aggressive=True)
        raise TimeoutError(
            f"opencode did not print a listening URL within {ready_timeout_s}s"
        )
    except BaseException:
        await host.terminate_subprocess(handle, aggressive=True)
        raise

    return handle, port


async def terminate_opencode(
    host: "Host",
    handle: ProcessHandle,
    *,
    aggressive: bool,
) -> None:
    """Thin wrapper over ``host.terminate_subprocess`` — kept for naming
    symmetry with ``launch_opencode``."""
    await host.terminate_subprocess(handle, aggressive=aggressive)
