"""Custom session controls in the shared agent configuration.

A task can show its own selectors in the conversation view, next to (or
instead of) the engine's built-in controls (``show_session_controls``). A
``CustomControl`` is a choice between ``CustomOption``s, each of which sets a
fixed combination of model / reasoning effort / permission mode
(``ControlSettings``); the control names its ``default`` option. Labels and
descriptions are ``Text`` (``optio_agents.text``, shared with the rest of the
agent configuration): a plain string or a per-language map
(``{"en": ..., "hu": ...}``) that ``resolve_text`` reads. A control's label
may be None (no caption); an option's may not.

Every engine TaskConfig inherits ``CustomControlsConfigMixin``. An engine that
cannot apply the controls yet refuses a non-empty list
(``_validate_custom_controls(supported=False)``).

The known effort levels and permission modes live here rather than in an
engine (optio-agents depends on none): they are the values of Claude Code, the
one engine that applies custom controls so far, and optio-agents-all's tests
check that the two stay equal. Checking them here fails a bad option at
config construction, wherever the config is built."""
from __future__ import annotations

import re
from dataclasses import dataclass

from optio_agents.text import Text, check_text

_ID_RE = re.compile(r"[A-Za-z0-9_-]+")

# Graded reasoning-effort levels, ordered low -> max.
KNOWN_REASONING_EFFORTS: tuple[str, ...] = ("low", "medium", "high", "xhigh", "max")

# Permission modes, as the agent reports them ("default" is the mode Claude
# Code's docs now also call "manual").
KNOWN_PERMISSION_MODES: tuple[str, ...] = (
    "default", "acceptEdits", "plan", "auto", "dontAsk", "bypassPermissions",
)

# The settings an option may set, in ControlSettings field order.
SETTING_KEYS: tuple[str, ...] = ("model", "reasoning_effort", "permission_mode")

@dataclass(frozen=True)
class ControlSettings:
    """What one option sets: a model, a reasoning-effort level and/or a
    permission mode. None leaves that setting as it is; at least one is set."""
    model: str | None = None
    reasoning_effort: str | None = None
    permission_mode: str | None = None

    def __post_init__(self) -> None:
        if self.model is None and self.reasoning_effort is None and self.permission_mode is None:
            raise ValueError(
                "ControlSettings must set at least one of model, reasoning_effort, "
                "permission_mode."
            )
        if self.model is not None and (not isinstance(self.model, str) or not self.model):
            raise ValueError(
                f"ControlSettings.model={self.model!r} must be a non-empty string."
            )
        if self.reasoning_effort is not None and self.reasoning_effort not in KNOWN_REASONING_EFFORTS:
            raise ValueError(
                f"ControlSettings.reasoning_effort={self.reasoning_effort!r} is not one "
                f"of {list(KNOWN_REASONING_EFFORTS)}."
            )
        if self.permission_mode is not None and self.permission_mode not in KNOWN_PERMISSION_MODES:
            hint = " ('manual' is spelled 'default' here)" if self.permission_mode == "manual" else ""
            raise ValueError(
                f"ControlSettings.permission_mode={self.permission_mode!r} is not one "
                f"of {list(KNOWN_PERMISSION_MODES)}{hint}."
            )

    def to_dict(self) -> dict[str, str]:
        """The settings this sets (the keys of ``SETTING_KEYS`` that are not
        None), e.g. ``{"model": "opus", "reasoning_effort": "xhigh"}``."""
        return {k: getattr(self, k) for k in SETTING_KEYS if getattr(self, k) is not None}


@dataclass(frozen=True)
class CustomOption:
    """One choice of a custom control: ``value`` identifies it (letters,
    digits, ``_``, ``-``); ``label`` and the optional ``description`` (a
    tooltip) are ``Text``; picking it applies ``settings``."""
    value: str
    label: Text
    settings: ControlSettings
    description: Text | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.value, str) or not _ID_RE.fullmatch(self.value):
            raise ValueError(
                f"CustomOption.value={self.value!r} must be non-empty and use only "
                "letters, digits, '_' and '-'."
            )
        where = f"CustomOption {self.value!r}"
        object.__setattr__(self, "label", check_text(self.label, what=f"{where}: label"))
        if self.description is not None:
            object.__setattr__(
                self, "description", check_text(self.description, what=f"{where}: description")
            )
        if not isinstance(self.settings, ControlSettings):
            raise ValueError(
                f"{where}: settings must be a ControlSettings, "
                f"got {type(self.settings).__name__}."
            )


