"""State machine for one optio-claudecode session.

Orchestrates a Host (local or remote) through install → (resume restore |
fresh plant) → launch ttyd(claude) → protocol session → snapshot capture.

Most protocol plumbing lives in optio-host. This module does the
claudecode-specific orchestration plus the resume/snapshot brackets,
mirroring optio-opencode's session module. The one structural difference
from opencode: sensitive state is the ``<workdir>/home/.claude/`` subtree
(tarred + optionally encrypted) rather than an exported session DB.
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
import sys
import time as _time
from datetime import datetime, timezone
from typing import AsyncIterator, Callable

from optio_core.context import ProcessContext
from optio_core.models import BasicAuth, TaskInstance

from optio_agents.context import HookContext
from optio_agents.protocol.session import _SessionFailed, run_log_protocol_session
from optio_host.host import Host, LocalHost, ProcessHandle, RemoteHost, proc_wait
from optio_host.paths import task_dir
from optio_agents import seeds as _seeds
from optio_agents import RESUME_NOTICE, SYSTEM_MESSAGE_PREFIX, get_protocol

from optio_claudecode import cred_watcher
from optio_claudecode import host_actions
from optio_claudecode import models as cc_models
from optio_claudecode.conversation import ClaudeCodeConversation
from optio_claudecode.conversation_listener import ConversationListener
from optio_claudecode.input_listener import serialized, start_input_listener
from optio_claudecode.account import resolve_account_summary
from optio_claudecode.oauth_redirect import rewrite_oauth_redirect
from optio_claudecode.seed_manifest import (
    CLAUDE_CRED_MANIFEST,
    CLAUDE_SEED_MANIFEST,
    CLAUDE_SEED_SUFFIX,
    _rekey_claude_json_projects,
)
from optio_claudecode.prompt import DEFAULT_CONVERSATION_INSTRUCTIONS, compose_agents_md
from optio_claudecode.snapshots import (
    insert_snapshot,
    load_latest_snapshot,
    prune_snapshots,
)
from optio_claudecode.transcript import rebase_session_blob
from optio_claudecode.types import ClaudeCodeTaskConfig


_LOG = logging.getLogger(__name__)

# Cancel/capture step tracing — shares the optio_core.cancel_trace logger and
# the OPTIO_CANCEL_TRACE env gate so its lines interleave with the executor's
# cancel trace. Diagnostic only; no behavioral effect.
_trace_logger = logging.getLogger("optio_core.cancel_trace")
_CANCEL_TRACE = os.environ.get("OPTIO_CANCEL_TRACE", "0").lower() in ("1", "true", "yes")


def _trace(fmt: str, *args: object) -> None:
    if _CANCEL_TRACE:
        _trace_logger.warning("[%.3f] optio-claudecode " + fmt, _time.monotonic(), *args)


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


def _fs_isolation_dirs(
    config: ClaudeCodeTaskConfig, host: Host,
) -> list[tuple[str, str]] | None:
    """The agent-facing (path, mode) list of directories it may touch under fs
    isolation (its workdir + caller extras), or None when isolation is off.
    Used to tell the agent its sandbox bounds in CLAUDE.md. Paths stay
    verbatim (incl. ``~/``): the agent's own $HOME view is what it needs."""
    if not config.fs_isolation:
        return None
    extras = [(ad.path, ad.mode) for ad in (config.extra_allowed_dirs or [])]
    return [(host.workdir.rstrip("/"), "rwx"), *extras]


async def _build_claustrum_wrap(
    host: Host, config: ClaudeCodeTaskConfig, claustrum_path: str | None,
) -> list[str] | None:
    """claustrum argv prefix for an fs-isolated launch, or None when fs_isolation
    is off. Shared by the iframe and conversation launch paths."""
    if not config.fs_isolation:
        return None
    from . import fs_allowlist
    cache_dir = await host_actions._resolve_cache_dir(host, config.claude_install_dir)
    # `~/` caller extras expand against the REAL host home (the claude process
    # runs under an isolated $HOME, and grants reach claustrum verbatim).
    host_home = (
        await host.resolve_host_home() if config.extra_allowed_dirs else None
    )
    grants = fs_allowlist.build_grant_flags(
        workdir=host.workdir,
        claude_cache_dir=cache_dir,
        extra_allowed_dirs=config.extra_allowed_dirs,
        host_home=host_home,
    )
    return [claustrum_path, "--best-effort", "--abi-min", "1", *grants, "--"]


def _partials_enabled(config: ClaudeCodeTaskConfig) -> bool:
    """--include-partial-messages: standalone knob, or implied by
    conversation_ui (its live view is fed by partials)."""
    return config.include_partial_messages or config.conversation_ui


