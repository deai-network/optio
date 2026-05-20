"""Opencode-specific actions over a generic Host.

Each function takes a ``Host`` (from optio_host) and uses only generic
primitives (``run_command``, ``launch_subprocess``, etc.) plus opencode-
shaped state-passing. Free functions, not Host methods, so optio-host's
Host Protocol stays generic.

Install path is uniform: ``ensure_opencode_installed`` drives
csillag/opencode's ``smart-install.sh --check`` and, when needed, downloads
the release zip as an optio child task (`HookContext.download_file`),
unpacks it on the host, and places the binary at
``<install_dir>/opencode``. ``install_dir`` defaults to
``~/.local/bin`` (resolved per host) and is overridable via the
``install_dir`` keyword argument on the public entry points; consumers
expose this as ``OpencodeTaskConfig.opencode_install_dir``.
No isinstance branches.
"""

from __future__ import annotations

import asyncio
import os
import re
import shlex
from typing import TYPE_CHECKING, Callable

from optio_host.host import ProcessHandle

if TYPE_CHECKING:
    from optio_host.host import Host


_READY_RE = re.compile(r"(http://[^\s]+)")

_SMART_INSTALL_URL = (
    "https://raw.githubusercontent.com/csillag/opencode/main/smart-install.sh"
)

# Sub-path of the host's $HOME used as the default opencode install
# directory when no explicit ``install_dir`` is supplied. Kept as a
# constant so the three places that care about it (smart-install PATH
# augmentation, post-ok ``command -v`` lookup, ``_install_opencode_from_zip``
# install target) stay in agreement.
DEFAULT_INSTALL_SUBDIR = ".local/bin"


async def _resolve_install_dir(host: "Host", install_dir: str | None) -> str:
    """Return ``install_dir`` if given, else the host's default install dir.

    Default: ``<host_home>/<DEFAULT_INSTALL_SUBDIR>``.
    """
    if install_dir is not None:
        return install_dir
    host_home = await host.resolve_host_home()
    return f"{host_home}/{DEFAULT_INSTALL_SUBDIR}"


def _path_augmented(cmd: str, install_dir: str) -> str:
    """Prefix ``cmd`` with an export that prepends ``install_dir`` to PATH.

    Used so smart-install's internal ``command -v opencode`` and the
    post-"ok" lookup find the binary at the install location even when
    the calling shell's PATH doesn't already include it (common: the
    python process inherits a slimmed-down PATH that doesn't add
    ``~/.local/bin``, so smart-install would falsely report "download"
    and we'd reinstall on every task run).
    """
    return f'export PATH={shlex.quote(install_dir)}:"$PATH"; {cmd}'


async def _smart_install_check(
    host: "Host", *, install_dir: str | None = None,
) -> tuple[str, str | None]:
    """Run smart-install.sh --check on ``host`` and parse the result.

    Returns:
      ("ok", None) when opencode is already up to date.
      ("download", url) when opencode is missing or stale; ``url`` is the
        release-archive zip to fetch.

    ``install_dir`` is prepended to PATH inside the remote shell so
    smart-install's internal ``command -v opencode`` can see binaries
    installed by a prior ``_install_opencode_from_zip``. Defaults to
    ``~/.local/bin`` on the host.

    Raises RuntimeError on non-zero exit or unparseable output.
    """
    resolved_install_dir = await _resolve_install_dir(host, install_dir)
    cmd = _path_augmented(
        f"curl -fsSL {_SMART_INSTALL_URL} | bash -s -- --check",
        resolved_install_dir,
    )
    result = await host.run_command(cmd)
    if result.exit_code != 0:
        raise RuntimeError(
            f"smart-install --check failed on host (exit {result.exit_code}): "
            f"{result.stderr.strip()[:200]}"
        )
    line = result.stdout.strip()
    if line == "opencode ok":
        return ("ok", None)
    if line.startswith("download "):
        url = line[len("download "):].strip()
        if not url:
            raise RuntimeError(
                f"smart-install --check returned empty URL: {result.stdout!r}"
            )
        return ("download", url)
    raise RuntimeError(
        f"smart-install --check returned unexpected output: {result.stdout!r}"
    )


