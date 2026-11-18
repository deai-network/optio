"""Deliverable metadata: the entries optio pushes with every deliverable.

A deliverable's metadata is a list of ``{key, value, source}`` entries that says
which agent and model delivered the file. The same fact may come from several
sources (the model optio launched and the model the CLI reports), so consumers
pick one with ``meta_get(md, key, prefer=(...))`` and spell keys and sources only
through the constants here. ``snapshot_metadata`` takes the list when the driver
accepts a ``DELIVERABLE:`` line, and ``invoke_on_deliverable`` is the one way optio
hands a deliverable and its metadata to a consumer's ``on_deliverable`` callback.

Design: ``docs/2026-11-18-deliverable-metadata-design.md``.

This module is a leaf: it imports only submodules of ``optio_agents``, never the
package itself, so that ``optio_agents.protocol`` can import it while the
package is still being initialised.
"""

import inspect
import logging
import math
import weakref
from datetime import datetime, timezone
from typing import Any, Awaitable, Callable, Literal, TypedDict

from optio_agents.agent_info import AgentInfo

_LOG = logging.getLogger(__name__)

MetaValue = str | int | float | bool | None
DeliverableMeta = TypedDict("DeliverableMeta", {"key": str, "value": MetaValue, "source": str})
DeliverableMetadata = list[DeliverableMeta]

KEY_ORIGIN = "origin"
KEY_AGENT_TYPE = "agent.type"
KEY_AGENT_NAME = "agent.name"
KEY_AGENT_VERSION = "agent.version"
KEY_AGENT_SESSION = "agent.session"
KEY_PROVIDER = "provider"
KEY_GATEWAY_REGION = "gateway.region"
KEY_MODEL = "model"
KEY_MODEL_LABEL = "model.label"
KEY_EFFORT = "effort"
KEY_DELIVERED_AT = "delivered_at"
KEY_RESUMED = "resumed"

SOURCE_CONFIG = "config"
SOURCE_OPTIO = "optio"
SOURCE_AGENT = "agent"
SOURCE_SEED = "seed"
SOURCE_HARNESS = "harness"

ORIGIN_AGENT = "agent"
ORIGIN_HARNESS = "harness"

MAX_ENTRIES = 32
MAX_VALUE_CHARS = 256
DEFAULT_PREFER = (SOURCE_AGENT, SOURCE_OPTIO, SOURCE_SEED, SOURCE_CONFIG, SOURCE_HARNESS)

MetadataProvider = Callable[[], DeliverableMetadata]
"""A wrapper's entries for one deliverable.

Called with no arguments when the driver accepts a ``DELIVERABLE:`` line; returns the
list. It is synchronous and reads in-memory state only: the driver calls it inline and
nothing can time it out. An awaitable answer (an ``async def`` provider) counts as a
failure.
"""

_SCALARS = (str, int, float, bool)
# BSON (excavator stores the list in MongoDB) has no int wider than 64 bits.
_INT64_MIN, _INT64_MAX = -(2**63), 2**63 - 1


def entry(key: str, value: MetaValue, source: str) -> DeliverableMeta:
    """One metadata entry."""
    return {"key": key, "value": value, "source": source}


def meta_get(
    md: DeliverableMetadata | None, key: str, prefer: tuple[str, ...] = DEFAULT_PREFER
) -> MetaValue:
    """The value of ``key`` from the source that comes earliest in ``prefer``.

    Among entries with the same key and source the first wins. Sources outside
    ``prefer`` are ignored. None when there is no such entry.
    """
    if not md:
        return None
    for source in prefer:
        for item in md:
            if item["key"] == key and item["source"] == source:
                return item["value"]
    return None


def clamp(md: DeliverableMetadata) -> DeliverableMetadata:
    """A bounded copy of ``md`` that holds well-formed scalar entries only.

    Drops anything that is not a dict with a str ``key``, a str ``source`` and a
    scalar ``value``, and a value BSON cannot store (an int outside the signed
    64-bit range, a float that is not finite); keeps only those three fields, in
    new dicts; cuts string values to ``MAX_VALUE_CHARS``; keeps the first
    ``MAX_ENTRIES``. The input is never changed.
    """
    out: DeliverableMetadata = []
    for item in md:
        if len(out) >= MAX_ENTRIES:
            break
        if not isinstance(item, dict) or "value" not in item:
            continue
        key, value, source = item.get("key"), item["value"], item.get("source")
        if not isinstance(key, str) or not isinstance(source, str):
            continue
        if value is not None and not isinstance(value, _SCALARS):
            continue
        if isinstance(value, int) and not isinstance(value, bool) and not _INT64_MIN <= value <= _INT64_MAX:
            continue
        if isinstance(value, float) and not math.isfinite(value):
            continue
        if isinstance(value, str):
            value = value[:MAX_VALUE_CHARS]
        out.append(entry(key, value, source))
    return out


def agent_entries(config: Any, info: AgentInfo) -> DeliverableMetadata:
    """The entries every wrapper adds: the agent type and the agent's name."""
    return [
        entry(KEY_AGENT_TYPE, config.agent_type, SOURCE_CONFIG),
        entry(KEY_AGENT_NAME, info.name, SOURCE_CONFIG),
    ]


