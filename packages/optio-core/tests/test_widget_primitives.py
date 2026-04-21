import asyncio
import pytest
from bson import ObjectId
from optio_core.models import TaskInstance, BasicAuth, QueryAuth, HeaderAuth
from optio_core.store import upsert_process, update_widget_upstream, clear_widget_upstream, update_widget_data, clear_result_fields
from optio_core.context import ProcessContext
from optio_core.executor import Executor


def _make_test_context(db, proc, *, process_id: str | None = None) -> ProcessContext:
    """Minimal ProcessContext for unit tests that only exercise DB-mutation methods."""
    return ProcessContext(
        process_oid=proc["_id"],
        process_id=process_id or proc["processId"],
        root_oid=proc["_id"],
        depth=0,
        params={},
        services={},
        db=db,
        prefix="test",
        cancellation_flag=asyncio.Event(),
        child_counter={},
        metadata={},
    )


@pytest.mark.asyncio
async def test_upsert_persists_ui_widget(mongo_db):
    async def noop(ctx):
        pass

    task = TaskInstance(
        execute=noop,
        process_id="widget-task",
        name="Widget Task",
        ui_widget="iframe",
    )
    result = await upsert_process(mongo_db, "test", task)
    assert result["uiWidget"] == "iframe"


@pytest.mark.asyncio
async def test_upsert_ui_widget_absent_when_unset(mongo_db):
    async def noop(ctx):
        pass

    task = TaskInstance(execute=noop, process_id="plain-task", name="Plain Task")
    result = await upsert_process(mongo_db, "test", task)
    assert result.get("uiWidget") is None


@pytest.mark.asyncio
async def test_widget_upstream_round_trip_basic_auth(mongo_db):
    async def noop(ctx):
        pass

    task = TaskInstance(execute=noop, process_id="u1", name="U1")
    proc = await upsert_process(mongo_db, "test", task)
    oid = proc["_id"]

    await update_widget_upstream(
        mongo_db, "test", oid,
        url="http://127.0.0.1:9000",
        inner_auth=BasicAuth(username="u", password="p"),
    )

    doc = await mongo_db["test_processes"].find_one({"_id": oid})
    assert doc["widgetUpstream"]["url"] == "http://127.0.0.1:9000"
    assert doc["widgetUpstream"]["innerAuth"] == {
        "kind": "basic", "username": "u", "password": "p",
    }


@pytest.mark.asyncio
async def test_widget_upstream_round_trip_query_auth(mongo_db):
    async def noop(ctx):
        pass

    task = TaskInstance(execute=noop, process_id="u2", name="U2")
    proc = await upsert_process(mongo_db, "test", task)
    oid = proc["_id"]

    await update_widget_upstream(
        mongo_db, "test", oid,
        url="http://127.0.0.1:9000",
        inner_auth=QueryAuth(name="tok", value="secret"),
    )

    doc = await mongo_db["test_processes"].find_one({"_id": oid})
    assert doc["widgetUpstream"]["innerAuth"] == {
        "kind": "query", "name": "tok", "value": "secret",
    }


@pytest.mark.asyncio
async def test_widget_upstream_round_trip_header_auth(mongo_db):
    async def noop(ctx):
        pass

    task = TaskInstance(execute=noop, process_id="u3", name="U3")
    proc = await upsert_process(mongo_db, "test", task)
    oid = proc["_id"]

    await update_widget_upstream(
        mongo_db, "test", oid,
        url="http://127.0.0.1:9000",
        inner_auth=HeaderAuth(name="X-Tok", value="s"),
    )

    doc = await mongo_db["test_processes"].find_one({"_id": oid})
    assert doc["widgetUpstream"]["innerAuth"] == {
        "kind": "header", "name": "X-Tok", "value": "s",
    }


@pytest.mark.asyncio
async def test_widget_upstream_clear_sets_null(mongo_db):
    async def noop(ctx):
        pass

    task = TaskInstance(execute=noop, process_id="u4", name="U4")
    proc = await upsert_process(mongo_db, "test", task)
    oid = proc["_id"]

    await update_widget_upstream(mongo_db, "test", oid, url="http://x")
    await clear_widget_upstream(mongo_db, "test", oid)

    doc = await mongo_db["test_processes"].find_one({"_id": oid})
    assert doc["widgetUpstream"] is None


