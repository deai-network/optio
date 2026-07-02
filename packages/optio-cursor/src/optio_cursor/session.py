"""State machine for one optio-cursor session (Stage 0: iframe/ttyd, local).

Orchestrates a Host (local or remote) through resolve cursor-agent → install
ttyd → plant AGENTS.md (+ cli-config.json) → launch ttyd(cursor-agent) inside
tmux → optio.log protocol session → teardown.

Adapted from optio-grok's iframe path. Stage 0 drops the resume/snapshot,
seed, conversation, credential-watcher, and fs-isolation branches; those
arrive in later stages.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os

from optio_core.context import ProcessContext
from optio_core.models import TaskInstance

from optio_agents import HookContext, get_protocol
from optio_agents.protocol.session import _SessionFailed, run_log_protocol_session
from optio_host.host import Host, LocalHost, ProcessHandle
from optio_host.paths import task_dir

from optio_cursor import host_actions
from optio_cursor.prompt import compose_agents_md
from optio_cursor.types import CursorTaskConfig


_LOG = logging.getLogger(__name__)

READY_TIMEOUT_S = 30.0


def _build_host(config: CursorTaskConfig, process_id: str) -> Host:
    """Construct the appropriate Host for the given config.

    Extracted so tests can monkeypatch ``session._build_host`` to inject a
    fake host (mirrors the grok/claudecode pattern). Delegates to
    host_actions.build_host."""
    taskdir = task_dir(
        ssh=config.ssh, process_id=process_id, consumer_name="optio-cursor",
    )
    return host_actions.build_host(config.ssh, taskdir)


async def run_cursor_session(ctx: ProcessContext, config: CursorTaskConfig) -> None:
    """Execute function body for one optio-cursor task instance."""
    host: Host = _build_host(config, ctx.process_id)
    # Cursor's login flow prints its auth URL (NO_OPEN_BROWSER=1 is set in the
    # launch env); "redirect" surfaces it to the operator via BROWSER:.
    protocol = get_protocol(browser="redirect")
    launched_handle: ProcessHandle | None = None
    tmux_path: str | None = None
    tmux_socket: str | None = None
    tmux_session: str | None = None
    cursor_path: str | None = None
    ttyd_path: str | None = None
    cancelled = False

    await host.connect()

    async def _prepare(host: Host, hook_ctx: HookContext) -> None:
        """Resolve cursor-agent + ttyd, plant cli-config.json and AGENTS.md.

        Handed to run_log_protocol_session, which runs it AFTER
        host.setup_workdir() has wiped the workdir and BEFORE it subscribes
        the optio.log tail.
        """
        nonlocal cursor_path, ttyd_path
        cursor_path = await host_actions.ensure_cursor_installed(
            hook_ctx,
            install_if_missing=config.install_if_missing,
            install_dir=config.cursor_install_dir,
        )
        ttyd_path = await host_actions.ensure_ttyd_installed(
            hook_ctx,
            install_if_missing=config.install_ttyd_if_missing,
            install_dir=config.ttyd_install_dir,
        )

        # Permission rules are config-planted (cursor-agent has no
        # --allow/--deny argv): write cli-config.json under the per-task HOME
        # BEFORE launch so cursor reads it on startup.
        cli_config = host_actions.build_cli_config(
            allowed_tools=config.allowed_tools,
            disallowed_tools=config.disallowed_tools,
        )
        if cli_config is not None:
            await host.write_text(
                "home/.cursor/cli-config.json",
                json.dumps(cli_config, indent=2) + "\n",
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

    async def _cursor_body(host: Host, hook_ctx: HookContext) -> None:
        nonlocal launched_handle, tmux_path, tmux_socket, tmux_session

        # Network binding (same env handling as grok/claudecode for
        # multi-container deploys).
        bind_addr = os.environ.get("OPTIO_WIDGET_TUNNEL_BIND", "127.0.0.1")
        upstream_host = os.environ.get("OPTIO_WIDGET_TUNNEL_HOST", "127.0.0.1")
        ttyd_iface = bind_addr if isinstance(host, LocalHost) else "127.0.0.1"

        cursor_flags = host_actions.build_cursor_flags(
            force=config.force,
            auto_review=config.auto_review,
            sandbox=config.sandbox,
            model=config.model,
            resuming=False,  # Stage 0 tasks never resume.
        )
        cursor_flags = [
            *cursor_flags,
            *host_actions.build_auto_start_args(auto_start=config.auto_start),
        ]
        launch_env = {
            **(config.env or {}),
            **(hook_ctx.browser_launch_env or {}),
        }
        if config.api_key:
            # api_key rides the launch env, never argv (process listings).
            launch_env["CURSOR_API_KEY"] = config.api_key
        ctx.report_progress(None, "Launching Cursor…")
        handle, ttyd_port, tmux_socket, tmux_session = await host_actions.launch_ttyd_with_cursor(
            host,
            ttyd_path=ttyd_path,
            cursor_path=cursor_path,
            bind_iface=ttyd_iface,
            extra_env=launch_env,
            cursor_flags=cursor_flags,
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
        ctx.report_progress(None, "Cursor is live")

        # Await the cursor process inside tmux (NOT the ttyd connection). ttyd
        # stays up serving viewers; the task is alive while the tmux session
        # is. The protocol driver cancels this body when it sees DONE/ERROR in
        # optio.log; if cursor exits some other way, has-session goes false and
        # the body returns -> driver treats it as premature exit.
        while ctx.should_continue() and await host_actions.tmux_session_alive(
            host, tmux_path, tmux_socket, tmux_session,
        ):
            await asyncio.sleep(1.0)

    async def _agent_sender(message: str) -> None:
        await host_actions.send_text_to_cursor(
            host, tmux_path, tmux_socket, tmux_session, message,
        )

    try:
        await run_log_protocol_session(
            host, ctx,
            body=_cursor_body,
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
            and cursor_path
        ):
            try:
                await host_actions.teardown_session_tree(
                    host,
                    tmux_path=tmux_path,
                    tmux_socket=tmux_socket,
                    tmux_session=tmux_session,
                    cursor_path=cursor_path,
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


def create_cursor_task(
    process_id: str,
    name: str,
    config: CursorTaskConfig,
    description: str | None = None,
    metadata: dict | None = None,
) -> TaskInstance:
    """Return a TaskInstance that runs one optio-cursor session.

    ``metadata`` is the caller app's task-tagging payload; it is stamped onto
    the TaskInstance verbatim and never read by the task itself.
    """

    async def _execute(ctx: ProcessContext) -> None:
        await run_cursor_session(ctx, config)

    return TaskInstance(
        execute=_execute,
        process_id=process_id,
        name=name,
        description=description,
        ui_widget="iframe",
        supports_resume=config.supports_resume,
        metadata=metadata or {},
    )
