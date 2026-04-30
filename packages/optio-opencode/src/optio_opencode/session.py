"""The state machine that runs one optio-opencode session.

Orchestrates a Host (local or remote) through the lifecycle described in
Section 4 of the design spec.  The public entry point is the factory
``create_opencode_task(...)`` which wraps ``run_opencode_session`` in a
``TaskInstance`` and sets ``ui_widget="iframe"``.

Most of the per-session work is generic log/deliverables protocol
plumbing (parse ``optio.log``, fetch deliverables, watch for cancel) and
lives in ``optio_host.protocol.run_log_protocol_session``.  This module
keeps only the opencode-specific work — write AGENTS.md / opencode.json,
install/launch the opencode binary, set up tunnel and widget, and the
resume/snapshot brackets around the protocol session.
"""

from __future__ import annotations

import asyncio
import base64
import json
import logging
import os
import secrets
import shlex
import tempfile
from datetime import datetime, timezone
from typing import AsyncIterator

from optio_core.context import ProcessContext
from optio_core.models import BasicAuth, TaskInstance

from optio_host.context import HookContext
from optio_host.host import Host, LocalHost, ProcessHandle, RemoteHost
from optio_host.paths import task_dir
from optio_host.protocol.session import _SessionFailed, run_log_protocol_session
from optio_opencode import host_actions
from optio_opencode.prompt import compose_agents_md
from optio_opencode.snapshots import (
    insert_snapshot,
    load_latest_snapshot,
    prune_snapshots,
)
from optio_opencode.types import OpencodeTaskConfig


_LOG = logging.getLogger(__name__)


