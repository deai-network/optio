"""Engine-neutral session-control contract.

A SessionControl is one live, UI-renderable knob a wrapper exposes for its
running session (model, thinking effort, permission/plan mode, ...). It
generalizes the former bespoke model selector: the model is just the
``id="model"`` control. Wrappers emit these (serialized) in their widgetData
and implement ``Conversation.set_control`` to push value changes to the native
transport. Mirrors the frozen-dataclass style of ``seeds.SeedManifest``.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, Sequence

ControlKind = Literal["select", "boolean", "segmented", "slider"]

# A select/segmented control that collapses to a single choice is inherently
# unchangeable — engines mark it disabled with this reason so the UI grays it
# and explains why on hover (see SessionControl.why_disabled).
SINGLE_OPTION_REASON = "Only one option available."

# What the common controls are for, as a question: shown on hover, so a
# narrow controls bar can drop the labels (owner ruling 2026-10-09). Engines
# may word their own.
MODEL_DESCRIPTION = "What model is powering this conversation?"
EFFORT_DESCRIPTION = "How much should the model think before answering?"


@dataclass(frozen=True)
class ControlOption:
    """One member of a ``select`` control's option list."""
    value: str
    label: str
    description: str | None = None
    disabled: bool = False
    why_disabled: str | None = None
    # Shown as the main or a dangerous option, like an action's variant.
    variant: "Literal['primary', 'danger'] | None" = None
    # A question to confirm before switching to this option.
    confirm: str | None = None

    def to_dict(self) -> dict:
        d: dict = {"value": self.value, "label": self.label, "disabled": self.disabled}
        if self.description is not None:
            d["description"] = self.description
        if self.why_disabled is not None:
            d["whyDisabled"] = self.why_disabled
        if self.variant is not None:
            d["variant"] = self.variant
        if self.confirm is not None:
            d["confirm"] = self.confirm
        return d


@dataclass(frozen=True)
class SessionControl:
    """One engine-neutral session control. ``value`` is the current value;
    ``options`` applies to ``select``, ``levels`` (ordered) to ``segmented``,
    and ``boolean`` carries neither."""
    id: str
    kind: ControlKind
    label: str
    value: "str | bool"
    category: str | None = None
    description: str | None = None
    options: "list[ControlOption] | None" = None
    levels: "list[str] | None" = None
    disabled: bool = False
    why_disabled: str | None = None

    def to_dict(self) -> dict:
        d: dict = {
            "id": self.id, "kind": self.kind, "label": self.label,
            "value": self.value, "disabled": self.disabled,
        }
        if self.category is not None:
            d["category"] = self.category
        if self.description is not None:
            d["description"] = self.description
        if self.options is not None:
            d["options"] = [o.to_dict() for o in self.options]
        if self.levels is not None:
            d["levels"] = list(self.levels)
        if self.why_disabled is not None:
            d["whyDisabled"] = self.why_disabled
        return d


def model_control(
    *, models: list[dict], current: str | None, label: str = "Model",
    description: str = MODEL_DESCRIPTION,
) -> SessionControl:
    """Build the ``id="model"`` select from a wrapper's model catalog
    (``[{id,label,description?,disabled?,disabledReason?}]`` — the shape every
    wrapper's ``models.py`` already produces)."""
    options = [
        ControlOption(
            value=m["id"],
            label=m.get("label", m["id"]),
            description=m.get("description"),
            disabled=bool(m.get("disabled", False)),
            why_disabled=m.get("disabledReason"),
        )
        for m in models
    ]
    locked = len(options) <= 1
    return SessionControl(
        id="model", kind="select", label=label, category="model",
        description=description, value=current or "", options=options,
        disabled=locked,
        why_disabled=SINGLE_OPTION_REASON if locked else None,
    )


def effort_control(*, levels, current, disabled=False, why_disabled=None, label="Effort",
                   description=EFFORT_DESCRIPTION):
    """Build the id="reasoning_effort" slider from ordered effort levels."""
    return SessionControl(
        id="reasoning_effort", kind="slider", label=label, category="thought_level",
        description=description,
        value=(current or (levels[0] if levels else "")), levels=list(levels),
        disabled=disabled, why_disabled=why_disabled,
    )


# --- per-task allowlist of offered controls ----------------------------------
# A task config's optional ``session_controls`` names the control ids the
# operator is offered, on top of ``show_session_controls`` (which shows the bar
# at all). None (the default) offers every control the wrapper builds. A
# wrapper filters every controls snapshot it emits through filter_controls and
# refuses a /control change to an id control_allowed rejects, so a hidden
# control cannot be set by posting to the endpoint directly either.

def filter_controls(controls: list[dict], allowed: "Sequence[str] | None") -> list[dict]:
    """The serialized controls whose id is in ``allowed``, in their own order;
    all of them when ``allowed`` is None."""
    if allowed is None:
        return list(controls)
    return [c for c in controls if c.get("id") in allowed]


def control_allowed(control_id: str, allowed: "Sequence[str] | None") -> bool:
    """Whether a /control change to ``control_id`` may go through."""
    return allowed is None or control_id in allowed


def validate_session_controls(
    value: object, *, show_session_controls: bool, owner: str,
) -> None:
    """Config validation for ``session_controls``: None, or a list of non-empty
    control ids, and only together with ``show_session_controls``. ``owner``
    names the config class in the error."""
    if value is None:
        return
    if not isinstance(value, list) or not all(isinstance(v, str) and v for v in value):
        raise ValueError(
            f"{owner}: session_controls must be a list of control ids (or None), "
            f"got {value!r}"
        )
    if not show_session_controls:
        raise ValueError(
            f"{owner}: session_controls narrows the controls shown by "
            "show_session_controls=True; set that too"
        )
