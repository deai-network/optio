import pytest

from optio_agents.deliverable_meta import entry
from optio_demo.tasks._feedback import make_feedback_on_deliverable


class _Ctx:
    def __init__(self, pid):
        self.process_id = pid
        self.msgs = []

    def report_progress(self, percent, message=None):
        self.msgs.append(message)


@pytest.mark.asyncio
async def test_accepts_when_marker_present():
    cb = make_feedback_on_deliverable("t")
    assert await cb(_Ctx("p-accept"), "x.md", "all done. Over and out.") is None


@pytest.mark.asyncio
async def test_nudges_then_caps():
    cb = make_feedback_on_deliverable("t")
    ctx = _Ctx("p-cap")
    r1 = await cb(ctx, "x.md", "no marker here")
    assert r1 is not None and "over and out" in r1.lower()
    r2 = await cb(ctx, "x.md", "still missing")
    assert r2 is not None
    r3 = await cb(ctx, "x.md", "still missing")  # 3rd: past cap=2
    assert r3 is None
    assert any("cap" in (m or "") for m in ctx.msgs)


@pytest.mark.asyncio
async def test_prints_the_metadata(capsys):
    cb = make_feedback_on_deliverable("t")
    await cb(_Ctx("p-md"), "x.md", "done. Over and out.", [entry("model", "lyceum/kimi-k3.1-pro", "agent")])
    assert "[t] metadata: model=lyceum/kimi-k3.1-pro (agent)" in capsys.readouterr().out


@pytest.mark.asyncio
async def test_prints_no_metadata_when_there_is_none(capsys):
    cb = make_feedback_on_deliverable("t")
    await cb(_Ctx("p-md-none"), "x.md", "done. Over and out.")
    await cb(_Ctx("p-md-empty"), "x.md", "done. Over and out.", [])
    assert capsys.readouterr().out.count("[t] metadata: none\n") == 2
