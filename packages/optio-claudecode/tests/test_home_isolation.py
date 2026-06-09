"""Verify that an optio-claudecode session never reads or modifies the
host user's real ~/.claude/ directory.

Strategy: set HOME to a controlled tmp_path *for the test process and
all its children*, pre-populate that fake-real-home with a sentinel
~/.claude/.credentials.json file, run a session, and verify the
sentinel is byte-identical after the session ran.
"""

from __future__ import annotations

import pathlib

import pytest

from optio_claudecode import ClaudeCodeTaskConfig, create_claudecode_task


@pytest.mark.asyncio
async def test_real_home_credentials_untouched(
    tmp_path: pathlib.Path,
    task_root,
    shim_install_dir: pathlib.Path,
    claude_cache_dir: pathlib.Path,
    ctx_and_captures,
    monkeypatch,
):
    ctx, _captures, _cancellation_flag = ctx_and_captures
    fake_real_home = tmp_path / "real_home"
    fake_real_home.mkdir()
    (fake_real_home / ".claude").mkdir()
    sentinel = fake_real_home / ".claude" / ".credentials.json"
    sentinel.write_text('{"sentinel": true}', encoding="utf-8")
    sentinel_mtime = sentinel.stat().st_mtime_ns
    sentinel_content = sentinel.read_text(encoding="utf-8")

    monkeypatch.setenv("HOME", str(fake_real_home))
    monkeypatch.setenv("FAKE_CLAUDE_SCENARIO", "happy")

    task = create_claudecode_task(
        process_id="cc-home-isolation",
        name="Home isolation",
        config=ClaudeCodeTaskConfig(
            consumer_instructions="Hi.",
            fs_isolation=False,
            credentials_json={"injected": True},
            claude_install_dir=str(claude_cache_dir),
            ttyd_install_dir=str(shim_install_dir),
        ),
    )
    await task.execute(ctx)

    assert sentinel.read_text(encoding="utf-8") == sentinel_content
    assert sentinel.stat().st_mtime_ns == sentinel_mtime
