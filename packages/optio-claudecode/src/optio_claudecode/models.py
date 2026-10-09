"""Claude Code's model list for the conversation widget, from the CLI itself.

The stream-json control request ``initialize`` answers with the list Claude
Code's own /model picker (and claude-agent-acp) shows: aliases such as
``default``, ``opus[1m]``, ``sonnet``, ``haiku``, each with a display name, a
description, the full model id it resolves to today (``resolvedModel``) and its
effort levels. ``--model`` takes these aliases (and full ids); an alias always
runs the newest model it names. A launch value that is not one of them (a
pinned full id) comes back in the list as an entry of its own.

Best-effort: when the CLI cannot tell (an error answer, no models, the process
ending first), the picker falls back to the bare aliases.
"""
from __future__ import annotations

import asyncio
import logging
import re

_LOG = logging.getLogger(__name__)

# Graded reasoning-effort levels (ordered low→max) claude's `--effort` flag
# accepts (config validation). Each model lists the ones it supports; the
# live control (id="reasoning_effort") is a slider over those.
EFFORT_LEVELS = ["low", "medium", "high", "xhigh", "max"]

# Default effort preselected on the slider when the caller sets no
# reasoning_effort (mid-high, matching Claude Code's own default posture).
DEFAULT_EFFORT = "high"

# The alias the model select shows when nothing is configured or picked.
DEFAULT_MODEL = "default"

# How long the session waits for the CLI's initialize answer before falling
# back to the bare aliases (the CLI answers at once; this only bounds a hung
# process).
INITIALIZE_TIMEOUT_S = 30.0

# The picker when the CLI cannot list its models: the aliases, no descriptions.
_FALLBACK = [("default", "Default"), ("opus", "Opus"), ("sonnet", "Sonnet"), ("haiku", "Haiku")]


def fallback_models() -> list[dict]:
    """The bare aliases (a fresh list each call)."""
    return [{"id": value, "label": label} for value, label in _FALLBACK]


def parse_cli_models(models) -> list[dict]:
    """The CLI's ``initialize`` ``models`` as catalog entries, in its order:
    ``{id: value, label: displayName, description?, resolved?: resolvedModel,
    effort?: supportedEffortLevels}``. ``effort`` only when the model
    ``supportsEffort`` with a non-empty level list. Entries without a value are
    skipped; ``[]`` when nothing usable is left."""
    out: list[dict] = []
    for m in models if isinstance(models, list) else []:
        if not isinstance(m, dict):
            continue
        value = m.get("value")
        if not isinstance(value, str) or not value:
            continue
        entry: dict = {"id": value, "label": m.get("displayName") or value}
        if isinstance(m.get("description"), str):
            entry["description"] = m["description"]
        if isinstance(m.get("resolvedModel"), str):
            entry["resolved"] = m["resolvedModel"]
        levels = m.get("supportedEffortLevels")
        if m.get("supportsEffort") and isinstance(levels, list) and levels:
            entry["effort"] = [lvl for lvl in levels if isinstance(lvl, str)]
        out.append(entry)
    return out


async def fetch_cli_models(conversation, *, timeout_s: float = INITIALIZE_TIMEOUT_S) -> list[dict] | None:
    """The running CLI's model list (``parse_cli_models`` of its initialize
    answer), or None when it cannot tell: an error answer, no models, no
    answer in ``timeout_s``, or the process gone. Never raises."""
    try:
        info = await asyncio.wait_for(conversation.initialize(), timeout_s)
    except Exception:  # noqa: BLE001 — best effort: the caller falls back
        _LOG.info("model list: claude initialize failed; using the bare aliases", exc_info=True)
        return None
    return parse_cli_models((info or {}).get("models")) or None


def launch_model(configured: str | None, *, continuing: bool) -> str | None:
    """``--model`` for a launch. The configured model when there is one. A
    continued session with none passes ``default``: ``--continue`` alone keeps
    the full id the transcript ended on, while an alias runs the newest model
    it names. A fresh launch with none passes nothing (claude's own default)."""
    if configured:
        return configured
    return DEFAULT_MODEL if continuing else None


def _by_id(catalog: list[dict], value: str) -> dict | None:
    return next((m for m in catalog if m.get("id") == value), None)


def _resolving_to(catalog: list[dict], model: str) -> dict | None:
    """The entry resolving to ``model``: the model's own alias before
    ``default``, which only names today's default."""
    found = [m for m in catalog if m.get("resolved") == model]
    return next((m for m in found if m["id"] != DEFAULT_MODEL), found[0] if found else None)