async def run_claudecode_session(
    ctx: ProcessContext, config: ClaudeCodeTaskConfig,
) -> None:
    """Execute function body for one optio-claudecode task instance."""
    host: Host = _build_host(config, ctx.process_id)
    protocol = get_protocol(browser="redirect")
    launched_handle: ProcessHandle | None = None
    tmux_path: str | None = None
    tmux_socket: str | None = None
    tmux_session: str | None = None
    injection_lock = asyncio.Lock()
    input_runner = None  # aiohttp AppRunner | None
    conv_listener: ConversationListener | None = None
    cancelled = False
    # Set by _prepare (the driver runs it after the workdir wipe, before the
    # optio.log tail); read by the body and the teardown finally.
    claude_path: str | None = None
    ttyd_path: str | None = None
    claustrum_path: str | None = None
    claustrum_newer: str | None = None
    resuming = False
    # `pass_continue` decides whether claude is launched with --continue.
    # It is NOT the same as `resuming`: a restored snapshot with no
    # transcript must launch WITHOUT --continue (D3).
    pass_continue = False
    cred_baseline: str | None = None
    cred_watch_task: "asyncio.Task | None" = None
    resolved_seed_id: str | None = None
    lease_holder: str | None = None

    await host.connect()

    # Crash-orphan rescue: if a non-graceful host death left this task's
    # tmux/ttyd/claude tree running with unsaved state, harvest it into a fresh
    # snapshot and kill it BEFORE the driver wipes the workdir. No-op otherwise.
    await _rescue_orphan_if_present(ctx, config=config, host=host)

    async def _prepare(host: Host, hook_ctx: HookContext) -> None:
        """Install the claude+ttyd runtime and restore a resume snapshot.

        Handed to run_log_protocol_session, which runs it AFTER
        host.setup_workdir() has wiped the workdir and BEFORE it subscribes
        the optio.log tail. That ordering is exactly why install + restore
        belong here: the runtime planted now survives to launch (the old
        double-wipe nuked it), and a restored optio.log is rotated away below
        before the tail can replay its stale DONE/ERROR.
        """
        nonlocal claude_path, ttyd_path, resuming, pass_continue
        nonlocal claustrum_path, claustrum_newer
        claude_path = await host_actions.ensure_claude_installed(
            hook_ctx,
            install_if_missing=config.install_if_missing,
            install_dir=config.claude_install_dir,
        )
        # ttyd serves the TUI in iframe mode only; conversation mode is headless
        # (no tmux/ttyd), so skip the install check/download entirely there.
        if config.mode == "iframe":
            ttyd_path = await host_actions.ensure_ttyd_installed(
                hook_ctx,
                install_if_missing=config.install_ttyd_if_missing,
                install_dir=config.ttyd_install_dir,
            )

        claustrum_path = None
        claustrum_newer = None
        if config.fs_isolation:
            claustrum_path = await host_actions.ensure_claustrum_installed(
                hook_ctx, install_dir=config.claude_install_dir,
            )
            claustrum_newer = await host_actions.claustrum_newer_tag()

        resume_requested = bool(getattr(ctx, "resume", False))
        snapshot: dict | None = None
        if resume_requested:
            snapshot = await load_latest_snapshot(
                ctx._db, prefix=ctx._prefix, process_id=ctx.process_id,
            )

        resuming = snapshot is not None
        if resuming:
            if config.session_restore_from is not None:
                _LOG.info(
                    "optio resume in progress; session_restore_from is skipped "
                    "(restore directives apply to fresh runs only)",
                )
            if config.on_seed_saved is not None:
                _LOG.warning(
                    "resume: on_seed_saved ignored (no full capture on resume); "
                    "seed_id is still used to overlay current credentials",
                )
            # Plaintext workdir first (establishes the tree incl. home/), then
            # decrypt + extract home/.claude on top. Decrypt failure is treated
            # as tampering/key-rotation and propagated — never silent
            # fresh-start (the decrypt call is intentionally outside any
            # except, so it surfaces straight to the caller).
            await host.restore_workdir(_stream_blob(ctx, snapshot["workdirBlobId"]))
            # restore_workdir empties + repopulates the workdir, wiping the claude
            # runtime set up above (home/.local/share/claude/versions symlink +
            # bin/claude — they live IN the workdir now, unlike the old real-home
            # install). Re-establish it on the restored tree so launch finds claude.
            # Idempotent: cache hit → just relinks; no reinstall.
            await host_actions.ensure_claude_installed(
                hook_ctx,
                install_if_missing=config.install_if_missing,
                install_dir=config.claude_install_dir,
                progress_label="Restoring Claude Code runtime…",
            )
            payload = await _read_blob_bytes(ctx, snapshot["sessionBlobId"])
            decrypt = config.session_blob_decrypt or (lambda b: b)
            plain = decrypt(payload)
            await _extract_home_claude(host, plain)
            # CLAUDE_CONFIG_DIR relocated `.claude.json` from the HOME root into
            # `.claude/`. A snapshot captured before that change restores it at
            # the old root path, where claude (driven by CLAUDE_CONFIG_DIR) never
            # looks -> folder-trust prompt -> bypassPermissions can't suppress ->
            # the session hangs. Apply the SAME old->new relocation the seed
            # consume path uses, so a resumed old-layout snapshot is normalized.
            await _rekey_claude_json_projects(host)
            await _rotate_optio_log(host)
            pass_continue = await _has_transcript(host)
            if not pass_continue:
                _LOG.warning(
                    "resume: restored snapshot has no transcript; launching "
                    "without --continue (D3 safety)",
                )
        elif config.session_restore_from is not None:
            # Explicit session restore (fresh runs only): fetch, decrypt,
            # rekey to this workdir (and truncate when requested), plant.
            payload = await _read_blob_bytes(ctx, config.session_restore_from)
            decrypt = config.session_blob_decrypt or (lambda b: b)
            plain = decrypt(payload)
            plain = rebase_session_blob(
                plain,
                new_workdir=host.workdir,
                until_uuid=config.session_restore_until,
            )
            await _extract_home_claude(host, plain)
            # rebase_session_blob rekeyed the transcript DIR slug, but not the
            # .claude.json `projects` keys — they still name the original
            # session's workdir. Under CLAUDE_CONFIG_DIR claude reads
            # .claude/.claude.json; an un-rekeyed projects map leaves the new
            # workdir untrusted -> folder-trust prompt (which bypassPermissions
            # cannot suppress) -> the session hangs. Collapse projects to the
            # launch workdir with trust, exactly as the optio-resume path does.
            await _rekey_claude_json_projects(host)
            pass_continue = await _has_transcript(host)
            if not pass_continue:
                raise RuntimeError(
                    "session_restore_from: restored blob contains no transcript"
                )

    async def _claudecode_body(host: Host, hook_ctx: HookContext) -> None:
        nonlocal launched_handle, tmux_path, tmux_socket, tmux_session, input_runner
        nonlocal cred_baseline, cred_watch_task
        nonlocal resolved_seed_id, lease_holder

        if callable(config.seed_id):
            resolved_seed_id = await config.seed_id(ctx.process_id)  # may raise SeedUnavailableError
            lease_holder = ctx.process_id  # provider holds the lease under this holder
        else:
            resolved_seed_id = config.seed_id

        cred_baseline, focus_env = await _plant_session_content(
            ctx, host, hook_ctx, config, protocol,
            resuming=resuming, resolved_seed_id=resolved_seed_id,
        )

        # Network binding (same env handling as opencode for multi-container deploys)
        bind_addr = os.environ.get("OPTIO_WIDGET_TUNNEL_BIND", "127.0.0.1")
        upstream_host = os.environ.get("OPTIO_WIDGET_TUNNEL_HOST", "127.0.0.1")
        ttyd_iface = bind_addr if isinstance(host, LocalHost) else "127.0.0.1"

        if config.fs_isolation and claustrum_newer and config.on_deliverable is not None:
            rel = f"{config.delivery_type}/claustrum-update-{claustrum_newer}.md"
            text = (
                f"A newer claustrum release ({claustrum_newer}) is available; "
                f"the pinned version is {host_actions._CLAUSTRUM_PINNED_TAG}. "
                f"Audit it and consider bumping the pin."
            )
            await host.write_text(f"deliverables/{rel}", text)
            try:
                await config.on_deliverable(hook_ctx, rel, text)
            finally:
                # Clean slate for the real agent: remove the notice file. (No
                # optio.log "Deliverable:" line is written — this is a direct
                # callback invocation, not the tail loop.)
                await host.run_command(
                    f"rm -f {__import__('shlex').quote(host.workdir.rstrip('/') + '/deliverables/' + rel)}"
                )

        claude_flags = host_actions.build_claude_flags(
            permission_mode=config.permission_mode,
            allowed_tools=config.allowed_tools,
            disallowed_tools=config.disallowed_tools,
            model=config.model,
            resuming=pass_continue,
        )
        # auto_start: append the kickoff prompt ONLY on a genuine fresh launch.
        # Gated on `resuming` (not pass_continue): a no-transcript resume still
        # drops --continue (D3) but must NOT re-issue the kickoff, which would
        # restart the task instead of leaving the restored session as-is.
        claude_flags = [
            *claude_flags,
            *host_actions.build_auto_start_args(
                auto_start=config.auto_start, resuming=resuming,
            ),
        ]
        # Resume notification: a System:-prefixed positional appended after
        # --continue, so the restored conversation gets a "you have been
        # resumed" turn. Gated on pass_continue (a transcript is actually
        # being continued); a no-transcript resume sends nothing.
        claude_flags = [
            *claude_flags,
            *host_actions.build_resume_notice_args(
                resuming=resuming, pass_continue=pass_continue,
            ),
        ]
        launch_env = {
            **(config.env or {}),
            **focus_env,
            **(hook_ctx.browser_launch_env or {}),
        }
        ctx.report_progress(None, "Launching Claude Code…")
        claustrum_wrap = await _build_claustrum_wrap(host, config, claustrum_path)
        handle, ttyd_port, tmux_socket, tmux_session = await host_actions.launch_ttyd_with_claude(
            host,
            ttyd_path=ttyd_path,
            claude_path=claude_path,
            bind_iface=ttyd_iface,
            extra_env=launch_env,
            claude_flags=claude_flags,
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
        # In-session input listener: receives human messages from the
        # iframe-input widget via the API widget-control proxy and injects
        # them under the same lock as system messages (no garbling).
        async def _inject_raw(text: str) -> None:
            await host_actions.send_text_to_claude(
                host, tmux_path, tmux_socket, tmux_session, text,
            )
        async def _inject_key(key: str) -> None:
            await host_actions.send_key_to_claude(
                host, tmux_path, tmux_socket, tmux_session, key,
            )
        input_runner, input_port = await start_input_listener(
            bind_iface=ttyd_iface,
            on_input=serialized(injection_lock, _inject_raw),
            on_key=serialized(injection_lock, _inject_key),
        )
        await ctx.set_control_upstream(f"http://{upstream_host}:{input_port}")
        ctx.report_progress(None, "Claude Code is live")

        # Await the claude process inside tmux (NOT the ttyd connection). ttyd
        # stays up serving viewers; the task is alive while the tmux session is.
        # The protocol driver cancels this body when it sees DONE/ERROR in
        # optio.log; if claude exits some other way, has-session goes false and
        # the body returns -> driver treats it as premature exit.
        if resolved_seed_id is not None:
            cred_watch_task = asyncio.create_task(cred_watcher.run_credential_watcher(
                ctx, host,
                seed_id=resolved_seed_id,
                baseline=cred_baseline,
                encrypt=config.session_blob_encrypt,
                decrypt=config.session_blob_decrypt,
                lease_holder=lease_holder,
            ))

        while ctx.should_continue() and await host_actions.tmux_session_alive(
            host, tmux_path, tmux_socket, tmux_session,
        ):
            await asyncio.sleep(1.0)

        if cred_watch_task is not None:
            cred_watch_task.cancel()
            try:
                await cred_watch_task
            except asyncio.CancelledError:
                pass
            cred_watch_task = None

    conversation: ClaudeCodeConversation | None = None
    if config.mode == "conversation":
        conversation = ClaudeCodeConversation(permission_gate=config.permission_gate)

    async def _conversation_body(host: Host, hook_ctx: HookContext) -> None:
        nonlocal launched_handle, cred_baseline, cred_watch_task
        nonlocal resolved_seed_id, lease_holder

        if callable(config.seed_id):
            resolved_seed_id = await config.seed_id(ctx.process_id)  # may raise SeedUnavailableError
            lease_holder = ctx.process_id  # provider holds the lease under this holder
        else:
            resolved_seed_id = config.seed_id

        cred_baseline_out, focus_env = await _plant_session_content(
            ctx, host, hook_ctx, config, protocol,
            resuming=resuming, resolved_seed_id=resolved_seed_id,
        )
        cred_baseline = cred_baseline_out

        current_model = config.model

        async def _spawn(model: str | None, *, do_continue: bool):
            claude_flags = host_actions.build_claude_flags(
                permission_mode=config.permission_mode,
                allowed_tools=config.allowed_tools,
                disallowed_tools=config.disallowed_tools,
                model=model,
                resuming=do_continue,
            )
            argv = host_actions.build_conversation_argv(
                claude_path, claude_flags=claude_flags,
                permission_gate=config.permission_gate,
                include_partial_messages=_partials_enabled(config),
                replay_user_messages=config.conversation_ui,
            )
            # Same claustrum fs-isolation wrap as the iframe path; claustrum
            # execve's claude, so the bidirectional stream-json pipes pass
            # through unchanged.
            wrap = await _build_claustrum_wrap(host, config, claustrum_path)
            if wrap:
                argv = [*wrap, *argv]
            cmd = " ".join(shlex.quote(a) for a in argv)
            handle = await host.launch_subprocess(
                cmd, env=env, cwd=host.workdir,
                env_remove=config.scrub_env, stdin=True,
            )
            conversation.attach(handle)
            reader = asyncio.create_task(conversation.run_reader())
            return handle, reader

        env = host_actions.conversation_launch_env(
            host.workdir,
            {**(config.env or {}), **focus_env, **(hook_ctx.browser_launch_env or {})},
        )
        ctx.report_progress(None, "Launching Claude Code (conversation)…")
        handle, reader_task = await _spawn(current_model, do_continue=pass_continue)
        launched_handle = handle

        ctx.publish_result(conversation)
        ctx.report_progress(None, "Claude Code conversation is live")

        nonlocal conv_listener
        if config.conversation_ui:
            listener_password = secrets.token_urlsafe(32)
            bind_addr = os.environ.get("OPTIO_WIDGET_TUNNEL_BIND", "127.0.0.1")
            upstream_host = os.environ.get("OPTIO_WIDGET_TUNNEL_HOST", "127.0.0.1")
            # On resume, re-prime the replay buffer from the snapshot-persisted
            # file so a viewer sees the prior conversation history, not just new
            # messages (Claude's own transcript is continued separately).
            initial_events = await _load_conversation_buffer(host) if resuming else None
            # conversation_ui occupies the single permission-handler slot (see design §2).
            uploads_dir = f"{host.workdir}/uploads"

            async def _write_upload(name: str, data: bytes) -> str:
                safe = re.sub(r"[^A-Za-z0-9._-]", "_", (name.split("/")[-1] or "file"))[:200] or "file"
                await host.put_file_to_host(data, f"{uploads_dir}/{safe}")
                return f"uploads/{safe}"

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
                initial_events=initial_events,
                upload_writer=_write_upload,
                max_upload_bytes=config.max_upload_bytes,
                download_reader=_read_download,
                max_download_bytes=config.max_download_bytes,
            )
            listener_port = await conv_listener.start(bind_addr)
            await ctx.set_widget_upstream(
                f"http://{upstream_host}:{listener_port}",
                inner_auth=BasicAuth(username="optio", password=listener_password),
            )
            model_list = await cc_models.fetch_available_models(host, home_dir=f"{host.workdir}/home")
            await ctx.set_widget_data({
                "protocol": "claudecode",
                "toolVerbosity": config.tool_verbosity,
                "showModelSelector": config.show_model_selector,
                "models": model_list["models"],
                "currentModel": current_model,
                "showFileUpload": config.show_file_upload,
                "maxUploadBytes": config.max_upload_bytes,
                "fileDownload": config.file_download,
                "maxDownloadBytes": config.max_download_bytes,
            })
            ctx.report_progress(None, "Conversation UI is live")

        # Kickoff / resume notice as first stdin messages (print mode with
        # --input-format stream-json takes no positional prompt). The resume
        # notice uses the System: convention, which is only taught to the agent
        # in the host-protocol prompt block — so suppress it when host_protocol
        # is off (the agent still detects resumes via resume.log).
        if config.auto_start and not resuming:
            await conversation.send(host_actions.AUTO_START_PROMPT)
        elif resuming and pass_continue and config.host_protocol:
            await conversation.send(f"{SYSTEM_MESSAGE_PREFIX}{RESUME_NOTICE}")

        if resolved_seed_id is not None:
            cred_watch_task = asyncio.create_task(cred_watcher.run_credential_watcher(
                ctx, host,
                seed_id=resolved_seed_id,
                baseline=cred_baseline,
                encrypt=config.session_blob_encrypt,
                decrypt=config.session_blob_decrypt,
                lease_holder=lease_holder,
            ))

        try:
            while True:
                # proc_wait handles both pid_like variants (asyncio subprocess
                # on LocalHost, asyncssh process on RemoteHost) and yields the
                # exit code.
                wait_task = asyncio.create_task(proc_wait(handle))
                close_task = asyncio.create_task(conversation.close_requested.wait())
                model_task = asyncio.create_task(conversation.model_change_requested.wait())
                done, _ = await asyncio.wait(
                    {wait_task, close_task, model_task},
                    return_when=asyncio.FIRST_COMPLETED,
                )
                for t in (wait_task, close_task, model_task):
                    if t not in done:
                        t.cancel()

                if model_task in done and close_task not in done and wait_task not in done:
                    # --- model swap: relaunch in place, keep the task alive ---
                    new_model = conversation.requested_model or current_model
                    conversation.model_change_requested.clear()
                    ctx.report_progress(None, f"Switching model to {new_model}…")
                    # Tell the conversation this process EOF is a restart, not a
                    # close — keeps the widget live (no x-optio-closed / grayed
                    # input) and the conversation object open across the swap.
                    conversation.begin_restart()
                    # Graceful kill (not aggressive) so claude flushes its
                    # transcript before --continue resumes it on the new model.
                    await host.terminate_subprocess(handle, aggressive=False)
                    reader_task.cancel()
                    try:
                        await reader_task
                    except asyncio.CancelledError:
                        pass
                    current_model = new_model
                    handle, reader_task = await _spawn(current_model, do_continue=True)
                    launched_handle = handle
                    ctx.report_progress(None, f"Claude Code resumed on {new_model}")
                    continue

                if close_task in done and wait_task not in done:
                    # Caller asked to close: cooperative shutdown, clean end.
                    if config.host_protocol:
                        # The keyword driver treats a body return without DONE as
                        # a premature exit. A caller-requested close IS the clean
                        # end of the session, so emit the DONE ourselves (same
                        # harness-side convention as the tmux wrapper's exit echo)
                        # and park until the driver observes it and cancels us.
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
                    raise RuntimeError(
                        f"claude exited unexpectedly (exit {rc})"
                    )
                break
        finally:
            if cred_watch_task is not None:
                cred_watch_task.cancel()
                try:
                    await cred_watch_task
                except asyncio.CancelledError:
                    pass
                cred_watch_task = None
            reader_task.cancel()
            try:
                await reader_task
            except asyncio.CancelledError:
                pass

    async def _agent_sender(message: str) -> None:
        if config.mode == "conversation":
            await conversation.send(message)
            return
        # Serialized against the human-input listener via injection_lock so a
        # system message can never interleave with a human burst.
        async with injection_lock:
            await host_actions.send_text_to_claude(
                host, tmux_path, tmux_socket, tmux_session, message,
            )

    body = _conversation_body if config.mode == "conversation" else _claudecode_body
    try:
        await run_log_protocol_session(
            host, ctx,
            body=body,
            prepare=_prepare,
            on_deliverable=config.on_deliverable,
            after_execute=config.after_execute,
            protocol=protocol,
            browser_url_rewrite=rewrite_oauth_redirect,
            agent_sender=_agent_sender,
            keywords=config.host_protocol,
        )
    except _SessionFailed as fail:
        raise RuntimeError(str(fail)) from None
    finally:
        if not ctx.should_continue():
            cancelled = True
        _trace("finally: ENTER cancelled=%s resuming=%s", cancelled, resuming)
        if (
            launched_handle is not None
            and tmux_path is not None
            and tmux_socket is not None
            and tmux_session is not None
            and claude_path
        ):
            _trace("finally: teardown_session_tree START aggressive=%s", cancelled)
            try:
                await host_actions.teardown_session_tree(
                    host,
                    tmux_path=tmux_path,
                    tmux_socket=tmux_socket,
                    tmux_session=tmux_session,
                    claude_path=claude_path,
                    ttyd_handle=launched_handle,
                    aggressive=cancelled,
                )
            except Exception:
                _LOG.exception("teardown_session_tree failed")
            _trace("finally: teardown_session_tree DONE")

        # Conversation mode has no tmux/ttyd tree (the gate above never fires):
        # kill the pipe-bound claude directly, then wait for quiescence so the
        # snapshot capture below tars a static home/.claude.
        if config.mode == "conversation" and launched_handle is not None:
            _trace("finally: terminate_subprocess (conversation) START aggressive=%s", cancelled)
            try:
                await host.terminate_subprocess(launched_handle, aggressive=cancelled)
            except Exception:
                _LOG.exception("terminate_subprocess (conversation) failed")
            try:
                await host_actions.await_claude_gone(host, claude_path or "claude")
            except Exception:
                _LOG.exception("await_claude_gone failed; proceeding")
            _trace("finally: terminate_subprocess (conversation) DONE")

        if input_runner is not None:
            try:
                await input_runner.cleanup()
            except Exception:
                _LOG.exception("input listener cleanup failed")
            try:
                await ctx.clear_control_upstream()
            except Exception:
                _LOG.exception("clear control upstream failed")

        if conv_listener is not None:
            # Persist the replay buffer into the workdir BEFORE stop() so it
            # lands in the resume snapshot; the next launch re-primes from it.
            if config.supports_resume:
                try:
                    await host.write_text(
                        _CONV_BUFFER_FILE, json.dumps(conv_listener.export_buffer()),
                    )
                except Exception:
                    _LOG.exception("conversation buffer persist failed")
            try:
                await conv_listener.stop()
            except Exception:
                _LOG.exception("conversation listener cleanup failed")

        if cred_watch_task is not None:
            cred_watch_task.cancel()
            try:
                await cred_watch_task
            except asyncio.CancelledError:
                pass

        if lease_holder is not None and resolved_seed_id is not None:
            try:
                await _seeds.release(
                    ctx._db, prefix=ctx._prefix, suffix=CLAUDE_SEED_SUFFIX,
                    seed_id=resolved_seed_id, holder=lease_holder,
                )
            except Exception:
                _LOG.exception("lease release failed (TTL will reclaim)")

        if resolved_seed_id is not None:
            try:
                await cred_watcher.save_back_if_changed(
                    ctx, host,
                    seed_id=resolved_seed_id,
                    baseline=cred_baseline,
                    encrypt=config.session_blob_encrypt,
                    decrypt=config.session_blob_decrypt,
                )
            except Exception:
                _LOG.exception("final credential save-back failed")

        if not resuming and config.on_seed_saved is not None:
            # Credentials-present guard: never store a seed whose home/.claude
            # has no valid credentials (a login-less or aborted setup session).
            # Without a usable refresh token the seed is dead on arrival —
            # account resolves to None and it only pollutes the pool. Mirrors the
            # save-back (cred_fingerprint) and snapshot credentials guards.
            if await cred_watcher.cred_fingerprint(host) is None:
                _LOG.warning(
                    "seed capture skipped: no valid credentials in "
                    "home/.claude/.credentials.json (login-less session)",
                )
                _trace("finally: capture_seed SKIPPED (no credentials)")
            else:
                _trace("finally: capture_seed START")
                try:
                    seed_id = await _seeds.capture_seed(
                        ctx, host,
                        manifest=CLAUDE_SEED_MANIFEST,
                        suffix=CLAUDE_SEED_SUFFIX,
                        encrypt=config.session_blob_encrypt,
                    )
                    _trace("finally: capture_seed DONE id=%s", seed_id)
                    # 2nd arg: best-effort account summary from the seeded OAuth
                    # token (the isolated home creds are still on disk pre-cleanup).
                    # Returns None on any failure; never disturbs capture.
                    summary = await resolve_account_summary(host)
                    await _call_maybe_async(config.on_seed_saved, seed_id, summary)
                    _trace("finally: on_seed_saved fired (summary=%s)", summary)
                except Exception:
                    _LOG.exception(
                        "seed capture failed; callback not fired, teardown continues",
                    )
                    _trace("finally: capture_seed RAISED")

        # Explicit session capture (on_session_saved): runs BEFORE snapshot
        # capture, whose workdir-tar step defensively wipes home/.claude.
        # Same reached-live gate as snapshots; unlike snapshots there is no
        # credentials guard — the caller owns blob semantics and lifecycle.
        if config.on_session_saved is not None and launched_handle is not None:
            _trace("finally: session blob capture START")
            try:
                _end_state = (
                    "cancelled" if cancelled
                    else "failed" if sys.exc_info()[0] is not None
                    else "done"
                )
                _session_blob_id = await _store_session_blob(
                    ctx, host,
                    session_blob_encrypt=config.session_blob_encrypt,
                )
                await _call_maybe_async(
                    config.on_session_saved, _session_blob_id, _end_state,
                )
                _trace(
                    "finally: session blob capture DONE id=%s state=%s",
                    _session_blob_id, _end_state,
                )
            except Exception:
                _LOG.exception(
                    "session blob capture failed; callback not fired, "
                    "teardown continues",
                )

        # Reached-live gate: only capture if claude actually came up.
        # launched_handle is assigned strictly AFTER merge_seed and a
        # successful ttyd/claude launch, so non-None ⟹ the environment was
        # fully planted+seeded and claude live. An interrupt or merge_seed
        # failure before launch leaves it None — skip capture entirely (do
        # NOT touch hasSavedState, so any prior good snapshot survives).
        if config.supports_resume and launched_handle is not None:
            _trace("finally: capture_snapshot START")
            try:
                await _capture_snapshot(
                    ctx, host,
                    end_state="cancelled" if cancelled else "done",
                    workdir_exclude=config.workdir_exclude,
                    session_blob_encrypt=config.session_blob_encrypt,
                )
                _trace("finally: capture_snapshot DONE")
            except Exception:
                _LOG.exception(
                    "snapshot capture failed; proceeding with workdir wipe",
                )
                _trace("finally: capture_snapshot RAISED")

        _trace("finally: cleanup_taskdir START aggressive=%s", cancelled)
        try:
            await host.cleanup_taskdir(aggressive=cancelled)
        except Exception:
            _LOG.exception("cleanup_taskdir failed")
        _trace("finally: cleanup_taskdir DONE")
        _trace("finally: disconnect START")
        try:
            await host.disconnect()
        except Exception:
            _LOG.exception("host.disconnect failed")
        _trace("finally: disconnect DONE")


