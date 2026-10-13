"""The workdir capture cap: the size setting, the regenerable-paths list, and
the capture decision.

`OPTIO_WORKDIR_CAPTURE_MAX_MB` controls how big a workdir can grow before a
suspend capture starts leaving out paths the agent has listed in
`./regenerable.txt` (see `REGENERABLE_LIST`). `regenerable_excludes` makes
that decision for a host through the host's own `run`; both hosts'
`archive_workdir` call it (LocalHost's in-process fallback measures with
`optio_host.archive.fallback_regenerable_excludes` instead).
"""

from __future__ import annotations

import os
import posixpath
import re
import shlex
from typing import Awaitable, Callable, NamedTuple


ENV_VAR = "OPTIO_WORKDIR_CAPTURE_MAX_MB"
DEFAULT_MAX_MB = 100
REGENERABLE_LIST = "regenerable.txt"

# `du` gets this long (`timeout`) to measure the workdir, running out counting
# as over the cap; reading the list on the host gets as long. Read at call
# time, so tests can raise it.
DU_TIMEOUT_S = 1

# At most this much of `regenerable.txt` is read.
LIST_MAX_BYTES = 64 * 1024

# Each applied entry becomes one `literal_exclude` argument in the single
# `bash -c` argument that runs the archive, and Linux caps one exec argument
# at 128 KiB (E2BIG). Entries are applied, in order, while their arguments
# fit in this many bytes; the rest are ignored.
EXCLUDES_MAX_BYTES = 64 * 1024

# Prints the first line of the host's `tar --version`, e.g.
# "tar (GNU tar) 1.35" or "tar (busybox) 1.37.0"; see
# `tar_takes_literal_excludes`.
TAR_VERSION_COMMAND = "tar --version 2>&1 | head -1"

# The characters a tar exclude pattern reads as glob syntax (and `\`, the
# escape character itself).
_GLOB_CHARS = re.compile(r"([\\*?\[])")

# The levels a note reaches the process log at (`ctx.report_progress`'s).
INFO = "info"
WARNING = "warning"

_MIB = 1024 * 1024

# `timeout`'s exit status when the command ran out of time.
_TIMED_OUT = 124

# `list_command`'s exit status when `[ -f regenerable.txt ]` fails.
_NOT_A_FILE = 1

# Why a capture over the cap read no list, as said after "regenerable.txt"
# in its note: the file is missing (or not a regular file), or reading it
# failed (`list_read_failed`).
LIST_MISSING = "is missing or not a regular file"


class CaptureNote(NamedTuple):
    """A capture decision's one-line note, and the level the process log
    shows it at: `WARNING` when the cap could not be applied as the agent
    meant (a list that is missing, unreadable or names no path, a tar that
    cannot take it, a failed decision), else `INFO`."""

    text: str
    level: str


def workdir_capture_max_bytes() -> int:
    """Return the configured capture cap in bytes.

    Reads `OPTIO_WORKDIR_CAPTURE_MAX_MB` from the environment. Unset or
    empty gives the default (`DEFAULT_MAX_MB` MiB). `"0"` means the feature
    is off. Any other non-negative integer is that many MiB. Anything else
    raises `ValueError`.
    """
    raw = os.environ.get(ENV_VAR)
    if raw is None or raw == "":
        return DEFAULT_MAX_MB * _MIB

    try:
        value = int(raw)
    except ValueError:
        value = -1

    if value < 0:
        raise ValueError(
            f"{ENV_VAR} must be a non-negative integer (MiB), got '{raw}'"
        )
    return value * _MIB


def parse_regenerable(text: str) -> list[str]:
    """Parse the contents of a `regenerable.txt` file.

    One path per line, relative to the workdir root. A line is a comment
    when its first non-space character is `#`; such lines, and blank
    lines, are skipped. A path line may carry a free note after a literal
    ` #`, which is dropped along with surrounding whitespace.

    `./x/` normalises to `x` (leading `./` stripped, then
    `posixpath.normpath`). Absolute paths, entries with a `..` segment,
    entries that normalise to `.` (the workdir itself), entries containing a
    NUL byte (no path can, and exec rejects one), and `regenerable.txt`
    itself (the list is always captured) are skipped.

    Returns the surviving entries in their original order, without
    duplicates.
    """
    result: list[str] = []
    seen: set[str] = set()

    for line in text.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        if stripped.startswith("#"):
            continue

        note_at = stripped.find(" #")
        if note_at != -1:
            stripped = stripped[:note_at].rstrip()
        if not stripped:
            continue

        path = stripped
        if "\x00" in path:
            continue
        if path.startswith("/"):
            continue
        if ".." in path.split("/"):
            continue
        if path.startswith("./"):
            path = path[2:]

        normalised = posixpath.normpath(path)
        if normalised in (".", REGENERABLE_LIST):
            continue
        if normalised in seen:
            continue

        seen.add(normalised)
        result.append(normalised)

    return result


