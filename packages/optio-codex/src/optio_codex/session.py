"""State machine for one optio-codex session (Stage 0: iframe/ttyd, local)."""

from __future__ import annotations

import asyncio
import inspect
import logging
import mimetypes
import os
import secrets
import shlex
from typing import AsyncIterator

from optio_core.context import ProcessContext
from optio_core.models import BasicAuth, TaskInstance

from optio_agents import HookContext, RESUME_NOTICE, SYSTEM_MESSAGE_PREFIX, get_protocol
from optio_agents import seeds as _seeds
from optio_agents.input_listener import serialized, start_input_listener
from optio_agents.protocol.session import _SessionFailed, run_log_protocol_session
from optio_agents.uploads import materialize
from optio_agents.session_controls import model_control
from optio_host.host import Host, LocalHost, ProcessHandle, proc_wait
from optio_host.paths import task_dir

from optio_codex import cred_watcher, host_actions
from optio_codex import models as codex_models
from optio_codex.conversation import CodexConversation
from optio_codex.conversation_listener import ConversationListener
from optio_codex.fs_allowlist import (
    SandboxSettings,
    build_sandbox_cli_args,
    build_sandbox_config_overrides,
    resolve_sandbox_settings,
)
from optio_codex.prompt import compose_agents_md
from optio_codex.seed_manifest import CODEX_SEED_MANIFEST, CODEX_SEED_SUFFIX
from optio_codex.snapshots import (
    effective_workdir_exclude,
    insert_snapshot,
    load_latest_snapshot,
    prune_snapshots,
)
from optio_codex.types import CodexTaskConfig


_LOG = logging.getLogger(__name__)
READY_TIMEOUT_S = 30.0


def _build_host(config: CodexTaskConfig, process_id: str) -> Host:
    taskdir = task_dir(
        ssh=config.ssh, process_id=process_id, consumer_name="optio-codex",
    )
    return host_actions.build_host(config.ssh, taskdir)


async def _call_maybe_async(fn, *args) -> None:
    """Invoke a callback that may be sync or async."""
    result = fn(*args)
    if inspect.isawaitable(result):
        await result


def _teardown_aggressive(*, cancelled: bool, seeded: bool) -> bool:
    """Whether to SIGKILL codex immediately on teardown vs SIGTERM-and-wait.

    A **seeded** session is torn down GRACEFULLY even on cancel: codex's
    single-use ChatGPT refresh token may have rotated this session, and codex's
    auth.json write is best-effort — an aggressive SIGKILL can beat the flush,
    stranding the rotation so the credential save-back persists the now-spent
    token and the next launch demands re-auth. SIGTERM-and-wait lets codex
    flush first. A non-seeded session keeps the fast aggressive kill on cancel.
    """
    return cancelled and not seeded


