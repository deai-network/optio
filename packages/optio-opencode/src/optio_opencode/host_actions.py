"""Opencode-specific actions over a generic Host.

Each function takes a ``Host`` (from optio_host) and uses only generic
primitives (``run_command``, ``launch_subprocess``, etc.) plus opencode-
shaped state-passing. Free functions, not Host methods, so optio-host's
Host Protocol stays generic.

Install path is uniform: ``ensure_opencode_installed`` drives
csillag/opencode's ``smart-install.sh --check`` and, when needed, downloads
the release zip as an optio child task (`HookContext.download_file`),
unpacks it on the host, and places the binary at
``<install_dir>/opencode``. ``install_dir`` defaults to the optio-owned
binary cache on the worker
(``OPENCODE_CACHE_DIR`` / ``${XDG_CACHE_HOME:-$HOME/.cache}/optio-opencode/bin``,
resolved per host — never the host home bin dir) and is overridable via the
``install_dir`` keyword argument on the public entry points; consumers
expose this as ``OpencodeTaskConfig.install_dir``.
No isinstance branches.
"""

from __future__ import annotations

import asyncio
import os
import re
import shlex
from typing import TYPE_CHECKING, Callable

from optio_host.host import ProcessHandle

from optio_agents import claustrum

from optio_opencode.info import AGENT_INFO

if TYPE_CHECKING:
    from optio_host.host import Host


_READY_RE = re.compile(r"(http://[^\s]+)")

_SMART_INSTALL_URL = (
    "https://raw.githubusercontent.com/csillag/opencode/main/smart-install.sh"
)

# The optio-owned opencode binary cache lives on the WORKER, never in the host
# user's home bin dir. Default cache:
# ``${XDG_CACHE_HOME:-$HOME/.cache}/optio-opencode/bin``, overridable via the
# ``OPENCODE_CACHE_DIR`` env var on the worker. Kept as a constant so the places
# that care about it (smart-install PATH augmentation, post-ok ``command -v``
# lookup, ``_install_opencode_from_zip`` install target) stay in agreement.
_OPENCODE_CACHE_SHELL_DEFAULT = (
    '${OPENCODE_CACHE_DIR:-${XDG_CACHE_HOME:-$HOME/.cache}/optio-opencode/bin}'
)


async def _resolve_install_dir(host: "Host", install_dir: str | None) -> str:
    """Resolve the opencode binary-cache dir as an absolute path on the worker.

    ``install_dir`` (config.install_dir) overrides. Else the worker's
    OPENCODE_CACHE_DIR / XDG_CACHE_HOME / $HOME decide it — resolved via a shell
    echo so RemoteHost gets the remote cache. Resolved from the worker's REAL env
    (this runs before per-task XDG isolation), so the cache stays shared and
    outside any workdir → never snapshotted; evictable → smart-install re-downloads."""
    if install_dir is not None:
        return install_dir.rstrip("/")
    r = await host.run_command(f'printf %s "{_OPENCODE_CACHE_SHELL_DEFAULT}"')
    path = r.stdout.strip()
    if r.exit_code != 0 or not path:
        raise RuntimeError(
            f"failed to resolve opencode cache dir on host (exit {r.exit_code}): "
            f"{r.stderr.strip()[:200]}"
        )
    return path.rstrip("/")


# --- claustrum provisioning -------------------------------------------------
#
# opencode is wrapped in claustrum (Landlock, fail-closed) so the whole
# ``opencode web`` server tree — the server and every tool subprocess it spawns
# — is confined to an explicit filesystem allowlist. The provisioning logic
# (engine cross-compile, ELF-guarded build cache, functional wrap+exec
# validation, fail-closed placement) is the shared ``optio_agents.claustrum``
# module; this wrapper contributes only the opencode-owned cache-dir resolution
# and, in session.py, the grant set (``fs_allowlist.build_grant_flags``).


async def ensure_claustrum_installed(
    hook_ctx,
    *,
    install_dir: str | None = None,
) -> str:
    """Ensure a functioning claustrum binary is on the host; return its path.

    Thin wrapper over :func:`optio_agents.claustrum.ensure_claustrum_installed`:
    resolves the opencode-owned target cache dir (on the worker, beside the
    opencode binary cache), pins the engine build cache to
    ``~/.cache/optio-opencode``, and forwards the UI progress callback. All the
    real work (cross-compile, ELF-guarded engine cache, functional wrap+exec
    validation) lives in the shared module. Fail-closed — any failure RAISES, so
    the caller never proceeds to an unconfined launch.
    """
    host = hook_ctx._host
    cache_dir = await _resolve_install_dir(host, install_dir)
    return await claustrum.ensure_claustrum_installed(
        host,
        cache_dir=cache_dir,
        engine_cache_dir=os.path.expanduser("~/.cache/optio-opencode"),
        report_progress=hook_ctx.report_progress,
    )


