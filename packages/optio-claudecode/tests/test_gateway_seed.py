"""Gateway seeds: stored pending, encrypted, with a non-secret summary."""

import io
import json
import traceback
from dataclasses import replace
from datetime import datetime, timezone

import gridfs
import pytest
from bson import ObjectId
from motor.motor_asyncio import AsyncIOMotorGridFSBucket

from optio_agents import seeds
from optio_claudecode.gateway import (
    GatewayModel,
    GatewayPreset,
    GatewaySeed,
    gateway_seed_status,
    read_gateway_seed,
    seed_summary,
    update_gateway_seed,
    write_gateway_seed,
)
from optio_claudecode.seed_manifest import REQUESTY_SEED_SUFFIX

KIMI = GatewayModel("prov-a/kimi-k3.1-pro", "prov-a/kimi-k3.1-pro", "pinned", ("low", "high"))
GLM = GatewayModel("glm-flash", "glm-flash", "managed", None)
SEED = GatewaySeed(
    provider="requesty",
    region="eu",
    base_url="https://router.eu.requesty.ai",
    token="rq-test-0123456789abcd",
    models=(KIMI, GLM),
    default_model="prov-a/kimi-k3.1-pro",
)
COLL = "t_claudecode_requesty_seeds"
# SEED's blob, decrypted.
SEED_BLOB = {
    "version": 1, "provider": "requesty", "region": "eu",
    "baseUrl": "https://router.eu.requesty.ai", "token": "rq-test-0123456789abcd",
    "models": [
        {"id": "prov-a/kimi-k3.1-pro", "label": "prov-a/kimi-k3.1-pro", "group": "pinned",
         "efforts": ["low", "high"], "status": "untested", "reason": None},
        {"id": "glm-flash", "label": "glm-flash", "group": "managed",
         "efforts": None, "status": "untested", "reason": None}],
    "defaultModel": "prov-a/kimi-k3.1-pro", "presets": None, "testedAt": None}


def enc(b: bytes) -> bytes:
    return b"X" + bytes(reversed(b))


def dec(b: bytes) -> bytes:
    assert b[:1] == b"X"
    return bytes(reversed(b[1:]))


def wrong_enc(b: bytes) -> bytes:
    """Another cipher: `dec` accepts its output but yields garbage."""
    return b"X" + bytes(x ^ 0x5A for x in reversed(b))


async def _blob_bytes(db, blob_id) -> bytes:
    buf = io.BytesIO()
    await AsyncIOMotorGridFSBucket(db).download_to_stream(blob_id, buf)
    return buf.getvalue()


async def _plant(db, plain: bytes, cipher=enc) -> str:
    """A seed document whose blob is `plain`, encrypted by `cipher`."""
    blob = await AsyncIOMotorGridFSBucket(db).upload_from_stream("seed", cipher(plain))
    res = await db[COLL].insert_one({
        "createdAt": datetime.now(timezone.utc), "blobId": blob, "status": "alive", "metadata": {},
    })
    return str(res.inserted_id)


def _assert_carries_no_token(exc: BaseException) -> None:
    """No exception context, and the token in neither str, repr nor traceback (RF1)."""
    assert exc.__context__ is None and exc.__cause__ is None
    shown = str(exc) + repr(exc) + "".join(traceback.format_exception(exc))
    assert SEED.token not in shown


async def _write(db, seed=SEED, **kw) -> str:
    return await write_gateway_seed(
        db, prefix="t", suffix=REQUESTY_SEED_SUFFIX, seed=seed, encrypt=enc, **kw,
    )


async def _read(db, sid: str) -> GatewaySeed:
    return await read_gateway_seed(
        db, prefix="t", suffix=REQUESTY_SEED_SUFFIX, seed_id=sid, decrypt=dec,
    )


async def _update(db, sid: str, seed: GatewaySeed, **kw) -> None:
    await update_gateway_seed(
        db, prefix="t", suffix=REQUESTY_SEED_SUFFIX, seed_id=sid, seed=seed, encrypt=enc, **kw,
    )


