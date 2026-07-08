"""Tests for group_cancel / group_cancel_and_wait.

Spec: docs/2026-04-30-group-cancel-design.md
"""
import asyncio
import time

import pytest

from optio_core.lifecycle import Optio
from optio_core.models import TaskInstance, LaunchBlocked

pytestmark = pytest.mark.asyncio


async def _start_optio(mongo_db, prefix, tasks, cancel_grace_seconds=2.0):
    """Helper: init an Optio with the given tasks, return (optio, run_task)."""
    async def gen(_s, _f):
        return list(tasks)
    optio = Optio()
    await optio.init(
        mongo_db=mongo_db, prefix=prefix,
        get_task_definitions=gen,
        cancel_grace_seconds=cancel_grace_seconds,
    )
    run_task = asyncio.create_task(optio.run())
    return optio, run_task

async def _stop_optio(optio, run_task):
    await optio.shutdown()
    run_task.cancel()
    try:
        await run_task
    except (asyncio.CancelledError, Exception):
        pass


# ---------- Filter validation (no Mongo needed) ----------

@pytest.mark.parametrize("bad_filter", [None, {}])
@pytest.mark.parametrize("block_new_launches", [False, True])
async def test_group_cancel_rejects_empty_filter(bad_filter, block_new_launches):
    """group_cancel raises ValueError on None / empty filter."""
    optio = Optio()
    with pytest.raises(ValueError, match="non-empty metadata_filter"):
        await optio.group_cancel(bad_filter, block_new_launches=block_new_launches)


@pytest.mark.parametrize("bad_filter", [None, {}])
@pytest.mark.parametrize("block_new_launches", [False, True])
async def test_group_cancel_and_wait_rejects_empty_filter(bad_filter, block_new_launches):
    """group_cancel_and_wait raises ValueError on None / empty filter."""
    optio = Optio()
    with pytest.raises(ValueError, match="non-empty metadata_filter"):
        await optio.group_cancel_and_wait(bad_filter, block_new_launches=block_new_launches)


# ---------- Snapshot + parallel cancel ----------

async def test_group_cancel_only_cancels_in_scope(mongo_db):
    """group_cancel cancels tasks that match the filter and leaves others alone."""
    started_a = asyncio.Event()
    started_b = asyncio.Event()
    release_b = asyncio.Event()

    async def cooperative_a(ctx):
        started_a.set()
        for _ in range(200):
            if ctx.cancellation_flag.is_set():
                return
            await asyncio.sleep(0.02)

    async def cooperative_b(ctx):  # noqa: ARG001
        started_b.set()
        await release_b.wait()

    task_a = TaskInstance(
        process_id="p.a", name="A", params={}, execute=cooperative_a,
        metadata={"team": "alpha"},
    )
    task_b = TaskInstance(
        process_id="p.b", name="B", params={}, execute=cooperative_b,
        metadata={"team": "beta"},
    )

    optio, run_task = await _start_optio(mongo_db, "gc_scope", [task_a, task_b])
    try:
        await optio.launch("p.a", session_id=None)
        await optio.launch("p.b", session_id=None)
        await started_a.wait()
        await started_b.wait()

        await optio.group_cancel({"team": "alpha"})

        # Poll until cooperative_a has observed the flag and unwound to
        # terminal, instead of assuming it happens within a fixed window.
        deadline = time.monotonic() + 60.0
        while time.monotonic() < deadline:
            proc_a = await optio.get_process("p.a")
            if proc_a["status"]["state"] == "cancelled":
                break
            await asyncio.sleep(0.02)
        else:
            raise AssertionError("p.a did not reach cancelled state")

        proc_b = await optio.get_process("p.b")
        assert proc_a["status"]["state"] == "cancelled"
        # p.b is out of scope (team beta) and blocked on release_b, so it
        # stays running regardless of scheduling.
        assert proc_b["status"]["state"] == "running"

        release_b.set()
        await asyncio.sleep(0.1)
    finally:
        await _stop_optio(optio, run_task)


