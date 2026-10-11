"""Claude Code's session controls, built from the running session's state.

The model select, the reasoning_effort slider (only for a model with graded
effort) and the permission_mode select, serialized for widgetData and for
every x-optio-control-update snapshot. A module function rather than a closure
in the session body, so it is unit-testable without a live host.

Also the task's own custom controls (``ClaudeCodeTaskConfig.custom_controls``,
``optio_agents.custom_controls``): the settings their defaults launch with,
the selection derived from the running values, their widget payload, and the
config checks specific to Claude Code.
"""
from __future__ import annotations

from collections.abc import Mapping, Sequence
from itertools import combinations

from optio_agents.custom_controls import CustomControl
from optio_agents.session_controls import (
    ControlOption,
    SessionControl,
    effort_control,
    filter_controls,
    model_control,
)

from optio_claudecode import models as cc_models

# Claude Code's permission modes, in its own order (its Shift+Tab cycle, then
# dontAsk and bypass): wire value -> (label, description). "default" is the
# value Claude Code reports for the mode its docs and CLI now call "manual".
PERMISSION_MODES: dict[str, tuple[str, str]] = {
    "default": ("Manual", "Asks before editing files or running commands"),
    "acceptEdits": ("Accept edits", "Edits files without asking; asks before other commands"),
    "plan": ("Plan", "Reads and plans without editing; asks you to approve the plan"),
    "auto": ("Auto", "A classifier reviews each action instead of you; risky actions are blocked"),
    "dontAsk": ("Don't ask", "Runs only pre-approved tools; refuses anything that would need approval"),
    "bypassPermissions": ("Bypass", "Runs everything without asking"),
}

# The modes that ask the operator (Manual before acting, Plan to approve its
# plan): usable only when the permission gate routes questions to the widget.
_ASKING_MODES = {"default", "plan"}
_NEEDS_GATE = ("Needs the permission gate: this task has no one to answer Claude's "
               "questions (permission_gate is off).")
_BYPASS_NOT_ALLOWED = "Not allowed for this task (allow_bypass_permissions)."
# Bypass is the dangerous mode: styled so, and switching to it asks first
# (owner ruling 2026-10-09: a simple confirmation).
_BYPASS_CONFIRM = "Switch to Bypass? Claude will run everything without asking."


def canonical_permission_mode(mode: str | None) -> str | None:
    """The wire value for a configured mode: "manual" is reported as "default"."""
    return "default" if mode == "manual" else mode


def permission_mode_unusable(mode: str, *, permission_gate: bool, bypass_allowed: bool) -> str | None:
    """Why this session cannot use ``mode`` (None: it can): a mode that asks,
    without the permission gate; bypass, when the task does not allow it
    (Claude Code refuses to switch into it unless launched able to)."""
    if mode in _ASKING_MODES and not permission_gate:
        return _NEEDS_GATE
    if mode == "bypassPermissions" and not bypass_allowed:
        return _BYPASS_NOT_ALLOWED
    return None


def permission_mode_options(*, permission_gate: bool, bypass_allowed: bool) -> list[ControlOption]:
    """Every Claude Code permission mode, always all six: the ones this session
    cannot use are disabled with the reason (``permission_mode_unusable``)."""
    options = []
    for mode, (label, description) in PERMISSION_MODES.items():
        reason = permission_mode_unusable(
            mode, permission_gate=permission_gate, bypass_allowed=bypass_allowed,
        )
        bypass = mode == "bypassPermissions"
        options.append(ControlOption(
            value=mode, label=label, description=description,
            disabled=reason is not None, why_disabled=reason,
            variant="danger" if bypass else None,
            confirm=_BYPASS_CONFIRM if bypass else None,
        ))
    return options


def settable_permission_modes(options: list[ControlOption]) -> list[str]:
    """The modes a set_control may switch to: the enabled options."""
    return [o.value for o in options if not o.disabled]


def permission_mode_control(*, current: str, options: list[ControlOption]) -> SessionControl:
    return SessionControl(
        id="permission_mode", kind="select", label="Permissions", category="mode",
        description="How should Claude ask before it acts?",
        value=current, options=options,
    )


def build_controls(
    *,
    catalog: list[dict],
    model: str | None,
    effort: str | None,
    permission_mode: str | None,
    permission_options: list[ControlOption],
    allowed: "list[str] | None" = None,
    runtime_model: str | None = None,
) -> list[dict]:
    """The serialized controls snapshot. The model select is always built,
    over ``catalog`` (the CLI's model list, ``models.parse_cli_models``),
    showing ``models.shown_model``: ``model`` (the configured or picked
    value, None: ``default``) unless ``runtime_model`` (the full id
    system/init reported) says otherwise; a value the catalog lacks is added
    as an option. The reasoning_effort slider only when the shown model lists
    effort levels, over those; the permission_mode select (all six modes,
    unusable ones disabled with their reason) showing the running mode (else
    the first usable one). ``allowed`` is the task's session_controls
    allowlist (None: all)."""
    shown = cc_models.shown_model(catalog, picked=model, runtime=runtime_model)
    options = cc_models.with_older_versions_last(cc_models.catalog_with(catalog, shown))
    ctrls = [model_control(models=options, current=shown)]
    levels, default = cc_models.model_effort(shown, options)
    if levels:
        ctrls.append(effort_control(levels=levels, current=effort or default))
    if permission_options:
        settable = settable_permission_modes(permission_options)
        current = canonical_permission_mode(permission_mode) or (settable or [permission_options[0].value])[0]
        ctrls.append(permission_mode_control(current=current, options=permission_options))
    return filter_controls([c.to_dict() for c in ctrls], allowed)


