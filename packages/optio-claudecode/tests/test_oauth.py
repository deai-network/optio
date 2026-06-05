"""Unit tests for host-free OAuth/verify (stubbed network)."""

import os

import pytest_asyncio
from bson import ObjectId
from motor.motor_asyncio import AsyncIOMotorClient
from optio_host.host import LocalHost

from optio_agents import seeds
from optio_claudecode import oauth


@pytest_asyncio.fixture
async def mongo_db():
    client = AsyncIOMotorClient(os.environ.get("MONGO_URL", "mongodb://localhost:27017"))
    name = f"optio_cc_oauth_{os.getpid()}"
    db = client[name]
    yield db
    await client.drop_database(name)
    client.close()


async def _ctx(mongo_db, taskdir):
    import asyncio
    from optio_core.context import ProcessContext
    oid = ObjectId()
    await mongo_db["t_processes"].insert_one({"_id": oid, "processId": "p"})
    return ProcessContext(
        process_oid=oid, process_id="p", root_oid=oid, depth=0, params={},
        services={}, db=mongo_db, prefix="t", cancellation_flag=asyncio.Event(),
        child_counter={"next": 0},
    )


def _plant_creds(workdir, access, refresh, expires_at):
    claude = os.path.join(workdir, "home", ".claude")
    os.makedirs(claude, exist_ok=True)
    import json
    with open(os.path.join(claude, ".credentials.json"), "w") as fh:
        json.dump({"claudeAiOauth": {
            "accessToken": access, "refreshToken": refresh, "expiresAt": expires_at,
            "scopes": ["user:inference"], "subscriptionType": "max",
        }}, fh)


async def _seed_with_creds(mongo_db, tmp_workdir, name, *, access, refresh, expires_at):
    src = LocalHost(taskdir=os.path.join(tmp_workdir, name))
    await src.setup_workdir()
    _plant_creds(src.workdir, access, refresh, expires_at)
    ctx = await _ctx(mongo_db, src.taskdir)
    manifest = seeds.SeedManifest(home_subdir="home", include=[".claude/.credentials.json"], version=1)
    sid = await seeds.capture_seed(ctx, src, manifest=manifest, suffix="_cc_seeds", encrypt=None)
    return sid


async def test_verify_fresh_valid_token_no_refresh(mongo_db, tmp_workdir, monkeypatch):
    future = 9999999999999
    sid = await _seed_with_creds(mongo_db, tmp_workdir, "v1", access="AT", refresh="RT", expires_at=future)

    async def fake_validate(t):
        assert t == "AT"
        return True
    async def fake_usage(t): return {"five_hour": {"utilization": 1.0, "resets_at": None}}
    async def fake_profile(t): return {"uuid": "u1", "summary": "Plan: Max for <a@b>"}
    async def fail_refresh(rt): raise AssertionError("must not refresh a valid token")
    monkeypatch.setattr(oauth, "validate_token", fake_validate)
    monkeypatch.setattr(oauth, "fetch_usage", fake_usage)
    monkeypatch.setattr(oauth, "summarize_profile", fake_profile)
    monkeypatch.setattr(oauth, "refresh_oauth_token", fail_refresh)

    res = await oauth.verify_and_refresh_seed(
        mongo_db, prefix="t", suffix="_cc_seeds", seed_id=sid, encrypt=None, decrypt=None,
    )
    assert res["alive"] is True
    assert res["account"] == {"uuid": "u1", "summary": "Plan: Max for <a@b>"}
    doc = await seeds.load_seed(mongo_db, prefix="t", suffix="_cc_seeds", seed_id=sid)
    assert "usage" in doc["metadata"] and doc["metadata"]["account"]["uuid"] == "u1"


