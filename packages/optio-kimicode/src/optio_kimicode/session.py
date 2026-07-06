"""State machine for one optio-kimicode session (iframe/kimi web, local + SSH).

Orchestrates a Host (local or remote) through resolve kimi → plant AGENTS.md →
launch ``kimi server run --foreground`` (the ``kimi web`` surface) → establish a
tunnel → inject the bearer token via the ``#token=`` URL fragment → pre-create a
kimi session and point the iframe at it → optio.log protocol session → teardown.

The iframe surface is driven the **opencode** way (not grok's): kimi web is a
pure web server with no ``--continue`` / no positional prompt, so the wrapper
pre-creates a session over REST (``POST /api/v1/sessions``), points the iframe at
``/sessions/<id>``, and injects agent input (the auto-start kickoff, the resume
notice, and ``_agent_sender`` feedback) via ``POST /api/v1/sessions/<id>/prompts``.

Resume is the two halves of resume-awareness: the PULL half restores the kimi
session store from a snapshot, appends ``resume.log``, and rotates a stale
``optio.log`` out of the way; the PUSH half POSTs ``System: you have been
resumed`` to the recovered session so the agent notices promptly. Seed,
credential-planting, conversation mode, and fs-isolation arrive in later stages.
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
import time as _time

from optio_core.context import ProcessContext
from optio_core.models import BasicAuth, TaskInstance

from optio_agents import HookContext, get_protocol
from optio_agents import RESUME_NOTICE, SYSTEM_MESSAGE_PREFIX
from optio_agents import seeds as _seeds
from optio_agents.protocol.session import _SessionFailed, run_log_protocol_session
from optio_host.host import Host, LocalHost, ProcessHandle, proc_wait
from optio_host.paths import task_dir

from optio_kimicode import cred_watcher, host_actions, verify
from optio_kimicode import models as kimi_models
from optio_kimicode.conversation import KimiCodeConversation
from optio_kimicode.conversation_listener import ConversationListener
from optio_kimicode.prompt import compose_agents_md
from optio_kimicode.seed_manifest import KIMI_SEED_MANIFEST, KIMI_SEED_SUFFIX
from optio_kimicode.snapshots import (
    capture_snapshot,
    load_latest_snapshot,
    restore_snapshot,
)
from optio_kimicode.types import KimiCodeTaskConfig


_LOG = logging.getLogger(__name__)

# Cancel/capture step tracing — shares the optio_core.cancel_trace logger and
# the OPTIO_CANCEL_TRACE env gate so its lines interleave with the executor's
# and host's cancel trace, giving one monotonic-timestamped teardown timeline
# (run with OPTIO_CANCEL_TRACE=1). Diagnostic only; no behavioral effect.
_trace_logger = logging.getLogger("optio_core.cancel_trace")
_CANCEL_TRACE = os.environ.get("OPTIO_CANCEL_TRACE", "0").lower() in ("1", "true", "yes")


def _trace(fmt: str, *args: object) -> None:
    if _CANCEL_TRACE:
        _trace_logger.warning("[%.3f] optio-kimicode " + fmt, _time.monotonic(), *args)


READY_TIMEOUT_S = 30.0

# Fresh-launch kickoff prompt POSTed to the pre-created kimi session so the agent
# starts the task unattended. Suppressed on resume (opencode parity).
AUTO_START_PROMPT = "Read AGENTS.md and execute the task it describes"


async def _call_maybe_async(fn, *args) -> None:
    """Invoke a callback that may be sync or async."""
    result = fn(*args)
    if inspect.isawaitable(result):
        await result


def _teardown_aggressive(*, cancelled: bool, seeded: bool) -> bool:
    """Whether to SIGKILL kimi immediately on teardown vs SIGTERM-and-wait.

    A **seeded** session is torn down GRACEFULLY even on cancel: kimi's
    single-use refresh token may have rotated this session, and kimi's
    credential write is best-effort — an aggressive SIGKILL can beat the flush,
    stranding the rotation so the credential save-back persists the now-spent
    token and the next launch demands re-auth. SIGTERM-and-wait lets kimi flush
    ``kimi-code.json`` first. A non-seeded session keeps the fast aggressive kill
    on cancel.
    """
    return cancelled and not seeded


async def _eof_shutdown(handle, *, timeout: float) -> bool:
    """Graceful shutdown for a conversation (ACP-over-stdio) subprocess: close
    its stdin so kimi sees EOF and exits on its own.

    Verified against the real binary: ``kimi acp`` exits rc=0 in ~20ms on stdin
    EOF (a clean shutdown that flushes its state) — whereas it IGNORES SIGTERM
    for the ENTIRE 5s graceful grace before being SIGKILLed, which both wastes
    the whole cancel budget and risks cutting the credential flush short. So EOF
    is strictly better: faster AND cleaner. Returns True if kimi exited within
    ``timeout`` (the common case), else False — the caller's signal-based
    ``terminate_subprocess`` is the backstop."""
    stdin = getattr(handle, "stdin", None)
    if stdin is not None:
        try:
            stdin.close()
        except Exception:  # noqa: BLE001 — a broken pipe is fine; it means EOF already
            pass
    try:
        await asyncio.wait_for(proc_wait(handle), timeout=timeout)
        return True
    except Exception:  # noqa: BLE001 — TimeoutError or a wait quirk → fall back to signals
        return False


def _build_host(config: KimiCodeTaskConfig, process_id: str) -> Host:
    """Construct the appropriate Host for the given config.

    Extracted so tests can monkeypatch ``session._build_host`` to inject a
    fake host (mirrors the grok/claudecode pattern). Delegates to
    ``host_actions.build_host`` (shared with verify)."""
    taskdir = task_dir(
        ssh=config.ssh, process_id=process_id, consumer_name="optio-kimicode",
    )
    return host_actions.build_host(config.ssh, taskdir)


async def _merge_seed_with_refresh(
    ctx: ProcessContext, host: Host, config: KimiCodeTaskConfig, *, seed_id: str,
) -> None:
    """Refresh the seed's rotating kimi token host-free, THEN merge it.

    kimi's access token is short-lived (~15 min), so a stored seed's
    ``credentials/kimi-code.json`` is almost always EXPIRED by launch time.
    Merging it as-is leaves kimi unauthenticated: ``session/new`` then ships an
    empty model picker (no model → every turn fails silently) or is rejected
    outright with "Authentication required". So first run the host-free
    ``verify_and_refresh_seed`` — a no-op when the token is still fresh, a
    ``refresh_token`` grant (rotating the single-use token in place) when it is
    within kimi's refresh threshold — so the merge below plants a LIVE token.

    Never blocks the launch: a ``False`` result (dead lineage, or a transient
    refresh failure) or a raised refresh is logged, and the existing credential
    is merged anyway — there is no better one to plant, and ``session/new`` will
    surface the auth error. Only the model/refresh network call is made here; no
    kimi process, no inference."""
    try:
        alive = await verify.verify_and_refresh_seed(
            ctx._db,
            prefix=ctx._prefix,
            seed_id=seed_id,
            encrypt=config.session_blob_encrypt,
            decrypt=config.session_blob_decrypt,
        )
    except Exception:  # noqa: BLE001 — a transport bug is inconclusive, not dead
        _LOG.exception("kimi seed %s pre-merge refresh failed", seed_id)
        alive = None
    if alive is False:
        # verify returns False for BOTH a SPOILED seed (spent/revoked refresh
        # token → verify marks pool status "dead") and a transient failure
        # (network → status untouched). A spoiled seed can NEVER launch, so fail
        # now with an actionable message rather than merging a dead credential
        # and letting kimi surface a cryptic 'Authentication required'. A
        # transient failure is inconclusive — merge the existing credential and
        # let session/new surface any error.
        doc = await _seeds.load_seed(
            ctx._db, prefix=ctx._prefix, suffix=KIMI_SEED_SUFFIX, seed_id=seed_id,
        )
        if doc is not None and doc.get("status") == "dead":
            raise RuntimeError(
                f"Kimi Code seed {seed_id} is spoiled: its login token is expired "
                f"or revoked and can no longer be refreshed — a fresh Kimi Code "
                f"login is required to use this seed."
            )
        _LOG.warning(
            "kimi seed %s could not be refreshed (transient); merging the existing "
            "credential — kimi may report an auth error", seed_id,
        )
    await _seeds.merge_seed(
        ctx, host,
        seed_id=seed_id,
        manifest=KIMI_SEED_MANIFEST,
        suffix=KIMI_SEED_SUFFIX,
        decrypt=config.session_blob_decrypt,
    )


async def run_kimicode_session(ctx: ProcessContext, config: KimiCodeTaskConfig) -> None:
    """Execute function body for one optio-kimicode task instance."""
    host: Host = _build_host(config, ctx.process_id)
    # ``redirect``: kimi's first-login device-code URL is surfaced to the
    # operator (no browser interception needed — kimi prints the URL). Parity
    # with grok's protocol wiring; Stage 0 has no auth yet, so this is inert.
    protocol = get_protocol(browser="redirect")
    launched_handle: ProcessHandle | None = None
    cancelled = False
    kimi_path: str | None = None
    # Stage 8: the on-host claustrum binary for the fs-isolation wrap. Resolved
    # in _prepare when config.fs_isolation (fail-closed: provisioning raises
    # rather than launching unconfined); None when isolation is off.
    claustrum_path: str | None = None

    # Set by _prepare (after the workdir wipe, before the optio.log tail); read
    # by the body, _agent_sender, and the teardown finally.
    resuming = False
    preserved_session_id: str | None = None

    # Stage-4 seed lease + credential save-back. ``resolved_seed_id`` is the
    # seed planted for a fresh, seeded launch (str seed_id → itself; a
    # SeedProvider callable → awaited); None on resume / when unseeded.
    # ``lease_holder`` is the task's process_id when the seed came from a
    # lease-holding SeedProvider (renewed by the watcher, released at teardown).
    # ``cred_baseline`` is the post-merge kimi-code.json fingerprint the watcher
    # + backstop diff against; ``cred_watch_task`` is the in-session watcher.
    resolved_seed_id: str | None = None
    lease_holder: str | None = None
    cred_baseline: str | None = None
    cred_watch_task: "asyncio.Task | None" = None

    # Set by the body at launch; read by _agent_sender.
    worker_port: int | None = None
    token: str | None = None
    session_id: str | None = None

    await host.connect()

    async def _prepare(host: Host, hook_ctx: HookContext) -> None:
        """Resolve kimi, restore a resume snapshot, plant AGENTS.md.

        Handed to run_log_protocol_session, which runs it AFTER
        host.setup_workdir() wiped the workdir and BEFORE it subscribes the
        optio.log tail — so the resume restore + optio.log rotation land before
        the tail can re-emit a stale DONE.
        """
        nonlocal kimi_path, claustrum_path, resuming, preserved_session_id
        nonlocal resolved_seed_id, lease_holder, cred_baseline
        # Provision the fork kimi via smart-install --check + optio's own
        # downloader (progress in the UI), into an evictable cache OUTSIDE the
        # workdir; returns the per-task launch symlink
        # ``<workdir>/home/.local/bin/kimi``.
        kimi_path = await host_actions.ensure_kimicode_installed(
            host,
            download=hook_ctx.download_file,
            report_progress=hook_ctx.report_progress,
            install_dir=config.kimi_install_dir,
            install_if_missing=config.install_if_missing,
        )

        # Stage 8 filesystem isolation: provision claustrum (cross-compiled on
        # the engine, placed on the worker, --version verified). Default-on;
        # fail-closed — a provisioning failure RAISES here and aborts the
        # session rather than launching kimi unconfined. Resolved once (the
        # binary lives OUTSIDE the workdir and survives resume's restore).
        if config.fs_isolation:
            claustrum_path = await host_actions.ensure_claustrum_installed(
                hook_ctx, install_dir=config.kimi_install_dir,
            )

        snapshot = None
        if getattr(ctx, "resume", False) and config.supports_resume:
            snapshot = await load_latest_snapshot(ctx._db, ctx._prefix, ctx.process_id)
        resuming = snapshot is not None

        if resuming:
            # PULL half: restore the kimi session store (home/sessions) under
            # the identical workdir path (workDirKey pins on the abs path), then
            # rotate the restored optio.log so its stale DONE is not replayed.
            await restore_snapshot(
                ctx, host, snapshot,
                session_blob_decrypt=config.session_blob_decrypt,
            )
            # The launch symlink (home/.local/bin/kimi) lives INSIDE the workdir
            # and was wiped + re-materialized by the restore; re-establish it
            # against the cache (which lives OUTSIDE the workdir and survives).
            # Idempotent: cache hit → just relinks, no reinstall/redownload.
            kimi_path = await host_actions.ensure_kimicode_installed(
                host,
                install_dir=config.kimi_install_dir,
                install_if_missing=config.install_if_missing,
            )
            await host_actions.rotate_optio_log(host)
            preserved_session_id = await _recover_session_id(host)
        # Seed refresh + merge — BOTH the fresh and resume paths. Fresh: overlay
        # the stored kimi identity (credentials/kimi-code.json + config.toml) into
        # the empty workdir so kimi launches already-authed. RESUME: the restored
        # snapshot carries the session's now-EXPIRED token, so overlay a
        # freshly-refreshed credential on top — otherwise a resumed conversation
        # re-uses the stale token and kimi ships an empty model picker or rejects
        # session/new with "Authentication required" (the resume-launch failure).
        # The manifest touches only credentials/ + config.toml, so the restored
        # home/sessions store survives the overlay. The refresh is host-free (no
        # kimi process): a no-op when the token is fresh, a refresh_token grant
        # when stale, and a hard abort with an actionable message when the seed is
        # spoiled. kimi credentials are cwd-independent, so no rekey is needed.
        if config.seed_id is not None:
            if callable(config.seed_id):
                # A SeedProvider leases a seed from the pool (holder =
                # process_id); the watcher renews the lease, teardown releases
                # it. A plain string carries no lease.
                resolved_seed_id = await config.seed_id(ctx.process_id)
                lease_holder = ctx.process_id
            else:
                resolved_seed_id = config.seed_id
            await _merge_seed_with_refresh(
                ctx, host, config, seed_id=resolved_seed_id,
            )
            # Baseline the merged kimi-code.json so the in-session watcher and
            # the teardown backstop only save back a genuinely rotated token.
            cred_baseline = await cred_watcher.cred_fingerprint(host)

        # Fresh: plant the AGENTS.md the agent consumes. On resume the
        # snapshot-restored AGENTS.md is kept.
        if not resuming:
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

        # Permission mode (e.g. "yolo" = blanket auto-approve): written into the
        # task's config.toml so the daemon applies it to every session it creates
        # (iframe + conversation). Runs after seed-merge / resume-restore so it is
        # not wiped; no-op when permission_mode is None.
        await host_actions.write_kimi_config(
            host, host.workdir, permission_mode=config.permission_mode,
        )

        if config.supports_resume:
            await host_actions.append_resume_log_entry(host)

        if config.before_execute is not None:
            await config.before_execute(hook_ctx)

    async def _iframe_body(host: Host, hook_ctx: HookContext) -> None:
        nonlocal launched_handle, worker_port, token, session_id
        nonlocal cred_watch_task

        # Network binding (same env handling as grok/claudecode for
        # multi-container deploys).
        bind_addr = os.environ.get("OPTIO_WIDGET_TUNNEL_BIND", "127.0.0.1")
        upstream_host = os.environ.get("OPTIO_WIDGET_TUNNEL_HOST", "127.0.0.1")
        server_iface = bind_addr if isinstance(host, LocalHost) else "127.0.0.1"

        launch_env = {
            **(config.env or {}),
            **(hook_ctx.browser_launch_env or {}),
        }
        ctx.report_progress(None, "Launching Kimi Code…")
        # Stage 8: confine the kimi web server (and its tool subprocesses) to
        # the workdir + grants. None when fs_isolation is off.
        claustrum_wrap = await host_actions._build_claustrum_wrap(
            host, config, claustrum_path,
        )
        handle, server_port, token = await host_actions.launch_kimi_web(
            host,
            kimi_path=kimi_path,
            bind_iface=server_iface,
            extra_env=launch_env,
            env_remove=config.scrub_env,
            ready_timeout_s=READY_TIMEOUT_S,
            claustrum_wrap=claustrum_wrap,
        )
        launched_handle = handle

        worker_port = await host.establish_tunnel(server_port, bind_addr=bind_addr)
        await ctx.set_widget_upstream(f"http://{upstream_host}:{worker_port}")

        # Pre-create (or, on resume, reuse) a single kimi session for this task.
        # All dashboards embedding the widget navigate to the same session id via
        # the iframe URL, so concurrent viewers share live state rather than each
        # opening a fresh session (one background process, N observers).
        if preserved_session_id is not None:
            session_id = preserved_session_id
        else:
            session_id = await _create_kimi_session(worker_port, token, host.workdir)

        # Point the iframe directly at the session. kimi-web recognises exactly
        # one deep-link path shape — ``/sessions/<id>`` (apps/kimi-web/src/lib/
        # sessionRoute.ts) — and reads the bearer from the ``#token=`` fragment
        # (apps/kimi-web/src/api/daemon/serverAuth.ts), a client-side fragment
        # the SPA scrubs from the URL and never sends to the server.
        fragment = f"#token={token}" if token else ""
        # ``widgetProxyUrl`` already ends in ``/`` (optio-ui ProcessWidget.tsx),
        # so do NOT prefix ``sessions/`` with another ``/`` — a ``//`` here becomes
        # a protocol-relative leading ``//`` after the proxy's prefix-strip script
        # runs, which makes ``history.replaceState`` throw a cross-origin SecurityError.
        await ctx.set_widget_data({
            # ``?embed=1`` puts the kimi-web SPA in embedded mode (no sidebar/nav
            # chrome) for the iframe surface. Query goes BEFORE the ``#token=``
            # fragment; the proxy's prefix-strip script preserves location.search,
            # so the flag survives the rewrite.
            "iframeSrc": f"{{widgetProxyUrl}}sessions/{session_id}?embed=1{fragment}",
            # kimi-web is a client-routed SPA: opt into the proxy prefix-strip so
            # its router sees the app URL space. (ttyd widgets omit this.)
            "stripProxyPrefix": True,
        })
        ctx.report_progress(None, "Kimi Code is live")

        # Start the in-session credential watcher for a seeded session: it saves
        # back the rotated kimi-code.json, and (when the seed is leased) renews
        # the lease and aborts the session on lease loss.
        if resolved_seed_id is not None:
            cred_watch_task = asyncio.create_task(
                cred_watcher.run_credential_watcher(
                    ctx, host,
                    seed_id=resolved_seed_id,
                    baseline=cred_baseline,
                    encrypt=config.session_blob_encrypt,
                    decrypt=config.session_blob_decrypt,
                    lease_holder=lease_holder,
                )
            )

        # auto_start: on a fresh launch, POST the kickoff prompt so kimi starts
        # the task unattended. On resume, PUSH the resume notice instead so the
        # rehydrated agent notices promptly (resume.log stays the pull-based
        # source of truth). Mirrors opencode session.py lines ~418-432.
        if config.auto_start and not resuming:
            await _post_kimi_prompt(worker_port, token, session_id, AUTO_START_PROMPT)
        elif resuming and config.supports_resume:
            await _post_kimi_prompt(
                worker_port, token, session_id,
                f"{SYSTEM_MESSAGE_PREFIX}{RESUME_NOTICE}",
            )

        # Await the kimi server. The protocol driver cancels this body when it
        # sees DONE/ERROR in optio.log; if the server exits on its own,
        # proc_wait returns and the body returns → driver treats it as a
        # premature exit. (kimi's server is long-lived — it does not exit on
        # task completion, unlike grok's TUI.)
        wait_task = asyncio.create_task(proc_wait(handle))
        try:
            while ctx.should_continue():
                if wait_task.done():
                    break
                await asyncio.sleep(1.0)
        finally:
            if not wait_task.done():
                wait_task.cancel()
                try:
                    await wait_task
                except asyncio.CancelledError:
                    pass

    # Conversation mode is headless (kimi acp stdio) — no kimi web server.
    conversation: KimiCodeConversation | None = None
    if config.mode == "conversation":
        conversation = KimiCodeConversation(
            cwd=host.workdir, permission_gate=config.permission_gate,
        )
    # Per-task conversation listener (conversation_ui only). Started in the
    # body after publish_result, torn down in the finally block.
    conv_listener: ConversationListener | None = None

    async def _conversation_body(host: Host, hook_ctx: HookContext) -> None:
        nonlocal launched_handle, cred_watch_task, conv_listener

        # Launch ``kimi acp`` directly (headless ACP over stdio; no kimi web
        # server, no tmux/ttyd). ``kimi acp`` accepts only ``--login`` — there
        # is NO model / sandbox / approve launch flag (verified against
        # apps/kimi-code/src/cli/sub/acp.ts): the model is chosen over ACP
        # (``session/set_model``) and the permission gate is the fs/terminal
        # capability seam KimiCodeConversation withholds, not a CLI flag.
        argv = [kimi_path, "acp"]
        # Stage 8 filesystem isolation: prepend the claustrum wrap so kimi acp
        # (and its tool subprocesses) run confined. None when fs_isolation is
        # off. claustrum execve's kimi, so the bidirectional JSON-RPC stdio
        # pipes pass through unchanged.
        claustrum_wrap = await host_actions._build_claustrum_wrap(
            host, config, claustrum_path,
        )
        # ``exec`` so /bin/sh REPLACES itself with claustrum (then kimi), making
        # kimi the session leader in the launcher's process group (the pgid
        # optio's teardown targets) rather than a forked grandchild killpg misses.
        cmd = host_actions.build_wrapped_exec_cmd(argv, claustrum_wrap=claustrum_wrap)
        # Same per-task HOME/KIMI_CODE_HOME/XDG isolation + PATH as the iframe
        # launch (build_launch_env). merge_stderr=False keeps kimi's diagnostics
        # off the JSON-RPC stdout stream the driver parses.
        env = host_actions.build_launch_env(
            host.workdir,
            {**(config.env or {}), **(hook_ctx.browser_launch_env or {})},
        )
        ctx.report_progress(None, "Launching Kimi Code (conversation)…")
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
        ctx.report_progress(None, "Kimi Code conversation is live")

        # Opt-in dashboard chat widget: start a per-task SSE listener over the
        # published conversation and publish it as the "conversation" widget via
        # the widget proxy (which injects the basic-auth credential).
        if config.conversation_ui:
            listener_password = secrets.token_urlsafe(32)
            bind_addr = os.environ.get("OPTIO_WIDGET_TUNNEL_BIND", "127.0.0.1")
            upstream_host = os.environ.get("OPTIO_WIDGET_TUNNEL_HOST", "127.0.0.1")
            # File upload: bytes land under <workdir>/uploads with a sanitized
            # name; the view injects a System: path reference so kimi reads them
            # with its own tools (headless kimi acp has no inline ingest).
            uploads_dir = f"{host.workdir}/uploads"

            async def _write_upload(name: str, data: bytes) -> str:
                safe = re.sub(r"[^A-Za-z0-9._-]", "_", (name.split("/")[-1] or "file"))[:200] or "file"
                await host.put_file_to_host(data, f"{uploads_dir}/{safe}")
                return f"uploads/{safe}"

            # File download: serve workdir-confined bytes for the optio-file:
            # sentinel links kimi emits. realpath guards against ../ escapes.
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
            # process), so it binds directly to the widget-tunnel interface and
            # its port is reachable without a host tunnel.
            listener_port = await conv_listener.start(bind_addr)
            await ctx.set_widget_upstream(
                f"http://{upstream_host}:{listener_port}",
                inner_auth=BasicAuth(username="optio", password=listener_password),
            )
            # Frontend-parity widgetData (four-touch: config → set_widget_data →
            # ConversationViewProps → KimiCodeView). The engine-neutral session
            # controls are projected from the ACP configOptions surface captured
            # at bootstrap (authed; exact ids the ACP setters accept): model
            # (select) + thinking (segmented off/on) + mode (select). kimi is
            # the one engine that surfaces more than just the model control.
            # default_model overrides the model control's initial value;
            # otherwise the live current values are shown.
            controls = kimi_models.parse_all_controls(
                conversation.session_config_options,
                default_model=config.default_model,
            )
            await ctx.set_widget_data({
                "protocol": "kimicode",
                "toolVerbosity": config.tool_verbosity,
                "thinkingVerbosity": config.thinking_verbosity,
                "showSessionControls": config.show_session_controls,
                "nativeSpinner": config.native_spinner,
                "controls": [c.to_dict() for c in controls],
                "showFileUpload": config.show_file_upload,
                "maxUploadBytes": config.max_upload_bytes,
                "fileDownload": config.file_download,
                "maxDownloadBytes": config.max_download_bytes,
            })
            ctx.report_progress(None, "Conversation UI is live")

            # Resume history backfill: bootstrap opened a FRESH session/new for
            # this task, so kimi never re-emitted the prior conversation — a viewer
            # attaching after resume would see only new turns. Now that the
            # ConversationListener above has subscribed to conversation.on_event (in
            # its constructor), issue ACP session/load(preserved_session_id): kimi
            # replays the prior turns as session/update notifications through the
            # SAME on_event fan-out, landing them in the listener's replay buffer so
            # a late viewer reconstructs the full history. ORDERING is load-bearing:
            # strictly AFTER the listener subscribes (the fan-out has no
            # late-subscriber buffer of its own) and BEFORE the resume-notice send
            # below (whose new turn must not be tangled with the replay). Gated on
            # resuming + a recovered id; replay_history falls back to the
            # session/new session (logging, never raising) when session/load fails —
            # resume then shows no history but stays fully usable.
            if resuming and preserved_session_id:
                await conversation.replay_history(preserved_session_id)

        # Start the in-session credential watcher for a seeded conversation: it
        # saves back the rotated kimi-code.json, and (when the seed is leased)
        # renews the lease and aborts the session on lease loss. Same wiring as
        # the iframe body.
        if resolved_seed_id is not None:
            cred_watch_task = asyncio.create_task(
                cred_watcher.run_credential_watcher(
                    ctx, host,
                    seed_id=resolved_seed_id,
                    baseline=cred_baseline,
                    encrypt=config.session_blob_encrypt,
                    decrypt=config.session_blob_decrypt,
                    lease_holder=lease_holder,
                )
            )

        # Kickoff prompt as the first ACP turn (headless: no positional prompt
        # path). On resume, PUSH the resume notice instead so the rehydrated
        # session notices promptly (resume.log stays the pull-based backstop) —
        # parity with the iframe REST push already shipped.
        if config.auto_start and not resuming:
            await conversation.send(AUTO_START_PROMPT)
        elif resuming and config.supports_resume:
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
                    raise RuntimeError(f"kimi acp exited unexpectedly (exit {rc})")
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
        # worker_port / token / session_id are set by _iframe_body at launch.
        # _post_kimi_prompt raises on a non-2xx / unreachable worker, which
        # send_to_agent converts to False.
        await _post_kimi_prompt(worker_port, token, session_id, message)

    body = _conversation_body if config.mode == "conversation" else _iframe_body
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
        _trace(
            "finally: ENTER cancelled=%s resuming=%s seeded=%s conversation=%s",
            cancelled, resuming, resolved_seed_id is not None,
            config.mode == "conversation",
        )
        # kimi authenticates with a SINGLE-USE rotating refresh token. If kimi
        # rotated it this session, the new kimi-code.json must reach the seed via
        # the backstop below — but an aggressive SIGKILL can beat kimi's flush,
        # stranding the rotation (the seed keeps the now-spent token → the next
        # launch demands re-auth). So when a SEED is in use, tear kimi down
        # GRACEFULLY (SIGTERM + wait, ≤5s) even on cancel, giving it time to
        # persist kimi-code.json before the final save-back reads it. Only a
        # non-seeded session keeps the fast aggressive kill on cancel.
        kimi_aggressive = _teardown_aggressive(
            cancelled=cancelled, seeded=resolved_seed_id is not None,
        )
        # Stop the conversation listener first (conversation_ui only) so its
        # long-lived SSE loops are woken (bounded shutdown) before the
        # subprocess teardown below.
        if conv_listener is not None:
            _trace("finally: conv_listener.stop START")
            try:
                await conv_listener.stop()
            except Exception:
                _LOG.exception("conversation listener cleanup failed")
            _trace("finally: conv_listener.stop DONE")
        # kimi serves its own SPA (iframe) / speaks ACP over stdio
        # (conversation) — either way there is no tmux/ttyd tree. Terminate the
        # subprocess directly; its EOF drives the conversation to closed. A
        # cancelled non-seeded session is torn down aggressively; a clean
        # completion or any seeded session uses SIGTERM so kimi shuts down (and
        # flushes creds) gracefully.
        # Conversation (ACP over stdio): close stdin first so kimi exits cleanly
        # on EOF (rc=0, ~20ms, flushing its creds) instead of ignoring SIGTERM
        # for the full 5s grace. This is the real graceful path; the
        # terminate_subprocess below becomes an instant SIGKILL backstop for any
        # group stragglers once kimi is already gone (or the real signal fallback
        # if EOF didn't take). The iframe (ttyd) path has no stdin to EOF.
        eof_exited = False
        if config.mode == "conversation" and launched_handle is not None:
            _trace("finally: eof_shutdown START (close stdin)")
            eof_exited = await _eof_shutdown(launched_handle, timeout=3.0)
            _trace("finally: eof_shutdown DONE exited=%s", eof_exited)
        if launched_handle is not None:
            _trace(
                "finally: terminate_subprocess START aggressive=%s",
                kimi_aggressive or eof_exited,
            )
            try:
                await host.terminate_subprocess(
                    launched_handle, aggressive=kimi_aggressive or eof_exited,
                )
            except Exception:
                _LOG.exception("terminate kimi server subprocess failed")
            _trace("finally: terminate_subprocess DONE")

        # Stop the credential watcher before the final save-back so the two
        # never race on the same seed blob.
        if cred_watch_task is not None:
            _trace("finally: cred_watch_task cancel START")
            cred_watch_task.cancel()
            try:
                await cred_watch_task
            except asyncio.CancelledError:
                pass
            _trace("finally: cred_watch_task cancel DONE")

        # Final backstop save-back — LOAD-BEARING, not defensive: kimi's own
        # credential write is best-effort and the kimi provider has already
        # consumed the old refresh token; a rotation in the last poll window is
        # persisted ONLY here. Runs after kimi terminated so kimi-code.json is
        # final (the graceful teardown above ensured the flush completed).
        if resolved_seed_id is not None:
            _trace("finally: save_back_if_changed START")
            try:
                cred_baseline = await cred_watcher.save_back_if_changed(
                    ctx, host,
                    seed_id=resolved_seed_id,
                    baseline=cred_baseline,
                    encrypt=config.session_blob_encrypt,
                    decrypt=config.session_blob_decrypt,
                )
            except Exception:
                _LOG.exception("final credential save-back failed")
            _trace("finally: save_back_if_changed DONE")

        # Release the lease AFTER the final save-back (opencode's deliberate
        # ordering): a new acquirer must never merge the pre-save-back blob.
        if lease_holder is not None and resolved_seed_id is not None:
            try:
                await _seeds.release(
                    ctx._db, prefix=ctx._prefix, suffix=KIMI_SEED_SUFFIX,
                    seed_id=resolved_seed_id, holder=lease_holder,
                )
            except Exception:
                _LOG.exception("lease release failed (TTL will reclaim)")

        # Seed capture (fresh only): store this session's kimi identity as a
        # reusable seed so a later fresh task can start already-authed. Same
        # reached-live gate as snapshots (launched_handle assigned strictly
        # after a successful launch). Guarded on kimi-code.json present — never
        # seed a login-less identity. Ignored on resume.
        if (
            not resuming
            and config.on_seed_saved is not None
            and launched_handle is not None
        ):
            try:
                if not await cred_watcher.capture_gate_ok(host):
                    _trace("finally: capture_seed SKIPPED (no credentials)")
                    _LOG.warning(
                        "seed capture skipped: home/credentials/kimi-code.json "
                        "absent or invalid (login-less session)",
                    )
                else:
                    _trace("finally: capture_seed START")
                    seed_id = await _seeds.capture_seed(
                        ctx, host,
                        manifest=KIMI_SEED_MANIFEST,
                        suffix=KIMI_SEED_SUFFIX,
                        encrypt=config.session_blob_encrypt,
                    )
                    await _call_maybe_async(config.on_seed_saved, seed_id, None)
                    _trace("finally: capture_seed DONE id=%s", seed_id)
            except Exception:
                _trace("finally: capture_seed RAISED")
                _LOG.exception(
                    "seed capture failed; callback not fired, teardown continues",
                )

        # Capture a resume snapshot of the now-static workdir + session store.
        # Gated on supports_resume + a launched handle (a session actually ran).
        if config.supports_resume and launched_handle is not None:
            _trace("finally: capture_snapshot START end_state=%s",
                   "cancelled" if cancelled else "done")
            try:
                await capture_snapshot(
                    ctx, host,
                    end_state="cancelled" if cancelled else "done",
                    session_blob_encrypt=config.session_blob_encrypt,
                    workdir_exclude=config.workdir_exclude,
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


# --- kimi REST helpers (session pre-create / prompt push) ------------------
#
# All under kimi's real ``/api/v1`` prefix (apps/kimi-web/src/api/config.ts
# buildRestUrl). Auth is ``Authorization: Bearer <token>`` (the banner token;
# packages/server/src/middleware/auth.ts). The blocking urllib calls run in an
# executor and retry transient connect/read errors because the first request
# over a freshly-opened SSH local forward occasionally drops while asyncssh
# wires up the channel (opencode's rationale).


def _bearer_headers(token: str | None) -> dict:
    headers = {"content-type": "application/json"}
    if token:
        headers["authorization"] = f"Bearer {token}"
    return headers


def _create_kimi_session_sync(port: int, token: str | None, directory: str) -> str:
    """POST kimi's ``/api/v1/sessions`` and return the new session id.

    Body mirrors ``createSessionRequestSchema`` (``sessionCreateSchema``):
    ``{metadata:{cwd}}`` (either ``workspace_id`` or ``metadata.cwd`` is
    required; the server registers the cwd). The reply is the standard envelope
    ``{code,msg,data:Session,request_id}`` — the session id is ``data.id``."""
    import time
    import urllib.request
    from urllib.error import URLError

    url = f"http://127.0.0.1:{port}/api/v1/sessions"
    payload = json.dumps({"metadata": {"cwd": directory}}).encode("utf-8")
    headers = _bearer_headers(token)

    last_exc: Exception | None = None
    body = None
    for attempt in range(4):
        if attempt > 0:
            time.sleep(0.15 * attempt)
        req = urllib.request.Request(url, method="POST", data=payload, headers=headers)
        try:
            with urllib.request.urlopen(req, timeout=15) as resp:
                body = resp.read().decode("utf-8")
            break
        except (URLError, ConnectionError, OSError) as exc:
            last_exc = exc
            continue
    else:
        raise RuntimeError(f"kimi POST /sessions failed after retries: {last_exc!r}")

    envelope = json.loads(body)
    data = envelope.get("data") if isinstance(envelope, dict) else None
    session_id = data.get("id") if isinstance(data, dict) else None
    if not isinstance(session_id, str) or not session_id:
        raise RuntimeError(
            f"kimi POST /sessions envelope has no string data.id: {body!r}"
        )
    return session_id


async def _create_kimi_session(port: int, token: str | None, directory: str) -> str:
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(
        None, _create_kimi_session_sync, port, token, directory,
    )


def _post_kimi_prompt_sync(
    port: int, token: str | None, session_id: str, message: str,
) -> None:
    """POST a text prompt to kimi's ``/api/v1/sessions/<id>/prompts``.

    Body mirrors ``promptSubmissionSchema``: a non-empty ``content`` array of
    message-content parts — here a single ``{type:'text',text}`` part."""
    import time
    import urllib.request
    from urllib.error import URLError

    url = f"http://127.0.0.1:{port}/api/v1/sessions/{session_id}/prompts"
    payload = json.dumps(
        {"content": [{"type": "text", "text": message}]}
    ).encode("utf-8")
    headers = _bearer_headers(token)

    last_exc: Exception | None = None
    for attempt in range(4):
        if attempt > 0:
            time.sleep(0.15 * attempt)
        req = urllib.request.Request(url, method="POST", data=payload, headers=headers)
        try:
            with urllib.request.urlopen(req, timeout=15) as resp:
                resp.read()
            return
        except (URLError, ConnectionError, OSError) as exc:
            last_exc = exc
            continue
    raise RuntimeError(f"kimi session prompt failed after retries: {last_exc!r}")


async def _post_kimi_prompt(
    port: int, token: str | None, session_id: str, message: str,
) -> None:
    loop = asyncio.get_event_loop()
    await loop.run_in_executor(
        None, _post_kimi_prompt_sync, port, token, session_id, message,
    )


async def _recover_session_id(host: Host) -> str | None:
    """Recover the id of the REAL prior conversation from the restored session
    store, so ``session/load`` replays the actual history — not an arbitrary one.

    After a restore, kimi's session store at ``<workdir>/home/sessions/
    <workDirKey>/<sessionId>/state.json`` (design §1) holds SEVERAL sessions: the
    real prior conversation PLUS, from earlier resume cycles, empty
    ``session/new`` sessions (no turns) and resume-notice-only sessions (whose
    only turn is the injected ``System: you have been resumed`` notice, so kimi
    titles/last-prompts them with it). The old ``find | head -n1`` picked a
    filesystem-ARBITRARY one — landing on an empty/notice session meant
    ``session/load`` replayed nothing (the history-loss bug), and the resume
    notice then minted yet another empty session, compounding it.

    So enumerate EVERY session, read its ``state.json``, and select the
    most-recently-updated one that carries a REAL user turn:

      * EXCLUDE a session whose ``lastPrompt`` is empty (a fresh ``session/new``)
        or begins with ``System: `` (:data:`SYSTEM_MESSAGE_PREFIX` — a harness
        notice, i.e. a resume-notice-only session): kimi records the injected
        message there, never a real user prompt.
      * Among the rest, keep the newest by ``updatedAt`` (ISO-8601, so a plain
        string compare orders it; kimi always writes it).

    The recovered id is the ``session_<uuid>`` dir name (matches the store's
    ``session_index.jsonl`` ``sessionId``). Returns None when NO session carries
    a real turn — the body then starts a clean session rather than resuming an
    empty/notice one (no arbitrary fallback)."""
    workdir = host.workdir.rstrip("/")
    sessions_root = f"{workdir}/home/sessions"
    listing = await host.run_command(
        f"find {shlex.quote(sessions_root)} -name state.json -type f 2>/dev/null || true"
    )
    best_id: str | None = None
    best_updated = ""
    for path in (listing.stdout or "").splitlines():
        path = path.strip()
        if not path:
            continue
        r = await host.run_command(f"cat {shlex.quote(path)} 2>/dev/null || true")
        try:
            state = json.loads(r.stdout or "")
        except (ValueError, TypeError):
            continue
        last_prompt = (state.get("lastPrompt") or "").strip()
        # Skip empty (fresh session/new) and harness-notice-only sessions.
        if not last_prompt or last_prompt.startswith(SYSTEM_MESSAGE_PREFIX):
            continue
        updated = str(state.get("updatedAt") or "")
        if best_id is None or updated > best_updated:
            # .../sessions/<workDirKey>/<sessionId>/state.json → <sessionId>.
            best_id, best_updated = os.path.basename(os.path.dirname(path)), updated
    return best_id


def create_kimicode_task(
    process_id: str,
    name: str,
    config: KimiCodeTaskConfig,
    description: str | None = None,
    metadata: dict | None = None,
) -> TaskInstance:
    """Return a TaskInstance that runs one optio-kimicode session.

    ``metadata`` is the caller app's task-tagging payload; it is stamped onto
    the TaskInstance verbatim and never read by the task itself.
    """

    async def _execute(ctx: ProcessContext) -> None:
        await run_kimicode_session(ctx, config)

    # iframe → the kimi web SPA widget. Conversation mode (Stage 6) carries the
    # live chat widget only when conversation_ui is on; otherwise no widget.
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
