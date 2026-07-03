"""Stage-0 done-when proof against the REAL codex CLI.

Guide Part 3, Stage 0: "A demo task launches, does work, emits DONE, and
tears down cleanly locally." The default suite proves the pipeline with the
fake agent; this module proves it with the real binary — one real (billable)
model turn, so it never runs unless explicitly opted in:

    OPTIO_CODEX_REAL_SESSION_TEST=1 .venv/bin/python -m pytest \
        packages/optio-codex/tests/test_real_codex_session.py -q

Skip-chain (grok convention: opt-in env + capability probes, never default):
env flag set, real ``codex`` on PATH, real ``tmux`` present, and an authed
``~/.codex/auth.json``. The operator identity is planted into the task's
isolated CODEX_HOME via ``before_execute`` (auth.json + a pre-trust
config.toml entry for the workdir) — the same shape Stage 3 seeds automate.
"""

import json
import os
import shutil
from pathlib import Path

import pytest

from optio_codex import CodexTaskConfig, create_codex_task

REAL_HOME_CODEX = Path.home() / ".codex"


def _authed() -> bool:
    try:
        data = json.loads((REAL_HOME_CODEX / "auth.json").read_text())
    except (OSError, ValueError):
        return False
    return bool(data.get("tokens") or data.get("OPENAI_API_KEY"))


pytestmark = pytest.mark.skipif(
    os.environ.get("OPTIO_CODEX_REAL_SESSION_TEST") != "1"
    or shutil.which("codex") is None
    or shutil.which("tmux") is None
    or not _authed(),
    reason="opt-in real-codex test (OPTIO_CODEX_REAL_SESSION_TEST=1, "
    "codex+tmux on PATH, authed ~/.codex/auth.json)",
)


@pytest.mark.asyncio
async def test_real_codex_stage0_done_when(ctx_and_captures, task_root):
    import asyncio

    ctx, captures, _cancel = ctx_and_captures

    async def _plant_identity(hook_ctx):
        host = hook_ctx._host
        auth = (REAL_HOME_CODEX / "auth.json").read_text()
        await host.write_text("home/.codex/auth.json", auth)
        # Pre-trust the workdir so the TUI never blocks on the trust prompt.
        await host.write_text(
            "home/.codex/config.toml",
            f'[projects."{host.workdir}"]\ntrust_level = "trusted"\n',
        )

    task = create_codex_task(
        process_id="codex-real-stage0",
        name="real stage0 proof",
        config=CodexTaskConfig(
            consumer_instructions=(
                "Your entire task: append the exact line DONE to the file "
                "optio.log in the current directory (as the coordination "
                "protocol above describes), then stop. Do nothing else."
            ),
            before_execute=_plant_identity,
            # Task-execution done-when proof: the agent must auto-run its
            # AGENTS.md task and emit DONE. auto_start defaults to False
            # (Gap 2, chat-task parity), so this task opts in explicitly.
            auto_start=True,
        ),
    )

    # Completes cleanly on agent-emitted DONE; hard cap so an unattended
    # real-CLI hang cannot wedge the opt-in run.
    await asyncio.wait_for(task.execute(ctx), timeout=280)

    assert any("Codex is live" == m for _, m in captures.progress)
