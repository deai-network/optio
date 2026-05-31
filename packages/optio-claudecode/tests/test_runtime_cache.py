"""Integration: claude provisioned from the shared version cache.

Uses the ttyd + claude shims and a pre-populated fake cache so no real
install.sh download happens. Asserts: cache reuse (no install), the versions
symlink points at the cache, and a captured snapshot does not contain the
240 MB binary (it lives in the cache, outside the workdir).
"""

from __future__ import annotations

import asyncio
import io
import pathlib
import tarfile

import pytest
from motor.motor_asyncio import AsyncIOMotorGridFSBucket

from optio_core.context import ProcessContext
from optio_core.models import TaskInstance
from optio_core.store import upsert_process

from optio_claudecode import ClaudeCodeTaskConfig, create_claudecode_task


async def _make_resume_ctx(mongo_db, process_id: str) -> ProcessContext:
    """A resume-capable ProcessContext whose process doc has supportsResume=True
    and whose process_id matches the task, so snapshot capture keys to it."""
    task = TaskInstance(
        execute=lambda c: None,  # type: ignore[arg-type, return-value]
        process_id=process_id, name=process_id, supports_resume=True,
    )
    proc = await upsert_process(mongo_db, "test", task)
    await mongo_db["test_processes"].update_one(
        {"_id": proc["_id"]}, {"$set": {"status": {"state": "running"}}},
    )
    return ProcessContext(
        process_oid=proc["_id"],
        process_id=process_id,
        root_oid=proc["_id"],
        depth=0,
        params={},
        services={},
        db=mongo_db,
        prefix="test",
        cancellation_flag=asyncio.Event(),
        child_counter={"next": 0},
    )


@pytest.mark.asyncio
async def test_cache_hit_reuses_without_install(
    shim_install_dir: pathlib.Path,
    claude_cache_dir: pathlib.Path,
    ctx_and_captures,
    monkeypatch,
):
    ctx, _cap, _flag = ctx_and_captures
    monkeypatch.setenv("FAKE_CLAUDE_SCENARIO", "happy")

    observed: dict[str, object] = {}

    async def probe(hook_ctx):
        wd = pathlib.Path(hook_ctx._host.workdir)
        versions = wd / "home" / ".local" / "share" / "claude" / "versions"
        observed["versions_is_symlink"] = versions.is_symlink()
        observed["versions_target"] = str(versions.resolve())
        observed["bin_claude_exists"] = (wd / "home" / ".local" / "bin" / "claude").exists()

    task = create_claudecode_task(
        process_id="cc-cache-hit",
        name="Cache hit",
        config=ClaudeCodeTaskConfig(
            consumer_instructions="hi",
            permission_mode="bypassPermissions",
            claude_install_dir=str(claude_cache_dir),  # cache override
            ttyd_install_dir=str(shim_install_dir),
            supports_resume=False,
            before_execute=probe,
        ),
    )
    await task.execute(ctx)

    assert observed["versions_is_symlink"] is True
    # versions → the cache override dir
    assert observed["versions_target"] == str(claude_cache_dir.resolve())
    assert observed["bin_claude_exists"] is True


@pytest.mark.asyncio
async def test_snapshot_excludes_claude_binary(
    shim_install_dir: pathlib.Path,
    claude_cache_dir: pathlib.Path,
    mongo_db,
    monkeypatch,
):
    """A resume-enabled session's workdir snapshot must not contain the cache
    binary (it is reachable only through the versions symlink, outside workdir)."""
    monkeypatch.setenv("FAKE_CLAUDE_SCENARIO", "happy")
    ctx = await _make_resume_ctx(mongo_db, "cc-cache-snap")
    task = create_claudecode_task(
        process_id="cc-cache-snap",
        name="Cache snap",
        config=ClaudeCodeTaskConfig(
            consumer_instructions="hi",
            permission_mode="bypassPermissions",
            claude_install_dir=str(claude_cache_dir),
            ttyd_install_dir=str(shim_install_dir),
            supports_resume=True,
        ),
    )
    await task.execute(ctx)

    # Load the latest snapshot's workdir blob and assert no cache version file
    # content was captured (only a symlink entry, or nothing, for versions).
    from optio_claudecode.snapshots import load_latest_snapshot
    snap = await load_latest_snapshot(mongo_db, prefix="test", process_id="cc-cache-snap")
    assert snap is not None
    bucket = AsyncIOMotorGridFSBucket(mongo_db)
    stream = await bucket.open_download_stream(snap["workdirBlobId"])
    blob = await stream.read()
    with tarfile.open(fileobj=io.BytesIO(blob), mode="r:gz") as tar:
        members = tar.getmembers()
    # No regular file under .local/share/claude/versions/ (a symlink member is
    # fine; a regular file would mean the 240 MB binary was captured).
    offending = [
        m.name for m in members
        if "/.local/share/claude/versions/" in ("/" + m.name) and m.isfile()
    ]
    assert offending == [], offending
