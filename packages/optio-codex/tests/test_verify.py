"""verify_and_refresh_seed unit tests — direct-OIDC path (mocked HTTP) + the
agent-probe fallback (fake codex).

The refresh talks straight to OpenAI's OIDC token endpoint (no codex process,
no model inference). These tests stub the two sync HTTP helpers
(_discover_sync / _refresh_sync) so the verify/refresh/save-back/status logic
runs against a real Mongo seed with zero network. One test forces the
discovery-unavailable path and asserts codex falls back to the (billable)
agent probe — codex KEEPS that path (unlike grok), so it is covered here.
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
from optio_codex import verify
from optio_codex.seed_manifest import CODEX_SEED_MANIFEST, CODEX_SEED_SUFFIX
from optio_codex.verify import verify_and_refresh_seed

_ISSUER = "https://auth.openai.com"
_CLIENT = "app_EMoamEEZ73f0CkXaXp7hrann"
# Discovery's token_endpoint is the REAL discovered value — an account-management
# surface, NOT codex's refresh URL. Production must IGNORE this and refresh
# against the hardcoded _REFRESH_URL (https://auth.openai.com/oauth/token). The
# stale-refresh test below asserts the refresh call used /oauth/token, so it
# fails if anyone regresses to disco["token_endpoint"].
_DISCO = {
    "issuer": _ISSUER,
    "token_endpoint": "https://auth.openai.com/api/accounts/oauth/token",
}


def _iso(dt: datetime) -> str:
    return dt.isoformat().replace("+00:00", "Z")


async def _make_seed(
    mongo_db, tmp_path, *, last_refresh, refresh_token="ORIGINAL",
    api_key=None, tokens=True,
) -> str:
    oid = ObjectId()
    await mongo_db["test_processes"].insert_one({"_id": oid, "processId": "p"})
    ctx = ProcessContext(
        process_oid=oid, process_id="p", root_oid=oid, depth=0, params={},
        services={}, db=mongo_db, prefix="test",
        cancellation_flag=asyncio.Event(), child_counter={"next": 0},
    )
    src = LocalHost(taskdir=str(tmp_path / f"seedsrc-{oid}"))
    await src.setup_workdir()
    d = os.path.join(src.workdir, "home", ".codex")
    os.makedirs(d, exist_ok=True)
    auth: dict = {"OPENAI_API_KEY": api_key, "last_refresh": last_refresh}
    if tokens:
        tok = {
            "id_token": "OLD_ID",
            "access_token": "OLD_ACCESS",
            "account_id": "acct-1",
        }
        if refresh_token is not None:
            tok["refresh_token"] = refresh_token
        auth["tokens"] = tok
    else:
        auth["tokens"] = None
    with open(os.path.join(d, "auth.json"), "w") as fh:
        fh.write(json.dumps(auth))
    with open(os.path.join(d, "config.toml"), "w") as fh:
        fh.write('model = "gpt-5.5"\n')
    return await seeds.capture_seed(
        ctx, src, manifest=CODEX_SEED_MANIFEST, suffix=CODEX_SEED_SUFFIX,
        encrypt=None,
    )


async def _seed_auth(mongo_db, seed_id: str) -> dict:
    doc = await seeds.load_seed(
        mongo_db, prefix="test", suffix=CODEX_SEED_SUFFIX, seed_id=seed_id)
    buf = io.BytesIO()
    await AsyncIOMotorGridFSBucket(mongo_db).download_to_stream(doc["blobId"], buf)
    with tarfile.open(fileobj=io.BytesIO(buf.getvalue()), mode="r:gz") as tar:
        return json.loads(tar.extractfile(".codex/auth.json").read().decode("utf-8"))


async def _doc(mongo_db, seed_id: str) -> dict:
    return await seeds.load_seed(
        mongo_db, prefix="test", suffix=CODEX_SEED_SUFFIX, seed_id=seed_id)


async def test_stale_chatgpt_refreshes_and_writes_back(mongo_db, tmp_path, monkeypatch):
    old = _iso(datetime.now(timezone.utc) - timedelta(days=9))  # >8d → refresh
    seed_id = await _make_seed(mongo_db, tmp_path, last_refresh=old)

    monkeypatch.setattr(verify, "_discover_sync", lambda issuer: _DISCO)
    seen = {}

    def fake_refresh(refresh_url, refresh_token, client_id):
        seen["call"] = (refresh_url, refresh_token, client_id)
        return {
            "access_token": "NEW_ACCESS", "refresh_token": "ROTATED",
            "id_token": "NEW_ID", "expires_in": 864000,
        }

    monkeypatch.setattr(verify, "_refresh_sync", fake_refresh)

    alive = await verify_and_refresh_seed(mongo_db, prefix="test", seed_id=seed_id)
    assert alive is True
    assert seen["call"] == ("https://auth.openai.com/oauth/token", "ORIGINAL", _CLIENT)

    auth = await _seed_auth(mongo_db, seed_id)
    assert auth["tokens"]["access_token"] == "NEW_ACCESS"
    assert auth["tokens"]["refresh_token"] == "ROTATED"
    assert auth["tokens"]["id_token"] == "NEW_ID"
    assert auth["tokens"]["account_id"] == "acct-1"        # identity preserved
    assert auth["last_refresh"] != old                     # stamped
    assert (await _doc(mongo_db, seed_id))["status"] == "alive"


async def test_fresh_chatgpt_does_not_refresh(mongo_db, tmp_path, monkeypatch):
    recent = _iso(datetime.now(timezone.utc) - timedelta(days=1))  # <8d
    seed_id = await _make_seed(mongo_db, tmp_path, last_refresh=recent)
    monkeypatch.setattr(verify, "_discover_sync", lambda issuer: _DISCO)

    def boom(*a):
        raise AssertionError("must not refresh a fresh token")

    monkeypatch.setattr(verify, "_refresh_sync", boom)
    alive = await verify_and_refresh_seed(mongo_db, prefix="test", seed_id=seed_id)
    assert alive is True
    auth = await _seed_auth(mongo_db, seed_id)
    assert auth["tokens"]["refresh_token"] == "ORIGINAL"   # untouched


async def test_api_key_seed_is_alive_by_presence(mongo_db, tmp_path, monkeypatch):
    # API-key seeds have no rotating token — no refresh, alive by presence.
    seed_id = await _make_seed(
        mongo_db, tmp_path, last_refresh=None, api_key="sk-abc", tokens=False)

    def boom(*a):
        raise AssertionError("API-key seed must not hit the token endpoint")

    monkeypatch.setattr(verify, "_discover_sync", boom)
    monkeypatch.setattr(verify, "_refresh_sync", boom)
    alive = await verify_and_refresh_seed(mongo_db, prefix="test", seed_id=seed_id)
    assert alive is True
    assert (await _doc(mongo_db, seed_id))["status"] == "alive"


async def test_refresh_4xx_marks_dead(mongo_db, tmp_path, monkeypatch):
    old = _iso(datetime.now(timezone.utc) - timedelta(days=9))
    seed_id = await _make_seed(mongo_db, tmp_path, last_refresh=old)
    monkeypatch.setattr(verify, "_discover_sync", lambda issuer: _DISCO)
    monkeypatch.setattr(verify, "_refresh_sync", lambda *a: verify._DEAD)
    alive = await verify_and_refresh_seed(mongo_db, prefix="test", seed_id=seed_id)
    assert alive is False
    assert (await _doc(mongo_db, seed_id))["status"] == "dead"


async def test_transport_failure_is_inconclusive_not_dead(mongo_db, tmp_path, monkeypatch):
    old = _iso(datetime.now(timezone.utc) - timedelta(days=9))
    seed_id = await _make_seed(mongo_db, tmp_path, last_refresh=old)
    monkeypatch.setattr(verify, "_discover_sync", lambda issuer: _DISCO)
    monkeypatch.setattr(verify, "_refresh_sync", lambda *a: None)  # network err
    alive = await verify_and_refresh_seed(mongo_db, prefix="test", seed_id=seed_id)
    assert alive is False
    assert (await _doc(mongo_db, seed_id)).get("status") != "dead"


async def test_no_refresh_token_is_dead(mongo_db, tmp_path):
    old = _iso(datetime.now(timezone.utc) - timedelta(days=9))
    seed_id = await _make_seed(
        mongo_db, tmp_path, last_refresh=old, refresh_token=None)
    alive = await verify_and_refresh_seed(mongo_db, prefix="test", seed_id=seed_id)
    assert alive is False
    assert (await _doc(mongo_db, seed_id))["status"] == "dead"


async def test_discovery_unavailable_falls_back_to_agent_probe(
    mongo_db, task_root, shim_install_dir, tmp_path, monkeypatch,
):
    # Discovery down → codex KEEPS the (billable) agent probe as the fallback
    # (divergence from grok). The fake codex probe answers "paris" → alive, and
    # rotates the auth.json ("ROTATED-BY-PROBE") which is saved back.
    monkeypatch.setenv("FAKE_CODEX_PROBE", "alive")
    old = _iso(datetime.now(timezone.utc) - timedelta(days=9))
    seed_id = await _make_seed(mongo_db, tmp_path, last_refresh=old)
    monkeypatch.setattr(verify, "_discover_sync", lambda issuer: None)  # no endpoint

    alive = await verify_and_refresh_seed(
        mongo_db, prefix="test", seed_id=seed_id, install_dir=str(shim_install_dir),
    )
    assert alive is True
    assert (await _doc(mongo_db, seed_id))["status"] == "alive"


async def test_unknown_seed(mongo_db):
    alive = await verify_and_refresh_seed(
        mongo_db, prefix="test", seed_id=str(ObjectId()))
    assert alive is False
