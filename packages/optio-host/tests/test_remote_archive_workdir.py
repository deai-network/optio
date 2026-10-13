"""RemoteHost.archive_workdir: block reads, compressor choice, failure paths.

The capture used to iterate asyncssh's stdout with ``async for``, which is a
``readline()`` loop: a gzip stream arrived as ~300-byte pieces and each one
cost a GridFS write, capping snapshots at ~1 MiB/s (see
docs/2026-09-13-snapshot-archive-throughput-design.md). These tests pin the
fix without a live SSH connection:

* the archive command itself is run locally through ``/bin/sh`` exactly as a
  remote login shell would run it, so quoting and ``pipefail`` are checked
  against real ``bash``/``tar``/``gzip``/``pigz``;
* the read loop, the compressor probe, stderr handling and cleanup run
  against a fake connection whose stdout, like asyncssh's, also supports line
  iteration.

The docker-gated round trip over a real sshd lives in optio-opencode's
test_host_remote_resume.py.

The capture cap (``OPTIO_WORKDIR_CAPTURE_MAX_MB``) is off for every test here
unless the test turns it on, so the host's environment never leaks in; the
capture-cap section turns it on and answers its ``du``, its list read and
its tar-flavour probe through the fake connection's ``run``.
"""

from __future__ import annotations

import asyncio
import io
import logging
import os
import shutil
import subprocess
import tarfile

import pytest

from optio_host import capture_cap
from optio_host import host as host_mod
from optio_host.capture_cap import ENV_VAR
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
    # Shell metacharacters in the workdir and the excludes must survive both
    # quoting layers (login shell, then bash -c) as literals.
    workdir = os.path.join(tmp_workdir, "w d's $HOME")
    for relpath in ("keep.txt", "sub/keep2.txt", "skip me/x", "it's/y",
                    "node_modules/z", "$(touch pwned)/q"):
        path = os.path.join(workdir, relpath)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w") as fh:
            fh.write(relpath)

    cmd = host_mod._archive_command(
        workdir, ["skip me", "it's", "node_modules", "$(touch pwned)"], compressor,
    )
    proc = subprocess.run(cmd, shell=True, capture_output=True, cwd=tmp_workdir)

    assert proc.returncode == 0, proc.stderr
    with tarfile.open(fileobj=io.BytesIO(proc.stdout), mode="r:gz") as tf:
        names = {m.name for m in tf.getmembers() if m.isfile()}
    assert names == {"./keep.txt", "./sub/keep2.txt"}
    assert not os.path.exists(os.path.join(tmp_workdir, "pwned"))


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


class _FakeStream:
    """Serves ``data`` through read(n), capped at ``max_piece`` per call like
    asyncssh (~200 KiB), and through ``async for`` in small pieces like
    asyncssh's readline()-based iterator.

    ``gate``: read() blocks until this event is set (models asyncssh pausing
    stdout while unread stderr fills the channel window). ``on_eof``: set
    once read() has returned EOF."""

    def __init__(
        self,
        data: bytes,
        *,
        max_piece: int = 200 * 1024,
        gate: asyncio.Event | None = None,
        on_eof: asyncio.Event | None = None,
    ) -> None:
        self._data = data
        self._max_piece = max_piece
        self._gate = gate
        self._on_eof = on_eof
        self.requested: list[int] = []

    async def read(self, n: int = -1) -> bytes:
        self.requested.append(n)
        if self._gate is not None:
            await self._gate.wait()
        take = len(self._data) if n < 0 else min(n, self._max_piece)
        piece, self._data = self._data[:take], self._data[take:]
        if not piece and self._on_eof is not None:
            self._on_eof.set()
        return piece

    def __aiter__(self):
        return self

    async def __anext__(self) -> bytes:
        if not self._data:
            raise StopAsyncIteration
        piece, self._data = self._data[:307], self._data[307:]
        return piece


