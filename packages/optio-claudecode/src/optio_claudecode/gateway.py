"""Gateway seeds: Claude Code through an LLM gateway (Requesty first).

A gateway seed is a document in `{prefix}{suffix}` (`REQUESTY_SEED_SUFFIX`
for Requesty) whose GridFS blob is one versioned JSON document, encrypted with
the consumer's seed cipher (`encrypt` None stores plaintext):

    {"version": 1, "provider", "region", "baseUrl", "token",
     "models": [{"id", "label", "group", "efforts": [..] | null,
                 "status", "reason"}],
     "defaultModel",
     "presets": {"fast" | "extensive": {"model", "effort"}} | null,
     "testedAt": ISO-UTC | null}

The document holds the non-secret part, so a consumer lists and labels the
seed without decrypting anything:

    {_id, createdAt, blobId, status: "pending" | "alive" | "dead", updatedAt?,
     metadata: {gateway: {"provider", "region", "keyHint": token[-4:],
                          "defaultModel",
                          "models": [{"id", "label", "status", "reason"}],
                          "presets": {...} | None, "testedAt": datetime | None}}}

`write_gateway_seed` sets `status` in the insert itself, so the document never
exists without one; `seeds.acquire` skips "pending" and "dead". A gateway seed
is never leased: consumers pass its id as a plain string. `seeds.purge_seed`
removes it (document and blob) like any other seed.

The token lives only in the encrypted blob: never in the document, a repr, or
an error message.

At launch a seed gives the session its catalog (`gateway_catalog`), the
child's non-secret environment (`gateway_launch_env`), `--model`
(`launch_model`) and `--effort` (`launch_effort`). The token is not part of
that environment: the launch command reads `GATEWAY_SECRET_ENV` from its first
stdin line (`host_actions.with_stdin_secrets`, `write_stdin_secrets`), so it
reaches the child's environment without ever being on a command line.
"""

from __future__ import annotations

import io
import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Callable, Literal

from gridfs.errors import NoFile
from motor.motor_asyncio import AsyncIOMotorDatabase, AsyncIOMotorGridFSBucket

from optio_agents import seeds

from optio_claudecode import models as cc_models

GATEWAY_SEED_VERSION = 1
_PRESET_KEYS = ("fast", "extensive")
_MALFORMED = object()

SeedStatus = Literal["pending", "alive", "dead"]


class GatewayError(Exception):
    """A gateway call failed. The subclasses name the known causes; any other
    failure (an unexpected status such as 429, a malformed body) is a plain
    GatewayError. No message ever contains the token."""


class GatewayAuthError(GatewayError):
    """The gateway rejected the key (HTTP 401 or 403)."""


class GatewayUnreachable(GatewayError):
    """The gateway could not be reached (connection error, timeout, HTTP 5xx)."""


class GatewayEmptyList(GatewayError):
    """The account allows no models."""


@dataclass(frozen=True)
class GatewayModel:
    """One model of a gateway seed. `efforts` are Claude Code's levels the
    model publishes, or None (no effort control, no effort sent). `status` is
    the last test's outcome; only "ok" is usable."""

    id: str
    label: str
    group: Literal["managed", "pinned", "policy"]
    efforts: tuple[str, ...] | None
    status: Literal["untested", "ok", "failed"] = "untested"
    reason: str | None = None


@dataclass(frozen=True)
class GatewayPreset:
    """A data chat's Fast or Extensive pick."""

    model: str
    effort: str | None = None


@dataclass(frozen=True)
class GatewaySeed:
    """The decrypted content of a gateway seed. `token` stays out of the repr."""

    provider: str
    region: str
    base_url: str
    token: str = field(repr=False)
    models: tuple[GatewayModel, ...]
    default_model: str
    presets: dict[str, GatewayPreset] | None = None
    tested_at: datetime | None = None

    def usable_models(self) -> tuple[GatewayModel, ...]:
        """The models that passed the last test (status "ok"), in seed order."""
        return tuple(m for m in self.models if m.status == "ok")

    def effective_default_model(self) -> str | None:
        """The default model if usable, else the first usable model, else None."""
        usable = [m.id for m in self.usable_models()]
        if self.default_model in usable:
            return self.default_model
        return usable[0] if usable else None