def du_command(workdir: str, patterns: list[str]) -> str:
    """Shell command printing the size of `workdir` as a capture sees it.

    `du -sb` (apparent size in bytes) with `patterns` as `--exclude`s, which
    du matches anywhere in the tree like tar's default, under
    `timeout DU_TIMEOUT_S` (exit 124 when it runs out).
    """
    words = ["timeout", str(DU_TIMEOUT_S), "du", "-sb"]
    words += [f"--exclude={shlex.quote(p)}" for p in patterns]
    words.append(".")
    return f"cd {shlex.quote(workdir)} && " + " ".join(words)


def list_command(workdir: str) -> str:
    """Shell command printing at most `LIST_MAX_BYTES` of `workdir`'s
    `regenerable.txt`, under `timeout DU_TIMEOUT_S`. Anything but a regular
    file (or a symlink to one) fails without being opened, so a FIFO cannot
    block it: the `[ -f ]` test exits 1 (`LIST_MISSING`). Other failures
    exit otherwise, e.g. 127 without `timeout`, or 124 when it fires."""
    name = shlex.quote(REGENERABLE_LIST)
    return (
        f"cd {shlex.quote(workdir)} && [ -f {name} ] && "
        f"timeout {DU_TIMEOUT_S} head -c {LIST_MAX_BYTES} -- {name}"
    )


def list_read_failed(detail: str) -> str:
    """Why a capture over the cap read no list when the list is there but
    reading it failed, `detail` saying how (e.g. `exit 127`)."""
    return f"could not be read ({detail})"


def _du_size(exit_code: int, stdout: str) -> tuple[int | None, str]:
    """`(size, "")` from a successful `du -sb`, else `(None, why)`."""
    if exit_code == _TIMED_OUT:
        return None, f"du timed out after {DU_TIMEOUT_S} s"
    if exit_code != 0:
        return None, f"du failed with exit {exit_code}"
    try:
        return int(stdout.split()[0]), ""
    except (IndexError, ValueError):
        return None, "du printed no size"


async def regenerable_excludes(
    run: Callable[[str], Awaitable[tuple[int, str]]],
    workdir: str,
    patterns: list[str],
    cap_bytes: int,
) -> tuple[list[str], CaptureNote | None]:
    """Decide what a capture of `workdir` leaves out under the cap.

    `run` executes a shell command on the workdir's host and returns its exit
    status and its stdout, decoded with `errors="replace"`. `patterns` are
    the capture's own exclude patterns, so what the capture skips anyway does
    not count. `cap_bytes` is `workdir_capture_max_bytes()`.

    Returns `(anchored_entries, note)`. With the cap off (`0`) nothing runs
    and the result is `([], None)`. Otherwise `du_command` measures the
    workdir; at or under the cap the result is `([], None)`. Over the cap,
    or when `du` fails (timeout included), `list_command` reads
    `regenerable.txt`: see `over_cap_excludes` for the result. A read that
    fails leaves nothing out, its note telling a missing list (exit 1) from
    any other failure. Exceptions from `run` propagate.
    """
    if cap_bytes == 0:
        return [], None
    exit_code, stdout = await run(du_command(workdir, patterns))
    size, unknown = _du_size(exit_code, stdout)
    if size is not None and size <= cap_bytes:
        return [], None
    exit_code, text = await run(list_command(workdir))
    if exit_code == 0:
        return over_cap_excludes(size, unknown, cap_bytes, text)
    unread = (
        LIST_MISSING if exit_code == _NOT_A_FILE
        else list_read_failed(f"exit {exit_code}")
    )
    return over_cap_excludes(size, unknown, cap_bytes, "", unread)


