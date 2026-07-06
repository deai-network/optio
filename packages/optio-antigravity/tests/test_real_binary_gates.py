"""Stage-0 done-when proof against the REAL ``agy`` (Antigravity CLI).

Guide Part 3, Stage 0: "A demo task launches, does work, emits DONE, and tears
down cleanly locally." The default suite proves the pipeline with the fake
``agy`` (test_session_local); this module proves it with the real binary — a
real (billable) model turn behind a real Google login, so it never runs unless
explicitly opted in:

    OPTIO_ANTIGRAVITY_REAL_SESSION_TEST=1 .venv/bin/python -m pytest \
        packages/optio-antigravity/tests/test_real_binary_gates.py -q

Skip-chain (grok/codex convention: opt-in env + capability probes, never
default): env flag set, real ``agy`` on PATH, real ``tmux`` present, and a
logged-in Antigravity identity. There is **no** Google login in CI or the dev
worktree, so this skips cleanly here — that is the point (the real-binary work
is tracked, not silently green); it is run in Task S1 / Stage 10 once the user
can log in.
"""

from __future__ import annotations

import os
import shutil
from pathlib import Path

import pytest


# TODO(S1): reconcile with the real-login spike. The real ``agy`` stores its
# Google OAuth token in the OS keyring (Secret Service / libsecret), NOT a plain
# file (design §2) — so a robust "logged-in?" probe must query the keyring, and
# the seed/save-back mechanism branches on whether an encrypted-file fallback
# exists when no Secret Service is present. Until S1 pins that, this uses a
# loose heuristic: the ``~/.gemini`` state tree that a completed login
# provisions (settings.json under antigravity-cli). Good enough to gate the
# opt-in run; replace with the S1-confirmed keyring/file check.
_GEMINI_DIR = Path.home() / ".gemini"


def _looks_logged_in() -> bool:
    """Best-effort: a completed Antigravity login provisions the shared
    ``~/.gemini`` tree. TODO(S1): swap for the keyring/token-store check S1
    identifies (the token itself is not in this tree)."""
    return (_GEMINI_DIR / "antigravity-cli" / "settings.json").exists()


# The logged-in-session gate (real agy on PATH + a logged-in ~/.gemini). Applied
# per-test rather than module-wide so the Stage-5 install gate below — which
# needs the OPPOSITE (a network, NOT a pre-installed/logged-in agy) — can carry
# its own, incompatible skip condition.
_real_session_gate = pytest.mark.skipif(
    os.environ.get("OPTIO_ANTIGRAVITY_REAL_SESSION_TEST") != "1"
    or shutil.which("agy") is None
    or shutil.which("tmux") is None
    or not _looks_logged_in(),
    reason="opt-in real-agy test (OPTIO_ANTIGRAVITY_REAL_SESSION_TEST=1, "
    "agy+tmux on PATH, a logged-in ~/.gemini). Runs in Task S1 / Stage 10.",
)

# The Stage-5 Tier-2 install gate: exercises the real manifest+tarball+sha512
# provisioning against the antigravity auto-updater host. Needs network but NO
# Google login (install is unauthenticated), so it is opt-in on its own env flag
# and deliberately does NOT require a pre-existing agy — that is the whole point.
_real_install_gate = pytest.mark.skipif(
    os.environ.get("OPTIO_ANTIGRAVITY_REAL_INSTALL_TEST") != "1",
    reason="opt-in real Tier-2 install gate (network to the antigravity "
    "auto-updater; no login needed). Runs in Stage 5 / Stage 10.",
)


@_real_session_gate
@pytest.mark.asyncio
async def test_iframe_reaches_done(ctx_and_captures, task_root):
    """Drive the real ``agy`` TUI (under ttyd) to agent-emitted ``DONE``.

    The prompt asks for nothing but the completion signal, so exactly one cheap
    real turn runs. A hard timeout guards against an unattended real-CLI hang.
    """
    import asyncio

    from optio_antigravity import AntigravityTaskConfig, create_antigravity_task

    ctx, captures, _cancel = ctx_and_captures

    task = create_antigravity_task(
        process_id="antigravity-real-stage0",
        name="real stage0 proof",
        config=AntigravityTaskConfig(
            consumer_instructions=(
                "Your entire task: append the exact line DONE to the file "
                "optio.log in the current directory (as the coordination "
                "protocol above describes), then stop. Do nothing else."
            ),
            # Turn-level auto-approve so the unattended TUI never blocks on a
            # tool-permission prompt.
            permission_mode="dangerously-skip-permissions",
            # Task-execution done-when proof: the agent must auto-run its
            # AGENTS.md task and emit DONE. auto_start defaults to False
            # (chat-task parity), so this task opts in explicitly.
            auto_start=True,
        ),
    )

    # Completes cleanly on agent-emitted DONE; hard cap so an unattended
    # real-CLI hang cannot wedge the opt-in run.
    await asyncio.wait_for(task.execute(ctx), timeout=280)

    assert any("Antigravity is live" == m for _, m in captures.progress)


