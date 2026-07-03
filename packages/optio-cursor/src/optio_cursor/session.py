"""State machine for one optio-cursor session (Stage 0: iframe/ttyd, local).

Orchestrates a Host (local or remote) through resolve cursor-agent → install
ttyd → plant AGENTS.md (+ cli-config.json) → launch ttyd(cursor-agent) inside
tmux → optio.log protocol session → teardown.

Adapted from optio-grok's iframe path. Stage 2 adds the resume/snapshot
branch, Stage 3 the seed branch, Stage 4 the pooled lease + credential
save-back, Stage 6 the conversation branch (headless ``cursor-agent acp``
publishing a live CursorConversation). fs-isolation arrives in a later stage.
"""

from __future__ import annotations

import asyncio
import inspect
import json
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
from optio_agents import seeds as _seeds
from optio_agents.protocol.session import _SessionFailed, run_log_protocol_session
from optio_host.host import Host, LocalHost, ProcessHandle, proc_wait
from optio_host.paths import task_dir

from optio_cursor import cred_watcher, host_actions
from optio_cursor import models as cursor_models
from optio_cursor.conversation import CursorConversation
from optio_cursor.conversation_listener import ConversationListener
from optio_cursor.prompt import compose_agents_md
from optio_cursor.seed_manifest import CURSOR_SEED_MANIFEST, CURSOR_SEED_SUFFIX
from optio_cursor.snapshots import (
    insert_snapshot,
    load_latest_snapshot,
    prune_snapshots,
)
from optio_cursor.types import CursorTaskConfig


_LOG = logging.getLogger(__name__)

READY_TIMEOUT_S = 30.0


async def _call_maybe_async(fn, *args) -> None:
    """Invoke a callback that may be sync or async."""
    result = fn(*args)
    if inspect.isawaitable(result):
        await result


def _build_host(config: CursorTaskConfig, process_id: str) -> Host:
    """Construct the appropriate Host for the given config.

    Extracted so tests can monkeypatch ``session._build_host`` to inject a
    fake host (mirrors the grok/claudecode pattern). Delegates to
    host_actions.build_host."""
    taskdir = task_dir(
        ssh=config.ssh, process_id=process_id, consumer_name="optio-cursor",
    )
    return host_actions.build_host(config.ssh, taskdir)


