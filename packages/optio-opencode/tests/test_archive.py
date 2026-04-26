"""Tests for tar.gz workdir archive helpers."""

import os

import pytest

from optio_opencode.archive import (
    DEFAULT_WORKDIR_EXCLUDES,
    consume_workdir_archive,
    yield_workdir_archive,
)


def _populate(root: str) -> None:
    os.makedirs(root, exist_ok=True)
    with open(os.path.join(root, "hello.txt"), "w") as fh:
        fh.write("hi")
    os.makedirs(os.path.join(root, "sub"), exist_ok=True)
    with open(os.path.join(root, "sub", "keep.py"), "w") as fh:
        fh.write("x = 1\n")
    os.makedirs(os.path.join(root, ".git"), exist_ok=True)
    with open(os.path.join(root, ".git", "HEAD"), "w") as fh:
        fh.write("ref: refs/heads/main\n")
    os.makedirs(os.path.join(root, "__pycache__"), exist_ok=True)
    with open(os.path.join(root, "__pycache__", "mod.cpython-311.pyc"), "wb") as fh:
        fh.write(b"\xff\xff")
    with open(os.path.join(root, "mod.pyc"), "wb") as fh:
        fh.write(b"\xff\xff")


async def _gather(stream) -> bytes:
    chunks = []
    async for c in stream:
        chunks.append(c)
    return b"".join(chunks)


async def _replay(buf: bytes):
    """Adapt a finished bytes buffer into an AsyncIterator[bytes] for consume_workdir_archive."""
    async def gen():
        view = memoryview(buf)
        step = 64 * 1024
        for i in range(0, len(view), step):
            yield bytes(view[i : i + step])
    return gen()


async def test_yield_and_consume_roundtrip(tmp_path):
    src = tmp_path / "src"
    dst = tmp_path / "dst"
    _populate(str(src))

    blob = await _gather(yield_workdir_archive(str(src), exclude=None))
    await consume_workdir_archive(await _replay(blob), str(dst))

    assert (dst / "hello.txt").read_text() == "hi"
    assert (dst / "sub" / "keep.py").read_text() == "x = 1\n"
    assert not (dst / ".git").exists()
    assert not (dst / "__pycache__").exists()
    assert not (dst / "mod.pyc").exists()


async def test_empty_exclude_list_captures_everything(tmp_path):
    src = tmp_path / "src"
    dst = tmp_path / "dst"
    _populate(str(src))

    blob = await _gather(yield_workdir_archive(str(src), exclude=[]))
    await consume_workdir_archive(await _replay(blob), str(dst))

    assert (dst / ".git" / "HEAD").read_text() == "ref: refs/heads/main\n"
    assert (dst / "__pycache__").exists()


async def test_custom_exclude_no_merge_with_defaults(tmp_path):
    """Non-empty list is verbatim; .git should NOT be excluded unless listed."""
    src = tmp_path / "src"
    dst = tmp_path / "dst"
    _populate(str(src))

    blob = await _gather(yield_workdir_archive(str(src), exclude=["*.log"]))
    await consume_workdir_archive(await _replay(blob), str(dst))

    assert (dst / ".git").exists()
    assert (dst / "__pycache__").exists()


async def test_consume_empties_destination_first(tmp_path):
    """consume_workdir_archive must wipe pre-existing dest contents before extracting."""
    src = tmp_path / "src"
    dst = tmp_path / "dst"
    _populate(str(src))
    os.makedirs(dst, exist_ok=True)
    (dst / "stale.txt").write_text("should be gone")

    blob = await _gather(yield_workdir_archive(str(src), exclude=None))
    await consume_workdir_archive(await _replay(blob), str(dst))

    assert not (dst / "stale.txt").exists()
    assert (dst / "hello.txt").read_text() == "hi"


def test_default_workdir_excludes_has_expected_entries():
    assert ".git" in DEFAULT_WORKDIR_EXCLUDES
    assert "node_modules" in DEFAULT_WORKDIR_EXCLUDES
    assert "__pycache__" in DEFAULT_WORKDIR_EXCLUDES
    assert ".venv" in DEFAULT_WORKDIR_EXCLUDES
    assert "*.pyc" in DEFAULT_WORKDIR_EXCLUDES
    assert ".DS_Store" in DEFAULT_WORKDIR_EXCLUDES