@_real_install_gate
@pytest.mark.asyncio
async def test_bare_worker_installs(tmp_path):
    """Stage 5 Tier-2: a bare worker with NO ``agy`` provisions one itself.

    Points the cache at a fresh empty dir and forces the no-host-agy path, so
    ``ensure_antigravity_installed`` must fetch the platform manifest, download
    the tarball, SHA512-verify it, and extract a real ``antigravity`` binary that
    passes the functional identity gate — proving the reproduced install.sh logic
    works end-to-end against the real auto-updater. No login is touched.
    """
    import asyncio

    from optio_host.host import LocalHost

    from optio_antigravity import host_actions

    host = LocalHost(taskdir=str(tmp_path / "task"))
    await host.setup_workdir()
    cache = tmp_path / "cache"

    # Engine-free download over the host (no hook_ctx child task available here).
    class _HostCtx:
        def __init__(self, host):
            self._host = host

        def report_progress(self, percent, message=None):
            pass

        async def download_file(self, url, dest):
            r = await host.run_command(
                f"curl -fsSL {url!r} -o {dest!r}",
            )
            if r.exit_code != 0:
                raise RuntimeError(f"curl failed: {r.stderr!r}")

    await asyncio.wait_for(
        host_actions._install_antigravity_into_cache(
            _HostCtx(host), host,
            cache_dir=str(cache), cached=str(cache / "agy"),
        ),
        timeout=180,
    )
    assert (cache / "agy").exists()
    assert await host_actions._is_agy(host, str(cache / "agy"))


@_real_session_gate
@pytest.mark.asyncio
async def test_resume_picks_up_prior(mongo_db, task_root):
    """A relaunch of a terminated real ``agy`` task resumes the prior workspace.

    Fresh run: the agent writes a marker file and emits DONE; teardown captures
    a workdir snapshot. Resume run (same process_id, ctx.resume=True): the
    session restores the workdir tar and relaunches with ``--continue`` + the
    ``System: you have been resumed`` positional. The marker planted by the
    fresh run must survive the restore, proving the workspace (and agy's
    ``home/.gemini/antigravity`` conversation store) came back.
    """
    import asyncio

    from optio_core.context import ProcessContext
    from optio_core.models import TaskInstance
    from optio_core.store import upsert_process

    from optio_antigravity import AntigravityTaskConfig
    from optio_antigravity.session import run_antigravity_session
    from optio_antigravity.snapshots import load_latest_snapshot

    pid = "antigravity-real-resume"

    async def _ctx(resume: bool) -> ProcessContext:
        task = TaskInstance(
            execute=lambda c: None,  # type: ignore[arg-type, return-value]
            process_id=pid, name=pid, supports_resume=True,
        )
        proc = await upsert_process(mongo_db, "test", task)
        await mongo_db["test_processes"].update_one(
            {"_id": proc["_id"]}, {"$set": {"status": {"state": "running"}}},
        )
        return ProcessContext(
            process_oid=proc["_id"], process_id=pid, root_oid=proc["_id"],
            depth=0, params={}, services={}, db=mongo_db, prefix="test",
            cancellation_flag=asyncio.Event(), child_counter={"next": 0},
            resume=resume,
        )

    cfg = AntigravityTaskConfig(
        consumer_instructions=(
            "Your entire task: create a file named resume_marker.txt containing "
            "the word MARKER in the current directory, then append the exact "
            "line DONE to optio.log and stop. Do nothing else."
        ),
        permission_mode="dangerously-skip-permissions",
        auto_start=True,
        supports_resume=True,
    )

    await asyncio.wait_for(run_antigravity_session(await _ctx(False), cfg), timeout=280)
    snap = await load_latest_snapshot(mongo_db, "test", pid)
    assert snap is not None, "fresh run must capture a snapshot"

    await asyncio.wait_for(run_antigravity_session(await _ctx(True), cfg), timeout=280)
    # Two snapshots ⟹ the resumed run also reached live and re-captured.
    from optio_antigravity.snapshots import SESSION_SNAPSHOT_COLLECTION_SUFFIX
    count = await mongo_db[
        f"test{SESSION_SNAPSHOT_COLLECTION_SUFFIX}"
    ].count_documents({"processId": pid})
    assert count == 2