def literal_exclude(entry: str) -> str:
    """The tar option, shell-quoted, that leaves out exactly `./<entry>` and
    everything under it, on GNU tar and BusyBox tar alike.

    The archive runs `tar cf - .`, so member names start with `./`, and both
    tars try an exclude pattern only from the start of a name or of one of
    its components: `./node_modules` matches `./node_modules` but never
    `./pkg/node_modules`. `\\`, `*`, `?` and `[` are backslash-escaped, so
    the entry is matched literally (`build[1]` never matches `build1`). No
    mode options such as GNU's `--anchored --no-wildcards`, which BusyBox
    tar rejects."""
    escaped = _GLOB_CHARS.sub(r"\\\1", entry)
    return "--exclude=" + shlex.quote("./" + escaped)


def tar_takes_literal_excludes(version_line: str) -> bool:
    """Whether the tar whose `TAR_VERSION_COMMAND` printed `version_line`
    reads `literal_exclude`'s patterns as meant: GNU tar and BusyBox tar do.
    Any other tar does not (bsdtar strips a pattern's leading `./`, so it
    would match anywhere), and the list is not applied."""
    return "GNU tar" in version_line or "busybox" in version_line.lower()


def unknown_tar_note(version_line: str) -> CaptureNote:
    """The note, a warning, of a capture over the cap whose host's tar fails
    `tar_takes_literal_excludes`, so nothing is left out."""
    return CaptureNote(
        f"{REGENERABLE_LIST} not applied: tar on this host is not GNU or "
        f"BusyBox ({version_line!r}); capturing everything",
        WARNING,
    )


def _complete_lines(text: str) -> str:
    """`text`, read with the `LIST_MAX_BYTES` limit, without a last line the
    limit may have cut short: a cut-off `src/gen` must not exclude `./src`.

    The decoded text is at least as long in UTF-8 as the bytes it came from
    (a replacement character takes 3 bytes), so a shorter text was never
    cut."""
    if len(text.encode("utf-8")) < LIST_MAX_BYTES or text.endswith("\n"):
        return text
    return text[: text.rfind("\n") + 1]


def _exec_cost(entry: str) -> int:
    """Bytes that `entry` adds to the archive's `bash -c` argument: its
    `literal_exclude` argument (escapes included) and a separator, plus 4
    for each single quote, which the outer `bash -c` quoting turns into
    `'"'"'`."""
    arg = literal_exclude(entry)
    return len(arg) + 4 * arg.count("'") + 1


def _within_exec_limit(entries: list[str]) -> list[str]:
    """The leading `entries` whose arguments fit in `EXCLUDES_MAX_BYTES`."""
    total = 0
    for count, entry in enumerate(entries):
        total += _exec_cost(entry)
        if total > EXCLUDES_MAX_BYTES:
            return entries[:count]
    return entries


def over_cap_excludes(
    size: int | None,
    unknown: str,
    cap_bytes: int,
    list_text: str,
    unread: str | None = None,
) -> tuple[list[str], CaptureNote]:
    """The result of a capture decision that found the workdir over the cap.

    `size` is the measured size in bytes, or `None` when the measurement
    failed, `unknown` then saying why. `list_text` is `regenerable.txt`'s
    contents as read (at most `LIST_MAX_BYTES`); a last line the limit cut
    short is dropped. `unread`, when the list could not be read, says why
    (`LIST_MISSING`, or `list_read_failed(...)`), and `list_text` is then
    ignored.

    Returns the entries to apply and the note: the paths left out (`INFO`),
    or, when there are none, a `WARNING` that the whole workdir is captured,
    naming why (`unread`, or that the list names no paths). Entries beyond
    `EXCLUDES_MAX_BYTES` of arguments are ignored, and the note says how
    many. Sizes in the note are whole MiB, rounded down.
    """
    listed = [] if unread else parse_regenerable(_complete_lines(list_text))
    entries = _within_exec_limit(listed)
    cap_mb = cap_bytes // _MIB
    measured = (
        f"workdir {size // _MIB} MB" if size is not None
        else f"workdir size unknown ({unknown})"
    )
    if not listed:
        return [], CaptureNote(
            f"{measured} is over the {cap_mb} MB capture cap, but {REGENERABLE_LIST} "
            f"{unread or 'lists no paths'}; capturing everything",
            WARNING,
        )
    if size is not None:
        measured += f" (cap {cap_mb} MB)"
    paths = "path" if len(entries) == 1 else "paths"
    note = f"{measured}: left out {len(entries)} {paths} listed in {REGENERABLE_LIST}"
    if len(entries) < len(listed):
        note += f" ({len(listed) - len(entries)} more ignored: list too long)"
    return entries, CaptureNote(note, INFO)
