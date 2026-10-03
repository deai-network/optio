"""LocalHost.archive_workdir: streamed tar | pigz -1 / gzip -1 pipeline.

LocalHost used to archive with Python ``tarfile`` at zlib level 9, single
threaded and fully in memory: 62 s for a 743 MiB workdir against a 30 s cancel
grace, so the snapshot never landed (see
docs/2026-10-03-local-archive-throughput-design.md). These tests pin the
replacement against real local processes; only ``shutil.which`` is faked, to
choose between the pipeline's compressors and the in-process fallback.
"""

from __future__ import annotations

import asyncio
import io
import logging
import os
import shutil
import tarfile

import pytest

from optio_host import host as host_mod
from optio_host.host import LocalHost


# gzip header byte 8 (XFL): 4 = fastest compression, 2 = maximum.
_XFL_FASTEST = 4


def _write(root: str, relpath: str, content: str | bytes = "x") -> None:
    path = os.path.join(root, relpath)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    mode = "wb" if isinstance(content, bytes) else "w"
    with open(path, mode) as fh:
        fh.write(content)


def _populate(root: str) -> None:
    _write(root, "hello.txt", "hi")
    _write(root, "sub/keep.py", "x = 1\n")
    _write(root, ".git/HEAD", "ref: refs/heads/main\n")
    _write(root, "node_modules/pkg/index.js", "//")
    _write(root, "__pycache__/mod.cpython-311.pyc", b"\xff\xff")
    _write(root, "mod.pyc", b"\xff\xff")
    _write(root, "run.log", "log")


def _host(tmp_workdir: str, name: str = "src") -> LocalHost:
    h = LocalHost(taskdir=os.path.join(tmp_workdir, name))
    os.makedirs(h.workdir, exist_ok=True)
    return h


async def _drain(it) -> bytes:
    out = b""
    async for chunk in it:
        out += chunk
    return out


def _members(blob: bytes) -> dict[str, tarfile.TarInfo]:
    with tarfile.open(fileobj=io.BytesIO(blob), mode="r:gz") as tf:
        return {m.name.removeprefix("./"): m for m in tf.getmembers()}


def _without(monkeypatch, *tools: str) -> None:
    """Make ``shutil.which`` (as seen by optio_host.host) miss ``tools``."""
    real_which = shutil.which

    def which(cmd, *args, **kwargs):
        if cmd in tools:
            return None
        return real_which(cmd, *args, **kwargs)

    monkeypatch.setattr(host_mod.shutil, "which", which)


@pytest.fixture
def spawned(monkeypatch):
    """Record every asyncio subprocess started during the test."""
    procs: list = []
    real_exec = asyncio.create_subprocess_exec

    async def recording_exec(*args, **kwargs):
        proc = await real_exec(*args, **kwargs)
        procs.append((args, proc))
        return proc

    monkeypatch.setattr(asyncio, "create_subprocess_exec", recording_exec)
    return procs


@pytest.fixture
def fake_pigz(tmp_workdir, monkeypatch):
    """A ``pigz`` on PATH that records its argv and execs ``gzip``."""
    bindir = os.path.join(tmp_workdir, "fakebin")
    os.makedirs(bindir)
    marker = os.path.join(tmp_workdir, "pigz-argv")
    script = os.path.join(bindir, "pigz")
    with open(script, "w") as fh:
        fh.write(f'#!/bin/sh\necho "$@" > {marker}\nexec gzip "$@"\n')
    os.chmod(script, 0o755)
    monkeypatch.setenv("PATH", bindir + os.pathsep + os.environ["PATH"])
    return marker


def _group_alive(pgid: int) -> bool:
    try:
        os.killpg(pgid, 0)
    except ProcessLookupError:
        return False
    return True


async def _wait_group_gone(pgid: int) -> bool:
    for _ in range(6000):  # bounded poll; the ceiling only bounds a hang
        if not _group_alive(pgid):
            return True
        await asyncio.sleep(0.01)
    return False


# --- round trips -------------------------------------------------------------


