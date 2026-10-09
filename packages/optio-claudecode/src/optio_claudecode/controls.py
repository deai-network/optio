"""Claude Code's session controls, built from the running session's state.

The model select, the reasoning_effort slider (only for a model with graded
effort) and the permission_mode select, serialized for widgetData and for
every x-optio-control-update snapshot. A module function rather than a closure
in the session body, so it is unit-testable without a live host.
"""
from __future__ import annotations

from optio_agents.session_controls import (
    SINGLE_OPTION_REASON,
    ControlOption,
    SessionControl,
    effort_control,
    filter_controls,
    model_control,
)

from optio_claudecode import models as cc_models

# Claude Code's permission modes: (label, description) as the select shows them.
PERMISSION_MODES: dict[str, tuple[str, str]] = {
    "default": ("Ask", "Ask before editing files or running commands"),
    "acceptEdits": ("Accept edits", "Edit files without asking"),
    "plan": ("Plan", "Read and plan only; no edits or commands"),
    "dontAsk": ("Don't ask", "Run only pre-approved tools; refuse the rest without asking"),
    "bypassPermissions": ("Bypass", "Run everything without asking"),
}

# With the permission gate a question reaches the operator, so the modes that
# ask are usable; without it nobody can answer one, so only the modes that
# never ask are (ClaudeCodeTaskConfig's headless-safe rule).
_GATED_MODES = ["default", "acceptEdits", "plan", "dontAsk"]
_UNGATED_MODES = ["acceptEdits", "dontAsk"]


def offered_permission_modes(*, permission_gate: bool, launch_mode: str | None) -> list[str]:
    """The permission modes a session can switch between. bypassPermissions
    only when the session was launched in it: Claude Code refuses to switch
    into it otherwise (verified on 2.1.268: "Cannot set permission mode to
    bypassPermissions because the session was not launched with
    --dangerously-skip-permissions")."""
    modes = list(_GATED_MODES if permission_gate else _UNGATED_MODES)
    if launch_mode == "bypassPermissions":
        modes.append("bypassPermissions")
    return modes


def permission_mode_control(*, current: str, modes: list[str]) -> SessionControl:
    options = [
        ControlOption(value=m, label=PERMISSION_MODES[m][0], description=PERMISSION_MODES[m][1])
        for m in modes
    ]
    locked = len(options) <= 1
    return SessionControl(
        id="permission_mode", kind="select", label="Permissions", category="mode",
        value=current, options=options,
        disabled=locked, why_disabled=SINGLE_OPTION_REASON if locked else None,
    )


def build_controls(
    *,
    catalog: list[dict],
    model: str | None,
    effort: str | None,
    permission_mode: str | None,
    permission_modes: list[str],
    allowed: "list[str] | None" = None,
) -> list[dict]:
    """The serialized controls snapshot. The model select is always built; the
    reasoning_effort slider only when the running model advertises graded
    effort (model may be None before system/init names it); the
    permission_mode select when the session offers any mode, showing the
    running mode (else the first offered). ``allowed`` is the task's
    session_controls allowlist (None: all)."""
    ctrls = [model_control(models=catalog, current=model)]
    levels, default = cc_models.model_effort(model, catalog) if model else (None, None)
    if levels:
        ctrls.append(effort_control(levels=levels, current=effort or default))
    if permission_modes:
        ctrls.append(permission_mode_control(
            current=permission_mode or permission_modes[0], modes=permission_modes,
        ))
    return filter_controls([c.to_dict() for c in ctrls], allowed)
