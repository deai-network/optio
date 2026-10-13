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
import shlex
from typing import Awaitable, Callable


ENV_VAR = "OPTIO_WORKDIR_CAPTURE_MAX_MB"
DEFAULT_MAX_MB = 100
REGENERABLE_LIST = "regenerable.txt"

# `du` gets this long (`timeout`) to measure the workdir; running out counts
# as over the cap.
DU_TIMEOUT_S = 1

_MIB = 1024 * 1024

# `timeout`'s exit status when the command ran out of time.
_TIMED_OUT = 124


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
    `posixpath.normpath`). Absolute paths, entries with a `..` segment, and
    entries that normalise to `.` (the workdir itself) are skipped.

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
        if path.startswith("/"):
            continue
        if ".." in path.split("/"):
            continue
        if path.startswith("./"):
            path = path[2:]

        normalised = posixpath.normpath(path)
        if normalised == ".":
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


def _list_command(workdir: str) -> str:
    return f"cd {shlex.quote(workdir)} && cat -- {shlex.quote(REGENERABLE_LIST)}"


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
) -> tuple[list[str], str | None]:
    """Decide what a capture of `workdir` leaves out under the cap.

    `run` executes a shell command on the workdir's host and returns its exit
    status and its stdout, decoded with `errors="replace"`. `patterns` are
    the capture's own exclude patterns, so what the capture skips anyway does
    not count. `cap_bytes` is `workdir_capture_max_bytes()`.

    Returns `(anchored_entries, note)`. With the cap off (`0`) nothing runs
    and the result is `([], None)`. Otherwise `du_command` measures the
    workdir; at or under the cap the result is `([], None)`. Over the cap,
    or when `du` fails (timeout included), `regenerable.txt` is read and
    parsed: see `over_cap_excludes` for the result.
    """
    if cap_bytes == 0:
        return [], None
    exit_code, stdout = await run(du_command(workdir, patterns))
    size, unknown = _du_size(exit_code, stdout)
    if size is not None and size <= cap_bytes:
        return [], None
    exit_code, text = await run(_list_command(workdir))
    return over_cap_excludes(size, unknown, cap_bytes, text if exit_code == 0 else "")


def over_cap_excludes(
    size: int | None, unknown: str, cap_bytes: int, list_text: str,
) -> tuple[list[str], str]:
    """The result of a capture decision that found the workdir over the cap.

    `size` is the measured size in bytes, or `None` when the measurement
    failed, `unknown` then saying why. `list_text` is `regenerable.txt`'s
    contents, `""` when it could not be read.

    Returns the parsed entries and the note: the paths left out, or, when
    there are none, a warning that the whole workdir is captured. Sizes in
    the note are whole MiB, rounded down.
    """
    entries = parse_regenerable(list_text)
    cap_mb = cap_bytes // _MIB
    measured = (
        f"workdir {size // _MIB} MB" if size is not None
        else f"workdir size unknown ({unknown})"
    )
    if not entries:
        return [], (
            f"{measured} is over the {cap_mb} MB capture cap, but {REGENERABLE_LIST} "
            "is missing or empty; capturing everything"
        )
    if size is not None:
        measured += f" (cap {cap_mb} MB)"
    paths = "path" if len(entries) == 1 else "paths"
    return entries, (
        f"{measured}: left out {len(entries)} {paths} listed in {REGENERABLE_LIST}"
    )