# --- helpers ---------------------------------------------------------------


# Workdir-relative file the conversation listener's replay buffer is persisted
# to at teardown (captured in the resume snapshot, re-primed on resume).
_CONV_BUFFER_FILE = ".optio-conversation-buffer.json"


async def _load_conversation_buffer(host: Host) -> "list[tuple[int, dict]] | None":
    """Read the persisted conversation replay buffer from the restored workdir.

    Returns a list of ``(seq, event)`` or None when absent/unreadable (a fresh
    start, or a malformed file — never fatal, the buffer just starts empty).
    """
    path = f"{host.workdir.rstrip('/')}/{_CONV_BUFFER_FILE}"
    try:
        raw = await host.fetch_bytes_from_host(path)
    except FileNotFoundError:
        return None
    except Exception:  # noqa: BLE001
        _LOG.exception("conversation buffer read failed; starting empty")
        return None
    try:
        data = json.loads(raw.decode("utf-8"))
    except (ValueError, UnicodeDecodeError):
        _LOG.warning("conversation buffer file is not valid JSON; starting empty")
        return None
    out: list[tuple[int, dict]] = []
    for entry in data if isinstance(data, list) else []:
        if isinstance(entry, list) and len(entry) == 2 and isinstance(entry[1], dict):
            # Defensive: drop any terminal close marker a pre-fix buffer may
            # carry, so it never replays the prior run's close on resume.
            if entry[1].get("type") == "x-optio-closed":
                continue
            out.append((entry[0], entry[1]))
    return out or None


