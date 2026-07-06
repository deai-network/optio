"""Integration test: full optio-antigravity session against a LocalHost.

Uses the shim binaries from conftest (fake ``agy`` + fake ``ttyd``). The
session should plant AGENTS.md and coordinate via optio.log without touching
the operator's real config — reaching ``DONE`` and tearing down cleanly.

Adapted from optio-grok's test_session_local (grok ← agy renames; the fake
scenario env var is ``FAKE_AGY_SCENARIO``).
"""

from __future__ import annotations

import pathlib

import pytest

from optio_antigravity import (
    AntigravityTaskConfig,
    create_antigravity_task,
)


@pytest.mark.asyncio
async def test_local_reaches_done_and_tears_down(
    shim_install_dir: pathlib.Path,
    ctx_and_captures,
    monkeypatch,
):
    """The happy scenario emits ``DONE``; the task returns cleanly, having
    gone live (iframe widget registered) and then torn the session down."""
    ctx, cap, _ = ctx_and_captures
    monkeypatch.setenv("FAKE_AGY_SCENARIO", "happy")

    task = create_antigravity_task(
        process_id="antigravity-local-happy",
        name="h",
        config=AntigravityTaskConfig(
            consumer_instructions="do the thing",
            agy_install_dir=str(shim_install_dir),
            ttyd_install_dir=str(shim_install_dir),
        ),
    )
    # Clean return == DONE observed in optio.log + teardown ran without error.
    await task.execute(ctx)

    # It reached the live state: the iframe widget was registered before DONE.
    assert any(
        isinstance(payload, dict) and "iframeSrc" in payload
        for payload in cap.widget_data
    )


@pytest.mark.asyncio
async def test_local_deliverable_callback_fired(
    shim_install_dir: pathlib.Path,
    ctx_and_captures,
    monkeypatch,
):
    ctx, *_ = ctx_and_captures
    monkeypatch.setenv("FAKE_AGY_SCENARIO", "deliverable")

    captured: list[tuple[str, str]] = []

    async def on_deliverable(hook_ctx, path, text):
        captured.append((path, text))

    task = create_antigravity_task(
        process_id="antigravity-local-deliverable",
        name="d",
        config=AntigravityTaskConfig(
            consumer_instructions="hand back a file",
            agy_install_dir=str(shim_install_dir),
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
    monkeypatch.setenv("FAKE_AGY_SCENARIO", "error")
    task = create_antigravity_task(
        process_id="antigravity-local-error",
        name="e",
        config=AntigravityTaskConfig(
            consumer_instructions="fail",
            agy_install_dir=str(shim_install_dir),
            ttyd_install_dir=str(shim_install_dir),
        ),
    )
    with pytest.raises(RuntimeError):
        await task.execute(ctx)