READY_TIMEOUT_S = 30.0


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

    # --- resume decision (BEFORE the protocol session starts) -------------
    resume_requested = bool(getattr(ctx, "resume", False))
    snapshot: dict | None = None
    if resume_requested:
        snapshot = await load_latest_snapshot(
            ctx._db, prefix=ctx._prefix, process_id=ctx.process_id,
        )

    async def _opencode_body(host: Host, hook_ctx: HookContext) -> None:
        """Opencode-specific body that runs inside the protocol driver.

        Captures launch state via nonlocal so the outer ``finally`` can
        terminate the subprocess and capture the snapshot.
        """
        nonlocal launched_handle, opencode_exec, session_id, preserved_session_id

        # The protocol driver has already created the workdir, deliverables/
        # subdirectory and an empty optio.log.  Ensure the opencode db from a
        # prior run (if any) is gone before we either restore a snapshot or
        # start fresh.
        await host.remove_file(opencode_db)

        resuming = snapshot is not None
        if resuming:
            try:
                await host.restore_workdir(_stream_blob(ctx, snapshot["workdirBlobId"]))
                session_bytes = await _read_blob_bytes(ctx, snapshot["sessionBlobId"])
                await host_actions.opencode_import(
                    host, opencode_db, session_bytes,
                    opencode_executable=opencode_exec,
                )
                # Move the restored log channel out of the way before tail
                # subscribes. The snapshot tar includes optio.log from the
                # previous run; without this `tail -F -n +1` would re-emit
                # every old DELIVERABLE / DONE / ERROR line and the resumed
                # process would terminate within seconds of launch (see
                # LocalHost.tail_log's "-n +1" choice). We preserve the
                # historical content by appending it to optio.log.old so
                # nothing is lost across consecutive resumes.
                await _rotate_optio_log(host)
                preserved_session_id = snapshot["sessionId"]
            except Exception:
                _LOG.exception(
                    "resume restore failed; falling back to fresh-start path "
                    "(Mongo blob preserved for inspection)",
                )
                await host.remove_file(opencode_db)
                resuming = False
                preserved_session_id = None

        if not resuming:
            await host.write_text(
                "AGENTS.md",
                compose_agents_md(
                    config.consumer_instructions,
                    workdir_exclude=config.workdir_exclude,
                    supports_resume=config.supports_resume,
                ),
            )
            await host.write_text(
                "opencode.json", json.dumps(config.opencode_config, indent=2),
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

        if config.supports_resume:
            await _append_resume_log_entry(host)

        # --- install ----------------------------------------------------
        # When OPTIO_OPENCODE_BINARY_DIR is set, we ship a platform-matched
        # binary from that directory onto the host (SFTP for remote, direct
        # exec for local) and skip the upstream curl installer entirely.
        # The directory layout matches opencode's build output:
        # ``<binary_dir>/opencode-<os>-<arch>[-baseline][-musl]/bin/opencode``.
        # This is how we ship the iframe-embeddability fork until those
        # fixes land upstream.
        binary_dir = os.environ.get("OPTIO_OPENCODE_BINARY_DIR")
        if binary_dir:
            target = await host_actions.detect_target(host)
            candidate = os.path.join(
                binary_dir, target.directory_name, "bin", "opencode"
            )
            if not os.path.isfile(candidate):
                raise RuntimeError(
                    f"OPTIO_OPENCODE_BINARY_DIR={binary_dir!r} does not contain "
                    f"a binary for target {target.directory_name!r} "
                    f"(expected {candidate!r})"
                )
            ctx.report_progress(
                None, f"Installing opencode binary ({target.directory_name})…"
            )

            _last_pct = -1

            def _on_progress(transferred: int, total: int) -> None:
                nonlocal _last_pct
                if total <= 0:
                    return
                pct = int(transferred * 100 / total)
                if pct == _last_pct:
                    return
                _last_pct = pct
                # Percent-only update: no message, so ProcessContext will
                # advance the progress bar without appending a log entry.
                ctx.report_progress(pct)

            opencode_exec = await host_actions.install_opencode_binary(
                host, candidate, progress=_on_progress,
            )
            ctx.report_progress(None, "opencode binary ready")
        else:
            await host_actions.ensure_opencode_installed(
                host, config.install_if_missing,
            )

        # --- launch ------------------------------------------------------
        version = await host_actions.opencode_version(
            host, opencode_executable=opencode_exec,
        )
        version_suffix = f" {version}" if version else ""
        ctx.report_progress(None, f"Launching opencode{version_suffix}…")
        handle, opencode_port = await host_actions.launch_opencode(
            host, password,
            ready_timeout_s=READY_TIMEOUT_S,
            opencode_executable=opencode_exec,
        )
        launched_handle = handle

        # --- tunnel + widget registration --------------------------------
        worker_port = await host.establish_tunnel(opencode_port)

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
            f"http://127.0.0.1:{worker_port}",
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
    session_error: BaseException | None = None
    await host.connect()
    try:
        await run_log_protocol_session(
            host, ctx,
            body=_opencode_body,
            on_deliverable=config.on_deliverable,
            before_execute=config.before_execute,
            after_execute=config.after_execute,
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

        if launched_handle is not None:
            try:
                await host.terminate_subprocess(launched_handle, aggressive=cancelled)
            except Exception:  # noqa: BLE001
                _LOG.exception("terminate_subprocess failed")

        if config.supports_resume and session_id is not None:
            try:
                await _capture_snapshot(
                    ctx, host,
                    session_id=preserved_session_id or session_id,
                    opencode_db=opencode_db,
                    end_state="cancelled" if cancelled else "done",
                    workdir_exclude=config.workdir_exclude,
                    opencode_executable=opencode_exec,
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


async def _capture_snapshot(
    ctx: ProcessContext,
    host: Host,
    *,
    session_id: str,
    opencode_db: str,
    end_state: str,
    workdir_exclude: list[str] | None,
    opencode_executable: str = "opencode",
) -> None:
    session_json = await host_actions.opencode_export(
        host, opencode_db, session_id,
        opencode_executable=opencode_executable,
    )
    expected_len = len(session_json)
    _LOG.info(
        "snapshot capture: session_json bytes=%d session_id=%s",
        expected_len, session_id,
    )

    async with ctx.store_blob("workdir") as wwriter:
        async for chunk in host.archive_workdir(workdir_exclude):
            await wwriter.write(chunk)
        workdir_blob_id = wwriter.file_id

    async with ctx.store_blob("session") as swriter:
        await swriter.write(session_json)
        session_blob_id = swriter.file_id
        # Belt-and-braces: GridIn._position is the byte count actually
        # written so far. After a single write of `session_json`, this
        # must equal len(session_json). Mismatch indicates the write was
        # truncated (we hit this once when asyncssh's stdout collection
        # was cut short by cancellation; see RemoteHost.opencode_export).
        written = getattr(swriter, "_position", None)
        if written is not None and written != expected_len:
            raise RuntimeError(
                f"snapshot session blob short-write: expected "
                f"{expected_len} bytes, GridIn._position is {written}"
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
        current = await host.fetch_deliverable_text(log_abs)
    except FileNotFoundError:
        current = ""
    if not current:
        # Nothing to rotate. Still ensure optio.log exists empty so the
        # tail process has something to follow.
        await host.write_text("optio.log", "")
        return
    try:
        existing_old = await host.fetch_deliverable_text(old_abs)
    except FileNotFoundError:
        existing_old = ""
    await host.write_text("optio.log.old", existing_old + current)
    await host.write_text("optio.log", "")


async def _append_resume_log_entry(host) -> None:
    """Append one ISO 8601 UTC timestamp line to <workdir>/resume.log.

    Creates the file if missing (via shell `>>`). Caller is responsible
    for gating this on config.supports_resume.
    """
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    target = f"{host.workdir}/resume.log"
    result = await host.run_command(
        f"echo {shlex.quote(ts)} >> {shlex.quote(target)}"
    )
    if result.exit_code != 0:
        raise RuntimeError(
            f"failed to append to resume.log: exit {result.exit_code}: "
            f"{result.stderr!r}"
        )


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


def create_opencode_task(
    process_id: str,
    name: str,
    config: OpencodeTaskConfig,
    description: str | None = None,
) -> TaskInstance:
    """Return a TaskInstance that runs one opencode web session."""

    async def _execute(ctx: ProcessContext) -> None:
        await run_opencode_session(ctx, config)

    return TaskInstance(
        execute=_execute,
        process_id=process_id,
        name=name,
        description=description,
        ui_widget="iframe",
        supports_resume=config.supports_resume,
    )