@dataclass(frozen=True)
class CustomControl:
    """One custom selector: ``id`` (letters, digits, ``_``, ``-``), a ``label``
    (``Text``, or None: no caption) and optional ``description`` (``Text``),
    at least two ``options`` with unique values, and the ``default`` option's
    value, applied when a session starts."""
    id: str
    label: Text | None
    default: str
    options: list[CustomOption]
    description: Text | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.id, str) or not _ID_RE.fullmatch(self.id):
            raise ValueError(
                f"CustomControl.id={self.id!r} must be non-empty and use only "
                "letters, digits, '_' and '-'."
            )
        where = f"CustomControl {self.id!r}"
        if self.label is not None:
            object.__setattr__(self, "label", check_text(self.label, what=f"{where}: label"))
        if self.description is not None:
            object.__setattr__(
                self, "description", check_text(self.description, what=f"{where}: description")
            )
        if not isinstance(self.options, (list, tuple)):
            raise ValueError(
                f"{where}: options must be a list of CustomOption, "
                f"got {type(self.options).__name__}."
            )
        seen: set[str] = set()
        for option in self.options:
            if not isinstance(option, CustomOption):
                raise ValueError(
                    f"{where}: options must hold only CustomOption entries, "
                    f"got {type(option).__name__}."
                )
            if option.value in seen:
                raise ValueError(f"{where}: duplicate option value {option.value!r}.")
            seen.add(option.value)
        if len(self.options) < 2:
            raise ValueError(f"{where} needs at least 2 options, got {len(self.options)}.")
        if self.default not in seen:
            raise ValueError(
                f"{where}: default={self.default!r} is not one of its option values "
                f"{[o.value for o in self.options]}."
            )
        # Own a copy, so the caller's list cannot change a frozen control.
        object.__setattr__(self, "options", list(self.options))


@dataclass(frozen=True, kw_only=True)
class CustomControlsConfigMixin:
    """The ``custom_controls`` setting, shared by every engine TaskConfig via
    inheritance. None (the default) or ``[]``: no custom controls. Independent
    of ``show_session_controls`` / ``session_controls``."""
    custom_controls: list[CustomControl] | None = None

    def _validate_custom_controls(self, *, supported: bool, agent: str) -> None:
        """Raise if ``custom_controls`` is not a list of CustomControl, is
        non-empty on an engine that cannot apply it yet, repeats a control id,
        or has two controls whose options set the same setting. Call from each
        engine config's ``__post_init__``."""
        controls = self.custom_controls
        if controls is None:
            return
        source = f"{type(self).__name__}.custom_controls"
        if not isinstance(controls, (list, tuple)):
            raise ValueError(
                f"{source} must be a list of CustomControl, got {type(controls).__name__}."
            )
        if not controls:
            return
        if not supported:
            raise ValueError(f"{agent} does not support custom_controls yet")
        seen: set[str] = set()
        owner: dict[str, str] = {}  # setting key -> id of the control that sets it
        for control in controls:
            if not isinstance(control, CustomControl):
                raise ValueError(
                    f"{source} must hold only CustomControl entries, "
                    f"got {type(control).__name__}."
                )
            if control.id in seen:
                raise ValueError(f"{source}: duplicate control id {control.id!r}.")
            seen.add(control.id)
            keys = {k for option in control.options for k in option.settings.to_dict()}
            for key in SETTING_KEYS:
                if key not in keys:
                    continue
                if key in owner:
                    raise ValueError(
                        f"{source}: {key} is set by the options of both {owner[key]!r} "
                        f"and {control.id!r}; a setting may belong to one control only."
                    )
                owner[key] = control.id