class _FakeProc:
    """``exit_status``/``returncode`` stay None until wait(), like asyncssh's
    before the exit-status message arrives -- unless ``status_arrived``, the
    exit status having come in while stdout is still buffered. wait() drops
    unread output, like asyncssh's wait() (communicate() -> collect_output())."""

    def __init__(
        self,
        stdout: _FakeStream,
        stderr: _FakeStream,
        exit_status: int | None,
        *,
        status_arrived: bool = False,
    ) -> None:
        self.stdout = stdout
        self.stderr = stderr
        self._final_status = exit_status
        self._finished = status_arrived
        self.closed = False

    @property
    def exit_status(self) -> int | None:
        return self._final_status if self._finished else None

    @property
    def returncode(self) -> int | None:
        return self.exit_status

    async def wait(self) -> None:
        self._finished = True
        self.stdout._data = b""
        self.stderr._data = b""

    def close(self) -> None:
        self.closed = True


class _RunResult:
    def __init__(self, exit_status: int, stdout: str = "") -> None:
        self.exit_status = exit_status
        self.stdout = stdout


class _FakeConn:
    def __init__(
        self,
        *,
        pigz: bool,
        data: bytes = b"",
        stderr: bytes = b"",
        exit_status: int | None = 0,
        stall_stdout_until_stderr_drained: bool = False,
        stdout_gate: asyncio.Event | None = None,
        exit_status_arrives_early: bool = False,
        du: "tuple[int, str] | Exception" = (0, "4096\t.\n"),
        regenerable: tuple[int, str] = (1, ""),
        tar_version: str = "tar (GNU tar) 1.35\n",
    ):
        self._pigz = pigz
        self._data = data
        self._stderr = stderr
        self._exit_status = exit_status
        self._stall = stall_stdout_until_stderr_drained
        self._stdout_gate = stdout_gate
        self._early = exit_status_arrives_early
        self._du = du
        self._regenerable = regenerable
        self._tar_version = tar_version
        self.run_commands: list[str] = []
        self.run_kwargs: list[dict] = []
        self.created: list[str] = []
        self.procs: list[_FakeProc] = []

    async def run(self, cmd: str, check: bool = False, **kwargs) -> _RunResult:
        self.run_commands.append(cmd)
        self.run_kwargs.append(kwargs)
        if "command -v pigz" in cmd:
            return _RunResult(0 if self._pigz else 1)
        if " du -sb " in cmd:
            if isinstance(self._du, Exception):
                raise self._du
            return _RunResult(*self._du)
        if "-- regenerable.txt" in cmd:
            return _RunResult(*self._regenerable)
        if cmd == capture_cap.TAR_VERSION_COMMAND:
            return _RunResult(0, self._tar_version)
        return _RunResult(0)

    async def create_process(self, cmd: str, encoding=None) -> _FakeProc:
        self.created.append(cmd)
        stderr_eof = asyncio.Event()
        gate = stderr_eof if self._stall else self._stdout_gate
        proc = _FakeProc(
            _FakeStream(self._data, gate=gate),
            _FakeStream(self._stderr, on_eof=stderr_eof),
            self._exit_status,
            status_arrived=self._early,
        )
        self.procs.append(proc)
        return proc


@pytest.fixture(autouse=True)
def _capture_cap_off(monkeypatch):
    monkeypatch.setenv(ENV_VAR, "0")


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
    assert not conn.procs[0].closed


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


async def test_archive_workdir_raises_when_the_channel_reports_no_exit_status():
    conn = _FakeConn(pigz=True, data=b"partial", exit_status=None)

    with pytest.raises(RuntimeError, match="no exit status"):
        await _drain(_host(conn).archive_workdir(exclude=None))


async def test_archive_workdir_error_carries_the_remote_stderr():
    conn = _FakeConn(
        pigz=True, exit_status=127, stderr=b"sh: 1: bash: not found\n",
    )

    with pytest.raises(RuntimeError, match="exit 127.*bash: not found"):
        await _drain(_host(conn).archive_workdir(exclude=None))


