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
import inspect
import logging
import mimetypes
import os
import secrets
import shlex
import time as _time
from typing import AsyncIterator, Callable

from optio_core.context import ProcessContext
from optio_core.models import BasicAuth, TaskInstance

from optio_agents import HookContext, RESUME_NOTICE, SYSTEM_MESSAGE_PREFIX, claustrum, get_protocol
from optio_agents import seeds as _seeds
from optio_agents.input_listener import serialized, start_input_listener
from optio_agents.session_controls import model_control
from optio_agents.uploads import materialize, upload_url_token
from optio_agents.protocol.session import _SessionFailed, run_log_protocol_session
from optio_host.host import Host, LocalHost, ProcessHandle, proc_wait
from optio_host.paths import task_dir

from optio_grok import cred_watcher, host_actions
from optio_grok import models as grok_models
from optio_grok.conversation import GrokConversation
from optio_grok.info import AGENT_INFO
from optio_grok.conversation_listener import ConversationListener
from optio_grok.prompt import compose_agents_md
from optio_grok.seed_manifest import GROK_SEED_MANIFEST, GROK_SEED_SUFFIX
from optio_grok.snapshots import (
    insert_snapshot,
    load_latest_snapshot,
    prune_snapshots,
)
from optio_grok.types import GrokTaskConfig


_LOG = logging.getLogger(__name__)

# Cancel/capture step tracing — shares the optio_core.cancel_trace logger and
# the OPTIO_CANCEL_TRACE env gate so its lines interleave with the executor's
# and host's cancel trace into one monotonic-timestamped teardown timeline (run
# with OPTIO_CANCEL_TRACE=1). Diagnostic only; no behavioral effect.
_trace_logger = logging.getLogger("optio_core.cancel_trace")
_CANCEL_TRACE = os.environ.get("OPTIO_CANCEL_TRACE", "0").lower() in ("1", "true", "yes")


def _trace(fmt: str, *args: object) -> None:
    if _CANCEL_TRACE:
        _trace_logger.warning("[%.3f] optio-grok " + fmt, _time.monotonic(), *args)


READY_TIMEOUT_S = 30.0


async def _call_maybe_async(fn, *args) -> None:
    """Invoke a callback that may be sync or async."""
    result = fn(*args)
    if inspect.isawaitable(result):
        await result


def _teardown_aggressive(*, cancelled: bool, seeded: bool) -> bool:
    """Whether to SIGKILL grok immediately on teardown vs SIGTERM-and-wait.

    A **seeded** session is torn down GRACEFULLY even on cancel: grok's
    single-use refresh token may have rotated this session, and grok's auth.json
    write is best-effort — an aggressive SIGKILL can beat the flush, stranding
    the rotation so the credential save-back persists the now-spent token and the
    next launch demands re-auth. SIGTERM-and-wait lets grok flush first. A
    non-seeded session keeps the fast aggressive kill on cancel.
    """
    return cancelled and not seeded


def _build_host(config: GrokTaskConfig, process_id: str) -> Host:
    """Construct the appropriate Host for the given config.

    Extracted so tests can monkeypatch ``session._build_host`` to inject a
    fake host (mirrors the claudecode/opencode pattern). Delegates to
    host_actions.build_host (shared with verify)."""
    taskdir = task_dir(
        ssh=config.ssh, process_id=process_id, consumer_name="optio-grok",
    )
    return host_actions.build_host(config.ssh, taskdir)


async def _build_claustrum_wrap(
    host: Host, config: GrokTaskConfig, claustrum_path: str | None,
) -> list[str] | None:
    """claustrum argv prefix for an fs-isolated launch, or None when
    fs_isolation is off. Shared by the iframe and conversation launch paths so
    both confine grok + its whole subprocess tree identically.

    Grants: the whole per-task ``<workdir>`` (rwx — covers the isolated
    ``home`` GROK_HOME/XDG tree) plus the grok binary cache (rox — the real
    binary the ``<workdir>/home/.local/bin/grok`` symlink targets, outside the
    workdir; grok cannot exec itself without it), on top of the shared system
    baseline. ``~/`` caller extras expand against the REAL host home (grok runs
    under an isolated $HOME, and grants reach claustrum verbatim — no shell
    between).
    """
    if not config.fs_isolation:
        return None
    from optio_agents import fs_grants
    cache_dir = await host_actions._resolve_grok_cache_dir(host, config.install_dir)
    host_home = (
        await host.resolve_host_home() if config.extra_allowed_dirs else None
    )
    grants = fs_grants.build_grant_flags(
        workdir=host.workdir,
        engine_cache_dir=cache_dir,
        extra_allowed_dirs=config.extra_allowed_dirs,
        host_home=host_home,
    )
    return claustrum.build_claustrum_wrap(claustrum_path, grants)


