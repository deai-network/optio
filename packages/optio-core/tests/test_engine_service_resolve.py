"""Exhaustive coverage for Optio._resolve.

Phase 5 lifted _resolve from the OptioEngineService adapter onto Optio
itself, so both the public API and the RPC adapter share one resolver.
Pin the resolution semantics here.

These tests use MagicMock for the Mongo collection (matching the pattern
in test_engine_service.py) — _resolve is a thin wrapper around two
find_one calls, and the branch under test is the input-routing logic.
"""

from __future__ import annotations

import re

import pytest
from unittest.mock import AsyncMock, MagicMock
from bson import ObjectId

from optio_core._engine_service import OptioEngineService
from optio_core._generated.optio_engine import LaunchParams
from optio_core.lifecycle import Optio


_OBJECTID_RE = re.compile(r"^[a-fA-F0-9]{24}$")


def _make_optio(coll: AsyncMock) -> Optio:
    """Construct an Optio with `_config.mongo_db` wired to `coll`."""
    optio = Optio()
    optio._config = MagicMock()
    db = MagicMock()
    db.__getitem__.return_value = coll
    optio._config.mongo_db = db
    optio._config.prefix = "test"
    return optio


def _make_service(coll: AsyncMock) -> OptioEngineService:
    """Smoke-helper: construct a service whose backing Optio resolves via `coll`.

    The adapter's _resolve was lifted to Optio in phase 5; the integration
    smoke tests at the bottom of this file still verify that the adapter
    delegates correctly. Build a MagicMock optio with a fake_resolve that
    mirrors Optio._resolve's logic, plus the bare attributes the adapter's
    launch() reads.
    """
    optio = MagicMock()
    optio._config = MagicMock()
    db = MagicMock()
    db.__getitem__.return_value = coll
    optio._config.mongo_db = db
    optio._config.prefix = "test"

    async def fake_resolve(id_str):
        if _OBJECTID_RE.match(id_str):
            doc = await coll.find_one({"_id": ObjectId(id_str)})
            if doc:
                return doc
        return await coll.find_one({"processId": id_str})
    optio._resolve = fake_resolve

    optio.launch = AsyncMock(return_value=None)
    return OptioEngineService(optio)


def _sample_proc(process_id: str = "alpha") -> dict:
    """Wire-shape proc doc sufficient for _resolve to return it."""
    oid = ObjectId()
    return {
        "_id": oid,
        "processId": process_id,
        "name": "test",
        "supportsResume": False,
        "cancellable": True,
        "status": {"state": "idle"},
        "progress": {"percent": None, "message": None},
        "log": [],
        "rootId": str(oid),
        "depth": 0,
        "order": 0,
        "createdAt": "2026-05-11T00:00:00+00:00",
        "metadata": {},
    }


# --- ObjectId hex branch ---


@pytest.mark.asyncio
async def test_resolve_by_object_id_hex_hits_id_branch():
    """24-char hex input queries _id; if a doc matches there, returns it."""
    proc = _sample_proc()
    coll = AsyncMock()
    coll.find_one = AsyncMock(return_value=proc)
    fw = _make_optio(coll)

    result = await fw._resolve(str(proc["_id"]))

    assert result is proc
    coll.find_one.assert_awaited_once()
    query = coll.find_one.call_args_list[0][0][0]
    assert "_id" in query
    assert isinstance(query["_id"], ObjectId)
    assert query["_id"] == proc["_id"]


# --- processId-string branch ---


@pytest.mark.asyncio
async def test_resolve_by_process_id_string_hits_processid_branch():
    """Non-hex string input skips the _id branch and queries processId."""
    proc = _sample_proc(process_id="my-task")
    coll = AsyncMock()
    coll.find_one = AsyncMock(return_value=proc)
    fw = _make_optio(coll)

    result = await fw._resolve("my-task")

    assert result is proc
    coll.find_one.assert_awaited_once()
    query = coll.find_one.call_args_list[0][0][0]
    assert query == {"processId": "my-task"}


# --- Misses ---