async def test_group_cancel_returns_before_terminal(mongo_db):
    """group_cancel returns once cancels are issued — not once tasks are terminal.

    The cooperative task here observes the flag only after a delay; if
    group_cancel waited for terminal state, the call would block for that
    delay. Since it doesn't, the call returns quickly and the task is
    still in cancel_requested / cancelling / running when we check.
    """
    started = asyncio.Event()

    async def slow_cooperative(ctx):
        started.set()
        # Don't check the flag until well after group_cancel has had a
        # chance to issue and return.
        await asyncio.sleep(2.0)

    task = TaskInstance(
        process_id="p.slow", name="Slow", params={}, execute=slow_cooperative,
        metadata={"team": "alpha"},
    )

    optio, run_task = await _start_optio(mongo_db, "gc_fire", [task])
    try:
        await optio.launch("p.slow", session_id=None)
        await started.wait()

        # group_cancel should return almost immediately.
        t0 = time.monotonic()
        await optio.group_cancel({"team": "alpha"})
        elapsed = time.monotonic() - t0
        assert elapsed < 0.4, f"group_cancel took {elapsed:.3f}s — should be fast"

        # Task is not terminal yet.
        proc = await optio.get_process("p.slow")
        assert proc["status"]["state"] in ("running", "cancel_requested", "cancelling")
    finally:
        await _stop_optio(optio, run_task)


# ---------- group_cancel_and_wait wait loop ----------

async def test_group_cancel_and_wait_all_cooperative(mongo_db):
    """All cooperative tasks reach terminal state 'cancelled' by the time
    group_cancel_and_wait returns; the call blocks until they do."""
    started = [asyncio.Event() for _ in range(3)]

    def make_cooperative(idx):
        async def fn(ctx):
            started[idx].set()
            for _ in range(200):
                if ctx.cancellation_flag.is_set():
                    return
                await asyncio.sleep(0.02)
        return fn

    tasks = [
        TaskInstance(
            process_id=f"p.coop.{i}", name=f"Coop{i}", params={},
            execute=make_cooperative(i), metadata={"team": "alpha"},
        )
        for i in range(3)
    ]

    optio, run_task = await _start_optio(mongo_db, "gcw_coop", tasks)
    try:
        for i in range(3):
            await optio.launch(f"p.coop.{i}", session_id=None)
        for ev in started:
            await ev.wait()

        await optio.group_cancel_and_wait({"team": "alpha"})

        for i in range(3):
            proc = await optio.get_process(f"p.coop.{i}")
            assert proc["status"]["state"] == "cancelled", (
                f"task {i} ended in {proc['status']['state']}"
            )
    finally:
        await _stop_optio(optio, run_task)


async def test_group_cancel_and_wait_mixed_cooperative_and_stubborn(mongo_db):
    """Cooperative tasks end 'cancelled'; stubborn tasks (ignore the flag)
    are force-cancelled by the supervisor and end 'failed' with the
    canonical error string."""
    started_coop = asyncio.Event()
    started_stub = asyncio.Event()

    async def cooperative(ctx):
        started_coop.set()
        for _ in range(200):
            if ctx.cancellation_flag.is_set():
                return
            await asyncio.sleep(0.02)

    async def stubborn(ctx):  # noqa: ARG001
        started_stub.set()
        while True:
            await asyncio.sleep(0.05)  # ignores the flag

    task_coop = TaskInstance(
        process_id="p.coop", name="Coop", params={}, execute=cooperative,
        metadata={"team": "alpha"},
    )
    task_stub = TaskInstance(
        process_id="p.stub", name="Stub", params={}, execute=stubborn,
        metadata={"team": "alpha"},
    )

    optio, run_task = await _start_optio(
        mongo_db, "gcw_mixed", [task_coop, task_stub], cancel_grace_seconds=0.5,
    )
    try:
        await optio.launch("p.coop", session_id=None)
        await optio.launch("p.stub", session_id=None)
        await started_coop.wait()
        await started_stub.wait()

        await optio.group_cancel_and_wait({"team": "alpha"})

        proc_coop = await optio.get_process("p.coop")
        proc_stub = await optio.get_process("p.stub")
        assert proc_coop["status"]["state"] == "cancelled"
        assert proc_stub["status"]["state"] == "failed"
        assert (
            "Task did not unwind within cancellation grace period"
            in (proc_stub["status"].get("error") or "")
        )
    finally:
        await _stop_optio(optio, run_task)


