"""Proof that run_claudecode_session's finally block actually runs with
optio_host.host's unwind_tracing() ContextVar set -- i.e. the fix for the
code review's "run_command log-flood" finding (run_command's OPTIO_
CANCEL_TRACE gate is confined to a task-cancel/snapshot unwind) is wired
into the REAL teardown path, not just exercised against the helper in
isolation (see test_run_command_unwind_trace.py in optio-host for that).

Uses the same shim-based full-session fixtures as test_session_hooks.py.
"""

from __future__ import annotations

import pathlib

import pytest

import optio_host.host as host_mod
from optio_claudecode import ClaudeCodeTaskConfig, create_claudecode_task

# Plants files into the shared home dir and drives a real session, same as
# test_session_hooks.py — serial to avoid concurrent home-dir races.
pytestmark = pytest.mark.serial


@pytest.mark.asyncio
async def test_finally_block_runs_with_unwind_tracing_set(
    shim_install_dir: pathlib.Path,
    claude_cache_dir: pathlib.Path,
    ctx_and_captures,
    monkeypatch,
):
    ctx, _captures, _cancellation_flag = ctx_and_captures
    monkeypatch.setenv("FAKE_CLAUDE_SCENARIO", "happy")

    seen_unwind_flag: list[bool] = []
    real_run_command = host_mod.LocalHost.run_command

    async def _spy_run_command(self, *args, **kwargs):
        # LocalHost.run_command has no tracing of its own (the gate lives on
        # RemoteHost), but the ContextVar is host-agnostic module state, so
        # this spy sees exactly what RemoteHost.run_command would have seen
        # had this session been SSH-backed.
        seen_unwind_flag.append(host_mod._unwind_tracing.get())
        return await real_run_command(self, *args, **kwargs)

    monkeypatch.setattr(host_mod.LocalHost, "run_command", _spy_run_command)

    task = create_claudecode_task(
        process_id="cc-unwind-tracing",
        name="Unwind tracing coverage",
        config=ClaudeCodeTaskConfig(
            consumer_instructions="Hi.",
            fs_isolation=False,
            install_dir=str(claude_cache_dir),
            ttyd_install_dir=str(shim_install_dir),
        ),
    )
    await task.execute(ctx)

    # A happy-path run still ends in the finally block's teardown +
    # _capture_snapshot (supports_resume defaults True and launched_handle
    # is set after the shim launch succeeds), so run_command is called
    # multiple times from inside `with unwind_tracing():` — the
    # credentials guard and both rm -rf steps in _capture_snapshot, and
    # await_claude_gone's pgrep poll inside teardown_session_tree.
    assert seen_unwind_flag, "expected at least one run_command call during teardown"
    assert any(seen_unwind_flag), (
        "expected at least one run_command call made while unwind_tracing() "
        "was active; got all-False, meaning the finally block's `with "
        "unwind_tracing():` wrapper is not covering its run_command calls"
    )
    # And it must not leak past the session: nothing outside a marked unwind
    # bracket should ever see it set.
    assert host_mod._unwind_tracing.get() is False
