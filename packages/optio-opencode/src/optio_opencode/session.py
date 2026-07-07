"""The state machine that runs one optio-opencode session.

Orchestrates a Host (local or remote) through the lifecycle described in
Section 4 of the design spec.  The public entry point is the factory
``create_opencode_task(...)`` which wraps ``run_opencode_session`` in a
``TaskInstance`` and sets ``ui_widget="iframe"``.

Most of the per-session work is generic log/deliverables protocol
plumbing (parse ``optio.log``, fetch deliverables, watch for cancel) and
lives in ``optio_agents.protocol.run_log_protocol_session``.  This module
keeps only the opencode-specific work — write AGENTS.md / opencode.json,
install/launch the opencode binary, set up tunnel and widget, and the
resume/snapshot brackets around the protocol session.
"""

from __future__ import annotations

import asyncio
import base64
import inspect
import json
import logging
import os
import secrets
import shlex
import tempfile
from datetime import datetime, timezone
from typing import AsyncIterator, Callable

from optio_core.context import ProcessContext
from optio_core.models import BasicAuth, TaskInstance

from optio_agents import HookContext
from optio_agents import RESUME_NOTICE, SYSTEM_MESSAGE_PREFIX
from optio_host.host import Host, LocalHost, ProcessHandle
from optio_host.paths import task_dir
from optio_agents.protocol.session import _SessionFailed, run_log_protocol_session
from optio_agents.uploads import materialize, upload_url_token
from optio_agents import seeds as _seeds
from optio_opencode import cred_watcher, host_actions
from optio_opencode import model_probe
from optio_opencode.conversation import OpencodeConversation
from optio_opencode.prompt import DEFAULT_CONVERSATION_INSTRUCTIONS, compose_agents_md
from optio_opencode.seed_manifest import (
    OPENCODE_CRED_MANIFEST,
    OPENCODE_SEED_MANIFEST,
    OPENCODE_SEED_SUFFIX,
)
from optio_agents import get_protocol
from optio_opencode.snapshots import (
    insert_snapshot,
    load_latest_snapshot,
    prune_snapshots,
)
from optio_opencode.types import OpencodeTaskConfig


_LOG = logging.getLogger(__name__)


READY_TIMEOUT_S = 30.0

# Fresh-launch kickoff prompt POSTed to the pre-created opencode session so the
# agent starts the task unattended. Suppressed on resume.
AUTO_START_PROMPT = "Read AGENTS.md and execute the task it describes"


def _build_host(config: OpencodeTaskConfig, process_id: str) -> Host:
    """Construct the appropriate Host object for the given config.

    Extracted so tests can monkeypatch ``optio_opencode.session._build_host``
    to inject a fake host without launching real subprocesses or SSH.
    Delegates to host_actions.build_host (shared with verify).
    """
    taskdir = task_dir(
        ssh=config.ssh, process_id=process_id, consumer_name="optio-opencode",
    )
    return host_actions.build_host(config.ssh, taskdir)


def _fold_tool_permissions(
    opencode_cfg: dict,
    *,
    allowed_tools: list[str] | None,
    disallowed_tools: list[str] | None,
) -> dict:
    """Fold ``allowed_tools``/``disallowed_tools`` into opencode.json's
    ``permission`` map (tool→"allow"/"deny").

    Merges with — never clobbers — an operator-supplied
    ``opencode_config["permission"]``: the convenience fields only add/override
    the specific tools they name. ``deny`` wins over ``allow`` for a tool named
    in both (deny is applied last). Returns a NEW dict; the input is not
    mutated. A no-op (returns ``opencode_cfg`` unchanged) when neither list is
    set."""
    if not allowed_tools and not disallowed_tools:
        return opencode_cfg
    cfg = dict(opencode_cfg)
    permission = dict(cfg.get("permission") or {})
    for tool in allowed_tools or []:
        permission[tool] = "allow"
    for tool in disallowed_tools or []:
        permission[tool] = "deny"
    cfg["permission"] = permission
    return cfg


def _warn_if_fs_isolation_unenforced(config: "OpencodeTaskConfig") -> None:
    """Emit a runtime warning on the worker console when ``fs_isolation`` is
    requested. opencode has no claustrum sandbox wired yet, so the flag is
    INERT (see OpencodeTaskConfig.fs_isolation): honour the operator's signal
    by making the no-op loud rather than silent."""
    if config.fs_isolation:
        _LOG.warning(
            "opencode fs_isolation requested but not yet enforced "
            "(claustrum pending); the agent is NOT filesystem-confined."
        )


def conversation_widget_data(config: "OpencodeTaskConfig", *, session_id: str, directory: str) -> dict:
    """The widgetData published for a conversation_ui task. Pure so it can be
    unit-tested without a live session."""
    return {
        "protocol": "opencode",
        "sessionID": session_id,
        "directory": directory,
        "toolVerbosity": config.tool_verbosity,
        "thinkingVerbosity": config.thinking_verbosity,
        "showSessionControls": config.show_session_controls,
        "nativeSpinner": config.native_spinner,
        "defaultModel": config.model,
        "showFileUpload": config.show_file_upload,
        "maxUploadBytes": config.max_upload_bytes,
        "fileDownload": config.file_download,
        "maxDownloadBytes": config.max_download_bytes,
    }


