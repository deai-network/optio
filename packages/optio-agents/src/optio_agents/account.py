"""Normalized cross-engine account identity + usage/limit status.

One vendor-agnostic ``AccountInfo`` every engine's ``analyze_account`` returns,
plus the shared ``is_limited`` gate. No vendor code lives here — the per-engine
analyzers map their vendor payload into this shape."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime


@dataclass(frozen=True)
class UsageWindow:
    label: str                  # vendor window id, e.g. "five_hour", "seven_day_opus"
    pct: float                  # utilization 0-100
    resets_at: datetime | None  # tz-aware; None if the vendor omits it
    model: str | None = None    # set for per-model windows, else None (global)

    def to_dict(self) -> dict:
        return {
            "label": self.label,
            "pct": self.pct,
            "resets_at": self.resets_at.isoformat() if self.resets_at else None,
            "model": self.model,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "UsageWindow":
        ra = d.get("resets_at")
        return cls(
            label=d["label"], pct=d["pct"],
            resets_at=datetime.fromisoformat(ra) if ra else None,
            model=d.get("model"),
        )


@dataclass(frozen=True)
class AccountInfo:
    name: str | None = None
    email: str | None = None
    plan: str | None = None
    account_id: str | None = None
    windows: tuple[UsageWindow, ...] = field(default_factory=tuple)
    raw: dict = field(default_factory=dict)

    def __post_init__(self):
        # Honor the declared ``tuple`` type even when a list is passed in, so
        # equality (and to_dict/from_dict roundtrips) is stable regardless of
        # the caller's sequence type.
        if not isinstance(self.windows, tuple):
            object.__setattr__(self, "windows", tuple(self.windows))

    @property
    def summary(self) -> str | None:
        """``"Plan: <plan> for <name> <<email>>"`` (name optional). Requires a
        plan and an email, else None. Generalized form of claudecode's
        ``format_account_summary``."""
        if not self.plan or not self.email:
            return None
        if self.name:
            return f"Plan: {self.plan} for {self.name} <{self.email}>"
        return f"Plan: {self.plan} for <{self.email}>"

    def next_reset(self) -> datetime | None:
        """Soonest ``resets_at`` among currently-maxed (pct >= 100) windows that
        have one; None if nothing is maxed / no reset times."""
        candidates = [w.resets_at for w in self.windows if w.pct >= 100 and w.resets_at]
        return min(candidates) if candidates else None

    def to_dict(self) -> dict:
        return {
            "name": self.name, "email": self.email, "plan": self.plan,
            "account_id": self.account_id,
            "windows": [w.to_dict() for w in self.windows],
            "raw": self.raw,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "AccountInfo":
        return cls(
            name=d.get("name"), email=d.get("email"), plan=d.get("plan"),
            account_id=d.get("account_id"),
            windows=tuple(UsageWindow.from_dict(w) for w in d.get("windows") or ()),
            raw=d.get("raw") or {},
        )


EMPTY = AccountInfo()


def is_limited(info: AccountInfo, now: datetime, models: list[str] = ()) -> bool:
    """True if a relevant window is maxed (pct >= 100) and not yet reset
    (``resets_at`` in the future, or absent → treated as limited).

    Gates every global window (``model is None``) always, plus each per-model
    window whose ``model`` is in ``models``. ``now`` is tz-aware. Returns False
    on ``EMPTY``/unknown — generalization of claudecode's ``usage_limited``."""
    wanted = set(models or ())
    for w in info.windows:
        if w.model is not None and w.model not in wanted:
            continue                       # per-model window for a model we don't need
        if w.pct < 100:
            continue
        if w.resets_at is None or w.resets_at > now:
            return True
    return False
