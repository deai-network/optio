"""Engine-neutral task-config vocabulary shared by every wrapper's TaskConfig.

Lifted from the (previously duplicated) per-wrapper types.py. AllowedDir uses
the 4-value superset mode; Landlock-only sandboxes treat rox==ro, rwx==rw."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Awaitable, Callable, Literal, get_args

ConversationMode = Literal["iframe", "conversation"]
# Tool-use reporting level (ascending). "description-while-active" shows a tool's
# one-line description WHILE it runs, then hides it once finished (the analysis
# default); "description-only" keeps a persistent one-line row; "verbose" adds the
# args/result detail.
ToolVerbosity = Literal["silent", "description-while-active", "description-only", "verbose"]
ThinkingVerbosity = Literal["hidden", "visible"]
SeedProvider = Callable[[str], Awaitable[str]]

# SSOT validation set, derived from the Literal so it can never drift. Every
# wrapper's TaskConfig validates ``tool_verbosity`` against this (was previously
# a hardcoded copy per wrapper).
TOOL_VERBOSITIES: frozenset[str] = frozenset(get_args(ToolVerbosity))


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


@dataclass(frozen=True)
class BlobCryptoConfigMixin:
    """At-rest crypto for the two GridFS blob channels, shared by every engine
    TaskConfig via inheritance. Fields stay top-level on each config (no
    nesting) so callers write ``session_blob_encrypt=`` verbatim.

    Each field is an optional synchronous bytes->bytes transform applied at
    GridFS write/read. The ``session_blob`` pair wraps this process's session
    snapshot (the per-task home tar, ds-scoped key); the ``seed_blob`` pair
    wraps the SEED tar (the shared pool account, pool-scoped key) — seeds and
    session snapshots live in different key scopes, so they need different
    transforms. Per pair: both set → encrypted at rest; both None (default) →
    plaintext; one without the other is a config error. The seed pair falls
    back to the session pair when unset (back-compat / single-key callers) —
    engines read seed transforms ONLY through the ``seed_encrypt`` /
    ``seed_decrypt`` accessors, the single place that fallback rule lives."""
    session_blob_encrypt: Callable[[bytes], bytes] | None = None
    session_blob_decrypt: Callable[[bytes], bytes] | None = None
    seed_blob_encrypt: Callable[[bytes], bytes] | None = None
    seed_blob_decrypt: Callable[[bytes], bytes] | None = None

    @property
    def seed_encrypt(self) -> Callable[[bytes], bytes] | None:
        """Transform for SEED-tar writes: the seed pair, else the session pair."""
        return self.seed_blob_encrypt or self.session_blob_encrypt

    @property
    def seed_decrypt(self) -> Callable[[bytes], bytes] | None:
        """Transform for SEED-tar reads: the seed pair, else the session pair."""
        return self.seed_blob_decrypt or self.session_blob_decrypt

    def _validate_blob_crypto(self) -> None:
        """Raise if either crypto pair is asymmetric. Call from each engine
        config's ``__post_init__``."""
        if (self.session_blob_encrypt is None) != (self.session_blob_decrypt is None):
            raise ValueError(
                f"{type(self).__name__}: session_blob_encrypt and "
                "session_blob_decrypt must be set together (both callables) "
                "or both left as None; one without the other is a config error."
            )
        if (self.seed_blob_encrypt is None) != (self.seed_blob_decrypt is None):
            raise ValueError(
                f"{type(self).__name__}: seed_blob_encrypt and seed_blob_decrypt "
                "must be set together or both left as None."
            )