@pytest.mark.asyncio
async def test_process_context_set_widget_upstream(mongo_db):
    async def noop(ctx):
        pass

    task = TaskInstance(execute=noop, process_id="c1", name="C1")
    proc = await upsert_process(mongo_db, "test", task)

    ctx = _make_test_context(mongo_db, proc)
    await ctx.set_widget_upstream(
        "http://127.0.0.1:9000",
        inner_auth=HeaderAuth(name="X-Tok", value="s"),
    )

    doc = await mongo_db["test_processes"].find_one({"_id": proc["_id"]})
    assert doc["widgetUpstream"]["url"] == "http://127.0.0.1:9000"
    assert doc["widgetUpstream"]["innerAuth"]["kind"] == "header"

    await ctx.clear_widget_upstream()
    doc = await mongo_db["test_processes"].find_one({"_id": proc["_id"]})
    assert doc["widgetUpstream"] is None


@pytest.mark.asyncio
async def test_widget_data_round_trip_nested_json(mongo_db):
    async def noop(ctx):
        pass

    task = TaskInstance(execute=noop, process_id="d1", name="D1")
    proc = await upsert_process(mongo_db, "test", task)
    ctx = _make_test_context(mongo_db, proc)

    payload = {
        "localStorageOverrides": {
            "opencode.settings.dat": '{"defaultServerUrl":"/api/widget/abc/"}',
        },
        "allow": "clipboard-read",
        "custom_worker_key": [1, 2, 3],
    }
    await ctx.set_widget_data(payload)

    doc = await mongo_db["test_processes"].find_one({"_id": proc["_id"]})
    assert doc["widgetData"] == payload


@pytest.mark.asyncio
async def test_widget_data_clear_sets_null(mongo_db):
    async def noop(ctx):
        pass

    task = TaskInstance(execute=noop, process_id="d2", name="D2")
    proc = await upsert_process(mongo_db, "test", task)
    ctx = _make_test_context(mongo_db, proc)

    await ctx.set_widget_data({"a": 1})
    await ctx.clear_widget_data()

    doc = await mongo_db["test_processes"].find_one({"_id": proc["_id"]})
    assert doc["widgetData"] is None


@pytest.mark.asyncio
async def test_widget_upstream_cleared_on_done(mongo_db):
    async def task_setting_upstream(ctx):
        await ctx.set_widget_upstream("http://127.0.0.1:9000")
        await asyncio.sleep(0)  # yield so write lands

    task = TaskInstance(execute=task_setting_upstream, process_id="t-done", name="T")
    await upsert_process(mongo_db, "test", task)
    executor = Executor(mongo_db, "test", {})
    executor.register_tasks([task])

    result = await executor.launch_process("t-done")
    assert result == "done"

    doc = await mongo_db["test_processes"].find_one({"processId": "t-done"})
    assert doc["widgetUpstream"] is None


@pytest.mark.asyncio
async def test_widget_upstream_cleared_on_failed(mongo_db):
    async def task_fails(ctx):
        await ctx.set_widget_upstream("http://127.0.0.1:9000")
        raise RuntimeError("boom")

    task = TaskInstance(execute=task_fails, process_id="t-fail", name="T")
    await upsert_process(mongo_db, "test", task)
    executor = Executor(mongo_db, "test", {})
    executor.register_tasks([task])

    result = await executor.launch_process("t-fail")
    assert result == "failed"

    doc = await mongo_db["test_processes"].find_one({"processId": "t-fail"})
    assert doc["widgetUpstream"] is None


@pytest.mark.asyncio
async def test_widget_upstream_cleared_on_cancelled(mongo_db):
    started = asyncio.Event()

    async def task_waits(ctx):
        await ctx.set_widget_upstream("http://127.0.0.1:9000")
        started.set()
        while ctx.should_continue():
            await asyncio.sleep(0.01)

    task = TaskInstance(execute=task_waits, process_id="t-cancel", name="T")
    await upsert_process(mongo_db, "test", task)
    executor = Executor(mongo_db, "test", {})
    executor.register_tasks([task])

    run = asyncio.create_task(executor.launch_process("t-cancel"))
    await started.wait()
    doc = await mongo_db["test_processes"].find_one({"processId": "t-cancel"})
    executor.request_cancel(doc["_id"])
    result = await run
    assert result == "cancelled"

    doc = await mongo_db["test_processes"].find_one({"processId": "t-cancel"})
    assert doc["widgetUpstream"] is None


@pytest.mark.asyncio
async def test_clear_result_fields_clears_widget_data_and_upstream(mongo_db):
    async def noop(ctx):
        pass

    task = TaskInstance(execute=noop, process_id="r1", name="R1")
    proc = await upsert_process(mongo_db, "test", task)
    oid = proc["_id"]

    await update_widget_data(mongo_db, "test", oid, {"a": 1})
    await update_widget_upstream(mongo_db, "test", oid, url="http://x")

    await clear_result_fields(mongo_db, "test", oid)

    doc = await mongo_db["test_processes"].find_one({"_id": oid})
    assert doc["widgetData"] is None
    assert doc["widgetUpstream"] is None