async def _plant_session_content(
    ctx: ProcessContext, host: Host, hook_ctx: HookContext,
    config: ClaudeCodeTaskConfig, protocol, *,
    resuming: bool, resolved_seed_id: str | None,
) -> "tuple[str | None, dict[str, str]]":
    """Fresh/resume content planting shared by both modes.

    Returns ``(cred_baseline, focus_env)``. Mirrors the pre-existing inline
    logic exactly for the iframe path; conversation mode reuses it unchanged
    except for the prompt-composition kwargs (mode-aware).
    """
    # focus_mode: layer the quiet-TUI knobs (focus view + fullscreen) onto
    # the planted settings, plus the no-flicker launch env. Off → passthrough.
    effective_claude_config, focus_env = host_actions.build_focus_mode(
        focus_mode=config.focus_mode, claude_config=config.claude_config,
    )
    cred_baseline: str | None = None
    refreshed_files: list[str] = []
    instructions = config.consumer_instructions
    omit_task_framing = False
    if config.mode == "conversation" and not instructions:
        instructions = DEFAULT_CONVERSATION_INSTRUCTIONS
        omit_task_framing = True
    if not resuming:
        # Fresh start: protocol driver has created workdir,
        # deliverables/, and an empty optio.log. Plant per-task HOME
        # files and CLAUDE.md before launching claude.
        await host_actions.plant_home_files(
            host,
            credentials_json=config.credentials_json,
            claude_config=effective_claude_config,
        )
        if resolved_seed_id is not None:
            # Seeded fresh: overlay the stored environment on top of
            # any consumer-planted creds/config (seed wins), then
            # rekey .claude.json projects to the new cwd. Begins a NEW
            # conversation — no --continue.
            _trace("body: merge_seed START id=%s", resolved_seed_id)
            await _seeds.merge_seed(
                ctx, host,
                seed_id=resolved_seed_id,
                manifest=CLAUDE_SEED_MANIFEST,
                suffix=CLAUDE_SEED_SUFFIX,
                decrypt=config.session_blob_decrypt,
            )
            _trace("body: merge_seed DONE")
            cred_baseline = await cred_watcher.cred_fingerprint(host)
        await host.write_text(
            "CLAUDE.md",
            compose_agents_md(
                instructions,
                documentation=protocol.documentation if config.host_protocol else None,
                workdir_exclude=config.workdir_exclude,
                supports_resume=config.supports_resume,
                host_protocol=config.host_protocol,
                omit_task_framing=omit_task_framing,
                fs_isolation_dirs=_fs_isolation_dirs(config, host),
                file_download=config.file_download,
            ),
        )
    else:
        # Resume: home/.claude (credentials, settings) was restored from
        # the session blob. Overlay the seed's CURRENT credentials on top
        # (the seed is the source of truth for creds; the snapshot may carry
        # a now-rotated/dead token). Non-credential home files are untouched.
        if resolved_seed_id is not None:
            await _seeds.merge_seed(
                ctx, host,
                seed_id=resolved_seed_id,
                manifest=CLAUDE_CRED_MANIFEST,
                suffix=CLAUDE_SEED_SUFFIX,
                decrypt=config.session_blob_decrypt,
            )
        cred_baseline = await cred_watcher.cred_fingerprint(host)
        refreshed_files = await _maybe_refresh_on_resume(host, hook_ctx, config)
    if config.supports_resume:
        await _append_resume_log_entry(host, refreshed=refreshed_files)
    if config.before_execute is not None:
        await config.before_execute(hook_ctx)
    return cred_baseline, focus_env


