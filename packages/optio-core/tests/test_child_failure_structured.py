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


async def test_run_child_survive_failure_returns_outcome_with_original(mongo_db):
    """survive_failure=True suppresses the raise; caller gets ChildOutcome
    with state='failed' and original_exception populated."""
    outcomes = {}

    async def failing_child(ctx):
        raise _SampleErr("u", 11)

    async def parent_task(ctx):
        outcome = await ctx.run_child(
            failing_child, process_id="sf1", name="SF1",
            survive_failure=True,
        )
        outcomes["o"] = outcome

    parent_inst = TaskInstance(
        execute=parent_task, process_id="parent-sf", name="Parent SF",
    )
    child_inst = TaskInstance(
        execute=failing_child, process_id="sf1", name="SF1",
    )
    await upsert_process(mongo_db, "test_sf", parent_inst)
    executor = Executor(mongo_db, "test_sf", {})
    executor.register_tasks([parent_inst, child_inst])
    result = await executor.launch_process("parent-sf")

    assert result == "done"
    o = outcomes["o"]
    assert o.state == "failed"
    assert isinstance(o.original_exception, _SampleErr)
    assert o.original_exception.exit_code == 11


async def test_run_child_done_outcome_is_none(mongo_db):
    """Successful child yields ChildOutcome('done', None)."""
    outcomes = {}

    async def ok_child(ctx):
        ctx.report_progress(100)

    async def parent_task(ctx):
        outcome = await ctx.run_child(ok_child, process_id="ok1", name="OK1")
        outcomes["o"] = outcome

    parent_inst = TaskInstance(execute=parent_task, process_id="parent-ok", name="Parent OK")
    child_inst = TaskInstance(execute=ok_child, process_id="ok1", name="OK1")
    await upsert_process(mongo_db, "test_ok", parent_inst)
    executor = Executor(mongo_db, "test_ok", {})
    executor.register_tasks([parent_inst, child_inst])
    await executor.launch_process("parent-ok")

    o = outcomes["o"]
    assert o.state == "done"
    assert o.original_exception is None


async def test_run_child_refused_outcome_is_cancelled_no_exception(mongo_db):
    """Parent's cancel flag is set with auto_cancel_children -> run_child
    returns ChildOutcome('cancelled', None) without spawning."""
    outcomes = {}

    async def short_child(ctx):
        ctx.report_progress(100)

    async def parent_task(ctx):
        ctx._cancellation_flag.set()
        outcome = await ctx.run_child(short_child, process_id="ref1", name="REF1")
        outcomes["o"] = outcome
        ctx._cancellation_flag.clear()

    parent_inst = TaskInstance(execute=parent_task, process_id="parent-ref", name="Parent REF")
    child_inst = TaskInstance(execute=short_child, process_id="ref1", name="REF1")
    await upsert_process(mongo_db, "test_ref", parent_inst)
    executor = Executor(mongo_db, "test_ref", {})
    executor.register_tasks([parent_inst, child_inst])
    await executor.launch_process("parent-ref")

    o = outcomes["o"]
    assert o.state == "cancelled"
    assert o.original_exception is None


async def test_no_execute_fn_synthesizes_runtimeerror_as_original(mongo_db):
    """When _execute_process receives execute_fn=None it hits the
    no-execute-fn early-fail path, returning ('failed', None).
    execute_child synthesizes a RuntimeError as .original so .original is
    never None."""
    caught = {}

    async def parent_task(ctx):
        try:
            await ctx.run_child(
                execute=None,  # type: ignore[arg-type]
                process_id="missing-child",
                name="Missing",
            )
        except ChildProcessFailed as e:
            caught["e"] = e

    parent_inst = TaskInstance(execute=parent_task, process_id="p-miss", name="P Miss")
    await upsert_process(mongo_db, "test_miss", parent_inst)
    executor = Executor(mongo_db, "test_miss", {})
    executor.register_tasks([parent_inst])
    await executor.launch_process("p-miss")

    e = caught["e"]
    assert e.name == "Missing"
    assert isinstance(e.original, RuntimeError)
    assert "Missing" in str(e.original) or "failed" in str(e.original).lower()


