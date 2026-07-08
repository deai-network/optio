"""Stage 8 — fail-closed filesystem isolation wired into both launch paths.

``CursorTaskConfig.fs_isolation`` defaults True, so these run the DEFAULT
config (no opt-in). The ``fake_claustrum`` autouse fixture stubs the real
Landlock build with a shim that execs its wrapped command, so the full wiring
(provision claustrum -> build grant flags -> wrap the cursor argv -> disable
cursor's own native sandbox) is exercised end-to-end. Kernel enforcement is
the env-gated test_sandbox_enforce.py (Task 3).
"""

from __future__ import annotations

import pathlib

import pytest

from optio_cursor import CursorTaskConfig, create_cursor_task
from optio_cursor import host_actions
from optio_cursor.session import run_cursor_session


# Spawns cursor-agent and touches shared home-dir paths; unsafe under
# concurrency. Marked `serial` for the final non-parallel phase.
pytestmark = pytest.mark.serial


@pytest.mark.asyncio
async def test_iframe_default_on_wraps_argv_and_disables_native_sandbox(
    shim_install_dir: pathlib.Path,
    ctx_and_captures,
    monkeypatch,
    tmp_path,
):
    """Default-on iframe launch: cursor-agent runs UNDER claustrum (grants +
    ``--`` separator) with its own native sandbox turned OFF. Proven via the
    durable claustrum-shim record (the workdir is wiped on teardown), the
    claustrum analogue of fake_grok's launch record."""
    ctx, *_ = ctx_and_captures
    record = tmp_path / "claustrum-launch.log"
    monkeypatch.setenv("FAKE_CURSOR_SCENARIO", "happy")
    monkeypatch.setenv("FAKE_CLAUSTRUM_RECORD", str(record))

    task = create_cursor_task(
        process_id="cursor-fs-iframe",
        name="fs",
        config=CursorTaskConfig(
            consumer_instructions="do it",
            install_dir=str(shim_install_dir),
            ttyd_install_dir=str(shim_install_dir),
            # fs_isolation defaults True — no opt-in. delivery_type is mandatory.
            delivery_type="audit",
        ),
    )
    await task.execute(ctx)

    assert record.exists(), "claustrum was not invoked (fs_isolation not wired)"
    line = record.read_text(encoding="utf-8")
    # claustrum flags + a workdir rwx grant + a baseline system grant.
    assert "--best-effort" in line and "--abi-min 1" in line
    assert "--rwx" in line and "--rox /usr" in line
    # The `--` separator, then the wrapped cursor-agent with native sandbox off.
    assert " -- " in line
    sep = line.index(" -- ")
    tail = line[sep + 4:]
    assert "cursor-agent" in tail
    assert "--sandbox disabled" in tail


@pytest.mark.asyncio
async def test_conversation_launch_is_claustrum_wrapped(
    shim_install_dir: pathlib.Path,
    ctx_and_captures,
    task_root,
    monkeypatch,
):
    """The headless conversation launch is claustrum-wrapped too (mirrors the
    iframe path): the launched command starts with claustrum, then execs
    cursor-agent past ``--`` with ``--sandbox disabled`` before ``acp``."""
    ctx, *_ = ctx_and_captures
    from optio_host.host import LocalHost

    captured: dict = {}

    async def _capture(self, cmd, **kw):
        captured["cmd"] = cmd
        raise RuntimeError("captured-launch")

    monkeypatch.setattr(LocalHost, "launch_subprocess", _capture)

    config = CursorTaskConfig(
        consumer_instructions="chat",
        mode="conversation",
        host_protocol=False,
        install_dir=str(shim_install_dir),
        ttyd_install_dir=str(shim_install_dir),
        # fs_isolation defaults True. delivery_type is mandatory.
        delivery_type="audit",
    )
    try:
        await run_cursor_session(ctx, config)
    except Exception:
        pass

    cmd = captured.get("cmd", "")
    assert "claustrum-shim.sh --best-effort --abi-min 1 " in cmd, cmd
    assert " -- " in cmd
    assert cmd.index("claustrum") < cmd.index("cursor-agent")
    tail = cmd[cmd.index(" -- ") + 4:]
    assert "--sandbox disabled" in tail
    assert tail.index("--sandbox") < tail.index("acp")


@pytest.mark.asyncio
async def test_fs_isolation_is_fail_closed(
    shim_install_dir: pathlib.Path,
    ctx_and_captures,
    monkeypatch,
):
    """Fail-closed (non-negotiable): when fs_isolation is on and claustrum
    cannot be provisioned (no Landlock / build failure), the task refuses to
    launch — it never falls back to an unconfined run."""
    ctx, *_ = ctx_and_captures
    monkeypatch.setenv("FAKE_CURSOR_SCENARIO", "happy")

    async def _boom(hook_ctx, *, install_dir=None):
        raise RuntimeError("claustrum unavailable: kernel lacks Landlock")

    monkeypatch.setattr(host_actions, "ensure_claustrum_installed", _boom)

    task = create_cursor_task(
        process_id="cursor-fs-failclosed",
        name="fc",
        config=CursorTaskConfig(
            consumer_instructions="do it",
            install_dir=str(shim_install_dir),
            ttyd_install_dir=str(shim_install_dir),
            delivery_type="audit",
        ),
    )
    with pytest.raises(RuntimeError, match="Landlock"):
        await task.execute(ctx)
