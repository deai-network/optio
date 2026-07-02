"""Reference demo task for optio-codex — Stage 0 (iframe/ttyd, local).

One static task that embeds the Codex TUI in the dashboard via ttyd and
exercises the full hook walkthrough (before_execute file ship,
deliverable callback, after_execute log readback).

Authentication (Stage 0 — no seeds yet): codex runs under HOME-isolation
(<workdir>/home), so the host user's ~/.codex is NOT inherited. Either
export ``OPTIO_CODEX_DEMO_OPENAI_API_KEY`` before starting the demo (it
is passed into the session env as ``OPENAI_API_KEY``), or run
``codex login`` interactively inside the embedded terminal after launch.
Seed-based provisioning (log in once, reuse everywhere) arrives with the
optio-codex seeds stage, which also completes the demo trio
(seed-setup + seed-pinned iframe + seed-pinned conversation).
"""

from __future__ import annotations

import os

from optio_codex import CodexTaskConfig, HookContext, create_codex_task
from optio_core.models import TaskInstance

from optio_demo.tasks._feedback import make_feedback_on_deliverable


CONTEXT_TXT = b"""\
Mission code-name: Project Petunia
Authorized color: turquoise
"""

CONSUMER_PROMPT = (
    "First, read the file `./context.txt` in your working directory. It "
    "contains a mission code-name and an authorized color. Ship a "
    "deliverable file at `./deliverables/mission-report.txt` containing "
    "the mission code-name, the authorized color, and the number 42. "
    "Then signal completion by appending a `DONE` line to the "
    "`./optio.log` file (writing `DONE` in the chat has no effect — it "
    "must go into that file)."
)


async def _before_execute(hook_ctx: HookContext) -> None:
    await hook_ctx.copy_file(CONTEXT_TXT, "context.txt")


async def _after_execute(hook_ctx: HookContext) -> None:
    log = await hook_ctx.read_text_from_host("optio.log")
    lines = [ln for ln in log.splitlines() if ln.strip()]
    hook_ctx.report_progress(None, f"optio.log carried {len(lines)} line(s)")


def _demo_env() -> dict[str, str] | None:
    api_key = os.environ.get("OPTIO_CODEX_DEMO_OPENAI_API_KEY")
    return {"OPENAI_API_KEY": api_key} if api_key else None


async def get_tasks(services) -> list[TaskInstance]:
    return [
        create_codex_task(
            process_id="codex-demo-iframe",
            name="Codex demo — iframe",
            description=(
                "OpenAI Codex TUI embedded via ttyd (Stage 0, local). "
                "Auth: set OPTIO_CODEX_DEMO_OPENAI_API_KEY, or run "
                "`codex login` in the terminal after launch."
            ),
            config=CodexTaskConfig(
                consumer_instructions=CONSUMER_PROMPT,
                env=_demo_env(),
                before_execute=_before_execute,
                after_execute=_after_execute,
                on_deliverable=make_feedback_on_deliverable("codex"),
            ),
        ),
    ]
