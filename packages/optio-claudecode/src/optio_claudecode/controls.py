"""Claude Code's session controls, built from the running session's state.

The model select, the reasoning_effort slider (only for a model with graded
effort) and the permission_mode select, serialized for widgetData and for
every x-optio-control-update snapshot. A module function rather than a closure
in the session body, so it is unit-testable without a live host.
"""
from __future__ import annotations

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


def permission_mode_options(*, permission_gate: bool, bypass_allowed: bool) -> list[ControlOption]:
    """Every Claude Code permission mode, always all six: the ones this session
    cannot use are disabled with the reason (the modes that ask, without the
    permission gate; bypass, when the task does not allow it: Claude Code
    refuses to switch into it unless launched able to)."""
    options = []
    for mode, (label, description) in PERMISSION_MODES.items():
        reason = None
        if mode in _ASKING_MODES and not permission_gate:
            reason = _NEEDS_GATE
        elif mode == "bypassPermissions" and not bypass_allowed:
            reason = _BYPASS_NOT_ALLOWED
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
    options = cc_models.catalog_with(catalog, shown)
    ctrls = [model_control(models=options, current=shown)]
    levels, default = cc_models.model_effort(shown, options)
    if levels:
        ctrls.append(effort_control(levels=levels, current=effort or default))
    if permission_options:
        settable = settable_permission_modes(permission_options)
        current = canonical_permission_mode(permission_mode) or (settable or [permission_options[0].value])[0]
        ctrls.append(permission_mode_control(current=current, options=permission_options))
    return filter_controls([c.to_dict() for c in ctrls], allowed)