async def run_codex_session(ctx: ProcessContext, config: CodexTaskConfig) -> None:
    """Execute function body for one optio-codex task instance."""
    host: Host = _build_host(config, ctx.process_id)
    # redirect (not suppress): codex's first-launch `codex login` opens the
    # loopback OAuth URL via xdg-open; the redirect shim captures it as a
    # BROWSER: marker so the driver surfaces it to the operator (who completes
    # the sign-in), instead of silently swallowing it. Matches claudecode/grok.
    protocol = get_protocol(browser="redirect")
    launched_handle: ProcessHandle | None = None
    tmux_path: str | None = None
    tmux_socket: str | None = None
    tmux_session: str | None = None
    codex_path: str | None = None
    ttyd_path: str | None = None
    # Stage 8: the task's resolved native-sandbox posture (mode + writable
    # roots + network), computed ONCE in _prepare from config + the real host
    # home, then rendered into every launch surface (iframe/exec argv via
    # build_sandbox_cli_args; the codex app-server launch via thread/start's
    # `sandbox` mode + build_sandbox_config_overrides in the conversation body).
    sandbox_settings: SandboxSettings | None = None
    cancelled = False
    # Whether a snapshot was restored this run (suppresses the auto-start
    # positional). Set by _prepare, read by the body.
    resuming = False
    # The session/rollout id recorded in the restored snapshot; drives the
    # `codex resume <id>` relaunch. None ⇒ fresh codex session even when the
    # workdir was restored (the snapshot predates any rollout).
    resume_session_id: str | None = None
    # Resolved seed id for a fresh, seeded launch (Stage 3). Set by _prepare
    # (str seed_id → itself; SeedProvider callable → awaited). Stays None on
    # resume and when no seed_id is configured.
    resolved_seed_id: str | None = None
    # Stage 4 lease + credential save-back. ``lease_holder`` is the task's
    # process_id when the seed came from a lease-holding SeedProvider
    # (renewed by the watcher, released at teardown). ``cred_baseline`` is
    # the post-merge auth.json fingerprint the watcher/backstop diff against.
    lease_holder: str | None = None
    cred_baseline: str | None = None
    cred_watch_task: "asyncio.Task | None" = None
    # iframe-input widget: an engine-side HTTP listener injects operator input
    # (typed messages + NAV keystrokes) into the codex tmux TUI. One lock
    # serializes human input against the system (_agent_sender) sends so tmux
    # injection bursts never interleave.
    input_runner = None
    injection_lock = asyncio.Lock()
    # Conversation mode (Stage 6). The CodexConversation is constructed inside
    # _conversation_body (it needs resume_thread_id, resolved by _prepare) and
    # published via ctx.publish_result; the per-task SSE listener (conversation_ui
    # only) is started after publish and torn down first in the finally block.
    conversation: CodexConversation | None = None
    conv_listener: ConversationListener | None = None

    await host.connect()

    async def _prepare(host: Host, hook_ctx: HookContext) -> None:
        """Restore a resume snapshot, provision codex + ttyd, plant AGENTS.md.

        Handed to run_log_protocol_session, which runs it AFTER
        host.setup_workdir() has wiped the workdir and BEFORE it subscribes
        the optio.log tail. That ordering is why the restore belongs here:
        the restored optio.log is rotated away below before the tail can
        replay its stale DONE/ERROR, and AGENTS.md is planted AFTER the
        restore so the restore cannot wipe it.

        Restore runs BEFORE ensure_codex_installed — a deliberate divergence
        from the grok template (which ensures first): codex's launch path is
        the per-task symlink INSIDE the workdir
        (<workdir>/home/.local/bin/codex), and restore_workdir empties the
        workdir before extracting. Provisioning after the restore re-creates
        the home tree and re-points the symlink (mkdir -p / ln -sfn are
        idempotent), so the launch path can never dangle.
        """
        nonlocal codex_path, ttyd_path, resuming, resume_session_id
        nonlocal resolved_seed_id, lease_holder, cred_baseline
        nonlocal sandbox_settings

        resume_requested = bool(getattr(ctx, "resume", False))
        snapshot = None
        if resume_requested:
            snapshot = await load_latest_snapshot(
                ctx._db, ctx._prefix, ctx.process_id,
            )
        resuming = snapshot is not None
        if resuming:
            # Restore the workdir tar (carries home/.codex — sessions/,
            # auth, config). A present snapshot that fails to restore is
            # fatal — the call is intentionally outside any except so it
            # surfaces to the caller (no silent fresh-start).
            await host.restore_workdir(_stream_blob(ctx, snapshot["workdirBlobId"]))
            await host_actions._rotate_optio_log(host)
            resume_session_id = snapshot.get("sessionId")
            if resume_session_id is None:
                _LOG.warning(
                    "resume: snapshot for %s carries no sessionId (codex never "
                    "persisted a rollout in that run); the workdir is restored "
                    "but codex starts a FRESH session — explicit-id resume "
                    "only, never `resume --last` (it silently mints a new "
                    "session on a miss).",
                    ctx.process_id,
                )

        codex_path = await host_actions.ensure_codex_installed(
            hook_ctx,
            install_if_missing=config.install_if_missing,
            install_dir=config.codex_install_dir,
        )
        # Stage 8: resolve the native-sandbox posture ONCE. ``~/`` grants
        # expand against the REAL host home (codex runs under an isolated
        # $HOME), so the settings need it up front; every launch surface
        # renders from this single object.
        host_home = await host.resolve_host_home()
        sandbox_settings = resolve_sandbox_settings(config, host_home=host_home)
        # Conversation mode is headless (codex app-server stdio) — no ttyd.
        if config.mode != "conversation":
            ttyd_path = await host_actions.ensure_ttyd_installed(
                hook_ctx,
                install_if_missing=config.install_ttyd_if_missing,
                install_dir=config.ttyd_install_dir,
            )
        if not resuming and config.seed_id is not None:
            # Seeded FRESH start: resolve the seed id (str → itself; a
            # SeedProvider callable → awaited, may raise
            # SeedUnavailableError) and overlay the stored codex identity
            # (auth.json + config.toml) into the fresh workdir BEFORE
            # AGENTS.md, so codex launches already-authed. Codex auth/config
            # are cwd-independent, so no rekey is needed — but the new
            # workdir must be pre-trusted (cwd-dependent, hence a post-merge
            # edit here rather than a manifest transform).
            if callable(config.seed_id):
                # A SeedProvider leases a seed from the pool (holder =
                # process_id); the watcher renews the lease, teardown
                # releases it. A plain string carries no lease.
                resolved_seed_id = await config.seed_id(ctx.process_id)
                lease_holder = ctx.process_id
            else:
                resolved_seed_id = config.seed_id
            await _seeds.merge_seed(
                ctx, host,
                seed_id=resolved_seed_id,
                manifest=CODEX_SEED_MANIFEST,
                suffix=CODEX_SEED_SUFFIX,
                decrypt=None,
            )
            await host_actions.ensure_workdir_trusted(host)
            # Baseline the merged auth.json so the in-session watcher and
            # the teardown backstop only save back a genuinely rotated token.
            cred_baseline = await cred_watcher.cred_fingerprint(host)
        await host.write_text(
            "AGENTS.md",
            compose_agents_md(
                config.consumer_instructions,
                documentation=protocol.documentation if config.host_protocol else None,
                host_protocol=config.host_protocol,
                workdir_exclude=config.workdir_exclude,
                supports_resume=config.supports_resume,
                file_download=config.file_download,
            ),
        )
        if config.supports_resume:
            await host_actions._append_resume_log_entry(host)
        if config.before_execute is not None:
            # End-of-prepare placement matches claudecode (its
            # _plant_session_content ends with before_execute, inside its
            # _prepare); opencode fires it inside the body instead.
            await config.before_execute(hook_ctx)

    async def _codex_body(host: Host, hook_ctx: HookContext) -> None:
        nonlocal launched_handle, tmux_path, tmux_socket, tmux_session
        nonlocal cred_watch_task, input_runner

        bind_addr = os.environ.get("OPTIO_WIDGET_TUNNEL_BIND", "127.0.0.1")
        upstream_host = os.environ.get("OPTIO_WIDGET_TUNNEL_HOST", "127.0.0.1")
        ttyd_iface = bind_addr if isinstance(host, LocalHost) else "127.0.0.1"

        codex_flags = [
            # `codex resume <id>` is a SUBCOMMAND — it must precede the flags.
            *host_actions.build_resume_args(resume_session_id),
            *host_actions.build_codex_flags(
                model=config.model,
                ask_for_approval=config.ask_for_approval,
                sandbox_args=build_sandbox_cli_args(sandbox_settings),
            ),
            # Positional kickoff prompt: fresh launches only (suppressed when
            # a snapshot was restored — re-kicking would duplicate the task).
            *host_actions.build_auto_start_args(
                auto_start=config.auto_start, resuming=resuming,
            ),
            # PUSH resume awareness (Gap 1): a System: notice positional appended
            # after `resume <id>` + flags so the resumed TUI session gets a "you
            # have been resumed" turn (mutually exclusive with the fresh-launch
            # kickoff above). Parity with claudecode/opencode/grok; resume.log
            # stays the pull-based backstop.
            *host_actions.build_resume_notice_args(resuming=resuming),
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

        # iframe-input widget: start the engine-side input listener and publish it
        # as the control upstream. The operator types messages / drives TUI menus
        # (e.g. pastes the login authorization code) via /api/widget-control. Both
        # the human path and the system _agent_sender share ``injection_lock`` so
        # tmux injection bursts never interleave.
        async def _human_input(text: str) -> None:
            await host_actions.send_text_to_codex(
                host, tmux_path, tmux_socket, tmux_session, text,
            )

        async def _human_key(key: str) -> None:
            await host_actions.send_key_to_codex(
                host, tmux_path, tmux_socket, tmux_session, key,
            )

        input_runner, input_port = await start_input_listener(
            bind_iface=bind_addr,
            on_input=serialized(injection_lock, _human_input),
            on_key=serialized(injection_lock, _human_key),
        )
        await ctx.set_control_upstream(f"http://{upstream_host}:{input_port}")

        # Start the in-session credential watcher for a seeded session: it
        # saves back the rotated auth.json, and (when the seed is leased)
        # renews the lease and aborts the session on lease loss.
        if resolved_seed_id is not None:
            cred_watch_task = asyncio.create_task(
                cred_watcher.run_credential_watcher(
                    ctx, host,
                    seed_id=resolved_seed_id,
                    baseline=cred_baseline,
                    encrypt=None,
                    decrypt=None,
                    lease_holder=lease_holder,
                )
            )

        while ctx.should_continue() and await host_actions.tmux_session_alive(
            host, tmux_path, tmux_socket, tmux_session,
        ):
            await asyncio.sleep(1.0)

    async def _conversation_body(host: Host, hook_ctx: HookContext) -> None:
        nonlocal launched_handle, conversation, conv_listener

        # Launch `codex app-server` directly (no tmux/ttyd). The sandbox MODE
        # and approval policy travel in thread/start params (the app-server has
        # no --sandbox flag); the writable_roots/network_access ride the same
        # `-c sandbox_workspace_write.*` overrides the iframe uses, on the
        # app-server command line (Stage 8, one SandboxSettings SSOT).
        # merge_stderr=False keeps codex diagnostics off the JSONL stdout.
        conversation = CodexConversation(
            cwd=host.workdir,
            permission_gate=config.permission_gate,
            model=config.model,
            sandbox=sandbox_settings.mode,
            # Plan B: on resume, continue the stored thread (thread/resume)
            # instead of starting a fresh one. resume_session_id is the codex
            # thread id recorded in the restored snapshot.
            resume_thread_id=resume_session_id if resuming else None,
        )
        argv = [
            codex_path, "app-server",
            *build_sandbox_config_overrides(sandbox_settings),
        ]
        cmd = " ".join(shlex.quote(a) for a in argv)
        env = {
            **host_actions._isolation_env(host.workdir),
            **(config.env or {}),
            **(hook_ctx.browser_launch_env or {}),
        }
        ctx.report_progress(None, "Launching Codex (conversation)…")
        handle = await host.launch_subprocess(
            cmd, env=env, cwd=host.workdir,
            env_remove=config.scrub_env, stdin=True, merge_stderr=False,
        )
        launched_handle = handle
        conversation.attach(handle)
        reader_task = asyncio.create_task(conversation.run_reader())
        try:
            await conversation.bootstrap()
        except Exception:
            reader_task.cancel()
            raise

        ctx.publish_result(conversation)
        ctx.report_progress(None, "Codex conversation is live")

        # Opt-in dashboard chat widget: per-task SSE listener over the
        # published conversation, reached via the widget proxy (which injects
        # the basic-auth credential).
        if config.conversation_ui:
            listener_password = secrets.token_urlsafe(32)
            bind_addr = os.environ.get("OPTIO_WIDGET_TUNNEL_BIND", "127.0.0.1")
            upstream_host = os.environ.get("OPTIO_WIDGET_TUNNEL_HOST", "127.0.0.1")
            # File upload flows through the generic optio-api /api/widget-upload
            # route → materializeUpload RPC → this per-task writer, which runs in
            # THIS process (only it holds the live Host). The writer lands the
            # bytes under <workdir>/uploads/<name> and fires config.on_upload; the
            # view injects a System: path reference so codex reads them with its
            # own tools.
            async def _upload_writer(filename: str, data: bytes) -> str:
                return await materialize(
                    host, host.workdir, filename, data,
                    hook_ctx=hook_ctx, on_upload=config.on_upload,
                )
            ctx.register_upload_writer(_upload_writer)

            # File download: serve workdir-confined bytes for the optio-file:
            # sentinel links codex emits. realpath guards against ../ escapes.
            async def _read_download(relpath: str) -> tuple[bytes, str]:
                workdir = host.workdir.rstrip("/")
                real = os.path.realpath(os.path.join(workdir, relpath))
                if real != workdir and not real.startswith(workdir + os.sep):
                    raise ValueError("forbidden")       # outside the workdir
                data = await host.fetch_bytes_from_host(real)
                if len(data) > config.max_download_bytes:
                    raise ValueError("too-large")
                mime = mimetypes.guess_type(real)[0] or "application/octet-stream"
                return data, mime

            conv_listener = ConversationListener(
                conversation, password=listener_password,
                download_reader=_read_download,
                max_download_bytes=config.max_download_bytes,
            )
            # In-process aiohttp app: binds directly on the widget-tunnel
            # interface, no host tunnel needed.
            listener_port = await conv_listener.start(bind_addr)
            await ctx.set_widget_upstream(
                f"http://{upstream_host}:{listener_port}",
                inner_auth=BasicAuth(username="optio", password=listener_password),
            )
            # Model picker options come from the model/list captured at
            # bootstrap (authed, exact ids), else the static fallback.
            model_list = codex_models.parse_model_list(conversation.model_list)
            current_model = (
                config.default_model
                or conversation.current_model_id
                or model_list.get("default")
            )
            # The model picker is now the generic id="model" SessionControl;
            # codex exposes only this one (INLINE switch via set_control).
            control = model_control(
                models=model_list["models"], current=current_model,
            )
            # The client POSTs uploads to the generic optio-api route, resolved
            # relative to {widgetProxyUrl} (=<base>/api/widget/<db>/<prefix>/<pid>/):
            # climb to <base>/api/, then descend into the sibling widget-upload
            # route with the SAME db/prefix/pid. Relative so a base path prefix or
            # non-origin API host is preserved (see resolveUploadUrl).
            upload_url = (
                "{widgetProxyUrl}../../../../widget-upload/"
                f"{ctx._db.name}/{ctx._prefix}/{ctx.process_id}"
            )
            await ctx.set_widget_data({
                "protocol": "codex",
                "toolVerbosity": config.tool_verbosity,
                "thinkingVerbosity": config.thinking_verbosity,
                "showSessionControls": config.show_session_controls,
                "nativeSpinner": config.native_spinner,
                "controls": [control.to_dict()],
                "showFileUpload": config.show_file_upload,
                "maxUploadBytes": config.max_upload_bytes,
                "fileDownload": config.file_download,
                "maxDownloadBytes": config.max_download_bytes,
                "uploadUrl": upload_url,
            })
            ctx.report_progress(None, "Conversation UI is live")

            # Resume history backfill: the resumed thread already carried its
            # prior conversation inline in the thread/resume response
            # (thread.turns[].items[]), which bootstrap stashed — but the
            # listener's replay buffer starts empty and only accrues LIVE turns,
            # so a viewer attaching after resume would see none of the prior
            # conversation. Now that ConversationListener above has subscribed to
            # conversation.on_event (in its constructor), re-emit every stored
            # item as the item/completed the live stream would have sent, through
            # the SAME on_event fan-out, so the whole prior history lands in the
            # replay buffer; a late viewer then reconstructs it exactly like live
            # turns. ORDERING is load-bearing: strictly AFTER the listener
            # subscribes (else the buffer misses the history) and BEFORE the
            # resume-notice send below (else that new turn would interleave ahead
            # of the history). Gated on resuming — a fresh thread/start carries no
            # prior turns.
            if resuming:
                replayed = await conversation.replay_history()
                if replayed:
                    _LOG.info(
                        "codex conversation resume: replayed %d prior events",
                        replayed,
                    )

        # Kickoff prompt as the first turn (headless: no positional prompt
        # path). Suppressed on resume — re-kicking would duplicate the task.
        # On resume, PUSH a System: resume notice instead so the resumed thread
        # notices promptly (parity; resume.log stays the pull-based backstop).
        if config.auto_start and not resuming:
            await conversation.send(host_actions.AUTO_START_PROMPT)
        elif resuming:
            await conversation.send(f"{SYSTEM_MESSAGE_PREFIX}{RESUME_NOTICE}")

        try:
            while True:
                wait_task = asyncio.create_task(proc_wait(handle))
                close_task = asyncio.create_task(
                    conversation.close_requested.wait())
                done, _ = await asyncio.wait(
                    {wait_task, close_task},
                    return_when=asyncio.FIRST_COMPLETED,
                )
                for t in (wait_task, close_task):
                    if t not in done:
                        t.cancel()

                if close_task in done and wait_task not in done:
                    # Caller asked to close: cooperative clean end.
                    if config.host_protocol:
                        # The keyword driver treats a body return without DONE
                        # as a premature exit; a caller-requested close IS the
                        # clean end, so emit DONE ourselves and park until the
                        # driver observes it and cancels this body.
                        log_path = f"{host.workdir}/optio.log"
                        await host.run_command(
                            f"echo DONE >> {shlex.quote(log_path)}"
                        )
                        await asyncio.Event().wait()  # cancelled by the driver
                    break

                # Subprocess exited on its own.
                try:
                    rc = wait_task.result()
                except Exception:
                    rc = None
                if (
                    not conversation.close_requested.is_set()
                    and ctx.should_continue()
                ):
                    raise RuntimeError(f"codex exited unexpectedly (exit {rc})")
                break
        finally:
            reader_task.cancel()
            try:
                await reader_task
            except asyncio.CancelledError:
                pass

    async def _agent_sender(message: str) -> None:
        if config.mode == "conversation":
            await conversation.send(message)
            return
        # Share the iframe-input lock so a system send never interleaves with a
        # concurrent operator injection into the same tmux TUI.
        async with injection_lock:
            await host_actions.send_text_to_codex(
                host, tmux_path, tmux_socket, tmux_session, message,
            )

    body = _conversation_body if config.mode == "conversation" else _codex_body
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
        # Codex authenticates (ChatGPT mode) with a SINGLE-USE rotating refresh
        # token. If codex rotated it this session, the new auth.json must reach
        # the seed via the backstop below — but an aggressive SIGKILL can beat
        # codex's flush, stranding the rotation (the seed keeps the now-spent
        # token → the next launch demands re-auth). So when a SEED is in use,
        # tear codex down GRACEFULLY (SIGTERM + wait) even on cancel, giving it
        # time to persist auth.json before the final save-back reads it. Only a
        # non-seeded session keeps the fast aggressive kill on cancel.
        codex_aggressive = _teardown_aggressive(
            cancelled=cancelled, seeded=resolved_seed_id is not None,
        )
        # Drop the in-process upload writer so a late materializeUpload RPC can't
        # reach a torn-down Host (raises NoUploadWriter instead). Runs whenever
        # the conversation-ui branch may have registered one.
        if conv_listener is not None:
            try:
                ctx.clear_upload_writer()
            except Exception:
                _LOG.exception("clear upload writer failed")
        # Stop the conversation listener first so its long-lived SSE loops are
        # woken (bounded shutdown) before the subprocess teardown below.
        if conv_listener is not None:
            try:
                await conv_listener.stop()
            except Exception:
                _LOG.exception("conversation listener cleanup failed")
        # Conversation mode has no tmux/ttyd tree — terminate the app-server
        # subprocess directly. Its EOF drives the conversation to closed.
        if config.mode == "conversation" and launched_handle is not None:
            try:
                await host.terminate_subprocess(
                    launched_handle, aggressive=codex_aggressive)
            except Exception:
                _LOG.exception("terminate codex conversation subprocess failed")
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
                    aggressive=codex_aggressive,
                )
            except Exception:
                _LOG.exception("teardown_session_tree failed")

        # Tear down the iframe-input listener + clear its control upstream.
        if input_runner is not None:
            try:
                await ctx.clear_control_upstream()
            except Exception:
                _LOG.exception("clear_control_upstream failed")
            try:
                await input_runner.cleanup()
            except Exception:
                _LOG.exception("input listener cleanup failed")

        # Stop the credential watcher before the final save-back so the two
        # never race on the same seed blob.
        if cred_watch_task is not None:
            cred_watch_task.cancel()
            try:
                await cred_watch_task
            except asyncio.CancelledError:
                pass

        # Final backstop save-back — LOAD-BEARING, not defensive: codex's
        # refresh already consumed the old refresh token server-side
        # (single-use, openai/codex#15410); a rotation in the last poll
        # window is persisted ONLY here. Runs after codex terminated so
        # auth.json is final.
        if resolved_seed_id is not None:
            try:
                cred_baseline = await cred_watcher.save_back_if_changed(
                    ctx, host,
                    seed_id=resolved_seed_id,
                    baseline=cred_baseline,
                    encrypt=None,
                    decrypt=None,
                )
            except Exception:
                _LOG.exception("final credential save-back failed")

        # Release the lease AFTER the final save-back (opencode's deliberate
        # ordering, ported via grok): a new acquirer must never merge the
        # pre-save-back blob.
        if lease_holder is not None and resolved_seed_id is not None:
            try:
                await _seeds.release(
                    ctx._db, prefix=ctx._prefix, suffix=CODEX_SEED_SUFFIX,
                    seed_id=resolved_seed_id, holder=lease_holder,
                )
            except Exception:
                _LOG.exception("lease release failed (TTL will reclaim)")

        # Seed capture (fresh only): store this session's codex identity as
        # a reusable seed so a later fresh task can start already-authed.
        # Reached-live gate: launched_handle is assigned strictly after a
        # successful launch — an interrupt before launch leaves it None.
        # Guarded on a VALID auth.json (capture_gate_ok) — never seed a
        # login-less identity. Ignored on resume.
        if (
            not resuming
            and config.on_seed_saved is not None
            and launched_handle is not None
        ):
            try:
                if not await cred_watcher.capture_gate_ok(host):
                    _LOG.warning(
                        "seed capture skipped: home/.codex/auth.json absent "
                        "or invalid (login-less session)",
                    )
                else:
                    seed_id = await _seeds.capture_seed(
                        ctx, host,
                        manifest=CODEX_SEED_MANIFEST,
                        suffix=CODEX_SEED_SUFFIX,
                        encrypt=None,
                    )
                    # 2nd arg (account summary) is resolved in a later
                    # stage; None for now.
                    await _call_maybe_async(config.on_seed_saved, seed_id, None)
            except Exception:
                _LOG.exception(
                    "seed capture failed; callback not fired, teardown continues",
                )

        # Reached-live gate: only capture if codex actually came up
        # (launched_handle is assigned strictly after a successful ttyd/codex
        # launch). An interrupt before launch leaves it None — skip capture
        # so any prior good snapshot survives and hasSavedState is untouched.
        if config.supports_resume and launched_handle is not None:
            try:
                await _capture_snapshot(
                    ctx, host,
                    end_state="cancelled" if cancelled else "done",
                    workdir_exclude=config.workdir_exclude,
                    # Iframe mode: scan the newest rollout filename. The
                    # conversation body records the live thread id captured at
                    # thread/start (thread/resume's resume source) instead.
                    session_id=(
                        conversation.thread_id
                        if config.mode == "conversation" and conversation is not None
                        else await host_actions.read_latest_session_id(host)
                    ),
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
    session_id: str | None,
) -> None:
    """Capture a single-blob resume snapshot of the (now static) workdir.

    Codex's rollout store lives under ``home/.codex/sessions`` INSIDE the
    workdir, so one workdir tar carries everything ``codex resume <id>``
    needs; ``session_id`` records WHICH session to resume. Streams the tar
    into GridFS honoring the effective exclude list, records the snapshot
    row, prunes to the retention limit (deleting stale blobs), and surfaces
    the Resume affordance.
    """
    exclude = effective_workdir_exclude(workdir_exclude)
    async with ctx.store_blob("workdir") as wwriter:
        async for chunk in host.archive_workdir(exclude):
            await wwriter.write(chunk)
        workdir_blob_id = wwriter.file_id

    await insert_snapshot(
        ctx._db, ctx._prefix,
        process_id=ctx.process_id,
        end_state=end_state,
        workdir_blob_id=workdir_blob_id,
        session_id=session_id,
    )

    stale = await prune_snapshots(ctx._db, ctx._prefix, ctx.process_id)
    for blob_id in stale:
        try:
            await ctx.delete_blob(blob_id)
        except Exception:
            _LOG.exception("delete_blob(workdir) failed")

    await ctx.mark_has_saved_state()


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

    # iframe → the ttyd TUI widget WITH an operator input box (iframe-input): a
    # textarea to type messages / paste the login code + on-screen NAV keys to
    # drive codex's TUI menus, reached via the control upstream. Conversation
    # mode carries the live chat widget only when conversation_ui is on;
    # otherwise no widget (the published Conversation is driven programmatically).
    if config.conversation_ui:
        ui_widget: str | None = "conversation"
    elif config.mode == "conversation":
        ui_widget = None
    else:
        ui_widget = "iframe-input"

    return TaskInstance(
        execute=_execute,
        process_id=process_id,
        name=name,
        description=description,
        ui_widget=ui_widget,
        supports_resume=config.supports_resume,
        metadata=metadata or {},
    )