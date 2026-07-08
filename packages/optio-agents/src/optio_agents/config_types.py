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


@dataclass(frozen=True)
class ClaustrumConfigMixin:
    """The claustrum filesystem-isolation triad, shared by every engine
    TaskConfig via inheritance. Fields stay top-level on each config (no nesting)
    so callers write ``fs_isolation=`` / ``delivery_type=`` verbatim.

    Claustrum (Landlock, fail-closed) is the trusted fs-isolation layer on every
    engine. ``delivery_type`` names a subdir under ``<workdir>/deliverables/``
    used to route the "a newer claustrum release is available" notice through
    ``on_deliverable`` — MANDATORY when ``fs_isolation`` is on, because a new
    release may patch a vulnerability the operator must hear about immediately."""
    fs_isolation: bool = True
    extra_allowed_dirs: list[AllowedDir] | None = None
    delivery_type: str | None = None

    def _validate_claustrum(self) -> None:
        """Raise if the claustrum triad is inconsistent. Call from each engine
        config's ``__post_init__``."""
        if self.fs_isolation and not (self.delivery_type and self.delivery_type.strip()):
            raise ValueError(
                f"{type(self).__name__}: fs_isolation is on (default) but "
                "delivery_type is unset. Set delivery_type=<subdir> (routes the "
                "'newer claustrum available' security notice via on_deliverable), "
                "or set fs_isolation=False to opt out."
            )
        for ad in self.extra_allowed_dirs or []:
            if ad.mode not in ("ro", "rw", "rox", "rwx"):
                raise ValueError(
                    f"{type(self).__name__}.extra_allowed_dirs: mode={ad.mode!r} "
                    "must be ro/rw/rox/rwx."
                )
