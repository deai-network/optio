"""The state machine that runs one optio-opencode session.

Orchestrates a Host (local or remote) through the lifecycle described in
Section 5 of the design spec.  The public entry point is the factory
``create_opencode_task(...)`` which wraps ``run_opencode_session`` in a
``TaskInstance`` and sets ``ui_widget="iframe"``.
"""

from __future__ import annotations

import asyncio
import base64
import json
import logging
import os
import secrets
import tempfile
from typing import AsyncIterator

from optio_core.context import ProcessContext
from optio_core.models import BasicAuth, TaskInstance

from optio_opencode.host import Host, LocalHost, LaunchedProcess, RemoteHost
from optio_opencode.logparse import (
    DeliverableEvent,
    DoneEvent,
    ErrorEvent,
    LogEvent,
    StatusEvent,
    UnknownLine,
    parse_log_line,
    validate_deliverable_path,
)
from optio_opencode.paths import local_taskdir, remote_taskdir
from optio_opencode.prompt import compose_agents_md
from optio_opencode.snapshots import (
    insert_snapshot,
    load_latest_snapshot,
    prune_snapshots,
)
from optio_opencode.types import DeliverableCallback, OpencodeTaskConfig


_LOG = logging.getLogger(__name__)


READY_TIMEOUT_S = 30.0
DELIVERABLE_QUEUE_BOUND = 64


class _SessionFailed(Exception):
    """Raised by the session loop to drive the process to 'failed'."""


async def run_opencode_session(ctx: ProcessContext, config: OpencodeTaskConfig) -> None:
    """Execute function body for one optio-opencode task instance."""
    # --- per-task filesystem layout ---------------------------------------
    if config.ssh is None:
        taskdir = local_taskdir(ctx.process_id)
        os.makedirs(taskdir, exist_ok=True)
        host: Host = LocalHost(taskdir=taskdir)
        os.makedirs(host.workdir, exist_ok=True)
        opencode_db = os.path.join(taskdir, "opencode.db")
    else:
        taskdir = remote_taskdir(ctx.process_id)
        host = RemoteHost(ssh_config=config.ssh, taskdir=taskdir)
        opencode_db = f"{taskdir}/opencode.db"

    password = secrets.token_urlsafe(32)
    process: LaunchedProcess | None = None
    cancelled = False
    preserved_session_id: str | None = None
    session_id: str | None = None

    # --- resume decision --------------------------------------------------
    resume_requested = bool(getattr(ctx, "resume", False))
    snapshot: dict | None = None
    if resume_requested:
        snapshot = await load_latest_snapshot(
            ctx._db, prefix=ctx._prefix, process_id=ctx.process_id,
        )
    resuming = snapshot is not None

    try:
        await host.connect()
        await host.setup_workdir()
        await host.remove_file(opencode_db)

        if resuming:
            try:
                await host.restore_workdir(_stream_blob(ctx, snapshot["workdirBlobId"]))
                session_bytes = await _read_blob_bytes(ctx, snapshot["sessionBlobId"])
                await host.opencode_import(opencode_db, session_bytes)
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
                "AGENTS.md", compose_agents_md(config.consumer_instructions),
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
            target = await host.detect_target()
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

            await host.install_opencode_binary(candidate, progress=_on_progress)
            ctx.report_progress(None, "opencode binary ready")
        else:
            await host.ensure_opencode_installed(config.install_if_missing)

        # --- launch ------------------------------------------------------
        version = await host.opencode_version()
        version_suffix = f" {version}" if version else ""
        ctx.report_progress(None, f"Launching opencode{version_suffix}…")
        process = await host.launch_opencode(
            password=password,
            ready_timeout_s=READY_TIMEOUT_S,
            env={"OPENCODE_DB": opencode_db},
        )

        # --- tunnel + widget registration --------------------------------
        worker_port = await host.establish_tunnel(process.opencode_port)

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

        # --- run --------------------------------------------------------
        deliverable_queue: asyncio.Queue[str] = asyncio.Queue(
            maxsize=DELIVERABLE_QUEUE_BOUND
        )
        done_flag = asyncio.Event()
        error_flag: list[str | None] = []  # [message] or [] if not fired
        subprocess_exit: list[int | None] = []  # [exit_code] when seen

        fetch_task = asyncio.create_task(
            _deliverable_fetch_loop(host, config.on_deliverable, deliverable_queue, ctx)
        )
        tail_task = asyncio.create_task(
            _tail_and_dispatch(
                host, ctx, deliverable_queue, done_flag, error_flag
            )
        )
        exit_task = asyncio.create_task(_await_subprocess_exit(host, process, subprocess_exit))
        cancel_task = asyncio.create_task(_watch_cancellation(ctx))

        done, _ = await asyncio.wait(
            {tail_task, exit_task, cancel_task},
            return_when=asyncio.FIRST_COMPLETED,
        )

        cancelled = (
            cancel_task in done
            and not cancel_task.cancelled()
            and cancel_task.exception() is None
            and cancel_task.result() is True
        )

        if error_flag:
            raise _SessionFailed(error_flag[0] or "opencode reported ERROR")
        # Any subprocess exit without a DONE is a failure, regardless of exit
        # code (opencode web is designed to run indefinitely until the LLM
        # writes DONE or ERROR). Cancellation is handled separately via
        # ``cancelled`` — if the user cancelled, we return cleanly.
        if subprocess_exit and not done_flag.is_set() and not cancelled:
            raise _SessionFailed(
                f"opencode exited with code {subprocess_exit[0]} before DONE"
            )

        # Drain remaining deliverables before returning.
        await deliverable_queue.join()

        # Cancel the still-running watchers.
        for t in (tail_task, exit_task, cancel_task, fetch_task):
            if not t.done():
                t.cancel()
        await asyncio.gather(
            tail_task, exit_task, cancel_task, fetch_task, return_exceptions=True
        )

    except _SessionFailed as fail:
        raise RuntimeError(str(fail)) from None

    finally:
        if process is not None:
            try:
                await host.terminate_opencode(process, aggressive=cancelled)
            except Exception:  # noqa: BLE001
                _LOG.exception("terminate_opencode failed")

        if session_id is not None:
            try:
                await _capture_snapshot(
                    ctx, host,
                    session_id=preserved_session_id or session_id,
                    opencode_db=opencode_db,
                    end_state="cancelled" if cancelled else "done",
                    workdir_exclude=config.workdir_exclude,
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
) -> None:
    session_json = await host.opencode_export(opencode_db, session_id)

    async with ctx.store_blob("workdir") as wwriter:
        async for chunk in host.archive_workdir(workdir_exclude):
            await wwriter.write(chunk)
        workdir_blob_id = wwriter.file_id

    async with ctx.store_blob("session") as swriter:
        await swriter.write(session_json)
        session_blob_id = swriter.file_id

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