def seed_summary(seed: GatewaySeed) -> str:
    """A one-line, non-secret label (the key appears only as its last four)."""
    return f"{seed.provider} {seed.region}, default {seed.default_model}, key …{seed.token[-4:]}"


# --- encoding ---------------------------------------------------------------


def _check(seed: GatewaySeed) -> None:
    ids = {m.id for m in seed.models}
    if seed.default_model not in ids:
        raise ValueError(f"default model {seed.default_model!r} is not one of the seed's models")
    presets = seed.presets or {}
    unknown = sorted(set(presets) - set(_PRESET_KEYS))
    if unknown:
        raise ValueError(f"unknown preset keys {unknown}; allowed: {', '.join(_PRESET_KEYS)}")
    for key, preset in presets.items():
        if preset.model not in ids:
            raise ValueError(f"preset {key!r} names {preset.model!r}, which is not one of the seed's models")


def _presets_json(presets: dict[str, GatewayPreset] | None) -> dict | None:
    if presets is None:
        return None
    return {k: {"model": p.model, "effort": p.effort} for k, p in presets.items()}


def _blob_json(seed: GatewaySeed) -> dict:
    return {
        "version": GATEWAY_SEED_VERSION,
        "provider": seed.provider,
        "region": seed.region,
        "baseUrl": seed.base_url,
        "token": seed.token,
        "models": [
            {
                "id": m.id,
                "label": m.label,
                "group": m.group,
                "efforts": None if m.efforts is None else list(m.efforts),
                "status": m.status,
                "reason": m.reason,
            }
            for m in seed.models
        ],
        "defaultModel": seed.default_model,
        "presets": _presets_json(seed.presets),
        "testedAt": None if seed.tested_at is None else seed.tested_at.astimezone(timezone.utc).isoformat(),
    }


def _seed_from_json(data: dict) -> GatewaySeed:
    presets = data["presets"]
    tested_at = data["testedAt"]
    return GatewaySeed(
        provider=data["provider"],
        region=data["region"],
        base_url=data["baseUrl"],
        token=data["token"],
        models=tuple(
            GatewayModel(
                id=m["id"],
                label=m["label"],
                group=m["group"],
                efforts=None if m["efforts"] is None else tuple(m["efforts"]),
                status=m["status"],
                reason=m["reason"],
            )
            for m in data["models"]
        ),
        default_model=data["defaultModel"],
        presets=None if presets is None else {
            k: GatewayPreset(model=p["model"], effort=p["effort"]) for k, p in presets.items()
        },
        tested_at=None if tested_at is None else datetime.fromisoformat(tested_at),
    )


# Neither helper below raises: an error raised inside an except block keeps the
# original on `__context__` (`from None` only hides it from the printout), and a
# JSON decode error's `.doc` / `.object` is the decrypted blob, which holds the
# token when the JSON is cut short. read_gateway_seed raises outside them.


def _loads(plain: bytes) -> object:
    """The decoded blob, or _MALFORMED."""
    try:
        return json.loads(plain)
    except ValueError:
        return _MALFORMED


def _seed_or_malformed(data: dict) -> object:
    """The GatewaySeed, or _MALFORMED for a missing or malformed field."""
    try:
        return _seed_from_json(data)
    except (KeyError, TypeError, ValueError, AttributeError):
        return _MALFORMED


def _metadata(seed: GatewaySeed) -> dict:
    return {
        "provider": seed.provider,
        "region": seed.region,
        "keyHint": seed.token[-4:],
        "defaultModel": seed.default_model,
        "models": [
            {"id": m.id, "label": m.label, "status": m.status, "reason": m.reason}
            for m in seed.models
        ],
        "presets": _presets_json(seed.presets),
        "testedAt": seed.tested_at,
    }


# --- storage ----------------------------------------------------------------


def _collection(db: AsyncIOMotorDatabase, prefix: str, suffix: str):
    return db[f"{prefix}{suffix}"]


async def _upload(db: AsyncIOMotorDatabase, seed: GatewaySeed, encrypt: Callable[[bytes], bytes] | None):
    plain = json.dumps(_blob_json(seed)).encode("utf-8")
    payload = encrypt(plain) if encrypt is not None else plain
    return await AsyncIOMotorGridFSBucket(db).upload_from_stream("seed", payload)


