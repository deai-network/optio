"""before_execute and after_execute hook semantics."""

from __future__ import annotations

import pathlib

import pytest

from optio_claudecode import ClaudeCodeTaskConfig, create_claudecode_task


# Plants files into the shared home dir and drives a session; run serially in
# the final phase to avoid concurrent home-dir races.
pytestmark = pytest.mark.serial


@pytest.mark.asyncio
async def test_before_execute_called_after_home_files_planted(
    shim_install_dir: pathlib.Path,
    claude_cache_dir: pathlib.Path,
    ctx_and_captures,
    monkeypatch,
):
    """before_execute must run after credentials/CLAUDE.md exist but
    before claude launches."""
    ctx, _captures, _cancellation_flag = ctx_and_captures
    monkeypatch.setenv("FAKE_CLAUDE_SCENARIO", "happy")
    observed: dict[str, bool] = {}

    async def before(hook_ctx):
        workdir = pathlib.Path(hook_ctx._host.workdir)
        observed["agents_md_exists"] = (workdir / "CLAUDE.md").exists()
        observed["cred_exists"] = (workdir / "home" / ".claude" / ".credentials.json").exists()
        observed["called"] = True

    task = create_claudecode_task(
        process_id="cc-before-hook",
        name="Before hook",
        config=ClaudeCodeTaskConfig(
            consumer_instructions="Hi.",
            fs_isolation=False,
            credentials_json={"a": 1},
            claude_install_dir=str(claude_cache_dir),
            ttyd_install_dir=str(shim_install_dir),
            before_execute=before,
        ),
    )
    await task.execute(ctx)
    assert observed == {
        "called": True,
        "agents_md_exists": True,
        "cred_exists": True,
    }


@pytest.mark.asyncio
async def test_after_execute_called_on_success(
    shim_install_dir: pathlib.Path,
    claude_cache_dir: pathlib.Path,
    ctx_and_captures,
    monkeypatch,
):
    ctx, _captures, _cancellation_flag = ctx_and_captures
    monkeypatch.setenv("FAKE_CLAUDE_SCENARIO", "happy")
    observed: dict[str, bool] = {}

    async def after(hook_ctx):
        observed["called"] = True

    task = create_claudecode_task(
        process_id="cc-after-hook-ok",
        name="After hook ok",
        config=ClaudeCodeTaskConfig(
            consumer_instructions="Hi.",
            fs_isolation=False,
            claude_install_dir=str(claude_cache_dir),
            ttyd_install_dir=str(shim_install_dir),
            after_execute=after,
        ),
    )
    await task.execute(ctx)
    assert observed == {"called": True}


@pytest.mark.asyncio
async def test_after_execute_called_on_error(
    shim_install_dir: pathlib.Path,
    claude_cache_dir: pathlib.Path,
    ctx_and_captures,
    monkeypatch,
):
    ctx, _captures, _cancellation_flag = ctx_and_captures
    monkeypatch.setenv("FAKE_CLAUDE_SCENARIO", "error")
    observed: dict[str, bool] = {}

    async def after(hook_ctx):
        observed["called"] = True

    task = create_claudecode_task(
        process_id="cc-after-hook-err",
        name="After hook err",
        config=ClaudeCodeTaskConfig(
            consumer_instructions="Hi.",
            fs_isolation=False,
            claude_install_dir=str(claude_cache_dir),
            ttyd_install_dir=str(shim_install_dir),
            after_execute=after,
        ),
    )
    with pytest.raises(RuntimeError):
        await task.execute(ctx)
    assert observed == {"called": True}