async def claustrum_newer_tag() -> str | None:
    """Return the newest claustrum tag if it is newer than the pinned one, else None.

    Engine-side egress only. Best-effort: network failure returns None (no notice).
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


def _isolation_env(host: "Host") -> dict[str, str]:
    """Per-task HOME/XDG isolation env, derived from ``host.workdir``.

    Merged into the launch env AND the export/import env so opencode's
    auth/config/data go per-task under ``<workdir>/home`` (where the seed
    manifest's ``home_subdir`` and merge/capture target). Distinct from the
    binary cache (``_resolve_install_dir``), which is shared and resolved from
    the worker's REAL env before this isolation applies.
    """
    home = f"{host.workdir.rstrip('/')}/home"
    return {
        "HOME": home,
        "XDG_CONFIG_HOME": f"{home}/.config",
        "XDG_DATA_HOME": f"{home}/.local/share",
        "XDG_CACHE_HOME": f"{home}/.cache",
    }


def _path_augmented(cmd: str, install_dir: str) -> str:
    """Prefix ``cmd`` with an export that prepends ``install_dir`` to PATH.

    Used so smart-install's internal ``command -v opencode`` and the
    post-"ok" lookup find the binary at the install location even when
    the calling shell's PATH doesn't already include it (common: the
    python process inherits a slimmed-down PATH that doesn't add the
    optio-owned opencode binary cache dir, so smart-install would falsely
    report "download" and we'd reinstall on every task run).
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
    installed by a prior ``_install_opencode_from_zip``. Defaults to the
    optio-owned opencode binary cache on the worker (see
    ``_resolve_install_dir``).

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
    host: "Host",
    download: "Callable[[str, str], Awaitable[None]]",
    url: str,
    *,
    install_dir: str | None = None,
) -> str:
    """Download the opencode release archive from ``url`` and install it.

    Uniform for LocalHost and RemoteHost:
      1. mktemp -d on the host.
      2. ``download(url, <tmpdir>/opencode.zip)`` (engine callers pass
         ``hook_ctx.download_file``, which spawns the child download task —
         emits its own progress on the child ctx; engine-less callers pass
         ``curl_downloader(host)``).
      3. unzip on the host (archive layout: ``bin/opencode`` + sidecars).
      4. mkdir -p ``install_dir``; move binary there; chmod +x.
      5. Remove the tempdir.

    ``install_dir`` defaults to the optio-owned opencode binary cache on
    the worker when None (see ``_resolve_install_dir``).

    Returns the absolute install path on the host.
    """
    resolved_install_dir = await _resolve_install_dir(host, install_dir)
    r = await host.run_command("mktemp -d -t optio-opencode-XXXXXX")
    if r.exit_code != 0:
        raise RuntimeError(
            f"mktemp -d failed (exit {r.exit_code}): {r.stderr.strip()[:200]}"
        )
    tmpdir = r.stdout.strip()
    zip_path = f"{tmpdir}/opencode.zip"
    try:
        await download(url, zip_path)

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
    host: "Host",
    *,
    download: "Callable[[str, str], Awaitable[None]]",
    report_progress: "Callable | None" = None,
    install_if_missing: bool = True,
    install_dir: str | None = None,
) -> str:
    """Ensure opencode is available on ``host``.

    Uniform local + remote: runs the upstream smart-install.sh in
    ``--check`` mode via ``host.run_command``. If the host already has the
    latest opencode, returns the absolute path that ``command -v opencode``
    resolves to. Otherwise — when ``install_if_missing`` is True — fetches
    the release zip via ``download`` (engine callers pass
    ``hook_ctx.download_file``, so progress shows up in the UI as an optio
    child task; engine-less callers pass ``curl_downloader(host)``),
    unpacks it, and installs the binary at ``<install_dir>/opencode``.

    ``install_dir`` is the absolute path of the directory that holds (or
    will hold) the ``opencode`` binary on the host. When None (default),
    resolves to the optio-owned binary cache on the worker
    (``OPENCODE_CACHE_DIR`` / ``${XDG_CACHE_HOME:-$HOME/.cache}/optio-opencode/bin``;
    see ``_resolve_install_dir``). Pass an explicit absolute path
    to opt out of the default — the same dir is used for installation, for
    smart-install's PATH lookup, and for the post-"ok" ``command -v``
    resolution, so all three stay in agreement.

    INVARIANT: install-dir resolution (_resolve_install_dir) runs against the
    host's REAL environment, never under _isolation_env. If the per-task
    isolation env leaked in, XDG_CACHE_HOME would point inside the (possibly
    throwaway) workdir: the binary would re-download per run and be deleted
    at teardown. The shared worker cache must stay outside every workdir.

    Returns the absolute path of the opencode binary on the host.

    Raises RuntimeError when the check is unparseable, when an install is
    needed but ``install_if_missing`` is False, or when any sub-step fails.
    """
    resolved_install_dir = await _resolve_install_dir(host, install_dir)
    # Mark the parent task indeterminate-active before any host I/O so the
    # dashboard shows it working rather than stuck at 0% while the install
    # check (and any subsequent download child task) runs.
    if report_progress is not None:
        report_progress(None, f"Checking {AGENT_INFO.name} installation…")
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
    if report_progress is not None:
        report_progress(None, f"Installing {AGENT_INFO.name}…")
    return await _install_opencode_from_zip(
        host, download, url, install_dir=resolved_install_dir,
    )


