"""The engine-wide conversation archive listener.

Spec: docs/2026-10-12-suspended-conversation-view-design.md §1.
"""

import asyncio
import base64
import logging
import time
from datetime import datetime, timezone

import aiohttp
import pytest
from bson import ObjectId

from optio_core.context import ProcessContext
from optio_core.conversation_archive import ConversationArchiveListener
from optio_core.lifecycle import Optio
from optio_core.models import TaskInstance

ENDPOINT_ID = "conversationArchive"
_HANG = 30.0  # a hang ceiling only


async def _noop(ctx):
    pass


async def _wait_until(pred, timeout=_HANG):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if await pred():
            return
        await asyncio.sleep(0.02)
    raise AssertionError("condition not reached within the hang ceiling")


def _task(process_id="t1", read_conversation=None):
    return TaskInstance(
        execute=_noop, process_id=process_id, name=process_id,
        supports_resume=True, read_conversation=read_conversation,
    )


async def _insert_proc(mongo_db, prefix, *, process_id="t1", state="cancelled",
                       supports_resume=True, has_saved_state=True):
    oid = ObjectId()
    await mongo_db[f"{prefix}_processes"].insert_one({
        "_id": oid, "processId": process_id, "name": process_id,
        "status": {"state": state},
        "supportsResume": supports_resume, "hasSavedState": has_saved_state,
        "depth": 0, "params": {}, "metadata": {},
    })
    return oid


class _Ctx:
    """Stand-in for the detached ProcessContext: records the doc it came from."""

    def __init__(self, doc):
        self.doc = doc
        self.process_id = doc["processId"]


async def _started(mongo_db, prefix, tasks=None, make_context=_Ctx):
    tasks = tasks or {}
    listener = ConversationArchiveListener(
        db=mongo_db, prefix=prefix, resolve_task=tasks.get, make_context=make_context,
    )
    await listener.start()
    return listener


def _basic(user, password):
    token = base64.b64encode(f"{user}:{password}".encode()).decode()
    return {"Authorization": f"Basic {token}"}


async def _get(listener, oid, *, auth="right"):
    if auth == "right":
        headers = _basic("optio", listener.password)
    else:
        headers = auth or {}
    async with aiohttp.ClientSession() as s:
        async with s.get(f"{listener.url}conversation/{oid}", headers=headers) as r:
            return r.status, await r.json(), dict(r.headers)


async def test_publishes_and_unpublishes_endpoint(mongo_db):
    listener = await _started(mongo_db, "ca1")
    endpoints = mongo_db["ca1_engine_endpoints"]
    try:
        doc = await endpoints.find_one({"_id": ENDPOINT_ID})
        assert doc is not None
        assert doc["url"] == listener.url
        assert doc["url"].startswith("http://127.0.0.1:") and doc["url"].endswith("/")
        assert doc["password"] == listener.password and len(doc["password"]) >= 32
        assert isinstance(doc["updatedAt"], datetime)
        status, body, _ = await _get(listener, ObjectId())
        assert (status, body["reason"]) == (404, "not-found")   # the url answers
    finally:
        await listener.stop()
    assert await endpoints.find_one({"_id": ENDPOINT_ID}) is None
    with pytest.raises(aiohttp.ClientConnectionError):
        await _get(listener, ObjectId())


async def test_advertised_host_and_bind_come_from_env(mongo_db, monkeypatch):
    monkeypatch.setenv("OPTIO_WIDGET_TUNNEL_BIND", "127.0.0.1")
    monkeypatch.setenv("OPTIO_WIDGET_TUNNEL_HOST", "engine.internal")
    listener = await _started(mongo_db, "ca2")
    try:
        doc = await mongo_db["ca2_engine_endpoints"].find_one({"_id": ENDPOINT_ID})
        assert doc["url"].startswith("http://engine.internal:")
        assert listener.url == doc["url"]
    finally:
        await listener.stop()


