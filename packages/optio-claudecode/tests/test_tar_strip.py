"""strip_member: streaming removal of one member from a gzip tar (no Mongo).

The deferred migration uses it to take the conversation buffer file out of
pre-fix workdir snapshot blobs. Spec:
docs/2026-10-11-conversation-buffer-encryption-design.md, section 6.
"""

from __future__ import annotations

import asyncio
import io
import os
import shutil
import subprocess
import tarfile

import pytest

from optio_claudecode.tar_strip import ArchiveError, strip_member


_NAME = ".optio-conversation-buffer.json"
_BUFFER = b'[[1,{"type":"user","message":"hello"}]]'


async def _chunks(data: bytes, size: int = 64 * 1024):
    for i in range(0, len(data), size):
        yield data[i:i + size]


async def _run(data: bytes, *, name: str = _NAME) -> tuple[bytes | None, bytes]:
    out = bytearray()

    async def sink(b: bytes) -> None:
        out.extend(b)

    got = await asyncio.wait_for(strip_member(_chunks(data), sink, name=name), 30)
    return got, bytes(out)


def _file(name: str, data: bytes, *, mode: int = 0o644, mtime: int = 1_600_000_000):
    info = tarfile.TarInfo(name)
    info.size = len(data)
    info.mode = mode
    info.mtime = mtime
    return info, data


def _dir(name: str, *, mode: int = 0o755, mtime: int = 1_600_000_100):
    info = tarfile.TarInfo(name)
    info.type = tarfile.DIRTYPE
    info.mode = mode
    info.mtime = mtime
    return info, None


def _link(name: str, target: str, *, kind: bytes = tarfile.SYMTYPE):
    info = tarfile.TarInfo(name)
    info.type = kind
    info.linkname = target
    info.mode = 0o777
    info.mtime = 1_600_000_200
    return info, None


def _archive(entries, *, fmt: int = tarfile.PAX_FORMAT) -> bytes:
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz", format=fmt, compresslevel=1) as tar:
        for info, data in entries:
            tar.addfile(info, io.BytesIO(data) if data is not None else None)
    return buf.getvalue()


def _members(data: bytes) -> dict[str, tuple[tarfile.TarInfo, bytes | None]]:
    out = {}
    with tarfile.open(fileobj=io.BytesIO(data), mode="r:gz") as tar:
        for m in tar.getmembers():
            out[m.name] = (m, tar.extractfile(m).read() if m.isreg() else None)
    return out


@pytest.mark.parametrize("prefix", ["./", ""])
async def test_strips_dot_slash_and_plain_names(prefix):
    entries = [
        _file(f"{prefix}keep.txt", b"work\n"),
        _file(f"{prefix}{_NAME}", _BUFFER),
        _file(f"{prefix}after.txt", b"later\n"),
    ]
    if prefix:
        entries.insert(0, _dir("./"))
    # GNU format for the `./` names, as `tar cf - .` writes them; PAX (the
    # Python fallback's tarfile default) for the plain names.
    data = _archive(entries, fmt=tarfile.GNU_FORMAT if prefix else tarfile.PAX_FORMAT)

    got, out = await _run(data)

    assert got == _BUFFER
    members = _members(out)
    assert f"{prefix}{_NAME}" not in members
    assert _NAME not in members and f"./{_NAME}" not in members
    assert members[f"{prefix}keep.txt"][1] == b"work\n"
    assert members[f"{prefix}after.txt"][1] == b"later\n"


