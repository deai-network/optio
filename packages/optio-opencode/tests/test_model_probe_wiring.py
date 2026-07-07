"""Session-level orchestration of the opencode model probe:
``_probe_or_cached_disabled_models`` — cache hit, resume skip, fresh probe+save,
and the widgetData disabled-map plumbing. A FRESH probe runs as a CHILD subtask
(``run_probe_child``); a cache hit / resume spawns no child."""
import pytest

from optio_core.exceptions import ResultNotPublished
from optio_core.models import ChildHandle

from optio_opencode import model_probe, session as sess


class FakeCtx:
    """Minimal ProcessContext stand-in for the probe orchestrator.

    ``run_child_task_with_result`` records the spawned probe child and runs its
    ``execute`` in-process (this ctx doubles as the child ctx), returning a
    ChildHandle with whatever the child published — so the fresh-probe path
    really goes through ``run_probe_child``."""

    def __init__(self, db):
        self._db = db
        self._prefix = "t"
        self.process_id = "root.p"
        self._child_counter = {"next": 0}
        self.progress: list = []
        self.spawned: list = []
        self._published = None
        self._did_publish = False

    def report_progress(self, pct, msg=None):
        self.progress.append((pct, msg))

    def publish_result(self, obj):
        self._published = obj
        self._did_publish = True

    async def run_child_task_with_result(self, task, **kw):
        self.spawned.append(task)
        await task.execute(self)
        if not self._did_publish:
            raise ResultNotPublished(task.process_id)
        return ChildHandle(result=self._published, task=None)


async def test_cache_hit_returns_disabled_map_without_probing(mongo_db, monkeypatch):
    ctx = FakeCtx(mongo_db)
    await model_probe.save_probe_cache(
        mongo_db, "t", "seed1", {"prov/good": True, "prov/bad": False},
    )
    called = {"fetch": 0, "run": 0}

    async def _no_fetch(*a, **k):
        called["fetch"] += 1
        return []

    async def _no_run(*a, **k):
        called["run"] += 1
        return {}

    monkeypatch.setattr(sess, "_fetch_opencode_models", _no_fetch)
    monkeypatch.setattr(sess, "_run_model_probe", _no_run)

    got = await sess._probe_or_cached_disabled_models(
        ctx, worker_port=1, password="pw", directory="/wd",
        seed_key="seed1", resuming=False,
    )
    assert got == {"prov/bad": model_probe.DISABLED_REASON}
    assert called == {"fetch": 0, "run": 0}  # cache hit → no network
    assert ctx.spawned == []  # cache hit → no probe subtask


async def test_resume_with_no_cache_skips_probe(mongo_db, monkeypatch):
    ctx = FakeCtx(mongo_db)
    called = {"run": 0}

    async def _no_run(*a, **k):
        called["run"] += 1
        return {}

    monkeypatch.setattr(sess, "_run_model_probe", _no_run)
    got = await sess._probe_or_cached_disabled_models(
        ctx, worker_port=1, password="pw", directory="/wd",
        seed_key="seedX", resuming=True,
    )
    assert got == {}
    assert called["run"] == 0
    assert ctx.spawned == []  # resume → no probe subtask


async def test_fresh_probe_runs_saves_and_returns_map(mongo_db, monkeypatch):
    ctx = FakeCtx(mongo_db)

    async def _fetch(port, password, directory):
        return ["prov/good", "prov/bad"]

    async def _run(port, password, directory, model_ids, report):
        # exercise the progress reporter
        for i, mid in enumerate(model_ids):
            report(i + 1, len(model_ids), mid)
        return {"prov/good": True, "prov/bad": False}

    monkeypatch.setattr(sess, "_fetch_opencode_models", _fetch)
    monkeypatch.setattr(sess, "_run_model_probe", _run)

    got = await sess._probe_or_cached_disabled_models(
        ctx, worker_port=1, password="pw", directory="/wd",
        seed_key="seed2", resuming=False,
    )
    assert got == {"prov/bad": model_probe.DISABLED_REASON}
    # a FRESH probe runs as a CHILD subtask nested under the parent
    assert len(ctx.spawned) == 1
    assert ctx.spawned[0].process_id == "root.p.model-probe-0"
    assert ctx.spawned[0].name == "Checking available models"
    # result persisted for the next launch on this seed
    cached = await model_probe.load_probe_cache(mongo_db, "t", "seed2")
    assert cached == {"prov/good": True, "prov/bad": False}
    # milestone + per-model progress was reported (on the child ctx)
    assert (0.0, "Checking available models…") in ctx.progress


async def test_empty_model_list_returns_empty(mongo_db, monkeypatch):
    ctx = FakeCtx(mongo_db)

    async def _fetch(*a, **k):
        return []

    async def _run(*a, **k):
        raise AssertionError("probe should not run with no models")

    monkeypatch.setattr(sess, "_fetch_opencode_models", _fetch)
    monkeypatch.setattr(sess, "_run_model_probe", _run)
    got = await sess._probe_or_cached_disabled_models(
        ctx, worker_port=1, password="pw", directory="/wd",
        seed_key=None, resuming=False,
    )
    assert got == {}
    # the child spawns (gating lives in the parent, enumeration in the child),
    # discovers no models, and publishes an empty map — the probe never runs
    assert len(ctx.spawned) == 1
