"""RemoteHost.archive_workdir: block reads, compressor choice, failure paths.

The capture used to iterate asyncssh's stdout with ``async for``, which is a
``readline()`` loop: a gzip stream arrived as ~300-byte pieces and each one
cost a GridFS write, capping snapshots at ~1 MiB/s (see
docs/2026-09-13-snapshot-archive-throughput-design.md). These tests pin the
fix without a live SSH connection:

* the archive command itself is run locally through ``/bin/sh`` exactly as a
  remote login shell would run it, so excludes quoting and ``pipefail`` are
  checked against real ``bash``/``tar``/``gzip``/``pigz``;
* the read loop and the compressor probe run against a fake connection whose
  stdout, like asyncssh's, also supports line iteration.

The docker-gated round trip over a real sshd lives in optio-opencode's
test_host_remote_resume.py.
"""

from __future__ import annotations

import io
import os
import shutil
import subprocess
import tarfile

import pytest

from optio_host import host as host_mod
from optio_host.host import RemoteHost
from optio_host.types import SSHConfig


def _compressors() -> list:
    return [
        "gzip -1",
        pytest.param(
            "pigz -1",
            marks=pytest.mark.skipif(
                shutil.which("pigz") is None, reason="pigz not installed",
            ),
        ),
    ]


# --- _archive_command, executed locally ------------------------------------


@pytest.mark.parametrize("compressor", _compressors())
def test_archive_command_roundtrips_workdir_and_honours_excludes(
    tmp_workdir, compressor,
):
    for relpath in ("keep.txt", "sub/keep2.txt", "skip me/x", "it's/y",
                    "node_modules/z"):
        path = os.path.join(tmp_workdir, relpath)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w") as fh:
            fh.write(relpath)

    cmd = host_mod._archive_command(
        tmp_workdir, ["skip me", "it's", "node_modules"], compressor,
    )
    proc = subprocess.run(cmd, shell=True, capture_output=True)

    assert proc.returncode == 0, proc.stderr
    with tarfile.open(fileobj=io.BytesIO(proc.stdout), mode="r:gz") as tf:
        names = {m.name for m in tf.getmembers() if m.isfile()}
    assert names == {"./keep.txt", "./sub/keep2.txt"}


@pytest.mark.skipif(os.geteuid() == 0, reason="root reads unreadable files")
@pytest.mark.parametrize("compressor", _compressors())
def test_archive_command_fails_when_tar_fails_inside_the_pipe(
    tmp_workdir, compressor,
):
    secret = os.path.join(tmp_workdir, "secret")
    with open(secret, "w") as fh:
        fh.write("x")
    os.chmod(secret, 0)
    try:
        cmd = host_mod._archive_command(tmp_workdir, [], compressor)
        proc = subprocess.run(cmd, shell=True, capture_output=True)
    finally:
        os.chmod(secret, 0o600)

    # The compressor exits 0; only pipefail carries tar's failure out.
    assert proc.returncode != 0


# --- RemoteHost.archive_workdir against a fake connection -------------------


class _FakeStdout:
    """Serves ``data`` through read(n), capped at ``max_piece`` per call like
    asyncssh (~200 KiB), and through ``async for`` in small pieces like
    asyncssh's readline()-based iterator."""

    def __init__(self, data: bytes, max_piece: int = 200 * 1024) -> None:
        self._data = data
        self._max_piece = max_piece
        self.requested: list[int] = []

    async def read(self, n: int = -1) -> bytes:
        self.requested.append(n)
        take = len(self._data) if n < 0 else min(n, self._max_piece)
        piece, self._data = self._data[:take], self._data[take:]
        return piece

    def __aiter__(self):
        return self

    async def __anext__(self) -> bytes:
        if not self._data:
            raise StopAsyncIteration
        piece, self._data = self._data[:307], self._data[307:]
        return piece


class _FakeProc:
    def __init__(self, stdout: _FakeStdout, exit_status: int) -> None:
        self.stdout = stdout
        self.exit_status = exit_status

    async def wait(self) -> None:
        return None


class _RunResult:
    def __init__(self, exit_status: int) -> None:
        self.exit_status = exit_status


class _FakeConn:
    def __init__(self, *, pigz: bool, data: bytes = b"", exit_status: int = 0):
        self._pigz = pigz
        self._data = data
        self._exit_status = exit_status
        self.run_commands: list[str] = []
        self.created: list[str] = []
        self.procs: list[_FakeProc] = []

    async def run(self, cmd: str, check: bool = False) -> _RunResult:
        self.run_commands.append(cmd)
        if "command -v pigz" in cmd:
            return _RunResult(0 if self._pigz else 1)
        return _RunResult(0)

    async def create_process(self, cmd: str, encoding=None) -> _FakeProc:
        self.created.append(cmd)
        proc = _FakeProc(_FakeStdout(self._data), self._exit_status)
        self.procs.append(proc)
        return proc


def _host(conn: _FakeConn) -> RemoteHost:
    h = RemoteHost(
        ssh_config=SSHConfig(host="h", user="u", key_path="/k", port=22),
        taskdir="/t",
    )
    h._conn = conn  # type: ignore[assignment]
    return h


async def _drain(it) -> bytes:
    out = b""
    async for chunk in it:
        out += chunk
    return out


async def test_archive_workdir_reads_256_kib_blocks_until_eof():
    data = os.urandom(1024 * 1024 + 12345)
    conn = _FakeConn(pigz=True, data=data)

    out = await _drain(_host(conn).archive_workdir(exclude=None))

    assert out == data
    stdout = conn.procs[0].stdout
    assert stdout.requested, "stdout was line-iterated instead of read()"
    assert set(stdout.requested) == {256 * 1024}
    # 200 KiB pieces: 6 carrying data, then the EOF read that ends the loop.
    assert len(stdout.requested) == 7


async def test_archive_workdir_compresses_with_pigz_when_present():
    conn = _FakeConn(pigz=True, data=b"x")

    await _drain(_host(conn).archive_workdir(exclude=["a b"]))

    assert conn.created == [host_mod._archive_command("/t/workdir", ["a b"], "pigz -1")]


async def test_archive_workdir_falls_back_to_gzip_without_pigz():
    conn = _FakeConn(pigz=False, data=b"x")

    await _drain(_host(conn).archive_workdir(exclude=["a b"]))

    assert conn.created == [host_mod._archive_command("/t/workdir", ["a b"], "gzip -1")]


async def test_archive_workdir_probes_for_pigz_once_per_connection():
    conn = _FakeConn(pigz=True, data=b"x")
    h = _host(conn)

    await _drain(h.archive_workdir(exclude=None))
    await _drain(h.archive_workdir(exclude=None))

    assert [c for c in conn.run_commands if "pigz" in c] == ["command -v pigz"]
    assert len(conn.created) == 2


async def test_archive_workdir_raises_when_the_remote_pipeline_fails():
    conn = _FakeConn(pigz=True, data=b"partial", exit_status=2)

    with pytest.raises(RuntimeError, match="exit 2"):
        await _drain(_host(conn).archive_workdir(exclude=None))