async def test_preserves_other_members_and_metadata():
    big = os.urandom(3 * 1024 * 1024)
    entries = [
        _dir("d", mode=0o750, mtime=1_500_000_000),
        _file("d/secret.txt", b"mode 0640\n", mode=0o640, mtime=1_234_567_890),
        _file(_NAME, _BUFFER),
        _link("d/sym", "secret.txt"),
        _link("hard", "d/secret.txt", kind=tarfile.LNKTYPE),
        _file("big.bin", big, mode=0o600, mtime=1_700_000_000),
    ]
    for info, _ in entries:
        info.uid, info.gid, info.uname, info.gname = 1234, 5678, "agent", "agents"
    data = _archive(entries)

    got, out = await _run(data)

    assert got == _BUFFER
    before = _members(data)
    after = _members(out)
    assert set(after) == set(before) - {_NAME}
    assert list(after) == [n for n in before if n != _NAME]  # order kept
    for name, (m, content) in after.items():
        orig, orig_content = before[name]
        assert m.type == orig.type, name
        assert m.mode == orig.mode, name
        assert m.mtime == orig.mtime, name
        assert m.linkname == orig.linkname, name
        assert (m.uid, m.gid, m.uname, m.gname) == (1234, 5678, "agent", "agents"), name
        assert content == orig_content, name
    assert after["d/secret.txt"][0].mode == 0o640
    assert after["d/secret.txt"][0].mtime == 1_234_567_890
    assert after["d/sym"][0].issym() and after["d/sym"][0].linkname == "secret.txt"
    assert after["hard"][0].islnk() and after["hard"][0].linkname == "d/secret.txt"
    assert after["big.bin"][1] == big


async def test_absent_returns_none():
    # Only a top-level member is the buffer file (spec section 6); one deeper
    # down is ordinary workdir content and passes through.
    entries = [
        _file("keep.txt", b"work\n"),
        _dir("sub"),
        _file(f"sub/{_NAME}", b"not the buffer"),
    ]
    data = _archive(entries)

    got, out = await _run(data)

    assert got is None
    after = _members(out)
    assert set(after) == {"keep.txt", "sub", f"sub/{_NAME}"}
    assert after[f"sub/{_NAME}"][1] == b"not the buffer"


@pytest.mark.skipif(
    shutil.which("tar") is None or shutil.which("gzip") is None,
    reason="needs the tar and gzip CLIs",
)
async def test_cli_tar_archive(tmp_path):
    """The capture's own format: `tar cf - . | gzip -1` (GNU tar, `./` names)."""
    root = tmp_path / "wd"
    (root / "sub").mkdir(parents=True)
    (root / "keep.txt").write_bytes(b"work\n")
    (root / "sub" / "x.py").write_bytes(b"print(1)\n")
    (root / _NAME).write_bytes(_BUFFER)
    os.symlink("keep.txt", root / "ln")
    data = subprocess.run(
        ["bash", "-c", "set -o pipefail; tar cf - . | gzip -1"],
        cwd=root, check=True, capture_output=True,
    ).stdout

    got, out = await _run(data)

    assert got == _BUFFER
    before = _members(data)
    after = _members(out)
    assert f"./{_NAME}" in before
    assert set(after) == set(before) - {f"./{_NAME}"}
    assert after["./keep.txt"][1] == b"work\n"
    assert after["./sub/x.py"][1] == b"print(1)\n"
    assert after["./ln"][0].linkname == "keep.txt"
    for name, (m, _) in after.items():
        assert (m.mode, m.mtime, m.uid, m.gid) == (
            before[name][0].mode, before[name][0].mtime,
            before[name][0].uid, before[name][0].gid,
        ), name


async def test_unreadable_input_raises_archive_error():
    with pytest.raises(ArchiveError):
        await _run(b"this is not a gzip stream at all" * 100)
    data = _archive([_file("big.bin", os.urandom(512 * 1024)), _file(_NAME, _BUFFER)])
    with pytest.raises(ArchiveError):
        await _run(data[: len(data) // 2])


async def test_sink_error_propagates():
    # Large enough that the worker is blocked on the full output queue when
    # the sink fails, so this exercises the abort path, not a clean finish.
    data = _archive([_file("big.bin", os.urandom(12 * 1024 * 1024))])

    async def sink(b: bytes) -> None:
        raise RuntimeError("sink broke")

    with pytest.raises(RuntimeError, match="sink broke"):
        await asyncio.wait_for(strip_member(_chunks(data), sink, name=_NAME), 30)
