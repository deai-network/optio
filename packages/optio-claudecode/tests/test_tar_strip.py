"""strip_member: streaming removal of one member from a gzip tar (no Mongo).

The deferred migration uses it to take the conversation buffer file out of
pre-fix workdir snapshot blobs. Spec:
docs/2026-10-11-conversation-buffer-encryption-design.md, section 6.
"""

from __future__ import annotations

import asyncio
import gzip
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

    got = await asyncio.wait_for(strip_member(_chunks(data), sink, name=name), 60)
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
        await asyncio.wait_for(strip_member(_chunks(data), sink, name=_NAME), 60)


# --- damaged archives -------------------------------------------------------
# Each builder damages an archive that holds the buffer file, so a lenient
# reader would find the buffer and "succeed" with a wrong copy, after which the
# migration would delete the original. Shared with test_buffer_migration.py.

def _tar_bytes(entries, *, fmt: int = tarfile.GNU_FORMAT) -> bytes:
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w", format=fmt) as tar:
        for info, data in entries:
            tar.addfile(info, io.BytesIO(data) if data is not None else None)
    return buf.getvalue()


def _damage_base() -> list:
    # Compressible content, so a flipped byte lands in Huffman-coded data.
    return [
        _file("./keep.txt", b"work line\n" * 2000),
        _file(f"./{_NAME}", _BUFFER),
        _file("./after.txt", b"later line\n" * 2000),
    ]


def intact_archive() -> bytes:
    return gzip.compress(_tar_bytes(_damage_base()), mtime=0)


def damaged_bad_crc() -> bytes:
    data = bytearray(intact_archive())
    data[-8] ^= 0xFF  # the trailer's CRC32
    return bytes(data)


def damaged_missing_trailer() -> bytes:
    return intact_archive()[:-8]


def damaged_header_checksum() -> bytes:
    raw = bytearray(_tar_bytes(_damage_base()))
    with tarfile.open(fileobj=io.BytesIO(bytes(raw))) as tar:
        third = tar.getmembers()[2].offset  # the header after the buffer's
    raw[third] ^= 0x01  # a name byte; the header checksum no longer matches
    return gzip.compress(bytes(raw), mtime=0)


def damaged_flipped_deflate() -> bytes:
    data = bytearray(intact_archive())
    data[len(data) // 2] ^= 0xFF
    return bytes(data)


def damaged_no_end_blocks() -> bytes:
    # The tar stops right after the last member's data (no end-of-archive
    # blocks), inside an intact gzip stream: tarfile alone reads the missing
    # header as the end; _StrictTarInfo refuses it.
    raw = _tar_bytes(_damage_base())
    with tarfile.open(fileobj=io.BytesIO(raw)) as tar:
        last = tar.getmembers()[-1]
    end = last.offset_data + -(-last.size // tarfile.BLOCKSIZE) * tarfile.BLOCKSIZE
    assert raw[end:].strip(b"\0") == b""  # only the end blocks are cut
    return gzip.compress(raw[:end], mtime=0)


def damaged_data_after_end() -> bytes:
    return gzip.compress(_tar_bytes(_damage_base()) + b"\x01" * 512, mtime=0)


def uncopyable_custom_type() -> bytes:
    info, data = _file("./odd", b"custom payload")
    info.type = b"Q"
    return gzip.compress(_tar_bytes([*_damage_base(), (info, data)]), mtime=0)


def uncopyable_symlink_with_data() -> bytes:
    info, _ = _link("./ln", "keep.txt")
    info.size = 10
    return gzip.compress(_tar_bytes([*_damage_base(), (info, None)]), mtime=0)


def uncopyable_huge_device_number() -> bytes:
    # GNU tar stores a device major over 7 octal digits base-256; tarfile reads
    # it, but writing it in the PAX copy raises ValueError("overflow in number
    # field"), which is not a TarError.
    info = tarfile.TarInfo("./dev")
    info.type = tarfile.CHRTYPE
    info.devmajor = 8 ** 7
    return gzip.compress(_tar_bytes([*_damage_base(), (info, None)]), mtime=0)


DAMAGED_ARCHIVES = {
    "bad-crc": damaged_bad_crc,
    "missing-trailer": damaged_missing_trailer,
    "bad-header-checksum": damaged_header_checksum,
    "flipped-deflate": damaged_flipped_deflate,
    "data-after-end": damaged_data_after_end,
    "no-end-blocks": damaged_no_end_blocks,
    "custom-type": uncopyable_custom_type,
    "symlink-with-data": uncopyable_symlink_with_data,
    "huge-device-number": uncopyable_huge_device_number,
}


async def test_intact_damage_base_strips():
    got, out = await _run(intact_archive())
    assert got == _BUFFER
    assert set(_members(out)) == {"./keep.txt", "./after.txt"}


@pytest.mark.parametrize("make", list(DAMAGED_ARCHIVES.values()), ids=list(DAMAGED_ARCHIVES))
async def test_damaged_archive_raises(make):
    with pytest.raises(ArchiveError):
        await _run(make())


async def test_multi_member_gzip():
    raw = _tar_bytes(_damage_base())
    cut = len(raw) // 3
    data = gzip.compress(raw[:cut], mtime=0) + gzip.compress(raw[cut:], mtime=0)

    got, out = await _run(data)

    assert got == _BUFFER
    members = _members(out)
    assert set(members) == {"./keep.txt", "./after.txt"}
    assert members["./after.txt"][1] == b"later line\n" * 2000


_needs_cli = pytest.mark.skipif(
    shutil.which("tar") is None or shutil.which("gzip") is None,
    reason="needs the tar and gzip CLIs",
)


def _cli_archive(root, flags: str = "") -> bytes:
    return subprocess.run(
        ["bash", "-c", f"set -o pipefail; tar {flags} -cf - . | gzip -1"],
        cwd=root, check=True, capture_output=True,
    ).stdout


@_needs_cli
async def test_cli_long_name_and_long_link(tmp_path):
    """GNU tar writes `././@LongLink` entries for a path over 100 chars and a
    long symlink target; both must come through."""
    root = tmp_path / "wd"
    deep = root / ("d" * 60) / ("e" * 60) / ("f" * 60) / ("g" * 60)
    deep.mkdir(parents=True)
    (deep / "file.txt").write_bytes(b"deep\n")
    long_target = "t" * 150 + "/target.txt"
    os.symlink(long_target, root / "ln")
    (root / _NAME).write_bytes(_BUFFER)
    long_path = "./" + str((deep / "file.txt").relative_to(root))
    assert len(long_path) > 250
    data = _cli_archive(root)

    got, out = await _run(data)

    assert got == _BUFFER
    members = _members(out)
    assert members[long_path][1] == b"deep\n"
    assert members["./ln"][0].linkname == long_target
    dest = tmp_path / "out"
    dest.mkdir()
    subprocess.run(["tar", "xzf", "-", "-C", str(dest)], input=out, check=True)
    assert (dest / long_path).read_bytes() == b"deep\n"
    assert os.readlink(dest / "ln") == long_target
    assert not (dest / _NAME).exists()


@_needs_cli
async def test_cli_sparse_member_raises(tmp_path):
    root = tmp_path / "wd"
    root.mkdir()
    with open(root / "sparse.bin", "wb") as f:
        f.write(b"x")
        f.truncate(8 * 1024 * 1024)
    (root / _NAME).write_bytes(_BUFFER)
    data = _cli_archive(root, "--sparse")
    if not any(m.issparse() for m, _ in _members(data).values()):
        pytest.skip("this tar/filesystem wrote no sparse member")

    with pytest.raises(ArchiveError):
        await _run(data)
