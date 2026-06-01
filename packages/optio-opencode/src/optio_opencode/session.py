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
from optio_host.host import Host, LocalHost, ProcessHandle, RemoteHost
from optio_host.paths import task_dir
from optio_agents.protocol.session import _SessionFailed, run_log_protocol_session
from optio_agents import seeds as _seeds
from optio_opencode import host_actions
from optio_opencode.prompt import compose_agents_md
from optio_opencode.seed_manifest import OPENCODE_SEED_MANIFEST, OPENCODE_SEED_SUFFIX
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
    """
    taskdir = task_dir(
        ssh=config.ssh, process_id=process_id, consumer_name="optio-opencode",
    )
    if config.ssh is None:
        os.makedirs(taskdir, exist_ok=True)
        host: Host = LocalHost(taskdir=taskdir)
        os.makedirs(host.workdir, exist_ok=True)
        return host
    else:
        return RemoteHost(ssh_config=config.ssh, taskdir=taskdir)


async def run_opencode_session(ctx: ProcessContext, config: OpencodeTaskConfig) -> None:
    """Execute function body for one optio-opencode task instance."""
    # --- per-task filesystem layout ---------------------------------------
    host: Host = _build_host(config, ctx.process_id)
    protocol = get_protocol(browser="suppress")
    taskdir = task_dir(
        ssh=config.ssh, process_id=ctx.process_id, consumer_name="optio-opencode",
    )
    opencode_db = f"{taskdir}/opencode.db"

    password = secrets.token_urlsafe(32)
    cancelled = False
    launched_handle: ProcessHandle | None = None
    opencode_exec: str = "opencode"
    session_id: str | None = None
    preserved_session_id: str | None = None
    # Worker-side opencode port; hoisted so the finally can query the live
    # server (for the seed model default) before terminating it.
    worker_port: int | None = None

    # --- resume decision (BEFORE the protocol session starts) -------------
    resume_requested = bool(getattr(ctx, "resume", False))
    snapshot: dict | None = None
    if resume_requested:
        snapshot = await load_latest_snapshot(
            ctx._db, prefix=ctx._prefix, process_id=ctx.process_id,
        )

    # Connect + install BEFORE deciding fresh vs resume. The resume path
    # needs ``opencode import`` to replay the saved session DB, which
    # requires opencode to be installed on the host and resolved to an
    # absolute path. Hoisting also lets the fresh path skip the redundant
    # ``host.connect()`` later. ``setup_workdir`` is idempotent (mkdir -p)
    # and the protocol driver still calls it again for the fresh path —
    # harmless. Install progress reports through ``ctx``, so the
    # dashboard sees activity from the very first step.
    await host.connect()
    await host.setup_workdir()
    opencode_exec = await host_actions.ensure_opencode_installed(
        HookContext(ctx, host),
        install_if_missing=config.install_if_missing,
        install_dir=config.opencode_install_dir,
    )

    # Resume restore must run BEFORE the protocol session begins, so the
    # driver's tail_task does not subscribe to the restored stale optio.log
    # (which contains last run's DONE / ERROR events). The body below sees
    # ``resuming`` already decided.
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
        nonlocal worker_port

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
                    config.consumer_instructions,
                    documentation=protocol.documentation,
                    workdir_exclude=config.workdir_exclude,
                    supports_resume=config.supports_resume,
                ),
            )
            await host.write_text(
                "opencode.json", json.dumps(config.opencode_config, indent=2),
            )
            if config.seed_id is not None:
                # Seeded fresh: overlay the stored environment into
                # <workdir>/home, where the launch's XDG_DATA_HOME /
                # XDG_CONFIG_HOME point, so the seeded auth.json / opencode.json
                # are used. Begins a NEW session — no resume.
                await _seeds.merge_seed(
                    ctx, host,
                    seed_id=config.seed_id,
                    manifest=OPENCODE_SEED_MANIFEST,
                    suffix=OPENCODE_SEED_SUFFIX,
                    decrypt=config.session_blob_decrypt,
                )
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

        ctx.report_progress(None, f"Launching opencode{version_suffix}…")
        handle, opencode_port = await host_actions.launch_opencode(
            host, password,
            ready_timeout_s=READY_TIMEOUT_S,
            opencode_executable=opencode_exec,
            hostname=opencode_hostname,
            extra_env=hook_ctx.browser_launch_env,
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
            "localStorageOverrides": {
                "opencode.settings.dat:defaultServerUrl": "{widgetProxyUrl}",
            },
        })
        ctx.report_progress(None, "opencode is live")

        # auto_start: on a fresh launch, POST the kickoff prompt to the
        # pre-created session so opencode starts the task unattended.
        # Suppressed on resume (the restored session already carries its
        # conversation; re-prompting would re-trigger the task).
        if config.auto_start and not resuming:
            await _post_opencode_prompt(
                worker_port, password, session_id, AUTO_START_PROMPT,
            )

        # --- await opencode subprocess exit -----------------------------
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

    # --- run the protocol session -----------------------------------------
    # host.connect() already happened up-front (before install + resume).
    session_error: BaseException | None = None
    try:
        # before_execute is wired manually inside _opencode_body (after
        # install, before launch) per opencode's documented timing.
        # after_execute is left to the protocol driver — it fires after
        # the body terminates and before the outer finally runs the
        # snapshot capture, matching the documented contract.
        await run_log_protocol_session(
            host, ctx,
            body=_opencode_body,
            on_deliverable=config.on_deliverable,
            after_execute=config.after_execute,
            protocol=protocol,
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

        if not resuming and config.on_seed_saved is not None:
            try:
                if seed_model is not None:
                    # Write the model default into the seed's opencode.json
                    # before capture so it travels in the seed.
                    await _write_seed_model_config(host, seed_model)
                seed_id_out = await _seeds.capture_seed(
                    ctx, host,
                    manifest=OPENCODE_SEED_MANIFEST,
                    suffix=OPENCODE_SEED_SUFFIX,
                    encrypt=config.session_blob_encrypt,
                )
                await _call_maybe_async(config.on_seed_saved, seed_id_out)
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
        ui_widget="iframe",
        supports_resume=config.supports_resume,
        metadata=metadata or {},
    )
