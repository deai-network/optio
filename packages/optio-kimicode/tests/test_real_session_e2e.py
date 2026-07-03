"""Full-surface real-kimi E2E (row-30 checklist, driven through the session).

Everything here drives a REAL, AUTHENTICATED ``kimi`` through the actual
optio-kimicode session pipeline (``create_kimicode_task(...).execute(ctx)`` /
``run_kimicode_session``) — the fake harness is never involved. These are the
billable, auth-dependent legs of Appendix A row 30:

* ``test_real_iframe_reaches_done``            — checklist 1 (iframe launch/render/DONE)
* ``test_real_conversation_stream_tool_turn``  — checklist 2 (ACP stream/tool/turn)
* ``test_real_first_login_device_code_captures_seed`` — checklist 4 (device-code→seed)
* ``test_real_seed_replant_starts_authed``     — checklist 5 (seed replant→authed)
* ``test_real_resume_picks_up_prior_session``  — checklist 6 (resume)
* ``test_real_remote_ssh_one_surface``         — checklist 7 (remote SSH)

(Checklist 3 — each surface with fs-isolation ON — is the two
``test_*_sandbox_enforce.py`` files, so these keep ``fs_isolation=False`` to
isolate the surface under test.)

All are OFF by default and skip with a precise reason (see ``realbin`` +
``test_real_binary_gates.py``). No real authed kimi exists in this worktree, so
they skip here; that is intentional — the remaining real-binary work is tracked
in docs/2026-07-03-optio-kimicode-parity.md, not silently asserted green.

Extra opt-in inputs some legs need:
* ``OPTIO_KIMICODE_REAL_SEED_ID``   — a captured seed id (replant test)
* ``OPTIO_KIMICODE_REAL_SSH_HOST``  — an SSH host (+ ``_USER`` / ``_KEY_PATH`` /
  ``_PORT``) for the remote leg
* ``OPTIO_KIMICODE_REAL_DEVICE_LOGIN`` — acknowledges the device-code leg is
  INTERACTIVE (a human completes the login URL in a browser); it cannot run
  unattended and stays skipped otherwise.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from optio_kimicode import KimiCodeTaskConfig, SSHConfig, create_kimicode_task, list_seeds

from realbin import real_kimi_skip_reason, resolve_real_kimi

_FLAG = "OPTIO_KIMICODE_REAL_E2E"

# Module gate: opt-in flag + a real authed kimi. Each test adds its own extra
# precondition below (seed id / ssh host / interactive ack).
pytestmark = pytest.mark.skipif(
    real_kimi_skip_reason(_FLAG, need_creds=True) is not None,
    reason=real_kimi_skip_reason(_FLAG, need_creds=True) or "",
)


def _real_install_dir() -> str:
    """The directory holding the real kimi binary (used as kimi_install_dir → a
    cache HIT that links the real kimi into the task launch path)."""
    kimi = resolve_real_kimi()
    assert kimi is not None  # guaranteed by the module gate
    return str(Path(kimi).resolve().parent)


def _cfg(**overrides) -> KimiCodeTaskConfig:
    base = dict(
        kimi_install_dir=_real_install_dir(),
        fs_isolation=False,        # fs-iso covered by the sandbox_enforce tests
        supports_resume=False,
        auto_start=True,           # drive the first turn unattended
        host_protocol=True,        # DONE is the completion signal
    )
    base.update(overrides)
    return KimiCodeTaskConfig(**base)


_DONE_INSTRUCTION = (
    'Use your shell tool to run exactly: printf "DONE\\n" >> ./optio.log\n'
    "Then stop. Do nothing else."
)


@pytest.mark.asyncio
async def test_real_iframe_reaches_done(ctx_and_captures, task_root, tmp_path):
    """Checklist 1: real ``kimi web`` launches, the iframe widget is registered
    with the bearer token, the agent drives itself to DONE via optio.log, and the
    session tears down cleanly."""
    ctx, cap, _ = ctx_and_captures
    task = create_kimicode_task(
        process_id="kimi-real-iframe",
        name="real-iframe",
        config=_cfg(mode="iframe", consumer_instructions=_DONE_INSTRUCTION),
    )
    await task.execute(ctx)  # returns only if DONE was observed

    assert cap.widget_data, "no iframe widget registered"
    assert "iframeSrc" in cap.widget_data[-1]
    assert cap.widget_upstream, "no tunnel upstream published for the kimi server"
    assert task.ui_widget == "iframe"


@pytest.mark.asyncio
async def test_real_conversation_stream_tool_turn(ctx_and_captures, task_root, tmp_path):
    """Checklist 2: real ``kimi acp`` handshakes, streams the answer, runs a tool
    (the shell write), and completes the turn — the whole thing reaching DONE
    through the conversation-mode session."""
    ctx, cap, _ = ctx_and_captures
    task = create_kimicode_task(
        process_id="kimi-real-conversation",
        name="real-conversation",
        config=_cfg(mode="conversation", consumer_instructions=_DONE_INSTRUCTION),
    )
    await task.execute(ctx)  # a full real ACP turn that ends at DONE


@pytest.mark.skipif(
    os.environ.get("OPTIO_KIMICODE_REAL_DEVICE_LOGIN") != "1",
    reason=(
        "interactive: the device-code first-login requires a human to open the "
        "verification URL in a browser — set OPTIO_KIMICODE_REAL_DEVICE_LOGIN=1 "
        "and complete the login when prompted"
    ),
)
@pytest.mark.asyncio
async def test_real_first_login_device_code_captures_seed(
    mongo_db, ctx_and_captures, task_root, tmp_path,
):
    """Checklist 4: a fresh (un-seeded) task where the operator completes the
    RFC-8628 device-code login; on teardown the authenticated environment is
    captured as a reusable seed (``on_seed_saved`` fires; the seed store grows).

    Requires a real kimi and human interaction — hence its own opt-in flag on top
    of the module gate. Note: this test needs a kimi WITHOUT pre-existing creds so
    the CLI actually initiates the device flow (point ``KIMI_CODE_HOME`` at a
    throwaway home before running)."""
    ctx, *_ = ctx_and_captures
    saved: list[str] = []

    async def _on_seed_saved(seed_id: str, info: str | None = None) -> None:
        saved.append(seed_id)

    before = {s["seedId"] for s in await list_seeds(mongo_db, prefix="test")}
    task = create_kimicode_task(
        process_id="kimi-real-device-login",
        name="real-device-login",
        config=_cfg(
            mode="iframe",
            auto_start=False,  # let the operator log in interactively first
            consumer_instructions="Log in when prompted, then stop.",
            on_seed_saved=_on_seed_saved,
        ),
    )
    await task.execute(ctx)

    assert saved, "on_seed_saved did not fire — no seed captured from the login"
    after = {s["seedId"] for s in await list_seeds(mongo_db, prefix="test")}
    assert after - before, "the seed store did not grow after device-code login"


@pytest.mark.skipif(
    not os.environ.get("OPTIO_KIMICODE_REAL_SEED_ID"),
    reason=(
        "no captured seed: set OPTIO_KIMICODE_REAL_SEED_ID to a real seed id "
        "(capture one via the device-code login test first)"
    ),
)
@pytest.mark.asyncio
async def test_real_seed_replant_starts_authed(ctx_and_captures, task_root, tmp_path):
    """Checklist 5: a fresh task pinned to a captured ``seed_id`` starts ALREADY
    authenticated (the replanted creds let kimi run without any login) and drives
    itself to DONE."""
    ctx, cap, _ = ctx_and_captures
    seed_id = os.environ["OPTIO_KIMICODE_REAL_SEED_ID"]
    task = create_kimicode_task(
        process_id="kimi-real-seed-replant",
        name="real-seed-replant",
        config=_cfg(
            mode="iframe",
            seed_id=seed_id,
            consumer_instructions=_DONE_INSTRUCTION,
        ),
    )
    await task.execute(ctx)  # reaches DONE without an interactive login
    assert cap.widget_data, "seeded iframe task registered no widget"


@pytest.mark.asyncio
async def test_real_resume_picks_up_prior_session(ctx_and_captures, task_root, tmp_path):
    """Checklist 6: a relaunch restores the prior kimi session (snapshot) and
    pushes the resume notice. Two real cycles: fresh → DONE (captures a snapshot),
    then a resume that restores the session store and reaches DONE again.

    Uses the same ProcessContext object twice with ``ctx.resume`` toggled; the
    resume half exercises snapshot restore + the pushed RESUME_NOTICE against the
    real binary."""
    ctx, cap, _ = ctx_and_captures
    cfg = _cfg(
        mode="iframe", supports_resume=True, consumer_instructions=_DONE_INSTRUCTION,
    )

    task = create_kimicode_task(
        process_id="kimi-real-resume", name="real-resume", config=cfg,
    )
    await task.execute(ctx)          # cycle 1 — fresh, captures a snapshot

    ctx.resume = True                # cycle 2 — resume restores + re-runs
    task2 = create_kimicode_task(
        process_id="kimi-real-resume", name="real-resume", config=cfg,
    )
    await task2.execute(ctx)


def _ssh_from_env() -> "SSHConfig | None":
    host = os.environ.get("OPTIO_KIMICODE_REAL_SSH_HOST")
    if not host:
        return None
    return SSHConfig(
        host=host,
        user=os.environ.get("OPTIO_KIMICODE_REAL_SSH_USER") or os.environ.get("USER", ""),
        key_path=os.environ.get("OPTIO_KIMICODE_REAL_SSH_KEY_PATH")
        or str(Path.home() / ".ssh" / "id_ed25519"),
        port=int(os.environ.get("OPTIO_KIMICODE_REAL_SSH_PORT", "22")),
    )


@pytest.mark.skipif(
    not os.environ.get("OPTIO_KIMICODE_REAL_SSH_HOST"),
    reason=(
        "no remote host: set OPTIO_KIMICODE_REAL_SSH_HOST (+ optional _USER / "
        "_KEY_PATH / _PORT) to run the remote-SSH surface leg"
    ),
)
@pytest.mark.asyncio
async def test_real_remote_ssh_one_surface(ctx_and_captures, task_root, tmp_path):
    """Checklist 7: one real surface (iframe) driven over SSH via ``RemoteHost``
    — the generic remote path bootstraps kimi on the remote worker and reaches
    DONE, proving the wrapper runs identically remote."""
    ctx, cap, _ = ctx_and_captures
    ssh = _ssh_from_env()
    assert ssh is not None  # guaranteed by the skipif
    task = create_kimicode_task(
        process_id="kimi-real-remote",
        name="real-remote",
        config=_cfg(
            mode="iframe",
            ssh=ssh,
            # remote worker resolves its own kimi (login-shell PATH / vendor
            # install), so do not pin a local install dir.
            kimi_install_dir=None,
            consumer_instructions=_DONE_INSTRUCTION,
        ),
    )
    await task.execute(ctx)
    assert cap.widget_data, "remote iframe task registered no widget"