@pytest.mark.asyncio
async def test_resolve_unknown_object_id_returns_none():
    """Hex input matching no _id AND no processId returns None."""
    coll = AsyncMock()
    coll.find_one = AsyncMock(return_value=None)
    fw = _make_optio(coll)

    result = await fw._resolve(str(ObjectId()))

    assert result is None
    # Both branches were tried.
    assert coll.find_one.await_count == 2


@pytest.mark.asyncio
async def test_resolve_unknown_process_id_returns_none():
    """Non-hex string with no matching processId returns None."""
    coll = AsyncMock()
    coll.find_one = AsyncMock(return_value=None)
    fw = _make_optio(coll)

    result = await fw._resolve("nope-not-here")

    assert result is None
    # Only the processId branch was tried (non-hex skips _id lookup).
    coll.find_one.assert_awaited_once()
    query = coll.find_one.call_args_list[0][0][0]
    assert query == {"processId": "nope-not-here"}


@pytest.mark.asyncio
async def test_resolve_empty_string_returns_none():
    """Empty string is non-hex; processId branch lookups it; no match."""
    coll = AsyncMock()
    coll.find_one = AsyncMock(return_value=None)
    fw = _make_optio(coll)

    result = await fw._resolve("")

    assert result is None


# --- Fallback: hex doesn't match _id but matches a processId field ---


@pytest.mark.asyncio
async def test_resolve_hex_falls_through_to_process_id():
    """24-char hex matching no _id but matching some proc's processId
    field returns that proc via the second-branch fallback."""
    fake_hex = str(ObjectId())
    proc = _sample_proc(process_id=fake_hex)
    coll = AsyncMock()
    # First call (_id branch) returns None; second call (processId branch) finds the proc.
    coll.find_one = AsyncMock(side_effect=[None, proc])
    fw = _make_optio(coll)

    result = await fw._resolve(fake_hex)

    assert result is proc
    assert coll.find_one.await_count == 2
    first_query = coll.find_one.call_args_list[0][0][0]
    second_query = coll.find_one.call_args_list[1][0][0]
    assert "_id" in first_query
    assert second_query == {"processId": fake_hex}


# --- Collision: _id wins ---


@pytest.mark.asyncio
async def test_resolve_collision_id_branch_wins():
    """When the input hex matches one proc's _id AND another proc's
    processId field, the _id branch returns first; processId branch
    never runs. Pins current behavior."""
    oid = ObjectId()
    proc_a = _sample_proc(process_id="a-task")
    proc_a["_id"] = oid
    coll = AsyncMock()
    coll.find_one = AsyncMock(return_value=proc_a)
    fw = _make_optio(coll)

    result = await fw._resolve(str(oid))

    assert result is proc_a
    assert result["processId"] == "a-task"
    coll.find_one.assert_awaited_once()  # processId branch not consulted


# --- Integration smoke: launch accepts both id forms ---


@pytest.mark.asyncio
async def test_launch_accepts_object_id_hex_form():
    """OptioEngineService.launch resolves the hex _id form via Optio._resolve."""
    proc = _sample_proc()
    coll = AsyncMock()
    # Two find_one calls in launch(): pre-state read + post-launch read.
    coll.find_one = AsyncMock(side_effect=[proc, {**proc, "status": {"state": "scheduled"}}])
    svc = _make_service(coll)

    result = await svc.launch(LaunchParams.model_validate({"processId": str(proc["_id"])}))

    assert result.root.ok is True
    first_query = coll.find_one.call_args_list[0][0][0]
    assert "_id" in first_query


@pytest.mark.asyncio
async def test_launch_accepts_process_id_string_form():
    """OptioEngineService.launch resolves the processId string form via Optio._resolve."""
    proc = _sample_proc(process_id="launch-by-pid")
    coll = AsyncMock()
    coll.find_one = AsyncMock(side_effect=[proc, {**proc, "status": {"state": "scheduled"}}])
    svc = _make_service(coll)

    result = await svc.launch(LaunchParams.model_validate({"processId": "launch-by-pid"}))

    assert result.root.ok is True
    first_query = coll.find_one.call_args_list[0][0][0]
    assert first_query == {"processId": "launch-by-pid"}
