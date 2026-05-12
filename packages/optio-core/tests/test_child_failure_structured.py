"""Tests for structured child failure propagation."""
import pytest

from optio_core.exceptions import ChildProcessFailed


class _SampleErr(Exception):
    def __init__(self, url: str, exit_code: int):
        self.url = url
        self.exit_code = exit_code
        super().__init__(f"sample error for {url}")


def test_child_process_failed_carries_name_pid_and_original():
    original = _SampleErr("http://example.com", 42)
    cpf = ChildProcessFailed("Sample Child", "child-pid", original)
    assert cpf.name == "Sample Child"
    assert cpf.process_id == "child-pid"
    assert cpf.original is original
    assert isinstance(cpf.original, _SampleErr)
    assert cpf.original.url == "http://example.com"
    assert cpf.original.exit_code == 42


def test_child_process_failed_message_includes_repr_of_original():
    original = _SampleErr("u", 1)
    cpf = ChildProcessFailed("N", "P", original)
    msg = str(cpf)
    assert "N" in msg
    assert "P" in msg
    assert repr(original) in msg


def test_child_process_failed_is_an_exception():
    original = _SampleErr("u", 1)
    cpf = ChildProcessFailed("N", "P", original)
    assert isinstance(cpf, Exception)
    with pytest.raises(ChildProcessFailed):
        raise cpf


from optio_core.models import ChildOutcome


def test_child_outcome_default_no_exception():
    outcome = ChildOutcome(state="done")
    assert outcome.state == "done"
    assert outcome.original_exception is None


def test_child_outcome_with_exception():
    exc = _SampleErr("u", 9)
    outcome = ChildOutcome(state="failed", original_exception=exc)
    assert outcome.state == "failed"
    assert outcome.original_exception is exc


from optio_core.models import ChildResult


def test_child_result_defaults():
    r = ChildResult(process_id="p", state="done")
    assert r.process_id == "p"
    assert r.state == "done"
    assert r.error is None
    assert r.name == ""
    assert r.original_exception is None


def test_child_result_carries_name_and_original():
    exc = _SampleErr("u", 1)
    r = ChildResult(
        process_id="p", state="failed", error="Child failed",
        name="Sample", original_exception=exc,
    )
    assert r.name == "Sample"
    assert r.original_exception is exc


import asyncio
from optio_core.executor import Executor
from optio_core.models import TaskInstance
from optio_core.store import upsert_process


async def test_run_child_raises_child_process_failed_with_original(mongo_db):
    """Child raises a structured exception; parent catches ChildProcessFailed
    and recovers the original via .original."""
    caught = {}

    async def failing_child(ctx):
        raise _SampleErr("http://example.com/bin", 42)

    async def parent_task(ctx):
        try:
            await ctx.run_child(
                failing_child,
                process_id="failing-child-1",
                name="Failing Child",
            )
        except ChildProcessFailed as e:
            caught["name"] = e.name
            caught["process_id"] = e.process_id
            caught["original"] = e.original

    parent_inst = TaskInstance(
        execute=parent_task, process_id="parent-cpf", name="Parent CPF",
    )
    child_inst = TaskInstance(
        execute=failing_child, process_id="failing-child-1", name="Failing Child",
    )

    await upsert_process(mongo_db, "test_cpf", parent_inst)
    executor = Executor(mongo_db, "test_cpf", {})
    executor.register_tasks([parent_inst, child_inst])

    result = await executor.launch_process("parent-cpf")
    assert result == "done"
    assert caught["name"] == "Failing Child"
    assert caught["process_id"] == "failing-child-1"
    assert isinstance(caught["original"], _SampleErr)
    assert caught["original"].url == "http://example.com/bin"
    assert caught["original"].exit_code == 42


async def test_child_process_failed_cause_chain_is_original(mongo_db):
    """ChildProcessFailed.__cause__ is the original exception instance."""
    caught_cpf = {}

    async def failing_child(ctx):
        raise _SampleErr("u", 7)

    async def parent_task(ctx):
        try:
            await ctx.run_child(
                failing_child, process_id="cc1", name="CC1",
            )
        except ChildProcessFailed as e:
            caught_cpf["e"] = e

    parent_inst = TaskInstance(
        execute=parent_task, process_id="parent-cc", name="Parent CC",
    )
    child_inst = TaskInstance(
        execute=failing_child, process_id="cc1", name="CC1",
    )
    await upsert_process(mongo_db, "test_cc", parent_inst)
    executor = Executor(mongo_db, "test_cc", {})
    executor.register_tasks([parent_inst, child_inst])
    await executor.launch_process("parent-cc")

    e = caught_cpf["e"]
    assert e.__cause__ is e.original
    assert isinstance(e.__cause__, _SampleErr)
