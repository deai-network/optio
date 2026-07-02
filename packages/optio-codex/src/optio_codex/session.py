"""State machine for one optio-codex session (Stage 0: iframe/ttyd, local)."""

from __future__ import annotations

import asyncio
import logging
import os

from optio_core.context import ProcessContext
from optio_core.models import TaskInstance

from optio_agents import HookContext, get_protocol
from optio_agents.protocol.session import _SessionFailed, run_log_protocol_session
from optio_host.host import Host, LocalHost, ProcessHandle
from optio_host.paths import task_dir

from optio_codex import host_actions
from optio_codex.prompt import compose_agents_md
from optio_codex.types import CodexTaskConfig


_LOG = logging.getLogger(__name__)
READY_TIMEOUT_S = 30.0


def _build_host(config: CodexTaskConfig, process_id: str) -> Host:
    taskdir = task_dir(
        ssh=config.ssh, process_id=process_id, consumer_name="optio-codex",
    )
    return host_actions.build_host(config.ssh, taskdir)


async def run_codex_session(ctx: ProcessContext, config: CodexTaskConfig) -> None:
    """Execute function body for one optio-codex task instance."""
    host: Host = _build_host(config, ctx.process_id)
    protocol = get_protocol(browser="suppress")
    launched_handle: ProcessHandle | None = None
    tmux_path: str | None = None
    tmux_socket: str | None = None
    tmux_session: str | None = None
    codex_path: str | None = None
    ttyd_path: str | None = None
    cancelled = False

    await host.connect()

    async def _prepare(host: Host, hook_ctx: HookContext) -> None:
        nonlocal codex_path, ttyd_path
        codex_path = await host_actions.ensure_codex_installed(
            hook_ctx,
            install_if_missing=config.install_if_missing,
            install_dir=config.codex_install_dir,
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
                documentation=protocol.documentation if config.host_protocol else None,
                host_protocol=config.host_protocol,
            ),
        )
        if config.before_execute is not None:
            # End-of-prepare placement matches claudecode (its
            # _plant_session_content ends with before_execute, inside its
            # _prepare); opencode fires it inside the body instead.
            await config.before_execute(hook_ctx)

    async def _codex_body(host: Host, hook_ctx: HookContext) -> None:
        nonlocal launched_handle, tmux_path, tmux_socket, tmux_session

        bind_addr = os.environ.get("OPTIO_WIDGET_TUNNEL_BIND", "127.0.0.1")
        upstream_host = os.environ.get("OPTIO_WIDGET_TUNNEL_HOST", "127.0.0.1")
        ttyd_iface = bind_addr if isinstance(host, LocalHost) else "127.0.0.1"

        codex_flags = host_actions.build_codex_flags(
            model=config.model,
            ask_for_approval=config.ask_for_approval,
            sandbox=config.sandbox,
        )
        codex_flags = [
            *codex_flags,
            *host_actions.build_auto_start_args(auto_start=config.auto_start),
        ]
        launch_env = {
            **(config.env or {}),
            **(hook_ctx.browser_launch_env or {}),
        }
        ctx.report_progress(None, "Launching Codex…")
        handle, tmux_path_local, ttyd_port, tmux_socket, tmux_session = await host_actions.launch_ttyd_with_codex(
            host,
            ttyd_path=ttyd_path,
            codex_path=codex_path,
            bind_iface=ttyd_iface,
            extra_env=launch_env,
            codex_flags=codex_flags,
            ready_timeout_s=READY_TIMEOUT_S,
            env_remove=config.scrub_env,
        )
        launched_handle = handle
        tmux_path = tmux_path_local

        worker_port = await host.establish_tunnel(ttyd_port, bind_addr=bind_addr)
        await ctx.set_widget_upstream(f"http://{upstream_host}:{worker_port}")
        await ctx.set_widget_data({
            "iframeSrc": "{widgetProxyUrl}/",
        })
        ctx.report_progress(None, "Codex is live")

        while ctx.should_continue() and await host_actions.tmux_session_alive(
            host, tmux_path, tmux_socket, tmux_session,
        ):
            await asyncio.sleep(1.0)

    async def _agent_sender(message: str) -> None:
        await host_actions.send_text_to_codex(
            host, tmux_path, tmux_socket, tmux_session, message,
        )

    try:
        await run_log_protocol_session(
            host, ctx,
            body=_codex_body,
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
            tmux_path is not None
            and tmux_socket is not None
            and tmux_session is not None
            and codex_path
        ):
            try:
                await host_actions.teardown_session_tree(
                    host,
                    tmux_path=tmux_path,
                    tmux_socket=tmux_socket,
                    tmux_session=tmux_session,
                    codex_path=codex_path,
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


def create_codex_task(
    process_id: str,
    name: str,
    config: CodexTaskConfig,
    description: str | None = None,
    metadata: dict | None = None,
) -> TaskInstance:
    """Return a TaskInstance that runs one optio-codex session."""

    async def _execute(ctx: ProcessContext) -> None:
        await run_codex_session(ctx, config)

    return TaskInstance(
        execute=_execute,
        process_id=process_id,
        name=name,
        description=description,
        ui_widget="iframe",
        supports_resume=config.supports_resume,
        metadata=metadata or {},
    )