def delivery_entries(
    *, origin: str, source: str, resumed: bool | None, now: datetime | None = None
) -> DeliverableMetadata:
    """``origin``, ``delivered_at`` (ISO-8601 UTC, milliseconds) and, when known, ``resumed``."""
    at = (now or datetime.now(timezone.utc)).astimezone(timezone.utc).isoformat(timespec="milliseconds")
    md = [entry(KEY_ORIGIN, origin, source), entry(KEY_DELIVERED_AT, at, source)]
    if resumed is not None:
        md.append(entry(KEY_RESUMED, resumed, source))
    return md


def snapshot_metadata(
    provider: MetadataProvider | None,
    *,
    resumed: bool,
    now: datetime | None = None,
) -> DeliverableMetadata:
    """The metadata of one agent deliverable, taken when the driver accepts its line.

    The driver's own ``origin`` (``agent``), ``delivered_at`` and ``resumed``, source
    ``optio``, come first and the provider's entries follow; the whole list is clamped.
    Synchronous, like the provider, so taking it adds no await between the driver
    accepting the line and queueing the deliverable.

    A provider failure never fails the delivery. These leave the provider's part empty:
    an ``Exception`` raised by the provider, an answer that is not a list, and an
    awaitable answer (closed first when it has ``close``, as a coroutine does, so no
    never-awaited warning appears). Each is logged at WARNING with the type name and the
    keys sent, never the exception's text or any value. ``CancelledError`` propagates.
    """
    own = delivery_entries(origin=ORIGIN_AGENT, source=SOURCE_OPTIO, resumed=resumed, now=now)
    provided: DeliverableMetadata = []
    if provider is not None:
        failure: str | None = None
        try:
            answer: Any = provider()
            if inspect.isawaitable(answer):
                failure = f"answered a {type(answer).__name__}: a provider must be synchronous"
                close = getattr(answer, "close", None)
                if callable(close):
                    close()
            elif not isinstance(answer, list):
                failure = f"answered a {type(answer).__name__}, not a list"
            else:
                provided = answer
        except Exception as exc:  # noqa: BLE001 - a provider failure never fails a delivery
            failure = type(exc).__name__
        if failure is not None:
            _log_provider_failure(failure, own)
    return clamp(own + provided)


def _log_provider_failure(what: str, sent: DeliverableMetadata) -> None:
    """Log a provider failure by type and the keys sent; never a value or an exception's text."""
    _LOG.warning(
        "deliverable metadata provider failed (%s); sending the driver's entries only, keys: %s",
        what,
        ", ".join(item["key"] for item in sent),
    )


_MetadataMode = Literal["positional", "keyword", "none"]
_MODES: "weakref.WeakKeyDictionary[Callable[..., Any], _MetadataMode]" = weakref.WeakKeyDictionary()
_POSITIONAL_KINDS = (inspect.Parameter.POSITIONAL_ONLY, inspect.Parameter.POSITIONAL_OR_KEYWORD)


def _read_metadata_mode(callback: Callable[..., Any]) -> _MetadataMode:
    try:
        params = list(inspect.signature(callback).parameters.values())
    except (ValueError, TypeError):
        return "none"
    kinds = [p.kind for p in params]
    if inspect.Parameter.VAR_POSITIONAL in kinds or sum(k in _POSITIONAL_KINDS for k in kinds) >= 4:
        return "positional"
    if inspect.Parameter.VAR_KEYWORD in kinds or any(
        p.kind is inspect.Parameter.KEYWORD_ONLY and p.name == "metadata" for p in params
    ):
        return "keyword"
    return "none"


def _metadata_mode(callback: Callable[..., Any]) -> _MetadataMode:
    """How ``callback`` takes the metadata, read once per callback.

    The answer is cached weakly by callback; a callable that cannot be weakly
    referenced (or hashed) is inspected on every call.
    """
    try:
        mode = _MODES.get(callback)
    except TypeError:
        return _read_metadata_mode(callback)
    if mode is None:
        mode = _MODES[callback] = _read_metadata_mode(callback)
    return mode


async def invoke_on_deliverable(
    callback: Callable[..., Awaitable[str | None]],
    hook_ctx: Any,
    path: str,
    text: str,
    metadata: DeliverableMetadata,
) -> str | None:
    """Call an ``on_deliverable`` callback; await and return its answer.

    The metadata goes in only when the callback takes it:

    * as a fourth positional argument when it has 4 or more positional
      parameters or ``*args``;
    * else as ``metadata=`` when it has a keyword-only ``metadata`` or
      ``**kwargs``;
    * else not at all: the callback gets ``(hook_ctx, path, text)``, as before
      the metadata existed. So does a callback whose signature cannot be read.
    """
    mode = _metadata_mode(callback)
    if mode == "positional":
        return await callback(hook_ctx, path, text, metadata)
    if mode == "keyword":
        return await callback(hook_ctx, path, text, metadata=metadata)
    return await callback(hook_ctx, path, text)
