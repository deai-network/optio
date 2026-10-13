"""Tests for the workdir capture cap: the setting, the regenerable list
parser, and the capture decision (`regenerable_excludes`)."""

import os
import subprocess

import pytest

from optio_host import capture_cap
from optio_host.capture_cap import (
    DEFAULT_MAX_MB,
    ENV_VAR,
    REGENERABLE_LIST,
    du_command,
    list_command,
    parse_regenerable,
    regenerable_excludes,
    workdir_capture_max_bytes,
)


def test_module_constants():
    assert ENV_VAR == "OPTIO_WORKDIR_CAPTURE_MAX_MB"
    assert DEFAULT_MAX_MB == 100
    assert REGENERABLE_LIST == "regenerable.txt"


def test_unset_gives_default(monkeypatch):
    monkeypatch.delenv(ENV_VAR, raising=False)
    assert workdir_capture_max_bytes() == 104857600


def test_empty_gives_default(monkeypatch):
    monkeypatch.setenv(ENV_VAR, "")
    assert workdir_capture_max_bytes() == 104857600


def test_zero_turns_it_off(monkeypatch):
    monkeypatch.setenv(ENV_VAR, "0")
    assert workdir_capture_max_bytes() == 0


def test_valid_value_is_used(monkeypatch):
    monkeypatch.setenv(ENV_VAR, "250")
    assert workdir_capture_max_bytes() == 250 * 1048576


@pytest.mark.parametrize("bad", ["-1", "1.5", "lots"])
def test_invalid_value_raises(monkeypatch, bad):
    monkeypatch.setenv(ENV_VAR, bad)
    with pytest.raises(ValueError, match="OPTIO_WORKDIR_CAPTURE_MAX_MB"):
        workdir_capture_max_bytes()


def test_parse_regenerable_sanitises():
    text = (
        "# deps\n"
        "node_modules  # npm ci\n"
        "./frontend/node_modules/\n"
        "\n"
        "/abs\n"
        "../out\n"
        "a/../b\n"
        ".\n"
        "build[1]\n"
        "node_modules\n"
    )
    assert parse_regenerable(text) == [
        "node_modules",
        "frontend/node_modules",
        "build[1]",
    ]


def test_parse_regenerable_empty_text():
    assert parse_regenerable("") == []


def test_parse_regenerable_drops_entries_with_a_nul_byte():
    # A POSIX path cannot contain NUL, and exec refuses an argument with one.
    assert parse_regenerable("a\x00b\nc\n\x00\n") == ["c"]


# --- the capture decision ------------------------------------------------------

_MIB = 1024 * 1024
_W = "/w"
_PATTERNS = [".git", "*.pyc"]
_LIST_COMMAND = (
    "cd /w && [ -f regenerable.txt ] && timeout 1 head -c 65536 -- regenerable.txt"
)


class _FakeRun:
    """Stands in for a host's `run`: answers the decision's `du` with `du` and
    its read of the list with `cat`, recording every command."""

    def __init__(
        self,
        du: tuple[int, str] = (0, "4096\t.\n"),
        cat: tuple[int, str] = (1, ""),
    ) -> None:
        self.du = du
        self.cat = cat
        self.commands: list[str] = []

    async def __call__(self, cmd: str) -> tuple[int, str]:
        self.commands.append(cmd)
        if cmd == du_command(_W, _PATTERNS):
            return self.du
        if cmd == _LIST_COMMAND:
            return self.cat
        raise AssertionError(f"unexpected command {cmd!r}")


def test_du_command_shape():
    assert du_command("/w d", ["a b", "*.pyc"]) == (
        "cd '/w d' && timeout 1 du -sb --exclude='a b' --exclude='*.pyc' ."
    )
    assert du_command("/w", []) == "cd /w && timeout 1 du -sb ."


def test_list_command_shape():
    assert list_command("/w") == _LIST_COMMAND


def test_du_command_measures_without_the_excluded_paths(tmp_path, monkeypatch):
    monkeypatch.setattr(capture_cap, "DU_TIMEOUT_S", 60)  # not about the timeout
    workdir = tmp_path / "w d's $HOME"
    (workdir / "skip me").mkdir(parents=True)
    (workdir / "skip me" / "blob").write_bytes(bytes(100_000))
    (workdir / "keep").write_bytes(bytes(1000))

    def size(patterns: list[str]) -> int:
        proc = subprocess.run(
            ["bash", "-c", du_command(str(workdir), patterns)],
            capture_output=True, text=True,
        )
        assert proc.returncode == 0, proc.stderr
        return int(proc.stdout.split()[0])

    assert 1000 <= size(["skip me"]) < 100_000
    assert size([]) >= 101_000