async def test_archive_workdir_error_keeps_only_the_tail_of_stderr():
    noise = b"tar: noise\n" * 2000 + b"tar: the real cause\n"
    conn = _FakeConn(pigz=True, exit_status=2, stderr=noise)

    with pytest.raises(RuntimeError) as excinfo:
        await _drain(_host(conn).archive_workdir(exclude=None))

    assert "the real cause" in str(excinfo.value)
    assert len(str(excinfo.value)) < 5000


async def test_archive_workdir_drains_stderr_while_stdout_streams():
    conn = _FakeConn(
        pigz=True, data=b"x" * 1000, stderr=b"tar: warning\n" * 100,
        stall_stdout_until_stderr_drained=True,
    )

    # 60 s bounds a true hang only: without a concurrent stderr reader the
    # stalled stdout never delivers.
    out = await asyncio.wait_for(
        _drain(_host(conn).archive_workdir(exclude=None)), 60,
    )

    assert out == b"x" * 1000


async def test_archive_workdir_closes_the_remote_pipeline_when_abandoned():
    conn = _FakeConn(pigz=True, data=os.urandom(1024 * 1024))
    gen = _host(conn).archive_workdir(exclude=None)

    await gen.__anext__()
    await gen.aclose()

    assert conn.procs[0].closed


async def test_archive_workdir_closes_the_channel_when_abandoned_after_the_exit_status():
    # The exit status can arrive while stdout is still buffered; the channel
    # keeps holding that data until it is closed.
    conn = _FakeConn(
        pigz=True, data=os.urandom(1024 * 1024), exit_status_arrives_early=True,
    )
    gen = _host(conn).archive_workdir(exclude=None)

    await gen.__anext__()
    await gen.aclose()

    assert conn.procs[0].closed


async def test_archive_workdir_closes_the_remote_pipeline_when_cancelled():
    conn = _FakeConn(pigz=True, data=b"x", stdout_gate=asyncio.Event())
    consumer = asyncio.create_task(
        _drain(_host(conn).archive_workdir(exclude=None)),
    )
    for _ in range(6000):  # bounded poll: until the consumer blocks in read()
        if conn.procs and conn.procs[0].stdout.requested:
            break
        await asyncio.sleep(0.01)
    assert conn.procs and conn.procs[0].stdout.requested

    consumer.cancel()
    with pytest.raises(asyncio.CancelledError):
        await consumer

    assert conn.procs[0].closed


async def test_archive_workdir_start_trace_names_workdir_and_compressor(
    monkeypatch, caplog,
):
    monkeypatch.setattr(host_mod, "_CANCEL_TRACE", True)
    conn = _FakeConn(pigz=True, data=b"x")

    with caplog.at_level(logging.WARNING, logger="optio_core.cancel_trace"):
        await _drain(_host(conn).archive_workdir(exclude=["a b"]))

    start = [r.getMessage() for r in caplog.records
             if "archive_workdir START" in r.getMessage()]
    assert len(start) == 1
    assert "workdir=/t/workdir" in start[0]
    assert "compressor=pigz -1" in start[0]
    assert "bash -c" not in start[0]


# --- capture cap ---------------------------------------------------------------

_MIB = 1024 * 1024
_W = "/t/workdir"


def _cap_commands(conn: _FakeConn) -> list[str]:
    """The commands the capture decision ran (everything but the pigz probe)."""
    return [c for c in conn.run_commands if "pigz" not in c]