async def test_stop_keeps_a_newer_engines_document(mongo_db):
    old = await _started(mongo_db, "ca3")
    new = await _started(mongo_db, "ca3")  # e.g. a restart overlapping the old engine
    endpoints = mongo_db["ca3_engine_endpoints"]
    try:
        await old.stop()
        doc = await endpoints.find_one({"_id": ENDPOINT_ID})
        assert doc is not None and doc["password"] == new.password
    finally:
        await new.stop()
    assert await endpoints.find_one({"_id": ENDPOINT_ID}) is None


async def test_requires_basic_auth(mongo_db):
    async def hook(ctx):
        raise AssertionError("must not run")
    oid = await _insert_proc(mongo_db, "ca4")
    listener = await _started(mongo_db, "ca4", {"t1": _task(read_conversation=hook)})
    try:
        for auth in (
            None,
            _basic("optio", "wrong"),
            _basic("someone", listener.password),
            {"Authorization": "Basic not-base64!"},
            {"Authorization": f"Bearer {listener.password}"},
        ):
            status, body, headers = await _get(listener, oid, auth=auth)
            assert status == 401
            assert body["reason"] == "unauthorized" and "message" in body
            assert headers["Cache-Control"] == "no-store"
    finally:
        await listener.stop()


async def test_unknown_process_404(mongo_db):
    listener = await _started(mongo_db, "ca5")
    try:
        for pid in (ObjectId(), "not-an-object-id"):
            status, body, _ = await _get(listener, pid)
            assert status == 404
            assert body["reason"] == "not-found" and body["message"]
    finally:
        await listener.stop()


@pytest.mark.parametrize("fields", [
    {"state": "running"},
    {"state": "cancelled", "has_saved_state": False},
    {"state": "cancelled", "supports_resume": False},
    {"state": "done"},
])
async def test_not_suspended_409(mongo_db, fields):
    async def hook(ctx):
        raise AssertionError("must not run")
    oid = await _insert_proc(mongo_db, "ca6", **fields)
    listener = await _started(mongo_db, "ca6", {"t1": _task(read_conversation=hook)})
    try:
        status, body, _ = await _get(listener, oid)
        assert (status, body["reason"]) == (409, "not-suspended")
    finally:
        await listener.stop()


async def test_no_task_or_hook_404(mongo_db):
    no_task = await _insert_proc(mongo_db, "ca7", process_id="gone")
    no_hook = await _insert_proc(mongo_db, "ca7", process_id="plain")
    listener = await _started(mongo_db, "ca7", {"plain": _task("plain")})
    try:
        for oid in (no_task, no_hook):
            status, body, _ = await _get(listener, oid)
            assert (status, body["reason"]) == (404, "no-conversation-support")
    finally:
        await listener.stop()


async def test_hook_none_404(mongo_db):
    async def hook(ctx):
        return None
    oid = await _insert_proc(mongo_db, "ca8")
    listener = await _started(mongo_db, "ca8", {"t1": _task(read_conversation=hook)})
    try:
        status, body, _ = await _get(listener, oid)
        assert (status, body["reason"]) == (404, "no-conversation")
    finally:
        await listener.stop()


async def test_returns_events_no_store(mongo_db):
    seen = []

    async def hook(ctx):
        seen.append(ctx)
        return [[1, {"type": "user"}]]
    oid = await _insert_proc(mongo_db, "ca9")
    listener = await _started(mongo_db, "ca9", {"t1": _task(read_conversation=hook)})
    try:
        status, body, headers = await _get(listener, oid)
    finally:
        await listener.stop()
    assert status == 200
    assert body == {"events": [[1, {"type": "user"}]], "capturedAt": None}
    assert headers["Cache-Control"] == "no-store"
    # The hook got the context built from this process's document.
    assert len(seen) == 1 and seen[0].doc["_id"] == oid


