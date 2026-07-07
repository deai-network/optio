"""Integration test: full optio-grok session against a LocalHost.

Uses the shim binaries from conftest. The session should plant AGENTS.md
and coordinate via optio.log without touching the operator's real config.
"""

from __future__ import annotations

import pathlib

import pytest

from optio_grok import (
    GrokTaskConfig,
    create_grok_task,
)


# Spawns grok in a real tmux/ttyd session; fixed session name + shared workdir
# state make it unsafe under concurrency. Run in the final non-parallel phase.
pytestmark = pytest.mark.serial


@pytest.mark.asyncio
async def test_local_deliverable_callback_fired(
    shim_install_dir: pathlib.Path,
    ctx_and_captures,
    monkeypatch,
):
    ctx, *_ = ctx_and_captures
    monkeypatch.setenv("FAKE_GROK_SCENARIO", "deliverable")

    captured: list[tuple[str, str]] = []

    async def on_deliverable(hook_ctx, path, text):
        captured.append((path, text))

    task = create_grok_task(
        process_id="grok-local-deliverable",
        name="d",
        config=GrokTaskConfig(
            consumer_instructions="hand back a file",
            grok_install_dir=str(shim_install_dir),
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
    monkeypatch.setenv("FAKE_GROK_SCENARIO", "error")
    task = create_grok_task(
        process_id="grok-local-error",
        name="e",
        config=GrokTaskConfig(
            consumer_instructions="fail",
            grok_install_dir=str(shim_install_dir),
            ttyd_install_dir=str(shim_install_dir),
        ),
    )
    with pytest.raises(RuntimeError):
        await task.execute(ctx)
