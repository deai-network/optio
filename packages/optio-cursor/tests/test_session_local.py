"""Integration test: full optio-cursor session against a LocalHost.

Uses the shim binaries from conftest. The session should plant AGENTS.md
and coordinate via optio.log without touching the operator's real config.
"""

from __future__ import annotations

import pathlib

import pytest

from optio_cursor import (
    CursorTaskConfig,
    create_cursor_task,
)


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
            cursor_install_dir=str(shim_install_dir),
            ttyd_install_dir=str(shim_install_dir),
            on_deliverable=on_deliverable,
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
            cursor_install_dir=str(shim_install_dir),
            ttyd_install_dir=str(shim_install_dir),
        ),
    )
    with pytest.raises(RuntimeError):
        await task.execute(ctx)
