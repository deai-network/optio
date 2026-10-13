"""LocalHost.archive_workdir: streamed tar | pigz -1 / gzip -1 pipeline.

LocalHost used to archive with Python ``tarfile`` at zlib level 9, single
threaded and fully in memory: 62 s for a 743 MiB workdir against a 30 s cancel
grace, so the snapshot never landed (see
docs/2026-10-03-local-archive-throughput-design.md). These tests pin the
replacement against real local processes; only ``shutil.which`` is faked, to
choose between the pipeline's compressors and the in-process fallback.

The capture cap (``OPTIO_WORKDIR_CAPTURE_MAX_MB``) adds a ``du`` subprocess
before the pipeline. It is off for every test here unless the test turns it
on, so the pipeline tests keep pinning a single subprocess and the host's
environment never leaks in; the capture-cap section turns it on. Its 1 s
``du``/list-read timeout and walk budget are raised to 60 s for every test,
none of which is about them, so no test races a real timer.
"""

from __future__ import annotations

import asyncio
import io
import logging
import os
import shlex
import shutil
import subprocess
import tarfile

import pytest

from optio_host import archive as archive_mod
from optio_host import capture_cap
from optio_host import host as host_mod
from optio_host.capture_cap import ENV_VAR
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


def _populate_anchored(root: str) -> None:
    _write(root, "node_modules/a.js", "a")
    _write(root, "pkg/node_modules/b.js", "b")
    _write(root, "build[1]/x", "x")
    _write(root, "build1/z", "z")  # what `build[1]` would match as a glob
    _write(root, "buildx/y", "y")
    _write(root, "star/*", "*")
    _write(root, "star/keep", "s")  # what `star/*` would match as a glob
    _write(root, "back\\slash", "\\")
    _write(root, "keep.txt", "k")
    _write(root, ".git/HEAD", "ref: refs/heads/main\n")


# The entries of `_populate_anchored` that a regenerable list names, and what
# a capture leaving them out (and `.git`) must keep.
_ANCHORED = ["node_modules", "build[1]", "star/*", "back\\slash"]
_ANCHORED_KEPT = {"pkg/node_modules/b.js", "build1/z", "buildx/y", "star/keep", "keep.txt"}


def _tar_member_names(blob: bytes) -> set[str]:
    with tarfile.open(fileobj=io.BytesIO(blob), mode="r:gz") as tf:
        return {m.name.removeprefix("./") for m in tf.getmembers() if m.isfile()}


def _host(tmp_workdir: str, name: str = "src") -> LocalHost:
    h = LocalHost(taskdir=os.path.join(tmp_workdir, name))
    os.makedirs(h.workdir, exist_ok=True)
    return h


async def _drain(it) -> bytes:
    out = b""
    async for chunk in it:
        out += chunk
    return out


class _Notes(list):
    """An ``on_note`` that collects each ``(note, level)`` it is handed."""

    def __call__(self, note: str, level: str) -> None:
        self.append((note, level))


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


@pytest.fixture(autouse=True)
def _capture_cap_off(monkeypatch):
    monkeypatch.setenv(ENV_VAR, "0")


@pytest.fixture(autouse=True)
def _generous_capture_timeouts(monkeypatch):
    monkeypatch.setattr(capture_cap, "DU_TIMEOUT_S", 60)
    monkeypatch.setattr(archive_mod, "WALK_BUDGET_S", 60)


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


# --- anchored excludes -------------------------------------------------------


def test_archive_command_anchored_excludes_are_literal_and_root_only(tmp_workdir):
    # Run for real on this host's tar (GNU tar on excavator). The docker-gated
    # round trip in optio-opencode's test_host_remote_resume.py runs the same
    # kind of excludes on BusyBox tar.
    workdir = os.path.join(tmp_workdir, "src")
    _populate_anchored(workdir)

    cmd = host_mod._archive_command(workdir, [".git"], "gzip -1", _ANCHORED)
    proc = subprocess.run(cmd, shell=True, capture_output=True)

    assert proc.returncode == 0, proc.stderr
    assert _tar_member_names(proc.stdout) == _ANCHORED_KEPT


