"""Tests for ProcessScheduler — partial sync semantics."""

import pytest
from optio_core.models import TaskInstance
from optio_core.scheduler import ProcessScheduler


class FakeAPScheduler:
    """Minimal stand-in for apscheduler.AsyncScheduler used by ProcessScheduler."""

    def __init__(self):
        self.jobs: dict[str, dict] = {}

    async def add_job(self, fn, trigger=None, id=None, args=None):
        self.jobs[id] = {"fn": fn, "trigger": trigger, "args": args}

    async def remove_job(self, job_id: str):
        if job_id in self.jobs:
            del self.jobs[job_id]
        else:
            raise KeyError(job_id)


async def _noop(_pid: str):
    pass


def _ps_with_fake() -> tuple[ProcessScheduler, FakeAPScheduler]:
    ps = ProcessScheduler(launch_fn=_noop)
    fake = FakeAPScheduler()
    ps._scheduler = fake  # bypass start()
    return ps, fake


def _t(pid: str, *, group: str, schedule: str | None = "0 * * * *") -> TaskInstance:
    async def fn(ctx):  # pragma: no cover
        pass
    return TaskInstance(
        execute=fn, process_id=pid, name=pid,
        metadata={"group": group}, schedule=schedule,
    )


@pytest.mark.asyncio
async def test_sync_schedules_full_replace_no_filter():
    ps, fake = _ps_with_fake()
    a = _t("a", group="ingest")
    b = _t("b", group="etl")
    await ps.sync_schedules([a, b])
    assert set(fake.jobs) == {"sched_a", "sched_b"}
    assert set(ps._jobs) == {"sched_a", "sched_b"}

    # full replace: only [a] remains
    await ps.sync_schedules([a])
    assert set(fake.jobs) == {"sched_a"}
    assert set(ps._jobs) == {"sched_a"}


@pytest.mark.asyncio
async def test_sync_schedules_partial_keeps_out_of_scope():
    ps, fake = _ps_with_fake()
    in1 = _t("in1", group="ingest")
    in2 = _t("in2", group="ingest")
    out1 = _t("out1", group="etl")
    await ps.sync_schedules([in1, in2, out1])
    assert set(fake.jobs) == {"sched_in1", "sched_in2", "sched_out1"}

    # Partial sync: callback returned only [in1]; in2 should be dropped, out1 preserved.
    await ps.sync_schedules([in1], metadata_filter={"group": "ingest"})
    assert set(fake.jobs) == {"sched_in1", "sched_out1"}
    assert set(ps._jobs) == {"sched_in1", "sched_out1"}


@pytest.mark.asyncio
async def test_sync_schedules_partial_replaces_existing_inscope():
    ps, fake = _ps_with_fake()
    v1 = _t("a", group="ingest", schedule="0 * * * *")
    await ps.sync_schedules([v1])

    v2 = _t("a", group="ingest", schedule="*/5 * * * *")
    await ps.sync_schedules([v2], metadata_filter={"group": "ingest"})

    assert set(fake.jobs) == {"sched_a"}
    assert ps._jobs["sched_a"].schedule == "*/5 * * * *"


@pytest.mark.asyncio
async def test_sync_schedules_skips_tasks_with_no_schedule():
    ps, fake = _ps_with_fake()
    scheduled = _t("a", group="ingest")
    unscheduled = _t("b", group="ingest", schedule=None)
    await ps.sync_schedules([scheduled, unscheduled])
    assert set(fake.jobs) == {"sched_a"}
    assert "sched_b" not in ps._jobs


@pytest.mark.asyncio
async def test_sync_schedules_no_apscheduler_is_noop():
    ps = ProcessScheduler(launch_fn=_noop)
    # _scheduler stays None
    await ps.sync_schedules([_t("a", group="ingest")])
    # No exception, no jobs tracked.
    assert ps._jobs == {}