async def test_a_written_seed_reads_back_through_the_cipher(mongo_db):      # RF1, RF2
    sid = await _write(mongo_db)
    assert await _read(mongo_db, sid) == SEED
    doc = await mongo_db[COLL].find_one()
    assert str(doc["_id"]) == sid
    assert doc["status"] == "pending"
    # ONE insert writes the whole document, status included (RF2).
    assert set(doc) == {"_id", "createdAt", "blobId", "status", "metadata"}
    blob = await _blob_bytes(mongo_db, doc["blobId"])
    assert SEED.token.encode() not in blob
    assert SEED.token not in str(doc) and SEED.token not in repr(SEED)
    assert json.loads(dec(blob)) == SEED_BLOB


async def test_the_document_carries_the_non_secret_part(mongo_db):
    await _write(mongo_db)
    doc = await mongo_db[COLL].find_one()
    assert doc["metadata"]["gateway"] == {
        "provider": "requesty", "region": "eu", "keyHint": "abcd",
        "defaultModel": "prov-a/kimi-k3.1-pro", "presets": None, "testedAt": None, "models": [
            {"id": "prov-a/kimi-k3.1-pro", "label": "prov-a/kimi-k3.1-pro", "status": "untested", "reason": None},
            {"id": "glm-flash", "label": "glm-flash", "status": "untested", "reason": None}]}

    with_presets = replace(SEED, presets={
        "fast": GatewayPreset("glm-flash"),
        "extensive": GatewayPreset("prov-a/kimi-k3.1-pro", "high"),
    })
    sid = await _write(mongo_db, with_presets, status="alive")
    doc = await mongo_db[COLL].find_one({"_id": ObjectId(sid)})
    assert doc["status"] == "alive"
    assert doc["metadata"]["gateway"]["presets"] == {
        "fast": {"model": "glm-flash", "effort": None},
        "extensive": {"model": "prov-a/kimi-k3.1-pro", "effort": "high"},
    }


async def test_update_swaps_the_blob_and_sets_the_status(mongo_db):
    sid = await _write(mongo_db)
    old_blob = (await mongo_db[COLL].find_one())["blobId"]
    tested = datetime(2026, 11, 17, 12, 0, tzinfo=timezone.utc)
    new = replace(
        SEED,
        models=(replace(KIMI, status="ok"), replace(GLM, status="failed", reason="HTTP 404")),
        presets={"fast": GatewayPreset("glm-flash"), "extensive": GatewayPreset("prov-a/kimi-k3.1-pro", "high")},
        tested_at=tested,
    )

    await _update(mongo_db, sid, new)

    doc = await mongo_db[COLL].find_one()
    assert doc["status"] == "pending"
    assert doc["blobId"] != old_blob
    with pytest.raises(gridfs.errors.NoFile):
        await _blob_bytes(mongo_db, old_blob)
    assert isinstance(doc["updatedAt"], datetime)
    gw = doc["metadata"]["gateway"]
    assert gw["testedAt"].replace(tzinfo=timezone.utc) == tested
    assert gw["models"] == [
        {"id": "prov-a/kimi-k3.1-pro", "label": "prov-a/kimi-k3.1-pro", "status": "ok", "reason": None},
        {"id": "glm-flash", "label": "glm-flash", "status": "failed", "reason": "HTTP 404"}]
    back = await _read(mongo_db, sid)
    assert back == new
    assert back.tested_at.tzinfo is not None
    assert back.models[0].efforts == ("low", "high")

    # An old blob that is already gone does not stop the next update.
    await AsyncIOMotorGridFSBucket(mongo_db).delete(doc["blobId"])
    await _update(mongo_db, sid, new, status="alive")
    doc = await mongo_db[COLL].find_one()
    assert doc["status"] == "alive"
    assert await _read(mongo_db, sid) == new


async def test_update_of_a_seed_removed_meanwhile_raises_and_leaves_no_blob(mongo_db, monkeypatch):
    sid = await _write(mongo_db)
    real_load = seeds.load_seed

    async def load_then_purge(db, **kw):
        doc = await real_load(db, **kw)
        await seeds.purge_seed(db, **kw)
        return doc

    monkeypatch.setattr(seeds, "load_seed", load_then_purge)
    with pytest.raises(KeyError):
        await _update(mongo_db, sid, SEED, status="alive")
    assert await mongo_db[COLL].count_documents({}) == 0
    assert await mongo_db["fs.files"].count_documents({}) == 0


