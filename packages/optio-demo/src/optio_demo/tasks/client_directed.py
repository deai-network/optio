"""Client-directed events demo tasks (phase 2).

Four tasks exercising the three new capabilities end-to-end:

  - ``open-optio-repo``: pure-Python ``ctx.request_browser_open`` (view-scoped).
  - ``open-browser-via-tool``: a host task that runs a tiny Python script
    (``import webbrowser; webbrowser.open(URL)`` then ``DONE``) through the
    optio-agents session driver with ``get_protocol(browser="redirect")`` —
    shim → ``BROWSER:`` marker → parser → ``ctx.request_browser_open`` with no
    claude/opencode involved.
  - ``need-attention-demo``: ``ctx.need_attention`` (session-scoped).
  - ``domain-message-demo``: ``ctx.domain_message`` (session-scoped).
"""

from __future__ import annotations

import asyncio
import os

from optio_core.models import TaskInstance
from optio_host.host import LocalHost
from optio_agents import run_log_protocol_session, get_protocol


OPTIO_REPO_URL = "https://github.com/deai-network/optio"


async def _open_optio_repo(ctx) -> None:
    ctx.report_progress(0, "Opening the optio repo in your browser")
    rid = await ctx.request_browser_open(OPTIO_REPO_URL)
    ctx.report_progress(100, f"Requested browser open (requestId={rid})")


async def _open_browser_via_tool(ctx) -> None:
    """Host-bridge capture test: run a Python opener under capture shims."""
    taskdir = f"/tmp/optio-demo-browser-{os.getpid()}-{ctx.process_id}"
    os.makedirs(taskdir, exist_ok=True)
    host = LocalHost(taskdir=taskdir)
    os.makedirs(host.workdir, exist_ok=True)

    async def body(host, hook_ctx) -> None:
        # The driver installed the redirect (capture) shims before the body
        # ran and exposed their env on hook_ctx.browser_launch_env. A trivial
        # opener: webbrowser.open routes through xdg-open (the shim), which
        # appends the BROWSER: marker to optio.log. Then signal DONE.
        script = (
            "import webbrowser; "
            f"webbrowser.open({OPTIO_REPO_URL!r}); "
        )
        await host.run_command(
            f"python3 -c {script!r}",
            env=hook_ctx.browser_launch_env,
            cwd=host.workdir,
        )
        # The shim has appended BROWSER: by now; close out the session.
        await host.run_command(f"echo DONE >> {host.workdir}/optio.log")

    await run_log_protocol_session(
        host, ctx, body=body, protocol=get_protocol(browser="redirect"),
    )


async def _need_attention_demo(ctx) -> None:
    # Delay before asking, so the operator can navigate away to another task
    # and observe the session-scoped attention request pull them back here.
    DELAY = 10
    for elapsed in range(DELAY):
        ctx.report_progress(
            int(100 * elapsed / DELAY),
            f"Working… will request attention in {DELAY - elapsed}s "
            f"(navigate away to test it)",
        )
        await asyncio.sleep(1)
    rid = await ctx.need_attention("The demo task would like you to look at it.")
    ctx.report_progress(100, f"Attention requested (requestId={rid})")


async def _domain_message_demo(ctx) -> None:
    ctx.report_progress(0, "Sending a domain message")
    rid = await ctx.domain_message(
        "demo-event",
        {"severity": "info", "detail": "hello from the demo task"},
    )
    ctx.report_progress(100, f"Domain message sent (requestId={rid})")


def get_tasks() -> list[TaskInstance]:
    return [
        TaskInstance(
            execute=_open_optio_repo,
            process_id="open-optio-repo",
            name="Open the optio repo",
            description=(
                "Pure-Python browser_open: asks your browser to open the optio "
                "GitHub repo. View-scoped — delivered to whoever is watching."
            ),
        ),
        TaskInstance(
            execute=_open_browser_via_tool,
            process_id="open-browser-via-tool",
            name="Open browser via tool (capture bridge)",
            description=(
                "Host task running a Python webbrowser.open under "
                "get_protocol(redirect) capture shims; exercises shim → "
                "BROWSER: marker → parser → ctx.request_browser_open "
                "end-to-end (no agent)."
            ),
        ),
        TaskInstance(
            execute=_need_attention_demo,
            process_id="need-attention-demo",
            name="Request attention",
            description=(
                "Calls ctx.need_attention(...). Session-scoped — reaches the "
                "browser session that launched it. The dashboard navigates to "
                "this process via onAttention."
            ),
        ),
        TaskInstance(
            execute=_domain_message_demo,
            process_id="domain-message-demo",
            name="Send a domain message",
            description=(
                "Calls ctx.domain_message(keyword, data). Session-scoped; the "
                "dashboard surfaces it via onDomainMessage (console/toast)."
            ),
        ),
    ]
