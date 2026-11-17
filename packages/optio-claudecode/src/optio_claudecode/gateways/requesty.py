"""The Requesty provider: list an account's models, build a new gateway seed.

`list_models` sends GET `{base}/v1/models` (the account's pinned models and
its policies) and GET `{base}/v1/models/managed` (the bare managed names),
both with `Authorization: Bearer <token>`, and merges the two lists into one
in three groups:

- `managed`: every entry of the managed list, and a `/v1/models` id without
  `/` (Requesty picks the provider);
- `pinned`: a `/v1/models` id `provider/model`;
- `policy`: a `/v1/models` id `policy/<name>`.

A duplicate id keeps its first entry (`/v1/models` comes first). An id ending
in `@eu` is listed for region `eu` only. Effort levels are the entry's
`reasoning.supported_efforts` that Claude Code knows (`models.EFFORT_LEVELS`,
in that order); an entry without any borrows them from a non-policy entry with
the same `model_canonical_name`. Policies get none: their feature flags are
placeholders.

The token goes into the request header only: no error message, log line or
repr carries it. Redirects are not followed, so it never reaches another URL.
"""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass
from typing import Literal, Sequence

import aiohttp

from optio_claudecode.gateway import (
    GatewayAuthError,
    GatewayEmptyList,
    GatewayError,
    GatewayModel,
    GatewayPreset,
    GatewaySeed,
    GatewayUnreachable,
)
from optio_claudecode.models import EFFORT_LEVELS

PROVIDER = "requesty"
REGIONS: dict[str, str] = {
    "global": "https://router.requesty.ai",
    "eu": "https://router.eu.requesty.ai",
}

_MODELS_PATH = "/v1/models"
_MANAGED_PATH = "/v1/models/managed"
_GROUP_ORDER = ("managed", "pinned", "policy")

Group = Literal["managed", "pinned", "policy"]


@dataclass(frozen=True)
class ModelChoice:
    """One model the account allows. `label` is the id. `efforts` are the
    Claude Code levels the model publishes (or borrows), None for none.
    `eu_only` marks an `@eu` name, which is listed for region `eu` only."""

    id: str
    label: str
    group: Group
    efforts: tuple[str, ...] | None
    eu_only: bool


def _region_url(region: str) -> str:
    try:
        return REGIONS[region]
    except KeyError:
        raise ValueError(f"unknown Requesty region {region!r}; known: {', '.join(REGIONS)}") from None


async def _entries(session: aiohttp.ClientSession, base: str, path: str, token: str) -> list[dict]:
    """The entries one endpoint lists. Every error is raised from None: the
    aiohttp exception it replaces is never chained into a traceback.
    Redirects are not followed, so the token never goes to another URL: a 3xx
    is an unexpected status like any other (its Location is not named)."""
    try:
        async with session.get(
            base + path, headers={"Authorization": f"Bearer {token}"}, allow_redirects=False,
        ) as resp:
            status = resp.status
            body = await resp.read()
    except asyncio.TimeoutError:
        raise GatewayUnreachable(f"GET {path}: timed out") from None
    except aiohttp.ClientError:
        raise GatewayUnreachable(f"GET {path}: connection failed") from None
    if status in (401, 403):
        raise GatewayAuthError(f"GET {path}: HTTP {status}") from None
    if status >= 500:
        raise GatewayUnreachable(f"GET {path}: HTTP {status}") from None
    if not 200 <= status < 300:
        raise GatewayError(f"GET {path}: HTTP {status}") from None
    try:
        data = json.loads(body)
    except ValueError:
        raise GatewayError(f"GET {path}: HTTP {status}, the body is not JSON") from None
    if isinstance(data, dict):
        data = data.get("data")
    if not isinstance(data, list) or not all(
        isinstance(e, dict) and isinstance(e.get("id"), str) and e["id"] for e in data
    ):
        raise GatewayError(f"GET {path}: HTTP {status}, unexpected body shape") from None
    return data


def _models_group(model_id: str) -> Group:
    if model_id.startswith("policy/"):
        return "policy"
    return "pinned" if "/" in model_id else "managed"


def _canonical(entry: dict) -> str | None:
    name = entry.get("model_canonical_name")
    return name if isinstance(name, str) else None


def _own_efforts(entry: dict) -> tuple[str, ...] | None:
    reasoning = entry.get("reasoning")
    levels = reasoning.get("supported_efforts") if isinstance(reasoning, dict) else None
    if not isinstance(levels, list):
        return None
    return tuple(level for level in EFFORT_LEVELS if level in levels) or None


def _merge(models: list[dict], managed: list[dict], region: str) -> list[ModelChoice]:
    tagged = [(e, _models_group(e["id"])) for e in models] + [(e, "managed") for e in managed]
    lenders: dict[str, tuple[str, ...]] = {}
    for entry, group in tagged:
        own, canonical = _own_efforts(entry), _canonical(entry)
        if group != "policy" and own and canonical is not None:
            lenders.setdefault(canonical, own)
    seen: set[str] = set()
    choices: list[ModelChoice] = []
    for entry, group in tagged:
        model_id = entry["id"]
        if model_id in seen:
            continue
        seen.add(model_id)
        eu_only = model_id.endswith("@eu")
        if eu_only and region != "eu":
            continue
        if group == "policy":
            efforts = None
        else:
            efforts = _own_efforts(entry) or lenders.get(_canonical(entry))
        choices.append(ModelChoice(model_id, model_id, group, efforts, eu_only))
    choices.sort(key=lambda c: (_GROUP_ORDER.index(c.group), c.id))
    return choices


async def list_models(
    token: str, region: str, *, timeout: float = 15.0, base_url: str | None = None,
) -> list[ModelChoice]:
    """The models the account allows: managed, then pinned, then policy, each
    sorted by id. `base_url` replaces the region's URL (tests); the region
    still decides the `@eu` names.

    Raises ValueError for an unknown region, GatewayAuthError when the key is
    rejected (HTTP 401 / 403), GatewayUnreachable on a connection error, a
    timeout (`timeout` seconds per request) or HTTP 5xx, GatewayEmptyList when
    the merged list is empty, and a plain GatewayError for any other answer (an
    unexpected status such as 429 or a redirect, which is never followed, a
    body that is not JSON or not a list of entries with an id)."""
    region_url = _region_url(region)  # checked even when base_url replaces it
    base = (base_url or region_url).rstrip("/")
    async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=timeout)) as session:
        models = await _entries(session, base, _MODELS_PATH, token)
        managed = await _entries(session, base, _MANAGED_PATH, token)
    choices = _merge(models, managed, region)
    if not choices:
        raise GatewayEmptyList("the account allows no models")
    return choices


def new_seed(
    *,
    token: str,
    region: str,
    models: Sequence[GatewayModel],
    default_model: str,
    presets: dict[str, GatewayPreset] | None = None,
) -> GatewaySeed:
    """A Requesty gateway seed for `gateway.write_gateway_seed`: provider
    `PROVIDER`, base URL the region's. Raises ValueError for an unknown
    region."""
    return GatewaySeed(
        provider=PROVIDER,
        region=region,
        base_url=_region_url(region),
        token=token,
        models=tuple(models),
        default_model=default_model,
        presets=presets,
    )
