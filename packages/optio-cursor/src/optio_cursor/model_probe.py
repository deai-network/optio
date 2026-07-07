"""Cursor's conversation-mode model-availability probe (+ per-seed cache).

Cursor advertises its FULL model catalogue in the ACP ``session/new`` block with
no plan/entitlement flag (verified against the live binary: no ``_meta``, each
entry is just ``{modelId, name}``). Selecting a model the account cannot use is
not rejected — cursor accepts ``session/set_model`` and then answers the turn
with a plain ``Upgrade your plan to continue`` message (normal ``end_turn``, no
error). There is no way to know a model's availability up front.

So we probe: at conversation start, before the widget is shown, send each model a
trivial question and keep the ones that actually answer. The result is cached per
seed (a plan rarely changes) so only the first conversation on a seed pays the
cost.

The mechanics (cache, disabled-reason surfacing, the generic probe loop) live in
the engine-agnostic ``optio_agents.model_probe`` — shared with optio-opencode.
This module pins cursor's usable signal (expected answer text present), its
cache-collection suffix, and its plan-gate reason string.
"""
from __future__ import annotations

from optio_agents import model_probe as _shared

PROBE_QUESTION = _shared.PROBE_QUESTION
PROBE_EXPECT = "budapest"
PROBE_CACHE_SUFFIX = "_cursor_model_probe"
PROBE_CACHE_TTL = _shared.PROBE_CACHE_TTL
# Shown in the picker (as a tooltip) for a model the probe found unusable —
# cursor plan-gates it. Follows excavator's decision/reason pattern: the disabled
# state carries a human reason.
DISABLED_REASON = "Not enabled in your Cursor subscription plan"

probe_cache_key = _shared.probe_cache_key


async def probe_models(
    conversation,
    model_ids: list[str],
    *,
    question: str = PROBE_QUESTION,
    expect: str = PROBE_EXPECT,
    per_model_timeout: float = 30.0,
    report=None,
) -> dict[str, bool]:
    """Return ``{model_id: usable}``. For each id: set the model then ask
    ``question``; usable iff ``expect`` (case-insensitive) appears in the answer
    (a plan-gated model replies "Upgrade your plan to continue")."""
    def _usable(answer, _error):
        return expect.lower() in (answer or "").lower()

    return await _shared.probe_models(
        conversation, model_ids,
        usable_check=_usable,
        set_model=lambda conv, mid: conv.set_active_model(mid),
        question=question,
        per_model_timeout=per_model_timeout,
        report=report,
    )


def apply_probe(models: list[dict], usable: dict[str, bool]) -> list[dict]:
    """Return ``models`` with ``disabled=True`` + a ``disabledReason`` on any id
    the probe found unusable (the picker surfaces the reason)."""
    return _shared.apply_probe(models, usable, reason=DISABLED_REASON)


async def load_probe_cache(db, prefix: str, seed_id: str, *, now=None):
    return await _shared.load_probe_cache(
        db, prefix, seed_id, suffix=PROBE_CACHE_SUFFIX, now=now,
    )


async def save_probe_cache(db, prefix: str, seed_id: str, usable: dict[str, bool], *, now=None):
    return await _shared.save_probe_cache(
        db, prefix, seed_id, usable, suffix=PROBE_CACHE_SUFFIX, now=now,
    )
