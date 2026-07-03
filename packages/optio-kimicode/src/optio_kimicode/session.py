"""State machine for one optio-kimicode session (Stage 0: iframe/kimi web, local).

Orchestrates a Host (local or remote) through resolve kimi → plant AGENTS.md →
launch ``kimi server run --foreground`` (the ``kimi web`` surface) → establish a
tunnel → inject the bearer token via the ``#token=`` URL fragment → optio.log
protocol session → teardown.

Adapted from optio-grok's iframe path. The defining delta: kimi serves its OWN
web SPA, so there is no ttyd/tmux tree — the launch is a single long-lived
server subprocess and teardown is a direct terminate. Stage 0 drops the
resume/snapshot, seed, conversation, credential-planting, and fs-isolation
branches; those arrive in later stages (plan groups 2-5).
"""

from __future__ import annotations

import asyncio
import logging
import os

from optio_core.context import ProcessContext
from optio_core.models import TaskInstance

from optio_agents import HookContext, get_protocol
from optio_agents.protocol.session import _SessionFailed, run_log_protocol_session
from optio_host.host import Host, LocalHost, ProcessHandle, proc_wait
from optio_host.paths import task_dir

from optio_kimicode import host_actions
from optio_kimicode.prompt import compose_agents_md
from optio_kimicode.types import KimiCodeTaskConfig


_LOG = logging.getLogger(__name__)

READY_TIMEOUT_S = 30.0


def _build_host(config: KimiCodeTaskConfig, process_id: str) -> Host:
    """Construct the appropriate Host for the given config.

    Extracted so tests can monkeypatch ``session._build_host`` to inject a
    fake host (mirrors the grok/claudecode pattern). Delegates to
    ``host_actions.build_host`` (shared with verify)."""
    taskdir = task_dir(
        ssh=config.ssh, process_id=process_id, consumer_name="optio-kimicode",
    )
    return host_actions.build_host(config.ssh, taskdir)


