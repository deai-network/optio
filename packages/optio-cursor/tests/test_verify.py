"""verify_and_refresh_seed unit tests (fake cursor probe, real Mongo).

Host-free verify: plant a seed into a throwaway workdir (per-task HOME +
XDG_CONFIG_HOME), run one headless ``cursor-agent -p "<probe>" --trust``
challenge-answer via the cursor shim (fake_cursor's probe mode), take the
verdict from stdout only, and write the rotated auth.json back into the seed.
No real cursor-agent binary or network.

Adapted from optio-grok's ``test_verify.py`` (grok → cursor renames; the
credential member is ``.config/cursor/auth.json`` with cursor's flat
``accessToken``/``refreshToken`` shape).
"""

from __future__ import annotations

import asyncio
import io
import json
import os
import tarfile

from bson import ObjectId
from motor.motor_asyncio import AsyncIOMotorGridFSBucket
from optio_core.context import ProcessContext
from optio_host.host import LocalHost

from optio_agents import seeds
from optio_cursor.seed_manifest import CURSOR_SEED_MANIFEST, CURSOR_SEED_SUFFIX
from optio_cursor.verify import verify_and_refresh_seed


async def _make_seed(mongo_db, tmp_path) -> str:
    oid = ObjectId()
    await mongo_db["test_processes"].insert_one({"_id": oid, "processId": "p"})
    ctx = ProcessContext(
        process_oid=oid, process_id="p", root_oid=oid, depth=0, params={},
        services={}, db=mongo_db, prefix="test",
        cancellation_flag=asyncio.Event(), child_counter={"next": 0},
    )
    src = LocalHost(taskdir=str(tmp_path / "seedsrc"))
    await src.setup_workdir()
    cfg = os.path.join(src.workdir, "home", ".config", "cursor")
    os.makedirs(cfg, exist_ok=True)
    with open(os.path.join(cfg, "auth.json"), "w") as fh:
        fh.write(json.dumps({
            "accessToken": "fake-access-token",
            "refreshToken": "ORIGINAL",
        }))
    ch = os.path.join(src.workdir, "home", ".cursor")
    os.makedirs(ch, exist_ok=True)
    with open(os.path.join(ch, "cli-config.json"), "w") as fh:
        fh.write(json.dumps({"version": 1}))
    return await seeds.capture_seed(
        ctx, src, manifest=CURSOR_SEED_MANIFEST, suffix=CURSOR_SEED_SUFFIX,
        encrypt=None,
    )


async def _seed_auth(mongo_db, seed_id: str) -> dict:
    doc = await seeds.load_seed(
        mongo_db, prefix="test", suffix=CURSOR_SEED_SUFFIX, seed_id=seed_id,
    )
    buf = io.BytesIO()
    await AsyncIOMotorGridFSBucket(mongo_db).download_to_stream(doc["blobId"], buf)
    with tarfile.open(fileobj=io.BytesIO(buf.getvalue()), mode="r:gz") as tar:
        f = tar.extractfile(".config/cursor/auth.json")
        return json.loads(f.read().decode("utf-8"))


async def test_alive_and_writes_back_rotated_auth(
    mongo_db, task_root, shim_install_dir, tmp_path, monkeypatch,
):
    monkeypatch.setenv("FAKE_CURSOR_PROBE", "alive")
    seed_id = await _make_seed(mongo_db, tmp_path)

    alive = await verify_and_refresh_seed(
        mongo_db, prefix="test", seed_id=seed_id,
        install_dir=str(shim_install_dir),
    )
    assert alive is True

    auth = await _seed_auth(mongo_db, seed_id)
    assert auth["refreshToken"] == "ROTATED-BY-PROBE", auth

    doc = await seeds.load_seed(
        mongo_db, prefix="test", suffix=CURSOR_SEED_SUFFIX, seed_id=seed_id,
    )
    assert doc["metadata"]["verify"]["alive"] is True
    assert doc["status"] == "alive"


async def test_dead_on_auth_error(
    mongo_db, task_root, shim_install_dir, tmp_path, monkeypatch,
):
    monkeypatch.setenv("FAKE_CURSOR_PROBE", "dead")
    seed_id = await _make_seed(mongo_db, tmp_path)

    alive = await verify_and_refresh_seed(
        mongo_db, prefix="test", seed_id=seed_id,
        install_dir=str(shim_install_dir),
    )
    assert alive is False

    doc = await seeds.load_seed(
        mongo_db, prefix="test", suffix=CURSOR_SEED_SUFFIX, seed_id=seed_id,
    )
    assert doc["status"] == "dead"
    assert doc["metadata"]["verify"]["alive"] is False


async def test_prompt_echo_does_not_false_positive(
    mongo_db, task_root, shim_install_dir, tmp_path, monkeypatch,
):
    # An error path that echoes the prompt must NOT count as alive — the
    # challenge answer token ("paris") is absent from the prompt.
    monkeypatch.setenv("FAKE_CURSOR_PROBE", "echo")
    seed_id = await _make_seed(mongo_db, tmp_path)

    alive = await verify_and_refresh_seed(
        mongo_db, prefix="test", seed_id=seed_id,
        install_dir=str(shim_install_dir),
    )
    assert alive is False


async def test_exit_code_carries_no_verdict(
    mongo_db, task_root, shim_install_dir, tmp_path, monkeypatch,
):
    # Answer present + non-zero exit -> still alive (stdout-only verdict).
    monkeypatch.setenv("FAKE_CURSOR_PROBE", "alive_badexit")
    seed_id = await _make_seed(mongo_db, tmp_path)

    alive = await verify_and_refresh_seed(
        mongo_db, prefix="test", seed_id=seed_id,
        install_dir=str(shim_install_dir),
    )
    assert alive is True


async def test_unknown_seed(mongo_db, task_root, shim_install_dir):
    alive = await verify_and_refresh_seed(
        mongo_db, prefix="test", seed_id=str(ObjectId()),
        install_dir=str(shim_install_dir),
    )
    assert alive is False