async def test_cap_zero_runs_nothing():
    run = _FakeRun()

    assert await regenerable_excludes(run, _W, _PATTERNS, 0) == ([], None)
    assert run.commands == []


@pytest.mark.parametrize("size", [1000, 2000])
async def test_under_or_at_the_cap_leaves_nothing_out(size):
    run = _FakeRun(du=(0, f"{size}\t.\n"), cat=(0, "a\n"))

    assert await regenerable_excludes(run, _W, _PATTERNS, 2000) == ([], None)
    assert run.commands == [du_command(_W, _PATTERNS)]


async def test_over_the_cap_leaves_out_the_listed_paths():
    # 2.5 MiB: the note's sizes are whole MiB, rounded down.
    run = _FakeRun(du=(0, f"{2 * _MIB + _MIB // 2}\t.\n"), cat=(0, "a\nb\n"))

    entries, note = await regenerable_excludes(run, _W, _PATTERNS, _MIB)

    assert entries == ["a", "b"]
    assert note == (
        "workdir 2 MB (cap 1 MB): left out 2 paths listed in regenerable.txt"
    )
    assert run.commands == [du_command(_W, _PATTERNS), _LIST_COMMAND]


async def test_over_the_cap_with_one_entry_says_path():
    run = _FakeRun(du=(0, f"{3 * _MIB}\t.\n"), cat=(0, "a\n"))

    _, note = await regenerable_excludes(run, _W, _PATTERNS, _MIB)

    assert note == "workdir 3 MB (cap 1 MB): left out 1 path listed in regenerable.txt"


async def test_du_timeout_counts_as_over_the_cap():
    run = _FakeRun(du=(124, ""), cat=(0, "a\nb\n"))

    entries, note = await regenerable_excludes(run, _W, _PATTERNS, 100 * _MIB)

    assert entries == ["a", "b"]
    assert note == (
        "workdir size unknown (du timed out after 1 s): "
        "left out 2 paths listed in regenerable.txt"
    )


async def test_other_du_failure_counts_as_over_the_cap():
    run = _FakeRun(du=(127, ""), cat=(0, "a\n"))

    entries, note = await regenerable_excludes(run, _W, _PATTERNS, 100 * _MIB)

    assert entries == ["a"]
    assert note.startswith("workdir size unknown (du failed with exit 127): ")


async def test_over_the_cap_without_a_list_leaves_nothing_out():
    run = _FakeRun(du=(0, f"{2 * _MIB}\t.\n"), cat=(1, ""))

    entries, note = await regenerable_excludes(run, _W, _PATTERNS, _MIB)

    assert entries == []
    assert note == (
        "workdir 2 MB is over the 1 MB capture cap, but regenerable.txt "
        "is missing or empty; capturing everything"
    )


async def test_du_timeout_without_a_list_leaves_nothing_out():
    run = _FakeRun(du=(124, ""), cat=(0, "# nothing yet\n"))

    entries, note = await regenerable_excludes(run, _W, _PATTERNS, _MIB)

    assert entries == []
    assert note == (
        "workdir size unknown (du timed out after 1 s) is over the 1 MB "
        "capture cap, but regenerable.txt is missing or empty; capturing everything"
    )


async def test_binary_list_does_not_raise():
    # What a binary regenerable.txt looks like once the host's run has
    # decoded it with errors="replace".
    run = _FakeRun(du=(0, f"{2 * _MIB}\t.\n"), cat=(0, "\ufffd\ufffd\n"))

    entries, note = await regenerable_excludes(run, _W, _PATTERNS, _MIB)

    assert entries == ["\ufffd\ufffd"]
    assert "left out 1 path" in note


def _run_list_command(workdir: str) -> subprocess.CompletedProcess:
    return subprocess.run(["bash", "-c", list_command(workdir)], capture_output=True)


def test_list_command_reads_at_most_64_kib(tmp_path, monkeypatch):
    monkeypatch.setattr(capture_cap, "DU_TIMEOUT_S", 60)  # not about the timeout
    (tmp_path / "regenerable.txt").write_bytes(b"a\n" * 50_000)

    proc = _run_list_command(str(tmp_path))

    assert proc.returncode == 0
    assert proc.stdout == (b"a\n" * 50_000)[:65536]


