"""Integration test: full optio-kimicode iframe session against a LocalHost.

Uses the kimi shim from conftest. The session should plant AGENTS.md, launch
the ``kimi web`` (``kimi server run``) surface, register a token-carrying
iframe widget, coordinate via optio.log, reach DONE, and tear down cleanly
without touching the operator's real config.
"""

from __future__ import annotations

import pathlib

import pytest

from optio_kimicode import (
    KimiCodeTaskConfig,
    create_kimicode_task,
)


def _stage0_config(**overrides) -> KimiCodeTaskConfig:
    """A Stage-0 iframe config: snapshots (Stage 2) and fs-isolation (Stage 8)
    are not wired yet, so keep them off."""
    base = dict(
        consumer_instructions="do the thing",
        fs_isolation=False,
        supports_resume=False,
    )
    base.update(overrides)
    return KimiCodeTaskConfig(**base)


@pytest.mark.asyncio
async def test_iframe_reaches_done_and_registers_token_widget(
    shim_install_dir: pathlib.Path,
    ctx_and_captures,
    task_root,
    monkeypatch,
):
    ctx, cap, _ = ctx_and_captures
    monkeypatch.setenv("FAKE_KIMI_SCENARIO", "happy")

    task = create_kimicode_task(
        process_id="kimi-iframe-happy",
        name="i",
        config=_stage0_config(kimi_install_dir=str(shim_install_dir)),
    )

    # Reaches DONE and returns (no premature-exit / no failure).
    await task.execute(ctx)

    # The iframe widget was registered pointing at the proxy, carrying the
    # bearer token in the `#token=` fragment.
    assert cap.widget_data, "no widget data was set"
    iframe_src = cap.widget_data[-1]["iframeSrc"]
    assert iframe_src.startswith("{widgetProxyUrl}/")
    assert "#token=fake-kimi-token" in iframe_src

    # A tunnel upstream was published for the running kimi server.
    assert cap.widget_upstream, "no widget upstream was set"

    # The task instance advertises the iframe widget.
    assert task.ui_widget == "iframe"


@pytest.mark.asyncio
async def test_iframe_deliverable_callback_fired(
    shim_install_dir: pathlib.Path,
    ctx_and_captures,
    task_root,
    monkeypatch,
):
    ctx, *_ = ctx_and_captures
    monkeypatch.setenv("FAKE_KIMI_SCENARIO", "deliverable")

    captured: list[tuple[str, str]] = []

    async def on_deliverable(hook_ctx, path, text):
        captured.append((path, text))

    task = create_kimicode_task(
        process_id="kimi-iframe-deliverable",
        name="d",
        config=_stage0_config(
            kimi_install_dir=str(shim_install_dir),
            on_deliverable=on_deliverable,
        ),
    )
    await task.execute(ctx)

    assert len(captured) == 1
    assert "hello from fake kimi" in captured[0][1]


@pytest.mark.asyncio
async def test_iframe_error_raises(
    shim_install_dir: pathlib.Path,
    ctx_and_captures,
    task_root,
    monkeypatch,
):
    ctx, *_ = ctx_and_captures
    monkeypatch.setenv("FAKE_KIMI_SCENARIO", "error")
    task = create_kimicode_task(
        process_id="kimi-iframe-error",
        name="e",
        config=_stage0_config(kimi_install_dir=str(shim_install_dir)),
    )
    with pytest.raises(RuntimeError):
        await task.execute(ctx)