async def _stream_blob(ctx: ProcessContext, blob_id) -> "AsyncIterator[bytes]":
    async with ctx.load_blob(blob_id) as reader:
        while True:
            chunk = await reader.read(1 << 20)
            if not chunk:
                break
            yield chunk


async def _read_blob_bytes(ctx: ProcessContext, blob_id) -> bytes:
    out = bytearray()
    async with ctx.load_blob(blob_id) as reader:
        while True:
            chunk = await reader.read(1 << 20)
            if not chunk:
                break
            out.extend(chunk)
    return bytes(out)


async def _call_maybe_async(fn, *args) -> None:
    """Invoke a callback that may be sync or async."""
    result = fn(*args)
    if inspect.isawaitable(result):
        await result


async def _has_transcript(host: Host) -> bool:
    """True if the restored snapshot carries a claude transcript.

    D3 safety: claude exits at startup if `--continue` is passed with no
    session to continue. Detect by looking for any `*.jsonl` under
    home/.claude/projects/.
    """
    workdir = host.workdir.rstrip("/")
    projects = f"{workdir}/home/.claude/projects"
    r = await host.run_command(
        f"find {shlex.quote(projects)} -name '*.jsonl' -print -quit 2>/dev/null || true"
    )
    return bool(r.stdout.strip())


