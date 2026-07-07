"""Engine-neutral task-config vocabulary shared by every wrapper's TaskConfig.

Lifted from the (previously duplicated) per-wrapper types.py. AllowedDir uses
the 4-value superset mode; Landlock-only sandboxes treat rox==ro, rwx==rw."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Awaitable, Callable, Literal

ConversationMode = Literal["iframe", "conversation"]
ToolVerbosity = Literal["silent", "description-only", "verbose"]
ThinkingVerbosity = Literal["hidden", "visible"]
SeedProvider = Callable[[str], Awaitable[str]]


class SeedUnavailableError(Exception):
    """Raised by a SeedProvider when no seed is available for the task."""


@dataclass
class AllowedDir:
    """A filesystem grant beyond the workdir. ``rox``/``rwx`` add an execute
    bit; Landlock-only engines treat them as ``ro``/``rw`` (exec implied)."""
    path: str
    mode: Literal["ro", "rw", "rox", "rwx"]

    def __post_init__(self) -> None:
        if self.mode not in ("ro", "rw", "rox", "rwx"):
            raise ValueError(f"AllowedDir.mode={self.mode!r} must be ro/rw/rox/rwx")