async def test_roundtrip_with_default_excludes(tmp_workdir):
    src, dst = _host(tmp_workdir), _host(tmp_workdir, "dst")
    _populate(src.workdir)

    await dst.restore_workdir(src.archive_workdir(None))

    assert open(os.path.join(dst.workdir, "hello.txt")).read() == "hi"
    assert open(os.path.join(dst.workdir, "sub", "keep.py")).read() == "x = 1\n"
    assert open(os.path.join(dst.workdir, "run.log")).read() == "log"
    for gone in (".git", "node_modules", "__pycache__", "mod.pyc"):
        assert not os.path.exists(os.path.join(dst.workdir, gone)), gone


async def test_explicit_excludes_are_verbatim_not_merged_with_defaults(tmp_workdir):
    src, dst = _host(tmp_workdir), _host(tmp_workdir, "dst")
    _populate(src.workdir)

    await dst.restore_workdir(src.archive_workdir(["*.log"]))

    assert not os.path.exists(os.path.join(dst.workdir, "run.log"))
    assert os.path.exists(os.path.join(dst.workdir, ".git", "HEAD"))
    assert os.path.exists(os.path.join(dst.workdir, "node_modules", "pkg", "index.js"))


async def test_empty_exclude_list_captures_everything(tmp_workdir):
    src, dst = _host(tmp_workdir), _host(tmp_workdir, "dst")
    _populate(src.workdir)

    await dst.restore_workdir(src.archive_workdir([]))

    for kept in (".git/HEAD", "node_modules/pkg/index.js", "mod.pyc", "run.log"):
        assert os.path.exists(os.path.join(dst.workdir, kept)), kept


async def test_metacharacters_in_workdir_and_excludes_stay_literal(tmp_workdir):
    h = LocalHost(taskdir=os.path.join(tmp_workdir, "t d's $HOME"))
    for relpath in ("keep.txt", "skip me/x", "it's/y", "$(touch pwned)/q"):
        _write(h.workdir, relpath, relpath)

    blob = await _drain(h.archive_workdir(["skip me", "it's", "$(touch pwned)"]))

    files = {n for n, m in _members(blob).items() if m.isfile()}
    assert files == {"keep.txt"}
    assert not os.path.exists(os.path.join(tmp_workdir, "pwned"))


async def test_symlinks_are_stored_as_symlinks_not_followed(tmp_workdir):
    src, dst = _host(tmp_workdir), _host(tmp_workdir, "dst")
    _write(src.workdir, "data/inner.txt", "inner")
    os.symlink("data/inner.txt", os.path.join(src.workdir, "file_link"))
    os.symlink("data", os.path.join(src.workdir, "dir_link"))

    blob = await _drain(src.archive_workdir(None))

    members = _members(blob)
    assert members["file_link"].issym() and members["file_link"].linkname == "data/inner.txt"
    assert members["dir_link"].issym() and members["dir_link"].linkname == "data"
    assert "dir_link/inner.txt" not in members

    async def replay():
        yield blob

    await dst.restore_workdir(replay())
    assert os.readlink(os.path.join(dst.workdir, "dir_link")) == "data"
    assert open(os.path.join(dst.workdir, "dir_link", "inner.txt")).read() == "inner"


# --- compression ---------------------------------------------------------------


async def test_uses_pigz_at_the_fastest_level_when_present(
    tmp_workdir, fake_pigz, spawned,
):
    h = _host(tmp_workdir)
    _populate(h.workdir)

    blob = await _drain(h.archive_workdir(None))

    assert open(fake_pigz).read().split() == ["-1"]
    assert blob[8] == _XFL_FASTEST
    assert len(spawned) == 1


async def test_falls_back_to_gzip_at_the_fastest_level_without_pigz(
    tmp_workdir, monkeypatch, spawned,
):
    _without(monkeypatch, "pigz")
    h = _host(tmp_workdir)
    _populate(h.workdir)

    blob = await _drain(h.archive_workdir(None))

    assert len(spawned) == 1
    cmdline = " ".join(spawned[0][0])
    assert "gzip -1" in cmdline and "pigz" not in cmdline
    assert blob[8] == _XFL_FASTEST