async def test_verify_expired_refreshes_and_saves_back(mongo_db, tmp_workdir, monkeypatch):
    sid = await _seed_with_creds(mongo_db, tmp_workdir, "v2", access="OLD", refresh="RT", expires_at=1)

    async def fake_refresh(rt):
        assert rt == "RT"
        return {"access_token": "NEW_AT", "refresh_token": "NEW_RT", "expires_in": 28800, "scope": "user:inference"}
    async def fake_usage(t):
        assert t == "NEW_AT"
        return {"five_hour": {"utilization": 1.0}}
    async def fake_profile(t): return {"uuid": "u2", "summary": "s"}
    monkeypatch.setattr(oauth, "refresh_oauth_token", fake_refresh)
    monkeypatch.setattr(oauth, "fetch_usage", fake_usage)
    monkeypatch.setattr(oauth, "summarize_profile", fake_profile)

    res = await oauth.verify_and_refresh_seed(
        mongo_db, prefix="t", suffix="_cc_seeds", seed_id=sid, encrypt=None, decrypt=None,
    )
    assert res["alive"] is True
    # creds saved back: re-read the seed's credentials member
    import io
    import json
    import tarfile

    from motor.motor_asyncio import AsyncIOMotorGridFSBucket
    doc = await seeds.load_seed(mongo_db, prefix="t", suffix="_cc_seeds", seed_id=sid)
    b = io.BytesIO()
    await AsyncIOMotorGridFSBucket(mongo_db).download_to_stream(doc["blobId"], b)
    with tarfile.open(fileobj=io.BytesIO(b.getvalue()), mode="r:gz") as tar:
        creds = json.loads(tar.extractfile(".claude/.credentials.json").read())["claudeAiOauth"]
    assert creds["accessToken"] == "NEW_AT" and creds["refreshToken"] == "NEW_RT"


async def test_verify_dead_on_invalid_grant(mongo_db, tmp_workdir, monkeypatch):
    sid = await _seed_with_creds(mongo_db, tmp_workdir, "v3", access="OLD", refresh="RT", expires_at=1)

    async def dead_refresh(rt): return None
    monkeypatch.setattr(oauth, "refresh_oauth_token", dead_refresh)
    res = await oauth.verify_and_refresh_seed(
        mongo_db, prefix="t", suffix="_cc_seeds", seed_id=sid, encrypt=None, decrypt=None,
    )
    assert res["alive"] is False


def test_usage_limited():
    from datetime import datetime, timezone, timedelta
    now = datetime.now(timezone.utc)
    future = (now + timedelta(hours=1)).isoformat()
    past = (now - timedelta(hours=1)).isoformat()
    from optio_claudecode import oauth
    assert oauth.usage_limited({"five_hour": {"utilization": 100.0, "resets_at": future}}, now) is True
    assert oauth.usage_limited({"five_hour": {"utilization": 100.0, "resets_at": past}}, now) is False
    assert oauth.usage_limited({"five_hour": {"utilization": 16.0, "resets_at": future}}, now) is False
    assert oauth.usage_limited(None, now) is False
    # per-model gating
    u = {"five_hour": {"utilization": 1.0}, "seven_day_opus": {"utilization": 100.0, "resets_at": future}}
    assert oauth.usage_limited(u, now) is False
    assert oauth.usage_limited(u, now, models_required=["opus"]) is True


def test_seed_signature_excludes_auth_and_noise(tmp_path):
    import io
    import tarfile

    def mk(members):
        buf = io.BytesIO()
        with tarfile.open(fileobj=buf, mode="w:gz") as tar:
            for name, data in members.items():
                info = tarfile.TarInfo(name=name)
                info.size = len(data)
                info.mtime = 0
                tar.addfile(info, io.BytesIO(data))
        return buf.getvalue()
    from optio_claudecode import oauth
    good = mk({
        ".claude/.credentials.json": b'{"x":1}',
        ".claude/settings.json": b'{"theme":"dark","skipDangerousModePermissionPrompt":true}',
        ".claude/mcp-needs-auth-cache.json": b'{}',
        ".claude.json": b'{"firstStartTime":"A"}',
    })
    # same shape, different auth + different .claude.json -> SAME signature
    good2 = mk({
        ".claude/.credentials.json": b'{"x":2}',
        ".claude/settings.json": b'{"theme":"dark","skipDangerousModePermissionPrompt":true}',
        ".claude/mcp-needs-auth-cache.json": b'{}',
        ".claude.json": b'{"firstStartTime":"B","userID":"u"}',
    })
    # degenerate: missing mcp-cache + missing the settings key
    bad = mk({
        ".claude/.credentials.json": b'{"x":3}',
        ".claude/settings.json": b'{"theme":"dark"}',
        ".claude.json": b'{"firstStartTime":"C"}',
    })
    assert oauth.seed_signature(good) == oauth.seed_signature(good2)
    assert oauth.seed_signature(bad) != oauth.seed_signature(good)
    assert ".claude/.credentials.json" not in oauth.seed_signature(good)["members"]
    assert ".claude.json" not in oauth.seed_signature(good)["members"]
