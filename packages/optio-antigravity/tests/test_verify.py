"""verify_and_refresh_seed unit tests — host-free direct-OIDC path (mocked HTTP).

The refresh talks straight to Google's OIDC token endpoint (no ``agy`` process,
no model inference). These tests stub the sync HTTP helpers (``_discover_sync`` /
``_refresh_sync`` / ``_validate_sync``, or ``urllib.request.urlopen`` for the
HTTP-error cases) so the whole verify/refresh/save-back/status logic runs against
a real Mongo seed with zero network. Mirrors optio-grok's ``test_verify``.

Token store (S1, real Google login 2026-07-06): agy writes NESTED JSON to
``.gemini/antigravity-cli/antigravity-oauth-token`` (imported here as the SSOT
``_TOKEN_MEMBER``): ``{"auth_method": "consumer", "token": {access_token,
token_type, refresh_token, expiry}}``. ``expiry`` is an ISO-8601 string (not
epoch-millis). agy is a public PKCE client → the refresh grant sends client_id
only (no secret), and only a definitive ``invalid_grant`` retires a seed.
"""

from __future__ import annotations

import asyncio
import io
import json
import os
import tarfile
from datetime import datetime, timedelta, timezone
from urllib.error import HTTPError

from bson import ObjectId
from motor.motor_asyncio import AsyncIOMotorGridFSBucket
from optio_core.context import ProcessContext
from optio_host.host import LocalHost

from optio_agents import seeds
from optio_antigravity import verify
from optio_antigravity.seed_manifest import (
    ANTIGRAVITY_SEED_MANIFEST,
    ANTIGRAVITY_SEED_SUFFIX,
    _TOKEN_STORE_RELPATH as _TOKEN_MEMBER,
)
from optio_antigravity.verify import verify_and_refresh_seed

_DISCO = {
    "token_endpoint": "https://oauth2.googleapis.com/token",
    "userinfo_endpoint": "https://openidconnect.googleapis.com/v1/userinfo",
}


def _iso(dt: datetime) -> str:
    """agy's ``expiry`` is an ISO-8601 string with a timezone offset."""
    return dt.isoformat()


def _http_error(body: dict) -> HTTPError:
    """A urllib HTTPError whose response body is ``body`` as JSON (what agy's
    token endpoint returns on a rejected refresh — ``_refresh_sync`` reads the
    ``error`` field to decide dead vs inconclusive)."""
    return HTTPError(
        "https://oauth2.googleapis.com/token", 400, "Bad Request", {},
        io.BytesIO(json.dumps(body).encode("utf-8")),
    )


async def _make_seed(mongo_db, tmp_path, *, expiry, refresh_token="ORIGINAL") -> str:
    oid = ObjectId()
    await mongo_db["test_processes"].insert_one({"_id": oid, "processId": "p"})
    ctx = ProcessContext(
        process_oid=oid, process_id="p", root_oid=oid, depth=0, params={},
        services={}, db=mongo_db, prefix="test",
        cancellation_flag=asyncio.Event(), child_counter={"next": 0},
    )
    src = LocalHost(taskdir=str(tmp_path / f"seedsrc-{oid}"))
    await src.setup_workdir()
    # The real nested agy store at .gemini/antigravity-cli/antigravity-oauth-token.
    token = {"access_token": "OLD_ACCESS", "token_type": "Bearer", "expiry": expiry}
    if refresh_token is not None:
        token["refresh_token"] = refresh_token
    store = {"auth_method": "consumer", "token": token}
    token_abs = os.path.join(src.workdir, "home", _TOKEN_MEMBER)
    os.makedirs(os.path.dirname(token_abs), exist_ok=True)
    with open(token_abs, "w") as fh:
        fh.write(json.dumps(store))
    # a non-secret settings member so the manifest captures the full set
    sdir = os.path.join(src.workdir, "home", ".gemini", "antigravity-cli")
    os.makedirs(sdir, exist_ok=True)
    with open(os.path.join(sdir, "settings.json"), "w") as fh:
        fh.write("{}")
    return await seeds.capture_seed(
        ctx, src, manifest=ANTIGRAVITY_SEED_MANIFEST, suffix=ANTIGRAVITY_SEED_SUFFIX,
        encrypt=None,
    )