async def write_gateway_seed(
    db: AsyncIOMotorDatabase,
    *,
    prefix: str,
    suffix: str,
    seed: GatewaySeed,
    encrypt: Callable[[bytes], bytes] | None,
    status: SeedStatus = "pending",
) -> str:
    """Store a new gateway seed; return its id. Raises ValueError when the
    default model or a preset's model is not one of the seed's models, or a
    preset key is not "fast" / "extensive"."""
    _check(seed)
    blob_id = await _upload(db, seed, encrypt)
    result = await _collection(db, prefix, suffix).insert_one({
        "createdAt": datetime.now(timezone.utc),
        "blobId": blob_id,
        "status": status,
        "metadata": {"gateway": _metadata(seed)},
    })
    return str(result.inserted_id)


async def gateway_seed_status(
    db: AsyncIOMotorDatabase, *, prefix: str, suffix: str, seed_id: str,
) -> str | None:
    """The seed document's status ("pending" | "alive" | "dead"); None for an
    unknown id."""
    doc = await seeds.load_seed(db, prefix=prefix, suffix=suffix, seed_id=seed_id)
    return None if doc is None else doc.get("status")


async def read_gateway_seed(
    db: AsyncIOMotorDatabase,
    *,
    prefix: str,
    suffix: str,
    seed_id: str,
    decrypt: Callable[[bytes], bytes] | None,
) -> GatewaySeed:
    """Load and decrypt a gateway seed. Raises KeyError for an unknown id and
    ValueError for a blob that is not a well-formed version-1 gateway seed
    (raised with no exception context, so no plaintext travels with it)."""
    doc = await seeds.load_seed(db, prefix=prefix, suffix=suffix, seed_id=seed_id)
    if doc is None:
        raise KeyError(f"unknown gateway seed: {seed_id!r}")
    buf = io.BytesIO()
    await AsyncIOMotorGridFSBucket(db).download_to_stream(doc["blobId"], buf)
    raw = buf.getvalue()
    plain = decrypt(raw) if decrypt is not None else raw
    data = _loads(plain)
    if data is _MALFORMED:
        raise ValueError(f"gateway seed {seed_id!r}: the blob is not JSON (wrong cipher?)")
    version = data.get("version") if isinstance(data, dict) else None
    if version != GATEWAY_SEED_VERSION:
        raise ValueError(f"gateway seed {seed_id!r}: unsupported blob version {version!r}")
    seed = _seed_or_malformed(data)
    if seed is _MALFORMED:
        raise ValueError(f"gateway seed {seed_id!r}: the blob has a missing or malformed field")
    return seed


async def update_gateway_seed(
    db: AsyncIOMotorDatabase,
    *,
    prefix: str,
    suffix: str,
    seed_id: str,
    seed: GatewaySeed,
    encrypt: Callable[[bytes], bytes] | None,
    status: SeedStatus | None = None,
) -> None:
    """Replace a gateway seed's content in place (the id is stable), and its
    status when given. Crash-safe blob swap: store the new blob, repoint the
    document, delete the old blob. Raises KeyError for an unknown id."""
    coll = _collection(db, prefix, suffix)
    doc = await seeds.load_seed(db, prefix=prefix, suffix=suffix, seed_id=seed_id)
    if doc is None:
        raise KeyError(f"unknown gateway seed: {seed_id!r}")
    bucket = AsyncIOMotorGridFSBucket(db)
    new_blob_id = await _upload(db, seed, encrypt)
    sets = {
        "blobId": new_blob_id,
        "metadata.gateway": _metadata(seed),
        "updatedAt": datetime.now(timezone.utc),
    }
    if status is not None:
        sets["status"] = status
    res = await coll.update_one({"_id": doc["_id"]}, {"$set": sets})
    if res.matched_count == 0:  # removed since we loaded it
        await bucket.delete(new_blob_id)
        raise KeyError(f"unknown gateway seed: {seed_id!r}")
    try:
        await bucket.delete(doc["blobId"])
    except NoFile:
        pass


# --- launch -----------------------------------------------------------------

