"""Opt-in real-codex E2E for the seed + resume + remote surfaces (never default).

Guide Testing Layer 2 checklist rows: seed replant (a fresh task starts
already-authenticated), resume (a relaunch continues the prior codex session),
and remote-SSH (path/tty/callback assumptions that hold locally routinely break
remote). Billable/slow → opt-in:

    OPTIO_CODEX_SEED_RESUME_TEST=1 .venv/bin/python -m pytest \
        packages/optio-codex/tests/test_real_codex_seed_resume.py -q

Skip-chain (grok convention): env flag set, real ``codex`` + ``tmux`` on PATH,
authed ``~/.codex/auth.json``. The remote sub-case additionally requires
``OPTIO_CODEX_DEMO_SSH_HOST`` (a remote with real codex+tmux and the operator
identity planted through ``before_execute``), so it skips locally.
"""

from __future__ import annotations

import asyncio
import json
import os
import shutil
from pathlib import Path

import pytest

from optio_core.context import ProcessContext
from optio_core.models import TaskInstance
from optio_core.store import upsert_process

from optio_codex import CodexTaskConfig, SSHConfig
from optio_codex.seed_manifest import CODEX_SEED_SUFFIX
from optio_codex.session import run_codex_session
from optio_codex.snapshots import load_latest_snapshot

REAL_HOME_CODEX = Path.home() / ".codex"

_DONE_INSTRUCTIONS = (
    "Your entire task: append the exact line DONE to the file optio.log in the "
    "current directory (as the coordination protocol above describes), then "
    "stop. Do nothing else."
)


def _authed() -> bool:
    try:
        data = json.loads((REAL_HOME_CODEX / "auth.json").read_text())
    except (OSError, ValueError):
        return False
    return bool(data.get("tokens") or data.get("OPENAI_API_KEY"))


pytestmark = pytest.mark.skipif(
    os.environ.get("OPTIO_CODEX_SEED_RESUME_TEST") != "1"
    or shutil.which("codex") is None
    or shutil.which("tmux") is None
    or not _authed(),
    reason="opt-in real-codex seed/resume test (OPTIO_CODEX_SEED_RESUME_TEST=1, "
    "codex+tmux on PATH, authed ~/.codex/auth.json)",
)


