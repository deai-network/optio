"""State machine for one optio-antigravity session (Stage 0: iframe/ttyd, local).

Orchestrates a Host (local or remote) through resolve agy → install ttyd →
plant AGENTS.md → launch ttyd(agy) inside tmux → optio.log protocol session →
teardown.

Adapted from optio-grok's iframe path. Stage 0 drops the resume/snapshot, seed,
conversation, credential, and fs-isolation branches; those arrive in later
stages (which re-open this module).
"""

from __future__ import annotations

import asyncio
import logging
import mimetypes
import os
import re
import secrets
import shlex
from typing import AsyncIterator

from optio_core.context import ProcessContext
from optio_core.models import BasicAuth, TaskInstance

from optio_agents import HookContext, RESUME_NOTICE, SYSTEM_MESSAGE_PREFIX, get_protocol
from optio_agents.protocol.session import _SessionFailed, run_log_protocol_session
from optio_agents.session_controls import model_control
from optio_host.host import Host, LocalHost, ProcessHandle
from optio_host.paths import task_dir

from optio_antigravity import host_actions
from optio_antigravity import models as antigravity_models
from optio_antigravity.conversation import AntigravityConversation
from optio_antigravity.conversation_listener import ConversationListener
from optio_antigravity.prompt import compose_agents_md
from optio_antigravity.snapshots import (
    insert_snapshot,
    load_latest_snapshot,
    prune_snapshots,
)
from optio_antigravity.types import AntigravityTaskConfig


_LOG = logging.getLogger(__name__)

READY_TIMEOUT_S = 30.0


def _build_host(config: AntigravityTaskConfig, process_id: str) -> Host:
    """Construct the appropriate Host for the given config.

    Extracted so tests can monkeypatch ``session._build_host`` to inject a fake
    host (mirrors the grok/opencode pattern). Delegates to
    host_actions.build_host (shared with verify)."""
    taskdir = task_dir(
        ssh=config.ssh, process_id=process_id, consumer_name="optio-antigravity",
    )
    return host_actions.build_host(config.ssh, taskdir)


