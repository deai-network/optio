import pytest

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