def curl_downloader(host: "Host") -> "Callable[[str, str], Awaitable[None]]":
    """Context-free downloader for engine-less callers (verify): fetch a URL
    to a host path via curl on the host itself, vs the engine's child-task
    download_file."""
    async def download(url: str, dest: str) -> None:
        r = await host.run_command(
            f"curl -fsSL {shlex.quote(url)} -o {shlex.quote(dest)}"
        )
        if r.exit_code != 0:
            raise RuntimeError(
                f"curl download failed (exit {r.exit_code}): {r.stderr.strip()[:200]}"
            )
    return download


def build_host(ssh, taskdir: str) -> "Host":
    """ssh_config + taskdir -> LocalHost/RemoteHost. Lifted from
    session._build_host so engine-less callers (verify) share it."""
    import os as _os
    from optio_host.host import LocalHost, RemoteHost

    if ssh is None:
        _os.makedirs(taskdir, exist_ok=True)
        host = LocalHost(taskdir=taskdir)
        _os.makedirs(host.workdir, exist_ok=True)
        return host
    return RemoteHost(ssh_config=ssh, taskdir=taskdir)


async def run_opencode_probe(
    host: "Host",
    *,
    opencode_executable: str,
    model: str,
    prompt: str,
    wrap: "list[str] | None" = None,
    timeout_s: float = 180.0,
) -> "tuple[str, int]":
    """Headless one-shot `opencode run` under the per-task isolation env.
    Returns (stdout, exit_code). `wrap` is an argv prefix seam (future
    claustrum fs-isolation). Plain output — the caller's verdict is a
    challenge-answer match on stdout; exit code is diagnostics only."""
    import asyncio as _asyncio

    argv = [*(wrap or []), opencode_executable, "run", "--model", model, prompt]
    cmd = " ".join(shlex.quote(a) for a in argv)
    result = await _asyncio.wait_for(
        host.run_command(f"bash -lc {shlex.quote(cmd)}", env=_isolation_env(host)),
        timeout=timeout_s,
    )
    return (result.stdout or "", result.exit_code)


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
            env={**_isolation_env(host), "OPENCODE_DB": opencode_db_path},
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
            env={**_isolation_env(host), "OPENCODE_DB": opencode_db_path},
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
    extra_env: dict[str, str] | None = None,
    env_remove: list[str] | None = None,
    claustrum_wrap: list[str] | None = None,
) -> tuple[ProcessHandle, int]:
    """Launch ``opencode web`` on ``host``; wait for the listening URL.

    Writes the password to ``<workdir>/.opencode-password`` (mode 600)
    and references it via ``$(cat ...)`` in the launch command so the
    literal value never appears on the remote process's argv.

    Browser-open suppression is handled by the optio-agents protocol
    driver (``get_protocol(browser="suppress")``), which installs no-op
    opener stubs under ``<workdir>/bin`` and returns the ``BROWSER`` /
    ``PATH`` env additions; the caller passes those in via ``extra_env``.

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

    # Build cmd: read password from file via $(cat), cd to workdir so
    # opencode picks up opencode.json. Browser suppression (the BROWSER
    # env + the <workdir>/bin PATH prepend that shadows the openers) comes
    # from the protocol driver's suppress shims, passed in via extra_env.
    #
    # NOTE: do NOT wrap in `bash -lc` / `bash -l`. A login shell sources
    # the user's profile (~/.profile, ~/.bash_profile, /etc/profile),
    # which on most Linux installs rewrites PATH from scratch and
    # therefore wipes the workdir/bin prefix carried in `env` below. With
    # the prefix gone, the suppress stubs stop hiding the real openers and
    # opencode succeeds at opening a real browser window. opencode_executable
    # is an absolute path (resolved by ensure_opencode_installed), so
    # login-shell PATH lookup is not needed to find the binary.
    #
    # claustrum_wrap (fs isolation) is spliced BETWEEN the ``env …`` password
    # assignment and the opencode executable: ``exec env VAR=… claustrum … --
    # opencode web``. The ``$(cat …)`` runs in the outer shell (before exec), so
    # the password read happens outside the Landlock confinement; ``env`` then
    # execs claustrum, which Landlock-confines itself and execs opencode, so the
    # server and every tool subprocess inherit the allowlist. None → no wrap.
    wrap_prefix = f"{shlex.join(claustrum_wrap)} " if claustrum_wrap else ""
    cmd = (
        f"exec env "
        f"OPENCODE_SERVER_PASSWORD=\"$(cat {shlex.quote(host.workdir + '/' + pw_file)})\" "
        f"{wrap_prefix}{opencode_executable} web --port=0 --hostname={shlex.quote(hostname)}"
    )

    # OPENCODE_DB must point at the same per-task db file used by the
    # subsequent export/import CLI calls. Without this, the server falls
    # back to opencode's global default db while export/import target the
    # taskdir-local file — causing snapshot capture to "Session not found"
    # against an empty file. Convention: opencode.db is a sibling of the
    # workdir under taskdir (session.py: opencode_db = f"{taskdir}/opencode.db").
    # The browser-suppression env (PATH prepend + BROWSER) comes from extra_env.
    # The HOME/XDG isolation env (from _isolation_env) points opencode's
    # auth/config/data at <workdir>/home so per-task seeding works.
    # OPENCODE_DISABLE_AUTOUPDATE=1 disables opencode's in-agent auto-updater
    # (the binary's update fn early-returns on this var). A managed wrapper pins
    # the binary via optio's own smart-install.sh --check, so a self-download
    # would fight our version control and can stall/bloat a session mid-run;
    # disabling it here is safe because the cache stays fresh independently.
    # Set as a base default; a caller extra_env may still override.
    env = {
        **_isolation_env(host),
        "OPENCODE_DB": f"{host.taskdir}/opencode.db",
        "OPENCODE_DISABLE_AUTOUPDATE": "1",
        **(extra_env or {}),
    }

    handle = await host.launch_subprocess(
        cmd, env=env, cwd=host.workdir, env_remove=env_remove,
    )

    # Retain the tail of the launch stream so a failed start surfaces its REASON.
    # `opencode web` is a plain server (not a TUI), so its startup diagnostics —
    # a bind error, an auth/config crash, a claustrum "operation not permitted"
    # denial — arrive as ordinary lines on the merged stdout+stderr the loop
    # below already consumes. Without retaining them, they are scanned by
    # _READY_RE, discarded, and the operator sees only "exited before printing a
    # URL". A bounded deque keeps memory flat on a chatty-but-successful start.
    # No ANSI strip (unlike the tmux engines' pipe-pane mirror): server logs are
    # plain text, not PTY-painted TUI frames.
    from collections import deque

    tail: "deque[str]" = deque(maxlen=40)

    def _reason_suffix() -> str:
        recent = "\n".join(tail).strip()
        return f"; last output:\n{recent}" if recent else ""

    async def _read_url() -> int:
        async for raw in handle.stdout:
            if isinstance(raw, bytes):
                line = raw.decode("utf-8", errors="replace").rstrip()
            else:
                line = str(raw).rstrip()
            if line:
                tail.append(line)
            m = _READY_RE.search(line)
            if m:
                m2 = re.search(r":(\d+)", m.group(1))
                if not m2:
                    raise RuntimeError(f"could not find port in URL: {line}")
                return int(m2.group(1))
        raise RuntimeError(
            f"opencode exited before printing a URL{_reason_suffix()}"
        )

    try:
        port = await asyncio.wait_for(_read_url(), timeout=ready_timeout_s)
    except asyncio.TimeoutError:
        await host.terminate_subprocess(handle, aggressive=True)
        raise TimeoutError(
            f"opencode did not print a listening URL within {ready_timeout_s}s"
            f"{_reason_suffix()}"
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