async def run_antigravity_session(
    ctx: ProcessContext, config: AntigravityTaskConfig,
) -> None:
    """Execute function body for one optio-antigravity task instance."""
    host: Host = _build_host(config, ctx.process_id)
    # ``redirect``: plant capture-variant browser shims so agy's first-launch
    # Google OAuth login — which may shell out to open a URL — is intercepted
    # and surfaced to the operator via a BROWSER: log line (the operator
    # completes the flow in their own browser; remote uses seeds/device-auth
    # per the design).
    protocol = get_protocol(browser="redirect")
    launched_handle: ProcessHandle | None = None
    tmux_path: str | None = None
    tmux_socket: str | None = None
    tmux_session: str | None = None
    agy_path: str | None = None
    ttyd_path: str | None = None
    # Stage 8: the on-host claustrum binary for the fs-isolation wrap. Resolved
    # in _prepare when config.fs_isolation (fail-closed: provisioning raises
    # rather than launching unconfined); stays None when isolation is off.
    claustrum_path: str | None = None
    cancelled = False
    # Whether a snapshot was restored this run (drives --continue + the resume
    # notice + auto-start suppression). Set by _prepare, read by the body + the
    # snapshot-capture skip in teardown.
    resuming = False
    pass_continue = False

    await host.connect()

    async def _prepare(host: Host, hook_ctx: HookContext) -> None:
        """Resolve agy + ttyd, restore a resume snapshot, and plant AGENTS.md.

        Handed to run_log_protocol_session, which runs it AFTER
        host.setup_workdir() has wiped the workdir and BEFORE it subscribes the
        optio.log tail. That ordering is why restore belongs here: the restored
        optio.log is rotated away below before the tail can replay its stale
        DONE/ERROR, and AGENTS.md is (re)planted AFTER the restore so it is not
        wiped by it.
        """
        nonlocal agy_path, ttyd_path, resuming, pass_continue, claustrum_path
        agy_path = await host_actions.ensure_antigravity_installed(
            hook_ctx,
            install_if_missing=config.install_if_missing,
            install_dir=config.agy_install_dir,
        )
        # Conversation mode is headless (each turn is a one-shot `agy -p` under a
        # PTY) — no ttyd is launched, so skip its install.
        if config.mode != "conversation":
            ttyd_path = await host_actions.ensure_ttyd_installed(
                hook_ctx,
                install_if_missing=config.install_ttyd_if_missing,
                install_dir=config.ttyd_install_dir,
            )

        resume_requested = bool(getattr(ctx, "resume", False))
        snapshot = None
        if resume_requested:
            snapshot = await load_latest_snapshot(
                ctx._db, ctx._prefix, ctx.process_id,
            )
        resuming = snapshot is not None
        if resuming:
            # Restore the workdir tar (carries home/.gemini/antigravity, i.e.
            # agy's conversation/transcript/settings store). A present snapshot
            # that fails to restore is fatal — the restore call is intentionally
            # outside any except so it surfaces to the caller (no silent
            # fresh-start).
            await host.restore_workdir(_stream_blob(ctx, snapshot["workdirBlobId"]))
            # The agy launch symlink (home/.local/bin/agy) lives INSIDE the
            # workdir and was wiped by the restore; re-establish it against the
            # cache (which lives OUTSIDE the workdir and survives). Idempotent:
            # cache hit → just relinks, no reinstall/redownload. ttyd survives
            # untouched (real host home, outside the workdir).
            agy_path = await host_actions.ensure_antigravity_installed(
                hook_ctx,
                install_if_missing=config.install_if_missing,
                install_dir=config.agy_install_dir,
                progress_label="Restoring Antigravity runtime…",
            )
            await host_actions._rotate_optio_log(host)
            # A restored snapshot means agy persisted a conversation for this
            # workspace; --continue resumes the most recent one. The per-task
            # HOME lives inside the restored workdir at the same absolute path
            # (deterministic taskdir), so the workspace-keyed lookup matches.
            pass_continue = True

        # Stage 8 filesystem isolation: provision claustrum (cross-compiled on
        # the engine, cached by (tag, arch), placed on the target host, and
        # FUNCTIONALLY validated). Fail-closed — any provisioning failure raises,
        # so an fs-isolated session never launches unconfined. Resolved once (the
        # binary lives in the cache OUTSIDE the workdir, so it survives a resume
        # restore and is shared across tasks). Shared by the iframe + conversation
        # launch paths via host_actions._build_claustrum_wrap.
        if config.fs_isolation:
            claustrum_path = await host_actions.ensure_claustrum_installed(
                hook_ctx, install_dir=config.agy_install_dir,
            )

        # Disable agy's background self-update for this task (best-effort settings
        # key) on every launch path — fresh and resume alike — so the pinned
        # cached binary is never fought by a background update or stalled on the
        # updater probe. Runs after any resume restore so it re-asserts on the
        # restored settings.json. TODO(S2): reconcile with the self-update spike.
        await host_actions.disable_agy_self_update(host, host.workdir)

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
        if config.supports_resume:
            await host_actions._append_resume_log_entry(host)
        if config.before_execute is not None:
            await config.before_execute(hook_ctx)

    async def _agy_body(host: Host, hook_ctx: HookContext) -> None:
        nonlocal launched_handle, tmux_path, tmux_socket, tmux_session

        # Network binding (same env handling as grok/claudecode for
        # multi-container deploys).
        bind_addr = os.environ.get("OPTIO_WIDGET_TUNNEL_BIND", "127.0.0.1")
        upstream_host = os.environ.get("OPTIO_WIDGET_TUNNEL_HOST", "127.0.0.1")
        ttyd_iface = bind_addr if isinstance(host, LocalHost) else "127.0.0.1"

        agy_flags = host_actions.build_agy_flags(
            permission_mode=config.permission_mode,
            model=config.model,
            resuming=pass_continue,
        )
        agy_flags = [
            *agy_flags,
            *host_actions.build_auto_start_args(
                auto_start=config.auto_start, resuming=resuming,
            ),
            # PUSH resume awareness: a System: notice appended after --continue so
            # the resumed TUI session gets a "you have been resumed" turn (mutually
            # exclusive with the fresh-launch kickoff above). resume.log stays the
            # pull-based backstop.
            *host_actions.build_resume_notice_args(resuming=resuming),
        ]
        launch_env = {
            **(config.env or {}),
            **(hook_ctx.browser_launch_env or {}),
        }
        # Stage 8: confine the agy invocation inside the tmux pane with the
        # claustrum wrap (None when fs_isolation is off → agy runs unconfined).
        # tmux + ttyd themselves stay unconfined (infrastructure).
        claustrum_wrap = await host_actions._build_claustrum_wrap(
            host, config, claustrum_path,
        )
        ctx.report_progress(None, "Launching Antigravity…")
        handle, ttyd_port, tmux_socket, tmux_session = await host_actions.launch_ttyd_with_agy(
            host,
            ttyd_path=ttyd_path,
            agy_path=agy_path,
            bind_iface=ttyd_iface,
            extra_env=launch_env,
            agy_flags=agy_flags,
            ready_timeout_s=READY_TIMEOUT_S,
            env_remove=config.scrub_env,
            claustrum_wrap=claustrum_wrap,
        )
        launched_handle = handle
        tmux_path = await host_actions._require_tmux(host)

        worker_port = await host.establish_tunnel(ttyd_port, bind_addr=bind_addr)
        await ctx.set_widget_upstream(f"http://{upstream_host}:{worker_port}")
        await ctx.set_widget_data({
            "iframeSrc": "{widgetProxyUrl}/",
        })
        ctx.report_progress(None, "Antigravity is live")

        # Await the agy process inside tmux (NOT the ttyd connection). ttyd stays
        # up serving viewers; the task is alive while the tmux session is. The
        # protocol driver cancels this body when it sees DONE/ERROR in optio.log;
        # if agy exits some other way, has-session goes false and the body
        # returns -> driver treats it as premature exit.
        while ctx.should_continue() and await host_actions.tmux_session_alive(
            host, tmux_path, tmux_socket, tmux_session,
        ):
            await asyncio.sleep(1.0)

    # Conversation-mode driver + its opt-in dashboard listener. Both are
    # constructed inside _conversation_body (agy_path is only resolved in
    # _prepare); the nonlocals let _agent_sender + teardown reach them.
    conversation: AntigravityConversation | None = None
    conv_listener: ConversationListener | None = None

    async def _conversation_body(host: Host, hook_ctx: HookContext) -> None:
        nonlocal conversation, conv_listener

        # Antigravity has NO live transport: a conversation is synthetic, each
        # turn driven by a fresh `agy -p` under a PTY with events read from the
        # transcript file (design §1/§5). So — unlike grok's persistent `agent
        # stdio` process — there is no long-lived subprocess to launch/await
        # here; AntigravityConversation.send spawns and reaps one process per
        # turn. The task stays live between turns, ended by the caller's close.
        launch_env = {
            **host_actions._isolation_env(host.workdir),
            **(config.env or {}),
            **(hook_ctx.browser_launch_env or {}),
        }
        # The per-task HOME (from _isolation_env) is <workdir>/home, so agy
        # writes its transcript to <workdir>/home/.gemini/antigravity/....
        transcript_path = (
            f"{launch_env['HOME']}/.gemini/antigravity/transcript.jsonl"
        )
        # Stage 8: each ``agy -p`` turn runs Landlock-confined via the claustrum
        # wrap prepended to its argv (None when fs_isolation is off).
        claustrum_wrap = await host_actions._build_claustrum_wrap(
            host, config, claustrum_path,
        )
        conversation = AntigravityConversation(
            host=host,
            agy_path=agy_path,
            cwd=host.workdir,
            transcript_path=transcript_path,
            env=launch_env,
            model=config.model,
            claustrum_wrap=claustrum_wrap,
        )
        ctx.publish_result(conversation)
        ctx.report_progress(None, "Antigravity conversation is live")

        # Opt-in dashboard chat widget: start a per-task SSE listener over the
        # published conversation and publish it as the "conversation" widget via
        # the widget proxy (which injects the basic-auth credential).
        if config.conversation_ui:
            listener_password = secrets.token_urlsafe(32)
            bind_addr = os.environ.get("OPTIO_WIDGET_TUNNEL_BIND", "127.0.0.1")
            upstream_host = os.environ.get("OPTIO_WIDGET_TUNNEL_HOST", "127.0.0.1")
            # File upload: bytes land under <workdir>/uploads with a sanitized
            # name; the view injects a System: path reference so agy reads them
            # with its own tools (a synthetic -p turn has no inline ingest).
            uploads_dir = f"{host.workdir}/uploads"

            async def _write_upload(name: str, data: bytes) -> str:
                safe = re.sub(r"[^A-Za-z0-9._-]", "_", (name.split("/")[-1] or "file"))[:200] or "file"
                await host.put_file_to_host(data, f"{uploads_dir}/{safe}")
                return f"uploads/{safe}"

            # File download: serve workdir-confined bytes for the optio-file:
            # sentinel links agy emits. agy's deliverables/artifacts land under
            # <workdir>/home/.gemini/antigravity/artifacts/ (inside the per-task
            # HOME, so already under the workdir); realpath guards against ../.
            async def _read_download(relpath: str) -> tuple[bytes, str]:
                workdir = host.workdir.rstrip("/")
                real = os.path.realpath(os.path.join(workdir, relpath))
                if real != workdir and not real.startswith(workdir + os.sep):
                    raise ValueError("forbidden")           # outside the workdir
                data = await host.fetch_bytes_from_host(real)
                if len(data) > config.max_download_bytes:
                    raise ValueError("too-large")
                mime = mimetypes.guess_type(real)[0] or "application/octet-stream"
                return data, mime

            conv_listener = ConversationListener(
                conversation, password=listener_password,
                upload_writer=_write_upload,
                max_upload_bytes=config.max_upload_bytes,
                download_reader=_read_download,
                max_download_bytes=config.max_download_bytes,
            )
            # The listener is an in-process aiohttp app (not a remote-host
            # process like ttyd), so it binds directly to the widget-tunnel
            # interface and its port is reachable without a host tunnel.
            listener_port = await conv_listener.start(bind_addr)
            await ctx.set_widget_upstream(
                f"http://{upstream_host}:{listener_port}",
                inner_auth=BasicAuth(username="optio", password=listener_password),
            )
            # Model picker options: `agy models` on the host (Gemini + BYO
            # Claude/GPT ids), else a static list. Antigravity has no ACP
            # session block to prefer (design §1), so the CLI is the only live
            # source. default_model overrides the picker's initial value.
            model_list = await antigravity_models.fetch_available_models(
                host=host, agy_path=agy_path,
            )
            current_model = (
                config.default_model or config.model or model_list.get("default")
            )
            # The model is the id="model" entry of the engine-neutral
            # session-controls list; antigravity exposes only this one control
            # (switched restart-based via set_control — the next turn's --model).
            control = model_control(
                models=model_list["models"], current=current_model,
            )
            await ctx.set_widget_data({
                "protocol": "antigravity",
                "toolVerbosity": config.tool_verbosity,
                "thinkingVerbosity": config.thinking_verbosity,
                "showSessionControls": config.show_session_controls,
                "controls": [control.to_dict()],
                "showFileUpload": config.show_file_upload,
                "maxUploadBytes": config.max_upload_bytes,
                "fileDownload": config.file_download,
                "maxDownloadBytes": config.max_download_bytes,
            })
            ctx.report_progress(None, "Conversation UI is live")

        # Kickoff prompt as the first turn (unattended runs). On resume, push a
        # System: resume notice instead so the resumed conversation notices
        # promptly (resume.log stays the pull-based backstop).
        if config.auto_start and not resuming:
            await conversation.send(host_actions.AUTO_START_PROMPT)
        elif resuming:
            await conversation.send(f"{SYSTEM_MESSAGE_PREFIX}{RESUME_NOTICE}")

        # Park until the caller closes the conversation. There is no persistent
        # subprocess to await (the conversation is idle between turns), so
        # close_requested is the sole completion signal.
        await conversation.close_requested.wait()
        if config.host_protocol:
            # The keyword driver treats a body return without DONE as a premature
            # exit; a caller-requested close IS the clean end, so emit DONE
            # ourselves and park until the driver observes it and cancels this
            # body. With host_protocol=False the driver runs scaffolding-only, so
            # the body's normal return below is itself the clean completion.
            log_path = f"{host.workdir}/optio.log"
            await host.run_command(f"echo DONE >> {shlex.quote(log_path)}")
            await asyncio.Event().wait()  # cancelled by the driver

    async def _agent_sender(message: str) -> None:
        if config.mode == "conversation":
            if conversation is not None:
                await conversation.send(message)
            return
        await host_actions.send_text_to_agy(
            host, tmux_path, tmux_socket, tmux_session, message,
        )

    body = _conversation_body if config.mode == "conversation" else _agy_body
    try:
        await run_log_protocol_session(
            host, ctx,
            body=body,
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
        # Stop the conversation listener first so its long-lived SSE loops are
        # woken (bounded shutdown) before any subprocess teardown below.
        if conv_listener is not None:
            try:
                await conv_listener.stop()
            except Exception:
                _LOG.exception("conversation listener cleanup failed")
        # Conversation mode has no tmux/ttyd tree and no persistent agy
        # subprocess — each turn is spawned and reaped inside send(). close()
        # kills any in-flight turn and flips the conversation closed.
        if config.mode == "conversation" and conversation is not None:
            try:
                await conversation.close()
            except Exception:
                _LOG.exception("conversation close failed")
        if (
            launched_handle is not None
            and tmux_path is not None
            and tmux_socket is not None
            and tmux_session is not None
            and agy_path
        ):
            try:
                await host_actions.teardown_session_tree(
                    host,
                    tmux_path=tmux_path,
                    tmux_socket=tmux_socket,
                    tmux_session=tmux_session,
                    agy_path=agy_path,
                    ttyd_handle=launched_handle,
                    aggressive=cancelled,
                )
            except Exception:
                _LOG.exception("teardown_session_tree failed")

        # Reached-live gate: only capture if agy actually came up
        # (launched_handle is assigned strictly after a successful ttyd/agy
        # launch). An interrupt before launch leaves it None — skip capture so
        # any prior good snapshot survives and hasSavedState is untouched.
        if config.supports_resume and launched_handle is not None:
            try:
                await _capture_snapshot(
                    ctx, host,
                    end_state="cancelled" if cancelled else "done",
                    workdir_exclude=config.workdir_exclude,
                )
            except Exception:
                _LOG.exception(
                    "snapshot capture failed; proceeding with workdir wipe",
                )

        try:
            await host.cleanup_taskdir(aggressive=cancelled)
        except Exception:
            _LOG.exception("cleanup_taskdir failed")
        try:
            await host.disconnect()
        except Exception:
            _LOG.exception("host.disconnect failed")


async def _stream_blob(ctx: ProcessContext, blob_id) -> "AsyncIterator[bytes]":
    async with ctx.load_blob(blob_id) as reader:
        while True:
            chunk = await reader.read(1 << 20)
            if not chunk:
                break
            yield chunk


async def _capture_snapshot(
    ctx: ProcessContext,
    host: Host,
    *,
    end_state: str,
    workdir_exclude: list[str] | None,
) -> None:
    """Capture a single-blob resume snapshot of the (now static) workdir.

    ``agy``'s conversation state lives under ``home/.gemini/antigravity`` INSIDE
    the workdir, so one plaintext workdir tar carries everything ``--continue`` /
    ``--conversation <id>`` needs — no separate session blob. Streams the tar
    into GridFS, records the snapshot row, prunes to the retention limit
    (deleting stale blobs), and surfaces the Resume affordance.
    """
    async with ctx.store_blob("workdir") as wwriter:
        async for chunk in host.archive_workdir(workdir_exclude):
            await wwriter.write(chunk)
        workdir_blob_id = wwriter.file_id

    await insert_snapshot(
        ctx._db, ctx._prefix,
        process_id=ctx.process_id,
        end_state=end_state,
        workdir_blob_id=workdir_blob_id,
    )

    stale = await prune_snapshots(ctx._db, ctx._prefix, ctx.process_id)
    for blob_id in stale:
        try:
            await ctx.delete_blob(blob_id)
        except Exception:
            _LOG.exception("delete_blob(workdir) failed")

    await ctx.mark_has_saved_state()


def create_antigravity_task(
    process_id: str,
    name: str,
    config: AntigravityTaskConfig,
    description: str | None = None,
    metadata: dict | None = None,
) -> TaskInstance:
    """Return a TaskInstance that runs one optio-antigravity session.

    ``metadata`` is the caller app's task-tagging payload; it is stamped onto
    the TaskInstance verbatim and never read by the task itself.
    """

    async def _execute(ctx: ProcessContext) -> None:
        await run_antigravity_session(ctx, config)

    # iframe → the ttyd TUI widget. Conversation mode (Stage 6) carries the live
    # chat widget only when conversation_ui is on; otherwise no widget.
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
