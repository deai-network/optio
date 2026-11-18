"""Deliverable metadata: the entries optio pushes with every deliverable.

A deliverable's metadata is a list of ``{key, value, source}`` entries that says
which agent and model delivered the file. The same fact may come from several
sources (the model optio launched and the model the CLI reports), so consumers
pick one with ``meta_get(md, key, prefer=(...))`` and spell keys and sources only
through the constants here.

Design: ``docs/2026-11-18-deliverable-metadata-design.md``.

This module is a leaf: it imports only submodules of ``optio_agents``, never the
package itself, so that ``optio_agents.protocol`` can import it while the
package is still being initialised.
"""

from datetime import datetime, timezone
from typing import Any, TypedDict

from optio_agents.agent_info import AgentInfo

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

_SCALARS = (str, int, float, bool)


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
    scalar ``value``; keeps only those three fields, in new dicts; cuts string
    values to ``MAX_VALUE_CHARS``; keeps the first ``MAX_ENTRIES``. The input is
    never changed.
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