# --- custom controls ---------------------------------------------------------------

# The /control id of a custom control: "custom:<control id>".
CUSTOM_CONTROL_PREFIX = "custom:"


def custom_defaults(controls: "Sequence[CustomControl] | None") -> dict[str, str]:
    """The settings the controls' default options set, keyed as
    ``ControlSettings.to_dict`` (model, reasoning_effort, permission_mode):
    what a session starts with instead of the config's own values. Each key
    belongs to one control at most (config validation), so none overlap."""
    out: dict[str, str] = {}
    for control in controls or ():
        option = next(o for o in control.options if o.value == control.default)
        out.update(option.settings.to_dict())
    return out


def task_models(controls: "Sequence[CustomControl] | None") -> frozenset[str]:
    """The models the controls' options name. The task's own choice, like a
    configured model: launched and switched to as given, even when the CLI's
    model list (its picker's entries) does not name them."""
    return frozenset(
        o.settings.model for c in controls or () for o in c.options if o.settings.model
    )


def custom_control_value(
    control: CustomControl, *, catalog: list[dict],
    model: str | None, effort: str | None, permission_mode: str | None,
) -> str | None:
    """The option of ``control`` whose settings all match the running values,
    else None (e.g. after a manual change in the built-in controls). A model
    is compared as the model select shows it (``models.shown_model`` over
    ``catalog``: no model is ``default``, a full id an alias resolves to is
    that alias); an unknown effort (None) matches no level; ``manual`` is the
    mode ``default``. Settings an option leaves out do not count."""
    running = {
        "model": cc_models.shown_model(catalog, picked=model, runtime=None),
        "reasoning_effort": effort,
        "permission_mode": canonical_permission_mode(permission_mode),
    }
    for option in control.options:
        wanted = option.settings.to_dict()
        if "model" in wanted:
            wanted["model"] = cc_models.shown_model(catalog, picked=wanted["model"], runtime=None)
        if all(running[key] == value for key, value in wanted.items()):
            return option.value
    return None


def custom_controls_snapshot(
    controls: "Sequence[CustomControl] | None", *, catalog: list[dict],
    model: str | None, effort: str | None, permission_mode: str | None,
) -> list[dict]:
    """widgetData.customControls (and every x-optio-control-update's): per
    control ``{id, label, description, options: [{value, label,
    description}], value}``. Labels and descriptions as defined (a string or
    a language map: the widget picks the language); a missing description is
    None; ``value`` is the derived selection (``custom_control_value``).
    Language maps are copied: the payload goes out in events and widgetData,
    the definition stays the task's."""
    def text(value):
        return dict(value) if isinstance(value, Mapping) else value

    return [
        {
            "id": control.id,
            "label": text(control.label),
            "description": text(control.description),
            "options": [
                {"value": o.value, "label": text(o.label), "description": text(o.description)}
                for o in control.options
            ],
            "value": custom_control_value(
                control, catalog=catalog, model=model, effort=effort,
                permission_mode=permission_mode,
            ),
        }
        for control in controls or ()
    ]


def validate_custom_controls(
    controls: "Sequence[CustomControl]", *, permission_gate: bool, bypass_allowed: bool,
    owner: str,
) -> None:
    """Claude Code's checks on a task's custom controls (on top of the shared
    ones, ``CustomControlsConfigMixin._validate_custom_controls``); raise
    ValueError naming ``owner``. Every permission mode an option sets must be
    one this task can switch to (``permission_mode_unusable``): a control's
    default replaces the config's own mode at launch, and a pick switches to
    it live. Two options of one control must differ in a setting both set:
    otherwise both could match the running values and the selection shown
    would be ambiguous."""
    for control in controls:
        for option in control.options:
            mode = option.settings.permission_mode
            reason = mode and permission_mode_unusable(
                mode, permission_gate=permission_gate, bypass_allowed=bypass_allowed,
            )
            if reason:
                raise ValueError(
                    f"{owner}.custom_controls: option {option.value!r} of {control.id!r} sets "
                    f"permission_mode={mode!r}, which this task cannot switch to: {reason}"
                )
        for a, b in combinations(control.options, 2):
            sa, sb = a.settings.to_dict(), b.settings.to_dict()
            if not any(sa[key] != sb[key] for key in sa.keys() & sb.keys()):
                raise ValueError(
                    f"{owner}.custom_controls: options {a.value!r} and {b.value!r} of "
                    f"{control.id!r} could both match the running settings (they must "
                    "differ in a setting both set), so the selection shown would be "
                    "ambiguous."
                )