async def test_dict_result_carries_captured_at(mongo_db):
    results = iter([
        {"events": [(1, {"type": "user"}), (2, {"type": "assistant"})],
         "capturedAt": "2026-10-12T08:00:00+00:00"},
        # A naive datetime (Mongo's default) is UTC.
        {"events": [], "capturedAt": datetime(2026, 10, 12, 8, 0, 0)},
        {"events": [], "capturedAt": datetime(2026, 10, 12, 8, 0, 0, tzinfo=timezone.utc)},
        {"events": [], "capturedAt": None},
    ])

    async def hook(ctx):
        return next(results)
    oid = await _insert_proc(mongo_db, "ca10")
    listener = await _started(mongo_db, "ca10", {"t1": _task(read_conversation=hook)})
    try:
        bodies = [(await _get(listener, oid))[1] for _ in range(4)]
    finally:
        await listener.stop()
    assert bodies[0] == {
        "events": [[1, {"type": "user"}], [2, {"type": "assistant"}]],
        "capturedAt": "2026-10-12T08:00:00+00:00",
    }
    assert bodies[1]["capturedAt"] == "2026-10-12T08:00:00+00:00"
    assert bodies[2]["capturedAt"] == "2026-10-12T08:00:00+00:00"
    assert bodies[3] == {"events": [], "capturedAt": None}


async def test_hook_raises_500_without_content_in_logs(mongo_db, caplog):
    async def hook(ctx):
        raise ValueError("SECRET-CONTENT")
    oid = await _insert_proc(mongo_db, "ca11", process_id="secret-task")
    listener = await _started(mongo_db, "ca11", {"secret-task": _task("secret-task", hook)})
    # Ours and aiohttp's (access log, server errors) at every level. (pymongo's
    # own DEBUG command log echoes documents, the published password included;
    # it is off unless an app turns it on.)
    caplog.set_level(logging.DEBUG, logger="optio_core")
    caplog.set_level(logging.DEBUG, logger="aiohttp")
    try:
        status, body, headers = await _get(listener, oid)
    finally:
        await listener.stop()
    assert (status, body["reason"]) == (500, "read-failed")
    assert headers["Cache-Control"] == "no-store"
    assert "SECRET-CONTENT" not in str(body)
    assert str(oid) in caplog.text and "secret-task" in caplog.text
    assert "ValueError" in caplog.text
    assert "SECRET-CONTENT" not in caplog.text
    assert listener.password not in caplog.text


async def test_unserializable_result_is_read_failed(mongo_db, caplog):
    async def hook(ctx):
        return [[1, {"type": "user", "payload": object()}]]
    oid = await _insert_proc(mongo_db, "ca12")
    listener = await _started(mongo_db, "ca12", {"t1": _task(read_conversation=hook)})
    try:
        status, body, _ = await _get(listener, oid)
    finally:
        await listener.stop()
    assert (status, body["reason"]) == (500, "read-failed")


# -- Optio wiring -------------------------------------------------------------


async def test_detached_context_shared_with_resurrect(mongo_db, monkeypatch):
    built = []
    real = Optio._detached_context

    def spy(self, proc, **kwargs):
        ctx = real(self, proc, **kwargs)
        built.append((proc["_id"], kwargs, ctx))
        return ctx
    monkeypatch.setattr(Optio, "_detached_context", spy)

    got = []

    async def resurrect_hook(ctx):
        got.append(ctx)
    fw = Optio()
    await fw.init(mongo_db=mongo_db, prefix="ca13")
    proc = await fw.adhoc_define(TaskInstance(
        execute=_noop, process_id="r1", name="R1", supports_resume=True,
        resurrect=resurrect_hook, params={"p": 1}, metadata={"m": "x"},
    ))
    coll = mongo_db["ca13_processes"]
    await coll.update_one({"_id": proc["_id"]}, {"$set": {
        "status.state": "failed", "hasUnsavedWork": True, "hasSavedState": True,
    }})

    async def _no_launch(oid, *, resume, session_id):
        pass
    fw._executor.launch_process = _no_launch
    assert (await fw.resurrect("r1", session_id="s1")).ok is True

    async def ran():
        return bool(got) and proc["_id"] not in fw._resurrecting
    await _wait_until(ran)
    assert len(built) == 1
    oid, kwargs, ctx = built[0]
    assert oid == proc["_id"] and kwargs == {"session_id": "s1"}
    assert got[0] is ctx
    assert isinstance(ctx, ProcessContext)
    assert ctx.process_id == "r1" and ctx.session_id == "s1" and ctx.resume is False
    assert ctx.params == {"p": 1} and ctx.metadata == {"m": "x"}
    assert ctx._executor is fw._executor


