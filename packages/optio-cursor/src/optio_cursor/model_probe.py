"""Conversation-mode model availability probe (+ per-seed cache).

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
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

_LOG = logging.getLogger(__name__)

PROBE_QUESTION = "What is the capital city of Hungary?"
PROBE_EXPECT = "budapest"
PROBE_CACHE_SUFFIX = "_cursor_model_probe"
PROBE_CACHE_TTL = timedelta(hours=24)


async def _probe_turn(conversation, question: str, timeout: float) -> str:
    """Send one prompt and return the turn's full assistant text (or "" on
    timeout). Uses the conversation's turn-completion fan-out (on_message)."""
    import asyncio

    loop = asyncio.get_event_loop()
    fut: asyncio.Future = loop.create_future()

    def _on_msg(text: str) -> None:
        if not fut.done():
            fut.set_result(text)

    unsub = conversation.on_message(_on_msg)
    try:
        await conversation.send(question)
        return await asyncio.wait_for(fut, timeout)
    except asyncio.TimeoutError:
        return ""
    finally:
        unsub()


async def probe_models(
    conversation,
    model_ids: list[str],
    *,
    question: str = PROBE_QUESTION,
    expect: str = PROBE_EXPECT,
    per_model_timeout: float = 30.0,
    report=None,
) -> dict[str, bool]:
    """Return ``{model_id: usable}``. For each id: ``set_model`` then ask
    ``question``; usable iff ``expect`` (case-insensitive) appears in the answer
    (a plan-gated model replies "Upgrade your plan to continue"). The original
    model is restored afterwards. Never raises — any failure marks the model
    unusable."""
    original = conversation.current_model_id
    result: dict[str, bool] = {}
    total = len(model_ids)
    for i, mid in enumerate(model_ids):
        if report is not None:
            report(i + 1, total, mid)
        try:
            await conversation.set_active_model(mid)
            answer = await _probe_turn(conversation, question, per_model_timeout)
            result[mid] = expect.lower() in (answer or "").lower()
        except Exception:  # noqa: BLE001 — a probe failure just disables the model
            _LOG.exception("model probe failed for %r", mid)
            result[mid] = False
    if original is not None:
        try:
            await conversation.set_active_model(original)
        except Exception:  # noqa: BLE001
            _LOG.exception("restoring model %r after probe failed", original)
    return result


def apply_probe(models: list[dict], usable: dict[str, bool]) -> list[dict]:
    """Return ``models`` with ``disabled=True`` on any id the probe found
    unusable. Ids absent from ``usable`` are left as-is (not probed → unchanged)."""
    out = []
    for m in models:
        mid = m.get("id")
        if mid in usable and not usable[mid]:
            m = {**m, "disabled": True}
        elif mid in usable:
            m = {**m, "disabled": False}
        out.append(m)
    return out


async def load_probe_cache(
    db, prefix: str, seed_id: str, *, now: datetime | None = None,
) -> dict[str, bool] | None:
    """Return the cached ``{model_id: usable}`` map for ``seed_id`` when present
    and within TTL, else None."""
    now = now or datetime.now(timezone.utc)
    doc = await db[f"{prefix}{PROBE_CACHE_SUFFIX}"].find_one({"_id": seed_id})
    if not doc:
        return None
    probed_at = doc.get("probedAt")
    if not isinstance(probed_at, datetime):
        return None
    if probed_at.tzinfo is None:
        probed_at = probed_at.replace(tzinfo=timezone.utc)
    if now - probed_at > PROBE_CACHE_TTL:
        return None
    usable = doc.get("usable")
    return usable if isinstance(usable, dict) else None


async def save_probe_cache(
    db, prefix: str, seed_id: str, usable: dict[str, bool],
    *, now: datetime | None = None,
) -> None:
    """Upsert the probe result for ``seed_id`` with a fresh timestamp."""
    now = now or datetime.now(timezone.utc)
    await db[f"{prefix}{PROBE_CACHE_SUFFIX}"].update_one(
        {"_id": seed_id},
        {"$set": {"usable": usable, "probedAt": now}},
        upsert=True,
    )