async def run_opencode_session(ctx: ProcessContext, config: OpencodeTaskConfig) -> None:
    """Execute function body for one optio-opencode task instance."""
    # --- per-task filesystem layout ---------------------------------------
    host: Host = _build_host(config, ctx.process_id)
    protocol = get_protocol(
        browser="suppress",
        client_messages=config.use_client_messages,
        caller_messages=config.on_caller_message is not None,
    )

    instructions = config.consumer_instructions
    omit_task_framing = False
    if config.mode == "conversation" and not instructions:
        instructions = DEFAULT_CONVERSATION_INSTRUCTIONS
        omit_task_framing = True
    taskdir = task_dir(
        ssh=config.ssh, process_id=ctx.process_id, consumer_name="optio-opencode",
    )
    opencode_db = f"{taskdir}/opencode.db"

    password = secrets.token_urlsafe(32)
    cancelled = False
    launched_handle: ProcessHandle | None = None
    conversation: OpencodeConversation | None = None
    reader_task: "asyncio.Task | None" = None
    opencode_exec: str = "opencode"
    session_id: str | None = None
    preserved_session_id: str | None = None
    # Worker-side opencode port; hoisted so the finally can query the live
    # server (for the seed model default) before terminating it.
    worker_port: int | None = None

    # Set by _prepare (the driver runs it after the workdir wipe, before the
    # optio.log tail); read by the body and the teardown finally.
    resuming = False

    cred_baseline: str | None = None
    cred_watch_task: "asyncio.Task | None" = None
    resolved_seed_id: str | None = None
    lease_holder: str | None = None

    await host.connect()

    async def _prepare(host: Host, hook_ctx: HookContext) -> None:
        """Install opencode and restore a resume snapshot.

        Handed to run_log_protocol_session, which runs it AFTER
        host.setup_workdir() wiped the workdir and BEFORE it subscribes the
        optio.log tail. The resume path needs ``opencode import`` to replay
        the saved session DB (so opencode must be installed + resolved to an
        absolute path first), and the restored optio.log is rotated away below
        before the tail can re-emit its stale DELIVERABLE/DONE/ERROR lines.
        """
        nonlocal opencode_exec, resuming, preserved_session_id
        opencode_exec = await host_actions.ensure_opencode_installed(
            hook_ctx._host,
            download=hook_ctx.download_file,
            report_progress=hook_ctx.report_progress,
            install_if_missing=config.install_if_missing,
            install_dir=config.install_dir,
        )

        resume_requested = bool(getattr(ctx, "resume", False))
        snapshot: dict | None = None
        if resume_requested:
            snapshot = await load_latest_snapshot(
                ctx._db, prefix=ctx._prefix, process_id=ctx.process_id,
            )

        resuming = snapshot is not None
        if resuming:
            await host.remove_file(opencode_db)
            try:
                await host.restore_workdir(_stream_blob(ctx, snapshot["workdirBlobId"]))
                session_bytes_raw = await _read_blob_bytes(ctx, snapshot["sessionBlobId"])
                decrypt = config.session_blob_decrypt or (lambda b: b)
                session_bytes = decrypt(session_bytes_raw)
                await host_actions.opencode_import(
                    host, opencode_db, session_bytes,
                    opencode_executable=opencode_exec,
                )
                # Move the restored log channel out of the way BEFORE the
                # protocol driver subscribes its tail. The snapshot tar
                # includes optio.log from the previous run; without rotation,
                # ``tail -F -n +1`` would re-emit every old DELIVERABLE /
                # DONE / ERROR line and the resumed process would terminate
                # within seconds of launch.  Preserve the historical content
                # by appending it to optio.log.old.
                await _rotate_optio_log(host)
                preserved_session_id = snapshot["sessionId"]
            except Exception as resume_exc:
                # If the failure was the session-blob decrypt hook raising,
                # this indicates the snapshot was tampered with or the
                # consumer's keypair changed. Fail loud — silently dropping
                # to fresh-start would mask the security-relevant signal.
                if "decrypt" in repr(resume_exc).lower() and "blob" in repr(resume_exc).lower():
                    _LOG.error(
                        "resume restore failed inside session_blob_decrypt; "
                        "refusing to fall through to fresh-start. Operator must "
                        "investigate the snapshot blob.",
                    )
                    raise
                _LOG.exception(
                    "resume restore failed; falling back to fresh-start path "
                    "(Mongo blob preserved for inspection)",
                )
                await host.remove_file(opencode_db)
                resuming = False
                preserved_session_id = None

    async def _opencode_body(host: Host, hook_ctx: HookContext) -> None:
        """Opencode-specific body that runs inside the protocol driver.

        Captures launch state via nonlocal so the outer ``finally`` can
        terminate the subprocess and capture the snapshot.
        """
        nonlocal launched_handle, opencode_exec, session_id, preserved_session_id
        nonlocal worker_port, conversation, reader_task
        nonlocal cred_baseline, cred_watch_task, resolved_seed_id, lease_holder

        if callable(config.seed_id):
            # The provider acquires a pooled seed lease inside (holder =
            # process_id); the watcher renews it, teardown releases it.
            resolved_seed_id = await config.seed_id(ctx.process_id)
            lease_holder = ctx.process_id
        else:
            resolved_seed_id = config.seed_id

        refreshed_files: list[str] = []
        if not resuming:
            # Fresh start: the protocol driver has already created the
            # workdir, deliverables/ subdir, and empty optio.log. Ensure
            # any stale opencode db from a prior crashed run is gone, then
            # write the fresh AGENTS.md and opencode.json that the agent
            # consumes.
            await host.remove_file(opencode_db)
            await host.write_text(
                "AGENTS.md",
                compose_agents_md(
                    instructions,
                    documentation=protocol.documentation if config.host_protocol else None,
                    workdir_exclude=config.workdir_exclude,
                    supports_resume=config.supports_resume,
                    host_protocol=config.host_protocol,
                    omit_task_framing=omit_task_framing,
                    file_download=config.file_download,
                ),
            )
            opencode_cfg = dict(config.opencode_config)
            if config.model is not None and "model" not in opencode_cfg:
                # The harmonized default-model field takes effect in EVERY
                # mode via opencode.json's top-level "model" key (opencode's
                # defaultModel() reads it first), so an unattended/iframe run
                # uses this model instead of the first-provider fallback. An
                # explicit opencode_config["model"] (operator raw) wins.
                opencode_cfg["model"] = config.model
            if config.mode == "conversation":
                # Questions (multi-choice asks) have no conversation-mode
                # answering path yet — disable the tool so a session can
                # never block on one. See design doc "Non-goals".
                opencode_cfg["tools"] = {**opencode_cfg.get("tools", {}), "question": False}
            opencode_cfg = _fold_tool_permissions(
                opencode_cfg,
                allowed_tools=config.allowed_tools,
                disallowed_tools=config.disallowed_tools,
            )
            await host.write_text(
                "opencode.json", json.dumps(opencode_cfg, indent=2),
            )
            if resolved_seed_id is not None:
                # Seeded fresh: overlay the stored environment into
                # <workdir>/home, where the launch's XDG_DATA_HOME /
                # XDG_CONFIG_HOME point, so the seeded auth.json / opencode.json
                # are used. Begins a NEW session — no resume.
                await _seeds.merge_seed(
                    ctx, host,
                    seed_id=resolved_seed_id,
                    manifest=OPENCODE_SEED_MANIFEST,
                    suffix=OPENCODE_SEED_SUFFIX,
                    decrypt=config.session_blob_decrypt,
                )
            cred_baseline = await cred_watcher.cred_fingerprint(host)
            # Note: do NOT call ctx.clear_has_saved_state() here. The spec
            # described it as "belt-and-braces", but in practice it makes
            # `hasSavedState` track the live session rather than the durable
            # snapshot collection. A worker crash mid-Restart would then
            # leave hasSavedState=false even though perfectly good prior
            # snapshots are still in Mongo, hiding the Resume affordance
            # from the UI. The flag is now only ever flipped true by
            # mark_has_saved_state at terminal capture; resume's stale-flag
            # self-healing (snapshot lookup returns None → fresh-start
            # fallback) handles the rare case where the flag is true but
            # no snapshot exists.
        else:
            # The seed is the source of truth for credentials; the snapshot
            # may carry a now-rotated/dead token. Overlay the seed's CURRENT
            # auth.json over the restored workdir (mirrors claudecode).
            if resolved_seed_id is not None:
                await _seeds.merge_seed(
                    ctx, host,
                    seed_id=resolved_seed_id,
                    manifest=OPENCODE_CRED_MANIFEST,
                    suffix=OPENCODE_SEED_SUFFIX,
                    decrypt=config.session_blob_decrypt,
                )
            cred_baseline = await cred_watcher.cred_fingerprint(host)
            # Resume: when on_resume_refresh is wired, recompute AGENTS.md
            # from the refreshed config and overwrite the workdir copy if
            # the rendered text differs from the snapshot-restored file.
            refreshed_files = await _maybe_refresh_on_resume(host, hook_ctx, config)

        if config.supports_resume:
            await _append_resume_log_entry(host, refreshed=refreshed_files)

        # opencode is already installed by run_opencode_session before
        # this body runs (so resume restore can call opencode_import
        # against a known-good absolute path). ``opencode_exec`` is set
        # on the enclosing closure.

        # --- before_execute hook ----------------------------------------
        # Fires after the binary is in place and before opencode launches,
        # so consumer hooks can ship per-task files via hook_ctx.copy_file
        # and run setup commands via hook_ctx.run_on_host.
        if config.before_execute is not None:
            await config.before_execute(hook_ctx)

        # --- launch ------------------------------------------------------
        version = await host_actions.opencode_version(
            host, opencode_executable=opencode_exec,
        )
        version_suffix = f" {version}" if version else ""
        # --- tunnel + widget registration --------------------------------
        # By default the SSH tunnel listens on 127.0.0.1 — only the worker
        # process (this engine) can reach it.  For multi-container deploys
        # where the API proxy lives in a different container than the
        # engine but on the same Docker network, set
        # OPTIO_WIDGET_TUNNEL_BIND=0.0.0.0 so sibling containers can
        # connect to the engine's port, and OPTIO_WIDGET_TUNNEL_HOST to
        # the Docker DNS name other containers resolve to reach this
        # engine (e.g. the compose service name). Both default to
        # 127.0.0.1 so single-host deploys are unchanged.
        bind_addr = os.environ.get("OPTIO_WIDGET_TUNNEL_BIND", "127.0.0.1")
        upstream_host = os.environ.get("OPTIO_WIDGET_TUNNEL_HOST", "127.0.0.1")

        # LocalHost has no SSH tunnel — establish_tunnel is a no-op — so
        # opencode itself must bind to ``bind_addr`` for sibling containers
        # to reach it. RemoteHost keeps opencode bound to the remote's
        # loopback; the SSH tunnel on the engine side handles exposure.
        opencode_hostname = bind_addr if isinstance(host, LocalHost) else "127.0.0.1"

        _warn_if_fs_isolation_unenforced(config)
        ctx.report_progress(None, f"Launching opencode{version_suffix}…")
        handle, opencode_port = await host_actions.launch_opencode(
            host, password,
            ready_timeout_s=READY_TIMEOUT_S,
            opencode_executable=opencode_exec,
            hostname=opencode_hostname,
            extra_env=hook_ctx.browser_launch_env,
            env_remove=config.scrub_env,
        )
        launched_handle = handle

        worker_port = await host.establish_tunnel(opencode_port, bind_addr=bind_addr)

        if preserved_session_id is not None:
            session_id = preserved_session_id
        else:
            # Pre-create a single opencode session for this task instance.
            # All dashboards that embed this widget navigate to the same
            # session ID via the iframe URL, so concurrent viewers share
            # live state (events over SSE) rather than each creating a fresh
            # isolated session on load.  Matches optio's mental model: one
            # background process, N observers.
            session_id = await _create_opencode_session(
                worker_port, password, host.workdir,
            )

        if config.mode != "conversation":
            await ctx.set_widget_upstream(
                f"http://{upstream_host}:{worker_port}",
                inner_auth=BasicAuth(username="opencode", password=password),
            )
            # Point the iframe directly at the pre-created session so viewers
            # skip both the project picker and the "new session" default.
            # opencode's SPA expects the :dir router param to be a URL-safe
            # base64 encoding of the directory path (see
            # packages/app/src/utils/base64.ts in opencode) — NOT percent-
            # encoding.  The {widgetProxyUrl} token is resolved by the iframe
            # widget at mount time.
            _workdir_b64 = (
                base64.urlsafe_b64encode(host.workdir.encode("utf-8"))
                .decode("ascii").rstrip("=")
            )
            await ctx.set_widget_data({
                "iframeSrc": f"{{widgetProxyUrl}}{_workdir_b64}/session/{session_id}",
                # opencode is a client-routed SPA loaded at a deep sub-route: the
                # proxy must strip its prefix from location.pathname so the SPA
                # router sees the app URL space. (ttyd widgets omit this.)
                "stripProxyPrefix": True,
                "localStorageOverrides": {
                    "opencode.settings.dat:defaultServerUrl": "{widgetProxyUrl}",
                    # Start with the review/diff panel collapsed. opencode defaults
                    # it OPEN (layout store `review.panelOpened ?? true`), eating
                    # the right half of the iframe with a panel that is useless in
                    # this embedded context. The persist layer deep-merges this
                    # partial blob into the layout defaults, so only panelOpened is
                    # forced false; the operator can still toggle it open per
                    # session. (UI-state key — if opencode renames the `layout`
                    # store it silently reverts to default-open.)
                    "opencode.global.dat:layout": '{"review": {"panelOpened": false}}',
                },
            })
            ctx.report_progress(None, "opencode is live")
        elif config.conversation_ui:
            # Conversation widget: the opencode server itself is the upstream
            # (same proxy + inner-auth model as iframe mode); the widget talks
            # opencode's native API through the proxy.
            await ctx.set_widget_upstream(
                f"http://{upstream_host}:{worker_port}",
                inner_auth=BasicAuth(username="opencode", password=password),
            )
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

            # widgetData.uploadUrl token; see optio_agents.uploads.upload_url_token.
            upload_url = upload_url_token(ctx._db.name, ctx._prefix, ctx.process_id)
            # opencode routes resolve their project instance from the request's
            # location context; the widget sends `directory` as the ?directory=
            # query param on every call.
            # Model-availability probe: greys out models this seed's account
            # cannot use. Runs HERE — after the model list is server-side
            # resolvable and BEFORE the widget is shown — so its throwaway
            # "capital of Hungary" turns (on a separate session) never reach the
            # picker. Gated on show_session_controls (the picker is only visible
            # then); cached per seed; never re-probes on resume. The disabled-map
            # rides widgetData → OpencodeView disables those picker options.
            disabled_models: dict[str, str] = {}
            if config.show_session_controls:
                try:
                    disabled_models = await _probe_or_cached_disabled_models(
                        ctx, worker_port=worker_port, password=password,
                        directory=host.workdir,
                        seed_key=model_probe.probe_cache_key(
                            resolved_seed_id, config.seed_id,
                        ),
                        resuming=resuming,
                    )
                except Exception:  # noqa: BLE001
                    _LOG.exception(
                        "model-probe orchestration failed; picker left unfiltered",
                    )

            widget_data = conversation_widget_data(
                config, session_id=session_id, directory=host.workdir,
            )
            widget_data["uploadUrl"] = upload_url
            if disabled_models:
                widget_data["disabledModels"] = disabled_models
            await ctx.set_widget_data(widget_data)

        if resolved_seed_id is not None:
            cred_watch_task = asyncio.create_task(cred_watcher.run_credential_watcher(
                ctx, host,
                seed_id=resolved_seed_id,
                baseline=cred_baseline,
                encrypt=config.session_blob_encrypt,
                decrypt=config.session_blob_decrypt,
                lease_holder=lease_holder,
            ))

        # auto_start: on a fresh launch, POST the kickoff prompt to the
        # pre-created session so opencode starts the task unattended.
        # Suppressed on resume (the restored session already carries its
        # conversation; re-prompting would re-trigger the task).
        if config.auto_start and not resuming:
            await _post_opencode_prompt(
                worker_port, password, session_id, AUTO_START_PROMPT,
            )
        elif resuming and config.supports_resume:
            # Push notification: make the resumed agent NOTICE the resume
            # promptly (resume.log remains the pull-based source of truth).
            await _post_opencode_prompt(
                worker_port, password, session_id,
                f"{SYSTEM_MESSAGE_PREFIX}{RESUME_NOTICE}",
            )

        if config.mode != "conversation":
            # --- await opencode subprocess exit (iframe mode, unchanged) ---
            # The protocol driver runs this body alongside the tail dispatcher
            # and a cancel watcher.  When the user cancels, the driver cancels
            # this body's task; when the agent emits DONE/ERROR, the driver
            # returns / raises and again cancels this body.  In either case the
            # await below is interrupted via CancelledError before proc exits.
            # If, however, opencode exits on its own without emitting DONE
            # first, the body returns normally and the driver detects this as
            # "premature body exit" and raises _SessionFailed.
            proc = launched_handle.pid_like
            await proc.wait()  # type: ignore[union-attr]
            return

        # --- conversation mode: publish the gateway, then wait on
        # close-requested vs. process exit (mirrors optio-claudecode).
        conversation = OpencodeConversation(
            port=worker_port, password=password,
            session_id=session_id, directory=host.workdir,
        )
        reader_task = asyncio.create_task(conversation.run_reader())
        ctx.publish_result(conversation)
        ctx.report_progress(None, "opencode conversation is live")

        proc = launched_handle.pid_like
        wait_task = asyncio.create_task(proc.wait())  # type: ignore[union-attr]
        close_task = asyncio.create_task(conversation.close_requested.wait())
        try:
            done, _ = await asyncio.wait(
                {wait_task, close_task}, return_when=asyncio.FIRST_COMPLETED,
            )
            if close_task in done and wait_task not in done:
                # Caller asked to close: cooperative shutdown, clean end.
                wait_task.cancel()
                if config.host_protocol:
                    # The keyword driver treats a body return without DONE as
                    # a premature exit. A caller-requested close IS the clean
                    # end, so emit the DONE ourselves and park until the
                    # driver observes it and cancels us (claudecode parity).
                    log_path = f"{host.workdir}/optio.log"
                    await host.run_command(f"echo DONE >> {shlex.quote(log_path)}")
                    await asyncio.Event().wait()  # cancelled by the driver
                return
            # Server exited on its own.
            close_task.cancel()
            if not conversation.close_requested.is_set() and ctx.should_continue():
                raise RuntimeError("opencode exited unexpectedly")
        finally:
            reader_task.cancel()
            try:
                await reader_task
            except asyncio.CancelledError:
                pass

    # --- run the protocol session -----------------------------------------
    # host.connect() already happened up-front (before install + resume).
    session_error: BaseException | None = None

    async def _agent_sender(message: str) -> None:
        # worker_port / session_id are set by _opencode_body at launch;
        # password is established at function scope. _post_opencode_prompt
        # raises on a non-2xx / unreachable worker, which
        # send_to_agent converts to False.
        await _post_opencode_prompt(worker_port, password, session_id, message)

    try:
        # before_execute is wired manually inside _opencode_body (after
        # install, before launch) per opencode's documented timing.
        # after_execute is left to the protocol driver — it fires after
        # the body terminates and before the outer finally runs the
        # snapshot capture, matching the documented contract.
        await run_log_protocol_session(
            host, ctx,
            body=_opencode_body,
            prepare=_prepare,
            on_deliverable=config.on_deliverable,
            on_caller_message=config.on_caller_message,
            after_execute=config.after_execute,
            protocol=protocol,
            agent_sender=_agent_sender,
            keywords=config.host_protocol,
        )
    except _SessionFailed as fail:
        session_error = fail
        raise RuntimeError(str(fail)) from None
    except BaseException as exc:
        session_error = exc
        raise

    finally:
        # Cancellation detection. The protocol driver swallows cancellation
        # cleanly and returns; we observe it here via the ProcessContext
        # flag.  ``aggressive=True`` triggers SIGKILL behaviour for a
        # cancelled session vs. a clean SIGTERM for a normal exit.
        if not ctx.should_continue():
            cancelled = True

        # Drop the in-process upload writer so a late materializeUpload RPC
        # can't reach a torn-down Host (raises NoUploadWriter instead).
        # Idempotent; safe even when no writer was registered.
        if config.mode == "conversation" and config.conversation_ui:
            try:
                ctx.clear_upload_writer()
            except Exception:  # noqa: BLE001
                _LOG.exception("clear upload writer failed")

        # Resolve the operator's last-used model BEFORE terminating opencode
        # (the query needs the live server). Synthesised into the seed's
        # opencode.json below so an unattended seeded session runs that model
        # rather than opencode's first-provider fallback. Best-effort.
        seed_model: str | None = None
        if (
            not resuming
            and config.on_seed_saved is not None
            and worker_port is not None
            and session_id is not None
        ):
            try:
                seed_model = await _resolve_session_model(
                    worker_port, password, session_id,
                )
            except Exception:  # noqa: BLE001
                _LOG.exception(
                    "seed model resolution failed; seed will carry no model default",
                )

        if launched_handle is not None:
            try:
                await host.terminate_subprocess(launched_handle, aggressive=cancelled)
            except Exception:  # noqa: BLE001
                _LOG.exception("terminate_subprocess failed")

        if cred_watch_task is not None:
            cred_watch_task.cancel()
            try:
                await cred_watch_task
            except asyncio.CancelledError:
                pass

        # Final backstop save-back — LOAD-BEARING, not defensive: opencode's
        # own auth write-back is best-effort (auth.set().catch(() => {})) and
        # the provider has already consumed the old refresh token; a rotation
        # in the last poll window is saved ONLY here. Runs after the
        # subprocess terminated so the on-disk auth.json is final.
        if resolved_seed_id is not None:
            try:
                cred_baseline = await cred_watcher.save_back_if_changed(
                    ctx, host,
                    seed_id=resolved_seed_id,
                    baseline=cred_baseline,
                    encrypt=config.session_blob_encrypt,
                    decrypt=config.session_blob_decrypt,
                )
            except Exception:  # noqa: BLE001
                _LOG.exception("final credential save-back failed")

        # Release AFTER the final save-back (deliberate divergence from
        # claudecode, which releases first): a new acquirer must never merge
        # the pre-save-back blob.
        if lease_holder is not None and resolved_seed_id is not None:
            try:
                await _seeds.release(
                    ctx._db, prefix=ctx._prefix, suffix=OPENCODE_SEED_SUFFIX,
                    seed_id=resolved_seed_id, holder=lease_holder,
                )
            except Exception:  # noqa: BLE001
                _LOG.exception("lease release failed (TTL will reclaim)")

        if not resuming and config.on_seed_saved is not None:
            try:
                if seed_model is not None:
                    # Write the model default into the seed's opencode.json
                    # before capture so it travels in the seed.
                    await _write_seed_model_config(host, seed_model)
                if not await cred_watcher.capture_gate_ok(host):
                    _LOG.warning(
                        "seed capture skipped: auth.json invalid/absent or no "
                        "model in opencode.json (unusable seed)",
                    )
                else:
                    seed_id_out = await _seeds.capture_seed(
                        ctx, host,
                        manifest=OPENCODE_SEED_MANIFEST,
                        suffix=OPENCODE_SEED_SUFFIX,
                        encrypt=config.session_blob_encrypt,
                    )
                    # 2nd arg: the resolved "providerID/modelID" (or None).
                    await _call_maybe_async(
                        config.on_seed_saved, seed_id_out, seed_model,
                    )
            except Exception:  # noqa: BLE001
                _LOG.exception("opencode seed capture failed; callback not fired")

        if config.supports_resume and session_id is not None:
            try:
                await _capture_snapshot(
                    ctx, host,
                    session_id=preserved_session_id or session_id,
                    opencode_db=opencode_db,
                    end_state="cancelled" if cancelled else "done",
                    workdir_exclude=config.workdir_exclude,
                    opencode_executable=opencode_exec,
                    session_blob_encrypt=config.session_blob_encrypt,
                )
            except Exception:  # noqa: BLE001
                _LOG.exception(
                    "snapshot capture failed; proceeding with workdir wipe",
                )

        try:
            await host.cleanup_taskdir(aggressive=cancelled)
        except Exception:  # noqa: BLE001
            _LOG.exception("cleanup_taskdir failed")
        try:
            await host.disconnect()
        except Exception:  # noqa: BLE001
            _LOG.exception("host.disconnect failed")