async def test_archive_workdir_over_the_cap_excludes_the_listed_paths(
    monkeypatch, caplog,
):
    monkeypatch.setenv(ENV_VAR, "1")
    conn = _FakeConn(
        pigz=True, data=b"x", du=(0, f"{2 * _MIB}\t.\n"), regenerable=(0, "big\n"),
    )
    notes: list[str] = []

    with caplog.at_level(logging.INFO, logger="optio_host.archive"):
        await _drain(_host(conn).archive_workdir(exclude=["a b"], on_note=notes.append))

    assert _cap_commands(conn) == [
        capture_cap.du_command(_W, ["a b"]),
        "cd /t/workdir && [ -f regenerable.txt ] && "
        "timeout 1 head -c 65536 -- regenerable.txt",
        "tar --version 2>&1 | head -1",
    ]
    # Decoded with errors="replace", so a binary list cannot raise.
    assert [kw for c, kw in zip(conn.run_commands, conn.run_kwargs) if "pigz" not in c] == [
        {"errors": "replace"}, {"errors": "replace"}, {"errors": "replace"},
    ]
    assert conn.created == [host_mod._archive_command(_W, ["a b"], "pigz -1", ["big"])]
    assert "--exclude=./big . |" in conn.created[0]
    assert "--anchored" not in conn.created[0]
    assert notes == ["workdir 2 MB (cap 1 MB): left out 1 path listed in regenerable.txt"]
    assert [(r.levelno, r.getMessage()) for r in caplog.records
            if r.name == "optio_host.archive"] == [(logging.INFO, notes[0])]


async def test_archive_workdir_under_the_cap_measures_and_captures_everything(monkeypatch):
    monkeypatch.setenv(ENV_VAR, "100")
    conn = _FakeConn(pigz=True, data=b"x", regenerable=(0, "big\n"))
    notes: list[str] = []

    await _drain(_host(conn).archive_workdir(exclude=["a b"], on_note=notes.append))

    assert _cap_commands(conn) == [capture_cap.du_command(_W, ["a b"])]
    assert conn.created == [host_mod._archive_command(_W, ["a b"], "pigz -1")]
    assert notes == []


async def test_archive_workdir_cap_zero_runs_no_du(monkeypatch):
    monkeypatch.setenv(ENV_VAR, "0")
    conn = _FakeConn(pigz=True, data=b"x", du=(0, f"{2 * _MIB}\t.\n"), regenerable=(0, "big\n"))
    notes: list[str] = []

    await _drain(_host(conn).archive_workdir(exclude=["a b"], on_note=notes.append))

    assert _cap_commands(conn) == []
    assert conn.created == [host_mod._archive_command(_W, ["a b"], "pigz -1")]
    assert notes == []


async def test_archive_workdir_du_timeout_without_a_list_warns_and_captures_everything(
    monkeypatch, caplog,
):
    monkeypatch.setenv(ENV_VAR, "1")
    conn = _FakeConn(pigz=True, data=b"x", du=(124, ""), regenerable=(1, ""))
    notes: list[str] = []

    with caplog.at_level(logging.INFO, logger="optio_host.archive"):
        await _drain(_host(conn).archive_workdir(exclude=["a b"], on_note=notes.append))

    assert conn.created == [host_mod._archive_command(_W, ["a b"], "pigz -1")]
    assert notes == [
        "workdir size unknown (du timed out after 1 s) is over the 1 MB capture cap, "
        "but regenerable.txt is missing or empty; capturing everything"
    ]
    assert [(r.levelno, r.getMessage()) for r in caplog.records
            if r.name == "optio_host.archive"] == [(logging.WARNING, notes[0])]


async def test_archive_workdir_decision_that_raises_captures_everything(monkeypatch, caplog):
    monkeypatch.setenv(ENV_VAR, "1")
    data = os.urandom(300 * 1024)
    conn = _FakeConn(pigz=True, data=data, du=ConnectionError("channel open failed"))
    notes: list[str] = []

    with caplog.at_level(logging.INFO, logger="optio_host.archive"):
        out = await _drain(_host(conn).archive_workdir(exclude=["a b"], on_note=notes.append))

    assert out == data
    assert conn.created == [host_mod._archive_command(_W, ["a b"], "pigz -1")]
    assert notes == [
        "capture decision failed (ConnectionError('channel open failed')); "
        "capturing everything"
    ]
    assert [(r.levelno, r.getMessage()) for r in caplog.records
            if r.name == "optio_host.archive"] == [(logging.WARNING, notes[0])]