async def test_group_cancel_and_wait_raises_on_internal_ceiling(mongo_db, monkeypatch):
    """Patch the executor's force_cancel to a no-op so the supervisor never
    finalizes the stubborn task. group_cancel_and_wait must raise
    asyncio.TimeoutError once the internal ceiling expires."""
    started = asyncio.Event()

    async def stubborn(ctx):  # noqa: ARG001
        started.set()
        while True:
            await asyncio.sleep(0.05)

    task = TaskInstance(
        process_id="p.stub", name="Stub", params={}, execute=stubborn,
        metadata={"team": "alpha"},
    )

    # Use a very small grace + ceiling buffer so the test is fast. The
    # ceiling = cancel_grace_seconds + 25 in production; we patch that
    # constant via a small monkeypatch on the helper at the call site.
    optio, run_task = await _start_optio(
        mongo_db, "gcw_ceil", [task], cancel_grace_seconds=0.2,
    )
    try:
        await optio.launch("p.stub", session_id=None)
        await started.wait()

        # No-op the executor's force_cancel so the supervisor cannot
        # finalize the stubborn task.
        async def noop(*a, **k):
            return None
        monkeypatch.setattr(optio._executor, "force_cancel", noop)

        # Patch the +25.0 buffer to something tiny by monkeypatching the
        # helper to use a smaller ceiling. Cleanest: temporarily mutate
        # the config's cancel_grace_seconds to make the formula evaluate
        # to a small number, e.g. set to a negative value so total ≈ 0.5.
        # But: cancel_grace_seconds also controls when the supervisor
        # would force-cancel — which is patched out. So we can shrink it.
        optio._config.cancel_grace_seconds = -24.5  # ceiling = 0.5

        with pytest.raises(asyncio.TimeoutError, match="did not reach a terminal state"):
            await optio.group_cancel_and_wait({"team": "alpha"})
    finally:
        # Reset before shutdown so shutdown's grace logic doesn't go wild.
        optio._config.cancel_grace_seconds = 0.2
        await _stop_optio(optio, run_task)


# ---------- block_new_launches=True ----------

async def test_block_new_launches_rejects_during_call(mongo_db):
    """While group_cancel_and_wait runs with block_new_launches=True,
    a concurrent launch matching the filter raises LaunchBlocked.
    After the helper returns, the guard is gone."""
    started = asyncio.Event()

    async def cooperative(ctx):
        started.set()
        for _ in range(200):
            if ctx.cancellation_flag.is_set():
                return
            await asyncio.sleep(0.02)

    target_task = TaskInstance(
        process_id="p.target", name="Target", params={}, execute=cooperative,
        metadata={"team": "alpha"},
    )
    intruder_task = TaskInstance(
        process_id="p.intruder", name="Intruder", params={}, execute=cooperative,
        metadata={"team": "alpha"},
    )

    optio, run_task = await _start_optio(
        mongo_db, "gcw_guard", [target_task, intruder_task],
    )
    try:
        await optio.launch("p.target", session_id=None)
        await started.wait()

        # We need the intruder launch to race with the helper. Spawn a
        # coroutine that waits briefly then attempts to launch.
        intruder_blocked = asyncio.Event()

        async def attempt_intruder():
            # Attempt the launch the instant the helper has registered the
            # guard (block_launches populates _launch_blocks synchronously on
            # entry, before the snapshot). Gating on that state — not a fixed
            # sleep — makes "launch during the call" deterministic.
            deadline = time.monotonic() + 60.0
            while not optio._launch_blocks:
                if time.monotonic() >= deadline:
                    raise AssertionError("launch guard was never registered")
                await asyncio.sleep(0.005)
            with pytest.raises(LaunchBlocked):
                await optio.launch_and_wait("p.intruder", session_id=None)
            intruder_blocked.set()

        intruder_task_handle = asyncio.create_task(attempt_intruder())

        await optio.group_cancel_and_wait(
            {"team": "alpha"}, block_new_launches=True,
        )

        await intruder_task_handle
        assert intruder_blocked.is_set()

        # Guard lifted on return.
        assert optio._launch_blocks == {}
    finally:
        await _stop_optio(optio, run_task)


