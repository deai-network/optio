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
import secrets
import shlex
from typing import AsyncIterator

from optio_core.context import ProcessContext
from optio_core.models import BasicAuth, TaskInstance

from optio_agents import HookContext, RESUME_NOTICE, SYSTEM_MESSAGE_PREFIX, get_protocol
from optio_agents import seeds as _seeds
from optio_agents.input_listener import serialized, start_input_listener
from optio_agents.session_controls import model_control
from optio_agents.uploads import materialize
from optio_agents.protocol.session import _SessionFailed, run_log_protocol_session
from optio_host.host import Host, LocalHost, ProcessHandle, proc_wait
from optio_host.paths import task_dir

from optio_cursor import cred_watcher, host_actions
from optio_cursor import model_probe
from optio_cursor import models as cursor_models
from optio_cursor.conversation import CursorConversation
from optio_cursor.conversation_listener import ConversationListener
from optio_cursor.prompt import compose_agents_md
from optio_cursor.seed_manifest import CURSOR_SEED_MANIFEST, CURSOR_SEED_SUFFIX
from optio_cursor.snapshots import (
    effective_workdir_exclude,
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


def _teardown_aggressive(*, cancelled: bool, seeded: bool) -> bool:
    """Whether to SIGKILL cursor immediately on teardown vs SIGTERM-and-wait.

    A **seeded** session is torn down GRACEFULLY even on cancel: cursor's
    single-use refresh token may have rotated this session, and cursor's
    auth.json write is best-effort — an aggressive SIGKILL can beat the flush,
    stranding the rotation so the credential save-back persists the now-spent
    token and the next launch demands re-auth. SIGTERM-and-wait lets cursor
    flush first. A non-seeded session keeps the fast aggressive kill on cancel.
    """
    return cancelled and not seeded


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


async def _probe_or_cached_models(
    ctx, conversation, models: list[dict], *, host, seed_id: str | None,
    resuming: bool,
) -> list[dict]:
    """Grey out models the seed's plan cannot use. cursor lists its full
    catalogue with no plan flag; a gated model silently answers "Upgrade your
    plan to continue". So probe each id once on a FRESH launch (before the widget
    is shown), cache the result per seed (24h), and reuse it on later launches
    incl. resumes — see model_probe. Never probes on resume (the plan was already
    determined). Best-effort: on any failure the list is returned unchanged."""
    ids = [m["id"] for m in models if m.get("id")]
    if not ids:
        return models
    usable: dict[str, bool] | None = None
    if seed_id is not None:
        try:
            usable = await model_probe.load_probe_cache(ctx._db, ctx._prefix, seed_id)
        except Exception:  # noqa: BLE001
            _LOG.exception("model-probe cache load failed")
    if usable is None:
        if resuming:
            # A resumed session never re-probes: the plan was settled on the
            # fresh run (and a cache miss here must not pay the probe cost).
            return models
        # Log the milestone ONCE, then advance the progress bar silently per
        # model (percent-only calls are coalesced — no per-model log spam).
        ctx.report_progress(0.0, "Checking available models…")

        def _report(i: int, total: int, _mid: str) -> None:
            ctx.report_progress(i / total * 100.0)

        try:
            usable = await model_probe.probe_models(conversation, ids, report=_report)
        except Exception:  # noqa: BLE001
            _LOG.exception("model probe failed; showing the unfiltered list")
            return models
        if seed_id is not None:
            try:
                await model_probe.save_probe_cache(
                    ctx._db, ctx._prefix, seed_id, usable,
                )
            except Exception:  # noqa: BLE001
                _LOG.exception("model-probe cache save failed")
        # Drop the probe's throwaway turns so they never leak into the operator's
        # conversation (fresh ACP session; the catalogue is unchanged), then
        # purge the probe session's on-disk records so a later resume can't
        # rediscover them (see host_actions.purge_cursor_session).
        probe_sid = await conversation.reset_session()
        if probe_sid:
            try:
                await host_actions.purge_cursor_session(host, probe_sid)
            except Exception:  # noqa: BLE001
                _LOG.exception("purging the probe session failed")
    return model_probe.apply_probe(models, usable)


async def run_cursor_session(ctx: ProcessContext, config: CursorTaskConfig) -> None:
    """Execute function body for one optio-cursor task instance."""
    host: Host = _build_host(config, ctx.process_id)
    # Cursor's login opens the auth URL via xdg-open; "redirect" shadows
    # xdg-open with a capture shim that surfaces the URL to the operator on a
    # BROWSER: line (NO_OPEN_BROWSER is intentionally NOT set — see
    # host_actions._isolation_env).
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
    # Persisted ACP session id restored from the resume snapshot (conversation
    # mode). Threaded into replay_history below so cursor session/load's it
    # DIRECTLY (skipping the session/list heuristic); None when not resuming, in
    # iframe mode, or when the prior snapshot recorded no id (pre-seam rows).
    resume_session_id: str | None = None
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
    # iframe-input widget: an engine-side HTTP listener injects operator input
    # (typed messages + NAV keystrokes) into the cursor tmux TUI. One lock
    # serializes human input against the system (_agent_sender) sends so bursts
    # never interleave.
    input_runner = None
    injection_lock = asyncio.Lock()

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
        nonlocal lease_holder, cred_baseline, claustrum_path, resume_session_id
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
            # surfaces to the caller (no silent fresh-start).
            await host.restore_workdir(_stream_blob(ctx, snapshot["workdirBlobId"]))
            # The cursor-agent launch symlink (home/.local/bin/cursor-agent)
            # lives INSIDE the workdir and was wiped by the restore;
            # re-establish it against the cache (which lives OUTSIDE the workdir
            # and survives). Idempotent: cache hit → just relinks, no
            # reinstall/redownload. ttyd survives untouched (real host home,
            # outside the workdir).
            cursor_path = await host_actions.ensure_cursor_installed(
                hook_ctx,
                install_if_missing=config.install_if_missing,
                install_dir=config.cursor_install_dir,
                progress_label="Restoring cursor-agent runtime…",
            )
            await host_actions._rotate_optio_log(host)
            # A restored snapshot means cursor persisted a chat for this cwd;
            # --continue resumes the most recent one. $HOME lives inside the
            # restored workdir at the same absolute path (deterministic
            # taskdir), so the cwd-keyed chat lookup matches.
            pass_continue = True
            # Conversation mode: the ACP session id the fresh cursor stored last
            # run (None for iframe snapshots or pre-seam rows). Handed to
            # replay_history below so cursor session/load's the prior conversation
            # DIRECTLY (skipping the session/list heuristic).
            resume_session_id = snapshot.get("sessionId")

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

        # Pre-authorize the workspace. cursor-agent gates a fresh directory
        # behind an interactive "Do you trust this directory?" prompt that
        # --force does not bypass; an unattended auto_start launch would hang
        # there and the task would die. Planting cursor's own trust marker skips
        # it (see host_actions.workspace_trust_marker).
        _trust_rel, _trust_content = host_actions.workspace_trust_marker(host.workdir)
        await host.write_text(_trust_rel, _trust_content)

        # cursor derives its socket/temp dir from CURSOR_DATA_DIR; the long
        # taskdir would push it over cursor's path-length limit and make it fall
        # back to an ungranted /tmp/.cursor (EACCES → exit 1 at startup). Symlink
        # a short path back into the granted workdir so the temp stays short and
        # confined. Idempotent — re-links on resume's restored tree.
        await host_actions.link_cursor_data_dir(host)

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
        nonlocal cred_watch_task, input_runner

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

        # iframe-input widget: start the engine-side input listener and publish
        # it as the control upstream. The operator types messages / drives the
        # TUI menus via /api/widget-control. Both the human path and the system
        # _agent_sender share ``injection_lock`` so tmux injection bursts never
        # interleave.
        async def _human_input(text: str) -> None:
            await host_actions.send_text_to_cursor(
                host, tmux_path, tmux_socket, tmux_session, text,
            )

        async def _human_key(key: str) -> None:
            await host_actions.send_key_to_cursor(
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
            # File upload flows through the generic optio-api /api/widget-upload
            # route → materializeUpload RPC → this per-task writer, which runs in
            # THIS process (only it holds the live Host). The writer lands the
            # bytes under <workdir>/uploads/<name> and fires config.on_upload; the
            # view injects a System: path reference so cursor reads them with its
            # own tools (headless cursor has no inline ingest).
            async def _upload_writer(filename: str, data: bytes) -> str:
                return await materialize(
                    host, host.workdir, filename, data,
                    hook_ctx=hook_ctx, on_upload=config.on_upload,
                )
            ctx.register_upload_writer(_upload_writer)

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

            # Model picker options: prefer the ACP session block captured at
            # bootstrap (authed, exact ids set_model accepts), else
            # `cursor-agent models` (auth-gated), else a static list. The
            # availability probe runs HERE — before the listener starts — so its
            # throwaway "capital of Hungary" turns never reach the widget.
            model_list = await cursor_models.fetch_available_models(
                conversation.session_models, host=host, cursor_path=cursor_path,
            )
            if config.show_session_controls and model_list.get("models"):
                model_list["models"] = await _probe_or_cached_models(
                    ctx, conversation, model_list["models"], host=host,
                    seed_id=model_probe.probe_cache_key(
                        resolved_seed_id, config.seed_id,
                    ),
                    resuming=resuming,
                )

            conv_listener = ConversationListener(
                conversation, password=listener_password,
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
            # default_model overrides the picker's initial value; otherwise
            # the live current model is shown (model_list was built + probed
            # above, before the listener started).
            current_model = config.default_model or model_list.get("default")
            # The model picker is now the engine-neutral id="model" session
            # control (probed catalogue → disabled ControlOptions carry
            # whyDisabled for plan-gated ids). Serialized camelCase for the UI.
            control = model_control(models=model_list["models"], current=current_model)
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
                "protocol": "cursor",
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

            # Resume history backfill: the restored workdir carried cursor's
            # PRIOR on-disk ACP session, but this run's bootstrap minted a FRESH
            # session/new, so the listener's replay buffer starts empty — a viewer
            # attaching post-resume would see only new turns. Now that
            # ConversationListener above has subscribed to conversation.on_event
            # (in its constructor), session/load the prior conversation: cursor
            # replays it via session/update notifications, which flow through the
            # SAME on_event fan-out into the replay buffer; a late viewer then
            # reconstructs the full prior history. When the snapshot persisted the
            # prior ACP session id (resume_session_id), replay_history loads it
            # DIRECTLY; otherwise (old pre-seam rows) it falls back to the
            # session/list + most-recent heuristic. ORDERING is load-bearing:
            # strictly AFTER the listener subscribes (else the buffer misses the
            # replay) and BEFORE the resume-notice send below (which continues the
            # now-loaded thread). Gated on resuming — a fresh session has no prior
            # conversation to backfill. Graceful: an empty list or a load error
            # keeps the fresh session, so resume never breaks (just no history).
            # Resume backfill window. Everything emitted between begin/end_replay
            # lands in the listener's DURABLE tier (never evicted by the live
            # agent_thought_chunk flood), so a late-reconnecting viewer still sees
            # the full prior conversation. The window wraps BOTH the session/load
            # replay AND the injected resume notice; drain() flushes the async
            # on_event dispatch so none of them leak into the live ring.
            if resuming:
                conv_listener.begin_replay()
                loaded = await conversation.replay_history(resume_session_id)
                if loaded:
                    _LOG.info(
                        "cursor resume: session/load replayed prior history (%s)",
                        "persisted id" if resume_session_id else "via session/list",
                    )
                else:
                    _LOG.info(
                        "cursor resume: no prior session found; starting fresh",
                    )

                # Replay→live boundary: the resume notice is sent as a LIVE turn
                # below, and cursor echoes user turns as user_message_chunk ONLY
                # during a session/load replay, never live (wire-confirmed). So
                # inject the user_message_chunk the shared reducer's boundary
                # branch consumes — AFTER replay (a pending last-replayed bubble
                # exists to finalize) and BEFORE the send below. It finalizes the
                # pending bubble (un-merge), bumps the turn (resume answer opens a
                # fresh bubble) and renders the notice as a muted activity row.
                conversation.emit_event({
                    "jsonrpc": "2.0", "method": "session/update",
                    "params": {"sessionId": loaded, "update": {
                        "sessionUpdate": "user_message_chunk",
                        "content": {
                            "type": "text",
                            "text": f"{SYSTEM_MESSAGE_PREFIX}{RESUME_NOTICE}",
                        }}},
                })
                # Flush the async on_event dispatch of both the replay and the
                # injected notice before closing the window, so none leaks into
                # the live ring (which would lose it to the thought-chunk flood).
                await conversation.drain()
                conv_listener.end_replay()

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
        # Share the iframe-input lock so a system send never interleaves with a
        # concurrent operator injection into the same tmux TUI.
        async with injection_lock:
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
        # Cursor authenticates with a SINGLE-USE rotating refresh token. If
        # cursor rotated it this session, that new auth.json must reach the seed
        # via the backstop below — but an aggressive SIGKILL can beat cursor's
        # flush, stranding the rotation (the seed keeps the now-spent token →
        # the next launch demands re-auth). So when a SEED is in use, tear
        # cursor down GRACEFULLY (SIGTERM + wait, ≤5s) even on cancel, giving it
        # time to persist auth.json before the final save-back reads it. Only a
        # non-seeded session keeps the fast aggressive kill on cancel.
        cursor_aggressive = _teardown_aggressive(
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
        # Conversation mode has no tmux/ttyd tree — terminate the cursor-agent
        # subprocess directly. Its EOF drives the conversation to closed.
        if config.mode == "conversation" and launched_handle is not None:
            try:
                await host.terminate_subprocess(launched_handle, aggressive=cursor_aggressive)
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
                    aggressive=cursor_aggressive,
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
                    # Conversation mode: persist the live ACP session id (the
                    # adopted-prior id after a replay, else the fresh session/new
                    # id) so the next resume can session/load + replay it
                    # directly. iframe mode has no conversation → None (plain
                    # --continue restore, session/list heuristic on resume).
                    session_id=(
                        conversation.session_id if conversation is not None else None
                    ),
                )
            except Exception:
                _LOG.exception(
                    "snapshot capture failed; proceeding with workdir wipe",
                )

        try:
            # Remove the short CURSOR_DATA_DIR symlink (it lives outside the
            # workdir, so cleanup_taskdir won't reap it). Best-effort.
            await host.run_command(
                f"rm -f {shlex.quote(host_actions._cursor_data_dir(host.workdir))}"
            )
        except Exception:
            _LOG.exception("cursor data-dir symlink cleanup failed")
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
    session_id: str | None = None,
) -> None:
    """Capture a single-blob resume snapshot of the (now static) workdir.

    Cursor's chat store lives under ``home/.cursor`` INSIDE the workdir, so
    one plaintext workdir tar carries everything ``--continue`` needs — no
    separate session blob (unlike optio-claudecode) and no defensive home
    wipe. Streams the tar into GridFS, records the snapshot row, prunes to
    the retention limit (deleting stale blobs), and surfaces the Resume
    affordance.

    ``session_id`` is cursor's live ACP session id (conversation mode); it rides
    the snapshot row so a later resume can ``session/load`` it directly to replay
    prior history. ``None`` for iframe mode.
    """
    async with ctx.store_blob("workdir") as wwriter:
        async for chunk in host.archive_workdir(
            effective_workdir_exclude(workdir_exclude)
        ):
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

    # iframe → the ttyd TUI widget WITH an operator input box (iframe-input): a
    # textarea to type messages + on-screen NAV keys to drive cursor's TUI menus,
    # reached via the control upstream. Conversation mode carries the live chat
    # widget only when conversation_ui is on (Group 6b); otherwise no widget
    # (the published Conversation is driven programmatically).
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
