"""The gateway setup task, on the real engine and fake_claude.

A setup tests a pending seed: when its default model passes, the seed becomes
alive and keeps only the models that passed; when the default fails, the seed
is removed and the run fails. Test again (``retest=True``) tests an alive or
dead seed again: a failing model stays in it as failed, and when every model
fails the seed is dead. A setup that is cancelled or fails before it writes
removes its pending seed.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import os
from dataclasses import replace
from datetime import datetime

import pytest
from bson import ObjectId

from optio_core.exceptions import ResultNotPublished

from optio_claudecode import REQUESTY_SEED_SUFFIX, oauth
from optio_claudecode.gateway import (
    GatewayModel,
    GatewayPreset,
    read_gateway_seed,
    write_gateway_seed,
)
from optio_claudecode.gateway_setup import create_gateway_setup_task
from optio_claudecode.gateways import requesty

from .test_conversation_ui_session import _make_optio, _wait_terminal


# Engine runs with live claude processes, and a cancellation within its grace.
pytestmark = pytest.mark.serial

PREFIX = "gwsetup"
TOKEN = "rq-setup-token-1234abcd"
SUMMARY = "requesty eu, default prov-a/kimi-k3.1-pro, key …abcd"
ERROR = "API Error: 404 model not available"
KIMI = GatewayModel("prov-a/kimi-k3.1-pro", "prov-a/kimi-k3.1-pro", "pinned", ("low", "high"))
GLM = GatewayModel("glm-flash", "glm-flash", "managed", None)
TESTED = (replace(KIMI, status="ok"), replace(GLM, status="ok"))


def _cipher(data: bytes) -> bytes:
    """The seed cipher of these tests (its own inverse)."""
    return data[::-1]


def _fake(monkeypatch, behaviours: dict[str, str]) -> None:
    monkeypatch.setenv("FAKE_CLAUDE_GATEWAY_MODELS", json.dumps(behaviours))


async def _write(mongo_db, *, status: str = "pending", models=(KIMI, GLM), presets=None) -> str:
    seed = requesty.new_seed(token=TOKEN, region="eu", models=models, default_model=KIMI.id,
                             presets=presets)
    return await write_gateway_seed(mongo_db, prefix=PREFIX, suffix=REQUESTY_SEED_SUFFIX,
                                    seed=seed, encrypt=_cipher, status=status)


async def _doc(mongo_db) -> dict | None:
    return await mongo_db[f"{PREFIX}_claudecode_requesty_seeds"].find_one()


async def _read(mongo_db, sid: str):
    return await read_gateway_seed(mongo_db, prefix=PREFIX, suffix=REQUESTY_SEED_SUFFIX,
                                   seed_id=sid, decrypt=_cipher)


def _task(pid: str, sid: str, claude_cache_dir, *, decrypt=_cipher, **kw):
    return create_gateway_setup_task(
        pid, "Setup Claude Code + Requesty", seed_id=sid, prefix=PREFIX,
        suffix=REQUESTY_SEED_SUFFIX, encrypt=_cipher, decrypt=decrypt,
        install_dir=str(claude_cache_dir), per_model_timeout=60, **kw,
    )


@contextlib.asynccontextmanager
async def _engine(mongo_db):
    optio = await _make_optio(mongo_db, PREFIX)
    try:
        yield optio
    finally:
        await optio.shutdown(grace_seconds=1.0)


async def _run(optio, task) -> tuple[dict, dict]:
    """Launch the setup; (its published result, its terminal record)."""
    await optio.adhoc_define(task)
    result = await optio.launch_and_await_result(task.process_id, session_id=None, timeout=120)
    return result, await _wait_terminal(optio, task.process_id)


async def _run_failing(optio, task) -> dict:
    """Launch a setup that publishes nothing; its terminal record."""
    await optio.adhoc_define(task)
    with pytest.raises(ResultNotPublished):
        await optio.launch_and_await_result(task.process_id, session_id=None, timeout=120)
    return await _wait_terminal(optio, task.process_id)


def _catching(task, caught: list):
    """``task`` with the exception its run fails with recorded in ``caught``."""
    async def execute(ctx):
        try:
            await task.execute(ctx)
        except Exception as e:
            caught.append(e)
            raise
    return replace(task, execute=execute)


def _without_context(error: BaseException) -> bool:
    """No other exception (one holding seed text) travels with ``error``."""
    return error.__context__ is None and error.__cause__ is None


def _messages(proc: dict) -> set[str]:
    return {e.get("message") for e in proc.get("log", [])}


async def _until(predicate, timeout: float = 60.0) -> None:
    loop = asyncio.get_running_loop()
    end = loop.time() + timeout
    while not predicate():
        if loop.time() > end:
            raise AssertionError(f"condition not met within {timeout:g} s")
        await asyncio.sleep(0.05)


async def test_a_pending_seed_whose_default_passes_becomes_alive(
    task_root, claude_cache_dir, mongo_db, monkeypatch,
):
    _fake(monkeypatch, {KIMI.id: "pass", GLM.id: "error"})
    sid = await _write(mongo_db)
    saved = []
    async with _engine(mongo_db) as optio:
        result, proc = await _run(optio, _task(
            "gw-setup-alive", sid, claude_cache_dir, on_seed_saved=lambda *args: saved.append(args),
        ))

    assert result["status"] == "alive" and result["seedId"] == sid
    assert [(m["id"], m["status"]) for m in result["models"]] == [(KIMI.id, "ok"), (GLM.id, "failed")]
    assert result["models"][0]["reason"] is None
    assert "404" in result["models"][1]["reason"]
    assert saved == [(sid, SUMMARY)]
    assert proc["status"]["state"] == "done"
    # The per-model progress lines are what the consumer shows.
    assert {f"{KIMI.id}: passed", f"{GLM.id}: failed ({ERROR})"} <= _messages(proc)
    doc = await _doc(mongo_db)
    assert doc["status"] == "alive" and isinstance(doc["metadata"]["gateway"]["testedAt"], datetime)
    # The alive seed holds only the models that passed.
    assert [(m["id"], m["status"]) for m in doc["metadata"]["gateway"]["models"]] == [(KIMI.id, "ok")]
    seed = await _read(mongo_db, sid)
    assert [(m.id, m.status, m.reason) for m in seed.models] == [(KIMI.id, "ok", None)]
    assert seed.default_model == KIMI.id and seed.tested_at is not None


@pytest.mark.parametrize(
    ("presets", "kept"),
    [
        ({"fast": GatewayPreset(GLM.id), "extensive": GatewayPreset(KIMI.id, "high")},
         {"extensive": GatewayPreset(KIMI.id, "high")}),
        ({"fast": GatewayPreset(GLM.id)}, None),
    ],
    ids=["one-left", "none-left"],
)
async def test_a_setup_clears_a_preset_whose_model_was_dropped(
    presets, kept, task_root, claude_cache_dir, mongo_db, monkeypatch,
):
    """Consumers fall back to effective_default_model() for a cleared preset;
    a preset naming a passing model keeps its effort."""
    _fake(monkeypatch, {KIMI.id: "pass", GLM.id: "error"})
    sid = await _write(mongo_db, presets=presets)
    async with _engine(mongo_db) as optio:
        result, _proc = await _run(optio, _task("gw-setup-presets", sid, claude_cache_dir))

    assert result["status"] == "alive"
    assert (await _read(mongo_db, sid)).presets == kept
    assert (await _doc(mongo_db))["metadata"]["gateway"]["presets"] == (
        None if kept is None else {k: {"model": p.model, "effort": p.effort} for k, p in kept.items()}
    )


async def test_a_pending_seed_whose_default_fails_is_removed(
    task_root, claude_cache_dir, mongo_db, monkeypatch,
):
    # The fake appends the token it got to the default's error.
    _fake(monkeypatch, {KIMI.id: "error-with-token", GLM.id: "pass"})
    sid = await _write(mongo_db)
    blob_id = (await _doc(mongo_db))["blobId"]
    saved, caught = [], []
    async with _engine(mongo_db) as optio:
        proc = await _run_failing(optio, _catching(_task(
            "gw-setup-removed", sid, claude_cache_dir, on_seed_saved=lambda *args: saved.append(args),
        ), caught))

    assert proc["status"]["state"] == "failed"
    assert proc["status"]["error"] == f"default model {KIMI.id}: {ERROR} ***"
    assert [type(e) for e in caught] == [RuntimeError] and _without_context(caught[0])
    assert f"{GLM.id}: passed" in _messages(proc)
    assert TOKEN not in json.dumps(proc, default=str)
    assert await _doc(mongo_db) is None
    assert await mongo_db["fs.files"].find_one({"_id": blob_id}) is None
    assert saved == []


async def test_test_again_marks_the_failing_model_and_keeps_the_seed_alive(
    task_root, claude_cache_dir, mongo_db, monkeypatch,
):
    _fake(monkeypatch, {KIMI.id: "pass", GLM.id: "error"})
    presets = {"fast": GatewayPreset(GLM.id), "extensive": GatewayPreset(KIMI.id, "high")}
    sid = await _write(mongo_db, status="alive", models=TESTED, presets=presets)
    saved = []

    async def on_seed_saved(seed_id, summary):        # an async callback is awaited
        await asyncio.sleep(0)
        saved.append((seed_id, summary))

    async with _engine(mongo_db) as optio:
        result, proc = await _run(optio, _task(
            "gw-retest-alive", sid, claude_cache_dir, retest=True, on_seed_saved=on_seed_saved,
        ))

    assert result == {"seedId": sid, "status": "alive", "models": [
        {"id": KIMI.id, "status": "ok", "reason": None},
        {"id": GLM.id, "status": "failed", "reason": ERROR},
    ]}
    assert saved == [(sid, SUMMARY)]
    assert proc["status"]["state"] == "done"
    assert (await _doc(mongo_db))["status"] == "alive"
    seed = await _read(mongo_db, sid)
    # The failing model stays in the seed, unusable; the presets stay as they are.
    assert [(m.id, m.status, m.reason) for m in seed.models] == [(KIMI.id, "ok", None), (GLM.id, "failed", ERROR)]
    assert seed.presets == presets


async def test_test_again_with_every_model_failing_marks_the_seed_dead(
    task_root, claude_cache_dir, mongo_db, monkeypatch,
):
    _fake(monkeypatch, {KIMI.id: "error", GLM.id: "wrong"})
    sid = await _write(mongo_db, status="alive", models=TESTED)
    saved = []
    async with _engine(mongo_db) as optio:
        result, proc = await _run(optio, _task(
            "gw-retest-dead", sid, claude_cache_dir, retest=True,
            on_seed_saved=lambda *args: saved.append(args),
        ))

    assert result["status"] == "dead" and result["seedId"] == sid
    assert [(m["id"], m["status"]) for m in result["models"]] == [(KIMI.id, "failed"), (GLM.id, "failed")]
    # The consumer resyncs its tasks when a retest kills the seed.
    assert saved == [(sid, SUMMARY)]
    assert proc["status"]["state"] == "done"
    assert (await _doc(mongo_db))["status"] == "dead"
    seed = await _read(mongo_db, sid)
    assert seed.usable_models() == () and [m.id for m in seed.models] == [KIMI.id, GLM.id]


async def test_an_on_seed_saved_that_raises_fails_the_run_and_keeps_the_seed(
    task_root, claude_cache_dir, mongo_db, monkeypatch,
):
    """The seed is written before the callback runs: it stays alive (and
    unassigned) rather than being removed."""
    _fake(monkeypatch, {KIMI.id: "pass", GLM.id: "pass"})
    sid = await _write(mongo_db)

    def on_seed_saved(seed_id, summary):
        raise RuntimeError("pool assignment failed")

    async with _engine(mongo_db) as optio:
        proc = await _run_failing(optio, _task(
            "gw-setup-callback", sid, claude_cache_dir, on_seed_saved=on_seed_saved,
        ))

    assert proc["status"]["state"] == "failed"
    assert proc["status"]["error"] == "pool assignment failed"
    doc = await _doc(mongo_db)
    assert doc["status"] == "alive"
    assert [m["id"] for m in doc["metadata"]["gateway"]["models"]] == [KIMI.id, GLM.id]


@pytest.mark.parametrize(
    ("status", "retest", "expected"),
    [
        ("pending", True, "gateway seed {sid} is pending: Test again needs an alive or dead seed"),
        ("alive", False, "gateway seed {sid} is alive: the setup needs a pending seed "
                         "(Test again is retest=True)"),
        (None, False, "unknown gateway seed {sid}"),
    ],
    ids=["retest-pending", "setup-alive", "unknown"],
)
async def test_the_flow_must_fit_the_seed_status(
    status, retest, expected, task_root, claude_cache_dir, mongo_db,
):
    sid = str(ObjectId()) if status is None else await _write(mongo_db, status=status)
    before = await _doc(mongo_db)
    async with _engine(mongo_db) as optio:
        proc = await _run_failing(optio, _task("gw-setup-mismatch", sid, claude_cache_dir, retest=retest))

    assert proc["status"]["state"] == "failed"
    assert proc["status"]["error"] == expected.format(sid=sid)
    # A refused flow leaves the seed as it was.
    assert await _doc(mongo_db) == before


async def test_a_cancelled_setup_removes_its_pending_seed(
    task_root, claude_cache_dir, mongo_db, monkeypatch, tmp_path,
):
    """RF5: no pending seed, no task directory and no claude process stay
    behind a cancelled setup."""
    _fake(monkeypatch, {KIMI.id: "hang", GLM.id: "hang"})
    started = tmp_path / "started"
    started.mkdir()
    monkeypatch.setenv("FAKE_CLAUDE_STARTED_DIR", str(started))
    sid = await _write(mongo_db)
    pid = "gw-setup-cancelled"
    async with _engine(mongo_db) as optio:
        await optio.adhoc_define(_task(pid, sid, claude_cache_dir))
        await optio.launch(pid, session_id=None)
        await _until(lambda: os.listdir(started))
        await optio.cancel(pid)
        proc = await _wait_terminal(optio, pid)

    assert proc["status"]["state"] == "cancelled"
    assert await _doc(mongo_db) is None
    assert os.listdir(task_root) == []
    for name in os.listdir(started):
        with pytest.raises(ProcessLookupError):
            os.kill(int(name), 0)


async def test_a_setup_that_fails_before_writing_removes_its_pending_seed(
    task_root, claude_cache_dir, mongo_db,
):
    sid = await _write(mongo_db)
    caught = []
    async with _engine(mongo_db) as optio:
        proc = await _run_failing(optio, _catching(_task(
            "gw-setup-unreadable", sid, claude_cache_dir, decrypt=lambda data: data,
        ), caught))

    assert proc["status"]["state"] == "failed"
    assert proc["status"]["error"] == f"gateway seed {sid!r}: the blob is not JSON (wrong cipher?)"
    # The removal in the finally adds nothing to the error.
    assert [type(e) for e in caught] == [ValueError] and _without_context(caught[0])
    assert await _doc(mongo_db) is None


async def test_verify_and_refresh_seed_refuses_a_gateway_seed(mongo_db):
    sid = await _write(mongo_db, status="alive", models=TESTED)
    with pytest.raises(ValueError, match="setup task"):
        await oauth.verify_and_refresh_seed(
            mongo_db, prefix=PREFIX, suffix=REQUESTY_SEED_SUFFIX, seed_id=sid,
            encrypt=_cipher, decrypt=_cipher,
        )
    assert (await _doc(mongo_db))["status"] == "alive"