# --- helpers ---------------------------------------------------------------


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


async def _capture_snapshot(
    ctx: ProcessContext,
    host: Host,
    *,
    session_id: str,
    opencode_db: str,
    end_state: str,
    workdir_exclude: list[str] | None,
    opencode_executable: str = "opencode",
    session_blob_encrypt: "Callable[[bytes], bytes] | None" = None,
) -> None:
    # Defense-in-depth (guard #2): refuse to capture a resumable snapshot
    # unless opencode's auth.json exists and is non-empty on the host. A
    # credential-less workdir is degenerate — restoring it would relaunch
    # opencode with no auth, so marking it resumable is worse than useless.
    # (Live-reach is already covered by the session_id is not None gate at
    # the call site; this covers the bad/empty-seed edge.)
    workdir = host.workdir.rstrip("/")
    chk = await host.run_command(
        f"test -s {shlex.quote(workdir)}/home/.local/share/opencode/auth.json "
        f"&& echo OK || true"
    )
    if "OK" not in chk.stdout:
        _LOG.warning(
            "snapshot capture skipped: opencode auth.json absent/empty; "
            "refusing to mark resumable"
        )
        return

    session_json = await host_actions.opencode_export(
        host, opencode_db, session_id,
        opencode_executable=opencode_executable,
    )
    expected_len_plain = len(session_json)
    _LOG.info(
        "snapshot capture: session_json plaintext bytes=%d session_id=%s",
        expected_len_plain, session_id,
    )

    encrypt = session_blob_encrypt or (lambda b: b)
    session_blob_payload = encrypt(session_json)
    expected_len_payload = len(session_blob_payload)

    async with ctx.store_blob("workdir") as wwriter:
        async for chunk in host.archive_workdir(workdir_exclude):
            await wwriter.write(chunk)
        workdir_blob_id = wwriter.file_id

    async with ctx.store_blob("session") as swriter:
        await swriter.write(session_blob_payload)
        session_blob_id = swriter.file_id
        # Belt-and-braces: GridIn._position is the byte count actually
        # written so far. Compare against the encrypted payload length
        # (NOT the plaintext length) — short-write would be a real failure.
        written = getattr(swriter, "_position", None)
        if written is not None and written != expected_len_payload:
            raise RuntimeError(
                f"snapshot session blob short-write: expected "
                f"{expected_len_payload} bytes, GridIn._position is {written}"
            )

    await insert_snapshot(
        ctx._db,
        prefix=ctx._prefix,
        process_id=ctx.process_id,
        end_state=end_state,
        session_id=session_id,
        session_blob_id=session_blob_id,
        workdir_blob_id=workdir_blob_id,
        deliverables_emitted=[],
    )
    pruned = await prune_snapshots(
        ctx._db, prefix=ctx._prefix, process_id=ctx.process_id,
    )
    for p in pruned:
        try:
            await ctx.delete_blob(p["sessionBlobId"])
        except Exception:  # noqa: BLE001
            _LOG.exception("delete_blob(session) failed")
        try:
            await ctx.delete_blob(p["workdirBlobId"])
        except Exception:  # noqa: BLE001
            _LOG.exception("delete_blob(workdir) failed")

    await ctx.mark_has_saved_state()


