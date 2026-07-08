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
            delivery_type="audit",
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
async def test_iframe_no_claustrum_when_fs_isolation_off(
    shim_install_dir: pathlib.Path,
    task_root,
    ctx_and_captures,
    tmp_path: pathlib.Path,
    monkeypatch,
):
    """fs_isolation=False → codex launches WITHOUT the claustrum wrap (no
    Landlock confinement). The NATIVE mode is decoupled now, so it still
    defaults to workspace-write (it only carries the network posture); an
    explicit sandbox='danger-full-access' would be needed to free it."""
    ctx, *_ = ctx_and_captures
    monkeypatch.setenv("FAKE_CODEX_SCENARIO", "happy")
    record = tmp_path / "codex_record.jsonl"
    monkeypatch.setenv("FAKE_CODEX_RECORD", str(record))
    claustrum_record = tmp_path / "claustrum_record.log"
    monkeypatch.setenv("FAKE_CLAUSTRUM_RECORD", str(claustrum_record))

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

    # Claustrum was never invoked — the launch is unconfined by Landlock.
    assert not claustrum_record.exists()
    argv = _launch_record(record)["argv"]
    # Native mode is decoupled from fs_isolation: default stays workspace-write.
    assert argv[argv.index("--sandbox") + 1] == "workspace-write"


@pytest.mark.asyncio
async def test_iframe_launch_is_claustrum_wrapped(
    shim_install_dir: pathlib.Path,
    task_root,
    ctx_and_captures,
    tmp_path: pathlib.Path,
    monkeypatch,
):
    """Default-on fs_isolation → codex runs UNDER claustrum: the claustrum
    record shows the grant flags + the `--` separator, and the wrapped command
    is codex."""
    ctx, *_ = ctx_and_captures
    monkeypatch.setenv("FAKE_CODEX_SCENARIO", "happy")
    record = tmp_path / "codex_record.jsonl"
    monkeypatch.setenv("FAKE_CODEX_RECORD", str(record))
    claustrum_record = tmp_path / "claustrum_record.log"
    monkeypatch.setenv("FAKE_CLAUSTRUM_RECORD", str(claustrum_record))

    task = create_codex_task(
        process_id="codex-sandbox-wrapped",
        name="s",
        config=CodexTaskConfig(
            consumer_instructions="do the thing",
            delivery_type="audit",
            install_dir=str(shim_install_dir),
            ttyd_install_dir=str(shim_install_dir),
        ),
    )
    await task.execute(ctx)

    assert claustrum_record.exists(), "claustrum was not invoked (wrap not wired)"
    line = claustrum_record.read_text().splitlines()[-1]
    assert line.startswith("--best-effort --abi-min 1 ")
    assert " -- " in line
    # a workdir rwx grant + a system baseline grant reached claustrum
    assert "--rwx" in line and "/usr" in line
    # the wrapped command is codex (after the -- separator)
    assert "codex" in line.split(" -- ", 1)[1]
