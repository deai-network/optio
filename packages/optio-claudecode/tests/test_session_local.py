"""Integration test: full optio-claudecode session against a LocalHost.

Uses the shim binaries from conftest. The session should:

  * write AGENTS.md, HOME-isolation files (under the test).
  * never touch the host user's real ~/.claude/.
"""

from __future__ import annotations

import json
import pathlib

import pytest

from optio_claudecode import (
    ClaudeCodeTaskConfig,
    create_claudecode_task,
)


@pytest.mark.asyncio
async def test_local_happy_path_writes_agents_md_and_home_files(
    shim_install_dir: pathlib.Path,
    ctx_and_captures,
    monkeypatch,
):
    """Assert AGENTS.md / credentials.json / settings.json placement.

    Reads them inside ``before_execute`` because the session's
    ``finally`` calls ``cleanup_taskdir`` after the run, removing the
    workdir before this test body resumes. ``before_execute`` fires
    AFTER all four files are planted and BEFORE ttyd launches — the
    exact assertion point we want.
    """
    ctx, _captures, _cancellation_flag = ctx_and_captures
    monkeypatch.setenv("FAKE_CLAUDE_SCENARIO", "happy")

    observed: dict[str, object] = {}

    async def assert_in_before(hook_ctx):
        workdir = pathlib.Path(hook_ctx._host.workdir)
        observed["agents_md"] = (workdir / "AGENTS.md").read_text()
        cred_path = workdir / "home" / ".claude" / ".credentials.json"
        observed["cred_json"] = json.loads(cred_path.read_text())
        observed["cred_mode"] = oct(cred_path.stat().st_mode)[-3:]
        settings_path = workdir / "home" / ".claude" / "settings.json"
        observed["settings_json"] = json.loads(settings_path.read_text())

    task = create_claudecode_task(
        process_id="cc-local-happy",
        name="Local happy",
        config=ClaudeCodeTaskConfig(
            consumer_instructions="Hello from the test.",
            credentials_json={"oauth_token": "test-token"},
            claude_config={"permissions": {"allow": ["Read"]}},
            permission_mode="bypassPermissions",
            claude_install_dir=str(shim_install_dir),
            ttyd_install_dir=str(shim_install_dir),
            before_execute=assert_in_before,
        ),
    )
    await task.execute(ctx)

    assert "Hello from the test." in observed["agents_md"]
    assert "STATUS:" in observed["agents_md"]
    assert observed["cred_json"] == {"oauth_token": "test-token"}
    assert observed["cred_mode"] == "600"
    assert observed["settings_json"] == {"permissions": {"allow": ["Read"]}}


@pytest.mark.asyncio
async def test_local_deliverable_callback_fired(
    shim_install_dir: pathlib.Path,
    ctx_and_captures,
    monkeypatch,
):
    ctx, _captures, _cancellation_flag = ctx_and_captures
    monkeypatch.setenv("FAKE_CLAUDE_SCENARIO", "deliverable")

    captured: list[tuple[str, str]] = []

    async def on_deliverable(hook_ctx, path, text):
        captured.append((path, text))

    task = create_claudecode_task(
        process_id="cc-local-deliverable",
        name="Local deliverable",
        config=ClaudeCodeTaskConfig(
            consumer_instructions="Hand back a file.",
            claude_install_dir=str(shim_install_dir),
            ttyd_install_dir=str(shim_install_dir),
            on_deliverable=on_deliverable,
        ),
    )
    await task.execute(ctx)

    assert len(captured) == 1
    path, text = captured[0]
    assert path == "greeting.txt"
    assert text == "hello from fake claude\n"


@pytest.mark.asyncio
async def test_local_error_keyword_propagates(
    shim_install_dir: pathlib.Path,
    ctx_and_captures,
    monkeypatch,
):
    ctx, _captures, _cancellation_flag = ctx_and_captures
    monkeypatch.setenv("FAKE_CLAUDE_SCENARIO", "error")
    task = create_claudecode_task(
        process_id="cc-local-error",
        name="Local error",
        config=ClaudeCodeTaskConfig(
            consumer_instructions="Fail please.",
            claude_install_dir=str(shim_install_dir),
            ttyd_install_dir=str(shim_install_dir),
        ),
    )
    with pytest.raises(RuntimeError) as exc_info:
        await task.execute(ctx)
    assert "scenario asked for failure" in str(exc_info.value)