async def _archive_home_claude(host: Host) -> bytes:
    """tar.gz the sensitive ``home/.claude`` subtree and fetch it as bytes."""
    workdir = host.workdir.rstrip("/")
    tmpfile = f"{workdir}/.optio-claudecode-session.tar.gz"
    _trace("archive_home: tar run_command START")
    r = await host.run_command(
        f"tar -czf {shlex.quote(tmpfile)} -C {shlex.quote(workdir)} home/.claude"
    )
    _trace("archive_home: tar run_command DONE exit=%d", r.exit_code)
    if r.exit_code != 0:
        raise RuntimeError(
            f"tar home/.claude failed (exit {r.exit_code}): "
            f"{r.stderr.strip()[:200]}"
        )
    try:
        _trace("archive_home: fetch_bytes START")
        out = await host.fetch_bytes_from_host(tmpfile)
        _trace("archive_home: fetch_bytes DONE bytes=%d", len(out))
        return out
    finally:
        await host.run_command(f"rm -f {shlex.quote(tmpfile)}")


async def _extract_home_claude(host: Host, plain: bytes) -> None:
    """Extract the decrypted ``home/.claude`` tar over the workdir."""
    workdir = host.workdir.rstrip("/")
    tmpfile = f"{workdir}/.optio-claudecode-restore.tar.gz"
    await host.put_file_to_host(plain, tmpfile)
    try:
        r = await host.run_command(
            f"tar -xzf {shlex.quote(tmpfile)} -C {shlex.quote(workdir)}"
        )
        if r.exit_code != 0:
            raise RuntimeError(
                f"tar -x home/.claude failed (exit {r.exit_code}): "
                f"{r.stderr.strip()[:200]}"
            )
    finally:
        await host.run_command(f"rm -f {shlex.quote(tmpfile)}")


