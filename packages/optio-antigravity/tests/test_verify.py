"""verify_and_refresh_seed unit tests — host-free direct-OIDC path (mocked HTTP).

The refresh talks straight to Google's OIDC token endpoint (no ``agy`` process,
no model inference). These tests stub the three sync HTTP helpers
(``_discover_sync`` / ``_refresh_sync`` / ``_validate_sync``) so the whole
verify/refresh/save-back/status logic runs against a real Mongo seed with zero
network. Mirrors optio-grok's ``test_verify`` (xAI ← Google renames; the seed's
token store is agy's flat Google ``oauth_creds.json``, not grok's
``{issuer::client: creds}`` wrapper).

TODO(S1): the token-store relpath/shape + the public CLI client_id are the
design's likely-outcome (§2 option 1). Reconcile with the real-login spike.
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
from optio_antigravity import verify
from optio_antigravity.seed_manifest import (
    ANTIGRAVITY_SEED_MANIFEST,
    ANTIGRAVITY_SEED_SUFFIX,
)
from optio_antigravity.verify import verify_and_refresh_seed

_TOKEN_MEMBER = ".gemini/oauth_creds.json"
_DISCO = {
    "token_endpoint": "https://oauth2.googleapis.com/token",
    "userinfo_endpoint": "https://openidconnect.googleapis.com/v1/userinfo",
}


def _millis(dt: datetime) -> int:
    return int(dt.timestamp() * 1000)


async def _make_seed(mongo_db, tmp_path, *, expiry_date, refresh_token="ORIGINAL") -> str:
    oid = ObjectId()
    await mongo_db["test_processes"].insert_one({"_id": oid, "processId": "p"})
    ctx = ProcessContext(
        process_oid=oid, process_id="p", root_oid=oid, depth=0, params={},
        services={}, db=mongo_db, prefix="test",
        cancellation_flag=asyncio.Event(), child_counter={"next": 0},
    )
    src = LocalHost(taskdir=str(tmp_path / f"seedsrc-{oid}"))
    await src.setup_workdir()
    d = os.path.join(src.workdir, "home", ".gemini")
    os.makedirs(d, exist_ok=True)
    creds = {
        "access_token": "OLD_ACCESS",
        "token_type": "Bearer",
        "scope": "https://www.googleapis.com/auth/cloud-platform openid email",
        "id_token": "OLD_ID",
        "expiry_date": expiry_date,
    }
    if refresh_token is not None:
        creds["refresh_token"] = refresh_token
    with open(os.path.join(d, "oauth_creds.json"), "w") as fh:
        fh.write(json.dumps(creds))
    # a non-secret settings member so the manifest captures the full set
    sdir = os.path.join(src.workdir, "home", ".gemini", "antigravity-cli")
    os.makedirs(sdir, exist_ok=True)
    with open(os.path.join(sdir, "settings.json"), "w") as fh:
        fh.write("{}")
    return await seeds.capture_seed(
        ctx, src, manifest=ANTIGRAVITY_SEED_MANIFEST, suffix=ANTIGRAVITY_SEED_SUFFIX,
        encrypt=None,
    )


async def _seed_creds(mongo_db, seed_id: str) -> dict:
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


async def test_expired_refreshes_and_writes_back(mongo_db, tmp_path, monkeypatch):
    past = _millis(datetime.now(timezone.utc) - timedelta(hours=1))
    seed_id = await _make_seed(mongo_db, tmp_path, expiry_date=past)

    monkeypatch.setattr(verify, "_discover_sync", lambda issuer: _DISCO)
    refreshed = {}

    def fake_refresh(token_endpoint, refresh_token, client_id, client_secret):
        refreshed["called"] = (token_endpoint, refresh_token, client_id, client_secret)
        return {"access_token": "NEW_ACCESS", "refresh_token": "ROTATED", "expires_in": 3600}

    monkeypatch.setattr(verify, "_refresh_sync", fake_refresh)

    alive = await verify_and_refresh_seed(mongo_db, prefix="test", seed_id=seed_id)
    assert alive is True
    # No client_id in a real Google store → the public CLI constant is used.
    assert refreshed["called"][0] == "https://oauth2.googleapis.com/token"
    assert refreshed["called"][1] == "ORIGINAL"
    assert refreshed["called"][2] == verify._GEMINI_CLI_CLIENT_ID

    creds = await _seed_creds(mongo_db, seed_id)
    assert creds["access_token"] == "NEW_ACCESS"        # access token rotated
    assert creds["refresh_token"] == "ROTATED"          # rotated token saved back
    assert isinstance(creds["expiry_date"], (int, float))
    assert verify._parse_expiry(creds["expiry_date"]) > datetime.now(timezone.utc)
    assert creds["scope"].startswith("https://")        # identity/scope preserved

    doc = await _doc(mongo_db, seed_id)
    assert doc["status"] == "alive"
    assert doc["metadata"]["verify"]["alive"] is True


async def test_refresh_without_rotation_preserves_old_token(mongo_db, tmp_path, monkeypatch):
    # Google typically does NOT return a new refresh_token; the old one must stay.
    past = _millis(datetime.now(timezone.utc) - timedelta(hours=1))
    seed_id = await _make_seed(mongo_db, tmp_path, expiry_date=past)
    monkeypatch.setattr(verify, "_discover_sync", lambda issuer: _DISCO)
    monkeypatch.setattr(
        verify, "_refresh_sync",
        lambda *a: {"access_token": "NEW_ACCESS", "expires_in": 3600},
    )
    alive = await verify_and_refresh_seed(mongo_db, prefix="test", seed_id=seed_id)
    assert alive is True
    creds = await _seed_creds(mongo_db, seed_id)
    assert creds["access_token"] == "NEW_ACCESS"
    assert creds["refresh_token"] == "ORIGINAL"         # preserved


async def test_not_expired_valid_does_not_refresh(mongo_db, tmp_path, monkeypatch):
    future = _millis(datetime.now(timezone.utc) + timedelta(hours=2))
    seed_id = await _make_seed(mongo_db, tmp_path, expiry_date=future)

    monkeypatch.setattr(verify, "_discover_sync", lambda issuer: _DISCO)
    monkeypatch.setattr(verify, "_validate_sync", lambda ep, tok: True)  # token live

    def boom(*a):
        raise AssertionError("must not refresh a valid, unexpired token")

    monkeypatch.setattr(verify, "_refresh_sync", boom)

    alive = await verify_and_refresh_seed(mongo_db, prefix="test", seed_id=seed_id)
    assert alive is True
    creds = await _seed_creds(mongo_db, seed_id)
    assert creds["refresh_token"] == "ORIGINAL"         # untouched
    assert creds["access_token"] == "OLD_ACCESS"


async def test_refresh_4xx_marks_dead(mongo_db, tmp_path, monkeypatch):
    past = _millis(datetime.now(timezone.utc) - timedelta(hours=1))
    seed_id = await _make_seed(mongo_db, tmp_path, expiry_date=past)

    monkeypatch.setattr(verify, "_discover_sync", lambda issuer: _DISCO)
    monkeypatch.setattr(verify, "_refresh_sync", lambda *a: verify._DEAD)

    alive = await verify_and_refresh_seed(mongo_db, prefix="test", seed_id=seed_id)
    assert alive is False
    doc = await _doc(mongo_db, seed_id)
    assert doc["status"] == "dead"
    assert doc["metadata"]["verify"]["alive"] is False


async def test_transport_failure_is_inconclusive_not_dead(mongo_db, tmp_path, monkeypatch):
    past = _millis(datetime.now(timezone.utc) - timedelta(hours=1))
    seed_id = await _make_seed(mongo_db, tmp_path, expiry_date=past)

    monkeypatch.setattr(verify, "_discover_sync", lambda issuer: _DISCO)
    monkeypatch.setattr(verify, "_refresh_sync", lambda *a: None)  # network error

    alive = await verify_and_refresh_seed(mongo_db, prefix="test", seed_id=seed_id)
    assert alive is False
    # A transient failure must NOT retire a possibly-healthy seed.
    assert (await _doc(mongo_db, seed_id)).get("status") != "dead"


async def test_discovery_failure_is_inconclusive(mongo_db, tmp_path, monkeypatch):
    past = _millis(datetime.now(timezone.utc) - timedelta(hours=1))
    seed_id = await _make_seed(mongo_db, tmp_path, expiry_date=past)
    monkeypatch.setattr(verify, "_discover_sync", lambda issuer: None)

    alive = await verify_and_refresh_seed(mongo_db, prefix="test", seed_id=seed_id)
    assert alive is False
    assert (await _doc(mongo_db, seed_id)).get("status") != "dead"


async def test_no_refresh_token_is_dead(mongo_db, tmp_path):
    past = _millis(datetime.now(timezone.utc) - timedelta(hours=1))
    seed_id = await _make_seed(mongo_db, tmp_path, expiry_date=past, refresh_token=None)

    alive = await verify_and_refresh_seed(mongo_db, prefix="test", seed_id=seed_id)
    assert alive is False
    assert (await _doc(mongo_db, seed_id))["status"] == "dead"


async def test_unknown_seed(mongo_db):
    alive = await verify_and_refresh_seed(
        mongo_db, prefix="test", seed_id=str(ObjectId()),
    )
    assert alive is False