async def _rotate_optio_log(host: Host) -> None:
    """Append the restored optio.log to optio.log.old, then truncate optio.log.

    Preserves the historical log content across consecutive resumes
    (rather than discarding it) while ensuring `tail -F -n +1` only sees
    fresh lines emitted in the resumed run.
    """
    workdir = host.workdir.rstrip("/")
    log_abs = f"{workdir}/optio.log"
    old_abs = f"{workdir}/optio.log.old"
    try:
        current = (await host.fetch_bytes_from_host(log_abs)).decode("utf-8")
    except FileNotFoundError:
        current = ""
    if not current:
        # Nothing to rotate. Still ensure optio.log exists empty so the
        # tail process has something to follow.
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
    """Append one line to <workdir>/resume.log.

    Line format: ``<ISO 8601 UTC timestamp>[ REFRESHED:<comma-separated names>]``.
    The optional ``REFRESHED:`` suffix signals that the harness rewrote
    the listed files on this session start. Agents are instructed (via
    the resume section of AGENTS.md) to re-read tagged files.

    Creates the file if missing (via shell `>>`). Caller is responsible
    for gating this on config.supports_resume.
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
    host, hook_ctx, config: OpencodeTaskConfig,
) -> list[str]:
    """Run the on_resume_refresh hook (if any) and rewrite AGENTS.md when
    the rendered content differs from the workdir copy.

    Returns the list of filenames the harness rewrote (currently at most
    ``["AGENTS.md"]``), suitable for tagging the next ``resume.log`` line.
    A hook that raises is logged and ignored — the resumed session keeps
    whatever AGENTS.md the snapshot restored.
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
        workdir_exclude=new_config.workdir_exclude,
        supports_resume=new_config.supports_resume,
        # Reflect the refreshed config so a resume keeps the downloadables block
        # (with the right wording — host_protocol drives comparative vs standalone).
        host_protocol=new_config.host_protocol,
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