_RESCUE_MARKER = ".optio-rescue-pending"


def _claude_bin_path(host: "Host") -> str:
    """Deterministic launch path of claude inside the isolated HOME."""
    return f"{host.workdir.rstrip('/')}/home/.local/bin/claude"


async def _marker_present(host: "Host", marker_path: str) -> bool:
    r = await host.run_command(
        f"test -e {shlex.quote(marker_path)} && echo YES || true"
    )
    return "YES" in r.stdout


async def _rescue_orphan_if_present(
    ctx: ProcessContext, host: Host, config: ClaudeCodeTaskConfig,
) -> None:
    """Before the driver wipes the workdir, recover a crash-surviving orphan.

    A non-graceful host death (disk-full, OOM, power loss) leaves the
    tmux/ttyd/claude sub-tree running, re-parented to init, with unsaved state
    on disk — but no snapshot. This bracket, run before
    ``run_log_protocol_session`` (hence before ``setup_workdir``), detects that
    orphan on the deterministic per-task socket, kills it, and captures its
    live state into a fresh snapshot that the unchanged resume path then
    restores. No-op unless an orphan (or a leftover rescue marker) is found.

    Kill-before-capture is deliberate: a dead, static workdir prevents a live
    claude from repopulating ``home/.claude`` into the plaintext workdir blob
    after the expunge, and yields a race-free tar. See spec decisions D3/D4."""
    if not getattr(config, "supports_resume", True):
        return
    if not bool(getattr(ctx, "resume", False)):
        return

    socket = host_actions._tmux_socket_path(host)
    session = "optio"
    marker_path = f"{host.workdir.rstrip('/')}/{_RESCUE_MARKER}"

    tmux_path = await host_actions._require_tmux(host)
    alive = await host_actions.tmux_session_alive(
        host, tmux_path, socket, session,
    )
    if not alive and not await _marker_present(host, marker_path):
        return  # normal resume; nothing to rescue

    _LOG.warning(
        "crash-orphan rescue: live=%s socket=%s — capturing live state before wipe",
        alive, socket,
    )

    # 1. Durable marker (retry guard: kill removes the has-session signal).
    await host.write_text(_RESCUE_MARKER, "")

    # 2. Kill the orphan tree (handle-less: orphan ttyd reaped by socket).
    claude_path = _claude_bin_path(host)
    await host_actions.teardown_session_tree(
        host,
        tmux_path=tmux_path,
        tmux_socket=socket,
        tmux_session=session,
        claude_path=claude_path,
        ttyd_handle=None,
        aggressive=True,
    )

    # 3. Capture the now-static workdir — identical artifacts to a normal
    #    teardown capture. Exclude the marker so a restored workdir cannot
    #    re-trigger rescue in a loop.
    exclude = [*(config.workdir_exclude or []), _RESCUE_MARKER]
    await _capture_snapshot(
        ctx, host,
        end_state="rescued",
        workdir_exclude=exclude,
        session_blob_encrypt=config.session_blob_encrypt,
    )

    # 4. Capture durable — clear the marker.
    await host.run_command(f"rm -f {shlex.quote(marker_path)}")
    _LOG.warning("crash-orphan rescue: fresh snapshot captured; orphan killed")


async def _store_session_blob(
    ctx: ProcessContext,
    host: Host,
    *,
    session_blob_encrypt: "Callable[[bytes], bytes] | None",
):
    """Tar home/.claude, encrypt, store as a standalone GridFS blob.

    Shared by snapshot capture and the explicit on_session_saved capture.
    Returns the GridFS file id.
    """
    session_bytes = await _archive_home_claude(host)
    encrypt = session_blob_encrypt or (lambda b: b)
    payload = encrypt(session_bytes)
    expected_len = len(payload)
    async with ctx.store_blob("session") as swriter:
        await swriter.write(payload)
        session_blob_id = swriter.file_id
        written = getattr(swriter, "_position", None)
        if written is not None and written != expected_len:
            raise RuntimeError(
                f"session blob short-write: expected {expected_len} bytes, "
                f"GridIn._position is {written}"
            )
    return session_blob_id


