"""The workdir capture cap: the size setting and the regenerable-paths list.

`OPTIO_WORKDIR_CAPTURE_MAX_MB` controls how big a workdir can grow before a
suspend capture starts leaving out paths the agent has listed in
`./regenerable.txt` (see `REGENERABLE_LIST`). This module only reads the
setting and parses the list's text; deciding when to apply it and reading
the file from a host are later tasks.
"""

from __future__ import annotations

import os
import posixpath


ENV_VAR = "OPTIO_WORKDIR_CAPTURE_MAX_MB"
DEFAULT_MAX_MB = 100
REGENERABLE_LIST = "regenerable.txt"

_MIB = 1024 * 1024


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