def _pick_local_workdir() -> str:
    return tempfile.mkdtemp(prefix="optio-opencode-")


def _create_opencode_session_sync(port: int, password: str, directory: str) -> str:
    """Blocking HTTP POST to opencode's /session route. Returns the new session id.

    Called via an executor from :func:`_create_opencode_session` so the main
    event loop isn't blocked on the synchronous urllib call.

    Retries on transient connect/read errors because over a freshly-opened
    SSH local forward the first request occasionally drops (asyncssh needs
    a moment before the channel is wired up).
    """
    import base64 as _b64
    import time
    import urllib.parse
    import urllib.request
    from urllib.error import URLError

    auth_token = _b64.b64encode(f"opencode:{password}".encode("utf-8")).decode("ascii")
    url = (
        f"http://127.0.0.1:{port}/session"
        f"?directory={urllib.parse.quote(directory, safe='')}"
    )
    headers = {
        "content-type": "application/json",
        "authorization": f"Basic {auth_token}",
    }

    last_exc: Exception | None = None
    for attempt in range(4):
        if attempt > 0:
            time.sleep(0.15 * attempt)
        req = urllib.request.Request(url, method="POST", data=b"{}", headers=headers)
        try:
            with urllib.request.urlopen(req, timeout=15) as resp:
                body = resp.read().decode("utf-8")
            break
        except (URLError, ConnectionError, OSError) as exc:
            last_exc = exc
            continue
    else:
        raise RuntimeError(
            f"opencode /session failed after retries: {last_exc!r}"
        )

    data = json.loads(body)
    session_id = data.get("id")
    if not isinstance(session_id, str) or not session_id:
        raise RuntimeError(
            f"opencode /session response has no string 'id' field: {body!r}"
        )
    return session_id


