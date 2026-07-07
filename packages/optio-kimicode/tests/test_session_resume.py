"""Full-cycle resume + push-notification test for optio-kimicode.

Exercises the group-2 wiring against the fake kimi web server:

* a fresh ``auto_start`` launch POSTs the kickoff prompt to the pre-created
  session;
* a fresh non-``auto_start`` launch POSTs nothing;
* a relaunch (``ctx.resume``) restores the kimi session store (non-empty
  ``home/sessions`` at the resumed server's startup), POSTs the resume notice
  (``SYSTEM_MESSAGE_PREFIX + RESUME_NOTICE``) to the recovered session, appends
  a second line to ``resume.log``, and rotates the stale ``optio.log`` out of
  the way (``optio.log.old``) so the restored DONE is not replayed.

Prompts pushed by the wrapper are recorded by the fake to an external journal
(``FAKE_KIMI_PROMPTS_LOG``, outside the workdir so it survives teardown); the
session-store / resume.log / optio.log assertions read the terminal snapshot's
GridFS workdir blob.
"""

from __future__ import annotations

import asyncio
import io
import json
import os
import tarfile
from pathlib import Path

import pytest
import pytest_asyncio
from motor.motor_asyncio import AsyncIOMotorClient, AsyncIOMotorGridFSBucket

from optio_agents import RESUME_NOTICE, SYSTEM_MESSAGE_PREFIX
from optio_core.context import ProcessContext
from optio_core.models import TaskInstance
from optio_core.store import upsert_process

from optio_kimicode import KimiCodeTaskConfig
from optio_kimicode.session import AUTO_START_PROMPT, run_kimicode_session
from optio_kimicode.snapshots import (
    SESSION_SNAPSHOT_COLLECTION_SUFFIX,
    load_latest_snapshot,
)


@pytest_asyncio.fixture
async def mongo_db():
    client = AsyncIOMotorClient(os.environ.get("MONGO_URL", "mongodb://localhost:27017"))
    db_name = f"optio_kimicode_resume_{os.getpid()}"
    db = client[db_name]
    yield db
    await client.drop_database(db_name)
    client.close()


async def _make_ctx(mongo_db, process_id: str, *, resume: bool) -> ProcessContext:
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
        resume=resume,
    )


async def _run_cycle(
    mongo_db,
    shim_install_dir,
    monkeypatch,
    process_id: str,
    *,
    resume: bool,
    auto_start: bool,
    journal: Path,
    encrypt=None,
    decrypt=None,
) -> None:
    monkeypatch.setenv("FAKE_KIMI_SCENARIO", "happy")
    monkeypatch.setenv("FAKE_KIMI_PROMPTS_LOG", str(journal))
    ctx = await _make_ctx(mongo_db, process_id, resume=resume)
    cfg = KimiCodeTaskConfig(
        consumer_instructions=f"do the thing ({process_id})",
        install_dir=str(shim_install_dir),
        fs_isolation=False,
        supports_resume=True,
        auto_start=auto_start,
        session_blob_encrypt=encrypt,
        session_blob_decrypt=decrypt,
    )
    await run_kimicode_session(ctx, cfg)


def _read_journal(journal: Path) -> list[dict]:
    if not journal.exists():
        return []
    return [json.loads(line) for line in journal.read_text().splitlines() if line.strip()]


def _prompts(records: list[dict]) -> list[str]:
    return [r["text"] for r in records if r.get("kind") == "prompt"]


# Reversible at-rest cipher: reversing the gzip bytes yields an invalid tar, so
# a restore only succeeds if the matching decrypt runs (mirrors test_snapshots).
def _reverse(b: bytes) -> bytes:
    return b[::-1]


_GZIP_MAGIC = b"\x1f\x8b"


async def _extract_from_workdir_blob(mongo_db, snap: dict, member: str) -> str | None:
    bucket = AsyncIOMotorGridFSBucket(mongo_db)
    stream = await bucket.open_download_stream(snap["workdirBlobId"])
    workdir_bytes = await stream.read()
    with tarfile.open(fileobj=io.BytesIO(workdir_bytes), mode="r:gz") as tar:
        try:
            f = tar.extractfile(tar.getmember(member))
        except KeyError:
            return None
        return f.read().decode("utf-8") if f else None


@pytest.mark.asyncio
async def test_fresh_auto_start_posts_kickoff(
    mongo_db, shim_install_dir, task_root, tmp_path, monkeypatch,
):
    journal = tmp_path / "journal.jsonl"
    await _run_cycle(
        mongo_db, shim_install_dir, monkeypatch, "kimi_autostart",
        resume=False, auto_start=True, journal=journal,
    )
    prompts = _prompts(_read_journal(journal))
    assert AUTO_START_PROMPT in prompts, f"kickoff not posted; prompts={prompts!r}"


@pytest.mark.asyncio
async def test_fresh_non_auto_start_posts_nothing(
    mongo_db, shim_install_dir, task_root, tmp_path, monkeypatch,
):
    journal = tmp_path / "journal.jsonl"
    await _run_cycle(
        mongo_db, shim_install_dir, monkeypatch, "kimi_noauto",
        resume=False, auto_start=False, journal=journal,
    )
    records = _read_journal(journal)
    # A session is still pre-created (create record present) but NO prompt.
    assert any(r.get("kind") == "create" for r in records), "session was not pre-created"
    assert _prompts(records) == [], f"unexpected prompt(s): {_prompts(records)!r}"