@pytest.mark.parametrize("kind", ["fifo", "dev-zero symlink"])
def test_list_command_skips_what_is_not_a_regular_file(tmp_path, monkeypatch, kind):
    # Not reaching the timeout: a FIFO would block `head` until it fires.
    monkeypatch.setattr(capture_cap, "DU_TIMEOUT_S", 60)
    path = tmp_path / "regenerable.txt"
    if kind == "fifo":
        os.mkfifo(path)
    else:
        os.symlink("/dev/zero", path)

    proc = _run_list_command(str(tmp_path))

    assert proc.returncode != 0
    assert proc.stdout == b""


async def test_list_with_a_nul_byte_drops_that_entry():
    # b"big\n\x89PNG\x00\x01\xff\n", as the host's run decodes it.
    run = _FakeRun(du=(0, f"{2 * _MIB}\t.\n"), cat=(0, "big\n\ufffdPNG\x00\x01\ufffd\n"))

    entries, note = await regenerable_excludes(run, _W, _PATTERNS, _MIB)

    assert entries == ["big"]
    assert note == "workdir 2 MB (cap 1 MB): left out 1 path listed in regenerable.txt"


async def test_list_cut_at_the_read_limit_drops_its_cut_off_last_line():
    # What `head -c 65536` returns from a longer list: "keep.txt" cut to
    # "keep", which must not exclude an unrelated ./keep.
    head = "big\n" + "#" * (65536 - 4 - 1 - 4) + "\n" + "keep"
    assert len(head.encode()) == 65536
    run = _FakeRun(du=(0, f"{2 * _MIB}\t.\n"), cat=(0, head))

    entries, _ = await regenerable_excludes(run, _W, _PATTERNS, _MIB)

    assert entries == ["big"]


async def test_short_list_keeps_a_last_line_without_newline():
    run = _FakeRun(du=(0, f"{2 * _MIB}\t.\n"), cat=(0, "big\nkeep"))

    entries, _ = await regenerable_excludes(run, _W, _PATTERNS, _MIB)

    assert entries == ["big", "keep"]


async def test_entries_beyond_the_command_line_budget_are_ignored():
    # Each entry becomes `--exclude=./<entry>` in one exec argument, which
    # Linux caps at 128 KiB; entries are kept while they fit in 64 KiB.
    names = [f"{i:04x}" for i in range(6000)]
    run = _FakeRun(du=(0, f"{2 * _MIB}\t.\n"), cat=(0, "\n".join(names) + "\n"))

    entries, note = await regenerable_excludes(run, _W, _PATTERNS, _MIB)

    # Each costs len("--exclude=./0000") + 1 separator = 17 bytes.
    kept = 65536 // 17
    assert kept == 3855
    assert entries == names[:kept]
    assert note == (
        "workdir 2 MB (cap 1 MB): left out 3855 paths listed in regenerable.txt "
        "(2145 more ignored: list too long)"
    )


# --- literal excludes and the tar flavour ---------------------------------------


@pytest.mark.parametrize("entry, arg", [
    ("node_modules", "--exclude=./node_modules"),
    ("build[1]", "--exclude='./build\\[1]'"),
    ("star/*", "--exclude='./star/\\*'"),
    ("a?b", "--exclude='./a\\?b'"),
    ("back\\slash", "--exclude='./back\\\\slash'"),
    ("it's", "--exclude='./it'\"'\"'s'"),
])
def test_literal_exclude_escapes_glob_characters(entry, arg):
    # GNU tar and BusyBox tar both read a backslash-escaped character
    # literally; `./` anchors the pattern at the archive root.
    assert capture_cap.literal_exclude(entry) == arg


async def test_escapes_count_against_the_command_line_budget():
    # "0000*" becomes --exclude='./0000\*': 20 bytes, plus 4 for each of its
    # two single quotes (the outer `bash -c` quoting), plus a separator.
    names = [f"{i:04x}*" for i in range(6000)]
    run = _FakeRun(du=(0, f"{2 * _MIB}\t.\n"), cat=(0, "\n".join(names) + "\n"))

    entries, _ = await regenerable_excludes(run, _W, _PATTERNS, _MIB)

    kept = 65536 // 29
    assert kept == 2259
    assert entries == names[:kept]


@pytest.mark.parametrize("version_line, applies", [
    ("tar (GNU tar) 1.35", True),
    ("tar (busybox) 1.37.0", True),
    ("bsdtar 3.7.2 - libarchive 3.7.2 zlib/1.3.1 liblzma/5.4.3 bz2lib/1.0.8", False),
    ("", False),
])
def test_tar_takes_literal_excludes(version_line, applies):
    assert capture_cap.tar_takes_literal_excludes(version_line) is applies


def test_tar_version_command():
    assert capture_cap.TAR_VERSION_COMMAND == "tar --version 2>&1 | head -1"