# The child's secret environment variables, in the order the launch reads them
# from stdin. Never passed in `env=`: a remote host writes that into the command
# line sshd runs.
GATEWAY_SECRET_ENV = ("ANTHROPIC_AUTH_TOKEN",)

# Inherited variables a gateway launch removes (`env_remove` globs), so no other
# Anthropic endpoint, key or OAuth token reaches the child.
GATEWAY_ENV_REMOVE = ("ANTHROPIC_*", "CLAUDE_CODE_OAUTH_TOKEN")

# Claude Code's model aliases. Each points at the default model, so its own
# background calls (haiku) and alias-based subagents stay on a validated model.
_MODEL_ALIASES = ("OPUS", "SONNET", "HAIKU", "FABLE")


def _usable_default(seed: GatewaySeed) -> str:
    model = seed.effective_default_model()
    if model is None:
        raise GatewayError("the gateway seed has no usable model")
    return model


def gateway_catalog(seed: GatewaySeed) -> list[dict]:
    """The session's model catalog: the usable models in seed order, as
    `models.parse_cli_models` entries `{"id", "label", "effort"?}`, with
    `effort` only for a model that has levels."""
    out: list[dict] = []
    for m in seed.usable_models():
        entry: dict = {"id": m.id, "label": m.label}
        if m.efforts:
            entry["effort"] = list(m.efforts)
        out.append(entry)
    return out


def gateway_launch_env(seed: GatewaySeed) -> dict[str, str]:
    """The child's environment from the seed, without the token
    (`GATEWAY_SECRET_ENV`). The aliases point at the effective default model.
    `CLAUDE_CODE_ALWAYS_ENABLE_EFFORT` (Claude Code sends no effort for a model
    id it does not know without it) is set when some usable model has levels:
    the environment is fixed at launch and models switch live. GatewayError
    when no model is usable."""
    default = _usable_default(seed)
    env = {
        "ANTHROPIC_BASE_URL": seed.base_url,
        **{f"ANTHROPIC_DEFAULT_{alias}_MODEL": default for alias in _MODEL_ALIASES},
        # The server-side auto-mode classifier needs Anthropic's API.
        "CLAUDE_CODE_AUTO_MODE_SERVER": "0",
        # Keeps the token from the agent's Bash commands, hooks and MCP servers.
        "CLAUDE_CODE_SUBPROCESS_ENV_SCRUB": "1",
    }
    if any(m.efforts for m in seed.usable_models()):
        env["CLAUDE_CODE_ALWAYS_ENABLE_EFFORT"] = "1"
    return env


def gateway_settings_json(env: dict[str, str]) -> str:
    """`--settings` for a gateway launch: ``{"env": env}`` as JSON, ``env``
    being the launch's non-secret gateway environment (`gateway_launch_env`).
    Claude Code applies a settings file's `env` over the process environment,
    and the agent can write the user, project and local settings files; the
    command-line layer outranks all three, at launch and after a settings
    reload (measured, AGENTS.md Testing), so `ANTHROPIC_BASE_URL` and the
    aliases stay the gateway's. ValueError, naming the key only, for a
    `GATEWAY_SECRET_ENV` key: the token never goes on a command line."""
    secret = sorted(k for k in env if k in GATEWAY_SECRET_ENV)
    if secret:
        raise ValueError(f"{', '.join(secret)} never goes into --settings")
    return json.dumps({"env": env}, separators=(",", ":"))


def launch_model(seed: GatewaySeed, preferred: str | None) -> str:
    """`--model` for a launch, an exact id: `preferred` when it is usable, else
    the effective default model (never the `default` alias). GatewayError when
    no model is usable."""
    if preferred in {m.id for m in seed.usable_models()}:
        return preferred
    return _usable_default(seed)


def launch_effort(seed: GatewaySeed, model: str, preferred: str | None) -> str | None:
    """`--effort` for a launch on `model`: None when the model has no levels
    or is not usable (none is sent); `preferred` when the model lists it;
    else the level the switcher preselects (`models.model_effort`: "high"
    when listed, else the highest; seed levels are in `models.EFFORT_LEVELS`
    order)."""
    levels, preselected = cc_models.model_effort(model, gateway_catalog(seed))
    if not levels:
        return None
    return preferred if preferred in levels else preselected