async def _install_opencode_from_zip(
    hook_ctx, url: str, *, install_dir: str | None = None,
) -> str:
    """Download the opencode release archive from ``url`` and install it.

    Uniform for LocalHost and RemoteHost:
      1. mktemp -d on the host.
      2. ``hook_ctx.download_file(url, <tmpdir>/opencode.zip)`` (spawns the
         child download task — emits its own progress on the child ctx).
      3. unzip on the host (archive layout: ``bin/opencode`` + sidecars).
      4. mkdir -p ``install_dir``; move binary there; chmod +x.
      5. Remove the tempdir.

    ``install_dir`` defaults to ``~/.local/bin`` on the host when None.

    Returns the absolute install path on the host.
    """
    host = hook_ctx._host
    resolved_install_dir = await _resolve_install_dir(host, install_dir)
    r = await host.run_command("mktemp -d -t optio-opencode-XXXXXX")
    if r.exit_code != 0:
        raise RuntimeError(
            f"mktemp -d failed (exit {r.exit_code}): {r.stderr.strip()[:200]}"
        )
    tmpdir = r.stdout.strip()
    zip_path = f"{tmpdir}/opencode.zip"
    try:
        await hook_ctx.download_file(url, zip_path)

        r = await host.run_command(
            f"unzip -o -q {shlex.quote(zip_path)} -d {shlex.quote(tmpdir)}"
        )
        if r.exit_code != 0:
            raise RuntimeError(
                f"unzip failed (exit {r.exit_code}): {r.stderr.strip()[:200]}"
            )

        install_path = f"{resolved_install_dir}/opencode"
        r = await host.run_command(f"mkdir -p {shlex.quote(resolved_install_dir)}")
        if r.exit_code != 0:
            raise RuntimeError(
                f"mkdir -p {resolved_install_dir!r} failed (exit {r.exit_code}): "
                f"{r.stderr.strip()[:200]}"
            )
        src = f"{tmpdir}/bin/opencode"
        r = await host.run_command(
            f"mv -f {shlex.quote(src)} {shlex.quote(install_path)}"
        )
        if r.exit_code != 0:
            raise RuntimeError(
                f"mv {src!r} → {install_path!r} failed (exit {r.exit_code}): "
                f"{r.stderr.strip()[:200]}"
            )
        r = await host.run_command(f"chmod +x {shlex.quote(install_path)}")
        if r.exit_code != 0:
            raise RuntimeError(
                f"chmod +x {install_path!r} failed (exit {r.exit_code}): "
                f"{r.stderr.strip()[:200]}"
            )
        return install_path
    finally:
        # Best-effort cleanup. Don't mask a primary exception with cleanup errors.
        await host.run_command(f"rm -rf {shlex.quote(tmpdir)}")