def test_archive_command_anchored_excludes_are_escaped_literals():
    """Pin: each entry is one `--exclude=./<entry>` with `\\`, `*`, `?` and
    `[` backslash-escaped; no `--anchored` or `--no-wildcards`, which BusyBox
    tar rejects."""
    script = (
        "set -o pipefail; cd /w && tar cf - --exclude=.git "
        "--exclude=./node_modules --exclude='./build\\[1]' --exclude='./star/\\*' "
        "--exclude='./back\\\\slash' --exclude='./a\\?b' . | gzip -1"
    )

    assert host_mod._archive_command(
        "/w", [".git"], "gzip -1", _ANCHORED + ["a?b"],
    ) == f"bash -c {shlex.quote(script)}"


def test_archive_command_without_anchored_matches_todays_string(tmp_workdir):
    """Pin: no `anchored` arg, or an empty one, reproduces today's exact command."""
    workdir = os.path.join(tmp_workdir, "src")
    patterns = [".git", "node_modules"]
    compressor = "gzip -1"

    excludes = " ".join(f"--exclude={shlex.quote(p)}" for p in patterns)
    script = (
        f"set -o pipefail; cd {shlex.quote(workdir)} && "
        f"tar cf - {excludes} . | {compressor}"
    )
    expected = f"bash -c {shlex.quote(script)}"

    assert host_mod._archive_command(workdir, patterns, compressor) == expected
    assert (
        host_mod._archive_command(workdir, patterns, compressor)
        == host_mod._archive_command(workdir, patterns, compressor, [])
    )


# --- capture cap -------------------------------------------------------------

_MIB = 1024 * 1024

# Explicit, without node_modules: DEFAULT_WORKDIR_EXCLUDES' match-anywhere
# node_modules would hide whether the listed a/node_modules stays anchored.
_CAP_EXCLUDE = [".git"]

_ALL_FILES = {
    "big/blob.bin", "keep.txt", "regenerable.txt",
    "a/node_modules/x.js", "b/a/node_modules/y.js",
}


def _populate_over_cap(root: str, regenerable: str | None = "big\na/node_modules\n") -> None:
    _write(root, "big/blob.bin", bytes(2 * _MIB))
    _write(root, "keep.txt", "k")
    _write(root, "a/node_modules/x.js", "x")
    _write(root, "b/a/node_modules/y.js", "y")
    if regenerable is not None:
        _write(root, "regenerable.txt", regenerable)


_LEFT_OUT_BIG = (
    "workdir 2 MB (cap 1 MB): left out 1 path listed in regenerable.txt", "info",
)
_MISSING_LIST = (
    "workdir 2 MB is over the 1 MB capture cap, but regenerable.txt "
    "is missing or empty; capturing everything",
    "warning",
)


def _archive_notes(caplog) -> list[tuple[int, str]]:
    return [(r.levelno, r.getMessage()) for r in caplog.records
            if r.name == "optio_host.archive"]


async def test_over_the_cap_leaves_out_the_listed_paths(tmp_workdir, monkeypatch, caplog):
    monkeypatch.setenv(ENV_VAR, "1")
    h = _host(tmp_workdir)
    _populate_over_cap(h.workdir)
    notes = _Notes()

    with caplog.at_level(logging.INFO, logger="optio_host.archive"):
        blob = await _drain(h.archive_workdir(_CAP_EXCLUDE, on_note=notes))

    assert not [n for n in _members(blob) if n == "big" or n.startswith("big/")]
    assert _tar_member_names(blob) == {"keep.txt", "regenerable.txt", "b/a/node_modules/y.js"}
    assert notes == [
        ("workdir 2 MB (cap 1 MB): left out 2 paths listed in regenerable.txt", "info"),
    ]
    assert _archive_notes(caplog) == [(logging.INFO, notes[0][0])]


async def test_under_the_cap_captures_everything(tmp_workdir, monkeypatch, caplog):
    monkeypatch.setenv(ENV_VAR, "100")
    h = _host(tmp_workdir)
    _populate_over_cap(h.workdir)
    notes = _Notes()

    with caplog.at_level(logging.INFO, logger="optio_host.archive"):
        blob = await _drain(h.archive_workdir(_CAP_EXCLUDE, on_note=notes))

    assert _tar_member_names(blob) == _ALL_FILES
    assert notes == []
    assert _archive_notes(caplog) == []


