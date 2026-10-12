"""Integration test: full optio-cursor session against a LocalHost.

Uses the shim binaries from conftest. The session should plant AGENTS.md
and coordinate via optio.log without touching the operator's real config.
"""

from __future__ import annotations

import pathlib

import pytest

from optio_agents import RESUME_NOTICE, SYSTEM_MESSAGE_PREFIX
from optio_cursor import (
    CursorTaskConfig,
    create_cursor_task,
)

_POLL_RULE = "At the start of every new incoming user message"
_NOTICE_RULE = f"`{SYSTEM_MESSAGE_PREFIX}{RESUME_NOTICE}` message on your input channel"


# Spawns cursor-agent in a real tmux/ttyd session; the fixed tmux session name
# and shared workdir state make these unsafe to run concurrently. Marked
# `serial` so the harness runs them in a final, non-parallel phase.
pytestmark = pytest.mark.serial


@pytest.mark.asyncio
async def test_local_deliverable_callback_fired(
    shim_install_dir: pathlib.Path,
    ctx_and_captures,
    monkeypatch,
):
    ctx, *_ = ctx_and_captures
    monkeypatch.setenv("FAKE_CURSOR_SCENARIO", "deliverable")

    captured: list[tuple[str, str]] = []

    async def on_deliverable(hook_ctx, path, text):
        captured.append((path, text))

    task = create_cursor_task(
        process_id="cursor-local-deliverable",
        name="d",
        config=CursorTaskConfig(
            consumer_instructions="hand back a file",
            install_dir=str(shim_install_dir),
            ttyd_install_dir=str(shim_install_dir),
            on_deliverable=on_deliverable,
            delivery_type="audit",
        ),
    )
    await task.execute(ctx)

    assert len(captured) == 1


@pytest.mark.asyncio
async def test_local_error_raises(
    shim_install_dir: pathlib.Path,
    ctx_and_captures,
    monkeypatch,
):
    ctx, *_ = ctx_and_captures
    monkeypatch.setenv("FAKE_CURSOR_SCENARIO", "error")
    task = create_cursor_task(
        process_id="cursor-local-error",
        name="e",
        config=CursorTaskConfig(
            consumer_instructions="fail",
            install_dir=str(shim_install_dir),
            ttyd_install_dir=str(shim_install_dir),
            delivery_type="audit",
        ),
    )
    with pytest.raises(RuntimeError):
        await task.execute(ctx)


@pytest.mark.asyncio
async def test_local_agents_md_waits_for_the_resume_notice(
    shim_install_dir: pathlib.Path,
    ctx_and_captures,
    monkeypatch,
):
    """Every resume is announced with the System: resume notice, so the
    planted AGENTS.md tells cursor to wait for it rather than read
    resume.log before each message."""
    ctx, *_ = ctx_and_captures
    monkeypatch.setenv("FAKE_CURSOR_SCENARIO", "happy")
    observed: dict[str, str] = {}

    async def capture(hook_ctx):
        observed["agents_md"] = await hook_ctx.read_text_from_host("AGENTS.md", silent=True)

    task = create_cursor_task(
        process_id="cursor-local-resume-notice",
        name="n",
        config=CursorTaskConfig(
            consumer_instructions="do the thing",
            install_dir=str(shim_install_dir),
            ttyd_install_dir=str(shim_install_dir),
            delivery_type="audit",
            supports_resume=True,
            before_execute=capture,
        ),
    )
    await task.execute(ctx)

    assert "## Resumes" in observed["agents_md"]
    assert _POLL_RULE not in observed["agents_md"]
    assert _NOTICE_RULE in observed["agents_md"]
