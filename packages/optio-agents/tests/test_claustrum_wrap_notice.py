# packages/optio-agents/tests/test_claustrum_wrap_notice.py
import pytest
from optio_agents import claustrum


def test_build_claustrum_wrap_shape():
    argv = claustrum.build_claustrum_wrap("/c/claustrum", ["--rwx", "/wd"])
    assert argv == ["/c/claustrum", "--best-effort", "--abi-min", "1", "--rwx", "/wd", "--"]


class _FakeHost:
    def __init__(self):
        self.workdir = "/wd"
        self.written = []
        self.ran = []
    async def write_text(self, rel, text):
        self.written.append((rel, text))
    async def run_command(self, cmd):
        self.ran.append(cmd)
        class R:  # minimal
            exit_code = 0
            stdout = ""
            stderr = ""
        return R()


@pytest.mark.asyncio
async def test_emit_notice_writes_calls_and_cleans_up():
    host = _FakeHost()
    seen = {}
    async def on_deliverable(ctx, rel, text):
        seen["rel"] = rel
        seen["text"] = text
    await claustrum.emit_claustrum_update_notice(
        host, object(), delivery_type="audit",
        on_deliverable=on_deliverable, newer="2.0.0", pinned="1.0.0",
    )
    assert seen["rel"] == "audit/claustrum-update-2.0.0.md"
    assert "2.0.0" in seen["text"] and "1.0.0" in seen["text"]
    assert host.written and host.written[0][0] == "deliverables/audit/claustrum-update-2.0.0.md"
    # cleanup removed the notice file
    assert any("rm -f" in c and "audit/claustrum-update-2.0.0.md" in c for c in host.ran)


@pytest.mark.asyncio
async def test_emit_notice_noop_without_callback():
    host = _FakeHost()
    await claustrum.emit_claustrum_update_notice(
        host, object(), delivery_type="audit",
        on_deliverable=None, newer="2.0.0", pinned="1.0.0",
    )
    assert not host.written
