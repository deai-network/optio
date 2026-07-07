"""Integration test: full optio-codex session against a LocalHost."""

from __future__ import annotations

import asyncio
import os
import pathlib
import shutil

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
        observed["resume_log"] = (workdir / "resume.log").read_text(encoding="utf-8")

    task = create_codex_task(
        process_id="codex-local-happy",
        name="Local happy",
        config=CodexTaskConfig(
            consumer_instructions="Hello from the test.",
            install_dir=str(shim_install_dir),
            ttyd_install_dir=str(shim_install_dir),
            after_execute=after_execute,
        ),
    )
    assert task.ui_widget == "iframe-input"
    assert task.supports_resume is True  # Stage 2: resumable by default

    await task.execute(ctx)

    assert "Hello from the test." in observed["agents_md"]
    assert "STATUS:" in observed["agents_md"]
    assert "DONE" in observed["optio_log"]
    assert observed["home_codex_isdir"] is True
    assert observed["per_task_codex"] is True
    assert observed["resume_log"].count("\n") == 1  # exactly one launch line
    assert "## Resumes" in observed["agents_md"]
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
            install_dir=str(shim_install_dir),
            ttyd_install_dir=str(shim_install_dir),
            on_deliverable=on_deliverable,
        ),
    )
    await task.execute(ctx)

    assert len(captured) == 1
    path, text = captured[0]
    assert path.endswith("greeting.txt")
    assert text == "hello from fake codex\n"


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
            install_dir=str(shim_install_dir),
            ttyd_install_dir=str(shim_install_dir),
        ),
    )
    with pytest.raises(RuntimeError, match="scenario asked for failure"):
        await task.execute(ctx)


@pytest.mark.asyncio
async def test_exit_zero_appends_done_via_shell_channel(
    shim_install_dir, task_root, ctx_and_captures, monkeypatch,
):
    """M5: codex exiting 0 without writing DONE itself → the launch shell's
    rc-branch appends DONE and the session completes clean."""
    ctx, *_ = ctx_and_captures
    monkeypatch.setenv("FAKE_CODEX_SCENARIO", "exit_zero")
    observed = {}

    async def after_execute(hook_ctx):
        log = pathlib.Path(hook_ctx._host.workdir) / "optio.log"
        observed["optio_log"] = log.read_text(encoding="utf-8")

    task = create_codex_task(
        process_id="codex-exit-zero", name="z",
        config=CodexTaskConfig(
            consumer_instructions="exit cleanly",
            install_dir=str(shim_install_dir),
            ttyd_install_dir=str(shim_install_dir),
            after_execute=after_execute,
        ),
    )
    await task.execute(ctx)
    assert "DONE" in observed["optio_log"]


@pytest.mark.asyncio
async def test_exit_nonzero_appends_error_and_raises(
    shim_install_dir, task_root, ctx_and_captures, monkeypatch,
):
    ctx, *_ = ctx_and_captures
    monkeypatch.setenv("FAKE_CODEX_SCENARIO", "exit_nonzero")
    task = create_codex_task(
        process_id="codex-exit-nonzero", name="n",
        config=CodexTaskConfig(
            consumer_instructions="crash",
            install_dir=str(shim_install_dir),
            ttyd_install_dir=str(shim_install_dir),
        ),
    )
    with pytest.raises(RuntimeError, match="codex exited 3"):
        await task.execute(ctx)


@pytest.mark.asyncio
async def test_cancellation_returns_clean_and_tears_down(
    shim_install_dir, task_root, ctx_and_captures, monkeypatch,
):
    """M6: setting the cancellation flag mid-run returns clean (no raise)
    and the tmux session + codex process are gone afterwards."""
    from optio_codex import host_actions
    from optio_host.host import LocalHost
    from optio_host.paths import task_dir

    ctx, captures, cancellation_flag = ctx_and_captures
    monkeypatch.setenv("FAKE_CODEX_SCENARIO", "long")

    process_id = "codex-cancel"
    task = create_codex_task(
        process_id=process_id, name="c",
        config=CodexTaskConfig(
            consumer_instructions="run forever",
            install_dir=str(shim_install_dir),
            ttyd_install_dir=str(shim_install_dir),
        ),
    )

    async def _cancel_when_running():
        for _ in range(200):  # up to 20s
            if any("Codex is live" == m for _, m in captures.progress):
                break
            await asyncio.sleep(0.1)
        cancellation_flag.set()

    canceller = asyncio.create_task(_cancel_when_running())
    await task.execute(ctx)  # must NOT raise: cancellation is a clean return
    await canceller

    # Teardown proof: the per-task tmux session no longer exists.
    taskdir = task_dir(ssh=None, process_id=process_id, consumer_name="optio-codex")
    host = LocalHost(taskdir=taskdir)
    # Aggressive cleanup reaped the whole workdir (itself teardown evidence);
    # recreate it so LocalHost.run_command has a cwd for the tmux probe.
    os.makedirs(host.workdir, exist_ok=True)
    socket_path = host_actions._tmux_socket_path(host)
    tmux = shutil.which("tmux")
    alive = await host_actions.tmux_session_alive(host, tmux, socket_path, "optio")
    assert alive is False

    # …and no codex process launched from this task's per-task path survives.
    per_task_codex = f"{host.workdir}/home/.local/bin/codex"
    gone = await host_actions.await_codex_gone(host, per_task_codex, timeout_s=5.0)
    assert gone is True