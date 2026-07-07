"""Engine-agnostic model-availability probe (+ per-seed cache).

Some agent backends advertise a model catalogue that includes models the
current account/plan cannot actually use, and there is no up-front flag to tell
them apart. The only reliable signal is to *try* each model once. This module
holds everything that is identical across engines; the two engine-specific bits
are injected:

* ``set_model(conversation, model_id)`` — how to make the next turn use a model.
* ``usable_check(answer_text, error) -> bool`` — the engine's usable signal.
  Cursor: the expected answer text is present (a gated model instead replies
  "Upgrade your plan to continue", a normal ``end_turn`` with no error).
  Opencode: the turn produced an answer and did NOT end in an error (a bad
  model — e.g. a model the ChatGPT account can't use — ends the turn with an
  error event instead of an answer).

The result (``{model_id: usable}``) is cached per seed (a plan rarely changes)
so only the first conversation on a seed pays the probe cost. Each engine keeps
a thin wrapper that pins its own cache-collection ``suffix``, disabled ``reason``
string, and the two injected callables — see ``optio_cursor.model_probe`` and
``optio_opencode.model_probe``.
"""
from __future__ import annotations

import asyncio
import inspect
import logging
from datetime import datetime, timedelta, timezone

_LOG = logging.getLogger(__name__)

PROBE_QUESTION = "What is the capital city of Hungary?"
PROBE_CACHE_TTL = timedelta(hours=24)


async def _maybe_await(value):
    if inspect.isawaitable(value):
        return await value
    return value


async def _probe_turn(conversation, question: str, timeout: float, *, error_from_event=None):
    """Send one prompt; return ``(answer_text, error)``.

    ``answer_text`` is the turn's assistant text ("" on timeout / no answer);
    ``error`` is the engine-specific error object when the turn ended in one, else
    None. Resolves as soon as EITHER an assistant message completes (via
    ``on_message``) or — when ``error_from_event`` is supplied and the
    conversation exposes ``on_event`` — an event maps to an error."""
    loop = asyncio.get_event_loop()
    fut: asyncio.Future = loop.create_future()

    def _on_msg(text: str) -> None:
        if not fut.done():
            fut.set_result((text, None))

    unsub_msg = conversation.on_message(_on_msg)
    unsub_evt = None
    if error_from_event is not None and hasattr(conversation, "on_event"):
        def _on_evt(ev) -> None:
            err = error_from_event(ev)
            if err is not None and not fut.done():
                fut.set_result(("", err))
        unsub_evt = conversation.on_event(_on_evt)
    try:
        await conversation.send(question)
        return await asyncio.wait_for(fut, timeout)
    except asyncio.TimeoutError:
        return ("", None)
    finally:
        unsub_msg()
        if unsub_evt is not None:
            unsub_evt()


async def probe_models(
    conversation,
    model_ids: list[str],
    *,
    usable_check,
    set_model,
    question: str = PROBE_QUESTION,
    per_model_timeout: float = 30.0,
    error_from_event=None,
    report=None,
) -> dict[str, bool]:
    """Return ``{model_id: usable}``. For each id: ``set_model`` then ask
    ``question``; ``usable = usable_check(answer_text, error)``. The original
    model is restored afterwards. Never raises — any per-model failure marks the
    model unusable."""
    original = getattr(conversation, "current_model_id", None)
    result: dict[str, bool] = {}
    total = len(model_ids)
    for i, mid in enumerate(model_ids):
        if report is not None:
            report(i + 1, total, mid)
        try:
            await _maybe_await(set_model(conversation, mid))
            answer, error = await _probe_turn(
                conversation, question, per_model_timeout,
                error_from_event=error_from_event,
            )
            result[mid] = bool(usable_check(answer, error))
        except Exception:  # noqa: BLE001 — a probe failure just disables the model
            _LOG.exception("model probe failed for %r", mid)
            result[mid] = False
    if original is not None:
        try:
            await _maybe_await(set_model(conversation, original))
        except Exception:  # noqa: BLE001
            _LOG.exception("restoring model %r after probe failed", original)
    return result


def probe_cache_key(resolved_seed_id, config_seed_id):
    """Stable per-seed cache key that survives resume.

    A fresh seeded launch sets ``resolved_seed_id`` (the merged/leased seed); a
    RESUMED session skips the merge, leaving it ``None``. Fall back to the
    config's string ``seed_id`` so the cache still hits/saves across resumes. A
    pooled (callable) seed has no stable key on resume → ``None`` (probe again)."""
    if resolved_seed_id is not None:
        return resolved_seed_id
    if isinstance(config_seed_id, str):
        return config_seed_id
    return None


def apply_probe(models: list[dict], usable: dict[str, bool], *, reason: str) -> list[dict]:
    """Return ``models`` with ``disabled=True`` + a ``disabledReason`` on any id
    the probe found unusable (a server-side model control surfaces the reason).
    Ids absent from ``usable`` are left as-is (not probed → unchanged)."""
    out = []
    for m in models:
        mid = m.get("id")
        if mid in usable and not usable[mid]:
            m = {**m, "disabled": True, "disabledReason": reason}
        elif mid in usable:
            m = {k: v for k, v in m.items() if k != "disabledReason"}
            m["disabled"] = False
        out.append(m)
    return out


def disabled_map(usable: dict[str, bool], reason: str) -> dict[str, str]:
    """Return ``{model_id: reason}`` for every id the probe found unusable — the
    shape a client-fetched picker consumes from widgetData to grey the model
    out with a hover explanation."""
    return {mid: reason for mid, u in usable.items() if not u}


async def load_probe_cache(
    db, prefix: str, seed_id: str, *, suffix: str, now: datetime | None = None,
) -> dict[str, bool] | None:
    """Return the cached ``{model_id: usable}`` map for ``seed_id`` when present
    and within TTL, else None. ``suffix`` names the per-engine cache collection
    (``{prefix}{suffix}``)."""
    now = now or datetime.now(timezone.utc)
    doc = await db[f"{prefix}{suffix}"].find_one({"_id": seed_id})
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
    *, suffix: str, now: datetime | None = None,
) -> None:
    """Upsert the probe result for ``seed_id`` with a fresh timestamp into the
    per-engine cache collection (``{prefix}{suffix}``)."""
    now = now or datetime.now(timezone.utc)
    await db[f"{prefix}{suffix}"].update_one(
        {"_id": seed_id},
        {"$set": {"usable": usable, "probedAt": now}},
        upsert=True,
    )
