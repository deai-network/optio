"""Host abstraction for optio task types.

Two concrete implementations:

* ``LocalHost`` — the optio worker itself.  Uses asyncio subprocess.
* ``RemoteHost`` — a remote machine reachable over SSH.  Uses asyncssh,
  multiplexing command exec + SFTP + local port forwarding over a single
  connection.

This module only knows about generic remote-execution primitives. All
opencode-specific actions (binary install, launch, opencode_import/export,
etc.) live in ``optio_opencode.host_actions`` as free functions taking
``Host`` — see the optio-host split spec, sections 4-5.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import AsyncIterator, Protocol

from optio_host.context import RunResult


@dataclass
class ProcessHandle:
    """Generic handle for a subprocess started via ``Host.launch_subprocess``.

    ``pid_like`` is opaque (``asyncio.subprocess.Process`` for LocalHost,
    ``asyncssh.SSHClientProcess`` for RemoteHost). ``stdout`` yields bytes
    chunks (typically lines) as the subprocess emits them; the iterator
    completes when the subprocess closes its stdout. When ``merge_stderr=True``
    (the default in ``Host.launch_subprocess``), ``stderr`` is ``None`` and
    stderr bytes are merged into ``stdout``. When ``merge_stderr=False``,
    ``stderr`` is a separate iterator over stderr-only bytes.

    When ``stdin=True`` is passed to ``Host.launch_subprocess``, ``stdin`` is
    a writable byte stream exposing the duck-typed surface
    ``write(bytes) -> None``, ``drain() -> Awaitable[None]``,
    ``close() -> None``, ``wait_closed() -> Awaitable[None]``. Default is
    ``None`` (parent's stdin is inherited; the handle does not expose a
    writer).
    """
    pid_like: object
    stdout: AsyncIterator[bytes]
    stderr: AsyncIterator[bytes] | None = None
    stdin: object | None = None


class Host(Protocol):
    """Generic local-or-remote host primitives — no opencode awareness."""

    workdir: str  # absolute path on the host where work runs
    taskdir: str  # absolute path of per-process taskdir (workdir's parent)

    async def connect(self) -> None: ...

    async def disconnect(self) -> None: ...

    async def setup_workdir(self) -> None:
        """Create the workdir directory if it does not exist."""

    async def write_text(self, relpath: str, content: str) -> None:
        """Write a UTF-8 text file inside the workdir."""

    async def establish_tunnel(self, remote_port: int) -> int:
        """Return the port on the worker machine at which the upstream
        is reachable.  Local hosts return ``remote_port`` unchanged;
        remote hosts open an SSH local forward and return the local port."""

    def tail_file(self, absolute_path: str) -> AsyncIterator[str]:
        """Async iterator yielding lines (without trailing newlines) from
        ``absolute_path`` as they are appended.  Terminates when the
        underlying tail process ends or the host disconnects."""

    async def cleanup_taskdir(self, aggressive: bool) -> None:
        """Remove the entire per-task directory.

        aggressive=False: wait for the rm to complete.
        aggressive=True: fire-and-forget; return as soon as the call
        has been dispatched.
        """

    async def launch_subprocess(
        self,
        command: str,
        *,
        env: dict[str, str] | None = None,
        cwd: str | None = None,
        merge_stderr: bool = True,
    ) -> ProcessHandle:
        """Spawn ``command`` (interpreted by ``/bin/sh -c`` semantics) and
        return a handle whose ``stdout`` iterator yields bytes as they arrive.

        Returns BEFORE the subprocess exits; caller is responsible for
        terminating it via ``terminate_subprocess`` (or letting it exit).

        ``merge_stderr`` (default True): stderr bytes are merged into stdout
        via ``2>&1`` semantics; ``ProcessHandle.stderr`` is ``None``. When
        False, stderr is captured separately and exposed as
        ``ProcessHandle.stderr`` -- caller iterates both streams.
        """

    async def terminate_subprocess(
        self,
        handle: ProcessHandle,
        *,
        aggressive: bool,
    ) -> None:
        """Terminate the subprocess associated with ``handle``.

        aggressive=False: SIGTERM, wait up to 5 s, then SIGKILL.
        aggressive=True: SIGKILL immediately; do not wait.
        """

    async def remove_file(self, path: str) -> None: ...

    async def put_file_to_host(
        self,
        source,                       # str | os.PathLike | bytes | AsyncIterator[bytes]
        absolute_target: str,
        *,
        expected_sha256: str | None = None,
        skip_if_unchanged: bool = False,
        progress_cb=None,             # Callable[[float | None, str | None], None] | None
    ) -> None: ...

    async def fetch_bytes_from_host(
        self,
        absolute_path: str,
        *,
        progress_cb=None,
    ) -> bytes: ...

    async def run_command(
        self,
        command: str,
        *,
        cwd: str | None = None,
        env: dict[str, str] | None = None,
    ) -> RunResult: ...

    async def resolve_host_home(self) -> str: ...

    def archive_workdir(
        self, exclude: list[str] | None,
    ) -> AsyncIterator[bytes]: ...
    # NB: archive_workdir is a *plain* method that returns an AsyncIterator,
    # not an async method. Callers iterate via `async for chunk in host.archive_workdir(...)`.

    async def restore_workdir(
        self, stream: AsyncIterator[bytes],
    ) -> None: ...


# --- implementation -----------------------------------------------------

import asyncio
import hashlib
import os
import shlex
import shutil


class LocalHost:
    """Host implementation for a local subprocess."""

    workdir: str
    taskdir: str

    def __init__(self, taskdir: str):
        # taskdir is the per-process directory that holds workdir plus
        # any consumer-specific sidecars (e.g. opencode.db). workdir is
        # always taskdir/workdir.
        self.taskdir = taskdir
        self.workdir = os.path.join(taskdir, "workdir")
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
        """Create the workdir directory if it does not exist.

        Sets taskdir + workdir to mode 0o700 so that only the engine UID
        can traverse them. opencode.db (session transcript) and
        workdir/.env are both inside taskdir and inherit this protection.
        """
        os.makedirs(self.taskdir, exist_ok=True)
        os.chmod(self.taskdir, 0o700)
        os.makedirs(self.workdir, exist_ok=True)
        os.chmod(self.workdir, 0o700)

    async def write_text(self, relpath: str, content: str) -> None:
        full = os.path.join(self.workdir, relpath)
        os.makedirs(os.path.dirname(full) or self.workdir, exist_ok=True)
        with open(full, "w", encoding="utf-8") as fh:
            fh.write(content)

    async def establish_tunnel(self, remote_port: int) -> int:
        return remote_port

    async def tail_file(self, absolute_path: str) -> AsyncIterator[str]:
        """Async iterator yielding appended lines from ``absolute_path``."""
        # `-n +1` reads from line 1, not EOF.  The design spec suggested
        # `-n 0` (EOF) to avoid reprocessing a truncated earlier run, but
        # our workdir is always fresh (the protocol driver creates the log
        # empty just before this) so there's nothing to reprocess, and
        # `-n 0` has a race: lines written to the log before tail
        # subscribes (e.g. the consumer eagerly appending STATUS right
        # after launch) are silently skipped.  `-n +1` + always-empty
        # initial log gives us at-least-once delivery.
        self._tail_proc = await asyncio.create_subprocess_exec(
            "tail", "-F", "-n", "+1", absolute_path,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
        )
        assert self._tail_proc.stdout is not None
        while True:
            raw = await self._tail_proc.stdout.readline()
            if not raw:
                break
            yield raw.decode("utf-8", errors="replace").rstrip("\r\n")

    async def cleanup_taskdir(self, aggressive: bool) -> None:
        # On local filesystems rmtree is fast enough that aggressive vs. not
        # makes no difference. Wipes the whole per-task dir (workdir plus
        # any consumer-specific sidecars).
        if os.path.exists(self.taskdir):
            shutil.rmtree(self.taskdir, ignore_errors=True)

    async def launch_subprocess(
        self,
        command: str,
        *,
        env: dict[str, str] | None = None,
        cwd: str | None = None,
        merge_stderr: bool = True,
    ) -> ProcessHandle:
        proc_env = os.environ.copy()
        if env:
            proc_env.update(env)
        proc = await asyncio.create_subprocess_exec(
            "/bin/sh", "-c", command,
            cwd=cwd if cwd is not None else self.workdir,
            env=proc_env,
            stdout=asyncio.subprocess.PIPE,
            stderr=(
                asyncio.subprocess.STDOUT if merge_stderr
                else asyncio.subprocess.PIPE
            ),
        )

        async def _stream(reader) -> AsyncIterator[bytes]:
            while True:
                line = await reader.readline()
                if not line:
                    break
                yield line

        if merge_stderr:
            assert proc.stdout is not None
            return ProcessHandle(
                pid_like=proc, stdout=_stream(proc.stdout), stderr=None,
            )
        assert proc.stdout is not None and proc.stderr is not None
        return ProcessHandle(
            pid_like=proc,
            stdout=_stream(proc.stdout),
            stderr=_stream(proc.stderr),
        )

    async def terminate_subprocess(
        self,
        handle: ProcessHandle,
        *,
        aggressive: bool,
    ) -> None:
        proc: asyncio.subprocess.Process = handle.pid_like  # type: ignore[assignment]
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

    async def run_command(
        self,
        command: str,
        *,
        cwd: str | None = None,
        env: dict[str, str] | None = None,
    ) -> RunResult:
        proc = await asyncio.create_subprocess_exec(
            "/bin/sh", "-c", command,
            cwd=cwd if cwd is not None else self.workdir,
            env=env,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout_b, stderr_b = await proc.communicate()
        return RunResult(
            stdout=stdout_b.decode("utf-8", errors="replace"),
            stderr=stderr_b.decode("utf-8", errors="replace"),
            exit_code=proc.returncode if proc.returncode is not None else -1,
        )

    def archive_workdir(
        self, exclude: list[str] | None,
    ) -> "AsyncIterator[bytes]":
        from optio_host.archive import yield_workdir_archive
        return yield_workdir_archive(self.workdir, exclude=exclude)

    async def restore_workdir(
        self, stream: "AsyncIterator[bytes]",
    ) -> None:
        from optio_host.archive import consume_workdir_archive
        await consume_workdir_archive(stream, self.workdir)

    async def remove_file(self, path: str) -> None:
        try:
            os.unlink(path)
        except FileNotFoundError:
            pass
        except OSError as exc:
            import logging
            logging.getLogger(__name__).warning(
                "LocalHost.remove_file(%r) failed: %r", path, exc,
            )

    async def put_file_to_host(
        self,
        source,
        absolute_target: str,
        *,
        expected_sha256: str | None = None,
        skip_if_unchanged: bool = False,
        progress_cb=None,
    ) -> None:
        os.makedirs(os.path.dirname(absolute_target), exist_ok=True)

        if skip_if_unchanged:
            # Validate early: AsyncIterator without expected_sha256 is always an error.
            if (
                not isinstance(source, (bytes, bytearray, str, os.PathLike))
                and expected_sha256 is None
            ):
                raise ValueError(
                    "skip_if_unchanged with an AsyncIterator source requires "
                    "expected_sha256"
                )

        if skip_if_unchanged and os.path.exists(absolute_target):
            target_sha = await asyncio.to_thread(_sha256_of_file, absolute_target)
            source_sha = await self._compute_source_sha(source, expected_sha256)
            if source_sha == target_sha:
                if progress_cb is not None:
                    progress_cb(None, "already up to date")
                return

        tmp = absolute_target + ".tmp"
        try:
            await self._stream_source_to_path(source, tmp, progress_cb)
            os.replace(tmp, absolute_target)
        except BaseException:
            try:
                os.unlink(tmp)
            except FileNotFoundError:
                pass
            raise

    async def _compute_source_sha(self, source, expected_sha256: str | None) -> str:
        if isinstance(source, (bytes, bytearray)):
            return hashlib.sha256(bytes(source)).hexdigest()
        if isinstance(source, (str, os.PathLike)):
            return await asyncio.to_thread(_sha256_of_file, os.fspath(source))
        # async iterator: caller must supply expected_sha256
        if expected_sha256 is None:
            raise ValueError(
                "skip_if_unchanged with an AsyncIterator source requires "
                "expected_sha256"
            )
        return expected_sha256

    async def _stream_source_to_path(self, source, dest_path: str, progress_cb) -> None:
        """Write `source` (path, bytes, or async iterator) to `dest_path`."""
        chunk_size = 16 * 1024
        if isinstance(source, (bytes, bytearray)):
            await asyncio.to_thread(_write_bytes_sync, dest_path, bytes(source))
            if progress_cb is not None:
                progress_cb(100.0, None)
            return
        if isinstance(source, (str, os.PathLike)):
            total = os.path.getsize(os.fspath(source))

            def _copy() -> None:
                with open(os.fspath(source), "rb") as src, open(dest_path, "wb") as dst:
                    while True:
                        chunk = src.read(chunk_size)
                        if not chunk:
                            break
                        dst.write(chunk)

            await asyncio.to_thread(_copy)
            if progress_cb is not None and total > 0:
                progress_cb(100.0, None)
            return
        # async iterator
        with open(dest_path, "wb") as dst:
            async for chunk in source:
                dst.write(chunk)
        if progress_cb is not None:
            progress_cb(100.0, None)

    async def fetch_bytes_from_host(
        self,
        absolute_path: str,
        *,
        progress_cb=None,
    ) -> bytes:
        def _read() -> bytes:
            with open(absolute_path, "rb") as fh:
                return fh.read()

        data = await asyncio.to_thread(_read)
        if progress_cb is not None:
            progress_cb(100.0, None)
        return data

    async def resolve_host_home(self) -> str:
        return os.path.expanduser("~")


def _write_bytes_sync(dest_path: str, data: bytes) -> None:
    with open(dest_path, "wb") as fh:
        fh.write(data)


def _sha256_of_file(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        while True:
            chunk = fh.read(1 << 20)
            if not chunk:
                break
            h.update(chunk)
    return h.hexdigest()


# --- RemoteHost -----------------------------------------------------

import asyncssh

from optio_host.types import SSHConfig


class RemoteHost:
    """Host implementation backed by a single asyncssh connection.

    The connection multiplexes:
      * command exec (run_command, launch_subprocess, tail -F, rm -rf)
      * SFTP (put_file_to_host, write_text, fetch_bytes_from_host)
      * local port forward (establish_tunnel)
    """

    workdir: str
    taskdir: str

    def __init__(self, ssh_config: SSHConfig, taskdir: str):
        self._ssh = ssh_config
        # taskdir is the per-process directory that holds workdir plus
        # any consumer-specific sidecars. workdir is always taskdir/workdir.
        self.taskdir = taskdir
        self.workdir = f"{self.taskdir}/workdir"
        self._conn: asyncssh.SSHClientConnection | None = None
        self._sftp: asyncssh.SFTPClient | None = None
        self._tail_proc: asyncssh.SSHClientProcess | None = None
        self._forward: asyncssh.SSHListener | None = None
        self._host_home_cache: str | None = None

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
        """Create the workdir directory if it does not exist."""
        assert self._conn is not None and self._sftp is not None
        qt = shlex.quote(self.taskdir)
        qw = shlex.quote(self.workdir)
        await self._conn.run(f"mkdir -p {qw}", check=True)
        await self._conn.run(f"chmod 700 {qt} {qw}", check=True)

    async def write_text(self, relpath: str, content: str) -> None:
        assert self._sftp is not None
        remote_path = f"{self.workdir}/{relpath}"
        # Ensure parent dir exists.
        parent = os.path.dirname(remote_path)
        if parent and parent != self.workdir:
            await self._conn.run(f"mkdir -p {parent}", check=True)  # type: ignore[union-attr]
        async with self._sftp.open(remote_path, "w", encoding="utf-8") as fh:
            await fh.write(content)

    async def establish_tunnel(self, remote_port: int) -> int:
        assert self._conn is not None
        self._forward = await self._conn.forward_local_port(
            "127.0.0.1", 0, "127.0.0.1", remote_port
        )
        return self._forward.get_port()

    async def tail_file(self, absolute_path: str) -> AsyncIterator[str]:
        """Async iterator yielding appended lines from ``absolute_path``."""
        assert self._conn is not None
        # See LocalHost.tail_file for why `-n +1` rather than the spec's
        # `-n 0`: fresh workdir + race-free at-least-once delivery.
        self._tail_proc = await self._conn.create_process(
            f"tail -F -n +1 {shlex.quote(absolute_path)}"
        )
        async for raw in self._tail_proc.stdout:
            yield raw.rstrip("\r\n")

    async def cleanup_taskdir(self, aggressive: bool) -> None:
        if self._conn is None:
            return
        # Always await: session.py calls host.disconnect() right after this
        # in the finally block, and a fire-and-forget asyncio task would be
        # aborted as soon as the SSH connection closes. The latency win
        # from skipping the await isn't worth the resulting taskdir leak.
        # `aggressive` is honored by terminate_subprocess (SIGKILL vs SIGTERM)
        # and is ignored here — by the time we reach cleanup, the subprocess
        # is already dead.
        await self._conn.run(
            f"rm -rf {shlex.quote(self.taskdir)}", check=False,
        )

    async def launch_subprocess(
        self,
        command: str,
        *,
        env: dict[str, str] | None = None,
        cwd: str | None = None,
        merge_stderr: bool = True,
    ) -> ProcessHandle:
        assert self._conn is not None
        env_prefix = ""
        if env:
            env_prefix = " ".join(
                f"export {k}={shlex.quote(v)};" for k, v in env.items()
            ) + " "
        cd_prefix = ""
        if cwd is not None:
            cd_prefix = f"cd {shlex.quote(cwd)} && "
        if merge_stderr:
            # Merge stderr into stdout so caller iterating handle.stdout sees both.
            full = f"{cd_prefix}{env_prefix}{command} 2>&1"
        else:
            full = f"{cd_prefix}{env_prefix}{command}"
        proc = await self._conn.create_process(full, encoding=None)

        async def _stream(reader) -> AsyncIterator[bytes]:
            async for chunk in reader:
                if chunk:
                    yield chunk

        if merge_stderr:
            return ProcessHandle(
                pid_like=proc, stdout=_stream(proc.stdout), stderr=None,
            )
        return ProcessHandle(
            pid_like=proc,
            stdout=_stream(proc.stdout),
            stderr=_stream(proc.stderr),
        )

    async def terminate_subprocess(
        self,
        handle: ProcessHandle,
        *,
        aggressive: bool,
    ) -> None:
        proc: asyncssh.SSHClientProcess = handle.pid_like  # type: ignore[assignment]
        if proc.returncode is not None:
            return
        if aggressive:
            # Spec requires SIGKILL in the cancellation path — .terminate()
            # sends SIGTERM, which the subprocess may handle and block on,
            # blowing our shutdown grace budget.
            proc.kill()
            # Best-effort: do not wait.
            return
        proc.terminate()
        try:
            await asyncio.wait_for(proc.wait(), timeout=5.0)
        except asyncio.TimeoutError:
            proc.kill()
            await proc.wait()

    def archive_workdir(
        self, exclude: list[str] | None,
    ) -> "AsyncIterator[bytes]":
        from optio_host.archive import DEFAULT_WORKDIR_EXCLUDES
        assert self._conn is not None
        patterns = list(DEFAULT_WORKDIR_EXCLUDES) if exclude is None else list(exclude)
        excludes = " ".join(f"--exclude={shlex.quote(p)}" for p in patterns)
        cmd = f"cd {shlex.quote(self.workdir)} && tar czf - {excludes} ."

        async def _gen() -> "AsyncIterator[bytes]":
            assert self._conn is not None
            proc = await self._conn.create_process(cmd, encoding=None)
            async for chunk in proc.stdout:
                if chunk:
                    yield chunk
            await proc.wait()
            if proc.exit_status not in (0, None):
                raise RuntimeError(f"remote tar czf - failed (exit {proc.exit_status})")

        return _gen()

    async def restore_workdir(
        self, stream: "AsyncIterator[bytes]",
    ) -> None:
        assert self._conn is not None
        # Empty workdir contents (preserve workdir itself).
        await self._conn.run(
            f"find {shlex.quote(self.workdir)} -mindepth 1 -delete",
            check=False,
        )
        cmd = f"cd {shlex.quote(self.workdir)} && tar xzf -"
        proc = await self._conn.create_process(cmd, encoding=None)
        async for chunk in stream:
            proc.stdin.write(chunk)
            await proc.stdin.drain()
        proc.stdin.write_eof()
        await proc.wait()
        if proc.exit_status not in (0, None):
            raise RuntimeError(f"remote tar xzf - failed (exit {proc.exit_status})")

    async def remove_file(self, path: str) -> None:
        assert self._conn is not None
        # `rm -f` is idempotent: missing files are not an error.
        await self._conn.run(f"rm -f {shlex.quote(path)}", check=False)

    async def fetch_bytes_from_host(
        self,
        absolute_path: str,
        *,
        progress_cb=None,
    ) -> bytes:
        assert self._conn is not None
        sftp = await self._conn.start_sftp_client()
        try:
            try:
                async with sftp.open(absolute_path, "rb") as fh:
                    data = await fh.read()
            except (asyncssh.SFTPError, asyncssh.SFTPNoSuchFile) as exc:
                # asyncssh maps "no such file" to a generic SFTPError in some
                # versions; check the message.
                if "No such file" in str(exc):
                    raise FileNotFoundError(absolute_path) from exc
                raise
            if progress_cb is not None:
                progress_cb(100.0, None)
            return data
        finally:
            sftp.exit()

    async def resolve_host_home(self) -> str:
        if self._host_home_cache is not None:
            return self._host_home_cache
        assert self._conn is not None
        result = await self._conn.run("printf '%s' \"$HOME\"", check=False)
        if result.exit_status != 0 or not result.stdout:
            # Fallback for very stripped-down containers.
            result = await self._conn.run("getent passwd \"$USER\" | cut -d: -f6", check=False)
        home = (result.stdout or "").strip()
        if not home or not home.startswith("/"):
            raise RuntimeError(f"could not resolve $HOME on remote host: {result.stdout!r}")
        self._host_home_cache = home
        return home

    async def run_command(
        self,
        command: str,
        *,
        cwd: str | None = None,
        env: dict[str, str] | None = None,
    ) -> RunResult:
        assert self._conn is not None
        run_cwd = cwd if cwd is not None else self.workdir
        # asyncssh's run() does not support cwd/env kwargs directly.
        # Build a shell invocation that handles both explicitly.
        # We use `export` so that variable expansions inside `command`
        # (e.g. `echo "$X"`) pick up the values set by the caller.
        exports = ""
        if env:
            exports = " ".join(
                f"export {k}={shlex.quote(v)};" for k, v in env.items()
            ) + " "
        full_command = f"cd {shlex.quote(run_cwd)} && {exports}{command}"
        result = await self._conn.run(full_command, check=False)
        stdout = result.stdout or ""
        stderr = result.stderr or ""
        return RunResult(
            stdout=stdout if isinstance(stdout, str) else stdout.decode("utf-8", errors="replace"),
            stderr=stderr if isinstance(stderr, str) else stderr.decode("utf-8", errors="replace"),
            exit_code=result.exit_status if result.exit_status is not None else -1,
        )

    async def put_file_to_host(
        self,
        source,
        absolute_target: str,
        *,
        expected_sha256: str | None = None,
        skip_if_unchanged: bool = False,
        progress_cb=None,
    ) -> None:
        assert self._conn is not None
        parent = os.path.dirname(absolute_target)
        if parent:
            await self._conn.run(f"mkdir -p {shlex.quote(parent)}", check=False)

        # Iterator + skip_if_unchanged + no expected_sha256 is always a
        # contract error, even if the target does not yet exist.
        if (
            skip_if_unchanged
            and expected_sha256 is None
            and not isinstance(source, (bytes, bytearray, str, os.PathLike))
        ):
            raise ValueError(
                "skip_if_unchanged with an AsyncIterator source requires "
                "expected_sha256"
            )

        if skip_if_unchanged:
            target_sha = await self._sha256_of_remote(absolute_target)
            if target_sha is not None:
                source_sha = await self._compute_source_sha_remote(
                    source, expected_sha256,
                )
                if source_sha == target_sha:
                    if progress_cb is not None:
                        progress_cb(None, "already up to date")
                    return

        tmp = absolute_target + ".tmp"
        try:
            await self._stream_source_to_remote(source, tmp, progress_cb)
            mv = await self._conn.run(
                f"mv -f {shlex.quote(tmp)} {shlex.quote(absolute_target)}",
                check=False,
            )
            if mv.exit_status not in (0, None):
                raise RuntimeError(
                    f"mv failed: exit {mv.exit_status}: {mv.stderr!r}"
                )
        except BaseException:
            try:
                await self._conn.run(f"rm -f {shlex.quote(tmp)}", check=False)
            except Exception:
                pass
            raise

    async def _sha256_of_remote(self, path: str) -> str | None:
        """Return the SHA-256 of `path`, or None if the file does not exist."""
        assert self._conn is not None
        # First check existence; we want None for missing, not a sha256sum error.
        check = await self._conn.run(
            f"test -f {shlex.quote(path)}", check=False,
        )
        if check.exit_status != 0:
            return None
        # sha256sum on Linux containers (which is what the test harness runs);
        # on macOS hosts use `shasum -a 256`.
        result = await self._conn.run(
            f"sha256sum {shlex.quote(path)} 2>/dev/null || shasum -a 256 {shlex.quote(path)}",
            check=False,
        )
        if result.exit_status != 0:
            return None
        # Output: "<hex>  <path>"
        first = result.stdout.split(None, 1)[0] if result.stdout else ""
        return first if len(first) == 64 else None

    async def _compute_source_sha_remote(
        self, source, expected_sha256: str | None,
    ) -> str:
        if isinstance(source, (bytes, bytearray)):
            return hashlib.sha256(bytes(source)).hexdigest()
        if isinstance(source, (str, os.PathLike)):
            return await asyncio.to_thread(_sha256_of_file, os.fspath(source))
        # The upfront guard above ensures expected_sha256 is set by this point.
        assert expected_sha256 is not None
        return expected_sha256

    async def _stream_source_to_remote(self, source, remote_path: str, progress_cb) -> None:
        """SFTP-write `source` (path/bytes/async iterator) to `remote_path`."""
        sftp = await self._conn.start_sftp_client()
        try:
            if isinstance(source, (bytes, bytearray)):
                async with sftp.open(remote_path, "wb") as fh:
                    await fh.write(bytes(source))
                if progress_cb is not None:
                    progress_cb(100.0, None)
                return
            if isinstance(source, (str, os.PathLike)):
                # asyncssh's put streams from disk and reports progress.
                def _progress_adapter(_src, _dst, transferred, total):
                    if progress_cb is not None and total:
                        progress_cb(min(100.0, transferred * 100.0 / total), None)
                await sftp.put(
                    os.fspath(source), remote_path,
                    progress_handler=_progress_adapter,
                )
                return
            # async iterator
            async with sftp.open(remote_path, "wb") as fh:
                async for chunk in source:
                    await fh.write(chunk)
            if progress_cb is not None:
                progress_cb(100.0, None)
        finally:
            sftp.exit()


# --- factory --------------------------------------------------------------


def make_host(*, ssh: SSHConfig | None, taskdir: str) -> Host:
    """Construct ``LocalHost`` (when ``ssh is None``) or ``RemoteHost``.

    Use this from consumer code instead of naming the implementation
    classes directly. Keeps the local-vs-remote choice in one place
    rather than scattered across the consumer.
    """
    if ssh is None:
        return LocalHost(taskdir=taskdir)
    return RemoteHost(ssh_config=ssh, taskdir=taskdir)
