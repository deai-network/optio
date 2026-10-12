"""Engine-neutral task-config vocabulary shared by every wrapper's TaskConfig.

Lifted from the (previously duplicated) per-wrapper types.py. AllowedDir uses
the 4-value superset mode; Landlock-only sandboxes treat rox==ro, rwx==rw."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Awaitable, Callable, Literal, get_args

from optio_agents.session_controls import validate_session_controls
from optio_agents.text import Text, check_text

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


@dataclass(frozen=True)
class SessionControlsConfigMixin:
    """The conversation widget's session-controls bar, shared by every engine
    TaskConfig via inheritance. Fields stay top-level on each config (no
    nesting) so callers write ``show_session_controls=`` verbatim.

    ``show_session_controls`` shows the bar: the engine's live controls (model,
    reasoning effort, permission/plan mode, ...); the engine says which
    configurations allow it. ``session_controls`` optionally narrows the bar to
    the listed control ids; None (the default) offers every control the engine
    builds. A control left out is neither shown nor settable through the
    widget's /control endpoint: engines filter every controls snapshot through
    ``session_controls.filter_controls`` and gate /control with
    ``settable_controls``."""
    show_session_controls: bool = False
    session_controls: list[str] | None = None

    @property
    def settable_controls(self) -> list[str] | None:
        """The control ids /control may change: none while the bar is off, else
        the allowlist (None: every control)."""
        return self.session_controls if self.show_session_controls else []

    def _validate_session_controls(self) -> None:
        """Raise if the allowlist is malformed or set without the bar. Call
        from each engine config's ``__post_init__``."""
        validate_session_controls(
            self.session_controls, show_session_controls=self.show_session_controls,
            owner=type(self).__name__,
        )


@dataclass(frozen=True)
class WelcomeCard:
    """The welcome card the conversation widget shows at the top of the
    transcript.

    ``title`` (required, not blank) and ``description`` (markdown) are
    ``Text``: a plain string, or a map of language code to string
    (``{"en": ..., "hu": ...}``) the widget resolves for the viewer's
    language. ``icon`` is a URL relative to the web app root: a value that
    neither starts with "/" nor contains "://" is published with a leading
    "/" ("img/a.png" -> "/img/a.png"); "/x.png" and absolute URLs
    ("https://...") are published as given. ``min_height`` is the icon's
    minimum height in CSS pixels (a positive int; None: the text's height).
    The icon fills the card's height either way: as tall as the text beside
    it, and at least ``min_height``; without an icon it has no effect."""
    title: Text
    description: Text | None = None
    icon: str | None = None
    min_height: int | None = None

    def __post_init__(self) -> None:
        title = check_text(self.title, what="WelcomeCard.title")
        if any(not t.strip() for t in _text_values(title)):
            raise ValueError(f"WelcomeCard.title={title!r} must not be blank.")
        object.__setattr__(self, "title", title)
        if self.description is not None:
            object.__setattr__(
                self, "description",
                check_text(self.description, what="WelcomeCard.description"),
            )
        if self.icon is not None and (
            not isinstance(self.icon, str) or not self.icon.strip()
        ):
            raise ValueError(
                f"WelcomeCard.icon={self.icon!r} must be a non-empty URL or None."
            )
        if self.min_height is not None and (
            isinstance(self.min_height, bool)
            or not isinstance(self.min_height, int)
            or self.min_height <= 0
        ):
            raise ValueError(
                f"WelcomeCard.min_height={self.min_height!r} must be a positive "
                "int (CSS pixels) or None."
            )

    def to_widget_data(self) -> dict:
        """The card as published in widgetData's ``welcome`` key: the texts
        unresolved (a string, or a plain dict per language), the icon URL
        rooted at the web app, ``minHeight`` in CSS pixels (None values stay
        None)."""
        icon = self.icon
        if icon is not None and not icon.startswith("/") and "://" not in icon:
            icon = "/" + icon
        return {
            "title": _copy_text(self.title),
            "description": _copy_text(self.description),
            "icon": icon,
            "minHeight": self.min_height,
        }


def _text_values(text: Text) -> list[str]:
    """A checked Text's strings: the string itself, or each language's."""
    return [text] if isinstance(text, str) else list(text.values())


def _copy_text(text: Text | None) -> Text | None:
    """A Text for publishing: a string as is, a language map as a new dict."""
    return dict(text) if text is not None and not isinstance(text, str) else text


@dataclass(frozen=True)
class WelcomeConfigMixin:
    """The conversation widget's welcome card, shared by every engine
    TaskConfig via inheritance. The field stays top-level on each config, so
    callers write ``welcome=WelcomeCard(...)`` verbatim.

    ``welcome`` (default None: no card) is shown at the top of the
    conversation transcript. Engines publish ``welcome_widget_data`` as the
    conversation widget's ``welcome`` widgetData key; the iframe / TUI
    widgets do not show it."""
    welcome: WelcomeCard | None = None

    @property
    def welcome_widget_data(self) -> dict | None:
        """widgetData's ``welcome`` value: the card's dict, or None."""
        return self.welcome.to_widget_data() if self.welcome is not None else None

    def _validate_welcome(self) -> None:
        """Raise if ``welcome`` is set to anything but a WelcomeCard. Call
        from each engine config's ``__post_init__``."""
        if self.welcome is not None and not isinstance(self.welcome, WelcomeCard):
            raise ValueError(
                f"{type(self).__name__}.welcome={self.welcome!r} must be a "
                "WelcomeCard or None."
            )
@dataclass(frozen=True, kw_only=True)
class ResumeConfigMixin:
    """How a suspended session resumes, shared by every engine TaskConfig via
    inheritance. Fields stay top-level on each config (no nesting) so callers
    write ``smooth_resume=`` verbatim.

    ``smooth_resume``: this task should be resumed without confirmation when a
    message arrives. Sending a message to the suspended session resumes it
    directly; without it (the default) the widget asks first. A send that
    would resurrect a failed run, discarding its unsaved work, still asks. Only
    Claude Code has a suspended view whose send resumes the session today (it
    publishes the flag as ``widgetData.smoothResume``); the other engines
    accept the flag, and it has no effect on them until they get one."""
    smooth_resume: bool = False