async def _tail_and_dispatch(
    host: Host,
    ctx: ProcessContext,
    deliverable_queue: asyncio.Queue[str],
    done_flag: asyncio.Event,
    error_flag: list,
) -> None:
    """Consume tail_log, parse each line, dispatch by keyword."""
    async for line in host.tail_log():
        ev: LogEvent = parse_log_line(line)
        if isinstance(ev, StatusEvent):
            ctx.report_progress(ev.percent, ev.message)
        elif isinstance(ev, DeliverableEvent):
            ctx.report_progress(None, f"Deliverable: {ev.path}")
            try:
                resolved = validate_deliverable_path(ev.path, host.workdir)
            except ValueError:
                ctx.report_progress(
                    None, f"invalid deliverable path {ev.path!r}, skipping"
                )
                continue
            try:
                deliverable_queue.put_nowait(resolved)
            except asyncio.QueueFull:
                await deliverable_queue.put(resolved)
        elif isinstance(ev, DoneEvent):
            if ev.summary:
                ctx.report_progress(None, ev.summary)
            done_flag.set()
            return
        elif isinstance(ev, ErrorEvent):
            error_flag.append(ev.message)
            return
        else:
            assert isinstance(ev, UnknownLine)
            if ev.text:
                ctx.report_progress(None, ev.text)


async def _deliverable_fetch_loop(
    host: Host,
    callback: DeliverableCallback | None,
    queue: asyncio.Queue[str],
    ctx: ProcessContext,
) -> None:
    """Consume resolved deliverable paths and invoke the callback."""
    while True:
        path = await queue.get()
        try:
            try:
                text = await host.fetch_deliverable_text(path)
            except UnicodeDecodeError:
                ctx.report_progress(
                    None, f"Deliverable {path}: not valid UTF-8, skipping callback"
                )
                continue
            except FileNotFoundError:
                ctx.report_progress(None, f"Deliverable {path}: not found")
                continue
            except Exception as exc:  # noqa: BLE001
                # Unexpected fetch error (SFTP drop, permission, etc.).  Log
                # and continue so the main loop's queue.join() does not hang.
                ctx.report_progress(
                    None, f"Deliverable {path}: fetch failed: {exc!r}, skipping"
                )
                continue

            if callback is None:
                continue
            try:
                await callback(path, text)
            except Exception as exc:  # noqa: BLE001
                ctx.report_progress(
                    None, f"on_deliverable callback raised: {exc!r}"
                )
        finally:
            queue.task_done()


async def _await_subprocess_exit(host: Host, process: LaunchedProcess, out: list) -> None:
    """Wait for the opencode process to exit and record its exit code."""
    # LocalHost's pid_like is an asyncio.subprocess.Process whose wait() returns int.
    # RemoteHost's pid_like is an asyncssh.SSHClientProcess whose wait() returns
    # an SSHCompletedProcess with .exit_status.
    proc = process.pid_like
    if not hasattr(proc, "wait"):
        raise TypeError(f"LaunchedProcess.pid_like {proc!r} does not expose .wait()")
    result = await proc.wait()  # type: ignore[union-attr]
    if hasattr(result, "exit_status"):
        out.append(result.exit_status)
    else:
        # asyncio case: result is already the exit code (int).
        out.append(result)


async def _watch_cancellation(ctx: ProcessContext) -> bool:
    """Return True when the process is cancelled."""
    while ctx.should_continue():
        await asyncio.sleep(0.1)
    return True


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
        supports_resume=True,
    )
