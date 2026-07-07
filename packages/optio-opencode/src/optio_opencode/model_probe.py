"""opencode's conversation-mode model-availability probe (+ per-seed cache).

opencode's ``GET /config/providers`` lists every configured provider/model with
no per-account entitlement flag. Selecting a model the account cannot use is not
rejected up front — the turn simply ERRORS (e.g. a ChatGPT-account Codex auth
rejects ``gpt-5.5-pro`` with ``Bad Request: … not supported when using Codex with
a ChatGPT account``). So we probe: send each model a trivial question and keep the
ones that answer without erroring. The result is cached per seed.

The mechanics (cache, disabled-map surfacing, the generic probe loop) live in the
engine-agnostic ``optio_agents.model_probe`` — shared with optio-cursor. This
module pins opencode's usable signal (an answer arrived and the turn did NOT
error), its ``/config/providers`` id enumeration, its cache-collection suffix, and
its disabled-reason string.
"""
from __future__ import annotations

from optio_agents import model_probe as _shared

PROBE_QUESTION = _shared.PROBE_QUESTION
PROBE_CACHE_SUFFIX = "_opencode_model_probe"
PROBE_CACHE_TTL = _shared.PROBE_CACHE_TTL
# Surfaced in the picker (as a tooltip) for a model the probe found unusable —
# the account/plan can't run it (the provider rejected the probe turn).
DISABLED_REASON = "Not usable with this account (the provider rejected it)"

probe_cache_key = _shared.probe_cache_key
run_probe_child = _shared.run_probe_child


def _error_from_event(ev: dict):
    """Map one raw conversation event to an error object, or None. opencode
    surfaces a failed turn either as a ``session.error`` event or as a completed
    assistant ``message.updated`` whose ``info`` carries an ``error`` field."""
    t = ev.get("type")
    props = ev.get("properties") or {}
    if t == "session.error":
        return props.get("error") or {"message": "session error"}
    if t == "message.updated":
        info = props.get("info") or {}
        if info.get("role") == "assistant" and info.get("error"):
            return info.get("error")
    return None


def _usable(answer, error) -> bool:
    """A model is usable iff the probe turn produced an answer AND did not end in
    an error (a timeout leaves answer="" and error=None → unusable)."""
    return error is None and bool((answer or "").strip())


async def probe_models(conversation, model_ids, *, per_model_timeout: float = 30.0, report=None):
    """Return ``{model_id: usable}`` for opencode's error-signal usable-check."""
    return await _shared.probe_models(
        conversation, model_ids,
        usable_check=_usable,
        set_model=lambda conv, mid: conv.set_active_model(mid),
        error_from_event=_error_from_event,
        per_model_timeout=per_model_timeout,
        report=report,
    )


def parse_model_ids(providers_json) -> list[str]:
    """Enumerate ``"providerID/modelID"`` ids from a ``GET /config/providers``
    response (Python peer of the UI's ``parseProviders``). Shape:
    ``{providers: [{id, name, models: {<modelId>: {id, providerID, name}}}], …}``."""
    if not isinstance(providers_json, dict):
        return []
    providers = providers_json.get("providers")
    if not isinstance(providers, list):
        return []
    ids: list[str] = []
    for p in providers:
        if not isinstance(p, dict):
            continue
        pid = p.get("id")
        models = p.get("models")
        if not isinstance(models, dict):
            continue
        for m in models.values():
            m = m if isinstance(m, dict) else {}
            prov = m.get("providerID") or pid
            mod = m.get("id")
            if prov and mod:
                ids.append(f"{prov}/{mod}")
    return ids


def parse_model_variants(providers_json) -> dict[str, list[str]]:
    """Map ``"providerID/modelID" -> [ordered variant keys]`` for every model
    that declares a non-empty ``variants`` map in a ``GET /config/providers``
    response. opencode's per-model ``variants`` are named reasoning-effort
    presets (e.g. ``{"low": …, "medium": …, "high": …}``); their KEYS are the
    graded effort levels the widget builds its effort slider from.

    ``parse_model_ids`` (the probe's id enumeration) discards ``variants``; this
    sibling reads the keys the probe throws away. Models with no ``variants``
    map are omitted entirely — an unsupported model ⇒ no effort control."""
    if not isinstance(providers_json, dict):
        return {}
    providers = providers_json.get("providers")
    if not isinstance(providers, list):
        return {}
    out: dict[str, list[str]] = {}
    for p in providers:
        if not isinstance(p, dict):
            continue
        pid = p.get("id")
        models = p.get("models")
        if not isinstance(models, dict):
            continue
        for m in models.values():
            m = m if isinstance(m, dict) else {}
            prov = m.get("providerID") or pid
            mod = m.get("id")
            variants = m.get("variants")
            if not (prov and mod and isinstance(variants, dict) and variants):
                continue
            keys = [str(k) for k in variants.keys()]
            if keys:
                out[f"{prov}/{mod}"] = keys
    return out


def disabled_map(usable: dict[str, bool]) -> dict[str, str]:
    """``{model_id: DISABLED_REASON}`` for every unusable id — published in
    widgetData so OpencodeView greys those models in its client-fetched picker."""
    return _shared.disabled_map(usable, DISABLED_REASON)


async def load_probe_cache(db, prefix: str, seed_id: str, *, now=None):
    return await _shared.load_probe_cache(
        db, prefix, seed_id, suffix=PROBE_CACHE_SUFFIX, now=now,
    )


async def save_probe_cache(db, prefix: str, seed_id: str, usable: dict[str, bool], *, now=None):
    return await _shared.save_probe_cache(
        db, prefix, seed_id, usable, suffix=PROBE_CACHE_SUFFIX, now=now,
    )