async def test_archive_workdir_failed_decision_survives_an_on_note_that_raises(monkeypatch):
    monkeypatch.setenv(ENV_VAR, "1")
    data = os.urandom(300 * 1024)
    conn = _FakeConn(pigz=True, data=data, du=ConnectionError("channel open failed"))

    def boom(note: str) -> None:
        raise RuntimeError("on_note exploded")

    out = await _drain(_host(conn).archive_workdir(exclude=["a b"], on_note=boom))

    assert out == data
    assert conn.created == [host_mod._archive_command(_W, ["a b"], "pigz -1")]


async def test_archive_workdir_on_note_that_raises_does_not_stop_the_capture(monkeypatch):
    monkeypatch.setenv(ENV_VAR, "1")
    data = os.urandom(300 * 1024)
    conn = _FakeConn(
        pigz=True, data=data, du=(0, f"{2 * _MIB}\t.\n"), regenerable=(0, "big\n"),
    )

    def boom(note: str) -> None:
        raise RuntimeError("on_note exploded")

    out = await _drain(_host(conn).archive_workdir(exclude=["a b"], on_note=boom))

    assert out == data
    assert conn.created == [host_mod._archive_command(_W, ["a b"], "pigz -1", ["big"])]


_BSDTAR = "bsdtar 3.7.2 - libarchive 3.7.2 zlib/1.3.1 liblzma/5.4.3 bz2lib/1.0.8"


async def test_archive_workdir_tar_neither_gnu_nor_busybox_captures_everything(
    monkeypatch, caplog,
):
    # bsdtar strips a pattern's leading ./, so ./big would match anywhere.
    monkeypatch.setenv(ENV_VAR, "1")
    conn = _FakeConn(
        pigz=True, data=b"x", du=(0, f"{2 * _MIB}\t.\n"), regenerable=(0, "big\n"),
        tar_version=_BSDTAR + "\n",
    )
    notes: list[str] = []

    with caplog.at_level(logging.INFO, logger="optio_host.archive"):
        await _drain(_host(conn).archive_workdir(exclude=["a b"], on_note=notes.append))

    assert conn.created == [host_mod._archive_command(_W, ["a b"], "pigz -1")]
    assert notes == [
        "regenerable.txt not applied: tar on this host is not GNU or BusyBox "
        f"({_BSDTAR!r}); capturing everything"
    ]
    assert [(r.levelno, r.getMessage()) for r in caplog.records
            if r.name == "optio_host.archive"] == [(logging.WARNING, notes[0])]


async def test_archive_workdir_busybox_tar_takes_the_list(monkeypatch):
    monkeypatch.setenv(ENV_VAR, "1")
    conn = _FakeConn(
        pigz=True, data=b"x", du=(0, f"{2 * _MIB}\t.\n"), regenerable=(0, "big\n"),
        tar_version="tar (busybox) 1.37.0\n",
    )

    await _drain(_host(conn).archive_workdir(exclude=["a b"]))

    assert conn.created == [host_mod._archive_command(_W, ["a b"], "pigz -1", ["big"])]


async def test_archive_workdir_probes_the_tar_flavour_once_per_connection(monkeypatch):
    monkeypatch.setenv(ENV_VAR, "1")
    conn = _FakeConn(
        pigz=True, data=b"x", du=(0, f"{2 * _MIB}\t.\n"), regenerable=(0, "big\n"),
    )
    h = _host(conn)

    await _drain(h.archive_workdir(exclude=["a b"]))
    await _drain(h.archive_workdir(exclude=["a b"]))

    assert conn.run_commands.count(capture_cap.TAR_VERSION_COMMAND) == 1
    assert conn.created == 2 * [host_mod._archive_command(_W, ["a b"], "pigz -1", ["big"])]