@pytest.mark.parametrize("method_name", ["group_cancel", "group_cancel_and_wait"])
async def test_block_new_launches_false_no_guard_registered(mongo_db, method_name):
    """With block_new_launches=False, _launch_blocks does not gain a token
    during the call. Capture-and-compare so unrelated guards don't break
    the assertion."""
    started = asyncio.Event()

    async def cooperative(ctx):
        started.set()
        for _ in range(200):
            if ctx.cancellation_flag.is_set():
                return
            await asyncio.sleep(0.02)

    task = TaskInstance(
        process_id="p.x", name="X", params={}, execute=cooperative,
        metadata={"team": "alpha"},
    )

    optio, run_task = await _start_optio(mongo_db, f"gc_noguard_{method_name}", [task])
    try:
        await optio.launch("p.x", session_id=None)
        await started.wait()

        before = set(optio._launch_blocks.keys())
        method = getattr(optio, method_name)
        await method({"team": "alpha"}, block_new_launches=False)
        after = set(optio._launch_blocks.keys())
        assert before == after
    finally:
        await _stop_optio(optio, run_task)


# ---------- Leak sweep ----------

async def test_leak_sweep_catches_post_snapshot_launch(mongo_db, monkeypatch):
    """A launch that passed _check_launch_blocks before the guard
    registered but completed its upsert AFTER the helper's initial
    snapshot is caught by the leak sweep and cancelled."""
    started_intruder = asyncio.Event()

    async def cooperative(ctx):
        started_intruder.set()
        for _ in range(200):
            if ctx.cancellation_flag.is_set():
                return
            await asyncio.sleep(0.02)

    intruder_task = TaskInstance(
        process_id="p.intruder", name="Intruder", params={}, execute=cooperative,
        metadata={"team": "alpha"},
    )

    optio, run_task = await _start_optio(
        mongo_db, "gcw_leak", [intruder_task],
    )
    try:
        # Patch _check_launch_blocks to a no-op for this test — simulates
        # the racing launch that passed the check before the guard arrived.
        monkeypatch.setattr(optio, "_check_launch_blocks", lambda _md: None)

        # Launch the intruder *strictly after* the helper's initial snapshot
        # so it is provably post-snapshot (not in the snapshot, caught only by
        # the leak sweep). Wrap list_processes to fire an event the instant the
        # snapshot read completes; the 100 ms leak-sweep delay then leaves
        # ample room for the intruder's upsert to land before the re-read.
        # (Gating on the snapshot signal, not a fixed sleep, is race-free.)
        snapshot_taken = asyncio.Event()
        orig_list_processes = optio.list_processes

        async def list_processes_signalling(*args, **kwargs):
            result = await orig_list_processes(*args, **kwargs)
            snapshot_taken.set()
            return result

        optio.list_processes = list_processes_signalling

        async def stage_intruder():
            await snapshot_taken.wait()
            await optio.launch("p.intruder", session_id=None)

        intruder_handle = asyncio.create_task(stage_intruder())

        await optio.group_cancel_and_wait(
            {"team": "alpha"}, block_new_launches=True,
        )
        await intruder_handle

        # Intruder must have been cancelled (caught by the leak sweep)
        # and reached a terminal state by the time the call returned.
        proc = await optio.get_process("p.intruder")
        assert proc["status"]["state"] in ("cancelled", "failed"), (
            f"intruder ended in {proc['status']['state']} "
            "(should have been caught by leak sweep)"
        )
    finally:
        await _stop_optio(optio, run_task)