async def _create_opencode_session(port: int, password: str, directory: str) -> str:
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(
        None, _create_opencode_session_sync, port, password, directory
    )


def _post_opencode_prompt_sync(
    port: int, password: str, session_id: str, message: str,
) -> None:
    """Blocking HTTP POST of a kickoff prompt to opencode's session route.

    Called via an executor from :func:`_post_opencode_prompt`. Mirrors
    :func:`_create_opencode_session_sync`'s BasicAuth + transient-retry pattern
    (the first request over a freshly-opened SSH local forward occasionally
    drops while asyncssh wires up the channel).

    Targets opencode's v1 fire-and-forget route ``POST
    /session/:sessionID/prompt_async`` (same ``/session`` prefix as
    :func:`_create_opencode_session_sync`). Its ``PromptPayload`` body
    requires ``parts`` — so ``{"parts": [{"type": "text", "text": msg}]}``.
    ``prompt_async`` "starts the session if needed and returns immediately"
    (204 No Content), which is the unattended auto-start semantics we want;
    the sync ``/session/:id/message`` route blocks streaming the whole AI
    response. Instance routing comes from opencode's ``process.cwd()`` (the
    workdir), so no ``?directory=`` query is needed.

    (Earlier targets were both wrong against opencode 1.14.x and crashed the
    task: the experimental v2 route ``/api/session/:id/prompt`` 400s every
    body with ``Expected Session.Message`` — the retries exhaust, a
    RuntimeError aborts the session, opencode is torn down, and the web UI
    502s its own backend.)
    """
    import base64 as _b64
    import time
    import urllib.request
    from urllib.error import URLError

    auth_token = _b64.b64encode(f"opencode:{password}".encode("utf-8")).decode("ascii")
    url = f"http://127.0.0.1:{port}/session/{session_id}/prompt_async"
    headers = {
        "content-type": "application/json",
        "authorization": f"Basic {auth_token}",
    }
    payload = json.dumps(
        {"parts": [{"type": "text", "text": message}]}
    ).encode("utf-8")

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
    raise RuntimeError(
        f"opencode session prompt failed after retries: {last_exc!r}"
    )


