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


pytestmark = pytest.mark.skipif(
    os.environ.get("OPTIO_ANTIGRAVITY_REAL_SESSION_TEST") != "1"
    or shutil.which("agy") is None
    or shutil.which("tmux") is None
    or not _looks_logged_in(),
    reason="opt-in real-agy test (OPTIO_ANTIGRAVITY_REAL_SESSION_TEST=1, "
    "agy+tmux on PATH, a logged-in ~/.gemini). Runs in Task S1 / Stage 10.",
)


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