async def _seed_store(mongo_db, seed_id: str) -> dict:
    doc = await seeds.load_seed(
        mongo_db, prefix="test", suffix=ANTIGRAVITY_SEED_SUFFIX, seed_id=seed_id,
    )
    buf = io.BytesIO()
    await AsyncIOMotorGridFSBucket(mongo_db).download_to_stream(doc["blobId"], buf)
    with tarfile.open(fileobj=io.BytesIO(buf.getvalue()), mode="r:gz") as tar:
        return json.loads(tar.extractfile(_TOKEN_MEMBER).read().decode("utf-8"))


async def _doc(mongo_db, seed_id: str) -> dict:
    return await seeds.load_seed(
        mongo_db, prefix="test", suffix=ANTIGRAVITY_SEED_SUFFIX, seed_id=seed_id,
    )


def test_parse_expiry_nanosecond_iso():
    # agy's expiry is Go RFC3339Nano (9 frac digits + offset); fromisoformat only
    # takes <=6, so the nanoseconds must be truncated, not rejected.
    dt = verify._parse_expiry("2026-07-06T15:17:07.684178639+02:00")
    assert dt is not None
    assert dt.tzinfo is not None
    assert dt == datetime(2026, 7, 6, 13, 17, 7, 684178, tzinfo=timezone.utc)


async def test_expired_refreshes_and_writes_back(mongo_db, tmp_path, monkeypatch):
    past = _iso(datetime.now(timezone.utc) - timedelta(hours=1))
    seed_id = await _make_seed(mongo_db, tmp_path, expiry=past)

    monkeypatch.setattr(verify, "_discover_sync", lambda issuer: _DISCO)
    refreshed = {}

    def fake_refresh(token_endpoint, refresh_token, client_id):
        refreshed["called"] = (token_endpoint, refresh_token, client_id)
        return {"access_token": "NEW_ACCESS", "refresh_token": "ROTATED", "expires_in": 3600}

    monkeypatch.setattr(verify, "_refresh_sync", fake_refresh)

    res = await verify_and_refresh_seed(mongo_db, prefix="test", seed_id=seed_id)
    assert res["alive"] is True
    assert res["account"] is None
    # PKCE public client → client_id only (no secret), the agy constant.
    assert refreshed["called"][0] == "https://oauth2.googleapis.com/token"
    assert refreshed["called"][1] == "ORIGINAL"
    assert refreshed["called"][2] == verify._AGY_CLIENT_ID

    store = await _seed_store(mongo_db, seed_id)
    tok = store["token"]
    assert tok["access_token"] == "NEW_ACCESS"          # access token rotated
    assert tok["refresh_token"] == "ROTATED"            # rotated token saved back
    assert isinstance(tok["expiry"], str)               # ISO string, not epoch
    assert verify._parse_expiry(tok["expiry"]) > datetime.now(timezone.utc)
    assert tok["token_type"] == "Bearer"                # inner keys preserved
    assert store["auth_method"] == "consumer"           # outer structure preserved

    doc = await _doc(mongo_db, seed_id)
    assert doc["status"] == "alive"
    assert doc["metadata"]["verify"]["alive"] is True


async def test_refresh_without_rotation_preserves_old_token(mongo_db, tmp_path, monkeypatch):
    # Google typically does NOT return a new refresh_token; the old one must stay.
    past = _iso(datetime.now(timezone.utc) - timedelta(hours=1))
    seed_id = await _make_seed(mongo_db, tmp_path, expiry=past)
    monkeypatch.setattr(verify, "_discover_sync", lambda issuer: _DISCO)
    monkeypatch.setattr(
        verify, "_refresh_sync",
        lambda *a: {"access_token": "NEW_ACCESS", "expires_in": 3600},
    )
    res = await verify_and_refresh_seed(mongo_db, prefix="test", seed_id=seed_id)
    assert res["alive"] is True
    assert res["account"] is None
    tok = (await _seed_store(mongo_db, seed_id))["token"]
    assert tok["access_token"] == "NEW_ACCESS"
    assert tok["refresh_token"] == "ORIGINAL"           # preserved


async def test_not_expired_valid_does_not_refresh(mongo_db, tmp_path, monkeypatch):
    future = _iso(datetime.now(timezone.utc) + timedelta(hours=2))
    seed_id = await _make_seed(mongo_db, tmp_path, expiry=future)

    monkeypatch.setattr(verify, "_discover_sync", lambda issuer: _DISCO)
    monkeypatch.setattr(verify, "_validate_sync", lambda ep, tok: True)  # token live

    def boom(*a):
        raise AssertionError("must not refresh a valid, unexpired token")

    monkeypatch.setattr(verify, "_refresh_sync", boom)

    res = await verify_and_refresh_seed(mongo_db, prefix="test", seed_id=seed_id)
    assert res["alive"] is True
    assert res["account"] is None
    tok = (await _seed_store(mongo_db, seed_id))["token"]
    assert tok["refresh_token"] == "ORIGINAL"           # untouched
    assert tok["access_token"] == "OLD_ACCESS"