async def _post_opencode_prompt(
    port: int, password: str, session_id: str, message: str,
) -> None:
    loop = asyncio.get_event_loop()
    await loop.run_in_executor(
        None, _post_opencode_prompt_sync, port, password, session_id, message
    )


def _resolve_session_model_sync(
    port: int, password: str, session_id: str,
) -> str | None:
    """Best-effort: return the operator's last-used model as
    ``"providerID/modelID"``, or None.

    GETs opencode's ``/session/:sessionID/message`` (the same ``/session``
    prefix as :func:`_create_opencode_session_sync`) and walks the message
    list; each ``info.role == "assistant"`` message carries ``providerID`` /
    ``modelID``. The LAST such message wins — the operator may switch models
    mid-session, and their final choice is the one to seed.

    Used at seed-capture time (opencode must still be alive) to synthesise the
    model default into the seed's ``opencode.json``. Returns None on any
    transport/parse error or when no assistant message exists; the caller then
    skips writing a default, leaving opencode's own resolution in place (no
    worse than before)."""
    import base64 as _b64
    import urllib.request
    from urllib.error import URLError

    auth_token = _b64.b64encode(f"opencode:{password}".encode("utf-8")).decode("ascii")
    url = f"http://127.0.0.1:{port}/session/{session_id}/message"
    req = urllib.request.Request(
        url, method="GET", headers={"authorization": f"Basic {auth_token}"},
    )
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except (URLError, ConnectionError, OSError, ValueError):
        return None
    if not isinstance(data, list):
        return None

    model: str | None = None
    for item in data:
        info = item.get("info", item) if isinstance(item, dict) else {}
        if not isinstance(info, dict) or info.get("role") != "assistant":
            continue
        prov, mod = info.get("providerID"), info.get("modelID")
        if isinstance(prov, str) and prov and isinstance(mod, str) and mod:
            model = f"{prov}/{mod}"
    return model


async def _resolve_session_model(
    port: int, password: str, session_id: str,
) -> str | None:
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(
        None, _resolve_session_model_sync, port, password, session_id
    )


def _fetch_opencode_models_sync(port: int, password: str, directory: str) -> list[str]:
    """GET opencode's ``/config/providers`` and enumerate ``"providerID/modelID"``
    ids (server-side peer of OpencodeView's client-side model fetch). Returns []
    on any transport/parse error — the probe then simply skips."""
    import base64 as _b64
    import urllib.parse
    import urllib.request
    from urllib.error import URLError

    auth_token = _b64.b64encode(f"opencode:{password}".encode("utf-8")).decode("ascii")
    url = (
        f"http://127.0.0.1:{port}/config/providers"
        f"?directory={urllib.parse.quote(directory, safe='')}"
    )
    req = urllib.request.Request(
        url, method="GET", headers={"authorization": f"Basic {auth_token}"},
    )
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except (URLError, ConnectionError, OSError, ValueError):
        return []
    return model_probe.parse_model_ids(data)


async def _fetch_opencode_models(port: int, password: str, directory: str) -> list[str]:
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(
        None, _fetch_opencode_models_sync, port, password, directory
    )


def _delete_opencode_session_sync(
    port: int, password: str, session_id: str, directory: str,
) -> None:
    """Best-effort DELETE of the throwaway probe session so its "capital of
    Hungary" turns never linger. Silently ignores failure (an idle orphan
    session is harmless — the operator session is separate and seed/snapshot
    capture reference only it)."""
    import base64 as _b64
    import urllib.parse
    import urllib.request
    from urllib.error import URLError

    auth_token = _b64.b64encode(f"opencode:{password}".encode("utf-8")).decode("ascii")
    url = (
        f"http://127.0.0.1:{port}/session/{session_id}"
        f"?directory={urllib.parse.quote(directory, safe='')}"
    )
    req = urllib.request.Request(
        url, method="DELETE", headers={"authorization": f"Basic {auth_token}"},
    )
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            resp.read()
    except (URLError, ConnectionError, OSError):
        pass


async def _delete_opencode_session(
    port: int, password: str, session_id: str, directory: str,
) -> None:
    loop = asyncio.get_event_loop()
    await loop.run_in_executor(
        None, _delete_opencode_session_sync, port, password, session_id, directory
    )


