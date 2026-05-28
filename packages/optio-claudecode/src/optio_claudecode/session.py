"""State machine for one optio-claudecode session.

Orchestrates a Host (local or remote) through:
  1. Ensure claude + ttyd binaries are installed on the host.
  2. Plant per-task HOME files (credentials.json, settings.json).
  3. Write AGENTS.md (consumer instructions + optio coordination prompt).
  4. Fire ``before_execute`` hook.
  5. Launch ttyd wrapping claude.
  6. Open the SSH tunnel and register the iframe widget.
  7. Hand off to ``run_log_protocol_session`` which tails ``optio.log``,
     dispatches DELIVERABLE / DONE / ERROR, and runs ``after_execute``.

Most of the per-session protocol plumbing lives in optio-host. This
module only does the claudecode-specific orchestration.
"""

from __future__ import annotations

import logging
import os

from optio_core.context import ProcessContext
from optio_core.models import TaskInstance

from optio_host.context import HookContext
from optio_host.host import Host, LocalHost, ProcessHandle, RemoteHost
from optio_host.paths import task_dir
from optio_host.protocol.session import _SessionFailed, run_log_protocol_session

from optio_claudecode import host_actions
from optio_claudecode.prompt import compose_agents_md
from optio_claudecode.types import ClaudeCodeTaskConfig


_LOG = logging.getLogger(__name__)

READY_TIMEOUT_S = 30.0


def _build_host(config: ClaudeCodeTaskConfig, process_id: str) -> Host:
    """Construct the appropriate Host for the given config.

    Extracted so tests monkeypatch ``session._build_host`` to inject a
    fake host (mirrors the opencode pattern).
    """
    taskdir = task_dir(
        ssh=config.ssh, process_id=process_id, consumer_name="optio-claudecode",
    )
    if config.ssh is None:
        os.makedirs(taskdir, exist_ok=True)
        host: Host = LocalHost(taskdir=taskdir)
        os.makedirs(host.workdir, exist_ok=True)
        return host
    return RemoteHost(ssh_config=config.ssh, taskdir=taskdir)


async def run_claudecode_session(
    ctx: ProcessContext, config: ClaudeCodeTaskConfig,
) -> None:
    """Execute function body for one optio-claudecode task instance."""
    host: Host = _build_host(config, ctx.process_id)
    launched_handle: ProcessHandle | None = None
    cancelled = False

    await host.connect()
    await host.setup_workdir()

    hook_ctx_outer = HookContext(ctx, host)
    claude_path = await host_actions.ensure_claude_installed(
        hook_ctx_outer,
        install_if_missing=config.install_if_missing,
        install_dir=config.claude_install_dir,
    )
    ttyd_path = await host_actions.ensure_ttyd_installed(
        hook_ctx_outer,
        install_if_missing=config.install_ttyd_if_missing,
        install_dir=config.ttyd_install_dir,
    )

    async def _claudecode_body(host: Host, hook_ctx: HookContext) -> None:
        nonlocal launched_handle

        # Fresh start: the protocol driver has created workdir,
        # deliverables/, and an empty optio.log already. Plant
        # per-task HOME files and AGENTS.md before launching ttyd.
        await host_actions.plant_home_files(
            host,
            credentials_json=config.credentials_json,
            claude_config=config.claude_config,
        )
        await host.write_text(
            "AGENTS.md",
            compose_agents_md(config.consumer_instructions),
        )

        if config.before_execute is not None:
            await config.before_execute(hook_ctx)

        # Network binding (same env handling as opencode for multi-container deploys)
        bind_addr = os.environ.get("OPTIO_WIDGET_TUNNEL_BIND", "127.0.0.1")
        upstream_host = os.environ.get("OPTIO_WIDGET_TUNNEL_HOST", "127.0.0.1")
        ttyd_iface = bind_addr if isinstance(host, LocalHost) else "127.0.0.1"

        claude_flags = host_actions.build_claude_flags(
            permission_mode=config.permission_mode,
            allowed_tools=config.allowed_tools,
            disallowed_tools=config.disallowed_tools,
        )
        ctx.report_progress(None, "Launching claude (ttyd)…")
        handle, ttyd_port = await host_actions.launch_ttyd_with_claude(
            host,
            ttyd_path=ttyd_path,
            claude_path=claude_path,
            bind_iface=ttyd_iface,
            extra_env=config.env,
            claude_flags=claude_flags,
            ready_timeout_s=READY_TIMEOUT_S,
        )
        launched_handle = handle

        worker_port = await host.establish_tunnel(ttyd_port, bind_addr=bind_addr)
        await ctx.set_widget_upstream(f"http://{upstream_host}:{worker_port}")
        await ctx.set_widget_data({
            "iframeSrc": "{widgetProxyUrl}/",
        })
        ctx.report_progress(None, "claude is live")

        # Await ttyd subprocess exit. Protocol driver cancels this body
        # when it sees DONE/ERROR; otherwise we get here only on a
        # premature exit, which the driver detects as failure.
        proc = launched_handle.pid_like
        await proc.wait()  # type: ignore[union-attr]

    try:
        await run_log_protocol_session(
            host, ctx,
            body=_claudecode_body,
            on_deliverable=config.on_deliverable,
            after_execute=config.after_execute,
        )
    except _SessionFailed as fail:
        raise RuntimeError(str(fail)) from None
    finally:
        if not ctx.should_continue():
            cancelled = True
        if launched_handle is not None:
            try:
                await host.terminate_subprocess(launched_handle, aggressive=cancelled)
            except Exception:
                _LOG.exception("terminate_subprocess failed")
        try:
            await host.cleanup_taskdir(aggressive=cancelled)
        except Exception:
            _LOG.exception("cleanup_taskdir failed")
        try:
            await host.disconnect()
        except Exception:
            _LOG.exception("host.disconnect failed")


def create_claudecode_task(
    process_id: str,
    name: str,
    config: ClaudeCodeTaskConfig,
    description: str | None = None,
) -> TaskInstance:
    """Return a TaskInstance that runs one optio-claudecode session."""

    async def _execute(ctx: ProcessContext) -> None:
        await run_claudecode_session(ctx, config)

    return TaskInstance(
        execute=_execute,
        process_id=process_id,
        name=name,
        description=description,
        ui_widget="iframe",
        supports_resume=False,
    )