@pytest.mark.asyncio
async def test_resume_restores_store_and_pushes_notice(
    mongo_db, shim_install_dir, task_root, tmp_path, monkeypatch,
):
    pid = "kimi_resume"
    j1 = tmp_path / "j1.jsonl"
    j2 = tmp_path / "j2.jsonl"

    # Cycle 1 — fresh launch WITH a kickoff prompt so the session holds a REAL
    # turn (recorded as state.json.lastPrompt): resume must recover THIS
    # conversation, not an empty session. Reaches DONE, captures a terminal
    # snapshot on teardown.
    await _run_cycle(
        mongo_db, shim_install_dir, monkeypatch, pid,
        resume=False, auto_start=True, journal=j1,
    )
    snap1 = await load_latest_snapshot(mongo_db, "test", pid)
    assert snap1 is not None, "fresh cycle did not capture a snapshot"

    # Cycle 2 — resume: restores the session store, pushes the resume notice.
    await _run_cycle(
        mongo_db, shim_install_dir, monkeypatch, pid,
        resume=True, auto_start=False, journal=j2,
    )

    records = _read_journal(j2)

    # (a) The resumed server saw the restored session store at startup.
    startup = next((r for r in records if r.get("kind") == "startup"), None)
    assert startup is not None and startup["session_files"] > 0, (
        f"resumed server saw no restored session store: {startup!r}"
    )

    # (b) The resume notice was POSTed to the session.
    notice = f"{SYSTEM_MESSAGE_PREFIX}{RESUME_NOTICE}"
    assert notice in _prompts(records), (
        f"resume notice not pushed; prompts={_prompts(records)!r}"
    )

    # A resume must NOT create a new session (it reuses the recovered id).
    assert not any(r.get("kind") == "create" for r in records), (
        "resume created a fresh session instead of reusing the restored one"
    )

    # (c) resume.log gained a second line; (d) optio.log was rotated to .old.
    snap2 = await load_latest_snapshot(mongo_db, "test", pid)
    assert snap2 is not None
    count = await mongo_db[f"test{SESSION_SNAPSHOT_COLLECTION_SUFFIX}"].count_documents(
        {"processId": pid}
    )
    assert count == 2, f"expected 2 snapshots after resume, got {count}"

    resume_log = await _extract_from_workdir_blob(mongo_db, snap2, "resume.log")
    assert resume_log is not None, "resume.log absent from resumed snapshot"
    lines = [ln for ln in resume_log.splitlines() if ln.strip()]
    assert len(lines) == 2, f"expected 2 resume.log lines, got {len(lines)}: {lines!r}"

    optio_old = await _extract_from_workdir_blob(mongo_db, snap2, "optio.log.old")
    assert optio_old is not None and "DONE" in optio_old, (
        "optio.log was not rotated to optio.log.old on resume (stale DONE guard)"
    )


@pytest.mark.asyncio
async def test_resume_roundtrips_with_encrypted_session_blob(
    mongo_db, shim_install_dir, task_root, tmp_path, monkeypatch,
):
    """When a cipher is supplied, the session blob is encrypted AT REST and a
    resume still round-trips (decrypt runs on restore)."""
    pid = "kimi_encrypted"
    j1 = tmp_path / "j1.jsonl"
    j2 = tmp_path / "j2.jsonl"

    # Cycle 1 — fresh launch with an at-rest cipher supplied.
    await _run_cycle(
        mongo_db, shim_install_dir, monkeypatch, pid,
        resume=False, auto_start=False, journal=j1,
        encrypt=_reverse, decrypt=_reverse,
    )
    snap1 = await load_latest_snapshot(mongo_db, "test", pid)
    assert snap1 is not None, "fresh cycle did not capture a snapshot"

    # The session blob is encrypted at rest: the raw stored bytes are the
    # reversed gzip, so reversing them (only) recovers the gzip magic. If the
    # cipher were NOT wired through, the raw bytes would be a plaintext gzip and
    # this discriminator fails.
    bucket = AsyncIOMotorGridFSBucket(mongo_db)
    stream = await bucket.open_download_stream(snap1["sessionBlobId"])
    raw = await stream.read()
    assert not raw.startswith(_GZIP_MAGIC), (
        "session blob stored in plaintext — cipher not wired through capture"
    )
    assert _reverse(raw).startswith(_GZIP_MAGIC), (
        "reversed session blob is not a gzip — unexpected at-rest encoding"
    )

    # Cycle 2 — resume with the SAME cipher: decrypt must run for the restored
    # session store to be usable by the resumed server.
    await _run_cycle(
        mongo_db, shim_install_dir, monkeypatch, pid,
        resume=True, auto_start=False, journal=j2,
        encrypt=_reverse, decrypt=_reverse,
    )
    records = _read_journal(j2)
    startup = next((r for r in records if r.get("kind") == "startup"), None)
    assert startup is not None and startup["session_files"] > 0, (
        f"resumed server saw no restored session store (decrypt failed?): {startup!r}"
    )
    notice = f"{SYSTEM_MESSAGE_PREFIX}{RESUME_NOTICE}"
    assert notice in _prompts(records), (
        f"resume notice not pushed; prompts={_prompts(records)!r}"
    )