async def _build_claustrum_wrap(
    host: Host, config: CursorTaskConfig, claustrum_path: str | None,
) -> list[str] | None:
    """claustrum argv prefix for an fs-isolated launch, or None when
    fs_isolation is off. Shared by the iframe and conversation launch paths so
    both confine cursor-agent + its whole subprocess tree identically.

    ``~/`` caller extras expand against the REAL host home (cursor-agent runs
    under an isolated $HOME, and grants reach claustrum verbatim — no shell
    between). Ported from optio-claudecode's ``_build_claustrum_wrap``.
    """
    if not config.fs_isolation:
        return None
    from . import fs_allowlist
    cache_dir = await host_actions._resolve_cursor_cache_dir(
        host, config.cursor_install_dir,
    )
    host_home = (
        await host.resolve_host_home() if config.extra_allowed_dirs else None
    )
    grants = fs_allowlist.build_grant_flags(
        workdir=host.workdir,
        cursor_cache_dir=cache_dir,
        extra_allowed_dirs=config.extra_allowed_dirs,
        host_home=host_home,
    )
    return [claustrum_path, "--best-effort", "--abi-min", "1", *grants, "--"]


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
    # Whether a snapshot was restored this run (drives --continue + the
    # auto-start suppression). Set by _prepare, read by the body + teardown.
    resuming = False
    pass_continue = False
    # Resolved seed id for a fresh, seeded launch (Stage 3). Set by _prepare
    # (str seed_id → itself; SeedProvider callable → awaited). Stays None on
    # resume and when no seed_id is configured.
    resolved_seed_id: str | None = None
    # Stage 4 lease + credential save-back. ``lease_holder`` is the task's
    # process_id when the seed came from a lease-holding SeedProvider (renewed
    # by the watcher, released at teardown). ``cred_baseline`` is the
    # post-merge auth.json fingerprint the watcher/backstop diff against.
    lease_holder: str | None = None
    cred_baseline: str | None = None
    cred_watch_task: "asyncio.Task | None" = None
    # Path to the claustrum Landlock binary on the (possibly remote) host, set
    # by _prepare when fs_isolation is on; read by both bodies to wrap the
    # cursor-agent launch. Stays None when fs_isolation is off.
    claustrum_path: str | None = None

    await host.connect()

    async def _prepare(host: Host, hook_ctx: HookContext) -> None:
        """Resolve cursor-agent + ttyd, restore a resume snapshot, and plant
        cli-config.json and AGENTS.md.

        Handed to run_log_protocol_session, which runs it AFTER
        host.setup_workdir() has wiped the workdir and BEFORE it subscribes
        the optio.log tail. That ordering is why restore belongs here: the
        restored optio.log is rotated away below before the tail can replay
        its stale DONE/ERROR, and cli-config.json + AGENTS.md are (re)planted
        AFTER the restore so they are not wiped by it.
        """
        nonlocal cursor_path, ttyd_path, resuming, pass_continue, resolved_seed_id
        nonlocal lease_holder, cred_baseline, claustrum_path
        cursor_path = await host_actions.ensure_cursor_installed(
            hook_ctx,
            install_if_missing=config.install_if_missing,
            install_dir=config.cursor_install_dir,
        )
        # Conversation mode is headless (cursor-agent acp) — no ttyd needed.
        if config.mode != "conversation":
            ttyd_path = await host_actions.ensure_ttyd_installed(
                hook_ctx,
                install_if_missing=config.install_ttyd_if_missing,
                install_dir=config.ttyd_install_dir,
            )

        # Filesystem isolation (Stage 8): provision the claustrum Landlock
        # binary BEFORE launch. Any failure raises here — fail-closed: the task
        # never proceeds to an unconfined launch. Skipped when fs_isolation is
        # off. Placed onto the same optio-cursor cache tree as cursor-agent.
        if config.fs_isolation:
            claustrum_path = await host_actions.ensure_claustrum_installed(
                hook_ctx, install_dir=config.cursor_install_dir,
            )

        resume_requested = bool(getattr(ctx, "resume", False))
        snapshot = None
        if resume_requested:
            snapshot = await load_latest_snapshot(
                ctx._db, ctx._prefix, ctx.process_id,
            )
        resuming = snapshot is not None
        if resuming:
            # Restore the workdir tar (carries home/.cursor, i.e. cursor's
            # chat store). A present snapshot that fails to restore is fatal —
            # the restore call is intentionally outside any except so it
            # surfaces to the caller (no silent fresh-start). cursor-agent +
            # ttyd survive the restore: both live OUTSIDE the workdir, so no
            # re-install is needed.
            await host.restore_workdir(_stream_blob(ctx, snapshot["workdirBlobId"]))
            await host_actions._rotate_optio_log(host)
            # A restored snapshot means cursor persisted a chat for this cwd;
            # --continue resumes the most recent one. $HOME lives inside the
            # restored workdir at the same absolute path (deterministic
            # taskdir), so the cwd-keyed chat lookup matches.
            pass_continue = True

        # Permission rules are config-planted (cursor-agent has no
        # --allow/--deny argv): write cli-config.json under the per-task HOME
        # BEFORE launch so cursor reads it on startup. Planted BEFORE any seed
        # merge, so a seeded cli-config.json overlays it (seed wins — the
        # claudecode plant-then-merge pattern); when the seed carries none,
        # the generated rules survive.
        cli_config = host_actions.build_cli_config(
            allowed_tools=config.allowed_tools,
            disallowed_tools=config.disallowed_tools,
        )
        if cli_config is not None:
            await host.write_text(
                "home/.cursor/cli-config.json",
                json.dumps(cli_config, indent=2) + "\n",
            )

        if not resuming and config.seed_id is not None:
            # Seeded FRESH start: resolve the seed id (str → itself; a
            # SeedProvider callable → awaited, may raise SeedUnavailableError)
            # and overlay the stored cursor identity (auth.json +
            # cli-config.json) into the fresh workdir BEFORE AGENTS.md, so
            # cursor launches already-authed. No --continue: this begins a
            # NEW session. Cursor auth/config are cwd-independent, so no
            # rekey is needed.
            if callable(config.seed_id):
                # A SeedProvider leases a seed from the pool (holder =
                # process_id); the watcher renews the lease, teardown releases
                # it. A plain string carries no lease.
                resolved_seed_id = await config.seed_id(ctx.process_id)
                lease_holder = ctx.process_id
            else:
                resolved_seed_id = config.seed_id
            await _seeds.merge_seed(
                ctx, host,
                seed_id=resolved_seed_id,
                manifest=CURSOR_SEED_MANIFEST,
                suffix=CURSOR_SEED_SUFFIX,
                decrypt=None,
            )
            # Baseline the merged auth.json so the in-session watcher and the
            # teardown backstop only save back a genuinely rotated token.
            cred_baseline = await cred_watcher.cred_fingerprint(host)

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

    async def _cursor_body(host: Host, hook_ctx: HookContext) -> None:
        nonlocal launched_handle, tmux_path, tmux_socket, tmux_session
        nonlocal cred_watch_task

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
            resuming=pass_continue,
            fs_isolation=config.fs_isolation,
        )
        cursor_flags = [
            *cursor_flags,
            *host_actions.build_auto_start_args(
                auto_start=config.auto_start, resuming=resuming,
            ),
            # PUSH resume awareness: a System: notice appended after --continue
            # so the resumed TUI session gets a "you have been resumed" turn
            # (mutually exclusive with the fresh-launch kickoff above). Parity
            # with claudecode/opencode/grok; resume.log stays the pull-based
            # backstop.
            *host_actions.build_resume_notice_args(resuming=resuming),
        ]
        launch_env = {
            **(config.env or {}),
            **(hook_ctx.browser_launch_env or {}),
        }
        if config.api_key:
            # api_key rides the launch env, never argv (process listings).
            launch_env["CURSOR_API_KEY"] = config.api_key
        ctx.report_progress(None, "Launching Cursor…")
        # Fs-isolation wrap: confine cursor-agent + its whole tool/subprocess
        # tree inside the tmux pane (None when fs_isolation is off).
        claustrum_wrap = await _build_claustrum_wrap(host, config, claustrum_path)
        handle, ttyd_port, tmux_socket, tmux_session = await host_actions.launch_ttyd_with_cursor(
            host,
            ttyd_path=ttyd_path,
            cursor_path=cursor_path,
            bind_iface=ttyd_iface,
            extra_env=launch_env,
            cursor_flags=cursor_flags,
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
        ctx.report_progress(None, "Cursor is live")

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

        # Await the cursor process inside tmux (NOT the ttyd connection). ttyd
        # stays up serving viewers; the task is alive while the tmux session
        # is. The protocol driver cancels this body when it sees DONE/ERROR in
        # optio.log; if cursor exits some other way, has-session goes false and
        # the body returns -> driver treats it as premature exit.
        while ctx.should_continue() and await host_actions.tmux_session_alive(
            host, tmux_path, tmux_socket, tmux_session,
        ):
            await asyncio.sleep(1.0)

    conversation: CursorConversation | None = None
    if config.mode == "conversation":
        conversation = CursorConversation(
            cwd=host.workdir, permission_gate=config.permission_gate,
        )
    # Per-task conversation listener (conversation_ui only). Started in the
    # body after publish_result, torn down in the finally block.
    conv_listener: ConversationListener | None = None

    async def _conversation_body(host: Host, hook_ctx: HookContext) -> None:
        nonlocal launched_handle, cred_watch_task, conv_listener

        # Launch `cursor-agent [--model M] [--force] acp` directly (no
        # tmux/ttyd). --force is used only when no permission gate is wired,
        # so cursor never blocks on a prompt nobody answers; with the gate on,
        # cursor surfaces session/request_permission and the published
        # conversation's handler answers it.
        argv = host_actions.build_conversation_argv(
            cursor_path,
            model=config.model,
            force=not config.permission_gate,
            sandbox=config.sandbox,
            fs_isolation=config.fs_isolation,
        )
        # Same claustrum fs-isolation wrap as the iframe path; claustrum
        # execve's cursor-agent, so the bidirectional ACP stdio pipes pass
        # through unchanged (None when fs_isolation is off).
        wrap = await _build_claustrum_wrap(host, config, claustrum_path)
        if wrap:
            argv = [*wrap, *argv]
        cmd = " ".join(shlex.quote(a) for a in argv)
        # Same per-task HOME/XDG isolation as the iframe launch. PATH is
        # inherited by launch_subprocess (os.environ.copy) so interpreters
        # resolve. merge_stderr=False keeps cursor's diagnostics off the
        # JSON-RPC stdout stream.
        env = {
            **host_actions._isolation_env(host.workdir),
            **(config.env or {}),
            **(hook_ctx.browser_launch_env or {}),
        }
        if config.api_key:
            # api_key rides the launch env, never argv (process listings).
            env["CURSOR_API_KEY"] = config.api_key
        ctx.report_progress(None, "Launching Cursor (conversation)…")
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
        ctx.report_progress(None, "Cursor conversation is live")

        # Opt-in dashboard chat widget: start a per-task SSE listener over the
        # published conversation and publish it as the "conversation" widget
        # via the widget proxy (which injects the basic-auth credential).
        if config.conversation_ui:
            listener_password = secrets.token_urlsafe(32)
            bind_addr = os.environ.get("OPTIO_WIDGET_TUNNEL_BIND", "127.0.0.1")
            upstream_host = os.environ.get("OPTIO_WIDGET_TUNNEL_HOST", "127.0.0.1")
            # File upload: bytes land under <workdir>/uploads with a sanitized
            # name; the view injects a System: path reference so cursor reads
            # them with its own tools (headless cursor has no inline ingest).
            uploads_dir = f"{host.workdir}/uploads"

            async def _write_upload(name: str, data: bytes) -> str:
                safe = re.sub(r"[^A-Za-z0-9._-]", "_", (name.split("/")[-1] or "file"))[:200] or "file"
                await host.put_file_to_host(data, f"{uploads_dir}/{safe}")
                return f"uploads/{safe}"

            # File download: serve workdir-confined bytes for the optio-file:
            # sentinel links cursor emits. realpath guards against ../ escapes.
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
            # Model picker options: prefer the ACP session block captured at
            # bootstrap (authed, exact ids set_model accepts), else
            # `cursor-agent models` (auth-gated), else a static list.
            # default_model overrides the picker's initial value; otherwise
            # the live current model is shown.
            model_list = await cursor_models.fetch_available_models(
                conversation.session_models, host=host, cursor_path=cursor_path,
            )
            current_model = config.default_model or model_list.get("default")
            await ctx.set_widget_data({
                "protocol": "cursor",
                "toolVerbosity": config.tool_verbosity,
                "thinkingVerbosity": config.thinking_verbosity,
                "showModelSelector": config.show_model_selector,
                "models": model_list["models"],
                "currentModel": current_model,
                "showFileUpload": config.show_file_upload,
                "maxUploadBytes": config.max_upload_bytes,
                "fileDownload": config.file_download,
                "maxDownloadBytes": config.max_download_bytes,
            })
            ctx.report_progress(None, "Conversation UI is live")

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

        # Kickoff prompt as the first turn (headless: no positional prompt
        # path). On resume, push a System: resume notice instead so the resumed
        # session notices promptly (parity with claudecode/opencode/grok;
        # resume.log stays the pull-based backstop). The System: convention is
        # always taught in the prompt, so no host_protocol gate is needed here.
        if config.auto_start and not resuming:
            await conversation.send(host_actions.AUTO_START_PROMPT)
        elif resuming:
            await conversation.send(f"{SYSTEM_MESSAGE_PREFIX}{RESUME_NOTICE}")

        try:
            while True:
                wait_task = asyncio.create_task(proc_wait(handle))
                close_task = asyncio.create_task(conversation.close_requested.wait())
                done, _ = await asyncio.wait(
                    {wait_task, close_task}, return_when=asyncio.FIRST_COMPLETED,
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
                if not conversation.close_requested.is_set() and ctx.should_continue():
                    raise RuntimeError(f"cursor exited unexpectedly (exit {rc})")
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
        await host_actions.send_text_to_cursor(
            host, tmux_path, tmux_socket, tmux_session, message,
        )

    body = _conversation_body if config.mode == "conversation" else _cursor_body
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
        # woken (bounded shutdown) before the subprocess teardown below.
        if conv_listener is not None:
            try:
                await conv_listener.stop()
            except Exception:
                _LOG.exception("conversation listener cleanup failed")
        # Conversation mode has no tmux/ttyd tree — terminate the cursor-agent
        # subprocess directly. Its EOF drives the conversation to closed.
        if config.mode == "conversation" and launched_handle is not None:
            try:
                await host.terminate_subprocess(launched_handle, aggressive=cancelled)
            except Exception:
                _LOG.exception("terminate cursor conversation subprocess failed")
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

        # Stop the credential watcher before the final save-back so the two
        # never race on the same seed blob.
        if cred_watch_task is not None:
            cred_watch_task.cancel()
            try:
                await cred_watch_task
            except asyncio.CancelledError:
                pass

        # Final backstop save-back — LOAD-BEARING, not defensive: cursor's own
        # auth write-back is best-effort and the provider may have already
        # consumed the old refresh token; a rotation in the last poll window is
        # persisted ONLY here. Runs after cursor terminated so auth.json is
        # final.
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
        # ordering, via grok): a new acquirer must never merge the
        # pre-save-back blob.
        if lease_holder is not None and resolved_seed_id is not None:
            try:
                await _seeds.release(
                    ctx._db, prefix=ctx._prefix, suffix=CURSOR_SEED_SUFFIX,
                    seed_id=resolved_seed_id, holder=lease_holder,
                )
            except Exception:
                _LOG.exception("lease release failed (TTL will reclaim)")

        # Seed capture (fresh only): store this session's cursor identity as
        # a reusable seed so a later fresh task can start already-authed.
        # Same reached-live gate as snapshots (launched_handle assigned
        # strictly after a successful launch). Guarded on auth.json present —
        # never seed a login-less identity. Ignored on resume.
        if (
            not resuming
            and config.on_seed_saved is not None
            and launched_handle is not None
        ):
            try:
                if not await cred_watcher.capture_gate_ok(host):
                    _LOG.warning(
                        "seed capture skipped: home/.config/cursor/auth.json "
                        "absent or invalid (login-less session)",
                    )
                else:
                    seed_id = await _seeds.capture_seed(
                        ctx, host,
                        manifest=CURSOR_SEED_MANIFEST,
                        suffix=CURSOR_SEED_SUFFIX,
                        encrypt=None,
                    )
                    # 2nd arg (account summary) is resolved in a later stage;
                    # None in Stage 3.
                    await _call_maybe_async(config.on_seed_saved, seed_id, None)
            except Exception:
                _LOG.exception(
                    "seed capture failed; callback not fired, teardown continues",
                )

        # Reached-live gate: only capture if cursor actually came up
        # (launched_handle is assigned strictly after a successful ttyd/cursor
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

    Cursor's chat store lives under ``home/.cursor`` INSIDE the workdir, so
    one plaintext workdir tar carries everything ``--continue`` needs — no
    separate session blob (unlike optio-claudecode) and no defensive home
    wipe. Streams the tar into GridFS, records the snapshot row, prunes to
    the retention limit (deleting stale blobs), and surfaces the Resume
    affordance.
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

    # iframe → the ttyd TUI widget. Conversation mode carries the live chat
    # widget only when conversation_ui is on (Group 6b); otherwise no widget
    # (the published Conversation is driven programmatically).
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
