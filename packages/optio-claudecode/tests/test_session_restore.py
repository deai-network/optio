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
        fs_isolation=False,
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
            fs_isolation=False,
        )


def test_restore_from_incompatible_with_auto_start():
    with pytest.raises(ValueError, match="auto_start"):
        _conv(session_restore_from=ObjectId(), auto_start=True)


import asyncio
import pathlib
import time as _time


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
        fs_isolation=False,
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


def _record_projects_hook(records: dict):
    """before_execute hook for restore-side sessions: snapshot the projects
    tree (it was planted during _prepare, which runs before this hook)."""
    async def before(hook_ctx):
        workdir = pathlib.Path(hook_ctx._host.workdir)
        records["workdir"] = str(workdir)
        proj = workdir / "home/.claude/projects"
        records["files"] = {
            str(p.relative_to(proj)): p.read_text()
            for p in proj.rglob("*.jsonl")
        } if proj.exists() else {}
    return before


async def _launch_restore_session(
    optio, shim_install_dir, claude_cache_dir, pid: str, blob_id, *,
    until: str | None = None, records: dict,
):
    task = create_claudecode_task(
        process_id=pid, name=pid,
        config=_flow_config(
            shim_install_dir, claude_cache_dir,
            session_restore_from=blob_id,
            session_restore_until=until,
            before_execute=_record_projects_hook(records),
        ),
    )
    await optio.adhoc_define(task)
    return await optio.launch_and_await_result(pid, session_id=None, timeout=60)


@pytest.mark.asyncio
async def test_restore_round_trip_rekeys_to_new_workdir(
    shim_install_dir, claude_cache_dir, task_root, mongo_db,
):
    optio = await _make_optio(mongo_db, "ccsr3")
    try:
        blob_id, _ = await _run_capture_session(
            optio, shim_install_dir, claude_cache_dir, "sr-rt-a",
        )
        records: dict = {}
        conv = await _launch_restore_session(
            optio, shim_install_dir, claude_cache_dir, "sr-rt-b", blob_id,
            records=records,
        )
        # Transcript landed under the NEW workdir's slug, content intact.
        slug = slugify_workdir(records["workdir"])
        assert list(records["files"]) == [f"{slug}/fixture.jsonl"]
        assert records["files"][f"{slug}/fixture.jsonl"] == _FIXTURE
        # Silent kickoff: our send is the fake's FIRST turn.
        msgs: asyncio.Queue[str] = asyncio.Queue()
        conv.on_message(msgs.put_nowait)
        await conv.send("ping")
        assert await asyncio.wait_for(msgs.get(), 10) == "reply-1"
        await conv.close()
        proc = await _wait_terminal(optio, "sr-rt-b")
        assert proc["status"]["state"] == "done"
    finally:
        await optio.shutdown(grace_seconds=1.0)


@pytest.mark.asyncio
async def test_restore_with_truncation(
    shim_install_dir, claude_cache_dir, task_root, mongo_db,
):
    optio = await _make_optio(mongo_db, "ccsr4")
    try:
        blob_id, _ = await _run_capture_session(
            optio, shim_install_dir, claude_cache_dir, "sr-tr-a",
        )
        records: dict = {}
        conv = await _launch_restore_session(
            optio, shim_install_dir, claude_cache_dir, "sr-tr-b", blob_id,
            until="u2", records=records,
        )
        slug = slugify_workdir(records["workdir"])
        text = records["files"][f"{slug}/fixture.jsonl"]
        assert '"u2"' in text
        assert '"uuid":"u3"' not in text  # u3 entry dropped
        await conv.close()
        await _wait_terminal(optio, "sr-tr-b")
    finally:
        await optio.shutdown(grace_seconds=1.0)


@pytest.mark.asyncio
async def test_restore_blob_without_transcript_fails(
    shim_install_dir, claude_cache_dir, task_root, mongo_db,
):
    from optio_core.exceptions import ResultNotPublished

    optio = await _make_optio(mongo_db, "ccsr5")
    try:
        # Capture session WITHOUT planting a transcript → blob has no *.jsonl.
        saved: list[tuple] = []
        task = create_claudecode_task(
            process_id="sr-nt-a", name="sr-nt-a",
            config=_flow_config(
                shim_install_dir, claude_cache_dir,
                on_session_saved=lambda b, s: saved.append((b, s)),
            ),
        )
        await optio.adhoc_define(task)
        conv = await optio.launch_and_await_result(
            "sr-nt-a", session_id=None, timeout=60,
        )
        await conv.close()
        await _wait_terminal(optio, "sr-nt-a")
        blob_id = saved[0][0]

        records: dict = {}
        task_b = create_claudecode_task(
            process_id="sr-nt-b", name="sr-nt-b",
            config=_flow_config(
                shim_install_dir, claude_cache_dir,
                session_restore_from=blob_id,
                before_execute=_record_projects_hook(records),
            ),
        )
        await optio.adhoc_define(task_b)
        with pytest.raises(ResultNotPublished):
            await optio.launch_and_await_result(
                "sr-nt-b", session_id=None, timeout=60,
            )
        proc = await optio.get_process("sr-nt-b")
        assert proc["status"]["state"] == "failed"
    finally:
        await optio.shutdown(grace_seconds=1.0)


