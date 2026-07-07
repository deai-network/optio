"""Session-level orchestration of the cursor model probe:
``_probe_or_cached_models`` — cache hit, resume skip, and a FRESH probe that runs
as a CHILD subtask (``run_probe_child``) which drives the operator conversation
(set_model → probe → restore), saves the cache, and purges the throwaway probe
session. A cache hit / resume spawns no child."""
import pytest

from optio_core.exceptions import ResultNotPublished
from optio_core.models import ChildHandle

from optio_cursor import model_probe, session as sess


class FakeCtx:
    """ProcessContext stand-in. ``run_child_task_with_result`` records the
    spawned probe child and runs its ``execute`` in-process (this ctx doubles as
    the child ctx), so the fresh-probe path really goes through
    ``run_probe_child`` and blocks the parent until the child publishes."""

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


class FakeConv:
    """ACP-conversation stand-in: a ``gated`` model answers "Upgrade your
    plan…"; others answer with Budapest. ``reset_session`` returns the probe
    session id so the parent purges it."""

    def __init__(self, gated=(), probe_sid="probe-sid"):
        self.current_model_id = "m-default"
        self._model = "m-default"
        self._gated = set(gated)
        self._handlers = []
        self.set_calls = []
        self._probe_sid = probe_sid
        self.reset_calls = 0

    async def set_active_model(self, m):
        self._model = m
        self.set_calls.append(m)

    def on_message(self, h):
        self._handlers.append(h)
        return lambda: self._handlers.remove(h)

    async def send(self, text):
        reply = (
            "Upgrade your plan to continue"
            if self._model in self._gated
            else "The capital city of Hungary is Budapest."
        )
        for h in list(self._handlers):
            h(reply)

    async def reset_session(self):
        self.reset_calls += 1
        return self._probe_sid


MODELS = [{"id": "m-default", "label": "D"}, {"id": "m-gated", "label": "G"}]


async def test_fresh_launch_probes_in_a_child_and_greys_gated(mongo_db, monkeypatch):
    ctx = FakeCtx(mongo_db)
    conv = FakeConv(gated={"m-gated"})
    purged: list = []

    async def _purge(host, sid):
        purged.append(sid)

    monkeypatch.setattr(sess.host_actions, "purge_cursor_session", _purge)

    out = await sess._probe_or_cached_models(
        ctx, conv, [dict(m) for m in MODELS], host=object(),
        seed_id="seed1", resuming=False,
    )
    # a FRESH probe runs as a CHILD subtask nested under the parent
    assert len(ctx.spawned) == 1
    assert ctx.spawned[0].process_id == "root.p.model-probe-0"
    assert ctx.spawned[0].name == "Checking available models"
    # gated model greyed out with the reason
    by_id = {m["id"]: m for m in out}
    assert by_id["m-default"]["disabled"] is False
    assert by_id["m-gated"]["disabled"] is True
    assert by_id["m-gated"]["disabledReason"] == model_probe.DISABLED_REASON
    # operator conversation's model restored after probing
    assert conv._model == "m-default"
    assert conv.set_calls[-1] == "m-default"
    # throwaway probe turns dropped + probe session purged
    assert conv.reset_calls == 1
    assert purged == ["probe-sid"]
    # milestone progress reported on the child ctx
    assert (0.0, "Checking available models…") in ctx.progress
    # result cached for the next launch on this seed
    assert await model_probe.load_probe_cache(mongo_db, "t", "seed1") == {
        "m-default": True, "m-gated": False,
    }


async def test_cache_hit_applies_without_spawning_child(mongo_db):
    ctx = FakeCtx(mongo_db)
    conv = FakeConv(gated={"m-gated"})
    await model_probe.save_probe_cache(
        mongo_db, "t", "seed1", {"m-default": True, "m-gated": False},
    )
    out = await sess._probe_or_cached_models(
        ctx, conv, [dict(m) for m in MODELS], host=object(),
        seed_id="seed1", resuming=False,
    )
    assert ctx.spawned == []          # cache hit → no probe subtask
    assert conv.set_calls == []       # operator conversation untouched
    assert conv.reset_calls == 0
    by_id = {m["id"]: m for m in out}
    assert by_id["m-gated"]["disabled"] is True
    assert by_id["m-default"]["disabled"] is False


async def test_resume_miss_returns_unchanged_without_child(mongo_db):
    ctx = FakeCtx(mongo_db)
    conv = FakeConv(gated={"m-gated"})
    models = [dict(m) for m in MODELS]
    out = await sess._probe_or_cached_models(
        ctx, conv, models, host=object(), seed_id="seedX", resuming=True,
    )
    assert ctx.spawned == []          # resume → no probe subtask
    assert out == models              # list returned unchanged
    assert conv.set_calls == []
    assert conv.reset_calls == 0