async def _capture_snapshot(
    ctx: ProcessContext,
    host: Host,
    *,
    end_state: str,
    workdir_exclude: list[str] | None,
    session_blob_encrypt: "Callable[[bytes], bytes] | None" = None,
) -> None:
    # 0. Credentials-present guard. Refuse to snapshot an unconfigured
    # environment: without home/.claude/.credentials.json a restored snapshot
    # drops the agent to /login (looking like a zero-config seed session).
    # This MUST be first — the resume path (load_latest_snapshot) ignores
    # hasSavedState, so the degenerate snapshot must never be CREATED, not
    # merely have its flag skipped.
    workdir = host.workdir.rstrip("/")
    chk = await host.run_command(
        f"test -s {shlex.quote(workdir)}/home/.claude/.credentials.json "
        f"&& echo OK || true"
    )
    if "OK" not in chk.stdout:
        _LOG.warning(
            "snapshot capture skipped: home/.claude/.credentials.json "
            "absent/empty; refusing to mark resumable",
        )
        return

    # 1-3. tar the sensitive subtree, encrypt, write the session blob.
    _trace("capture: store_session_blob START")
    session_blob_id = await _store_session_blob(
        ctx, host, session_blob_encrypt=session_blob_encrypt,
    )
    _trace("capture: store_session_blob DONE id=%s", session_blob_id)

    # 4. defensive wipe so the workdir tar cannot carry sensitive state.
    _trace("capture: rm -rf home/.claude START")
    await host.run_command(f"rm -rf {shlex.quote(workdir)}/home/.claude")
    _trace("capture: rm -rf home/.claude DONE")

    # 4b. Drop regenerable scratch that would bloat the workdir snapshot.
    # The claude binary is NOT here: home/.local/share/claude/versions is a
    # symlink to the shared optio cache, which os.walk does not follow and CLI
    # tar stores as a symlink, so it never enters the archive. mozilla
    # cache/profile are pure scratch.
    _trace("capture: rm -rf regenerable home dirs START")
    await host.run_command(
        "rm -rf "
        f"{shlex.quote(workdir)}/home/.cache/mozilla "
        f"{shlex.quote(workdir)}/home/.mozilla"
    )
    _trace("capture: rm -rf regenerable home dirs DONE")

    # 5. stream the plaintext workdir tar.
    _trace("capture: store_blob(workdir)+archive START")
    async with ctx.store_blob("workdir") as wwriter:
        async for chunk in host.archive_workdir(workdir_exclude):
            await wwriter.write(chunk)
        workdir_blob_id = wwriter.file_id
    _trace("capture: store_blob(workdir)+archive DONE id=%s", workdir_blob_id)

    # 6. insert the snapshot doc.
    _trace("capture: insert_snapshot START")
    await insert_snapshot(
        ctx._db,
        prefix=ctx._prefix,
        process_id=ctx.process_id,
        end_state=end_state,
        session_blob_id=session_blob_id,
        workdir_blob_id=workdir_blob_id,
        deliverables_emitted=[],
    )
    _trace("capture: insert_snapshot DONE")

    # 7. prune + delete stale blobs.
    pruned = await prune_snapshots(
        ctx._db, prefix=ctx._prefix, process_id=ctx.process_id,
    )
    for p in pruned:
        try:
            await ctx.delete_blob(p["sessionBlobId"])
        except Exception:
            _LOG.exception("delete_blob(session) failed")
        try:
            await ctx.delete_blob(p["workdirBlobId"])
        except Exception:
            _LOG.exception("delete_blob(workdir) failed")
    _trace("capture: prune DONE pruned=%d", len(pruned))

    # 8. surface the Resume affordance in the dashboard.
    await ctx.mark_has_saved_state()
    _trace("capture: mark_has_saved_state DONE")


async def _rotate_optio_log(host: Host) -> None:
    """Append the restored optio.log to optio.log.old, then truncate it.

    Copied verbatim from opencode. Preserves historical log content across
    consecutive resumes while ensuring the tail driver only sees fresh
    lines from the resumed run.
    """
    workdir = host.workdir.rstrip("/")
    log_abs = f"{workdir}/optio.log"
    old_abs = f"{workdir}/optio.log.old"
    try:
        current = (await host.fetch_bytes_from_host(log_abs)).decode("utf-8")
    except FileNotFoundError:
        current = ""
    if not current:
        await host.write_text("optio.log", "")
        return
    try:
        existing_old = (await host.fetch_bytes_from_host(old_abs)).decode("utf-8")
    except FileNotFoundError:
        existing_old = ""
    await host.write_text("optio.log.old", existing_old + current)
    await host.write_text("optio.log", "")


async def _append_resume_log_entry(
    host, *, refreshed: list[str] | None = None,
) -> None:
    """Append one line to ``<workdir>/resume.log``.

    Line format: ``<ISO 8601 UTC timestamp>[ REFRESHED:<comma-separated names>]``.
    Caller gates this on config.supports_resume.
    """
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    line = ts
    if refreshed:
        line = f"{ts} REFRESHED:{','.join(refreshed)}"
    target = f"{host.workdir}/resume.log"
    result = await host.run_command(
        f"echo {shlex.quote(line)} >> {shlex.quote(target)}"
    )
    if result.exit_code != 0:
        raise RuntimeError(
            f"failed to append to resume.log: exit {result.exit_code}: "
            f"{result.stderr!r}"
        )


async def _maybe_refresh_on_resume(
    host, hook_ctx, config: ClaudeCodeTaskConfig,
) -> list[str]:
    """Run on_resume_refresh (if any) and rewrite CLAUDE.md when changed.

    Returns the list of filenames rewritten (currently at most
    ``["CLAUDE.md"]``). A hook that raises is logged and ignored.
    """
    if config.on_resume_refresh is None:
        return []
    try:
        new_config = config.on_resume_refresh(config)
    except Exception:
        _LOG.exception(
            "on_resume_refresh raised; keeping existing CLAUDE.md from snapshot",
        )
        return []
    # Mode-aware prompt kwargs, recomputed from the refreshed config (same
    # defaulting as _plant_session_content's fresh-start composition).
    instructions = new_config.consumer_instructions
    omit_task_framing = False
    if new_config.mode == "conversation" and not instructions:
        instructions = DEFAULT_CONVERSATION_INSTRUCTIONS
        omit_task_framing = True
    new_claude_md = compose_agents_md(
        instructions,
        workdir_exclude=new_config.workdir_exclude,
        supports_resume=new_config.supports_resume,
        host_protocol=new_config.host_protocol,
        omit_task_framing=omit_task_framing,
        fs_isolation_dirs=_fs_isolation_dirs(new_config, hook_ctx._host),
        file_download=new_config.file_download,
    )
    try:
        existing = await hook_ctx.read_text_from_host("CLAUDE.md", silent=True)
    except FileNotFoundError:
        existing = None
    except Exception:
        _LOG.exception(
            "failed to read existing CLAUDE.md on resume; rewriting unconditionally",
        )
        existing = None
    if existing == new_claude_md:
        return []
    await host.write_text("CLAUDE.md", new_claude_md)
    return ["CLAUDE.md"]


def create_claudecode_task(
    process_id: str,
    name: str,
    config: ClaudeCodeTaskConfig,
    description: str | None = None,
    metadata: dict | None = None,
) -> TaskInstance:
    """Return a TaskInstance that runs one optio-claudecode session.

    ``metadata`` is the caller app's task-tagging payload (for later
    filter/select/identify); it is stamped onto the TaskInstance verbatim and
    never read by the task itself. Construction is the caller's concern — this
    factory only accepts and forwards it.
    """

    async def _execute(ctx: ProcessContext) -> None:
        await run_claudecode_session(ctx, config)

    return TaskInstance(
        execute=_execute,
        process_id=process_id,
        name=name,
        description=description,
        ui_widget=(
            "iframe-input" if config.mode == "iframe"
            else ("conversation" if config.conversation_ui else None)
        ),
        supports_resume=config.supports_resume,
        metadata=metadata or {},
    )