async def run_grok_session(ctx: ProcessContext, config: GrokTaskConfig) -> None:
    """Execute function body for one optio-grok task instance."""
    host: Host = _build_host(config, ctx.process_id)
    # ``redirect``: plant capture-variant browser shims (fake xdg-open/gio/open on
    # PATH + BROWSER env) so Grok Build's first-launch loopback OAuth login — which
    # shells out via the Rust ``webbrowser`` crate ($BROWSER first, then xdg-open)
    # — is intercepted. The shim writes ``BROWSER: <url>`` to optio.log; the tail
    # parser turns it into a browser-open request surfaced to the operator (who
    # completes the 127.0.0.1 loopback in their own browser — works in local mode;
    # remote uses seeds/device-auth per the design). ``suppress`` (the old value)
    # silently no-op'd the launch, so login just vanished with no feedback.
    protocol = get_protocol(
        browser="redirect",
        client_messages=config.use_client_messages,
        caller_messages=config.on_caller_message is not None,
    )
    launched_handle: ProcessHandle | None = None
    tmux_path: str | None = None
    tmux_socket: str | None = None
    tmux_session: str | None = None
    grok_path: str | None = None
    ttyd_path: str | None = None
    cancelled = False
    # Whether a snapshot was restored this run (drives --continue + the
    # auto-start suppression). Set by _prepare, read by the body + teardown.
    resuming = False
    pass_continue = False
    # Persisted ACP session id restored from the resume snapshot (conversation
    # mode). Threaded into the deferred session/load replay below; None when not
    # resuming, in iframe mode, or when the prior snapshot recorded no id.
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
    # by _prepare when fs_isolation is on; read by both bodies to wrap the grok
    # launch. ``claustrum_newer`` is the newest available claustrum tag when it
    # is ahead of the pinned one (drives the pre-launch update notice), else
    # None. Both stay None when fs_isolation is off.
    claustrum_path: str | None = None
    claustrum_newer: str | None = None
    # iframe-input widget: an engine-side HTTP listener injects operator input
    # (typed messages + NAV keystrokes) into the grok tmux TUI. One lock serializes
    # human input against the system (_agent_sender) sends so bursts never interleave.
    input_runner = None
    injection_lock = asyncio.Lock()

    await host.connect()

    async def _prepare(host: Host, hook_ctx: HookContext) -> None:
        """Resolve grok + ttyd, restore a resume snapshot, and plant AGENTS.md.

        Handed to run_log_protocol_session, which runs it AFTER
        host.setup_workdir() has wiped the workdir and BEFORE it subscribes
        the optio.log tail. That ordering is why restore belongs here: the
        restored optio.log is rotated away below before the tail can replay
        its stale DONE/ERROR, and AGENTS.md is (re)planted AFTER the restore
        so it is not wiped by it.
        """
        nonlocal grok_path, ttyd_path, resuming, pass_continue, resolved_seed_id
        nonlocal lease_holder, cred_baseline, resume_session_id
        nonlocal claustrum_path, claustrum_newer
        grok_path = await host_actions.ensure_grok_installed(
            hook_ctx,
            install_if_missing=config.install_if_missing,
            install_dir=config.install_dir,
        )
        # Filesystem isolation: provision the claustrum Landlock binary BEFORE
        # launch. Any failure raises here — fail-closed: the task never proceeds
        # to an unconfined launch. Skipped when fs_isolation is off. The pinned
        # tag drives the pre-launch "newer claustrum available" notice below.
        claustrum_path = None
        claustrum_newer = None
        if config.fs_isolation:
            claustrum_path = await host_actions.ensure_claustrum_installed(
                hook_ctx, install_dir=config.install_dir,
            )
            claustrum_newer = await host_actions.claustrum_newer_tag()
        # Conversation mode is headless (grok agent stdio) — no ttyd needed.
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
            # Restore the workdir tar (carries home/.grok, i.e. grok's session
            # store). A present snapshot that fails to restore is fatal — the
            # restore call is intentionally outside any except so it surfaces
            # to the caller (no silent fresh-start). When session-blob encryption
            # is configured, the stored tar is decrypted whole before restore
            # (see _restore_workdir_stream); plaintext streams chunk-by-chunk.
            await host.restore_workdir(
                _restore_workdir_stream(
                    ctx, snapshot["workdirBlobId"], config.session_blob_decrypt,
                )
            )
            # The grok launch symlink (home/.local/bin/grok) lives INSIDE the
            # workdir and was wiped by the restore; re-establish it against the
            # cache (which lives OUTSIDE the workdir and survives). Idempotent:
            # cache hit → just relinks, no reinstall/redownload. ttyd survives
            # untouched (real host home, outside the workdir).
            grok_path = await host_actions.ensure_grok_installed(
                hook_ctx,
                install_if_missing=config.install_if_missing,
                install_dir=config.install_dir,
                progress_label=f"Restoring {AGENT_INFO.name} runtime…",
                # The ensure_grok_installed call at the top of _prepare already
                # ran the version-check/refresh for this resume; skip the second
                # network probe — this call only needs to re-link the symlink the
                # restore wiped.
                check_update=False,
            )
            await host_actions._rotate_optio_log(host)
            # A restored snapshot means grok persisted a session for this cwd;
            # --continue resumes the most recent one. GROK_HOME lives inside the
            # restored workdir at the same absolute path (deterministic taskdir),
            # so the cwd-keyed session lookup matches.
            pass_continue = True
            # Conversation mode: the ACP session id the fresh grok stored last
            # run (None for iframe snapshots or pre-seam rows). Handed to the
            # deferred session/load replay in _conversation_body so grok re-emits
            # the prior conversation into the listener's replay buffer.
            resume_session_id = snapshot.get("sessionId")

        if not resuming and config.seed_id is not None:
            # Seeded FRESH start: resolve the seed id (str → itself; a
            # SeedProvider callable → awaited, may raise SeedUnavailableError)
            # and overlay the stored grok identity (auth.json + config.toml)
            # into the fresh workdir BEFORE AGENTS.md, so grok launches
            # already-authed. No --continue: this begins a NEW session. Grok
            # auth/config are cwd-independent, so no rekey is needed.
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
                manifest=GROK_SEED_MANIFEST,
                suffix=GROK_SEED_SUFFIX,
                decrypt=None,
            )
            # Baseline the merged auth.json so the in-session watcher and the
            # teardown backstop only save back a genuinely rotated token.
            cred_baseline = await cred_watcher.cred_fingerprint(host)

        # Route the "a newer claustrum release is available" notice through
        # on_deliverable (a security feature: a new release may patch a
        # vulnerability the operator must hear about ASAP), then clean it up.
        # No-op when fs_isolation is off, on_deliverable is None, or the cache
        # is already at the newest tag.
        if config.fs_isolation and claustrum_newer:
            await claustrum.emit_claustrum_update_notice(
                host, hook_ctx,
                delivery_type=config.delivery_type,
                on_deliverable=config.on_deliverable,
                newer=claustrum_newer,
                pinned=claustrum.CLAUSTRUM_PINNED_TAG,
            )

        # Force `[cli] auto_update = false` in the per-task GROK_HOME config so
        # grok never self-downloads a fresh ~150 MB binary into
        # home/.grok/downloads mid-session — that bloats the resume snapshot so
        # capture_snapshot overruns the cancel grace and the task force-fails.
        # After any restore/seed so it overrides a config.toml they carried;
        # runs for fresh, seeded, and resumed launches alike.
        await host_actions.write_grok_config(host, host.workdir)

        refreshed_files: list[str] = []
        if resuming:
            # Resume: AGENTS.md was restored from the snapshot workdir tar.
            # Recompose it from the (optionally hook-mutated) config and write
            # back only when it differs, so a resumed session picks up
            # instruction/template changes; the rewritten filename tags the
            # resume-log line so the agent re-reads. Parity with claudecode.
            refreshed_files = await _maybe_refresh_on_resume(host, hook_ctx, config)
        else:
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
            await host_actions._append_resume_log_entry(host, refreshed=refreshed_files)
        if config.before_execute is not None:
            await config.before_execute(hook_ctx)

    async def _grok_body(host: Host, hook_ctx: HookContext) -> None:
        nonlocal launched_handle, tmux_path, tmux_socket, tmux_session
        nonlocal cred_watch_task, input_runner

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
            resuming=pass_continue,
        )
        grok_flags = [
            *grok_flags,
            *host_actions.build_auto_start_args(
                auto_start=config.auto_start, resuming=resuming,
            ),
            # PUSH resume awareness: a System: notice appended after -c so the
            # resumed TUI session gets a "you have been resumed" turn (mutually
            # exclusive with the fresh-launch kickoff above). Parity with
            # claudecode/opencode; resume.log stays the pull-based backstop.
            *host_actions.build_resume_notice_args(resuming=resuming),
        ]
        launch_env = {
            **(config.env or {}),
            **(hook_ctx.browser_launch_env or {}),
        }
        ctx.report_progress(None, f"Launching {AGENT_INFO.name}…")
        # Fs-isolation wrap: confine grok + its whole tool/subprocess tree
        # inside the tmux pane (None when fs_isolation is off).
        claustrum_wrap = await _build_claustrum_wrap(host, config, claustrum_path)
        handle, ttyd_port, tmux_socket, tmux_session = await host_actions.launch_ttyd_with_grok(
            host,
            ttyd_path=ttyd_path,
            grok_path=grok_path,
            bind_iface=ttyd_iface,
            extra_env=launch_env,
            grok_flags=grok_flags,
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
        ctx.report_progress(None, f"{AGENT_INFO.name} is live")

        # iframe-input widget: start the engine-side input listener and publish it
        # as the control upstream. The operator types messages / drives TUI menus
        # via /api/widget-control. Both the human path and the system _agent_sender
        # share ``injection_lock`` so tmux injection bursts never interleave.
        async def _human_input(text: str) -> None:
            await host_actions.send_text_to_grok(
                host, tmux_path, tmux_socket, tmux_session, text,
            )

        async def _human_key(key: str) -> None:
            await host_actions.send_key_to_grok(
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

        # Await the grok process inside tmux (NOT the ttyd connection). ttyd
        # stays up serving viewers; the task is alive while the tmux session
        # is. The protocol driver cancels this body when it sees DONE/ERROR in
        # optio.log; if grok exits some other way, has-session goes false and
        # the body returns -> driver treats it as premature exit.
        while ctx.should_continue() and await host_actions.tmux_session_alive(
            host, tmux_path, tmux_socket, tmux_session,
        ):
            await asyncio.sleep(1.0)

    conversation: GrokConversation | None = None
    if config.mode == "conversation":
        conversation = GrokConversation(
            cwd=host.workdir, permission_gate=config.permission_gate,
        )
    # Per-task conversation listener (conversation_ui only). Started in the
    # body after publish_result, torn down in the finally block.
    conv_listener: ConversationListener | None = None

    async def _conversation_body(host: Host, hook_ctx: HookContext) -> None:
        nonlocal launched_handle, cred_watch_task, conv_listener

        # Launch `grok agent [--model M] [--always-approve] --no-leader stdio`
        # directly (no tmux/ttyd). --always-approve is used only when no
        # permission gate is wired, so grok never blocks on a prompt nobody
        # answers; with the gate on, grok surfaces session/request_permission
        # and the published conversation's handler answers it.
        argv = host_actions.build_conversation_argv(
            grok_path,
            model=config.model,
            reasoning_effort=config.reasoning_effort,
            no_leader=config.no_leader,
            always_approve=not config.permission_gate,
        )
        # Same claustrum fs-isolation wrap as the iframe path; claustrum
        # execve's grok, so the bidirectional ACP stdio pipes pass through
        # unchanged (None when fs_isolation is off).
        wrap = await _build_claustrum_wrap(host, config, claustrum_path)
        if wrap:
            argv = [*wrap, *argv]
        # `exec` so /bin/sh REPLACES itself with the (claustrum →) grok process
        # instead of forking it. launch_subprocess starts the sh with
        # start_new_session=True, making sh the session leader; exec transfers
        # that leadership straight into the chain, and claustrum execve's grok in
        # place — so grok IS the session leader in the launcher's process group,
        # the pgid optio's killpg teardown targets. (claustrum, unlike grok's old
        # fail-closed native profile, needs no controlling /dev/tty, so there is
        # no forked setsid tty wrapper to escape the pgid.)
        cmd = "exec " + " ".join(shlex.quote(a) for a in argv)
        # Same per-task HOME/GROK_HOME/XDG isolation as the iframe launch. PATH
        # is inherited by launch_subprocess (os.environ.copy) so interpreters
        # resolve. merge_stderr=False keeps grok's diagnostics off the JSON-RPC
        # stdout stream.
        env = {
            **host_actions._isolation_env(host.workdir),
            **(config.env or {}),
            **(hook_ctx.browser_launch_env or {}),
        }
        ctx.report_progress(None, f"Launching {AGENT_INFO.name} (conversation)…")
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
        ctx.report_progress(None, f"{AGENT_INFO.name} conversation is live")

        # Opt-in dashboard chat widget: start a per-task SSE listener over the
        # published conversation and publish it as the "conversation" widget via
        # the widget proxy (which injects the basic-auth credential).
        if config.conversation_ui:
            listener_password = secrets.token_urlsafe(32)
            bind_addr = os.environ.get("OPTIO_WIDGET_TUNNEL_BIND", "127.0.0.1")
            upstream_host = os.environ.get("OPTIO_WIDGET_TUNNEL_HOST", "127.0.0.1")
            # File upload flows through the generic optio-api /api/widget-upload
            # route → materializeUpload RPC → this per-task writer, which runs in
            # THIS process (only it holds the live Host). The writer lands the
            # bytes under <workdir>/uploads/<name> and fires config.on_upload; the
            # view injects a System: path reference so grok reads them with its
            # own tools (headless grok has no inline ingest).
            async def _upload_writer(filename: str, data: bytes) -> str:
                return await materialize(
                    host, host.workdir, filename, data,
                    hook_ctx=hook_ctx, on_upload=config.on_upload,
                )
            ctx.register_upload_writer(_upload_writer)

            # File download: serve workdir-confined bytes for the optio-file:
            # sentinel links grok emits. realpath guards against ../ escapes.
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
            # bootstrap (authed, exact ids set_model accepts), else `grok
            # models`, else a static list. config.model (which also drives the
            # launch --model flag) preselects the picker's initial value;
            # otherwise the live current model is shown.
            model_list = await grok_models.fetch_available_models(
                conversation.session_models, host=host, grok_path=grok_path,
            )
            current_model = config.model or model_list.get("default")

            def _model_entry(model_id: "str | None") -> "dict | None":
                for m in model_list["models"]:
                    if m["id"] == model_id:
                        return m
                return None

            def _build_controls(model_id: "str | None") -> list[dict]:
                """Derive the engine-neutral session-controls list for a given
                current model: just the id="model" select.

                NO live id="reasoning_effort" slider is surfaced. grok's ACP does
                not advertise per-model reasoning-effort capability — the
                session/new model block's per-model ``_meta`` carries only
                {totalContextTokens, agentType} (see models.parse_acp_models), so
                there is no reachable capability source. reasoning_effort stays a
                launch-only knob (``--reasoning-effort``; see
                host_actions.build_conversation_argv)."""
                return [
                    model_control(
                        models=model_list["models"], current=model_id,
                    ).to_dict()
                ]

            # Re-derive + re-emit the control set after a model change. (Today
            # this is just the id="model" select; grok exposes no per-model live
            # controls, so the snapshot is effectively idempotent.)
            conversation.set_controls_builder(_build_controls)
            # widgetData.uploadUrl token; see optio_agents.uploads.upload_url_token.
            upload_url = upload_url_token(ctx._db.name, ctx._prefix, ctx.process_id)
            await ctx.set_widget_data({
                "protocol": "grok",
                "toolVerbosity": config.tool_verbosity,
                "thinkingVerbosity": config.thinking_verbosity,
                "showSessionControls": config.show_session_controls,
                "nativeSpinner": config.native_spinner,
                "controls": _build_controls(current_model),
                "showFileUpload": config.show_file_upload,
                "maxUploadBytes": config.max_upload_bytes,
                "fileDownload": config.file_download,
                "maxDownloadBytes": config.max_download_bytes,
                "uploadUrl": upload_url,
            })
            ctx.report_progress(None, "Conversation UI is live")

            # Resume history replay: bootstrap() minted a FRESH session via
            # session/new, so the listener's replay buffer starts empty — a
            # viewer attaching after resume would see only NEW turns. grok
            # advertises loadSession (and does NOT advertise sessionCapabilities
            # .list, so there is no list-based rediscovery — we persist the id
            # ourselves), so now that ConversationListener above has subscribed
            # to conversation.on_event (in its constructor), load the PRIOR ACP
            # session by its persisted id: grok re-emits the whole conversation
            # as session/update notifications through the SAME on_event fan-out
            # so every historic turn lands in the replay buffer, AND adopts that
            # session so new turns continue the prior thread. ORDERING is
            # load-bearing: strictly AFTER the listener subscribes (else the
            # replayed updates are dropped — the dispatcher fans to zero
            # handlers) and BEFORE the resume-notice send below (which then goes
            # to the loaded session). Gated on resuming + a persisted id; on ANY
            # failure (unknown id, no loadable store after restore, capability
            # mismatch) the fresh session/new session is kept — resume must
            # never break, it just shows no history.
            # Resume backfill window. Everything emitted between begin/end_replay
            # lands in the listener's DURABLE tier (never evicted by the live
            # agent_thought_chunk flood), so a late-reconnecting viewer still sees
            # the full prior conversation. The window wraps BOTH the session/load
            # replay AND the injected resume notice; drain() flushes the async
            # on_event dispatch so none of them leak into the live ring.
            if resuming:
                conv_listener.begin_replay()
                if resume_session_id:
                    ok, detail = await conversation.replay_history(resume_session_id)
                    if ok:
                        _LOG.info("grok resume: session/load replayed prior history")
                    else:
                        _LOG.info(
                            "grok resume: session/load unavailable (%s); starting "
                            "fresh session", detail,
                        )
                else:
                    _LOG.info(
                        "grok resume: no persisted session id; starting fresh session",
                    )

                # Replay→live boundary: the resume notice is sent as a LIVE turn
                # below, and grok echoes user turns as user_message_chunk ONLY
                # during a session/load replay, never live. So inject the
                # user_message_chunk the shared reducer's boundary branch consumes
                # — AFTER replay (a pending last-replayed bubble exists to
                # finalize) and BEFORE the send below. It finalizes the pending
                # bubble (un-merge), bumps the turn (resume answer opens a fresh
                # bubble) and renders the notice as a muted activity row.
                conversation.emit_event({
                    "jsonrpc": "2.0", "method": "session/update",
                    "params": {"sessionId": resume_session_id, "update": {
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

        # Kickoff prompt as the first turn (headless: no positional prompt path).
        # On resume, push a System: resume notice instead so the resumed session
        # notices promptly (parity with claudecode/opencode; resume.log stays the
        # pull-based backstop). grok always teaches the System: convention, so no
        # host_protocol gate is needed here (unlike claudecode).
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
                        # The keyword driver treats a body return without DONE as
                        # a premature exit; a caller-requested close IS the clean
                        # end, so emit DONE ourselves and park until the driver
                        # observes it and cancels this body.
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
                    raise RuntimeError(f"grok exited unexpectedly (exit {rc})")
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
            await host_actions.send_text_to_grok(
                host, tmux_path, tmux_socket, tmux_session, message,
            )

    body = _conversation_body if config.mode == "conversation" else _grok_body
    try:
        await run_log_protocol_session(
            host, ctx,
            body=body,
            prepare=_prepare,
            on_deliverable=config.on_deliverable,
            on_caller_message=config.on_caller_message,
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
        _trace(
            "finally: ENTER cancelled=%s resuming=%s seeded=%s conversation=%s",
            cancelled, resuming, resolved_seed_id is not None,
            config.mode == "conversation",
        )
        # Grok authenticates with a SINGLE-USE rotating refresh token. If grok
        # rotated it this session, that new auth.json must reach the seed via the
        # backstop below — but an aggressive SIGKILL can beat grok's flush,
        # stranding the rotation (the seed keeps the now-spent token → the next
        # launch demands re-auth). So when a SEED is in use, tear grok down
        # GRACEFULLY (SIGTERM + wait, ≤5s) even on cancel, giving it time to
        # persist auth.json before the final save-back reads it. Only a
        # non-seeded session keeps the fast aggressive kill on cancel.
        grok_aggressive = _teardown_aggressive(
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
            _trace("finally: conv_listener.stop START")
            try:
                await conv_listener.stop()
            except Exception:
                _LOG.exception("conversation listener cleanup failed")
            _trace("finally: conv_listener.stop DONE")
        # Conversation mode has no tmux/ttyd tree — terminate the grok agent
        # subprocess directly. Its EOF drives the conversation to closed.
        if config.mode == "conversation" and launched_handle is not None:
            _trace(
                "finally: terminate_subprocess START aggressive=%s", grok_aggressive,
            )
            try:
                await host.terminate_subprocess(launched_handle, aggressive=grok_aggressive)
            except Exception:
                _LOG.exception("terminate grok conversation subprocess failed")
            _trace("finally: terminate_subprocess DONE")
        if (
            launched_handle is not None
            and tmux_path is not None
            and tmux_socket is not None
            and tmux_session is not None
            and grok_path
        ):
            _trace("finally: teardown_session_tree START aggressive=%s", grok_aggressive)
            try:
                await host_actions.teardown_session_tree(
                    host,
                    tmux_path=tmux_path,
                    tmux_socket=tmux_socket,
                    tmux_session=tmux_session,
                    grok_path=grok_path,
                    ttyd_handle=launched_handle,
                    aggressive=grok_aggressive,
                )
            except Exception:
                _LOG.exception("teardown_session_tree failed")
            _trace("finally: teardown_session_tree DONE")

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

        # Final backstop save-back — LOAD-BEARING, not defensive: grok's own
        # auth write-back is best-effort and the xAI provider has already
        # consumed the old refresh token; a rotation in the last poll window is
        # persisted ONLY here. Runs after grok terminated so auth.json is final.
        if resolved_seed_id is not None:
            _trace("finally: save_back_if_changed START")
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
            _trace("finally: save_back_if_changed DONE")

        # Release the lease AFTER the final save-back (opencode's deliberate
        # ordering): a new acquirer must never merge the pre-save-back blob.
        if lease_holder is not None and resolved_seed_id is not None:
            try:
                await _seeds.release(
                    ctx._db, prefix=ctx._prefix, suffix=GROK_SEED_SUFFIX,
                    seed_id=resolved_seed_id, holder=lease_holder,
                )
            except Exception:
                _LOG.exception("lease release failed (TTL will reclaim)")

        # Seed capture (fresh only): store this session's grok identity as a
        # reusable seed so a later fresh task can start already-authed. Same
        # reached-live gate as snapshots (launched_handle assigned strictly
        # after a successful launch). Guarded on auth.json present — never
        # seed a login-less identity. Ignored on resume.
        if (
            not resuming
            and config.on_seed_saved is not None
            and launched_handle is not None
        ):
            try:
                if not await cred_watcher.capture_gate_ok(host):
                    _trace("finally: capture_seed SKIPPED (no auth.json)")
                    _LOG.warning(
                        "seed capture skipped: home/.grok/auth.json absent or "
                        "invalid (login-less session)",
                    )
                else:
                    _trace("finally: capture_seed START")
                    seed_id = await _seeds.capture_seed(
                        ctx, host,
                        manifest=GROK_SEED_MANIFEST,
                        suffix=GROK_SEED_SUFFIX,
                        encrypt=None,
                    )
                    # 2nd arg (account summary) is resolved in a later stage;
                    # None in Stage 3.
                    await _call_maybe_async(config.on_seed_saved, seed_id, None)
                    _trace("finally: capture_seed DONE id=%s", seed_id)
            except Exception:
                _trace("finally: capture_seed RAISED")
                _LOG.exception(
                    "seed capture failed; callback not fired, teardown continues",
                )

        # Reached-live gate: only capture if grok actually came up
        # (launched_handle is assigned strictly after a successful ttyd/grok
        # launch). An interrupt before launch leaves it None — skip capture so
        # any prior good snapshot survives and hasSavedState is untouched.
        if config.supports_resume and launched_handle is not None:
            _trace("finally: capture_snapshot START end_state=%s",
                   "cancelled" if cancelled else "done")
            try:
                await _capture_snapshot(
                    ctx, host,
                    end_state="cancelled" if cancelled else "done",
                    workdir_exclude=config.workdir_exclude,
                    session_blob_encrypt=config.session_blob_encrypt,
                    # Conversation mode: persist the live ACP session id (the
                    # adopted-prior id after a replay, else the fresh session/new
                    # id) so the next resume can session/load + replay it. iframe
                    # mode has no conversation → None (plain --continue restore).
                    session_id=(
                        conversation.session_id if conversation is not None else None
                    ),
                )
                _trace("finally: capture_snapshot DONE")
            except Exception:
                _trace("finally: capture_snapshot RAISED")
                _LOG.exception(
                    "snapshot capture failed; proceeding with workdir wipe",
                )

        _trace("finally: cleanup_taskdir START aggressive=%s", cancelled)
        try:
            await host.cleanup_taskdir(aggressive=cancelled)
        except Exception:
            _LOG.exception("cleanup_taskdir failed")
        _trace("finally: cleanup_taskdir DONE")
        try:
            await host.disconnect()
        except Exception:
            _LOG.exception("host.disconnect failed")
        _trace("finally: EXIT (teardown complete)")


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


async def _restore_workdir_stream(
    ctx: ProcessContext,
    blob_id,
    session_blob_decrypt: "Callable[[bytes], bytes] | None",
) -> "AsyncIterator[bytes]":
    """Yield the workdir tar bytes for ``restore_workdir``.

    Plaintext (no decrypt): stream chunk-by-chunk so a large tar never buffers
    whole. Encrypted: the transform operates on the WHOLE ciphertext, so read
    the blob fully, decrypt, and yield the plaintext tar in one piece (mirrors
    claudecode's whole-bytes session-blob decrypt)."""
    if session_blob_decrypt is None:
        async for chunk in _stream_blob(ctx, blob_id):
            yield chunk
    else:
        payload = await _read_blob_bytes(ctx, blob_id)
        yield session_blob_decrypt(payload)


async def _capture_snapshot(
    ctx: ProcessContext,
    host: Host,
    *,
    end_state: str,
    workdir_exclude: list[str] | None,
    session_id: str | None = None,
    session_blob_encrypt: "Callable[[bytes], bytes] | None" = None,
) -> None:
    """Capture a single-blob resume snapshot of the (now static) workdir.

    Grok's session store lives under ``home/.grok`` INSIDE the workdir, so one
    plaintext workdir tar carries everything ``--continue`` needs — no separate
    session blob (unlike optio-claudecode) and no defensive home wipe. Streams
    the tar into GridFS, records the snapshot row, prunes to the retention
    limit (deleting stale blobs), and surfaces the Resume affordance.

    ``session_id`` is grok's live ACP session id (conversation mode); it rides
    the snapshot row so a later resume can ``session/load`` it to replay prior
    history. ``None`` for iframe mode.

    ``session_blob_encrypt`` (when set) wraps the workdir tar at rest. Because
    grok's session store lives under home/.grok INSIDE the workdir, this one
    tar IS the session blob, so encrypting it protects the resume state on the
    GridFS side. The transform operates on the WHOLE tar, so the plaintext is
    buffered and encrypted before the single write; the plaintext path keeps
    streaming chunk-by-chunk to avoid buffering a large tar. Decrypted on read
    by ``_restore_workdir_stream``.
    """
    async with ctx.store_blob("workdir") as wwriter:
        if session_blob_encrypt is None:
            async for chunk in host.archive_workdir(workdir_exclude):
                await wwriter.write(chunk)
        else:
            buf = bytearray()
            async for chunk in host.archive_workdir(workdir_exclude):
                buf.extend(chunk)
            await wwriter.write(session_blob_encrypt(bytes(buf)))
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


async def _maybe_refresh_on_resume(
    host: Host, hook_ctx: HookContext, config: GrokTaskConfig,
) -> list[str]:
    """Run ``on_resume_refresh`` (if any) and rewrite AGENTS.md when changed.

    Returns the list of filenames rewritten (currently at most
    ``["AGENTS.md"]``), fed to the resume-log append so the agent re-reads. A
    hook that raises is logged and ignored (the restored AGENTS.md is kept).
    Mirrors optio-claudecode's CLAUDE.md refresh; the instruction file here is
    AGENTS.md, recomposed via ``compose_agents_md``.
    """
    if config.on_resume_refresh is None:
        return []
    try:
        new_config = config.on_resume_refresh(config)
    except Exception:
        _LOG.exception(
            "on_resume_refresh raised; keeping existing AGENTS.md from snapshot",
        )
        return []
    new_agents_md = compose_agents_md(
        new_config.consumer_instructions,
        host_protocol=new_config.host_protocol,
        workdir_exclude=new_config.workdir_exclude,
        supports_resume=new_config.supports_resume,
        file_download=new_config.file_download,
    )
    try:
        existing = await hook_ctx.read_text_from_host("AGENTS.md", silent=True)
    except FileNotFoundError:
        existing = None
    except Exception:
        _LOG.exception(
            "failed to read existing AGENTS.md on resume; rewriting unconditionally",
        )
        existing = None
    if existing == new_agents_md:
        return []
    await host.write_text("AGENTS.md", new_agents_md)
    return ["AGENTS.md"]


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

    # iframe → the ttyd TUI widget WITH an operator input box (iframe-input): a
    # textarea to type messages + on-screen NAV keys to drive grok's TUI menus,
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