async def test_cap_zero_runs_no_du_and_todays_command(tmp_workdir, monkeypatch, spawned):
    monkeypatch.setenv(ENV_VAR, "0")
    _without(monkeypatch, "pigz")
    real_regenerable_excludes = host_mod.regenerable_excludes

    async def no_run(cmd: str):
        raise AssertionError(f"the capture cap ran {cmd!r} with the cap off")

    async def guarded(run, workdir, patterns, cap_bytes):
        return await real_regenerable_excludes(no_run, workdir, patterns, cap_bytes)

    monkeypatch.setattr(host_mod, "regenerable_excludes", guarded)
    h = _host(tmp_workdir)
    _populate_over_cap(h.workdir)
    notes = _Notes()

    blob = await _drain(h.archive_workdir(_CAP_EXCLUDE, on_note=notes))

    assert _tar_member_names(blob) == _ALL_FILES
    assert notes == []
    assert [args for args, _ in spawned] == [
        ("/bin/sh", "-c", host_mod._archive_command(h.workdir, _CAP_EXCLUDE, "gzip -1")),
    ]


async def test_over_the_cap_without_a_list_captures_everything_and_warns(
    tmp_workdir, monkeypatch, caplog,
):
    monkeypatch.setenv(ENV_VAR, "1")
    h = _host(tmp_workdir)
    _populate_over_cap(h.workdir, regenerable=None)
    notes = _Notes()

    with caplog.at_level(logging.INFO, logger="optio_host.archive"):
        blob = await _drain(h.archive_workdir(_CAP_EXCLUDE, on_note=notes))

    assert _tar_member_names(blob) == _ALL_FILES - {"regenerable.txt"}
    assert notes == [_MISSING_LIST]
    assert _archive_notes(caplog) == [(logging.WARNING, notes[0][0])]


async def test_fallback_over_the_cap_leaves_out_the_listed_paths(
    tmp_workdir, monkeypatch, spawned,
):
    monkeypatch.setenv(ENV_VAR, "1")
    _without(monkeypatch, "tar")
    h = _host(tmp_workdir)
    _populate_over_cap(h.workdir)
    notes = _Notes()

    blob = await _drain(h.archive_workdir(_CAP_EXCLUDE, on_note=notes))

    assert spawned == []
    assert _tar_member_names(blob) == {"keep.txt", "regenerable.txt", "b/a/node_modules/y.js"}
    assert notes == [
        ("workdir 2 MB (cap 1 MB): left out 2 paths listed in regenerable.txt", "info"),
    ]


async def test_fallback_under_the_cap_captures_everything(tmp_workdir, monkeypatch):
    monkeypatch.setenv(ENV_VAR, "100")
    _without(monkeypatch, "tar")
    h = _host(tmp_workdir)
    _populate_over_cap(h.workdir)
    notes = _Notes()

    blob = await _drain(h.archive_workdir(_CAP_EXCLUDE, on_note=notes))

    assert _tar_member_names(blob) == _ALL_FILES
    assert notes == []


def _boom(note: str, level: str) -> None:
    raise RuntimeError("on_note exploded")


@pytest.mark.parametrize("missing", [None, "tar"], ids=["tar-path", "fallback"])
async def test_list_entry_with_a_nul_byte_is_dropped(tmp_workdir, monkeypatch, missing):
    monkeypatch.setenv(ENV_VAR, "1")
    if missing:
        _without(monkeypatch, missing)
    h = _host(tmp_workdir)
    _populate_over_cap(h.workdir, regenerable=None)
    _write(h.workdir, "regenerable.txt", b"big\n\x89PNG\x00\x01\xff\n")
    notes = _Notes()

    blob = await _drain(h.archive_workdir(_CAP_EXCLUDE, on_note=notes))

    assert _tar_member_names(blob) == _ALL_FILES - {"big/blob.bin"}
    assert notes == [_LEFT_OUT_BIG]


