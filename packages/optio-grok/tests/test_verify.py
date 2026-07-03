"""verify_and_refresh_seed unit tests — host-free direct-OIDC path (mocked HTTP).

The refresh talks straight to the xAI OIDC token endpoint (no grok, no model
inference). These tests stub the three sync HTTP helpers (_discover_sync /
_refresh_sync / _validate_sync) so the whole verify/refresh/save-back/status
logic runs against a real Mongo seed with zero network.
"""

from __future__ import annotations

import asyncio
import io
import json
import os
import tarfile
from datetime import datetime, timedelta, timezone

from bson import ObjectId
from motor.motor_asyncio import AsyncIOMotorGridFSBucket
from optio_core.context import ProcessContext
from optio_host.host import LocalHost

from optio_agents import seeds
from optio_grok import verify
from optio_grok.seed_manifest import GROK_SEED_MANIFEST, GROK_SEED_SUFFIX
from optio_grok.verify import verify_and_refresh_seed

_ISSUER = "https://auth.x.ai"
_CLIENT = "b1a00492-073a-47ea-816f-4c329264a828"
_TOPKEY = f"{_ISSUER}::{_CLIENT}"
_DISCO = {
    "token_endpoint": "https://auth.x.ai/oauth2/token",
    "userinfo_endpoint": "https://auth.x.ai/oauth2/userinfo",
}


def _iso(dt: datetime) -> str:
    return dt.isoformat().replace("+00:00", "Z")


def _parse(s: str) -> datetime:
    return verify._parse_expiry(s)


async def _make_seed(mongo_db, tmp_path, *, expires_at, refresh_token="ORIGINAL") -> str:
    oid = ObjectId()
    await mongo_db["test_processes"].insert_one({"_id": oid, "processId": "p"})
    ctx = ProcessContext(
        process_oid=oid, process_id="p", root_oid=oid, depth=0, params={},
        services={}, db=mongo_db, prefix="test",
        cancellation_flag=asyncio.Event(), child_counter={"next": 0},
    )
    src = LocalHost(taskdir=str(tmp_path / f"seedsrc-{oid}"))
    await src.setup_workdir()
    d = os.path.join(src.workdir, "home", ".grok")
    os.makedirs(d, exist_ok=True)
    creds = {
        "key": "OLD_ACCESS",
        "auth_mode": "oidc",
        "email": "user@example.com",
        "user_id": "2433164c",
        "principal_id": "2433164c",
        "team_id": "edbfe989",
        "oidc_issuer": _ISSUER,
        "oidc_client_id": _CLIENT,
        "expires_at": expires_at,
    }
    if refresh_token is not None:
        creds["refresh_token"] = refresh_token
    with open(os.path.join(d, "auth.json"), "w") as fh:
        fh.write(json.dumps({_TOPKEY: creds}))
    with open(os.path.join(d, "config.toml"), "w") as fh:
        fh.write('model = "grok-fake"\n')
    return await seeds.capture_seed(
        ctx, src, manifest=GROK_SEED_MANIFEST, suffix=GROK_SEED_SUFFIX, encrypt=None,
    )


async def _seed_creds(mongo_db, seed_id: str) -> dict:
    doc = await seeds.load_seed(mongo_db, prefix="test", suffix=GROK_SEED_SUFFIX, seed_id=seed_id)
    buf = io.BytesIO()
    await AsyncIOMotorGridFSBucket(mongo_db).download_to_stream(doc["blobId"], buf)
    with tarfile.open(fileobj=io.BytesIO(buf.getvalue()), mode="r:gz") as tar:
        auth = json.loads(tar.extractfile(".grok/auth.json").read().decode("utf-8"))
    return next(iter(auth.values()))


async def _doc(mongo_db, seed_id: str) -> dict:
    return await seeds.load_seed(mongo_db, prefix="test", suffix=GROK_SEED_SUFFIX, seed_id=seed_id)