async def test_run_starts_and_shutdown_stops_listener(mongo_db):
    seen = []

    async def read_conversation(ctx):
        seen.append(ctx)
        return {"events": [[7, {"type": "assistant"}]], "capturedAt": "2026-10-12T00:00:00+00:00"}

    async def get_tasks(services, metadata_filter=None):
        return [_task("conv", read_conversation)]

    fw = Optio()
    await fw.init(mongo_db=mongo_db, prefix="ca14", get_task_definitions=get_tasks)
    endpoints = mongo_db["ca14_engine_endpoints"]
    assert await endpoints.find_one({"_id": ENDPOINT_ID}) is None  # not before run()
    runner = asyncio.create_task(fw.run())
    try:
        async def published():
            return await endpoints.find_one({"_id": ENDPOINT_ID}) is not None
        await _wait_until(published)
        endpoint = await endpoints.find_one({"_id": ENDPOINT_ID})

        coll = mongo_db["ca14_processes"]
        proc = await coll.find_one({"processId": "conv"})
        await coll.update_one({"_id": proc["_id"]}, {"$set": {
            "status.state": "cancelled", "hasSavedState": True,
        }})
        async with aiohttp.ClientSession() as s:
            async with s.get(
                f"{endpoint['url']}conversation/{proc['_id']}",
                headers=_basic("optio", endpoint["password"]),
            ) as r:
                assert r.status == 200
                assert await r.json() == {
                    "events": [[7, {"type": "assistant"}]],
                    "capturedAt": "2026-10-12T00:00:00+00:00",
                }
        ctx = seen[0]
        assert isinstance(ctx, ProcessContext)
        assert ctx.process_id == "conv" and ctx.resume is False and ctx.session_id is None
    finally:
        await fw.shutdown()
        await asyncio.wait_for(runner, _HANG)
    assert await endpoints.find_one({"_id": ENDPOINT_ID}) is None


async def test_listener_starts_before_rpc_server(mongo_db, redis_url):
    fw = Optio()
    await fw.init(mongo_db=mongo_db, prefix="ca15", redis_url=redis_url)
    endpoints = mongo_db["ca15_engine_endpoints"]
    at_rpc_start = []
    rpc_started = asyncio.Event()
    real_start = fw.rpc_server.start

    async def spy_start(*args, **kwargs):
        at_rpc_start.append(await endpoints.find_one({"_id": ENDPOINT_ID}))
        try:
            return await real_start(*args, **kwargs)
        finally:
            rpc_started.set()
    fw.rpc_server.start = spy_start
    runner = asyncio.create_task(fw.run())
    try:
        await asyncio.wait_for(rpc_started.wait(), _HANG)
    finally:
        await fw.shutdown()
        await asyncio.wait_for(runner, _HANG)
    assert at_rpc_start[0] is not None
    assert await endpoints.find_one({"_id": ENDPOINT_ID}) is None


async def test_listener_start_failure_does_not_stop_the_engine(mongo_db, monkeypatch, caplog):
    monkeypatch.setenv("OPTIO_WIDGET_TUNNEL_BIND", "256.0.0.1")  # cannot bind
    fw = Optio()
    await fw.init(mongo_db=mongo_db, prefix="ca16")
    runner = asyncio.create_task(fw.run())
    try:
        async def running():
            return fw._supervisor_task is not None
        await _wait_until(running)
        assert not runner.done()
    finally:
        await fw.shutdown()
        await asyncio.wait_for(runner, _HANG)
    assert await mongo_db["ca16_engine_endpoints"].find_one({"_id": ENDPOINT_ID}) is None
    assert "conversation archive" in caplog.text