@pytest.mark.parametrize("missing", [None, "tar"], ids=["tar-path", "fallback"])
async def test_fifo_list_is_an_empty_list_and_does_not_hang(tmp_workdir, monkeypatch, missing):
    monkeypatch.setenv(ENV_VAR, "1")
    if missing:
        _without(monkeypatch, missing)
    h = _host(tmp_workdir)
    _populate_over_cap(h.workdir, regenerable=None)
    fifo = os.path.join(h.workdir, "regenerable.txt")
    os.mkfifo(fifo)
    notes = _Notes()

    # 30 s bounds a true hang only: reading the FIFO would block for good.
    # Opening its write end then gives a stuck reader EOF, so nothing is
    # left blocked behind the test.
    try:
        blob = await asyncio.wait_for(
            _drain(h.archive_workdir(_CAP_EXCLUDE, on_note=notes)), 30,
        )
    finally:
        try:
            os.close(os.open(fifo, os.O_WRONLY | os.O_NONBLOCK))
        except OSError:
            pass  # ENXIO: no reader waiting

    assert _tar_member_names(blob) == _ALL_FILES - {"regenerable.txt"}
    assert notes == [_MISSING_LIST]


@pytest.mark.parametrize("missing", [None, "tar"], ids=["tar-path", "fallback"])
async def test_oversized_list_applies_the_entries_in_its_first_64_kib(
    tmp_workdir, monkeypatch, missing,
):
    monkeypatch.setenv(ENV_VAR, "1")
    if missing:
        _without(monkeypatch, missing)
    h = _host(tmp_workdir)
    _populate_over_cap(h.workdir, regenerable="big\n# " + "#" * 70_000 + "\nkeep.txt\n")
    notes = _Notes()

    blob = await _drain(h.archive_workdir(_CAP_EXCLUDE, on_note=notes))

    assert _tar_member_names(blob) == _ALL_FILES - {"big/blob.bin"}
    assert notes == [_LEFT_OUT_BIG]


async def test_list_too_long_for_one_command_line_still_captures(tmp_workdir, monkeypatch):
    # 13001 entries as --exclude=./<entry> would be ~221 KiB in one exec
    # argument, past Linux's 128 KiB: E2BIG, and the snapshot lost.
    monkeypatch.setenv(ENV_VAR, "1")
    h = _host(tmp_workdir)
    names = [f"{i:04x}" for i in range(13000)]
    _populate_over_cap(h.workdir, regenerable="big\n" + "\n".join(names) + "\n")
    notes = _Notes()

    blob = await _drain(h.archive_workdir(_CAP_EXCLUDE, on_note=notes))

    assert _tar_member_names(blob) == _ALL_FILES - {"big/blob.bin"}
    assert notes == [(
        "workdir 2 MB (cap 1 MB): left out 3855 paths listed in regenerable.txt "
        "(9146 more ignored: list too long)",
        "info",
    )]


async def test_decision_that_raises_captures_everything(tmp_workdir, monkeypatch, caplog):
    monkeypatch.setenv(ENV_VAR, "1")

    async def run_fails(cmd: str):
        raise OSError("cannot spawn bash")

    monkeypatch.setattr(host_mod, "_run_local_bash", run_fails)
    h = _host(tmp_workdir)
    _populate_over_cap(h.workdir)
    notes = _Notes()

    with caplog.at_level(logging.INFO, logger="optio_host.archive"):
        blob = await _drain(h.archive_workdir(_CAP_EXCLUDE, on_note=notes))

    assert _tar_member_names(blob) == _ALL_FILES
    assert notes == [(
        "capture decision failed (OSError('cannot spawn bash')); capturing everything",
        "warning",
    )]
    assert _archive_notes(caplog) == [(logging.WARNING, notes[0][0])]


async def test_on_note_that_raises_does_not_stop_the_capture(tmp_workdir, monkeypatch, caplog):
    monkeypatch.setenv(ENV_VAR, "1")
    h = _host(tmp_workdir)
    _populate_over_cap(h.workdir)

    with caplog.at_level(logging.INFO, logger="optio_host.archive"):
        blob = await _drain(h.archive_workdir(_CAP_EXCLUDE, on_note=_boom))

    assert _tar_member_names(blob) == {"keep.txt", "regenerable.txt", "b/a/node_modules/y.js"}
    records = _archive_notes(caplog)
    assert records[0][0] == logging.INFO
    assert records[1][0] == logging.WARNING and "on_note exploded" in records[1][1]