async def ensure_opencode_installed(
    hook_ctx,
    install_if_missing: bool = True,
    *,
    install_dir: str | None = None,
) -> str:
    """Ensure opencode is available on the host behind ``hook_ctx``.

    Uniform local + remote: runs the upstream smart-install.sh in
    ``--check`` mode via ``host.run_command``. If the host already has the
    latest opencode, returns the absolute path that ``command -v opencode``
    resolves to. Otherwise — when ``install_if_missing`` is True — downloads
    the release zip (as an optio child task, so progress shows up in the
    UI), unpacks it, and installs the binary at
    ``<install_dir>/opencode``.

    ``install_dir`` is the absolute path of the directory that holds (or
    will hold) the ``opencode`` binary on the host. When None (default),
    resolves to ``<host_home>/.local/bin``. Pass an explicit absolute path
    to opt out of the default — the same dir is used for installation, for
    smart-install's PATH lookup, and for the post-"ok" ``command -v``
    resolution, so all three stay in agreement.

    Returns the absolute path of the opencode binary on the host.

    Raises RuntimeError when the check is unparseable, when an install is
    needed but ``install_if_missing`` is False, or when any sub-step fails.
    """
    host = hook_ctx._host
    resolved_install_dir = await _resolve_install_dir(host, install_dir)
    # Mark the parent task indeterminate-active before any host I/O so the
    # dashboard shows it working rather than stuck at 0% while the install
    # check (and any subsequent download child task) runs.
    hook_ctx.report_progress(None, "Checking opencode installation…")
    kind, url = await _smart_install_check(host, install_dir=resolved_install_dir)
    if kind == "ok":
        # Resolve the on-PATH path. Login shell so ``$HOME``-relative
        # additions from ``~/.profile`` apply (e.g. a manual install at
        # some other location the user has added to PATH), and *also*
        # prepend ``resolved_install_dir`` so our install location wins
        # even when the login profile doesn't add it.
        lookup_inner = _path_augmented(
            "command -v opencode", resolved_install_dir,
        )
        r = await host.run_command(f"bash -lc {shlex.quote(lookup_inner)}")
        if r.exit_code != 0:
            raise RuntimeError(
                "smart-install reported 'opencode ok' but command -v opencode "
                f"failed on the host (exit {r.exit_code}): "
                f"{r.stderr.strip()[:200]}"
            )
        return r.stdout.strip()
    # kind == "download"
    if not install_if_missing:
        raise RuntimeError(
            "opencode is missing or stale on the host and "
            "install_if_missing=False was requested."
        )
    assert url is not None  # _smart_install_check guarantees
    hook_ctx.report_progress(None, "Installing opencode…")
    return await _install_opencode_from_zip(
        hook_ctx, url, install_dir=resolved_install_dir,
    )


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
    hostname: str = "127.0.0.1",
) -> tuple[ProcessHandle, int]:
    """Launch ``opencode web`` on ``host``; wait for the listening URL.

    Writes the password to ``<workdir>/.opencode-password`` (mode 600)
    and references it via ``$(cat ...)`` in the launch command so the
    literal value never appears on the remote process's argv.

    Lays down no-op browser-opener stubs (xdg-open, gio, open,
    sensible-browser) under ``<workdir>/bin`` and prepends that
    directory to PATH so opencode's automatic browser-launch is
    suppressed.

    ``hostname`` is passed to ``opencode web --hostname=`` so callers
    can bind to a non-loopback interface when consumers reach the server
    across a network boundary (e.g. LocalHost inside a docker container
    serving a sibling API-proxy container). Defaults to ``127.0.0.1`` to
    keep RemoteHost-over-SSH and single-host deployments unchanged.

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
    # cd to workdir so opencode picks up opencode.json.
    #
    # NOTE: do NOT wrap in `bash -lc` / `bash -l`. A login shell sources
    # the user's profile (~/.profile, ~/.bash_profile, /etc/profile),
    # which on most Linux installs rewrites PATH from scratch and
    # therefore wipes the workdir/bin prefix we set in `env` below. With
    # the prefix gone, the noop xdg-open / sensible-browser / gio / open
    # shadows below stop hiding the real ones and opencode succeeds at
    # opening a real browser window. opencode_executable is an absolute
    # path (resolved by ensure_opencode_installed), so login-shell PATH
    # lookup is not needed to find the binary. Let LocalHost / RemoteHost
    # launch_subprocess do the shell wrapping; we just need the env-var
    # prefix and $(cat ...) substitution, which any POSIX sh handles.
    cmd = (
        f"exec env "
        f"OPENCODE_SERVER_PASSWORD=\"$(cat {shlex.quote(host.workdir + '/' + pw_file)})\" "
        f"BROWSER=true "
        f"{opencode_executable} web --port=0 --hostname={shlex.quote(hostname)}"
    )

    # Prepend the noop-browsers bin dir to PATH via env on launch_subprocess.
    workdir_bin = f"{host.workdir}/bin"
    extra_path = workdir_bin + ":" + os.environ.get("PATH", "/usr/local/bin:/usr/bin:/bin")
    # OPENCODE_DB must point at the same per-task db file used by the
    # subsequent export/import CLI calls. Without this, the server falls
    # back to opencode's global default db while export/import target the
    # taskdir-local file — causing snapshot capture to "Session not found"
    # against an empty file. Convention: opencode.db is a sibling of the
    # workdir under taskdir (session.py: opencode_db = f"{taskdir}/opencode.db").
    env = {
        "PATH": extra_path,
        "OPENCODE_DB": f"{host.taskdir}/opencode.db",
    }

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