async def test_refresh_invalid_grant_marks_dead(mongo_db, tmp_path, monkeypatch):
    # A definitive invalid_grant (spent/revoked lineage) retires the seed. Drive
    # the REAL _refresh_sync via a mocked urlopen raising the token endpoint's
    # {"error":"invalid_grant"} 4xx, so the dead-classification path is exercised.
    past = _iso(datetime.now(timezone.utc) - timedelta(hours=1))
    seed_id = await _make_seed(mongo_db, tmp_path, expiry=past)

    monkeypatch.setattr(verify, "_discover_sync", lambda issuer: _DISCO)

    def raise_invalid_grant(req, timeout=None):
        raise _http_error({"error": "invalid_grant"})

    monkeypatch.setattr(verify.urllib.request, "urlopen", raise_invalid_grant)

    res = await verify_and_refresh_seed(mongo_db, prefix="test", seed_id=seed_id)
    assert res["alive"] is False
    assert res["account"] is None
    doc = await _doc(mongo_db, seed_id)
    assert doc["status"] == "dead"
    assert doc["metadata"]["verify"]["alive"] is False


async def test_refresh_non_invalid_grant_http_error_is_inconclusive(mongo_db, tmp_path, monkeypatch):
    # A non-invalid_grant 4xx (e.g. invalid_client — a possibly-wrong client/PKCE
    # assumption) must NOT retire a possibly-healthy seed. Guards fail-closed #6.
    past = _iso(datetime.now(timezone.utc) - timedelta(hours=1))
    seed_id = await _make_seed(mongo_db, tmp_path, expiry=past)

    monkeypatch.setattr(verify, "_discover_sync", lambda issuer: _DISCO)

    def raise_invalid_client(req, timeout=None):
        raise _http_error({"error": "invalid_client"})

    monkeypatch.setattr(verify.urllib.request, "urlopen", raise_invalid_client)

    res = await verify_and_refresh_seed(mongo_db, prefix="test", seed_id=seed_id)
    assert res["alive"] is False
    assert res["account"] is None
    assert (await _doc(mongo_db, seed_id)).get("status") != "dead"


async def test_transport_failure_is_inconclusive_not_dead(mongo_db, tmp_path, monkeypatch):
    past = _iso(datetime.now(timezone.utc) - timedelta(hours=1))
    seed_id = await _make_seed(mongo_db, tmp_path, expiry=past)

    monkeypatch.setattr(verify, "_discover_sync", lambda issuer: _DISCO)
    monkeypatch.setattr(verify, "_refresh_sync", lambda *a: None)  # network error

    res = await verify_and_refresh_seed(mongo_db, prefix="test", seed_id=seed_id)
    assert res["alive"] is False
    assert res["account"] is None
    # A transient failure must NOT retire a possibly-healthy seed.
    assert (await _doc(mongo_db, seed_id)).get("status") != "dead"


async def test_discovery_failure_is_inconclusive(mongo_db, tmp_path, monkeypatch):
    past = _iso(datetime.now(timezone.utc) - timedelta(hours=1))
    seed_id = await _make_seed(mongo_db, tmp_path, expiry=past)
    monkeypatch.setattr(verify, "_discover_sync", lambda issuer: None)

    res = await verify_and_refresh_seed(mongo_db, prefix="test", seed_id=seed_id)
    assert res["alive"] is False
    assert res["account"] is None
    assert (await _doc(mongo_db, seed_id)).get("status") != "dead"


async def test_no_refresh_token_is_dead(mongo_db, tmp_path):
    past = _iso(datetime.now(timezone.utc) - timedelta(hours=1))
    seed_id = await _make_seed(mongo_db, tmp_path, expiry=past, refresh_token=None)

    res = await verify_and_refresh_seed(mongo_db, prefix="test", seed_id=seed_id)
    assert res["alive"] is False
    assert res["account"] is None
    assert (await _doc(mongo_db, seed_id))["status"] == "dead"


async def test_unknown_seed(mongo_db):
    res = await verify_and_refresh_seed(
        mongo_db, prefix="test", seed_id=str(ObjectId()),
    )
    assert res["alive"] is False
    assert res["account"] is None