async def test_fallback_measurement_that_raises_captures_everything(
    tmp_workdir, monkeypatch, caplog,
):
    monkeypatch.setenv(ENV_VAR, "1")
    _without(monkeypatch, "tar")

    def measure_fails(root, patterns, budget_s=None):
        raise OSError("walk exploded")

    monkeypatch.setattr(archive_mod, "measure_workdir", measure_fails)
    h = _host(tmp_workdir)
    _populate_over_cap(h.workdir)
    notes = _Notes()

    with caplog.at_level(logging.INFO, logger="optio_host.archive"):
        blob = await _drain(h.archive_workdir(_CAP_EXCLUDE, on_note=notes))

    assert _tar_member_names(blob) == _ALL_FILES
    assert notes == [(
        "capture decision failed (OSError('walk exploded')); capturing everything",
        "warning",
    )]
    assert _archive_notes(caplog) == [(logging.WARNING, notes[0][0])]


async def test_fallback_on_note_that_raises_does_not_stop_the_capture(tmp_workdir, monkeypatch):
    monkeypatch.setenv(ENV_VAR, "1")
    _without(monkeypatch, "tar")
    h = _host(tmp_workdir)
    _populate_over_cap(h.workdir)

    blob = await _drain(h.archive_workdir(_CAP_EXCLUDE, on_note=_boom))

    assert _tar_member_names(blob) == {"keep.txt", "regenerable.txt", "b/a/node_modules/y.js"}


# --- the tar flavour -------------------------------------------------------------

_BSDTAR = "bsdtar 3.7.2 - libarchive 3.7.2 zlib/1.3.1 liblzma/5.4.3 bz2lib/1.0.8"


def _fake_tar(tmp_workdir: str, monkeypatch, version: str) -> str:
    """Put a ``tar`` on PATH whose ``--version`` prints ``version`` and
    appends a line to the returned marker file; anything else runs the real
    tar."""
    real = shutil.which("tar")
    bindir = os.path.join(tmp_workdir, "fakebin-tar")
    os.makedirs(bindir)
    marker = os.path.join(tmp_workdir, "tar-version-probes")
    with open(os.path.join(bindir, "tar"), "w") as fh:
        fh.write(
            "#!/bin/sh\n"
            f'if [ "$1" = --version ]; then echo probe >> {shlex.quote(marker)}; '
            f"echo {shlex.quote(version)}; exit 0; fi\n"
            f'exec {shlex.quote(real)} "$@"\n'
        )
    os.chmod(os.path.join(bindir, "tar"), 0o755)
    monkeypatch.setenv("PATH", bindir + os.pathsep + os.environ["PATH"])
    return marker


async def test_tar_neither_gnu_nor_busybox_captures_everything_and_warns(
    tmp_workdir, monkeypatch, caplog,
):
    # bsdtar strips a pattern's leading ./, so ./big would match anywhere.
    monkeypatch.setenv(ENV_VAR, "1")
    _fake_tar(tmp_workdir, monkeypatch, _BSDTAR)
    h = _host(tmp_workdir)
    _populate_over_cap(h.workdir)
    notes = _Notes()

    with caplog.at_level(logging.INFO, logger="optio_host.archive"):
        blob = await _drain(h.archive_workdir(_CAP_EXCLUDE, on_note=notes))

    assert _tar_member_names(blob) == _ALL_FILES
    assert notes == [(
        "regenerable.txt not applied: tar on this host is not GNU or BusyBox "
        f"({_BSDTAR!r}); capturing everything",
        "warning",
    )]
    assert _archive_notes(caplog) == [(logging.WARNING, notes[0][0])]


async def test_tar_flavour_is_probed_once_per_host(tmp_workdir, monkeypatch):
    monkeypatch.setenv(ENV_VAR, "1")
    marker = _fake_tar(tmp_workdir, monkeypatch, "tar (GNU tar) 1.35")
    h = _host(tmp_workdir)
    _populate_over_cap(h.workdir)

    for _ in range(2):
        blob = await _drain(h.archive_workdir(_CAP_EXCLUDE))
        assert _tar_member_names(blob) == {
            "keep.txt", "regenerable.txt", "b/a/node_modules/y.js",
        }

    assert open(marker).read() == "probe\n"


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
