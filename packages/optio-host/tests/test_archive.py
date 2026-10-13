"""Tests for tar.gz workdir archive helpers."""

import concurrent.futures
import io
import os
import tarfile

import pytest

from optio_host import archive as archive_mod
from optio_host.archive import (
    DEFAULT_WORKDIR_EXCLUDES,
    consume_workdir_archive,
    fallback_regenerable_excludes,
    measure_workdir,
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


def _populate_anchored(root: str) -> None:
    def write(relpath: str, content: str) -> None:
        path = os.path.join(root, relpath)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w") as fh:
            fh.write(content)

    os.makedirs(root, exist_ok=True)
    write("node_modules/a.js", "a")
    write("pkg/node_modules/b.js", "b")
    write("build[1]/x", "x")
    write("buildx/y", "y")
    write("keep.txt", "k")
    write(".git/HEAD", "ref: refs/heads/main\n")


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


def _tar_member_names(blob: bytes) -> set[str]:
    with tarfile.open(fileobj=io.BytesIO(blob), mode="r:gz") as tf:
        return {m.name.removeprefix("./") for m in tf.getmembers() if m.isfile()}


async def test_anchored_entry_excludes_root_but_keeps_nested_match(tmp_path):
    """node_modules listed anchored must drop ./node_modules but keep ./pkg/node_modules.

    `exclude` here is `[".git"]`, not the module defaults: `DEFAULT_WORKDIR_EXCLUDES`
    already contains "node_modules" as a match-anywhere glob, which would strip
    `pkg/node_modules` on its own and mask what this test is checking (the new
    anchored, root-only semantics), so it is kept out of this fixture's excludes.
    """
    src = tmp_path / "src"
    dst = tmp_path / "dst"
    _populate_anchored(str(src))

    blob = await _gather(
        yield_workdir_archive(str(src), exclude=[".git"], anchored=["node_modules", "build[1]"])
    )
    await consume_workdir_archive(await _replay(blob), str(dst))

    assert not (dst / "node_modules").exists()
    assert (dst / "pkg" / "node_modules" / "b.js").read_text() == "b"
    assert (dst / "keep.txt").read_text() == "k"
    assert not (dst / ".git").exists()


async def test_anchored_entry_with_glob_characters_is_taken_literally(tmp_path):
    """`build[1]` must be excluded (and everything under it) as a literal name,
    never expanded as a glob; `buildx` (which `build[1]` as a glob would not
    match either way) stays as a sanity check that only the literal dir went."""
    src = tmp_path / "src"
    dst = tmp_path / "dst"
    _populate_anchored(str(src))

    blob = await _gather(
        yield_workdir_archive(str(src), exclude=[".git"], anchored=["node_modules", "build[1]"])
    )
    await consume_workdir_archive(await _replay(blob), str(dst))

    assert not (dst / "build[1]").exists()
    assert (dst / "buildx" / "y").read_text() == "y"


async def test_anchored_none_or_empty_matches_todays_members(tmp_path):
    """With `anchored` empty/omitted, the fallback produces exactly today's members."""
    src = tmp_path / "src"
    _populate(str(src))

    blob_omitted = await _gather(yield_workdir_archive(str(src), exclude=None))
    blob_empty = await _gather(yield_workdir_archive(str(src), exclude=None, anchored=[]))
    blob_none = await _gather(yield_workdir_archive(str(src), exclude=None, anchored=None))

    names = _tar_member_names(blob_omitted)
    assert names == _tar_member_names(blob_empty)
    assert names == _tar_member_names(blob_none)


def test_default_workdir_excludes_has_expected_entries():
    assert ".git" in DEFAULT_WORKDIR_EXCLUDES
    assert "node_modules" in DEFAULT_WORKDIR_EXCLUDES
    assert "__pycache__" in DEFAULT_WORKDIR_EXCLUDES
    assert ".venv" in DEFAULT_WORKDIR_EXCLUDES
    assert "*.pyc" in DEFAULT_WORKDIR_EXCLUDES
    assert ".DS_Store" in DEFAULT_WORKDIR_EXCLUDES


# --- the fallback's capture decision -------------------------------------------


@pytest.fixture
def generous_walk_budget(monkeypatch):
    """The walk's 1 s budget, raised so a test not about it never races it."""
    monkeypatch.setattr(archive_mod, "WALK_BUDGET_S", 60)


def test_measure_workdir_honours_the_patterns(tmp_path, generous_walk_budget):
    (tmp_path / "keep.bin").write_bytes(bytes(1000))
    (tmp_path / ".git").mkdir()
    (tmp_path / ".git" / "pack").write_bytes(bytes(100_000))
    (tmp_path / "sub").mkdir()
    (tmp_path / "sub" / "mod.pyc").write_bytes(bytes(50_000))

    assert 1000 <= measure_workdir(str(tmp_path), [".git", "*.pyc"]) < 50_000
    assert measure_workdir(str(tmp_path), []) >= 151_000


def test_measure_workdir_gives_up_once_the_budget_is_spent(tmp_path):
    """A zero budget is spent before the first entry, whatever the clock reads."""
    (tmp_path / "keep.bin").write_bytes(bytes(1000))

    assert measure_workdir(str(tmp_path), [], budget_s=0) is None


def test_fallback_decision_reads_an_unreadable_list_as_empty(tmp_path, generous_walk_budget):
    (tmp_path / "keep.bin").write_bytes(bytes(1000))
    (tmp_path / "regenerable.txt").mkdir()  # open() fails on it

    entries, note = fallback_regenerable_excludes(str(tmp_path), [], 1)

    assert entries == []
    assert "missing or empty" in note


def test_fallback_decision_decodes_a_binary_list_with_replacement(
    tmp_path, generous_walk_budget,
):
    (tmp_path / "regenerable.txt").write_bytes(b"\xff\xfe\nkeep.bin\n")

    entries, note = fallback_regenerable_excludes(str(tmp_path), [], 1)

    assert entries == ["\ufffd\ufffd", "keep.bin"]
    assert "left out 2 paths" in note


def test_fallback_decision_counts_a_spent_walk_budget_as_over_the_cap(tmp_path, monkeypatch):
    """A zero budget is spent before the first entry, whatever the clock reads."""
    monkeypatch.setattr(archive_mod, "WALK_BUDGET_S", 0)
    (tmp_path / "regenerable.txt").write_text("keep.bin\n")

    entries, note = fallback_regenerable_excludes(str(tmp_path), [], 100 * 1024 * 1024)

    assert entries == ["keep.bin"]
    assert note == (
        "workdir size unknown (walk timed out after 0 s): "
        "left out 1 path listed in regenerable.txt"
    )


def _unblock_fifo(path) -> None:
    """Give a reader stuck opening the FIFO at `path` its EOF; a no-op when
    none is waiting (opening the write end then fails with ENXIO)."""
    try:
        os.close(os.open(path, os.O_WRONLY | os.O_NONBLOCK))
    except OSError:
        pass


@pytest.mark.parametrize("kind", ["fifo", "dev-zero symlink"])
def test_fallback_decision_reads_only_a_regular_list(tmp_path, generous_walk_budget, kind):
    # Opening a FIFO for reading blocks until a writer comes; /dev/zero never ends.
    (tmp_path / "keep.bin").write_bytes(bytes(1000))  # over the 1-byte cap
    path = tmp_path / "regenerable.txt"
    if kind == "fifo":
        os.mkfifo(path)
    else:
        os.symlink("/dev/zero", path)

    with concurrent.futures.ThreadPoolExecutor(1) as pool:
        decision = pool.submit(fallback_regenerable_excludes, str(tmp_path), [], 1)
        try:
            entries, note = decision.result(timeout=30)  # bounds a true hang only
        finally:
            if kind == "fifo":
                _unblock_fifo(path)

    assert entries == []
    assert "missing or empty" in note


def test_fallback_decision_reads_the_first_64_kib_of_the_list(tmp_path, generous_walk_budget):
    # "keep.txt" straddles the 64 KiB limit: cut to "keep", it must not apply.
    text = "big\n" + "#" * (65536 - 4 - 1 - 4) + "\n" + "keep.txt\nlater\n"
    (tmp_path / "regenerable.txt").write_text(text)

    entries, _ = fallback_regenerable_excludes(str(tmp_path), [], 1)

    assert entries == ["big"]