# How long to wait for the throwaway probe conversation's reader to connect its
# /global/event stream before giving up. Until it is ready, send() has no aiohttp
# session and no event stream, so probing would mark every model unusable.
_PROBE_READY_TIMEOUT = 20.0


async def _run_model_probe(
    port: int, password: str, directory: str, model_ids: list[str], report,
) -> dict[str, bool]:
    """Probe each model on a THROWAWAY opencode session (so the probe's
    "capital of Hungary" turns never pollute the operator's session), tear the
    throwaway session down, and return ``{model_id: usable}``."""
    probe_sid = await _create_opencode_session(port, password, directory)
    conv = OpencodeConversation(
        port=port, password=password, session_id=probe_sid, directory=directory,
    )
    reader = asyncio.create_task(conv.run_reader())
    try:
        # Wait for the reader to own an aiohttp session AND connect the event
        # stream before sending any probe turn. Without this gate the first
        # send() runs while conv._http is still None (the loop never got to run
        # run_reader), the turn errors, and EVERY model is marked unusable.
        try:
            await asyncio.wait_for(conv._ready.wait(), timeout=_PROBE_READY_TIMEOUT)
        except asyncio.TimeoutError:
            _LOG.warning(
                "model probe: throwaway conversation never became ready in %ss; "
                "skipping probe (unfiltered picker)", _PROBE_READY_TIMEOUT,
            )
            return {}
        return await model_probe.probe_models(conv, model_ids, report=report)
    finally:
        await conv.close()
        reader.cancel()
        try:
            await reader
        except asyncio.CancelledError:
            pass
        await _delete_opencode_session(port, password, probe_sid, directory)


async def _probe_or_cached_disabled_models(
    ctx: ProcessContext, *, worker_port: int, password: str, directory: str,
    seed_key: str | None, resuming: bool,
) -> dict[str, str]:
    """Return ``{model_id: reason}`` for models this seed's account can't use.

    opencode lists its full provider catalogue with no account flag; a model the
    account cannot run ERRORS the turn. So probe each id once on a FRESH launch
    (before the widget is shown), cache the result per seed (24h), and reuse it
    on later launches incl. resumes — see model_probe. Never probes on resume
    (the account was already determined). Best-effort: any failure yields an
    empty map (unfiltered picker)."""
    usable: dict[str, bool] | None = None
    if seed_key is not None:
        try:
            usable = await model_probe.load_probe_cache(ctx._db, ctx._prefix, seed_key)
        except Exception:  # noqa: BLE001
            _LOG.exception("model-probe cache load failed")
    if usable is None:
        if resuming:
            # A resumed session never re-probes: the account was settled on the
            # fresh run (and a cache miss here must not pay the probe cost). No
            # child is spawned — exactly like the download only spawns when a
            # download is needed.
            return {}

        # A fresh probe runs as a CHILD subtask so its "Checking available
        # models…" progress is its own node in the task tree (like the binary
        # download), not the parent's. The enumeration + probe + cache-save all
        # run under the child ctx; the child captures this parent scope (port /
        # password / directory / db) since it is an asyncio task in the same
        # process.
        async def _probe(child_ctx):
            model_ids = await _fetch_opencode_models(worker_port, password, directory)
            if not model_ids:
                return {}
            # Log the milestone ONCE, then advance the bar silently per model.
            child_ctx.report_progress(0.0, "Checking available models…")

            def _report(i: int, total: int, _mid: str) -> None:
                child_ctx.report_progress(i / total * 100.0)

            probed = await _run_model_probe(
                worker_port, password, directory, model_ids, _report,
            )
            if seed_key is not None:
                try:
                    await model_probe.save_probe_cache(
                        ctx._db, ctx._prefix, seed_key, probed,
                    )
                except Exception:  # noqa: BLE001
                    _LOG.exception("model-probe cache save failed")
            return probed

        usable = await model_probe.run_probe_child(
            ctx, name="Checking available models", description=None, probe=_probe,
        )
        if usable is None:
            # The probe child failed / never published — show the unfiltered
            # picker (best-effort).
            return {}
    return model_probe.disabled_map(usable)


async def _write_seed_model_config(host: Host, model_str: str) -> None:
    """Merge ``{"model": model_str}`` into the seed's XDG opencode config at
    ``<workdir>/home/.config/opencode/opencode.json`` (creating it if absent,
    preserving any existing keys).

    ``<workdir>/home`` is the seed manifest's ``home_subdir``, and
    ``.config/opencode/opencode.json`` is in the manifest's include list, so
    the file travels in the captured seed. On consume, opencode's
    ``defaultModel()`` reads ``cfg.model`` first — so an unattended seeded
    session (auto-start ``prompt_async`` sends no model) runs the operator's
    model instead of the first-provider fallback (anthropic with no key →
    ``invalid x-api-key``)."""
    cfg_dir = f"{host.workdir}/home/.config/opencode"
    cfg_path = f"{cfg_dir}/opencode.json"
    await host.run_command(f"mkdir -p {shlex.quote(cfg_dir)}")

    existing: dict = {}
    r = await host.run_command(f"cat {shlex.quote(cfg_path)}")
    if r.exit_code == 0 and r.stdout.strip():
        try:
            parsed = json.loads(r.stdout)
            if isinstance(parsed, dict):
                existing = parsed
        except ValueError:
            existing = {}
    existing["model"] = model_str
    await host.write_text(
        "home/.config/opencode/opencode.json", json.dumps(existing, indent=2),
    )


def create_opencode_task(
    process_id: str,
    name: str,
    config: OpencodeTaskConfig,
    description: str | None = None,
    metadata: dict | None = None,
) -> TaskInstance:
    """Return a TaskInstance that runs one opencode web session.

    ``metadata`` is the caller app's task-tagging payload (for later
    filter/select/identify); it is stamped onto the TaskInstance verbatim and
    never read by the task itself. Construction is the caller's concern — this
    factory only accepts and forwards it.
    """

    async def _execute(ctx: ProcessContext) -> None:
        await run_opencode_session(ctx, config)

    return TaskInstance(
        execute=_execute,
        process_id=process_id,
        name=name,
        description=description,
        ui_widget=(
            "iframe" if config.mode == "iframe"
            else ("conversation" if config.conversation_ui else None)
        ),
        supports_resume=config.supports_resume,
        metadata=metadata or {},
    )