async def _make_ctx(
    mongo_db, process_id: str, *, resume: bool = False, supports_resume: bool = False,
) -> tuple[ProcessContext, list[tuple[float | None, str | None]]]:
    """A running ProcessContext whose ``report_progress`` is captured, mirroring
    the fake-harness helpers (test_session_seed / test_session_resume) but usable
    against the real binary."""
    task = TaskInstance(
        execute=lambda c: None,  # type: ignore[arg-type, return-value]
        process_id=process_id, name=process_id, supports_resume=supports_resume,
    )
    proc = await upsert_process(mongo_db, "test", task)
    await mongo_db["test_processes"].update_one(
        {"_id": proc["_id"]}, {"$set": {"status": {"state": "running"}}},
    )
    ctx = ProcessContext(
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
    progress: list[tuple[float | None, str | None]] = []
    original = ctx.report_progress

    async def _report(percent=None, message=None):  # noqa: ANN001
        progress.append((percent, message))
        return await original(percent=percent, message=message)

    ctx.report_progress = _report  # type: ignore[method-assign]
    return ctx, progress


async def _plant_identity(hook_ctx):
    host = hook_ctx._host
    await host.write_text(
        "home/.codex/auth.json", (REAL_HOME_CODEX / "auth.json").read_text())
    await host.write_text(
        "home/.codex/config.toml",
        f'[projects."{host.workdir}"]\ntrust_level = "trusted"\n',
    )


@pytest.mark.asyncio
async def test_seed_capture_then_replant(mongo_db, task_root):
    """Capture a seed from the operator identity, then launch a FRESH seeded
    task and assert it runs already-authenticated (reaches 'Codex is live' and
    completes) without any interactive login."""
    captured: list[str] = []

    async def on_seed_saved(seed_id: str, info: str | None) -> None:
        captured.append(seed_id)

    ctx_src, _ = await _make_ctx(mongo_db, "codex-real-seed-src")
    await asyncio.wait_for(
        run_codex_session(
            ctx_src,
            CodexTaskConfig(
                consumer_instructions=_DONE_INSTRUCTIONS,
                before_execute=_plant_identity,
                on_seed_saved=on_seed_saved,
            ),
        ),
        timeout=280,
    )
    assert captured, "no seed captured from the planted operator identity"
    seed_id = captured[0]
    coll = mongo_db[f"test{CODEX_SEED_SUFFIX}"]
    assert await coll.count_documents({}) >= 1

    # Consume: a NEW task carrying only seed_id — no before_execute plant. If
    # the seed replanted the identity before launch, codex is authenticated and
    # the session reaches 'Codex is live' + completes.
    ctx_dst, progress = await _make_ctx(mongo_db, "codex-real-seed-dst")
    await asyncio.wait_for(
        run_codex_session(
            ctx_dst,
            CodexTaskConfig(
                consumer_instructions=_DONE_INSTRUCTIONS, seed_id=seed_id,
            ),
        ),
        timeout=280,
    )
    assert any(m == "Codex is live" for _, m in progress), progress


@pytest.mark.asyncio
async def test_resume_relaunch_picks_up_session(mongo_db, task_root):
    """Run a real iframe task with supports_resume=True to a snapshot, then
    relaunch with ctx.resume=True and assert it continues the SAME codex session
    id (snapshot sessionId round-trip). The Gap-1 'you have been resumed' notice
    positional on the relaunch argv is asserted against the fake in
    test_session_resume.py (no argv log exists for the real binary)."""
    pid = "codex-real-resume"
    cfg = CodexTaskConfig(
        consumer_instructions=_DONE_INSTRUCTIONS,
        before_execute=_plant_identity,
        supports_resume=True,
    )

    ctx_fresh, _ = await _make_ctx(mongo_db, pid, resume=False, supports_resume=True)
    await asyncio.wait_for(run_codex_session(ctx_fresh, cfg), timeout=280)
    snap1 = await load_latest_snapshot(mongo_db, "test", pid)
    assert snap1 is not None and snap1.get("sessionId")
    session_id = snap1["sessionId"]

    ctx_resume, _ = await _make_ctx(mongo_db, pid, resume=True, supports_resume=True)
    await asyncio.wait_for(run_codex_session(ctx_resume, cfg), timeout=280)
    snap2 = await load_latest_snapshot(mongo_db, "test", pid)
    assert snap2 is not None
    assert snap2["sessionId"] == session_id, (snap2.get("sessionId"), session_id)


@pytest.mark.asyncio
@pytest.mark.skipif(
    not os.environ.get("OPTIO_CODEX_DEMO_SSH_HOST"),
    reason="remote real-codex E2E needs OPTIO_CODEX_DEMO_SSH_HOST",
)
async def test_remote_iframe_surface_end_to_end(mongo_db, task_root):
    """At least one surface end-to-end over SSH (guide row: remote path/tty
    assumptions break where local passes). Plant the operator identity remotely
    via before_execute and assert the session reaches 'Codex is live'."""
    host = os.environ["OPTIO_CODEX_DEMO_SSH_HOST"]
    user = (
        os.environ.get("OPTIO_CODEX_DEMO_SSH_USER") or os.environ.get("USER") or "root"
    )
    key_path = os.environ.get(
        "OPTIO_CODEX_DEMO_SSH_KEY_PATH", os.path.expanduser("~/.ssh/id_ed25519"),
    )
    port = int(os.environ.get("OPTIO_CODEX_DEMO_SSH_PORT", "22"))

    ctx, progress = await _make_ctx(mongo_db, "codex-real-remote")
    await asyncio.wait_for(
        run_codex_session(
            ctx,
            CodexTaskConfig(
                consumer_instructions=_DONE_INSTRUCTIONS,
                ssh=SSHConfig(host=host, user=user, key_path=key_path, port=port),
                before_execute=_plant_identity,
            ),
        ),
        timeout=300,
    )
    assert any(m == "Codex is live" for _, m in progress), progress
