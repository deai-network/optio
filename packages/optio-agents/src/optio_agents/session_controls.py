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
from typing import Literal

ControlKind = Literal["select", "boolean", "segmented", "slider"]

# A select/segmented control that collapses to a single choice is inherently
# unchangeable — engines mark it disabled with this reason so the UI grays it
# and explains why on hover (see SessionControl.why_disabled).
SINGLE_OPTION_REASON = "Only one option available."


@dataclass(frozen=True)
class ControlOption:
    """One member of a ``select`` control's option list."""
    value: str
    label: str
    description: str | None = None
    disabled: bool = False
    why_disabled: str | None = None

    def to_dict(self) -> dict:
        d: dict = {"value": self.value, "label": self.label, "disabled": self.disabled}
        if self.description is not None:
            d["description"] = self.description
        if self.why_disabled is not None:
            d["whyDisabled"] = self.why_disabled
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
    *, models: list[dict], current: str | None, label: str = "Model"
) -> SessionControl:
    """Build the ``id="model"`` select from a wrapper's model catalog
    (``[{id,label,disabled?,disabledReason?}]`` — the shape every wrapper's
    ``models.py`` already produces)."""
    options = [
        ControlOption(
            value=m["id"],
            label=m.get("label", m["id"]),
            disabled=bool(m.get("disabled", False)),
            why_disabled=m.get("disabledReason"),
        )
        for m in models
    ]
    locked = len(options) <= 1
    return SessionControl(
        id="model", kind="select", label=label, category="model",
        value=current or "", options=options,
        disabled=locked,
        why_disabled=SINGLE_OPTION_REASON if locked else None,
    )


def effort_control(*, levels, current, disabled=False, why_disabled=None, label="Effort"):
    """Build the id="reasoning_effort" slider from ordered effort levels."""
    return SessionControl(
        id="reasoning_effort", kind="slider", label=label, category="thought_level",
        value=(current or (levels[0] if levels else "")), levels=list(levels),
        disabled=disabled, why_disabled=why_disabled,
    )
