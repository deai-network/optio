"""Tests for the engine-agnostic model-availability probe + per-seed cache.

The shared module carries everything two+ engines need identically: the cache
(load/save/key/TTL), the disabled-reason surfacing (list form for a server-side
control, map form for a client-fetched picker), and the generic probe loop whose
usable-signal (`usable_check`) and model-setter (`set_model`) are injected by the
engine. Cursor's usable-check is "expected text present"; opencode's is "no turn
error".
"""
from datetime import datetime, timedelta, timezone

from optio_core.exceptions import ChildProcessFailed, ResultNotPublished
from optio_core.models import ChildHandle

from optio_agents import model_probe


class FakeConv:
    """Minimal conversation stand-in.

    A model in ``gated`` answers "Upgrade your plan…" (text-signal engines mark
    it unusable); a model in ``errored`` fires an error EVENT instead of an
    answer (error-signal engines mark it unusable via ``on_event``)."""

    def __init__(self, *, gated=(), errored=()):
        self.current_model_id = "m-default"
        self._model = "m-default"
        self._gated = set(gated)
        self._errored = set(errored)
        self._msg_handlers = []
        self._evt_handlers = []
        self.set_calls = []

    async def set_active_model(self, m):
        self._model = m
        self.set_calls.append(m)

    def on_message(self, h):
        self._msg_handlers.append(h)
        return lambda: self._msg_handlers.remove(h)

    def on_event(self, h):
        self._evt_handlers.append(h)
        return lambda: self._evt_handlers.remove(h)

    async def send(self, text):
        if self._model in self._errored:
            for h in list(self._evt_handlers):
                h({"type": "session.error",
                   "properties": {"error": {"message": "bad model"}}})
            return
        reply = (
            "Upgrade your plan to continue"
            if self._model in self._gated
            else "The capital city of Hungary is Budapest."
        )
        for h in list(self._msg_handlers):
            h(reply)


def _text_usable(answer, error):
    return "budapest" in (answer or "").lower()


def _error_usable(answer, error):
    return error is None and bool((answer or "").strip())


def _set_model(conv, mid):
    return conv.set_active_model(mid)


async def test_probe_models_text_signal_marks_gated_unusable():
    conv = FakeConv(gated={"m-gated"})
    usable = await model_probe.probe_models(
        conv, ["m-default", "m-gated"],
        usable_check=_text_usable, set_model=_set_model, per_model_timeout=2.0,
    )
    assert usable == {"m-default": True, "m-gated": False}
    # original model restored after probing
    assert conv._model == "m-default"
    assert conv.set_calls[-1] == "m-default"


async def test_probe_models_error_signal_marks_errored_unusable():
    """opencode's signal: a bad model ends the turn with an error EVENT (no
    answer text). error_from_event surfaces it; usable_check treats a present
    error as unusable."""
    def _err_from_event(ev):
        if ev.get("type") == "session.error":
            return (ev.get("properties") or {}).get("error")
        return None

    conv = FakeConv(errored={"m-bad"})
    usable = await model_probe.probe_models(
        conv, ["m-default", "m-bad"],
        usable_check=_error_usable, set_model=_set_model,
        error_from_event=_err_from_event, per_model_timeout=2.0,
    )
    assert usable == {"m-default": True, "m-bad": False}


async def test_probe_models_reports_progress():
    conv = FakeConv()
    seen = []
    await model_probe.probe_models(
        conv, ["a", "b"], usable_check=_text_usable, set_model=_set_model,
        per_model_timeout=2.0, report=lambda i, total, mid: seen.append((i, total, mid)),
    )
    assert seen == [(1, 2, "a"), (2, 2, "b")]


def test_probe_cache_key_survives_resume():
    # fresh: resolved id wins
    assert model_probe.probe_cache_key("resolved", "cfg") == "resolved"
    # resumed (resolved None): string config seed_id is the stable key
    assert model_probe.probe_cache_key(None, "cfg-seed") == "cfg-seed"
    # resumed pooled seed (callable) → no stable key
    assert model_probe.probe_cache_key(None, lambda pid: "x") is None
    # no seed at all
    assert model_probe.probe_cache_key(None, None) is None


def test_apply_probe_disables_only_unusable_with_reason():
    models = [
        {"id": "a", "label": "A", "disabled": False},
        {"id": "b", "label": "B", "disabled": False},
        {"id": "c", "label": "C", "disabled": False},  # not probed
    ]
    out = model_probe.apply_probe(models, {"a": True, "b": False}, reason="nope")
    assert out[0]["disabled"] is False
    assert "disabledReason" not in out[0]
    assert out[1]["disabled"] is True
    assert out[1]["disabledReason"] == "nope"
    assert out[2]["disabled"] is False  # untouched (not probed)
    assert "disabledReason" not in out[2]


def test_disabled_map_keys_only_unusable():
    m = model_probe.disabled_map({"a": True, "b": False, "c": False}, "why")
    assert m == {"b": "why", "c": "why"}


