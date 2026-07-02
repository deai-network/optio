"""Integration test: full optio-codex session against a LocalHost."""

from __future__ import annotations

import pathlib

import pytest

from optio_codex import (
    CodexTaskConfig,
    create_codex_task,
)


@pytest.mark.asyncio
async def test_local_happy_path_done_in_optio_log(
    shim_install_dir: pathlib.Path,
    task_root,
    ctx_and_captures,
    monkeypatch,
):
    ctx, captures, _ = ctx_and_captures
    monkeypatch.setenv("FAKE_CODEX_SCENARIO", "happy")

    observed: dict[str, object] = {}

    async def after_execute(hook_ctx):
        workdir = pathlib.Path(hook_ctx._host.workdir)
        log_path = workdir / "optio.log"
        observed["optio_log"] = log_path.read_text(encoding="utf-8")
        observed["agents_md"] = (workdir / "AGENTS.md").read_text(encoding="utf-8")
        observed["home_codex_isdir"] = (workdir / "home" / ".codex").is_dir()  # C1
        observed["per_task_codex"] = (
            workdir / "home" / ".local" / "bin" / "codex"
        ).exists()  # C2

    task = create_codex_task(
        process_id="codex-local-happy",
        name="Local happy",
        config=CodexTaskConfig(
            consumer_instructions="Hello from the test.",
            codex_install_dir=str(shim_install_dir),
            ttyd_install_dir=str(shim_install_dir),
            after_execute=after_execute,
        ),
    )
    assert task.ui_widget == "iframe"
    assert task.supports_resume is False

    await task.execute(ctx)

    assert "Hello from the test." in observed["agents_md"]
    assert "STATUS:" in observed["agents_md"]
    assert "DONE" in observed["optio_log"]
    assert observed["home_codex_isdir"] is True
    assert observed["per_task_codex"] is True
    assert captures.widget_upstream
    assert captures.widget_data


@pytest.mark.asyncio
async def test_local_deliverable_callback_fired(
    shim_install_dir: pathlib.Path,
    task_root,
    ctx_and_captures,
    monkeypatch,
):
    ctx, *_ = ctx_and_captures
    monkeypatch.setenv("FAKE_CODEX_SCENARIO", "deliverable")

    captured: list[tuple[str, str]] = []

    async def on_deliverable(hook_ctx, path, text):
        captured.append((path, text))

    task = create_codex_task(
        process_id="codex-local-deliverable",
        name="d",
        config=CodexTaskConfig(
            consumer_instructions="hand back a file",
            codex_install_dir=str(shim_install_dir),
            ttyd_install_dir=str(shim_install_dir),
            on_deliverable=on_deliverable,
        ),
    )
    await task.execute(ctx)

    assert len(captured) == 1


@pytest.mark.asyncio
async def test_local_error_raises(
    shim_install_dir: pathlib.Path,
    task_root,
    ctx_and_captures,
    monkeypatch,
):
    ctx, *_ = ctx_and_captures
    monkeypatch.setenv("FAKE_CODEX_SCENARIO", "error")
    task = create_codex_task(
        process_id="codex-local-error",
        name="e",
        config=CodexTaskConfig(
            consumer_instructions="fail",
            codex_install_dir=str(shim_install_dir),
            ttyd_install_dir=str(shim_install_dir),
        ),
    )
    with pytest.raises(RuntimeError):
        await task.execute(ctx)