async def test_leak_sweep_noop_when_no_concurrent_launch(mongo_db):
    """With block_new_launches=True and no in-flight launches, the leak
    sweep adds zero pids; helper returns normally."""
    started = asyncio.Event()

    async def cooperative(ctx):
        started.set()
        for _ in range(200):
            if ctx.cancellation_flag.is_set():
                return
            await asyncio.sleep(0.02)

    task = TaskInstance(
        process_id="p.solo", name="Solo", params={}, execute=cooperative,
        metadata={"team": "alpha"},
    )

    optio, run_task = await _start_optio(mongo_db, "gcw_noleak", [task])
    try:
        await optio.launch("p.solo", session_id=None)
        await started.wait()

        # No concurrent stage_intruder; just call the helper.
        await optio.group_cancel_and_wait(
            {"team": "alpha"}, block_new_launches=True,
        )

        proc = await optio.get_process("p.solo")
        assert proc["status"]["state"] == "cancelled"
    finally:
        await _stop_optio(optio, run_task)


# ---------- Self-cancel ----------

async def test_self_cancel_via_group_cancel(mongo_db):
    """A task that calls group_cancel matching its own metadata returns
    from the call cleanly, then unwinds cooperatively."""
    reached_after_call = asyncio.Event()

    async def self_canceller(ctx):
        # Cancel my own group, including myself.
        await optio_handle["optio"].group_cancel({"team": "alpha"})
        reached_after_call.set()
        # Now keep checking the flag. Should see it set very soon.
        for _ in range(200):
            if ctx.cancellation_flag.is_set():
                return
            await asyncio.sleep(0.02)

    task = TaskInstance(
        process_id="p.self", name="Self", params={}, execute=self_canceller,
        metadata={"team": "alpha"},
    )

    # Trick: stash the optio reference where the task body can reach it.
    optio_handle = {}

    optio, run_task = await _start_optio(mongo_db, "gc_self", [task])
    optio_handle["optio"] = optio
    try:
        await optio.launch("p.self", session_id=None)

        # Wait for the task to reach the post-call point and then unwind.
        deadline = time.monotonic() + 60.0
        while time.monotonic() < deadline:
            proc = await optio.get_process("p.self")
            if proc["status"]["state"] == "cancelled":
                break
            await asyncio.sleep(0.05)
        proc = await optio.get_process("p.self")
        assert proc["status"]["state"] == "cancelled"
        assert reached_after_call.is_set()  # the call returned
    finally:
        await _stop_optio(optio, run_task)


async def test_guard_lifted_on_exception(mongo_db, monkeypatch):
    """When group_cancel_and_wait raises asyncio.TimeoutError with
    block_new_launches=True, the launch guard is lifted on the way out
    (capture-and-compare _launch_blocks)."""
    started = asyncio.Event()

    async def stubborn(ctx):  # noqa: ARG001
        started.set()
        while True:
            await asyncio.sleep(0.05)

    task = TaskInstance(
        process_id="p.stub", name="Stub", params={}, execute=stubborn,
        metadata={"team": "alpha"},
    )

    optio, run_task = await _start_optio(
        mongo_db, "gcw_lift", [task], cancel_grace_seconds=0.2,
    )
    try:
        await optio.launch("p.stub", session_id=None)
        await started.wait()

        # Patch out force_cancel + shrink the ceiling — same trick as
        # Task 6, so the helper raises TimeoutError.
        async def noop(*a, **k):
            return None
        monkeypatch.setattr(optio._executor, "force_cancel", noop)
        optio._config.cancel_grace_seconds = -24.5  # ceiling = 0.5

        before = set(optio._launch_blocks.keys())
        with pytest.raises(asyncio.TimeoutError):
            await optio.group_cancel_and_wait(
                {"team": "alpha"}, block_new_launches=True,
            )
        after = set(optio._launch_blocks.keys())
        assert before == after  # guard lifted on raise
    finally:
        optio._config.cancel_grace_seconds = 0.2
        await _stop_optio(optio, run_task)