def test_usable_models_and_the_effective_default():
    assert SEED.usable_models() == ()                    # untested is not usable
    assert SEED.effective_default_model() is None

    only_glm = replace(SEED, models=(replace(KIMI, status="failed", reason="x"), replace(GLM, status="ok")))
    assert only_glm.usable_models() == (replace(GLM, status="ok"),)
    assert only_glm.effective_default_model() == "glm-flash"

    both = replace(SEED, models=(replace(GLM, status="ok"), replace(KIMI, status="ok")))
    assert both.effective_default_model() == "prov-a/kimi-k3.1-pro"

    none_ok = replace(SEED, models=(replace(KIMI, status="failed"), replace(GLM, status="failed")))
    assert none_ok.usable_models() == ()
    assert none_ok.effective_default_model() is None


async def test_unknown_seed_and_unknown_version(mongo_db):
    unknown = str(ObjectId())
    with pytest.raises(KeyError):
        await _read(mongo_db, unknown)
    with pytest.raises(KeyError):
        await _read(mongo_db, "not-an-id")
    with pytest.raises(KeyError):
        await _update(mongo_db, unknown, SEED)
    assert await gateway_seed_status(mongo_db, prefix="t", suffix=REQUESTY_SEED_SUFFIX, seed_id=unknown) is None
    assert await gateway_seed_status(mongo_db, prefix="t", suffix=REQUESTY_SEED_SUFFIX, seed_id="not-an-id") is None
    sid = await _write(mongo_db)
    assert await gateway_seed_status(mongo_db, prefix="t", suffix=REQUESTY_SEED_SUFFIX, seed_id=sid) == "pending"

    v2 = await _plant(mongo_db, json.dumps({**SEED_BLOB, "version": 2}).encode())
    with pytest.raises(ValueError, match="version") as ei:
        await _read(mongo_db, v2)
    _assert_carries_no_token(ei.value)


@pytest.mark.parametrize("plain, cipher", [
    (json.dumps(SEED_BLOB).encode()[:-40], enc),
    (json.dumps(SEED_BLOB).encode(), wrong_enc),
], ids=["cut-short", "wrong-cipher"])
async def test_a_blob_that_is_not_json_raises_with_no_context(mongo_db, plain, cipher):   # RF1
    assert SEED.token.encode() in plain     # the decode error would carry it
    sid = await _plant(mongo_db, plain, cipher)
    with pytest.raises(ValueError, match="not JSON") as ei:
        await _read(mongo_db, sid)
    _assert_carries_no_token(ei.value)


@pytest.mark.parametrize("blob", [
    {k: v for k, v in SEED_BLOB.items() if k != "models"},
    {**SEED_BLOB, "models": [["prov-a/kimi-k3.1-pro"]]},
    {**SEED_BLOB, "presets": ["fast"]},
    {**SEED_BLOB, "testedAt": "yesterday"},
], ids=["missing-field", "model-not-an-object", "presets-not-an-object", "bad-date"])
async def test_a_version_1_blob_with_a_bad_field_raises_value_error(mongo_db, blob):
    sid = await _plant(mongo_db, json.dumps(blob).encode())
    with pytest.raises(ValueError, match="missing or malformed") as ei:
        await _read(mongo_db, sid)
    _assert_carries_no_token(ei.value)


@pytest.mark.parametrize("seed", [
    replace(SEED, default_model="prov-z/other"),
    replace(SEED, presets={"slow": GatewayPreset("glm-flash")}),
    replace(SEED, presets={"fast": GatewayPreset("prov-z/other")}),
], ids=["default", "preset-key", "preset-model"])
async def test_write_refuses_a_default_outside_the_models(mongo_db, seed):
    with pytest.raises(ValueError):
        await _write(mongo_db, seed)
    assert await mongo_db[COLL].count_documents({}) == 0
    assert await mongo_db["fs.files"].count_documents({}) == 0


def test_seed_summary():
    assert seed_summary(SEED) == "requesty eu, default prov-a/kimi-k3.1-pro, key …abcd"
