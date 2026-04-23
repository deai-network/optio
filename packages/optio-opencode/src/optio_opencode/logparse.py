"""Parse lines from the optio.log file that opencode (driven by the LLM) appends to.

The format is keyword-prefixed, one line per event. See the design spec Section 6.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from typing import Union


@dataclass(frozen=True)
class StatusEvent:
    percent: int | None
    message: str


@dataclass(frozen=True)
class DeliverableEvent:
    path: str


@dataclass(frozen=True)
class DoneEvent:
    summary: str | None


@dataclass(frozen=True)
class ErrorEvent:
    message: str | None


@dataclass(frozen=True)
class UnknownLine:
    text: str


LogEvent = Union[StatusEvent, DeliverableEvent, DoneEvent, ErrorEvent, UnknownLine]


_RE_STATUS = re.compile(r"^STATUS:\s*(?:(\d{1,3})%\s+)?(.*)$")
_RE_DELIVERABLE = re.compile(r"^DELIVERABLE:\s*(.+?)\s*$")
_RE_DONE = re.compile(r"^DONE(?::\s*(.*))?\s*$")
_RE_ERROR = re.compile(r"^ERROR(?::\s*(.*))?\s*$")


def parse_log_line(line: str) -> LogEvent:
    """Classify one line from optio.log into a LogEvent."""
    stripped = line.rstrip("\r\n").rstrip()
    m = _RE_STATUS.match(stripped)
    if m:
        pct_raw, msg = m.group(1), m.group(2)
        percent: int | None
        if pct_raw is None:
            percent = None
        else:
            percent = min(int(pct_raw), 100)
        return StatusEvent(percent=percent, message=msg)

    m = _RE_DELIVERABLE.match(stripped)
    if m:
        return DeliverableEvent(path=m.group(1))

    m = _RE_DONE.match(stripped)
    if m:
        summary = m.group(1) if m.group(1) else None
        return DoneEvent(summary=summary)

    m = _RE_ERROR.match(stripped)
    if m:
        msg = m.group(1) if m.group(1) else None
        return ErrorEvent(message=msg)

    return UnknownLine(text=stripped)


def validate_deliverable_path(path: str, workdir: str) -> str:
    """Resolve ``path`` against ``workdir`` and ensure it stays inside.

    Returns the absolute, normalized path.  Raises ValueError on escape.
    """
    workdir_abs = os.path.realpath(workdir)
    if os.path.isabs(path):
        candidate = os.path.realpath(path)
    else:
        candidate = os.path.realpath(os.path.join(workdir_abs, path))

    # Ensure the resolved path is inside workdir_abs.
    rel = os.path.relpath(candidate, workdir_abs)
    if rel == ".." or rel.startswith(".." + os.sep):
        raise ValueError(
            f"deliverable path escapes workdir: {path!r} (resolved to {candidate!r}, "
            f"workdir={workdir_abs!r})"
        )
    return candidate