class _ProbeChildFakeCtx:
    """ProcessContext stand-in for ``run_probe_child``: records the child
    TaskInstances handed to ``run_child_task_with_result`` and runs their
    ``execute`` in-process (this object doubles as the child ctx), returning a
    ChildHandle carrying whatever the child published. ``raise_on_spawn`` makes
    the spawn surface a failure instead (a child that failed / never published)."""

    def __init__(self, *, process_id="root.p", raise_on_spawn=None):
        self.process_id = process_id
        self._child_counter = {"next": 0}
        self.spawned = []
        self.progress = []
        self._published = None
        self._did_publish = False
        self._raise_on_spawn = raise_on_spawn

    def report_progress(self, pct, msg=None):
        self.progress.append((pct, msg))

    def publish_result(self, obj):
        self._published = obj
        self._did_publish = True

    async def run_child_task_with_result(self, task, **kw):
        self.spawned.append(task)
        if self._raise_on_spawn is not None:
            raise self._raise_on_spawn
        await task.execute(self)
        if not self._did_publish:
            raise ResultNotPublished(task.process_id)
        return ChildHandle(result=self._published, task=None)


async def test_run_probe_child_returns_published_result_from_a_child_task():
    ctx = _ProbeChildFakeCtx(process_id="root.parent")
    seen = {}

    async def _probe(child_ctx):
        child_ctx.report_progress(0.0, "Checking available models…")
        seen["ctx"] = child_ctx
        return {"m-good": True, "m-bad": False}

    result = await model_probe.run_probe_child(
        ctx, name="Checking available models", description="probe desc", probe=_probe,
    )
    assert result == {"m-good": True, "m-bad": False}
    # spawned exactly one child, nested under the parent process id
    assert len(ctx.spawned) == 1
    task = ctx.spawned[0]
    assert task.process_id == "root.parent.model-probe-0"
    assert task.name == "Checking available models"
    assert task.description == "probe desc"
    # the probe ran under the CHILD ctx and reported its own progress there
    assert (0.0, "Checking available models…") in ctx.progress
    assert seen["ctx"] is ctx


async def test_run_probe_child_passes_a_list_result_through_unchanged():
    """The result type is generic — cursor publishes a gated model LIST."""
    ctx = _ProbeChildFakeCtx()

    async def _probe(child_ctx):
        return [{"id": "a"}, {"id": "b", "disabled": True}]

    result = await model_probe.run_probe_child(
        ctx, name="Checking available models", description=None, probe=_probe,
    )
    assert result == [{"id": "a"}, {"id": "b", "disabled": True}]


async def test_run_probe_child_returns_none_when_child_never_publishes():
    """Best-effort: a child that ends without publishing (ResultNotPublished) is
    swallowed → None so the caller keeps its unfiltered-picker fallback."""
    ctx = _ProbeChildFakeCtx(
        raise_on_spawn=ResultNotPublished("root.p.model-probe-0"),
    )

    async def _probe(child_ctx):
        return {"x": True}

    result = await model_probe.run_probe_child(
        ctx, name="Checking available models", description=None, probe=_probe,
    )
    assert result is None
    assert len(ctx.spawned) == 1


async def test_run_probe_child_returns_none_when_child_fails():
    ctx = _ProbeChildFakeCtx(
        raise_on_spawn=ChildProcessFailed(
            "probe", "root.p.model-probe-0", RuntimeError("boom"),
        ),
    )

    async def _probe(child_ctx):
        raise RuntimeError("boom")

    result = await model_probe.run_probe_child(
        ctx, name="Checking available models", description=None, probe=_probe,
    )
    assert result is None


async def test_probe_cache_roundtrip_and_ttl(mongo_db):
    now = datetime(2026, 7, 3, 12, 0, tzinfo=timezone.utc)
    usable = {"m-default": True, "m-gated": False}
    await model_probe.save_probe_cache(
        mongo_db, "t", "seed1", usable, suffix="_x_probe", now=now,
    )
    got = await model_probe.load_probe_cache(
        mongo_db, "t", "seed1", suffix="_x_probe", now=now,
    )
    assert got == usable
    # just within TTL
    got = await model_probe.load_probe_cache(
        mongo_db, "t", "seed1", suffix="_x_probe", now=now + timedelta(hours=23),
    )
    assert got == usable
    # past TTL -> None
    got = await model_probe.load_probe_cache(
        mongo_db, "t", "seed1", suffix="_x_probe", now=now + timedelta(hours=25),
    )
    assert got is None
    # unknown seed -> None
    assert await model_probe.load_probe_cache(
        mongo_db, "t", "nope", suffix="_x_probe", now=now,
    ) is None
    # suffix isolates collections
    assert await model_probe.load_probe_cache(
        mongo_db, "t", "seed1", suffix="_other_probe", now=now,
    ) is None
