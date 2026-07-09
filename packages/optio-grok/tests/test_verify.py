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

import pytest
from bson import ObjectId
from motor.motor_asyncio import AsyncIOMotorGridFSBucket
from optio_core.context import ProcessContext
from optio_host.host import LocalHost

from optio_agents import seeds
from optio_agents.account import EMPTY, AccountInfo
from optio_grok import verify
from optio_grok.seed_manifest import GROK_SEED_MANIFEST, GROK_SEED_SUFFIX
from optio_grok.verify import verify_and_refresh_seed


@pytest.fixture(autouse=True)
def stub_analyze_account(monkeypatch):
    """Keep verify's account-analysis off the network. Alive paths now call
    ``analyze_account`` for the metadata.accounts stamp; stub it to ``EMPTY`` so
    the refresh/liveness tests stay host-free and deterministic. An ``EMPTY``
    result wraps to the empty ``accounts`` list (nothing to stamp). (The
    dedicated account-flow test below overrides this with a real AccountInfo.)"""
    async def _empty(creds):
        return EMPTY

    monkeypatch.setattr(verify, "analyze_account", _empty)

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

    res = await verify_and_refresh_seed(mongo_db, prefix="test", seed_id=seed_id)
    assert res["alive"] is True
    assert res["accounts"] == []  # stubbed EMPTY wraps to no accounts
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

    res = await verify_and_refresh_seed(mongo_db, prefix="test", seed_id=seed_id)
    assert res["alive"] is True
    assert res["accounts"] == []
    creds = await _seed_creds(mongo_db, seed_id)
    assert creds["refresh_token"] == "ORIGINAL"         # untouched
    assert creds["key"] == "OLD_ACCESS"


async def test_refresh_4xx_marks_dead(mongo_db, tmp_path, monkeypatch):
    past = _iso(datetime.now(timezone.utc) - timedelta(hours=1))
    seed_id = await _make_seed(mongo_db, tmp_path, expires_at=past)

    monkeypatch.setattr(verify, "_discover_sync", lambda issuer: _DISCO)
    monkeypatch.setattr(verify, "_refresh_sync", lambda *a: verify._DEAD)

    res = await verify_and_refresh_seed(mongo_db, prefix="test", seed_id=seed_id)
    assert res["alive"] is False
    assert res["accounts"] == []
    doc = await _doc(mongo_db, seed_id)
    assert doc["status"] == "dead"
    assert doc["metadata"]["verify"]["alive"] is False


async def test_transport_failure_is_inconclusive_not_dead(mongo_db, tmp_path, monkeypatch):
    past = _iso(datetime.now(timezone.utc) - timedelta(hours=1))
    seed_id = await _make_seed(mongo_db, tmp_path, expires_at=past)

    monkeypatch.setattr(verify, "_discover_sync", lambda issuer: _DISCO)
    monkeypatch.setattr(verify, "_refresh_sync", lambda *a: None)  # network error

    res = await verify_and_refresh_seed(mongo_db, prefix="test", seed_id=seed_id)
    assert res["alive"] is False
    assert res["accounts"] == []
    # A transient failure must NOT retire a possibly-healthy seed.
    assert (await _doc(mongo_db, seed_id)).get("status") != "dead"


async def test_discovery_failure_is_inconclusive(mongo_db, tmp_path, monkeypatch):
    past = _iso(datetime.now(timezone.utc) - timedelta(hours=1))
    seed_id = await _make_seed(mongo_db, tmp_path, expires_at=past)
    monkeypatch.setattr(verify, "_discover_sync", lambda issuer: None)

    res = await verify_and_refresh_seed(mongo_db, prefix="test", seed_id=seed_id)
    assert res["alive"] is False
    assert res["accounts"] == []
    assert (await _doc(mongo_db, seed_id)).get("status") != "dead"


async def test_no_refresh_token_is_dead(mongo_db, tmp_path):
    past = _iso(datetime.now(timezone.utc) - timedelta(hours=1))
    seed_id = await _make_seed(mongo_db, tmp_path, expires_at=past, refresh_token=None)

    res = await verify_and_refresh_seed(mongo_db, prefix="test", seed_id=seed_id)
    assert res["alive"] is False
    assert res["accounts"] == []
    assert (await _doc(mongo_db, seed_id))["status"] == "dead"


async def test_alive_carries_accounts_and_stamps_metadata(mongo_db, tmp_path, monkeypatch):
    # The alive path calls analyze_account(creds), wraps the single AccountInfo in
    # a 1-element list, returns it as ``accounts`` and stamps metadata.accounts.
    # Override the autouse EMPTY stub with a real AccountInfo, assert end-to-end.
    future = _iso(datetime.now(timezone.utc) + timedelta(hours=2))
    seed_id = await _make_seed(mongo_db, tmp_path, expires_at=future)

    monkeypatch.setattr(verify, "_discover_sync", lambda issuer: _DISCO)
    monkeypatch.setattr(verify, "_validate_sync", lambda ep, tok: True)

    info = AccountInfo(
        name="Test User", email="user@example.com", plan="Grok Pro",
        account_id="00000000-0000-0000-0000-000000000001", windows=(),
        raw={"userinfo": {"sub": "00000000-0000-0000-0000-000000000001"}},
    )
    seen = {}

    async def _analyze(creds):
        seen["creds"] = creds
        return info

    monkeypatch.setattr(verify, "analyze_account", _analyze)

    res = await verify_and_refresh_seed(mongo_db, prefix="test", seed_id=seed_id)
    assert res["alive"] is True
    assert res["accounts"] == [info]
    # analyze_account received the live creds dict (bearer key present).
    assert seen["creds"]["key"] == "OLD_ACCESS"

    doc = await _doc(mongo_db, seed_id)
    accounts = doc["metadata"]["accounts"]
    assert len(accounts) == 1
    assert accounts[0]["plan"] == "Grok Pro"
    assert accounts[0]["name"] == "Test User"
    assert accounts[0]["email"] == "user@example.com"
    assert accounts[0]["account_id"] == "00000000-0000-0000-0000-000000000001"
    assert accounts[0]["summary"] == "Plan: Grok Pro for Test User <user@example.com>"
    assert accounts[0]["windows"] == []  # grok: always empty


async def test_unknown_seed(mongo_db):
    res = await verify_and_refresh_seed(mongo_db, prefix="test", seed_id=str(ObjectId()))
    assert res["alive"] is False
    assert res["accounts"] == []