@pytest.mark.parametrize("missing", ["tar", "bash"])
async def test_without_tar_or_bash_archives_in_process_at_the_fastest_level(
    tmp_workdir, monkeypatch, spawned, missing,
):
    _without(monkeypatch, missing)
    src, dst = _host(tmp_workdir), _host(tmp_workdir, "dst")
    _populate(src.workdir)

    blob = await _drain(src.archive_workdir(None))

    assert spawned == []
    assert blob[8] == _XFL_FASTEST

    async def replay():
        yield blob

    await dst.restore_workdir(replay())
    assert open(os.path.join(dst.workdir, "hello.txt")).read() == "hi"
    assert not os.path.exists(os.path.join(dst.workdir, ".git"))


# --- streaming -------------------------------------------------------------------


async def test_streams_in_blocks_no_larger_than_the_read_block(tmp_workdir):
    h = _host(tmp_workdir)
    _write(h.workdir, "big.bin", os.urandom(2 * 1024 * 1024))

    chunks = [c async for c in h.archive_workdir(None)]

    assert len(chunks) > 1
    assert max(len(c) for c in chunks) <= host_mod._ARCHIVE_READ_BLOCK
    assert _members(b"".join(chunks))["big.bin"].size == 2 * 1024 * 1024


# --- failures and cleanup ----------------------------------------------------------


@pytest.mark.skipif(os.geteuid() == 0, reason="root reads unreadable files")
async def test_raises_with_the_stderr_tail_when_tar_fails(tmp_workdir, spawned):
    h = _host(tmp_workdir)
    _write(h.workdir, "secret.txt", "s")
    os.chmod(os.path.join(h.workdir, "secret.txt"), 0)

    with pytest.raises(RuntimeError) as excinfo:
        await _drain(h.archive_workdir(None))

    assert len(spawned) == 1
    assert "local workdir archive failed" in str(excinfo.value)
    assert "secret.txt" in str(excinfo.value)


async def test_kills_the_pipeline_when_the_stream_is_abandoned(tmp_workdir, spawned):
    h = _host(tmp_workdir)
    # Far more than the pipe and reader buffers hold, so the pipeline is
    # still blocked writing when the consumer walks away.
    _write(h.workdir, "big.bin", os.urandom(8 * 1024 * 1024))
    gen = h.archive_workdir(None)

    await gen.__anext__()
    assert len(spawned) == 1
    pgid = spawned[0][1].pid
    assert _group_alive(pgid)
    await gen.aclose()

    assert await _wait_group_gone(pgid)
    assert spawned[0][1].returncode is not None


async def test_kills_the_pipeline_when_the_consumer_is_cancelled(
    tmp_workdir, monkeypatch, spawned,
):
    # A compressor that never writes: the consumer blocks inside the read.
    bindir = os.path.join(tmp_workdir, "fakebin")
    os.makedirs(bindir)
    with open(os.path.join(bindir, "pigz"), "w") as fh:
        fh.write("#!/bin/sh\nexec sleep 600\n")
    os.chmod(os.path.join(bindir, "pigz"), 0o755)
    monkeypatch.setenv("PATH", bindir + os.pathsep + os.environ["PATH"])
    h = _host(tmp_workdir)
    _populate(h.workdir)

    consumer = asyncio.create_task(_drain(h.archive_workdir(None)))
    for _ in range(6000):  # bounded poll: until the pipeline is running
        if spawned:
            break
        await asyncio.sleep(0.01)
    assert spawned
    pgid = spawned[0][1].pid

    consumer.cancel()
    with pytest.raises(asyncio.CancelledError):
        await consumer

    assert await _wait_group_gone(pgid)


async def test_start_trace_names_workdir_and_compressor(
    tmp_workdir, monkeypatch, caplog,
):
    monkeypatch.setattr(host_mod, "_CANCEL_TRACE", True)
    _without(monkeypatch, "pigz")
    h = _host(tmp_workdir)
    _populate(h.workdir)

    with caplog.at_level(logging.WARNING, logger="optio_core.cancel_trace"):
        await _drain(h.archive_workdir(["a b"]))

    start = [r.getMessage() for r in caplog.records
             if "LocalHost.archive_workdir START" in r.getMessage()]
    assert len(start) == 1
    assert f"workdir={h.workdir}" in start[0]
    assert "compressor=gzip -1" in start[0]
