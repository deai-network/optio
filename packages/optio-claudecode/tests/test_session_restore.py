"""Explicit session restore: config validation + capture/restore session flows.

Spec: docs/2026-06-10-claudecode-session-restore-design.md
"""
from __future__ import annotations

import pytest
from bson import ObjectId

from optio_claudecode import ClaudeCodeTaskConfig


def _conv(**kw) -> ClaudeCodeTaskConfig:
    base = dict(
        consumer_instructions="x",
        mode="conversation",
        permission_mode="bypassPermissions",
    )
    base.update(kw)
    return ClaudeCodeTaskConfig(**base)


def test_restore_fields_default_off_and_valid_combo():
    cfg = _conv(
        session_restore_from=ObjectId(),
        session_restore_until="some-uuid",
        on_session_saved=lambda blob_id, end_state: None,
        model="claude-opus-4-8",
    )
    assert cfg.session_restore_until == "some-uuid"
    plain = _conv()
    assert plain.session_restore_from is None
    assert plain.session_restore_until is None
    assert plain.on_session_saved is None
    assert plain.model is None


def test_restore_until_requires_restore_from():
    with pytest.raises(ValueError, match="session_restore_until"):
        _conv(session_restore_until="some-uuid")


def test_restore_from_requires_conversation_mode():
    with pytest.raises(ValueError, match="conversation"):
        ClaudeCodeTaskConfig(
            consumer_instructions="x",
            mode="iframe",
            session_restore_from=ObjectId(),
        )


def test_restore_from_incompatible_with_auto_start():
    with pytest.raises(ValueError, match="auto_start"):
        _conv(session_restore_from=ObjectId(), auto_start=True)


import asyncio
import pathlib
import time as _time

from bson import ObjectId as _OID  # noqa: F401  (clarity in asserts)

from optio_core.lifecycle import Optio

from optio_claudecode import create_claudecode_task
from optio_claudecode.transcript import slugify_workdir

_TERMINAL = {"done", "failed", "cancelled"}

# A minimal valid transcript planted by before_execute on capture-side sessions.
_FIXTURE_LINES = [
    '{"type":"last-prompt","leafUuid":"u3","sessionId":"s"}',
    '{"uuid":"u1","parentUuid":null,"isSidechain":false,"sessionId":"s","type":"user"}',
    '{"uuid":"u2","parentUuid":"u1","isSidechain":false,"sessionId":"s","type":"assistant"}',
    '{"uuid":"u3","parentUuid":"u2","isSidechain":false,"sessionId":"s","type":"user"}',
]
_FIXTURE = "\n".join(_FIXTURE_LINES) + "\n"


async def _make_optio(mongo_db, prefix: str) -> Optio:
    optio = Optio()
    await optio.init(mongo_db=mongo_db, prefix=prefix)
    return optio


async def _wait_terminal(optio: Optio, process_id: str, timeout: float = 30.0) -> dict:
    end = _time.monotonic() + timeout
    while _time.monotonic() < end:
        proc = await optio.get_process(process_id)
        if proc is not None and proc["status"]["state"] in _TERMINAL:
            return proc
        await asyncio.sleep(0.05)
    raise AssertionError(f"{process_id} did not reach terminal state in {timeout}s")


def _flow_config(shim_install_dir, claude_cache_dir, **kw):
    base = dict(
        consumer_instructions="Converse with the test.",
        mode="conversation",
        permission_mode="bypassPermissions",
        claude_install_dir=str(claude_cache_dir),
        ttyd_install_dir=str(shim_install_dir),
        supports_resume=False,
    )
    base.update(kw)
    return ClaudeCodeTaskConfig(**base)


def _plant_transcript_hook(records: dict | None = None):
    """before_execute hook: write a fixture transcript into home/.claude
    so capture has something real to save (the fake claude writes none)."""
    async def before(hook_ctx):
        workdir = pathlib.Path(hook_ctx._host.workdir)
        pdir = workdir / "home/.claude/projects" / slugify_workdir(str(workdir))
        pdir.mkdir(parents=True, exist_ok=True)
        (pdir / "fixture.jsonl").write_text(_FIXTURE)
        if records is not None:
            records["workdir"] = str(workdir)
    return before


async def _run_capture_session(
    optio, shim_install_dir, claude_cache_dir, pid: str, *, cancel: bool = False,
):
    """Run one conversation session that plants a transcript and captures it.
    Returns the (blob_id, end_state) the callback received."""
    saved: list[tuple] = []
    task = create_claudecode_task(
        process_id=pid, name=pid,
        config=_flow_config(
            shim_install_dir, claude_cache_dir,
            before_execute=_plant_transcript_hook(),
            on_session_saved=lambda blob_id, end_state: saved.append(
                (blob_id, end_state)
            ),
        ),
    )
    await optio.adhoc_define(task)
    conv = await optio.launch_and_await_result(pid, session_id=None, timeout=60)
    if cancel:
        await optio.cancel(pid)
    else:
        await conv.close()
    await _wait_terminal(optio, pid)
    assert len(saved) == 1, f"on_session_saved fired {len(saved)} times"
    return saved[0]


@pytest.mark.asyncio
async def test_capture_on_close_fires_callback_done(
    shim_install_dir, claude_cache_dir, task_root, mongo_db,
):
    optio = await _make_optio(mongo_db, "ccsr1")
    try:
        blob_id, end_state = await _run_capture_session(
            optio, shim_install_dir, claude_cache_dir, "sr-cap-1",
        )
        assert isinstance(blob_id, ObjectId)
        assert end_state == "done"
    finally:
        await optio.shutdown(grace_seconds=1.0)


@pytest.mark.asyncio
async def test_capture_on_cancel_fires_callback_cancelled(
    shim_install_dir, claude_cache_dir, task_root, mongo_db,
):
    optio = await _make_optio(mongo_db, "ccsr2")
    try:
        blob_id, end_state = await _run_capture_session(
            optio, shim_install_dir, claude_cache_dir, "sr-cap-2", cancel=True,
        )
        assert isinstance(blob_id, ObjectId)
        assert end_state == "cancelled"
    finally:
        await optio.shutdown(grace_seconds=1.0)