def shown_model(catalog: list[dict], *, picked: str | None, runtime: str | None) -> str:
    """The value the model select shows.

    The picked value (configured, or the operator's pick), else ``default``. A
    full id an alias resolves to shows as that alias (its own alias before
    ``default``). The model the running claude reports (``runtime``, a full id from
    system/init) moves the value only when the shown entry is known to resolve
    to something else: then it shows the alias resolving to the runtime model,
    else the runtime id itself. The result may be missing from the catalog (see
    ``catalog_with``)."""
    value = picked or DEFAULT_MODEL
    entry = _by_id(catalog, value) or _resolving_to(catalog, value)
    if entry is not None:
        value = entry["id"]
    resolved = (entry or {}).get("resolved")
    if runtime and resolved is not None and resolved != runtime:
        other = _resolving_to(catalog, runtime)
        value = other["id"] if other is not None else runtime
    return value


def catalog_with(catalog: list[dict], value: str) -> list[dict]:
    """The catalog, plus a bare ``{id, label}`` entry for ``value`` when it
    lists no such id (so the select can show it)."""
    if _by_id(catalog, value) is not None:
        return catalog
    return [*catalog, {"id": value, "label": value}]


def model_effort(value: str, catalog: list[dict]) -> tuple[list[str] | None, str | None]:
    """``(levels, default)`` for the catalog entry ``value``: a fresh copy of
    its effort levels and the preselected one (``DEFAULT_EFFORT`` when listed,
    else the highest); ``(None, None)`` when the entry is missing or lists no
    effort, so the caller omits the slider."""
    levels = (_by_id(catalog, value) or {}).get("effort")
    if not levels:
        return (None, None)
    default = DEFAULT_EFFORT if DEFAULT_EFFORT in levels else levels[-1]
    return (list(levels), default)


# claude-<family>-<version>[-<yyyymmdd>]. The version is non-greedy so it stops
# before a date: claude-haiku-4-5-20251001 is version 4.5, dated (a greedy
# version 4.5.20251001 would sort newer than the undated claude-haiku-4-5).
_ID_RE = re.compile(r"^claude-([a-z]+)-(\d+(?:-\d+)*?)(?:-(\d{8}))?$")
# A bare family alias (opus, sonnet, haiku, ...), its [variant] split off.
_ALIAS_RE = re.compile(r"^[a-z]+$")


def _split_variant(model: str) -> tuple[str, str]:
    """``("claude-opus-5", "[1m]")`` for ``claude-opus-5[1m]``; ``(model, "")``
    without a variant."""
    base, bracket, rest = model.partition("[")
    return base, bracket + rest


def _family_version(model_id: str) -> tuple[str, tuple[int, ...], bool] | None:
    """(family, version, undated) of a ``claude-<family>-<version>`` id, else
    None."""
    m = _ID_RE.match(model_id)
    if m is None:
        return None
    return m.group(1), tuple(int(x) for x in m.group(2).split("-")), m.group(3) is None


def restore_model(catalog: list[dict], *, saved: str, resolved: str | None) -> str | None:
    """The model a resumed session runs for the operator's saved pick
    (``saved``, the select's value then; ``resolved``, the full id it ran as),
    against the CLI's list now.

    ``saved`` while the list still offers it: an entry of that value, or one
    resolving to it. The CLI's echo of a launch value it does not know (an
    entry whose resolvedModel is the value itself) does not count. Otherwise
    the newest entry of the same family (``claude-<family>-<version>`` of
    ``resolved``, else of ``saved``, else the alias name), preferring one
    with the same ``[variant]``; never ``default``, whose model changes under
    it. None when no entry is of that family (the caller falls back to the
    configured model)."""
    own = [m for m in catalog if m.get("resolved") != m.get("id")]
    if _by_id(own, saved) is not None or _resolving_to(own, saved) is not None:
        return saved
    source = resolved or saved
    base, variant = _split_variant(source)
    parsed = _family_version(base)
    if parsed is not None:
        family = parsed[0]
    else:
        alias = _split_variant(saved)[0]
        family = alias if _ALIAS_RE.match(alias) and alias != DEFAULT_MODEL else None
    if family is None:
        return None
    best_key: tuple | None = None
    best: str | None = None
    for m in own:
        if m["id"] == DEFAULT_MODEL or not isinstance(m.get("resolved"), str):
            continue
        r_base, r_variant = _split_variant(m["resolved"])
        fv = _family_version(r_base)
        if fv is None or fv[0] != family:
            continue
        key = (r_variant == variant, fv[1], fv[2])
        if best_key is None or key > best_key:
            best_key, best = key, m["id"]
    return best