async def test_parallel_group_results_carry_originals(mongo_db):
    """survive_failure=True at group level. group.results[i].original_exception
    is populated for failed children."""
    captured_results = {}

    async def fail_a(ctx):
        raise _SampleErr("ua", 1)

    async def fail_b(ctx):
        raise _SampleErr("ub", 2)

    async def parent_task(ctx):
        async with ctx.parallel_group(survive_failure=True) as g:
            await g.spawn(execute=fail_a, process_id="pa", name="PA")
            await g.spawn(execute=fail_b, process_id="pb", name="PB")
        captured_results["r"] = list(g.results)

    parent_inst = TaskInstance(execute=parent_task, process_id="pgr", name="PGR")
    a_inst = TaskInstance(execute=fail_a, process_id="pa", name="PA")
    b_inst = TaskInstance(execute=fail_b, process_id="pb", name="PB")
    await upsert_process(mongo_db, "test_pgr", parent_inst)
    executor = Executor(mongo_db, "test_pgr", {})
    executor.register_tasks([parent_inst, a_inst, b_inst])
    await executor.launch_process("pgr")

    results = captured_results["r"]
    assert len(results) == 2
    by_pid = {r.process_id: r for r in results}
    assert by_pid["pa"].state == "failed"
    assert isinstance(by_pid["pa"].original_exception, _SampleErr)
    assert by_pid["pa"].original_exception.url == "ua"
    assert by_pid["pa"].name == "PA"
    assert by_pid["pb"].state == "failed"
    assert isinstance(by_pid["pb"].original_exception, _SampleErr)
    assert by_pid["pb"].original_exception.url == "ub"
    assert by_pid["pb"].name == "PB"


async def test_parallel_group_raises_exception_group_with_per_child_wrappers(mongo_db):
    """On aggregate breach, __aexit__ raises ExceptionGroup[ChildProcessFailed]."""
    caught_eg = {}

    async def fail_a(ctx):
        raise _SampleErr("ua", 1)

    async def fail_b(ctx):
        raise _SampleErr("ub", 2)

    async def parent_task(ctx):
        try:
            async with ctx.parallel_group(survive_failure=False) as g:
                await g.spawn(execute=fail_a, process_id="ea", name="EA")
                await g.spawn(execute=fail_b, process_id="eb", name="EB")
        except* ChildProcessFailed as eg:
            caught_eg["matched"] = list(eg.exceptions)

    parent_inst = TaskInstance(execute=parent_task, process_id="peg", name="PEG")
    a_inst = TaskInstance(execute=fail_a, process_id="ea", name="EA")
    b_inst = TaskInstance(execute=fail_b, process_id="eb", name="EB")
    await upsert_process(mongo_db, "test_peg", parent_inst)
    executor = Executor(mongo_db, "test_peg", {})
    executor.register_tasks([parent_inst, a_inst, b_inst])
    await executor.launch_process("peg")

    matched = caught_eg.get("matched", [])
    assert len(matched) == 2
    by_name = {cpf.name: cpf for cpf in matched}
    assert "EA" in by_name and "EB" in by_name
    assert isinstance(by_name["EA"].original, _SampleErr)
    assert by_name["EA"].original.url == "ua"
    assert isinstance(by_name["EB"].original, _SampleErr)
    assert by_name["EB"].original.url == "ub"


async def test_parallel_group_mixed_cancel_and_fail_synthesizes_for_cancelled(mongo_db):
    """One child fails with a real exception, one is cancelled mid-flight.
    The cancelled child's ChildProcessFailed wrapper has a synthetic
    RuntimeError as .original (no real exception was raised).

    Uses Optio so the alpha-cascade notify_parent_abnormal callback is
    wired up — that is what propagates the parent's cancel down to the
    sibling spawn after the first child fails."""
    from optio_core.lifecycle import Optio

    prefix = "test_pmfx"
    caught_eg = {}

    async def fail_a(ctx):
        await asyncio.sleep(0.05)
        raise _SampleErr("ua", 1)

    async def slow_child(ctx):
        while ctx.should_continue():
            await asyncio.sleep(0.01)

    async def parent_task(ctx):
        try:
            async with ctx.parallel_group(
                survive_failure=False, survive_cancel=False,
            ) as g:
                await g.spawn(execute=fail_a, process_id="mfa", name="MFA")
                await g.spawn(execute=slow_child, process_id="mfb", name="MFB")
        except* ChildProcessFailed as eg:
            caught_eg["matched"] = list(eg.exceptions)

    parent_inst = TaskInstance(execute=parent_task, process_id="pmfx", name="PMFX")
    a_inst = TaskInstance(execute=fail_a, process_id="mfa", name="MFA")
    b_inst = TaskInstance(execute=slow_child, process_id="mfb", name="MFB")

    optio = Optio()
    await optio.init(mongo_db=mongo_db, prefix=prefix, cancel_grace_seconds=2.0)
    await upsert_process(mongo_db, prefix, parent_inst)
    optio._executor.register_tasks([parent_inst, a_inst, b_inst])

    try:
        await asyncio.wait_for(optio.launch_and_wait("pmfx"), timeout=10.0)
    finally:
        await optio.shutdown(grace_seconds=0.5)

    matched = caught_eg.get("matched", [])
    by_name = {cpf.name: cpf for cpf in matched}
    assert "MFA" in by_name
    assert isinstance(by_name["MFA"].original, _SampleErr)
    if "MFB" in by_name:
        assert isinstance(by_name["MFB"].original, RuntimeError)
        assert "cancelled" in str(by_name["MFB"].original).lower()
