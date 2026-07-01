"""State machine for one optio-grok session (Stage 0: iframe/ttyd, local).

Orchestrates a Host (local or remote) through resolve grok → install ttyd →
plant AGENTS.md → launch ttyd(grok) inside tmux → optio.log protocol session →
teardown.

Adapted from optio-claudecode's iframe path. Stage 0 drops the
resume/snapshot, seed, conversation, credential-planting, fs-isolation, and
in-session input-listener branches; those arrive in later stages.
"""

from __future__ import annotations

import asyncio
import logging
import os

from optio_core.context import ProcessContext
from optio_core.models import TaskInstance

from optio_agents import HookContext, get_protocol
from optio_agents.protocol.session import _SessionFailed, run_log_protocol_session
from optio_host.host import Host, LocalHost, ProcessHandle, RemoteHost
from optio_host.paths import task_dir

from optio_grok import host_actions
from optio_grok.prompt import compose_agents_md
from optio_grok.types import GrokTaskConfig


_LOG = logging.getLogger(__name__)

READY_TIMEOUT_S = 30.0


def _build_host(config: GrokTaskConfig, process_id: str) -> Host:
    """Construct the appropriate Host for the given config.

    Extracted so tests can monkeypatch ``session._build_host`` to inject a
    fake host (mirrors the claudecode/opencode pattern).
    """
    taskdir = task_dir(
        ssh=config.ssh, process_id=process_id, consumer_name="optio-grok",
    )
    if config.ssh is None:
        os.makedirs(taskdir, exist_ok=True)
        host: Host = LocalHost(taskdir=taskdir)
        os.makedirs(host.workdir, exist_ok=True)
        return host
    return RemoteHost(ssh_config=config.ssh, taskdir=taskdir)


async def run_grok_session(ctx: ProcessContext, config: GrokTaskConfig) -> None:
    """Execute function body for one optio-grok task instance."""
    host: Host = _build_host(config, ctx.process_id)
    protocol = get_protocol(browser="suppress")
    launched_handle: ProcessHandle | None = None
    tmux_path: str | None = None
    tmux_socket: str | None = None
    tmux_session: str | None = None
    grok_path: str | None = None
    ttyd_path: str | None = None
    cancelled = False

    await host.connect()

    async def _prepare(host: Host, hook_ctx: HookContext) -> None:
        """Resolve grok + ttyd and plant AGENTS.md.

        Handed to run_log_protocol_session, which runs it AFTER
        host.setup_workdir() has wiped the workdir and BEFORE it subscribes
        the optio.log tail.
        """
        nonlocal grok_path, ttyd_path
        grok_path = await host_actions.ensure_grok_installed(
            hook_ctx,
            install_if_missing=config.install_if_missing,
            install_dir=config.grok_install_dir,
        )
        ttyd_path = await host_actions.ensure_ttyd_installed(
            hook_ctx,
            install_if_missing=config.install_ttyd_if_missing,
            install_dir=config.ttyd_install_dir,
        )
        await host.write_text(
            "AGENTS.md",
            compose_agents_md(
                config.consumer_instructions,
                host_protocol=config.host_protocol,
            ),
        )
        if config.before_execute is not None:
            await config.before_execute(hook_ctx)

    async def _grok_body(host: Host, hook_ctx: HookContext) -> None:
        nonlocal launched_handle, tmux_path, tmux_socket, tmux_session

        # Network binding (same env handling as claudecode/opencode for
        # multi-container deploys).
        bind_addr = os.environ.get("OPTIO_WIDGET_TUNNEL_BIND", "127.0.0.1")
        upstream_host = os.environ.get("OPTIO_WIDGET_TUNNEL_HOST", "127.0.0.1")
        ttyd_iface = bind_addr if isinstance(host, LocalHost) else "127.0.0.1"

        grok_flags = host_actions.build_grok_flags(
            permission_mode=config.permission_mode,
            allowed_tools=config.allowed_tools,
            disallowed_tools=config.disallowed_tools,
            model=config.model,
            effort=config.effort,
            reasoning_effort=config.reasoning_effort,
            no_leader=config.no_leader,
            resuming=False,
        )
        grok_flags = [
            *grok_flags,
            *host_actions.build_auto_start_args(auto_start=config.auto_start),
        ]
        launch_env = {
            **(config.env or {}),
            **(hook_ctx.browser_launch_env or {}),
        }
        ctx.report_progress(None, "Launching Grok Build…")
        handle, ttyd_port, tmux_socket, tmux_session = await host_actions.launch_ttyd_with_grok(
            host,
            ttyd_path=ttyd_path,
            grok_path=grok_path,
            bind_iface=ttyd_iface,
            extra_env=launch_env,
            grok_flags=grok_flags,
            ready_timeout_s=READY_TIMEOUT_S,
            env_remove=config.scrub_env,
        )
        launched_handle = handle
        tmux_path = await host_actions._require_tmux(host)

        worker_port = await host.establish_tunnel(ttyd_port, bind_addr=bind_addr)
        await ctx.set_widget_upstream(f"http://{upstream_host}:{worker_port}")
        await ctx.set_widget_data({
            "iframeSrc": "{widgetProxyUrl}/",
        })
        ctx.report_progress(None, "Grok Build is live")

        # Await the grok process inside tmux (NOT the ttyd connection). ttyd
        # stays up serving viewers; the task is alive while the tmux session
        # is. The protocol driver cancels this body when it sees DONE/ERROR in
        # optio.log; if grok exits some other way, has-session goes false and
        # the body returns -> driver treats it as premature exit.
        while ctx.should_continue() and await host_actions.tmux_session_alive(
            host, tmux_path, tmux_socket, tmux_session,
        ):
            await asyncio.sleep(1.0)

    async def _agent_sender(message: str) -> None:
        await host_actions.send_text_to_grok(
            host, tmux_path, tmux_socket, tmux_session, message,
        )

    try:
        await run_log_protocol_session(
            host, ctx,
            body=_grok_body,
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
        if (
            launched_handle is not None
            and tmux_path is not None
            and tmux_socket is not None
            and tmux_session is not None
            and grok_path
        ):
            try:
                await host_actions.teardown_session_tree(
                    host,
                    tmux_path=tmux_path,
                    tmux_socket=tmux_socket,
                    tmux_session=tmux_session,
                    grok_path=grok_path,
                    ttyd_handle=launched_handle,
                    aggressive=cancelled,
                )
            except Exception:
                _LOG.exception("teardown_session_tree failed")

        try:
            await host.cleanup_taskdir(aggressive=cancelled)
        except Exception:
            _LOG.exception("cleanup_taskdir failed")
        try:
            await host.disconnect()
        except Exception:
            _LOG.exception("host.disconnect failed")


def create_grok_task(
    process_id: str,
    name: str,
    config: GrokTaskConfig,
    description: str | None = None,
    metadata: dict | None = None,
) -> TaskInstance:
    """Return a TaskInstance that runs one optio-grok session.

    ``metadata`` is the caller app's task-tagging payload; it is stamped onto
    the TaskInstance verbatim and never read by the task itself.
    """

    async def _execute(ctx: ProcessContext) -> None:
        await run_grok_session(ctx, config)

    return TaskInstance(
        execute=_execute,
        process_id=process_id,
        name=name,
        description=description,
        ui_widget="iframe",
        supports_resume=config.supports_resume,
        metadata=metadata or {},
    )
