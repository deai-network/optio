"""The operator's session-control picks, kept across an optio resume.

A pick made in the conversation widget (the model, the reasoning effort, the
permission mode) lasts for the session; this file carries it into a resumed
one. It lives in ``home/.claude`` (``PICKS_RELPATH``), so it travels in the
session blob next to the transcript, encrypted with it. Only an optio resume
reads it back (not ``session_restore_from`` or a seed), and the session checks
each pick against what the resumed run offers: a permission mode this task
does not offer is dropped (``restorable_permission_mode``), a model the CLI no
longer lists moves to its family (``models.restore_model``), an effort level
the model lacks gives way to the model's default.
"""
from __future__ import annotations

import json
import logging
from dataclasses import asdict, dataclass, fields

_LOG = logging.getLogger(__name__)

PICKS_RELPATH = "home/.claude/optio-picks.json"


@dataclass(frozen=True)
class Picks:
    """``model``: the model select's value as picked (an alias, or a full
    id); ``model_resolved``: the full id it resolved to then (the CLI's
    resolvedModel), for the family match; ``effort`` and
    ``permission_mode``: the picked level and mode. None: not picked."""
    model: str | None = None
    model_resolved: str | None = None
    effort: str | None = None
    permission_mode: str | None = None


def dumps(picks: Picks) -> str:
    return json.dumps({k: v for k, v in asdict(picks).items() if v is not None})


def loads(text: str) -> Picks | None:
    """The picks in ``text``, or None when it is not a JSON object. A value
    that is not a non-empty string is dropped; unknown keys are ignored."""
    try:
        raw = json.loads(text)
    except ValueError:
        return None
    if not isinstance(raw, dict):
        return None
    return Picks(**{f.name: raw[f.name] for f in fields(Picks)
                    if isinstance(raw.get(f.name), str) and raw[f.name]})


def restorable_permission_mode(saved: str | None, offered: list[str]) -> str | None:
    """``saved`` when this task offers it (the permission_mode control's
    settable modes), else None."""
    return saved if saved is not None and saved in offered else None


async def save(host, picks: Picks) -> None:
    """Write the picks file. Best effort: a failure is logged, never raised
    (a pick lost across a resume is not worth ending the session for)."""
    try:
        await host.write_text(PICKS_RELPATH, dumps(picks))
    except Exception:  # noqa: BLE001
        _LOG.warning("could not save the session-control picks", exc_info=True)


async def load(host) -> Picks | None:
    """The picks file of a restored session, or None (none saved, unreadable)."""
    try:
        raw = await host.fetch_bytes_from_host(f"{host.workdir.rstrip('/')}/{PICKS_RELPATH}")
    except Exception:  # noqa: BLE001 — usually: nothing was picked
        return None
    return loads(raw.decode("utf-8", "replace"))