@pytest.mark.asyncio
async def test_restore_directives_skipped_on_optio_resume(
    shim_install_dir, claude_cache_dir, task_root, mongo_db, caplog,
):
    """supports_resume task with restore directives: fresh run applies them;
    an optio-level resume skips them with a logged notice."""
    optio = await _make_optio(mongo_db, "ccsr6")
    try:
        blob_id, _ = await _run_capture_session(
            optio, shim_install_dir, claude_cache_dir, "sr-skip-src",
        )
        records: dict = {}
        task = create_claudecode_task(
            process_id="sr-skip", name="sr-skip",
            config=_flow_config(
                shim_install_dir, claude_cache_dir,
                supports_resume=True,
                session_restore_from=blob_id,
                before_execute=_record_projects_hook(records),
                # Snapshot capture refuses to mark resumable without
                # credentials on disk (see test_session_resume.py); plant
                # them so the resume run actually finds a snapshot.
                credentials_json={"token": "test"},
            ),
        )
        await optio.adhoc_define(task)
        conv = await optio.launch_and_await_result(
            "sr-skip", session_id=None, timeout=60,
        )
        await conv.close()
        await _wait_terminal(optio, "sr-skip")

        import logging
        with caplog.at_level(logging.INFO, logger="optio_claudecode.session"):
            conv2 = await optio.launch_and_await_result(
                "sr-skip", resume=True, session_id=None, timeout=60,
            )
            await conv2.close()
            proc = await _wait_terminal(optio, "sr-skip")
        assert proc["status"]["state"] == "done"
        assert "session_restore_from is skipped" in caplog.text
    finally:
        await optio.shutdown(grace_seconds=1.0)


def _plant_transcript_and_claude_json_hook(foreign_cwd: str):
    """Capture-side before_execute: plant a transcript AND a .claude.json whose
    `projects` is keyed to a FOREIGN workdir, so the restore-side rekey has
    something to fix."""
    async def before(hook_ctx):
        workdir = pathlib.Path(hook_ctx._host.workdir)
        pdir = workdir / "home/.claude/projects" / slugify_workdir(str(workdir))
        pdir.mkdir(parents=True, exist_ok=True)
        (pdir / "fixture.jsonl").write_text(_FIXTURE)
        cc = workdir / "home/.claude/.claude.json"
        cc.write_text(
            '{"projects":{"' + foreign_cwd + '":{"hasTrustDialogAccepted":true,"k":1}}}'
        )
    return before


def _record_claude_json_hook(records: dict):
    """Restore-side before_execute: capture .claude.json projects after _prepare."""
    async def before(hook_ctx):
        import json
        workdir = pathlib.Path(hook_ctx._host.workdir)
        records["workdir"] = str(workdir)
        cc = workdir / "home/.claude/.claude.json"
        records["claude_json"] = json.loads(cc.read_text()) if cc.exists() else None
    return before


@pytest.mark.asyncio
async def test_restore_rekeys_claude_json_projects_to_new_workdir(
    shim_install_dir, claude_cache_dir, task_root, mongo_db,
):
    """A restored .claude.json carries the ORIGINAL session's projects keys.
    Without a rekey, claude (running in the NEW workdir under CLAUDE_CONFIG_DIR)
    sees the new workdir untrusted -> folder-trust prompt -> bypassPermissions
    can't suppress -> hang. The session_restore_from path must collapse projects
    to the launch workdir with trust, like the optio-resume path does."""
    optio = await _make_optio(mongo_db, "ccsrk")
    try:
        saved: list = []
        task = create_claudecode_task(
            process_id="sr-rk-a", name="sr-rk-a",
            config=_flow_config(
                shim_install_dir, claude_cache_dir,
                before_execute=_plant_transcript_and_claude_json_hook("/old/cwd"),
                on_session_saved=lambda b, s: saved.append((b, s)),
            ),
        )
        await optio.adhoc_define(task)
        conv = await optio.launch_and_await_result("sr-rk-a", session_id=None, timeout=60)
        await conv.close()
        await _wait_terminal(optio, "sr-rk-a")
        blob_id, _ = saved[0]

        records: dict = {}
        task_b = create_claudecode_task(
            process_id="sr-rk-b", name="sr-rk-b",
            config=_flow_config(
                shim_install_dir, claude_cache_dir,
                session_restore_from=blob_id,
                before_execute=_record_claude_json_hook(records),
            ),
        )
        await optio.adhoc_define(task_b)
        conv2 = await optio.launch_and_await_result("sr-rk-b", session_id=None, timeout=60)
        await conv2.close()
        await _wait_terminal(optio, "sr-rk-b")

        cj = records["claude_json"]
        assert cj is not None, "restored .claude.json missing"
        assert list(cj["projects"].keys()) == [records["workdir"]], cj["projects"]
        assert cj["projects"][records["workdir"]]["hasTrustDialogAccepted"] is True
        assert "/old/cwd" not in cj["projects"]
    finally:
        await optio.shutdown(grace_seconds=1.0)
