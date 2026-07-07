"""Session-level wiring test for Stage 8 filesystem isolation (iframe).

Runs a local iframe task with the default ``fs_isolation=True`` and asserts
the fake codex was launched with ``--sandbox workspace-write`` plus the
``-c sandbox_workspace_write.*`` overrides derived from the config. The
workdir is wiped on teardown, so the fake records argv to a durable path
(``FAKE_CODEX_RECORD``) that outlives the task.
"""

from __future__ import annotations

import json
import pathlib

import pytest

from optio_codex import AllowedDir, CodexTaskConfig, create_codex_task


def _launch_record(path: pathlib.Path) -> dict:
    """Last recorded LAUNCH argv (skips `sandbox` subcommand probe records
    — Task 5's launch-time guard, if the Task-0 verdict required one)."""
    lines = [l for l in path.read_text().splitlines() if l.strip()]
    assert lines, f"fake codex wrote no launch record to {path}"
    launches = [
        r for r in map(json.loads, lines)
        if (r["argv"] or [""])[0] != "sandbox"
    ]
    assert launches, "no non-probe launch record found"
    return launches[-1]


@pytest.mark.asyncio
async def test_iframe_sandbox_args_wired(
    shim_install_dir: pathlib.Path,
    task_root,
    ctx_and_captures,
    tmp_path: pathlib.Path,
    monkeypatch,
):
    ctx, *_ = ctx_and_captures
    monkeypatch.setenv("FAKE_CODEX_SCENARIO", "happy")
    record = tmp_path / "codex_record.jsonl"
    monkeypatch.setenv("FAKE_CODEX_RECORD", str(record))

    task = create_codex_task(
        process_id="codex-sandbox-iframe",
        name="s",
        config=CodexTaskConfig(
            consumer_instructions="do the thing",
            install_dir=str(shim_install_dir),
            ttyd_install_dir=str(shim_install_dir),
            extra_allowed_dirs=[AllowedDir("/scratch", "rw")],
            network_access=True,
        ),
    )
    await task.execute(ctx)

    argv = _launch_record(record)["argv"]
    assert argv[argv.index("--sandbox") + 1] == "workspace-write"
    assert "danger-full-access" not in argv
    assert 'sandbox_workspace_write.writable_roots=["/scratch"]' in argv
    assert "sandbox_workspace_write.network_access=true" in argv


@pytest.mark.asyncio
async def test_iframe_unconfined_when_fs_isolation_off(
    shim_install_dir: pathlib.Path,
    task_root,
    ctx_and_captures,
    tmp_path: pathlib.Path,
    monkeypatch,
):
    ctx, *_ = ctx_and_captures
    monkeypatch.setenv("FAKE_CODEX_SCENARIO", "happy")
    record = tmp_path / "codex_record.jsonl"
    monkeypatch.setenv("FAKE_CODEX_RECORD", str(record))

    task = create_codex_task(
        process_id="codex-sandbox-off",
        name="s",
        config=CodexTaskConfig(
            consumer_instructions="do the thing",
            install_dir=str(shim_install_dir),
            ttyd_install_dir=str(shim_install_dir),
            fs_isolation=False,
        ),
    )
    await task.execute(ctx)

    argv = _launch_record(record)["argv"]
    assert argv[argv.index("--sandbox") + 1] == "danger-full-access"
    assert not any(a.startswith("sandbox_workspace_write.") for a in argv)