async def test_expired_refreshes_and_writes_back(mongo_db, tmp_path, monkeypatch):
    past = _iso(datetime.now(timezone.utc) - timedelta(hours=1))
    seed_id = await _make_seed(mongo_db, tmp_path, expires_at=past)

    monkeypatch.setattr(verify, "_discover_sync", lambda issuer: _DISCO)
    refreshed = {}

    def fake_refresh(token_endpoint, refresh_token, client_id):
        refreshed["called"] = (token_endpoint, refresh_token, client_id)
        return {"access_token": "NEW_ACCESS", "refresh_token": "ROTATED", "expires_in": 3600}

    monkeypatch.setattr(verify, "_refresh_sync", fake_refresh)

    alive = await verify_and_refresh_seed(mongo_db, prefix="test", seed_id=seed_id)
    assert alive is True
    assert refreshed["called"] == ("https://auth.x.ai/oauth2/token", "ORIGINAL", _CLIENT)

    creds = await _seed_creds(mongo_db, seed_id)
    assert creds["key"] == "NEW_ACCESS"                 # key = new access token
    assert creds["refresh_token"] == "ROTATED"          # rotated token saved back
    assert creds["expires_at"].endswith("Z")
    assert _parse(creds["expires_at"]) > datetime.now(timezone.utc)
    assert creds["email"] == "user@example.com"         # identity preserved

    doc = await _doc(mongo_db, seed_id)
    assert doc["status"] == "alive"
    assert doc["metadata"]["verify"]["alive"] is True


async def test_not_expired_valid_does_not_refresh(mongo_db, tmp_path, monkeypatch):
    future = _iso(datetime.now(timezone.utc) + timedelta(hours=2))
    seed_id = await _make_seed(mongo_db, tmp_path, expires_at=future)

    monkeypatch.setattr(verify, "_discover_sync", lambda issuer: _DISCO)
    monkeypatch.setattr(verify, "_validate_sync", lambda ep, tok: True)  # token live

    def boom(*a):
        raise AssertionError("must not refresh a valid, unexpired token")

    monkeypatch.setattr(verify, "_refresh_sync", boom)

    alive = await verify_and_refresh_seed(mongo_db, prefix="test", seed_id=seed_id)
    assert alive is True
    creds = await _seed_creds(mongo_db, seed_id)
    assert creds["refresh_token"] == "ORIGINAL"         # untouched
    assert creds["key"] == "OLD_ACCESS"


async def test_refresh_4xx_marks_dead(mongo_db, tmp_path, monkeypatch):
    past = _iso(datetime.now(timezone.utc) - timedelta(hours=1))
    seed_id = await _make_seed(mongo_db, tmp_path, expires_at=past)

    monkeypatch.setattr(verify, "_discover_sync", lambda issuer: _DISCO)
    monkeypatch.setattr(verify, "_refresh_sync", lambda *a: verify._DEAD)

    alive = await verify_and_refresh_seed(mongo_db, prefix="test", seed_id=seed_id)
    assert alive is False
    doc = await _doc(mongo_db, seed_id)
    assert doc["status"] == "dead"
    assert doc["metadata"]["verify"]["alive"] is False


async def test_transport_failure_is_inconclusive_not_dead(mongo_db, tmp_path, monkeypatch):
    past = _iso(datetime.now(timezone.utc) - timedelta(hours=1))
    seed_id = await _make_seed(mongo_db, tmp_path, expires_at=past)

    monkeypatch.setattr(verify, "_discover_sync", lambda issuer: _DISCO)
    monkeypatch.setattr(verify, "_refresh_sync", lambda *a: None)  # network error

    alive = await verify_and_refresh_seed(mongo_db, prefix="test", seed_id=seed_id)
    assert alive is False
    # A transient failure must NOT retire a possibly-healthy seed.
    assert (await _doc(mongo_db, seed_id)).get("status") != "dead"


async def test_discovery_failure_is_inconclusive(mongo_db, tmp_path, monkeypatch):
    past = _iso(datetime.now(timezone.utc) - timedelta(hours=1))
    seed_id = await _make_seed(mongo_db, tmp_path, expires_at=past)
    monkeypatch.setattr(verify, "_discover_sync", lambda issuer: None)

    alive = await verify_and_refresh_seed(mongo_db, prefix="test", seed_id=seed_id)
    assert alive is False
    assert (await _doc(mongo_db, seed_id)).get("status") != "dead"


async def test_no_refresh_token_is_dead(mongo_db, tmp_path):
    past = _iso(datetime.now(timezone.utc) - timedelta(hours=1))
    seed_id = await _make_seed(mongo_db, tmp_path, expires_at=past, refresh_token=None)

    alive = await verify_and_refresh_seed(mongo_db, prefix="test", seed_id=seed_id)
    assert alive is False
    assert (await _doc(mongo_db, seed_id))["status"] == "dead"


async def test_unknown_seed(mongo_db):
    alive = await verify_and_refresh_seed(mongo_db, prefix="test", seed_id=str(ObjectId()))
    assert alive is False
