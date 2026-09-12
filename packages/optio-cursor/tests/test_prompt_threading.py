"""The refreshed AGENTS.md carries the SESSION's protocol docs and the sandbox
note, so it matches the fresh-start composition."""

import dataclasses

from optio_agents import get_protocol

from optio_cursor.session import _maybe_refresh_on_resume
from optio_cursor.types import CursorTaskConfig


class _FakeHost:
    workdir = "/tmp/fake-wd"

    def __init__(self) -> None:
        self.writes: list[tuple[str, str]] = []

    async def write_text(self, path: str, text: str) -> None:
        self.writes.append((path, text))


class _FakeHookCtx:
    async def read_text_from_host(self, path: str, *, silent: bool = False) -> str:
        return "stale"


async def test_refresh_threads_docs_and_sandbox_note():
    cfg = CursorTaskConfig(
        consumer_instructions="original", delivery_type="audit", use_client_messages=True,
        on_resume_refresh=lambda c: dataclasses.replace(c, consumer_instructions="UPDATED"),
    )
    host = _FakeHost()
    protocol = get_protocol(browser="redirect", client_messages=True)
    assert await _maybe_refresh_on_resume(host, _FakeHookCtx(), cfg, protocol) == ["AGENTS.md"]
    (_, text), = host.writes
    assert "UPDATED" in text
    assert "CLIENT_MESSAGE:" in text            # the session's docs, not defaults
    assert "`/tmp/fake-wd`" in text             # sandbox note (fs_isolation default on)
