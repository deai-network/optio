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
import dataclasses
import inspect
import json
import logging
import mimetypes
import os
import secrets
import shlex
import sys
import time as _time
from datetime import datetime, timezone
from typing import AsyncIterator, Callable

from bson import ObjectId

from optio_core.context import ProcessContext
from optio_core.models import BasicAuth, SessionCrypto, TaskInstance

from optio_agents.context import HookContext
from optio_agents.conversation import ConversationClosed
from optio_agents.fs_grants import fs_isolation_dirs
from optio_agents.mcp import resolve_mcp_servers
from optio_agents.protocol.session import _SessionFailed, run_log_protocol_session
from optio_host.host import (
    Host, LocalHost, ProcessHandle, RemoteHost, proc_wait, unwind_tracing,
)
from optio_host.archive import DEFAULT_WORKDIR_EXCLUDES
from optio_host.paths import task_dir
from optio_agents import seeds as _seeds
from optio_agents import RESUME_NOTICE, SYSTEM_MESSAGE_PREFIX, claustrum, get_protocol
from optio_claudecode import controls as cc_controls
from optio_agents.todos import TodoTracker
from optio_agents.uploads import materialize, upload_url_token

from optio_claudecode import cred_watcher
from optio_claudecode import host_actions
from optio_claudecode import models as cc_models
from optio_claudecode import picks as cc_picks
from optio_claudecode.info import AGENT_INFO
from optio_claudecode.conversation import ClaudeCodeConversation
from optio_claudecode.todos import (
    ClaudeTodoExtractor, todo_progress_from_transcript,
)
from optio_claudecode.conversation_listener import ConversationListener
from optio_claudecode.input_listener import serialized, start_input_listener
from optio_agents.account import EMPTY, accounts_to_metadata
from optio_claudecode.account import resolve_capture_account
from optio_claudecode.oauth_redirect import rewrite_oauth_redirect
from optio_claudecode.seed_manifest import (
    CLAUDE_CRED_MANIFEST,
    CLAUDE_SEED_MANIFEST,
    CLAUDE_SEED_SUFFIX,
    _rekey_claude_json_projects,
)
from optio_claudecode.pending_captures import (
    delete_pending_capture,
    discard_pending_conversation_blob,
    discard_pending_workdir_blob,
    load_pending_capture,
    record_pending_capture,
    settle_pending_capture,
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


class _traced:
    """Async context manager: START/DONE/RAISED span for one unwind step.

    Emits through the same ``_trace`` gate (OPTIO_CANCEL_TRACE) as the
    hand-written START/DONE calls elsewhere in this module, with elapsed
    monotonic time added on exit. ``.start``/``.elapsed`` stay populated
    (regardless of the trace gate) so a caller can reuse the same clock for
    an always-on milestone message (``ctx.report_progress``).

    Diagnostic only: never swallows an exception raised inside the
    ``async with`` block — it logs RAISED (with the exception type) and
    re-raises unchanged.
    """

    def __init__(self, label: str, *, clock: "Callable[[], float]" = _time.monotonic) -> None:
        self.label = label
        self._clock = clock
        self.start = 0.0
        self.elapsed = 0.0

    async def __aenter__(self) -> "_traced":
        self.start = self._clock()
        _trace("%s START", self.label)
        return self

    async def __aexit__(self, exc_type, exc, tb) -> bool:
        self.elapsed = self._clock() - self.start
        if exc_type is not None:
            _trace("%s RAISED %s (%.1fs)", self.label, exc_type.__name__, self.elapsed)
        else:
            _trace("%s DONE (%.1fs)", self.label, self.elapsed)
        return False


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


async def _build_claustrum_wrap(
    host: Host, config: ClaudeCodeTaskConfig, claustrum_path: str | None,
) -> list[str] | None:
    """claustrum argv prefix for an fs-isolated launch, or None when fs_isolation
    is off. Shared by the iframe and conversation launch paths."""
    if not config.fs_isolation:
        return None
    from optio_agents import fs_grants
    cache_dir = await host_actions._resolve_cache_dir(host, config.install_dir)
    # `~/` caller extras expand against the REAL host home (the claude process
    # runs under an isolated $HOME, and grants reach claustrum verbatim).
    host_home = (
        await host.resolve_host_home() if config.extra_allowed_dirs else None
    )
    grants = fs_grants.build_grant_flags(
        workdir=host.workdir,
        engine_cache_dir=cache_dir,
        extra_allowed_dirs=config.extra_allowed_dirs,
        host_home=host_home,
    )
    # Claude Code cannot reach /tmp under claustrum: it refuses its default
    # temp dir /tmp/claude-<uid> ("not readable") and exits 1, interactive and
    # -p alike (owner report 2026-10-08). Point CLAUDE_CODE_TMPDIR into the
    # task's own, granted workdir instead (claude creates it). Set inside the
    # wrap, after claustrum's `--`, so every confined launch gets it and an
    # unconfined one keeps the default.
    tmpdir = f"{host.workdir.rstrip('/')}/{_SANDBOX_TMPDIR}"
    return [
        *claustrum.build_claustrum_wrap(claustrum_path, grants),
        "env", f"CLAUDE_CODE_TMPDIR={tmpdir}",
    ]


# Claude Code's temp dir inside the workdir, for claustrum-confined launches.
_SANDBOX_TMPDIR = ".claude-tmp"


def _partials_enabled(config: ClaudeCodeTaskConfig) -> bool:
    """--include-partial-messages: standalone knob, or implied by
    conversation_ui (its live view is fed by partials)."""
    return config.include_partial_messages or config.conversation_ui


async def run_claudecode_session(
    ctx: ProcessContext, config: ClaudeCodeTaskConfig,
) -> None:
    """Execute function body for one optio-claudecode task instance."""
    host: Host = _build_host(config, ctx.process_id)
    protocol = get_protocol(
        browser="redirect",
        client_messages=config.use_client_messages,
        caller_messages=config.on_caller_message is not None,
    )
    launched_handle: ProcessHandle | None = None
    tmux_path: str | None = None
    tmux_socket: str | None = None
    tmux_session: str | None = None
    injection_lock = asyncio.Lock()
    input_runner = None  # aiohttp AppRunner | None
    conv_listener: ConversationListener | None = None
    todo_tracker: TodoTracker | None = None
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

    # A pending-capture record left by a cut-off capture must not outlive
    # this launch: a later Resurrect would take it for this run's capture.
    # An uncommitted capture's session blob is garbage from here on.
    await settle_pending_capture(ctx, discard_session_blob=True)

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
            install_dir=config.install_dir,
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
                hook_ctx, install_dir=config.install_dir,
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
            await _restore_workdir_snapshot(ctx, host, snapshot["workdirBlobId"])
            # restore_workdir empties + repopulates the workdir, wiping the claude
            # runtime set up above (home/.local/share/claude/versions symlink +
            # bin/claude — they live IN the workdir now, unlike the old real-home
            # install). Re-establish it on the restored tree so launch finds claude.
            # Idempotent: cache hit → just relinks; no reinstall.
            # check_update=False: the first ensure_claude_installed call above
            # already ran the upstream freshness probe (and any refresh); this
            # re-link pass must not repeat the network round-trip (mirrors
            # grok's resume flow).
            await host_actions.ensure_claude_installed(
                hook_ctx,
                install_if_missing=config.install_if_missing,
                install_dir=config.install_dir,
                progress_label=f"Restoring {AGENT_INFO.name} runtime…",
                check_update=False,
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
        mcp_config_path = await _plant_mcp_servers(ctx, host, config)

        # Network binding (same env handling as opencode for multi-container deploys)
        bind_addr = os.environ.get("OPTIO_WIDGET_TUNNEL_BIND", "127.0.0.1")
        upstream_host = os.environ.get("OPTIO_WIDGET_TUNNEL_HOST", "127.0.0.1")
        ttyd_iface = bind_addr if isinstance(host, LocalHost) else "127.0.0.1"

        if config.fs_isolation and claustrum_newer:
            await claustrum.emit_claustrum_update_notice(
                host, hook_ctx,
                delivery_type=config.delivery_type,
                on_deliverable=config.on_deliverable,
                newer=claustrum_newer,
                pinned=claustrum.CLAUSTRUM_PINNED_TAG,
            )

        claude_flags = host_actions.build_claude_flags(
            permission_mode=config.permission_mode,
            allowed_tools=config.allowed_tools,
            disallowed_tools=config.disallowed_tools,
            tools=config.tools,
            mcp_config_path=mcp_config_path,
            # A continued session runs an alias (default when none is
            # configured), not the full id its transcript ended on.
            model=cc_models.launch_model(config.model, continuing=pass_continue),
            resuming=pass_continue,
        )
        # The positional prompts (at most one of the two), after `--` so no
        # multi-value option before them takes them for its values.
        claude_flags = [
            *claude_flags,
            *host_actions.positional_prompt_args([
                # auto_start: the kickoff prompt ONLY on a genuine fresh
                # launch. Gated on `resuming` (not pass_continue): a
                # no-transcript resume still drops --continue (D3) but must
                # NOT re-issue the kickoff, which would restart the task
                # instead of leaving the restored session as-is.
                *host_actions.build_auto_start_args(
                    auto_start=config.auto_start, resuming=resuming,
                ),
                # Resume notification: a System:-prefixed positional with
                # --continue, so the restored conversation gets a "you have
                # been resumed" turn. Gated on pass_continue (a transcript is
                # actually being continued); a no-transcript resume sends
                # nothing.
                *host_actions.build_resume_notice_args(
                    resuming=resuming, pass_continue=pass_continue,
                ),
            ]),
        ]
        launch_env = {
            **(config.env or {}),
            **focus_env,
            **(hook_ctx.browser_launch_env or {}),
        }
        ctx.report_progress(None, f"Launching {AGENT_INFO.name}…")
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
        await _on_agent_live(ctx, config)

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
        ctx.report_progress(None, f"{AGENT_INFO.name} is live")

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
                encrypt=config.seed_encrypt,
                decrypt=config.seed_decrypt,
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
        nonlocal resolved_seed_id, lease_holder, todo_tracker

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
        # Resolved once per launch (_spawn); the model probe gets neither the
        # file nor its flags.
        mcp_config_path = await _plant_mcp_servers(ctx, host, config)

        # The task's custom controls: each control's default option replaces
        # the config's own model / reasoning effort / permission mode
        # (controls.custom_defaults). `base_*` is what a session runs while
        # nothing is picked; a resume's saved picks replace them in turn. The
        # models the options name are the task's own: launched and switched
        # to as given, like a configured model (conversation.task_models).
        defaults = cc_controls.custom_defaults(config.custom_controls)
        base_model = defaults.get("model") or config.model
        base_effort = defaults.get("reasoning_effort") or config.reasoning_effort
        base_mode = defaults.get("permission_mode") or config.permission_mode
        conversation.task_models = cc_controls.task_models(config.custom_controls)
        # The `--model` value at launch: the configured model (or a custom
        # control's default) or the operator's pick (an alias from the CLI's
        # list, or a full id). None: no --model (claude's default) on a fresh
        # launch; a continued session runs the default alias
        # (models.launch_model). Once claude runs, conversation.model holds
        # it, and each live switch updates it.
        current_model = cc_models.launch_model(base_model, continuing=pass_continue)
        # Graded reasoning effort, `--effort` at launch the same way; then
        # conversation.effort, which a live switch updates.
        current_effort = base_effort
        # An optio resume of a continued conversation brings back the
        # operator's picks (picks.py), each checked against this run: the
        # permission mode below, the model and effort once the CLI has listed
        # its models. `picks` is what the next resume restores, saved on every
        # switch.
        saved_picks = (await cc_picks.load(host)
                       if resuming and pass_continue and config.conversation_ui else None)
        picks = saved_picks or cc_picks.Picks()
        # Serializes picks-file writes; each writes the latest `picks`.
        picks_lock = asyncio.Lock()
        if saved_picks is not None:
            # The model stays the launch value until the CLI's list says the
            # saved one is still offered (_restore_picks).
            current_effort = saved_picks.effort or current_effort
        # The CLI's own model list (its answer to the stream-json control
        # request initialize), asked once claude is up; None until then, and
        # while the CLI could not tell (then the picker offers the bare
        # aliases, and each live switch asks again: apply_settings).
        cli_models = None
        # Set inside the conversation_ui block once the model list is in:
        # () -> the serialized control list for the running values. Reused on
        # every live switch to re-emit the controls snapshot (effort presence
        # and preselected level follow the selected model).
        build_controls = None
        # Set next to it: () -> widgetData.customControls for the running
        # values (controls.custom_controls_snapshot).
        build_custom_controls = None
        # The controls snapshot the widget was last given (widgetData or an
        # x-optio-control-update). The system/init branch re-emits only a
        # changed one; the permission branch (system/init carries
        # permissionMode too) re-emits always, so a refused switch reverts
        # the select.
        shown_controls = None
        # The permission_mode control: all six modes, the ones this session
        # cannot use disabled with their reason. Switched live; bypass stays
        # reachable when the task allows it.
        permission_options = cc_controls.permission_mode_options(
            permission_gate=config.permission_gate, bypass_allowed=config.bypass_allowed,
        )
        conversation.permission_modes_offered = cc_controls.settable_permission_modes(
            permission_options,
        )
        if saved_picks is not None:
            # Launched in it (`--permission-mode`), if this task offers it.
            restored_mode = cc_picks.restorable_permission_mode(
                saved_picks.permission_mode, conversation.permission_modes_offered,
            )
            if restored_mode:
                conversation.permission_mode = restored_mode
            picks = dataclasses.replace(picks, permission_mode=restored_mode)

        def _resolved(value: str | None) -> str | None:
            # The full id the CLI listed `value` as resolving to (None: unknown).
            listed = cli_models or conversation.models_offered or []
            return next((m.get("resolved") for m in listed if m["id"] == value), None)

        async def _save_picks() -> None:
            # The latest `picks`, written in turn (a live switch and the
            # permission branch below may both save).
            async with picks_lock:
                await cc_picks.save(host, picks)

        def _restore_picks(catalog: list[dict]) -> bool:
            # The restored picks against the CLI's list, asked of a claude
            # launched without the saved model (so the list holds no echo of
            # it): the saved model while listed, else its family
            # (models.restore_model), else the configured model, else default;
            # a model the task names (a custom control's option) is kept as
            # is. An effort level that model lacks gives way to its default.
            # True when the model or effort to run is not the one launched.
            nonlocal current_model, current_effort, picks
            launched = (current_model, current_effort)
            if saved_picks.model:
                restored = (saved_picks.model if saved_picks.model in conversation.task_models
                            else cc_models.restore_model(
                                catalog, saved=saved_picks.model,
                                resolved=saved_picks.model_resolved))
                target = restored or cc_models.launch_model(base_model, continuing=True)
                if restored != saved_picks.model:
                    label = next((m["label"] for m in catalog if m["id"] == target), target)
                    ctx.report_progress(
                        None, f"Resuming on {label}: {saved_picks.model} is no longer offered",
                    )
                    picks = dataclasses.replace(picks, model=restored, model_resolved=next(
                        (m.get("resolved") for m in catalog if m["id"] == restored), None))
                current_model = target
            if saved_picks.effort:
                shown = cc_models.shown_model(catalog, picked=current_model, runtime=None)
                levels, _ = cc_models.model_effort(shown, catalog)
                if levels and current_effort not in levels:
                    picks = dataclasses.replace(picks, effort=None)
                    current_effort = None
            return (current_model, current_effort) != launched

        async def _spawn(model: str | None, *, do_continue: bool):
            claude_flags = host_actions.build_claude_flags(
                permission_mode=conversation.permission_mode or base_mode,
                allow_bypass=config.bypass_allowed,
                allowed_tools=config.allowed_tools,
                disallowed_tools=config.disallowed_tools,
                tools=config.tools,
                mcp_config_path=mcp_config_path,
                model=model,
                resuming=do_continue,
                effort=current_effort,
                show_thinking=config.thinking_verbosity == "visible",
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
        async def _probe_cli_models(model: str | None) -> list[dict] | None:
            """The CLI's model list before the launch: a short-lived claude
            (same env and fs-isolation, ``--model model``, no ``--continue``)
            asked only ``initialize``, then stopped. No turn, so no API call,
            and it writes no transcript (a later ``--continue`` is unaffected).
            None when it cannot tell."""
            flags = host_actions.build_claude_flags(
                permission_mode=None, allowed_tools=None, disallowed_tools=None, model=model,
            )
            argv = host_actions.build_conversation_argv(
                claude_path, claude_flags=flags, permission_gate=False,
            )
            wrap = await _build_claustrum_wrap(host, config, claustrum_path)
            if wrap:
                argv = [*wrap, *argv]
            probe = None
            try:
                probe = await host.launch_subprocess(
                    " ".join(shlex.quote(a) for a in argv), env=env, cwd=host.workdir,
                    env_remove=config.scrub_env, stdin=True,
                )
                probe.stdin.write((json.dumps({
                    "type": "control_request", "request_id": "optio-probe",
                    "request": {"subtype": "initialize"},
                }) + "\n").encode())
                drain = getattr(probe.stdin, "drain", None)
                if drain is not None:
                    await drain()
                async with asyncio.timeout(cc_models.INITIALIZE_TIMEOUT_S):
                    async for raw in probe.stdout:
                        try:
                            ev = json.loads(raw)
                        except ValueError:
                            continue
                        resp = ev.get("response") if isinstance(ev, dict) else None
                        if (ev.get("type") == "control_response" and isinstance(resp, dict)
                                and resp.get("request_id") == "optio-probe"):
                            inner = resp.get("response") if resp.get("subtype") == "success" else None
                            models = inner.get("models") if isinstance(inner, dict) else None
                            return cc_models.parse_cli_models(models) or None
            except Exception:  # noqa: BLE001 — best effort: checked after the launch
                _LOG.info("model list probe failed; restored picks are checked after launch",
                          exc_info=True)
            finally:
                if probe is not None:
                    try:
                        # Graceful, so claude removes its sessions/<pid>.json.
                        await host.terminate_subprocess(probe, aggressive=False)
                    except Exception:  # noqa: BLE001
                        pass
            return None

        # A resume restoring picks asks the CLI's list first, so this launch
        # already runs the checked picks: no message can reach a model the
        # CLI no longer offers. When the probe cannot tell, the launched
        # process's list is used instead (then a correction switches it live,
        # before the first message).
        restore_pending = False
        if saved_picks is not None and (saved_picks.model or saved_picks.effort):
            probed = await _probe_cli_models(current_model)
            if probed is not None:
                _restore_picks(probed)
            else:
                restore_pending = True
        ctx.report_progress(None, f"Launching {AGENT_INFO.name} (conversation)…")
        handle, reader_task = await _spawn(current_model, do_continue=pass_continue)
        launched_handle = handle
        conversation.model, conversation.effort = current_model, current_effort

        def _emit_controls() -> None:
            # The running values to the widget: the built-in controls
            # snapshot and the custom controls with their derived selection,
            # in one x-optio-control-update. Also the listener's
            # refresh_controls, after a refused custom pick.
            nonlocal shown_controls
            if build_controls is None:
                return      # not shown yet: widgetData will carry the values
            shown_controls = build_controls()
            conversation.emit_control_update(
                shown_controls, custom_controls=build_custom_controls(),
            )

        async def _on_settings_change(applied: bool) -> None:
            # A live model/effort switch claude answered (the conversation's
            # apply_settings, from a built-in or a custom control): taken, it
            # is kept for a resume; either way the widget gets the running
            # values (it patched its select optimistically). Saved before the
            # snapshot goes out, so the answer to the pick finds it saved.
            nonlocal picks, cli_models
            if not config.conversation_ui:
                return
            if applied:
                picks = dataclasses.replace(
                    picks, model=conversation.model,
                    model_resolved=_resolved(conversation.model), effort=conversation.effort,
                )
                await _save_picks()
                ctx.report_progress(
                    None,
                    f"{AGENT_INFO.name} switched to model={conversation.model or 'default'} "
                    f"effort={conversation.effort or 'default'}",
                )
            if cli_models is None and build_controls is not None:
                # The CLI could not list its models at launch; the switch
                # asked again (apply_settings), and maybe learned them.
                cli_models = conversation.models_offered
            _emit_controls()

        conversation.on_settings_change = _on_settings_change
        # Claude reports running another model or effort than optio recorded
        # (conversation.check_settings: after launch and after every switch).
        conversation.on_settings_mismatch = (
            lambda text: ctx.report_progress(None, text, level="warning"))
        await _on_agent_live(ctx, config)

        ctx.publish_result(conversation)
        ctx.report_progress(None, f"{AGENT_INFO.name} conversation is live")

        nonlocal conv_listener
        if config.conversation_ui:
            listener_password = secrets.token_urlsafe(32)
            bind_addr = os.environ.get("OPTIO_WIDGET_TUNNEL_BIND", "127.0.0.1")
            upstream_host = os.environ.get("OPTIO_WIDGET_TUNNEL_HOST", "127.0.0.1")
            # On resume, re-prime the replay buffer from the latest snapshot's
            # conversation record (decrypted like the session blob) so a viewer
            # sees the prior conversation history, not just new messages
            # (Claude's own transcript is continued separately).
            initial_events = (
                await _load_conversation_buffer(ctx, decrypt=config.session_blob_decrypt)
                if resuming else None
            )
            # conversation_ui occupies the single permission-handler slot (see design §2).
            # File uploads flow through the generic optio-api /api/widget-upload
            # route → materializeUpload RPC → this per-task writer, which runs in
            # THIS process (only it holds the live Host). The writer lands the
            # bytes in <workdir>/uploads/<name> and fires config.on_upload.
            async def _upload_writer(filename: str, data: bytes) -> str:
                return await materialize(
                    host, host.workdir, filename, data,
                    hook_ctx=hook_ctx, on_upload=config.on_upload,
                )
            ctx.register_upload_writer(_upload_writer)

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
                download_reader=_read_download,
                max_download_bytes=config.max_download_bytes,
                # Only the controls the widget shows may be set: none when the
                # controls bar is off, else the session_controls allowlist.
                allowed_controls=config.settable_controls,
                # The task's own controls ("custom:<id>"), whatever the bar
                # says; a refused pick re-sends the running values.
                custom_controls=config.custom_controls,
                refresh_controls=_emit_controls,
                # Reasoning gets its own rows: a cut-off reply's partial keeps
                # only its text.
                show_thinking=config.thinking_verbosity == "visible",
                # Reachable as soon as it starts, but the opening messages
                # below go first: input opens after them (open_input).
                hold_input=True,
            )
            listener_port = await conv_listener.start(bind_addr)
            await ctx.set_widget_upstream(
                f"http://{upstream_host}:{listener_port}",
                inner_auth=BasicAuth(username="optio", password=listener_password),
            )
            # Before the first message: claude answers initialize at once.
            cli_models = await cc_models.fetch_cli_models(conversation)
            # What a live switch is checked against (a switch made before
            # this point may have asked claude already).
            cli_models = cli_models or conversation.models_offered
            conversation.models_offered = cli_models
            checked = False
            if restore_pending and cli_models is not None and _restore_picks(cli_models):
                # The launched claude runs a restored pick its list no longer
                # offers: switch it to the corrected one, live, before any
                # message reaches it. An effort level the corrected model
                # lacks gives way to its preselected one (apply_settings).
                try:
                    await conversation.apply_settings(model=current_model)
                    checked = True      # the switch read back what claude runs
                except Exception:  # noqa: BLE001 — the session goes on, on the launched model
                    _LOG.warning("could not switch to the corrected resume picks", exc_info=True)
            if not checked:
                # Once after launch: does claude run the model and effort it
                # was launched with? (A mismatch is reported, see above.)
                await conversation.check_settings()

            def build_controls():
                # See controls.build_controls: the model select over the CLI's
                # list (the bare aliases when it could not tell) showing the
                # picked alias, else default, unless the model the running
                # claude reports says otherwise (unknown right after a switch,
                # until the next turn's system/init); the effort slider over
                # the selected model's levels; the permission_mode select
                # showing the running mode; narrowed by the session_controls
                # allowlist.
                return cc_controls.build_controls(
                    catalog=cli_models or cc_models.fallback_models(),
                    model=conversation.model, effort=conversation.effort,
                    runtime_model=conversation.runtime_model,
                    permission_mode=conversation.permission_mode or base_mode,
                    permission_options=permission_options,
                    allowed=config.session_controls,
                )

            def build_custom_controls():
                # See controls.custom_controls_snapshot: each custom control
                # as defined (labels unresolved), selecting the option whose
                # settings all match the running values (none after a manual
                # change in the built-in controls). Not narrowed by the
                # session_controls allowlist, sent whatever the bar says.
                return cc_controls.custom_controls_snapshot(
                    config.custom_controls,
                    catalog=cli_models or cc_models.fallback_models(),
                    model=conversation.model, effort=conversation.effort,
                    permission_mode=conversation.permission_mode or base_mode,
                )

            # widgetData.uploadUrl token; see optio_agents.uploads.upload_url_token.
            upload_url = upload_url_token(ctx._db.name, ctx._prefix, ctx.process_id)
            shown_controls = build_controls()
            await ctx.set_widget_data({
                "protocol": "claudecode",
                "toolVerbosity": config.tool_verbosity,
                "thinkingVerbosity": config.thinking_verbosity,
                # The welcome card atop the transcript (None: no card).
                "welcome": config.welcome_widget_data,
                "showSessionControls": config.show_session_controls,
                "nativeSpinner": config.native_spinner,
                "controls": shown_controls,
                "customControls": build_custom_controls(),
                "showFileUpload": config.show_file_upload,
                "maxUploadBytes": config.max_upload_bytes,
                "fileDownload": config.file_download,
                "maxDownloadBytes": config.max_download_bytes,
                "uploadUrl": upload_url,
            })
            ctx.report_progress(None, "Conversation UI is live")

        # After the launch lines, which pass percent None and would otherwise
        # leave the bar indeterminate. Claude does not replay history through
        # on_event. A resume folds the transcript when the task tools have
        # written a list, and otherwise reloads the saved checklist. The
        # same extractor stays armed, so a tool use the transcript has not
        # answered yet still pairs with the live result.
        extractor = ClaudeTodoExtractor()
        todo_tracker = TodoTracker(extractor, ctx)
        if resuming:
            rebuilt = await task_todos_from_transcript(host, extractor)
            if rebuilt is not None:
                await todo_tracker.use_rebuilt(rebuilt)
            else:
                await todo_tracker.restore(host)
        todo_tracker.arm(conversation)

        # Kickoff / resume notice as first stdin messages (print mode with
        # --input-format stream-json takes no positional prompt). The resume
        # notice goes out on every resume that continues a transcript, with or
        # without host_protocol: CLAUDE.md teaches the System: convention in
        # both (the keyword docs, else optio-agents' System: explainer) and
        # tells claude to wait for this notice instead of reading resume.log
        # before each message. A resume without a transcript sends nothing.
        if config.auto_start and not resuming:
            await conversation.send(host_actions.AUTO_START_PROMPT)
        elif resuming and pass_continue:
            await conversation.send(f"{SYSTEM_MESSAGE_PREFIX}{RESUME_NOTICE}")
        # Fix 19 (owner ruling 2026-09-15, finding 6 #2): the messages the
        # previous run left queued and undelivered go out again, in order,
        # behind the resume notice (without a notice the first one starts the
        # turn). A no-op on a fresh start: nothing was restored.
        if conv_listener is not None:
            await conv_listener.requeue_undelivered()
            # Only now may a viewer's message reach claude: one sent while the
            # session was starting (the suspended replay posts the moment the
            # listener is reachable) goes after the opening messages.
            conv_listener.open_input()

        if resolved_seed_id is not None:
            cred_watch_task = asyncio.create_task(cred_watcher.run_credential_watcher(
                ctx, host,
                seed_id=resolved_seed_id,
                baseline=cred_baseline,
                encrypt=config.seed_encrypt,
                decrypt=config.seed_decrypt,
                lease_holder=lease_holder,
            ))

        try:
            while True:
                # proc_wait handles both pid_like variants (asyncio subprocess
                # on LocalHost, asyncssh process on RemoteHost) and yields the
                # exit code.
                wait_task = asyncio.create_task(proc_wait(handle))
                close_task = asyncio.create_task(conversation.close_requested.wait())
                init_task = asyncio.create_task(conversation.runtime_model_observed.wait())
                perm_task = asyncio.create_task(conversation.permission_mode_observed.wait())
                waiters = (wait_task, close_task, init_task, perm_task)
                try:
                    done, _ = await asyncio.wait(
                        set(waiters), return_when=asyncio.FIRST_COMPLETED,
                    )
                finally:
                    # Fix 19 (coordinator item): cancelled here at session end
                    # (CancelledError is raised at the wait above), the waiters
                    # used to stay pending and log "Task was destroyed but it is
                    # pending!". On a normal return this is the loop it replaces:
                    # every waiter that has not completed is cancelled.
                    for t in waiters:
                        if not t.done():
                            t.cancel()

                if init_task in done and close_task not in done and wait_task not in done:
                    # --- the running model, as the stream's system/init names
                    # it (a full id, e.g. "claude-opus-5[1m]"; every turn
                    # starts with one, so after a live switch the next turn's
                    # names the new model): it moves the model select only
                    # when it is not what the selected alias resolves to
                    # (controls.build_controls); re-emit the snapshot then.
                    # conversation.model (the picked value) is unchanged. ---
                    conversation.runtime_model_observed.clear()
                    if build_controls is not None:
                        # (The custom controls' selection does not depend on
                        # the running model's full id.)
                        snapshot = build_controls()
                        if snapshot != shown_controls:
                            shown_controls = snapshot
                            conversation.emit_control_update(
                                snapshot, custom_controls=build_custom_controls(),
                            )
                    continue

                if perm_task in done and close_task not in done and wait_task not in done:
                    # --- the running permission mode changed (our live switch,
                    # a refused one, or Claude's own, e.g. leaving plan mode):
                    # keep it for a resume while it differs from the mode a
                    # session starts in (the configured one, or a custom
                    # control's default), then re-emit the controls snapshot
                    # so the select shows it (saved first: whoever sees the
                    # snapshot finds it saved). ---
                    conversation.permission_mode_observed.clear()
                    mode = conversation.permission_mode
                    kept = mode if mode and mode != base_mode else None
                    if config.conversation_ui and kept != picks.permission_mode:
                        picks = dataclasses.replace(picks, permission_mode=kept)
                        await _save_picks()
                    _emit_controls()
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
            # Fix 19 (owner ruling 2026-09-15, session-end design part 4): a
            # cooperative cancel (stop, suspend, engine shutdown) while a turn
            # runs first interrupts that turn and waits for its result, at most
            # GRACEFUL_INTERRUPT_TIMEOUT_S, while the reader still runs: the CLI
            # records the partial answer, and every event it sends on the way is
            # buffered. Then the reader stops (x-optio-closed) and the outer
            # finally SIGKILLs claude as before.
            if not ctx.should_continue():
                await _end_turn_gracefully(conversation, conv_listener)
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
            on_caller_message=config.on_caller_message,
            after_execute=config.after_execute,
            protocol=protocol,
            browser_url_rewrite=rewrite_oauth_redirect,
            agent_sender=_agent_sender,
            keywords=config.host_protocol,
        )
    except _SessionFailed as fail:
        raise RuntimeError(str(fail)) from None
    finally:
        # Mark the unwind for RemoteHost.run_command's OPTIO_CANCEL_TRACE gate
        # (see optio_host.host.unwind_tracing): every remote call below is
        # awaited in-line in this coroutine (this finally block creates no new
        # task), so one `with` covers all of it -- teardown_session_tree,
        # _capture_snapshot's run_command calls, cleanup_taskdir, disconnect.
        with unwind_tracing():
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
                # teardown_session_tree's own last step is await_claude_gone, so
                # by the time this span ends the agent process is confirmed gone.
                async with _traced(f"finally: teardown_session_tree aggressive={cancelled}") as t:
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
                if cancelled:
                    ctx.report_progress(
                        None, f"Stopping: agent stopped ({t.elapsed:.0f} s)",
                    )

            # Conversation mode has no tmux/ttyd tree (the gate above never fires):
            # kill the pipe-bound claude directly, then wait for quiescence so the
            # snapshot capture below tars a static home/.claude.
            if config.mode == "conversation" and launched_handle is not None:
                async with _traced(
                    f"finally: terminate_subprocess (conversation) aggressive={cancelled}"
                ) as t:
                    try:
                        await host.terminate_subprocess(launched_handle, aggressive=cancelled)
                    except Exception:
                        _LOG.exception("terminate_subprocess (conversation) failed")
                    try:
                        await host_actions.await_claude_gone(host, claude_path or "claude")
                    except Exception:
                        _LOG.exception("await_claude_gone failed; proceeding")
                if cancelled:
                    ctx.report_progress(
                        None, f"Stopping: agent stopped ({t.elapsed:.0f} s)",
                    )

            if input_runner is not None:
                async with _traced("finally: input listener cleanup"):
                    try:
                        await input_runner.cleanup()
                    except Exception:
                        _LOG.exception("input listener cleanup failed")
                    try:
                        await ctx.clear_control_upstream()
                    except Exception:
                        _LOG.exception("clear control upstream failed")

            # The listener's replay buffer, exported below for the snapshot capture.
            conversation_export: bytes | None = None
            if conv_listener is not None:
                async with _traced("finally: conversation listener teardown"):
                    # Drop the in-process upload writer so a late materializeUpload
                    # RPC can't reach a torn-down Host (raises NoUploadWriter
                    # instead).
                    try:
                        ctx.clear_upload_writer()
                    except Exception:
                        _LOG.exception("clear upload writer failed")
                    # Export the replay buffer BEFORE stop(); the capture stores it
                    # as the snapshot's own (encrypted) conversation blob, the next
                    # launch re-primes from it. Kept in memory: never written to the
                    # host workdir, whose snapshot blob is plaintext.
                    if config.supports_resume:
                        try:
                            conversation_export = json.dumps(
                                conv_listener.export_buffer(),
                            ).encode("utf-8")
                        except Exception:
                            _LOG.exception("conversation buffer export failed")
                    try:
                        await conv_listener.stop()
                    except Exception:
                        _LOG.exception("conversation listener cleanup failed")

            if cred_watch_task is not None:
                async with _traced("finally: cred_watch_task cancel"):
                    cred_watch_task.cancel()
                    try:
                        await cred_watch_task
                    except asyncio.CancelledError:
                        pass

            if lease_holder is not None and resolved_seed_id is not None:
                async with _traced("finally: seed lease release"):
                    try:
                        await _seeds.release(
                            ctx._db, prefix=ctx._prefix, suffix=CLAUDE_SEED_SUFFIX,
                            seed_id=resolved_seed_id, holder=lease_holder,
                        )
                    except Exception:
                        _LOG.exception("lease release failed (TTL will reclaim)")

            if resolved_seed_id is not None:
                async with _traced("finally: credential save-back"):
                    try:
                        await cred_watcher.save_back_if_changed(
                            ctx, host,
                            seed_id=resolved_seed_id,
                            baseline=cred_baseline,
                            encrypt=config.seed_encrypt,
                            decrypt=config.seed_decrypt,
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
                    _seed_t0 = _time.monotonic()
                    _trace("finally: capture_seed START")
                    try:
                        seed_id = await _seeds.capture_seed(
                            ctx, host,
                            manifest=CLAUDE_SEED_MANIFEST,
                            suffix=CLAUDE_SEED_SUFFIX,
                            encrypt=config.seed_encrypt,
                        )
                        _trace(
                            "finally: capture_seed DONE id=%s (%.1fs)",
                            seed_id, _time.monotonic() - _seed_t0,
                        )
                        # Normalized account(s) from the seeded OAuth token (the
                        # isolated home creds are still on disk pre-cleanup);
                        # fail-soft → EMPTY, never disturbs capture. Optio owns the
                        # capture-time stamp: persist metadata.accounts (claudecode
                        # is single-account, so the list holds at most one entry),
                        # then hand on_seed_saved the first account's human-readable
                        # summary (2nd arg), or None when there is no account.
                        info = await resolve_capture_account(host)
                        accounts = [info] if (info is not None and info != EMPTY) else []
                        await _seeds.declare_metadata(
                            ctx._db, prefix=ctx._prefix, suffix=CLAUDE_SEED_SUFFIX,
                            seed_id=seed_id,
                            metadata={"accounts": accounts_to_metadata(accounts)},
                        )
                        summary = accounts[0].summary if accounts else None
                        await _call_maybe_async(config.on_seed_saved, seed_id, summary)
                        _trace(
                            "finally: on_seed_saved fired (summary=%s) (%.1fs)",
                            summary, _time.monotonic() - _seed_t0,
                        )
                    except Exception as exc:
                        _LOG.exception(
                            "seed capture failed; callback not fired, teardown continues",
                        )
                        _trace(
                            "finally: capture_seed RAISED %s (%.1fs)",
                            type(exc).__name__, _time.monotonic() - _seed_t0,
                        )

            # Explicit session capture (on_session_saved): runs BEFORE snapshot
            # capture, whose workdir-tar step defensively wipes home/.claude.
            # Same reached-live gate as snapshots; unlike snapshots there is no
            # credentials guard — the caller owns blob semantics and lifecycle.
            if config.on_session_saved is not None and launched_handle is not None:
                _session_blob_t0 = _time.monotonic()
                _trace("finally: session blob capture START")
                try:
                    _end_state = (
                        "cancelled" if cancelled
                        else "failed" if sys.exc_info()[0] is not None
                        else "done"
                    )
                    _session_blob_id, _session_bytes_len = await _store_session_blob(
                        ctx, host,
                        session_blob_encrypt=config.session_blob_encrypt,
                    )
                    await _call_maybe_async(
                        config.on_session_saved, _session_blob_id, _end_state,
                    )
                    _trace(
                        "finally: session blob capture DONE id=%s state=%s bytes=%d (%.1fs)",
                        _session_blob_id, _end_state, _session_bytes_len,
                        _time.monotonic() - _session_blob_t0,
                    )
                except Exception as exc:
                    _LOG.exception(
                        "session blob capture failed; callback not fired, "
                        "teardown continues",
                    )
                    _trace(
                        "finally: session blob capture RAISED %s (%.1fs)",
                        type(exc).__name__, _time.monotonic() - _session_blob_t0,
                    )

            # Reached-live gate: only capture if claude actually came up.
            # launched_handle is assigned strictly AFTER merge_seed and a
            # successful ttyd/claude launch, so non-None ⟹ the environment was
            # fully planted+seeded and claude live. An interrupt or merge_seed
            # failure before launch leaves it None — skip capture entirely (do
            # NOT touch hasSavedState, so any prior good snapshot survives).
            capture_failed = False
            if config.supports_resume and launched_handle is not None:
                if todo_tracker is not None:
                    await todo_tracker.save(host)
                _snapshot_t0 = _time.monotonic()
                _trace("finally: capture_snapshot START")
                try:
                    # A resumed run with no export (it raised, or the run failed
                    # before the listener started): keep the record it resumed
                    # from, as rescue does, rather than a snapshot without one.
                    carried = None
                    if resuming and conversation_export is None:
                        carried = await _carry_conversation_forward(ctx)
                    saved = await _capture_snapshot(
                        ctx, host,
                        end_state="cancelled" if cancelled else "done",
                        workdir_exclude=config.workdir_exclude,
                        session_blob_encrypt=config.session_blob_encrypt,
                        conversation=conversation_export,
                        conversation_blob_id=carried,
                    )
                    if not saved and carried is not None:
                        await ctx.delete_blob(carried)
                    _trace(
                        "finally: capture_snapshot DONE (%.1fs)",
                        _time.monotonic() - _snapshot_t0,
                    )
                except Exception as exc:
                    capture_failed = True
                    _LOG.exception("snapshot capture failed")
                    _trace(
                        "finally: capture_snapshot RAISED %s (%.1fs)",
                        type(exc).__name__, _time.monotonic() - _snapshot_t0,
                    )

            await _cleanup_after_capture(
                host, capture_failed=capture_failed, cancelled=cancelled,
            )
            async with _traced("finally: disconnect"):
                try:
                    await host.disconnect()
                except Exception:
                    _LOG.exception("host.disconnect failed")


# --- helpers ---------------------------------------------------------------


# Workdir-relative file the conversation listener's replay buffer was persisted
# to at teardown before it became the snapshot's own (encrypted) conversation
# blob. No longer written or read; a workdir restored from an older snapshot
# may still hold it, so the restore deletes it (_restore_workdir_snapshot) and
# every workdir tar excludes it (_effective_workdir_excludes).
_CONV_BUFFER_FILE = ".optio-conversation-buffer.json"


def _effective_workdir_excludes(workdir_exclude: list[str] | None) -> list[str]:
    """The exclude list a workdir tar actually uses: the given list (the
    archive defaults when None, as the hosts read None), plus the
    conversation buffer file unless already listed. Always a new list."""
    # Unanchored on purpose: a file of this internal name is dropped at any depth.
    base = DEFAULT_WORKDIR_EXCLUDES if workdir_exclude is None else workdir_exclude
    if _CONV_BUFFER_FILE in base:
        return list(base)
    return [*base, _CONV_BUFFER_FILE]


async def _restore_workdir_snapshot(
    ctx: ProcessContext, host: Host, workdir_blob_id: ObjectId,
) -> None:
    """Restore a snapshot's plaintext workdir blob, then delete the
    conversation buffer file a snapshot taken before the fix may still carry
    (the record is the snapshot's conversation blob now)."""
    await host.restore_workdir(_stream_blob(ctx, workdir_blob_id))
    await host.remove_file(f"{host.workdir.rstrip('/')}/{_CONV_BUFFER_FILE}")


async def _latest_conversation_record(
    ctx: ProcessContext,
) -> "tuple[dict | None, bytes | None]":
    """The latest snapshot and the stored bytes of its conversation record,
    still under the task's cipher (not decrypted here). The bytes are None
    when there is no snapshot or the snapshot has no record. A Mongo or
    GridFS error (a record naming a blob that is gone included) raises."""
    snapshot = await load_latest_snapshot(
        ctx._db, prefix=ctx._prefix, process_id=ctx.process_id,
    )
    blob_id = snapshot.get("conversationBlobId") if snapshot is not None else None
    if blob_id is None:
        return snapshot, None
    return snapshot, await _read_blob_bytes(ctx, blob_id)


async def _latest_conversation_bytes(ctx: ProcessContext) -> bytes | None:
    """The stored bytes of the latest snapshot's conversation record, still
    under the task's cipher (not decrypted here), or None: no snapshot, a
    snapshot without a record, or a record that cannot be read (logged; never
    raises)."""
    try:
        _snapshot, raw = await _latest_conversation_record(ctx)
        return raw
    except Exception:  # noqa: BLE001
        _LOG.warning("conversation record could not be read", exc_info=True)
        return None


async def _carry_conversation_forward(ctx: ProcessContext) -> ObjectId | None:
    """For a capture with no live listener to export from (crash rescue,
    Resurrect): copy the latest snapshot's conversation record into a new
    blob, the stored bytes as they are (no decrypt, no re-encrypt: the cipher
    stays the task's). A new blob, since pruning deletes per snapshot.
    Returns its id, or None when there is no record to carry."""
    raw = await _latest_conversation_bytes(ctx)
    if raw is None:
        return None
    return await _store_conversation_blob(ctx, raw, encrypt=None)


class UnreadableConversationRecord(Exception):
    """A conversation record exists but cannot be decrypted, is not UTF-8
    JSON, or is not a list of ``[seq, event]`` entries. The message names the
    failure only, never the record's content, and the underlying error is not
    chained (a cipher's or decoder's message could quote it)."""


def _parse_conversation_record(
    raw: bytes, decrypt: "Callable[[bytes], bytes] | None",
) -> "list[tuple[int, dict]]":
    """Decrypt a stored conversation record through ``decrypt`` (the task's
    ``session_blob_decrypt``; None: stored plaintext), parse it and keep its
    ``(seq, event)`` entries, minus any terminal close marker. CPU-bound
    (callers run it in ``asyncio.to_thread``). An empty list is a record with
    nothing in it. Raises UnreadableConversationRecord."""
    try:
        plain = decrypt(raw) if decrypt is not None else raw
    except Exception as exc:  # noqa: BLE001
        # The type only: the message of a cipher error is not kept.
        raise UnreadableConversationRecord(
            f"could not be decrypted ({type(exc).__name__})",
        ) from None
    try:
        data = json.loads(plain.decode("utf-8"))
    except Exception:  # noqa: BLE001
        raise UnreadableConversationRecord("is not valid JSON") from None
    out: list[tuple[int, dict]] = []
    valid = 0
    for entry in data if isinstance(data, list) else []:
        if (isinstance(entry, list) and len(entry) == 2
                and isinstance(entry[0], int) and isinstance(entry[1], dict)):
            valid += 1
            # Defensive: drop any terminal close marker a pre-fix buffer may
            # carry, so it never replays the prior run's close on resume.
            if entry[1].get("type") == "x-optio-closed":
                continue
            out.append((entry[0], entry[1]))
    if not isinstance(data, list) or (data and not valid):
        raise UnreadableConversationRecord("is not a list of [seq, event] entries")
    return out


async def _load_conversation_buffer(
    ctx: ProcessContext, *, decrypt: "Callable[[bytes], bytes] | None",
) -> "list[tuple[int, dict]] | None":
    """Read the conversation replay buffer a resume primes the listener with:
    the latest snapshot's conversation record, through ``decrypt`` (the
    task's ``session_blob_decrypt``; None: stored plaintext).

    Returns a list of ``(seq, event)``, or None when absent, undecryptable
    or malformed (logged; never raises: the buffer just starts empty).
    """
    raw = await _latest_conversation_bytes(ctx)
    if raw is None:
        return None
    try:
        out = await asyncio.to_thread(_parse_conversation_record, raw, decrypt)
    except UnreadableConversationRecord as exc:
        # The id and the failure only: the record's content is the conversation.
        _LOG.warning(
            "conversation record of process %s %s; starting empty", ctx.process_id, exc,
        )
        return None
    return out or None


async def _read_conversation(
    ctx: ProcessContext, *, decrypt: "Callable[[bytes], bytes] | None",
) -> dict | None:
    """The ``TaskInstance.read_conversation`` hook (set only when
    ``conversation_ui``): the suspended process's saved conversation record,
    decrypted, parsed and filtered like a resume's buffer
    (``_parse_conversation_record``), but strict. Returns ``{"events":
    [[seq, event], ...], "capturedAt": <iso or None>}``, or None when there
    is no record (or it holds no entries). A record that cannot be read,
    decrypted or parsed raises (UnreadableConversationRecord, or the Mongo /
    GridFS error), so the engine's archive listener answers 500 read-failed
    instead of 404 no-conversation.

    ``ctx`` may be the engine's read-only context (process_id, _db, _prefix,
    load_blob only) — this function and everything it calls touch nothing
    else.
    """
    snapshot, raw = await _latest_conversation_record(ctx)
    if raw is None:
        return None

    def _parse() -> "list[list]":
        return [[seq, event] for seq, event in _parse_conversation_record(raw, decrypt)]
    try:
        events = await asyncio.to_thread(_parse)
    except UnreadableConversationRecord as exc:
        # The id and the failure only: the record's content is the conversation.
        _LOG.warning("conversation record of process %s %s", ctx.process_id, exc)
        raise
    if not events:
        return None
    captured_at = snapshot["capturedAt"]
    if captured_at is not None:
        # Mongo (pymongo/motor) decodes to a naive datetime that is UTC; a
        # naive isoformat() carries no offset, and the UI's `new Date(...)`
        # then reads it as local time. Always emit an explicit UTC offset.
        captured_at = (
            captured_at.replace(tzinfo=timezone.utc) if captured_at.tzinfo is None
            else captured_at.astimezone(timezone.utc)
        )
    return {
        "events": events,
        "capturedAt": captured_at.isoformat() if captured_at is not None else None,
    }


# Fix 19 (owner ruling 2026-09-15, session-end design part 4): how long a
# cooperative cancel gives a running turn to end gracefully (the CLI then
# keeps the partial answer in its transcript) before the reader stops and
# claude is SIGKILLed as before. It comes out of the cancel grace the
# snapshot shares, so it is small and bounded.
GRACEFUL_INTERRUPT_TIMEOUT_S = 3.0


async def _end_turn_gracefully(
    conversation, listener, *, timeout_s: float = GRACEFUL_INTERRUPT_TIMEOUT_S,
) -> bool:
    """End a running turn before the kill. Nothing happens unless the
    conversation is open and a turn runs. Then: refuse new sends, let the
    listener (if any) announce the session-end marker, interrupt the turn
    with cancel_queued and wait for its result, all within ``timeout_s``.
    Returns whether the turn ended in time; never raises (the kill follows
    anyway)."""
    if conversation is None or conversation.closed or not conversation.is_pending():
        return False
    conversation.begin_session_end()
    if listener is not None:
        listener.announce_session_end()
    try:
        async with asyncio.timeout(timeout_s):
            await conversation.interrupt_for_session_end()
    except TimeoutError:
        _LOG.warning(
            "session end: the running turn did not end within %.0f s of the "
            "interrupt; killing claude", timeout_s,
        )
        return False
    except ConversationClosed:
        return False
    except Exception:  # noqa: BLE001 — best effort; the kill follows anyway
        _LOG.exception("session end: graceful interrupt failed; killing claude")
        return False
    return True


async def _plant_session_content(
    ctx: ProcessContext, host: Host, hook_ctx: HookContext,
    config: ClaudeCodeTaskConfig, protocol, *,
    resuming: bool, resolved_seed_id: str | None,
) -> "tuple[str | None, dict[str, str]]":
    """Fresh/resume content planting shared by both modes.

    Returns ``(cred_baseline, focus_env)``. Mirrors the pre-existing inline
    logic exactly for the iframe path; conversation mode reuses it unchanged
    except for the prompt-composition kwargs (mode-aware).

    CLAUDE.md is composed with ``check_resume_log_every_message=False`` in
    every mode: claude reads resume.log only when the System: resume notice
    arrives, never before each message. Every resume that continues a
    transcript sends that notice, host_protocol or not (the --continue
    positional in iframe mode, the first stdin message in conversation
    mode). A resume without a transcript launches without --continue and
    sends none, and the wording is still right there: that is a new
    conversation, which reads CLAUDE.md anew and has no earlier resume.log
    line to compare against, so polling would tell it nothing either.
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
                decrypt=config.seed_decrypt,
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
                fs_isolation_dirs=fs_isolation_dirs(config, host.workdir),
                file_download=config.file_download,
                check_resume_log_every_message=False,
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
                decrypt=config.seed_decrypt,
            )
        cred_baseline = await cred_watcher.cred_fingerprint(host)
        refreshed_files = await _maybe_refresh_on_resume(host, hook_ctx, config, protocol)
    # Install the caller's Claude Code settings LAST — after the seed (fresh) or
    # the restored snapshot (resume) — so they WIN over whatever settings the
    # seed/snapshot carried, while preserving every other key. focus_mode knobs
    # are already folded into effective_claude_config by build_focus_mode.
    if effective_claude_config is not None:
        await host_actions.apply_claude_settings(host, effective_claude_config)
    if config.supports_resume:
        await _append_resume_log_entry(host, refreshed=refreshed_files)
    if config.before_execute is not None:
        await config.before_execute(hook_ctx)
    return cred_baseline, focus_env


async def _plant_mcp_servers(
    ctx: ProcessContext, host: Host, config: ClaudeCodeTaskConfig,
) -> str | None:
    """This launch's MCP servers, planted for ``--mcp-config``: the file's
    path, or None without servers (a file an older snapshot carried is
    removed; the capture now leaves it out). Resolved anew on every launch,
    fresh and resume, so a provider hands out fresh credentials each time; a
    provider's exception fails the launch with its own message. Logs the
    server names only (header values are credentials)."""
    servers = await resolve_mcp_servers(config, ctx.process_id)
    if servers:
        _LOG.info("MCP servers for this launch: %s", ", ".join(s.name for s in servers))
    return await host_actions.plant_mcp_config(host, host.workdir, servers)


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


async def _newest_transcript_path(host: Host) -> str | None:
    """Absolute path of the newest project ``*.jsonl``, or None."""
    workdir = host.workdir.rstrip("/")
    projects = f"{workdir}/home/.claude/projects"
    found = await host.run_command(
        f"find {shlex.quote(projects)} -name '*.jsonl' -printf '%T@\\t%p\\n' "
        "2>/dev/null | sort -n | tail -1"
    )
    line = (getattr(found, "stdout", None) or "").strip()
    if "\t" not in line:
        return None
    path = line.split("\t", 1)[1]
    return path or None


async def task_todos_from_transcript(host: Host, extractor: ClaudeTodoExtractor):
    """Checklist implied by the newest transcript, or None.

    The whole file, not the model-id tail: a TaskCreate early in the
    session is what gives the later list its subjects. None when the
    file is missing or no task-tool result was accepted, so the caller
    keeps the saved checklist.
    """
    path = await _newest_transcript_path(host)
    if not path:
        return None
    body = await host.run_command(f"cat {shlex.quote(path)} 2>/dev/null")
    return todo_progress_from_transcript(
        getattr(body, "stdout", None) or "", extractor,
    )


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
    """tar.gz the sensitive ``home/.claude`` subtree and fetch it as bytes.

    Without the MCP file (``host_actions.MCP_CONFIG_REL``): its header values
    are credentials, and it is rewritten at every launch, so no snapshot
    (encrypted or not) ever carries them. The file stays on the host."""
    workdir = host.workdir.rstrip("/")
    tmpfile = f"{workdir}/.optio-claudecode-session.tar.gz"
    _trace("archive_home: tar run_command START")
    # --exclude before the operand: GNU tar applies it to the names after it.
    r = await host.run_command(
        f"tar -czf {shlex.quote(tmpfile)} "
        f"--exclude={shlex.quote(host_actions.MCP_CONFIG_REL)} "
        f"-C {shlex.quote(workdir)} home/.claude"
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


def _excludes_with_rescue_marker(config: ClaudeCodeTaskConfig) -> list[str]:
    """The capture's effective workdir excludes plus the rescue marker, for
    the captures of crash-orphan rescue and Resurrect (a restored marker would
    re-trigger rescue). A given list replaces the archive defaults, so they are
    spelled out when the config leaves ``workdir_exclude`` unset."""
    base = (
        DEFAULT_WORKDIR_EXCLUDES if config.workdir_exclude is None
        else config.workdir_exclude
    )
    return [*base, _RESCUE_MARKER]


def _claude_bin_path(host: "Host") -> str:
    """Deterministic launch path of claude inside the isolated HOME."""
    return f"{host.workdir.rstrip('/')}/home/.local/bin/claude"


async def _marker_present(host: "Host", marker_path: str) -> bool:
    r = await host.run_command(
        f"test -e {shlex.quote(marker_path)} && echo YES || true"
    )
    return "YES" in r.stdout


async def _workdir_present(host: "Host") -> bool:
    # cwd="/": the workdir itself may be missing, and RemoteHost runs every
    # command as `cd <cwd> && ...`.
    r = await host.run_command(
        f"test -d {shlex.quote(host.workdir)} && echo YES || true", cwd="/",
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
    # A gracefully stopped task was already snapshotted and its workdir
    # removed; a crash leaves the workdir in place. No workdir, no orphan —
    # and every probe below would otherwise run inside the missing workdir.
    if not await _workdir_present(host):
        return

    socket = host_actions._tmux_socket_path(host)
    session = "optio"
    marker_path = f"{host.workdir.rstrip('/')}/{_RESCUE_MARKER}"

    # No tmux on the worker (conversation mode never needs it): no session to
    # probe. A failed capture keeps the workdir, so a Resume gets here.
    tmux_path = await host_actions.find_tmux(host)
    alive = tmux_path is not None and await host_actions.tmux_session_alive(
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
    #    Without tmux there is no tree (a marker alone brought us here).
    if tmux_path is not None:
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
    #    re-trigger rescue in a loop. No live listener here: the conversation
    #    record of the last suspend is carried forward (a copy; dropped again
    #    when the credentials guard refuses).
    exclude = _excludes_with_rescue_marker(config)
    conversation_blob_id = await _carry_conversation_forward(ctx)
    saved = await _capture_snapshot(
        ctx, host,
        end_state="rescued",
        workdir_exclude=exclude,
        session_blob_encrypt=config.session_blob_encrypt,
        conversation_blob_id=conversation_blob_id,
    )
    if not saved and conversation_blob_id is not None:
        await ctx.delete_blob(conversation_blob_id)

    # 4. Capture durable (or refused by the credentials guard) — clear the marker.
    await host.run_command(f"rm -f {shlex.quote(marker_path)}")
    if saved:
        _LOG.warning("crash-orphan rescue: fresh snapshot captured; orphan killed")
    else:
        _LOG.warning(
            "crash-orphan rescue: orphan killed; no snapshot (credentials guard refused)",
        )


async def _store_session_blob(
    ctx: ProcessContext,
    host: Host,
    *,
    session_blob_encrypt: "Callable[[bytes], bytes] | None",
):
    """Tar home/.claude, encrypt, store as a standalone GridFS blob.

    Shared by snapshot capture and the explicit on_session_saved capture.
    Returns ``(session_blob_id, byte_size)`` — the byte size is the encrypted
    payload actually written, used by callers for diagnostic traces and the
    "session state saved" milestone.
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
    return session_blob_id, expected_len


async def _store_conversation_blob(
    ctx: ProcessContext,
    data: bytes,
    *,
    encrypt: "Callable[[bytes], bytes] | None",
) -> ObjectId:
    """Store the conversation replay buffer (``export_buffer()`` JSON) as its
    own GridFS blob, ``conversation``, through ``encrypt`` when set (the
    task's ``session_blob_encrypt``: plaintext only when the session blob is
    plaintext too). Returns its id."""
    payload = encrypt(data) if encrypt is not None else data
    expected_len = len(payload)
    async with ctx.store_blob("conversation") as writer:
        await writer.write(payload)
        conversation_blob_id = writer.file_id
        written = getattr(writer, "_position", None)
        if written is not None and written != expected_len:
            raise RuntimeError(
                f"conversation blob short-write: expected {expected_len} bytes, "
                f"GridIn._position is {written}"
            )
    return conversation_blob_id


# How often _stream_archive_to_blob emits a "Snapshot: archiving workdir, N MB
# so far" progress line while streaming the remote tar into GridFS.
_ARCHIVE_PROGRESS_INTERVAL_S = 30.0


async def _stream_archive_to_blob(
    ctx: ProcessContext,
    host: Host,
    exclude: list[str] | None,
    wwriter,
    *,
    start: float,
    clock: "Callable[[], float]" = _time.monotonic,
) -> int:
    """Stream ``host.archive_workdir(exclude)`` into ``wwriter``, counting
    bytes and emitting a periodic always-on progress milestone.

    Pulled out of ``_capture_snapshot`` so the progress-cadence logic (every
    ``_ARCHIVE_PROGRESS_INTERVAL_S`` seconds measured from ``start``) can be
    unit-tested with a fake host and an injected ``clock``, without a real
    archive or GridFS. Returns the total byte count streamed. Does not
    change what is archived or how — purely an observing pass-through.
    """
    total = 0
    last_report = start
    async for chunk in host.archive_workdir(exclude):
        await wwriter.write(chunk)
        total += len(chunk)
        now = clock()
        if now - last_report >= _ARCHIVE_PROGRESS_INTERVAL_S:
            last_report = now
            ctx.report_progress(
                None,
                f"Snapshot: archiving workdir, {total // (1024 * 1024)} MB "
                f"so far ({int(now - start)} s)",
            )
    return total


async def _on_agent_live(ctx: ProcessContext, config: ClaudeCodeTaskConfig) -> None:
    """claude is up: from here the workdir holds work the last snapshot lacks,
    so a failure leaves something Resurrect can save."""
    if config.supports_resume:
        await ctx.mark_unsaved_work()


async def _cleanup_after_capture(host: Host, *, capture_failed: bool, cancelled: bool) -> None:
    """Remove the task directory, unless the capture failed: then it stays on
    the host (hasUnsavedWork stays set) so Resurrect can save it later."""
    if capture_failed:
        _LOG.warning(
            "snapshot capture failed; keeping task directory %s for Resurrect",
            getattr(host, "taskdir", "?"),
        )
        return
    async with _traced(f"finally: cleanup_taskdir aggressive={cancelled}"):
        try:
            await host.cleanup_taskdir(aggressive=cancelled)
        except Exception:
            _LOG.exception("cleanup_taskdir failed")


async def _capture_snapshot(
    ctx: ProcessContext,
    host: Host,
    *,
    end_state: str,
    workdir_exclude: list[str] | None,
    session_blob_encrypt: "Callable[[bytes], bytes] | None" = None,
    conversation: bytes | None = None,
    conversation_blob_id: ObjectId | None = None,
) -> bool:
    """Capture a snapshot: session blob, conversation blob (when
    ``conversation``, the listener's exported replay buffer, is given), then
    workdir blob and record.

    ``conversation_blob_id`` instead names a conversation blob already
    stored, which the record only references: crash rescue and Resurrect
    have no listener, so they carry the previous record forward (or adopt a
    cut-off capture's). Not both.

    Returns True when a snapshot record was inserted, False when the
    credentials guard refused (nothing stored by this call). Raises on any
    other failure.
    """
    if conversation is not None and conversation_blob_id is not None:
        raise ValueError("conversation and conversation_blob_id are exclusive")
    # 0. Credentials-present guard. Refuse to snapshot an unconfigured
    # environment: without home/.claude/.credentials.json a restored snapshot
    # drops the agent to /login (looking like a zero-config seed session).
    # This MUST be first — the resume path (load_latest_snapshot) ignores
    # hasSavedState, so the degenerate snapshot must never be CREATED, not
    # merely have its flag skipped.
    workdir = host.workdir.rstrip("/")
    async with _traced("capture: credentials guard"):
        chk = await host.run_command(
            f"test -s {shlex.quote(workdir)}/home/.claude/.credentials.json "
            f"&& echo OK || true"
        )
    if "OK" not in chk.stdout:
        _LOG.warning(
            "snapshot capture skipped: home/.claude/.credentials.json "
            "absent/empty; refusing to mark resumable",
        )
        # A snapshot without credentials is refused, so a later Resurrect
        # could not save this workdir either: nothing resurrectable is left.
        await ctx.clear_unsaved_work()
        return False

    # 1-3. tar the sensitive subtree, encrypt, write the session blob.
    async with _traced("capture: store_session_blob") as t:
        session_blob_id, session_bytes_len = await _store_session_blob(
            ctx, host, session_blob_encrypt=session_blob_encrypt,
        )
    _trace("capture: store_session_blob id=%s bytes=%d", session_blob_id, session_bytes_len)
    ctx.report_progress(
        None,
        f"Snapshot: session state saved "
        f"({session_bytes_len / (1024 * 1024):.1f} MB, {t.elapsed:.0f} s)",
    )

    # 3b. the conversation replay buffer, encrypted like the session blob.
    if conversation is not None:
        async with _traced("capture: store_conversation_blob"):
            conversation_blob_id = await _store_conversation_blob(
                ctx, conversation, encrypt=session_blob_encrypt,
            )

    # 4. defensive wipe so the workdir tar cannot carry sensitive state.
    async with _traced("capture: rm -rf home/.claude"):
        await host.run_command(f"rm -rf {shlex.quote(workdir)}/home/.claude")

    # 4b. Drop regenerable scratch that would bloat the workdir snapshot.
    # The claude binary is NOT here: home/.local/share/claude/versions is a
    # symlink to the shared optio cache, which os.walk does not follow and CLI
    # tar stores as a symlink, so it never enters the archive. mozilla
    # cache/profile are pure scratch.
    async with _traced("capture: rm -rf regenerable home dirs"):
        await host.run_command(
            "rm -rf "
            f"{shlex.quote(workdir)}/home/.cache/mozilla "
            f"{shlex.quote(workdir)}/home/.mozilla"
        )

    await _store_workdir_snapshot(
        ctx, host,
        end_state=end_state,
        workdir_exclude=workdir_exclude,
        session_blob_id=session_blob_id,
        conversation_blob_id=conversation_blob_id,
    )
    return True


async def _store_workdir_snapshot(
    ctx: ProcessContext,
    host: Host,
    *,
    end_state: str,
    workdir_exclude: list[str] | None,
    session_blob_id: ObjectId,
    conversation_blob_id: ObjectId | None = None,
) -> None:
    """Second half of a capture: archive the workdir, insert the snapshot
    record, flag the state, prune. Resurrect calls it alone when a cut-off
    capture had already stored the session blob and removed home/.claude.

    Every claudecode workdir tar is made here, so this is where the
    conversation buffer file is always excluded (_effective_workdir_excludes),
    whatever ``workdir_exclude`` the caller passes.
    """
    # 4c. Record the blobs before streaming: a capture cut off past the cancel
    # grace leaves this record, naming the partial workdir blob to delete
    # (and its conversation blob, already stored).
    # A record still here is from a cut-off capture nobody resurrected (the
    # run was resumed or restarted instead); the upsert below would orphan
    # its partial blob and its conversation blob, so discard those first
    # (each kept if a snapshot names it).
    stale = await load_pending_capture(ctx._db, ctx._prefix, ctx.process_id)
    if stale is not None:
        await discard_pending_workdir_blob(
            ctx._db, ctx._prefix,
            process_id=ctx.process_id, record=stale, delete_blob=ctx.delete_blob,
        )
        await discard_pending_conversation_blob(
            ctx._db, ctx._prefix,
            process_id=ctx.process_id, record=stale, delete_blob=ctx.delete_blob,
            keep=conversation_blob_id,
        )
    workdir_blob_id = ObjectId()
    await record_pending_capture(
        ctx._db, ctx._prefix,
        process_id=ctx.process_id,
        session_blob_id=session_blob_id,
        workdir_blob_id=workdir_blob_id,
        conversation_blob_id=conversation_blob_id,
    )

    # 5. stream the plaintext workdir tar.
    exclude = _effective_workdir_excludes(workdir_exclude)
    ctx.report_progress(None, "Snapshot: archiving workdir…")
    async with _traced("capture: archive_workdir+store") as t:
        async with ctx.store_blob("workdir", file_id=workdir_blob_id) as wwriter:
            total_bytes = await _stream_archive_to_blob(
                ctx, host, exclude, wwriter, start=t.start,
            )
    _trace("capture: archive_workdir+store id=%s bytes=%d", workdir_blob_id, total_bytes)
    ctx.report_progress(
        None,
        f"Snapshot: workdir archived "
        f"({total_bytes // (1024 * 1024)} MB in {t.elapsed:.0f} s)",
    )

    # 6. insert the snapshot doc.
    async with _traced("capture: insert_snapshot"):
        await insert_snapshot(
            ctx._db,
            prefix=ctx._prefix,
            process_id=ctx.process_id,
            end_state=end_state,
            session_blob_id=session_blob_id,
            workdir_blob_id=workdir_blob_id,
            deliverables_emitted=[],
            conversation_blob_id=conversation_blob_id,
        )
    await delete_pending_capture(ctx._db, ctx._prefix, ctx.process_id)

    # 7. surface the Resume affordance in the dashboard. Right after the
    # record, before the pruning: a force-cancel past the grace can cut the
    # capture off anywhere from here on, and the snapshot is complete now, so
    # the process must already offer Resume, not a Resurrect of work that is
    # saved (seen 2026-10-10: cut off 3 s into the steps after the record).
    async with _traced("capture: mark_has_saved_state"):
        await ctx.mark_has_saved_state()
    _trace("capture: mark_has_saved_state DONE")
    await ctx.clear_unsaved_work()
    ctx.report_progress(None, "Snapshot saved")

    # 8. prune + delete stale blobs.
    async with _traced("capture: prune") as t:
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
            if p.get("conversationBlobId") is not None:
                try:
                    await ctx.delete_blob(p["conversationBlobId"])
                except Exception:
                    _LOG.exception("delete_blob(conversation) failed")
    _trace("capture: prune pruned=%d", len(pruned))


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
    host, hook_ctx, config: ClaudeCodeTaskConfig, protocol,
) -> list[str]:
    """Run on_resume_refresh (if any) and rewrite CLAUDE.md when changed.

    ``protocol`` is the session's protocol; its documentation is rendered so
    the refreshed file matches the fresh-start composition (notice-only
    resume detection included, see ``_plant_session_content``).

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
        documentation=protocol.documentation if new_config.host_protocol else None,
        workdir_exclude=new_config.workdir_exclude,
        supports_resume=new_config.supports_resume,
        host_protocol=new_config.host_protocol,
        omit_task_framing=omit_task_framing,
        fs_isolation_dirs=fs_isolation_dirs(new_config, host.workdir),
        file_download=new_config.file_download,
        check_resume_log_every_message=False,
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

    async def _resurrect(ctx: ProcessContext) -> None:
        from optio_claudecode.resurrect import resurrect_claudecode_session
        await resurrect_claudecode_session(ctx, config)

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
        resurrect=_resurrect if config.supports_resume else None,
        metadata=metadata or {},
        session_crypto=SessionCrypto(
            encrypt=config.session_blob_encrypt,
            decrypt=config.session_blob_decrypt,
        ),
        read_conversation=(
            (lambda ctx: _read_conversation(ctx, decrypt=config.session_blob_decrypt))
            if config.conversation_ui else None
        ),
    )