async def test_no_block_new_launches_post_snapshot_not_cancelled(mongo_db):
    """With block_new_launches=False, a launch that lands during the
    wait phase is NOT cancelled by the helper (snapshot semantics)."""
    started_a = asyncio.Event()
    started_b = asyncio.Event()

    async def cooperative(ctx):
        started_a.set()
        for _ in range(200):
            if ctx.cancellation_flag.is_set():
                return
            await asyncio.sleep(0.02)

    async def long_runner(ctx):  # noqa: ARG001
        started_b.set()
        await asyncio.sleep(2.0)

    task_a = TaskInstance(
        process_id="p.a", name="A", params={}, execute=cooperative,
        metadata={"team": "alpha"},
    )
    task_b = TaskInstance(
        process_id="p.b", name="B", params={}, execute=long_runner,
        metadata={"team": "alpha"},
    )

    optio, run_task = await _start_optio(mongo_db, "gcw_noblock_post", [task_a, task_b])
    try:
        await optio.launch("p.a", session_id=None)
        await started_a.wait()

        # Stage task_b to launch *strictly after* the helper takes its
        # snapshot, so b is provably not in the snapshot. With
        # block_new_launches=False the only list_processes call in the
        # cancel path is that snapshot; wrap it to fire an event the
        # instant the snapshot read completes, and gate b's launch on it.
        # (Gating on a completion signal, not a sleep guess, makes the
        # ordering deterministic — the prior sleep(0.05) raced.)
        snapshot_taken = asyncio.Event()
        orig_list_processes = optio.list_processes

        async def list_processes_signalling(*args, **kwargs):
            result = await orig_list_processes(*args, **kwargs)
            snapshot_taken.set()
            return result

        optio.list_processes = list_processes_signalling

        async def stage_b():
            await snapshot_taken.wait()
            await optio.launch("p.b", session_id=None)

        b_handle = asyncio.create_task(stage_b())

        await optio.group_cancel_and_wait({"team": "alpha"})  # default False
        await b_handle
        # Wait until b is genuinely executing (long_runner sets started_b only
        # after the executor has written its 'running' row). Gating the read on
        # this — not on group_cancel's return — makes the assertion race-free.
        await started_b.wait()

        # b is still running — was not in the snapshot.
        proc_b = await optio.get_process("p.b")
        assert proc_b["status"]["state"] == "running"
    finally:
        await _stop_optio(optio, run_task)


@pytest.mark.parametrize("method_name", ["group_cancel", "group_cancel_and_wait"])
async def test_no_active_processes_match(mongo_db, method_name):
    """No matching tasks → both helpers return without error."""
    optio, run_task = await _start_optio(mongo_db, f"gc_empty_{method_name}", [])
    try:
        method = getattr(optio, method_name)
        # Default block_new_launches=False: trivial return.
        await method({"team": "alpha"})
        # block_new_launches=True: leak sweep runs but adds 0 pids.
        await method({"team": "alpha"}, block_new_launches=True)
    finally:
        await _stop_optio(optio, run_task)


# ---------- Public API export ----------

async def test_group_cancel_pair_exported_from_package():
    """Both helpers are exported from optio_core, bound to the singleton."""
    import optio_core
    assert optio_core.group_cancel == optio_core._instance.group_cancel
    assert optio_core.group_cancel_and_wait == optio_core._instance.group_cancel_and_wait
    assert "group_cancel" in optio_core.__all__
    assert "group_cancel_and_wait" in optio_core.__all__