async def run_kimicode_session(ctx: ProcessContext, config: KimiCodeTaskConfig) -> None:
    """Execute function body for one optio-kimicode task instance."""
    host: Host = _build_host(config, ctx.process_id)
    # ``redirect``: kimi's first-login device-code URL is surfaced to the
    # operator (no browser interception needed — kimi prints the URL). Parity
    # with grok's protocol wiring; Stage 0 has no auth yet, so this is inert.
    protocol = get_protocol(browser="redirect")
    launched_handle: ProcessHandle | None = None
    cancelled = False
    kimi_path: str | None = None

    await host.connect()

    async def _prepare(host: Host, hook_ctx: HookContext) -> None:
        """Resolve kimi and plant AGENTS.md.

        Handed to run_log_protocol_session, which runs it AFTER
        host.setup_workdir() has wiped the workdir and BEFORE it subscribes the
        optio.log tail.
        """
        nonlocal kimi_path
        # Stage-0 resolution (host-based). The two-tier install cache
        # (host_actions.ensure_kimicode_installed) supersedes this in group 4.
        kimi_path = await host_actions.resolve_kimi(
            host,
            install_dir=config.kimi_install_dir,
            install_if_missing=config.install_if_missing,
        )
        await host.write_text(
            "AGENTS.md",
            compose_agents_md(
                config.consumer_instructions,
                host_protocol=config.host_protocol,
                workdir_exclude=config.workdir_exclude,
                supports_resume=config.supports_resume,
                file_download=config.file_download,
            ),
        )
        if config.before_execute is not None:
            await config.before_execute(hook_ctx)

    async def _iframe_body(host: Host, hook_ctx: HookContext) -> None:
        nonlocal launched_handle

        # Network binding (same env handling as grok/claudecode for
        # multi-container deploys).
        bind_addr = os.environ.get("OPTIO_WIDGET_TUNNEL_BIND", "127.0.0.1")
        upstream_host = os.environ.get("OPTIO_WIDGET_TUNNEL_HOST", "127.0.0.1")
        server_iface = bind_addr if isinstance(host, LocalHost) else "127.0.0.1"

        launch_env = {
            **(config.env or {}),
            **(hook_ctx.browser_launch_env or {}),
        }
        ctx.report_progress(None, "Launching Kimi Code…")
        handle, server_port, token = await host_actions.launch_kimi_web(
            host,
            kimi_path=kimi_path,
            bind_iface=server_iface,
            extra_env=launch_env,
            env_remove=config.scrub_env,
            ready_timeout_s=READY_TIMEOUT_S,
        )
        launched_handle = handle

        worker_port = await host.establish_tunnel(server_port, bind_addr=bind_addr)
        await ctx.set_widget_upstream(f"http://{upstream_host}:{worker_port}")
        # The bearer token rides in the iframe URL's `#token=` fragment — a
        # client-side fragment the SPA reads from location.hash and never sends
        # to the server (so it never appears in access logs / the proxy).
        fragment = f"#token={token}" if token else ""
        await ctx.set_widget_data({
            "iframeSrc": "{widgetProxyUrl}/" + fragment,
        })
        ctx.report_progress(None, "Kimi Code is live")

        # Await the kimi server. The protocol driver cancels this body when it
        # sees DONE/ERROR in optio.log; if the server exits on its own,
        # proc_wait returns and the body returns → driver treats it as a
        # premature exit. (kimi's server is long-lived — it does not exit on
        # task completion, unlike grok's TUI.)
        wait_task = asyncio.create_task(proc_wait(handle))
        try:
            while ctx.should_continue():
                if wait_task.done():
                    break
                await asyncio.sleep(1.0)
        finally:
            if not wait_task.done():
                wait_task.cancel()
                try:
                    await wait_task
                except asyncio.CancelledError:
                    pass

    async def _agent_sender(message: str) -> None:
        # kimi's web SPA has no programmatic keystroke channel (unlike grok's
        # tmux TUI): operator feedback in iframe mode is typed into the SPA
        # directly. Conversation-mode sending arrives with that branch (group 5).
        raise NotImplementedError(
            "iframe mode has no agent_sender: kimi web accepts operator input "
            "through the SPA, not a host-side injection channel."
        )

    try:
        await run_log_protocol_session(
            host, ctx,
            body=_iframe_body,
            prepare=_prepare,
            on_deliverable=config.on_deliverable,
            after_execute=config.after_execute,
            protocol=protocol,
            agent_sender=_agent_sender,
            keywords=config.host_protocol,
        )
    except _SessionFailed as fail:
        raise RuntimeError(str(fail)) from None
    finally:
        if not ctx.should_continue():
            cancelled = True
        # kimi serves its own SPA — there is no tmux/ttyd tree. Terminate the
        # server subprocess directly. A cancelled (non-seeded) session is torn
        # down aggressively; a clean DONE completion uses SIGTERM so the server
        # shuts its socket down gracefully. (Seed-gated graceful teardown is a
        # Stage-4 concern.)
        if launched_handle is not None:
            try:
                await host.terminate_subprocess(
                    launched_handle, aggressive=cancelled,
                )
            except Exception:
                _LOG.exception("terminate kimi server subprocess failed")

        try:
            await host.cleanup_taskdir(aggressive=cancelled)
        except Exception:
            _LOG.exception("cleanup_taskdir failed")
        try:
            await host.disconnect()
        except Exception:
            _LOG.exception("host.disconnect failed")


def create_kimicode_task(
    process_id: str,
    name: str,
    config: KimiCodeTaskConfig,
    description: str | None = None,
    metadata: dict | None = None,
) -> TaskInstance:
    """Return a TaskInstance that runs one optio-kimicode session.

    ``metadata`` is the caller app's task-tagging payload; it is stamped onto
    the TaskInstance verbatim and never read by the task itself.
    """

    async def _execute(ctx: ProcessContext) -> None:
        await run_kimicode_session(ctx, config)

    # iframe → the kimi web SPA widget. Conversation mode (Stage 6) carries the
    # live chat widget only when conversation_ui is on; otherwise no widget.
    if config.conversation_ui:
        ui_widget: str | None = "conversation"
    elif config.mode == "conversation":
        ui_widget = None
    else:
        ui_widget = "iframe"

    return TaskInstance(
        execute=_execute,
        process_id=process_id,
        name=name,
        description=description,
        ui_widget=ui_widget,
        supports_resume=config.supports_resume,
        metadata=metadata or {},
    )
