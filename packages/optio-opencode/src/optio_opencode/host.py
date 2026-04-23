"""Host abstraction for optio-opencode.

Two concrete implementations:

* ``LocalHost`` — the optio worker itself.  Uses asyncio subprocess + aiofiles.
* ``RemoteHost`` — a remote machine reachable over SSH.  Uses asyncssh,
  multiplexing command exec + SFTP + local port forwarding over a single
  connection.

From the caller's perspective the two are indistinguishable except that
``ensure_opencode_installed`` may install opencode on remote hosts but
never on local hosts.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import AsyncIterator, Callable, Protocol

from optio_opencode.install import (
    OpencodeTarget,
    make_target,
    normalize_arch,
    normalize_os,
)


@dataclass
class LaunchedProcess:
    """Handle returned by ``Host.launch_opencode``."""
    # Implementations are free to use their own process type; this object
    # only carries the data the session state machine needs.
    pid_like: object
    """Opaque handle the host uses to terminate the process later."""

    opencode_port: int
    """The port opencode is listening on, on the host where it runs."""


class Host(Protocol):
    """Everything optio-opencode needs from a host."""

    workdir: str  # absolute path on the host where opencode runs

    async def connect(self) -> None: ...

    async def disconnect(self) -> None: ...

    async def setup_workdir(self) -> None:
        """Create workdir, workdir/deliverables, and an empty workdir/optio.log."""

    async def write_text(self, relpath: str, content: str) -> None:
        """Write a UTF-8 text file inside the workdir."""

    async def ensure_opencode_installed(self, install_if_missing: bool) -> None:
        """Raise RuntimeError if opencode is not available (and install_if_missing is False
        or an install attempt failed).  Remote hosts may run the curl installer;
        local hosts never install — they raise if missing regardless of the flag."""

    async def detect_target(self) -> "OpencodeTarget":
        """Detect the opencode build target (os/arch/musl/baseline) for this host.

        Uses platform / ``uname`` plus libc and CPU-feature probes.  The
        logic mirrors opencode's upstream installer so the resulting
        triple maps directly to a ``opencode-<os>-<arch>[-baseline][-musl]``
        subdirectory of the build output.
        """

    async def install_opencode_binary(
        self,
        local_binary_path: str,
        progress: "Callable[[int, int], None] | None" = None,
    ) -> None:
        """Install an opencode binary from the worker's filesystem.

        For LocalHost: remembers the binary path and uses it as the
        opencode command, bypassing any ``opencode`` on PATH.

        For RemoteHost: SFTP-uploads the binary to
        ``~/.local/bin/opencode`` (mode 0755, atomic rename), skipping
        the upload when the remote file's SHA-256 already matches.
        Subsequent ``launch_opencode`` calls run it by absolute path.

        If ``progress`` is given and an upload actually occurs (i.e. the
        remote does not already have a byte-identical file), it is
        invoked periodically with ``(bytes_transferred, total_bytes)``.
        Local installs are effectively instant so the callback is not
        invoked for LocalHost.

        Called in place of ``ensure_opencode_installed`` when the
        ``OPTIO_OPENCODE_BINARY_DIR`` env var is set.
        """

    async def launch_opencode(
        self,
        password: str,
        ready_timeout_s: float,
        extra_args: list[str] | None = None,
    ) -> LaunchedProcess:
        """Launch ``opencode web`` in the workdir with the given password.

        Blocks until opencode prints a ``Listening on http://...`` URL
        or ``ready_timeout_s`` elapses.  Raises TimeoutError on timeout
        (opencode is left killed/cleaned up).

        ``extra_args`` is a test-only hook for substituting a test-double
        binary for opencode; production callers omit it.
        """

    async def establish_tunnel(self, opencode_port: int) -> int:
        """Return the port on the worker machine at which the upstream
        is reachable.  Local hosts return ``opencode_port`` unchanged;
        remote hosts open an SSH local forward and return the local port."""

    def tail_log(self) -> AsyncIterator[str]:
        """Async iterator yielding lines (without trailing newlines) from
        workdir/optio.log as they are appended.  Terminates when the
        underlying tail process ends or the host disconnects."""

    async def fetch_deliverable_text(self, absolute_path: str) -> str:
        """Fetch ``absolute_path`` (already validated to live inside workdir)
        and return the UTF-8 decoded contents.  Raises UnicodeDecodeError
        on non-UTF-8 content; raises FileNotFoundError on missing file."""

    async def terminate_opencode(
        self,
        process: LaunchedProcess,
        aggressive: bool,
    ) -> None:
        """Terminate opencode.

        aggressive=False: SIGTERM, wait up to 5 s, then SIGKILL.
        aggressive=True: SIGKILL immediately; do not wait.
        """

    async def cleanup_workdir(self, aggressive: bool) -> None:
        """Remove the workdir.

        aggressive=False: wait for the rm to complete.
        aggressive=True: fire-and-forget; return as soon as the call
        has been dispatched.
        """


# --- implementation -----------------------------------------------------

import asyncio
import os
import re
import shutil


_READY_RE = re.compile(r"(http://[^\s]+)")


class LocalHost:
    """Host implementation for a local subprocess."""

    workdir: str

    def __init__(self, workdir: str, opencode_cmd: list[str] | None = None):
        self.workdir = workdir
        # Allow tests to substitute a fake opencode binary.
        self._opencode_cmd = opencode_cmd or ["opencode"]
        self._tail_proc: asyncio.subprocess.Process | None = None

    async def connect(self) -> None:
        return None

    async def disconnect(self) -> None:
        if self._tail_proc is not None and self._tail_proc.returncode is None:
            self._tail_proc.terminate()
            try:
                await asyncio.wait_for(self._tail_proc.wait(), timeout=1.0)
            except asyncio.TimeoutError:
                self._tail_proc.kill()
                await self._tail_proc.wait()
            self._tail_proc = None

    async def setup_workdir(self) -> None:
        os.makedirs(self.workdir, exist_ok=True)
        os.makedirs(os.path.join(self.workdir, "deliverables"), exist_ok=True)
        log_path = os.path.join(self.workdir, "optio.log")
        open(log_path, "a").close()

    async def write_text(self, relpath: str, content: str) -> None:
        full = os.path.join(self.workdir, relpath)
        os.makedirs(os.path.dirname(full) or self.workdir, exist_ok=True)
        with open(full, "w", encoding="utf-8") as fh:
            fh.write(content)

    async def ensure_opencode_installed(self, install_if_missing: bool) -> None:
        # Local mode: always expects pre-install.  Spec Section 1/9.
        # For tests the _opencode_cmd points at fake_opencode.py which always exists.
        if self._opencode_cmd[0] == "opencode" and shutil.which("opencode") is None:
            raise RuntimeError(
                "opencode is not available on this local host.  "
                "Install it first (e.g. `curl -fsSL opencode.ai/install | bash`); "
                "optio-opencode does not install opencode in local mode."
            )

    async def launch_opencode(
        self,
        password: str,
        ready_timeout_s: float,
        extra_args: list[str] | None = None,
    ) -> LaunchedProcess:
        env = os.environ.copy()
        env["OPENCODE_SERVER_PASSWORD"] = password
        # Suppress opencode's automatic browser-open.  The `open` npm package
        # on Linux defers to xdg-open, which on GNOME/KDE ignores $BROWSER and
        # uses the desktop environment's default handler.  Shadow xdg-open in
        # the subprocess PATH with a no-op script so whatever opener opencode
        # tries first exits silently.
        _bin = os.path.join(self.workdir, "bin")
        os.makedirs(_bin, exist_ok=True)
        for _noop in ("xdg-open", "gio", "open", "sensible-browser"):
            _p = os.path.join(_bin, _noop)
            with open(_p, "w") as _fh:
                _fh.write("#!/bin/sh\nexit 0\n")
            os.chmod(_p, 0o755)
        env["PATH"] = _bin + os.pathsep + env.get("PATH", "")
        # Defense in depth: $BROWSER is honored by xdg-open only in
        # non-desktop sessions, but setting it costs nothing.
        env["BROWSER"] = "true"
        # When using the real opencode binary, "web" is a subcommand.
        # When using the fake_opencode.py test double, there is no "web".
        # Discriminator is the basename of the executable: "opencode" (or
        # an absolute path ending in /opencode) is real; anything else
        # (python3, a shim script, ...) is a test double.
        is_real_opencode = os.path.basename(self._opencode_cmd[0]) == "opencode"
        if is_real_opencode:
            cmd = [*self._opencode_cmd, "web", "--port=0", "--hostname=127.0.0.1"]
        else:
            cmd = [*self._opencode_cmd, *(extra_args or [])]

        # opencode's UI.println writes the "Web interface:" URL line to
        # STDERR, not stdout.  Merge stderr into stdout so readline() sees it.
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            cwd=self.workdir,
            env=env,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
        )

        async def _read_url() -> int:
            assert proc.stdout is not None
            while True:
                raw = await proc.stdout.readline()
                if not raw:
                    raise RuntimeError("opencode exited before printing a URL")
                line = raw.decode("utf-8", errors="replace").rstrip()
                m = _READY_RE.search(line)
                if m:
                    url = m.group(1)
                    # Parse out the port.
                    m2 = re.search(r":(\d+)", url)
                    if not m2:
                        raise RuntimeError(f"could not find port in URL: {url}")
                    return int(m2.group(1))

        try:
            port = await asyncio.wait_for(_read_url(), timeout=ready_timeout_s)
        except (asyncio.TimeoutError, Exception) as exc:
            proc.kill()
            await proc.wait()
            if isinstance(exc, asyncio.TimeoutError):
                raise TimeoutError(
                    f"opencode did not print a listening URL within {ready_timeout_s}s"
                ) from None
            raise

        return LaunchedProcess(pid_like=proc, opencode_port=port)

    async def establish_tunnel(self, opencode_port: int) -> int:
        return opencode_port

    async def tail_log(self) -> AsyncIterator[str]:
        log_path = os.path.join(self.workdir, "optio.log")
        self._tail_proc = await asyncio.create_subprocess_exec(
            "tail", "-F", "-n", "+1", log_path,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
        )
        assert self._tail_proc.stdout is not None
        while True:
            raw = await self._tail_proc.stdout.readline()
            if not raw:
                break
            yield raw.decode("utf-8", errors="replace").rstrip("\r\n")

    async def fetch_deliverable_text(self, absolute_path: str) -> str:
        # Read bytes first so we can raise UnicodeDecodeError on bad content.
        with open(absolute_path, "rb") as fh:
            data = fh.read()
        return data.decode("utf-8")

    async def terminate_opencode(
        self,
        process: LaunchedProcess,
        aggressive: bool,
    ) -> None:
        proc: asyncio.subprocess.Process = process.pid_like  # type: ignore[assignment]
        if proc.returncode is not None:
            return
        if aggressive:
            proc.kill()
            try:
                await asyncio.wait_for(proc.wait(), timeout=1.0)
            except asyncio.TimeoutError:
                pass
            return
        proc.terminate()
        try:
            await asyncio.wait_for(proc.wait(), timeout=5.0)
        except asyncio.TimeoutError:
            proc.kill()
            await proc.wait()

    async def cleanup_workdir(self, aggressive: bool) -> None:
        # On local filesystems rmtree is fast enough that aggressive vs. not
        # makes no difference.
        if os.path.exists(self.workdir):
            shutil.rmtree(self.workdir, ignore_errors=True)

    async def detect_target(self) -> OpencodeTarget:
        import platform

        os_ = normalize_os(platform.system())
        arch = normalize_arch(platform.machine())
        rosetta = False
        if os_ == "darwin" and arch == "x64":
            proc = await asyncio.create_subprocess_exec(
                "sysctl", "-n", "sysctl.proc_translated",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.DEVNULL,
            )
            out, _ = await proc.communicate()
            rosetta = out.strip() == b"1"
        musl = False
        baseline = False
        if os_ == "linux":
            if os.path.isfile("/etc/alpine-release"):
                musl = True
            else:
                proc = await asyncio.create_subprocess_exec(
                    "sh", "-c", "ldd --version 2>&1 || true",
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.STDOUT,
                )
                out, _ = await proc.communicate()
                if b"musl" in out.lower():
                    musl = True
            if arch == "x64":
                try:
                    with open("/proc/cpuinfo", "rb") as fh:
                        cpuinfo = fh.read()
                except OSError:
                    cpuinfo = b""
                if b" avx2 " not in b" " + cpuinfo.lower() + b" ":
                    baseline = True
        elif os_ == "darwin" and arch == "x64":
            proc = await asyncio.create_subprocess_exec(
                "sysctl", "-n", "hw.optional.avx2_0",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.DEVNULL,
            )
            out, _ = await proc.communicate()
            if out.strip() != b"1":
                baseline = True
        return make_target(os_, arch, rosetta=rosetta, musl=musl, baseline=baseline)

    async def install_opencode_binary(
        self,
        local_binary_path: str,
        progress: Callable[[int, int], None] | None = None,
    ) -> None:
        if not os.path.isfile(local_binary_path):
            raise RuntimeError(
                f"opencode binary not found at {local_binary_path!r}"
            )
        # Simply point _opencode_cmd at the absolute path; we don't copy.
        self._opencode_cmd = [local_binary_path]
        # No actual transfer happens locally, so `progress` is not called.


# --- RemoteHost -----------------------------------------------------

import uuid

import asyncssh

from optio_opencode.types import SSHConfig


class RemoteHost:
    """Host implementation backed by a single asyncssh connection.

    The connection multiplexes:
      * command exec (install, launch, tail -F, rm -rf)
      * SFTP (write AGENTS.md, write opencode.json, fetch deliverables)
      * local port forward (the browser → opencode tunnel)
    """

    workdir: str

    def __init__(self, ssh_config: SSHConfig):
        self._ssh = ssh_config
        self.workdir = f"/tmp/optio-opencode-{uuid.uuid4().hex[:12]}"
        self._conn: asyncssh.SSHClientConnection | None = None
        self._sftp: asyncssh.SFTPClient | None = None
        self._launch_proc: asyncssh.SSHClientProcess | None = None
        self._tail_proc: asyncssh.SSHClientProcess | None = None
        self._forward: asyncssh.SSHListener | None = None
        # Set by install_opencode_binary() to an absolute path under the
        # remote workdir; launch_opencode uses it instead of `opencode` on
        # PATH when not None.
        self._opencode_exec: str = "opencode"

    async def connect(self) -> None:
        self._conn = await asyncssh.connect(
            host=self._ssh.host,
            username=self._ssh.user,
            port=self._ssh.port,
            client_keys=[self._ssh.key_path],
            known_hosts=None,  # Spec: known-hosts verification disabled in MVP.
        )
        self._sftp = await self._conn.start_sftp_client()

    async def disconnect(self) -> None:
        if self._tail_proc is not None and self._tail_proc.returncode is None:
            self._tail_proc.terminate()
            self._tail_proc = None
        if self._forward is not None:
            self._forward.close()
            self._forward = None
        try:
            if self._sftp is not None:
                self._sftp.exit()
                self._sftp = None
        finally:
            if self._conn is not None:
                self._conn.close()
                await self._conn.wait_closed()
                self._conn = None

    async def setup_workdir(self) -> None:
        assert self._conn is not None and self._sftp is not None
        await self._conn.run(f"mkdir -p {self.workdir}/deliverables", check=True)
        await self._conn.run(f"touch {self.workdir}/optio.log", check=True)

    async def write_text(self, relpath: str, content: str) -> None:
        assert self._sftp is not None
        remote_path = f"{self.workdir}/{relpath}"
        # Ensure parent dir exists.
        parent = os.path.dirname(remote_path)
        if parent and parent != self.workdir:
            await self._conn.run(f"mkdir -p {parent}", check=True)  # type: ignore[union-attr]
        async with self._sftp.open(remote_path, "w", encoding="utf-8") as fh:
            await fh.write(content)

    async def ensure_opencode_installed(self, install_if_missing: bool) -> None:
        assert self._conn is not None
        # Use bash -lc so that $HOME/.local/bin (where the opencode install
        # script puts the binary) is on PATH; a plain `command -v opencode`
        # via a non-login shell would miss it.
        check = await self._conn.run(
            "bash -lc 'command -v opencode'", check=False
        )
        if check.exit_status == 0:
            return
        if not install_if_missing:
            raise RuntimeError(
                f"opencode is not installed on {self._ssh.host} and "
                "install_if_missing=False was requested."
            )
        # Use the official install script.  PATH is usually not updated in
        # the non-login shell that ssh exec uses, so opencode may land in
        # ~/.local/bin — the launcher below runs through `bash -lc` to pick
        # that up.
        install = await self._conn.run(
            "curl -fsSL https://opencode.ai/install | bash",
            check=False,
        )
        if install.exit_status != 0:
            raise RuntimeError(
                f"opencode install on {self._ssh.host} failed "
                f"(exit {install.exit_status}): {install.stderr}"
            )

    async def launch_opencode(
        self,
        password: str,
        ready_timeout_s: float,
        extra_args: list[str] | None = None,
    ) -> LaunchedProcess:
        assert self._conn is not None
        cmd = (
            f"cd {self.workdir} && "
            f"OPENCODE_SERVER_PASSWORD={password} "
            f"BROWSER=true "
            # 2>&1 merges stderr into stdout because opencode prints the
            # "Web interface:" URL line via UI.println → stderr.
            # Single-quote the command.  `$opencode` is safely substituted
            # here (Python f-string) before the remote shell sees anything;
            # the remote bash just sees the absolute path or the literal
            # word "opencode".
            f"bash -lc '{self._opencode_exec} web --port=0 --hostname=127.0.0.1 2>&1'"
        )
        self._launch_proc = await self._conn.create_process(cmd)

        async def _read_url() -> int:
            assert self._launch_proc is not None
            async for raw in self._launch_proc.stdout:
                line = raw.rstrip()
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
            if self._launch_proc is not None:
                self._launch_proc.kill()
                # Don't await — teardown will close the connection.
            raise TimeoutError(
                f"opencode did not print a listening URL within {ready_timeout_s}s"
            )
        return LaunchedProcess(pid_like=self._launch_proc, opencode_port=port)

    async def establish_tunnel(self, opencode_port: int) -> int:
        assert self._conn is not None
        self._forward = await self._conn.forward_local_port(
            "127.0.0.1", 0, "127.0.0.1", opencode_port
        )
        return self._forward.get_port()

    async def tail_log(self) -> AsyncIterator[str]:
        assert self._conn is not None
        log_path = f"{self.workdir}/optio.log"
        self._tail_proc = await self._conn.create_process(
            f"tail -F -n +1 {log_path}"
        )
        async for raw in self._tail_proc.stdout:
            yield raw.rstrip("\r\n")

    async def fetch_deliverable_text(self, absolute_path: str) -> str:
        assert self._sftp is not None
        async with self._sftp.open(absolute_path, "rb") as fh:
            data = await fh.read()
        return data.decode("utf-8")

    async def terminate_opencode(
        self,
        process: LaunchedProcess,
        aggressive: bool,
    ) -> None:
        proc: asyncssh.SSHClientProcess = process.pid_like  # type: ignore[assignment]
        if proc.returncode is not None:
            return
        if aggressive:
            proc.terminate()
            # Best-effort: do not wait.
            return
        proc.terminate()
        try:
            await asyncio.wait_for(proc.wait(), timeout=5.0)
        except asyncio.TimeoutError:
            proc.kill()
            await proc.wait()

    async def cleanup_workdir(self, aggressive: bool) -> None:
        if self._conn is None:
            return
        cmd = f"rm -rf {self.workdir}"
        if aggressive:
            # Fire-and-forget: schedule the exec, do not await completion.
            asyncio.create_task(self._conn.run(cmd, check=False))
            return
        await self._conn.run(cmd, check=False)

    async def detect_target(self) -> OpencodeTarget:
        assert self._conn is not None

        async def _run(cmd: str, *, check: bool = False) -> tuple[int, str]:
            """Run a trivial command on the remote; return (exit_status, stdout).

            Kept plain-vanilla (no shell-embedded substitutions) to avoid any
            quoting subtleties over SSH.
            """
            r = await self._conn.run(cmd, check=check)  # type: ignore[union-attr]
            return (r.exit_status or 0, str(r.stdout or ""))

        _, uname_s = await _run("uname -s", check=True)
        _, uname_m = await _run("uname -m", check=True)
        os_ = normalize_os(uname_s)
        arch = normalize_arch(uname_m)

        musl = False
        rosetta = False
        baseline = False

        if os_ == "linux":
            rc, _ = await _run("test -f /etc/alpine-release")
            if rc == 0:
                musl = True
            else:
                # `ldd --version` prints either to stdout (glibc) or stderr
                # (musl).  2>&1 merges; we do not care about exit status.
                _, ldd_out = await _run("ldd --version 2>&1 || true")
                if "musl" in ldd_out.lower():
                    musl = True
            if arch == "x64":
                # /proc/cpuinfo may be unreadable in containers; absence =>
                # assume baseline to be safe.
                rc, cpuinfo = await _run("cat /proc/cpuinfo 2>/dev/null || true")
                if rc != 0 or " avx2 " not in " " + cpuinfo.lower() + " ":
                    baseline = True
        elif os_ == "darwin":
            if arch == "x64":
                _, ros = await _run(
                    "sysctl -n sysctl.proc_translated 2>/dev/null || echo 0"
                )
                rosetta = ros.strip() == "1"
                _, avx2 = await _run(
                    "sysctl -n hw.optional.avx2_0 2>/dev/null || echo 0"
                )
                baseline = avx2.strip() != "1"

        return make_target(os_, arch, rosetta=rosetta, musl=musl, baseline=baseline)

    async def install_opencode_binary(
        self,
        local_binary_path: str,
        progress: Callable[[int, int], None] | None = None,
    ) -> None:
        """Install the binary to ``~/.local/bin/opencode`` on the remote.

        Persistent (survives the workdir's rm -rf on teardown) and — if the
        remote user has ``~/.local/bin`` on PATH — discoverable by future
        manual invocations too.  Idempotent: we hash-compare the remote
        file against the local source and skip SFTP when they match, so
        subsequent task runs reuse the existing install without re-
        uploading ~150 MB.

        If ``progress`` is given and an upload actually happens, it is
        called periodically with ``(bytes_transferred, total_bytes)``.
        """
        import hashlib

        assert self._conn is not None and self._sftp is not None
        if not os.path.isfile(local_binary_path):
            raise RuntimeError(
                f"opencode binary not found at {local_binary_path!r}"
            )

        # Compute local SHA-256 (read in chunks so we don't load 150 MB at once).
        h = hashlib.sha256()
        with open(local_binary_path, "rb") as fh:
            for chunk in iter(lambda: fh.read(1 << 20), b""):
                h.update(chunk)
        local_hash = h.hexdigest()

        # Resolve $HOME on the remote to build an absolute path.
        r = await self._conn.run("printf %s \"$HOME\"", check=True)
        home = str(r.stdout or "").strip()
        if not home:
            raise RuntimeError("could not resolve $HOME on remote host")
        remote_bin_dir = f"{home}/.local/bin"
        remote_path = f"{remote_bin_dir}/opencode"

        # If the remote file already exists with the same SHA-256, we can
        # reuse it directly — skip the SFTP upload.
        rh = await self._conn.run(
            f"sha256sum {remote_path} 2>/dev/null | awk '{{print $1}}'",
            check=False,
        )
        remote_hash = str(rh.stdout or "").strip()

        if remote_hash != local_hash:
            # Ensure target dir exists, then stage via a temp path and rename
            # atomically so a concurrent run doesn't see a partially-written
            # file or overlap with another upload.
            await self._conn.run(f"mkdir -p {remote_bin_dir}", check=True)
            tmp_remote = f"{remote_path}.optio-opencode.{uuid.uuid4().hex[:8]}.tmp"

            # asyncssh's SFTP progress_handler has signature
            # (srcpath, dstpath, bytes_copied, total_bytes); paths are bytes.
            # We adapt it to the (transferred, total) API we expose.
            sftp_progress = None
            if progress is not None:
                def sftp_progress(
                    _src: bytes, _dst: bytes, transferred: int, total: int
                ) -> None:
                    progress(transferred, total)

            await self._sftp.put(
                local_binary_path, tmp_remote, progress_handler=sftp_progress
            )
            await self._conn.run(
                f"chmod +x {tmp_remote} && mv -f {tmp_remote} {remote_path}",
                check=True,
            )

        self._opencode_exec = remote_path
