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

import logging
import os
import shlex
from datetime import datetime, timezone
from typing import AsyncIterator, Callable

from optio_core.context import ProcessContext
from optio_core.models import TaskInstance

from optio_host.context import HookContext
from optio_host.host import Host, LocalHost, ProcessHandle, RemoteHost
from optio_host.paths import task_dir
from optio_host.protocol.session import _SessionFailed, run_log_protocol_session

from optio_claudecode import host_actions
from optio_claudecode.prompt import compose_agents_md
from optio_claudecode.snapshots import (
    insert_snapshot,
    load_latest_snapshot,
    prune_snapshots,
)
from optio_claudecode.types import ClaudeCodeTaskConfig


_LOG = logging.getLogger(__name__)

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


async def run_claudecode_session(
    ctx: ProcessContext, config: ClaudeCodeTaskConfig,
) -> None:
    """Execute function body for one optio-claudecode task instance."""
    host: Host = _build_host(config, ctx.process_id)
    launched_handle: ProcessHandle | None = None
    cancelled = False

    await host.connect()
    await host.setup_workdir()

    hook_ctx_outer = HookContext(ctx, host)
    claude_path = await host_actions.ensure_claude_installed(
        hook_ctx_outer,
        install_if_missing=config.install_if_missing,
        install_dir=config.claude_install_dir,
    )
    ttyd_path = await host_actions.ensure_ttyd_installed(
        hook_ctx_outer,
        install_if_missing=config.install_ttyd_if_missing,
        install_dir=config.ttyd_install_dir,
    )

    # --- resume decision (BEFORE the protocol session starts) -------------
    # Restore must happen before run_log_protocol_session subscribes its
    # tail, so the driver does not replay the previous run's DONE/ERROR
    # out of the restored optio.log.
    resume_requested = bool(getattr(ctx, "resume", False))
    snapshot: dict | None = None
    if resume_requested:
        snapshot = await load_latest_snapshot(
            ctx._db, prefix=ctx._prefix, process_id=ctx.process_id,
        )

    resuming = snapshot is not None
    if resuming:
        # Plaintext workdir first (establishes the tree incl. home/), then
        # decrypt + extract home/.claude on top. Decrypt failure is treated
        # as tampering/key-rotation and propagated — never silent
        # fresh-start (the decrypt call is intentionally outside any
        # except, so it surfaces straight to the caller).
        await host.restore_workdir(_stream_blob(ctx, snapshot["workdirBlobId"]))
        payload = await _read_blob_bytes(ctx, snapshot["sessionBlobId"])
        decrypt = config.session_blob_decrypt or (lambda b: b)
        plain = decrypt(payload)
        await _extract_home_claude(host, plain)
        await _rotate_optio_log(host)

    async def _claudecode_body(host: Host, hook_ctx: HookContext) -> None:
        nonlocal launched_handle

        refreshed_files: list[str] = []
        if not resuming:
            # Fresh start: protocol driver has created workdir,
            # deliverables/, and an empty optio.log. Plant per-task HOME
            # files and AGENTS.md before launching ttyd.
            await host_actions.plant_home_files(
                host,
                credentials_json=config.credentials_json,
                claude_config=config.claude_config,
            )
            await host.write_text(
                "AGENTS.md",
                compose_agents_md(
                    config.consumer_instructions,
                    workdir_exclude=config.workdir_exclude,
                    supports_resume=config.supports_resume,
                ),
            )
        else:
            # Resume: home/.claude (credentials, settings) was restored from
            # the session blob — do NOT re-plant. Optionally refresh AGENTS.md.
            refreshed_files = await _maybe_refresh_on_resume(host, hook_ctx, config)

        if config.supports_resume:
            await _append_resume_log_entry(host, refreshed=refreshed_files)

        if config.before_execute is not None:
            await config.before_execute(hook_ctx)

        # Network binding (same env handling as opencode for multi-container deploys)
        bind_addr = os.environ.get("OPTIO_WIDGET_TUNNEL_BIND", "127.0.0.1")
        upstream_host = os.environ.get("OPTIO_WIDGET_TUNNEL_HOST", "127.0.0.1")
        ttyd_iface = bind_addr if isinstance(host, LocalHost) else "127.0.0.1"

        claude_flags = host_actions.build_claude_flags(
            permission_mode=config.permission_mode,
            allowed_tools=config.allowed_tools,
            disallowed_tools=config.disallowed_tools,
            resuming=resuming,
        )
        ctx.report_progress(None, "Launching claude (ttyd)…")
        handle, ttyd_port = await host_actions.launch_ttyd_with_claude(
            host,
            ttyd_path=ttyd_path,
            claude_path=claude_path,
            bind_iface=ttyd_iface,
            extra_env=config.env,
            claude_flags=claude_flags,
            ready_timeout_s=READY_TIMEOUT_S,
        )
        launched_handle = handle

        worker_port = await host.establish_tunnel(ttyd_port, bind_addr=bind_addr)
        await ctx.set_widget_upstream(f"http://{upstream_host}:{worker_port}")
        await ctx.set_widget_data({
            "iframeSrc": "{widgetProxyUrl}/",
        })
        ctx.report_progress(None, "claude is live")

        # Await ttyd subprocess exit. Protocol driver cancels this body
        # when it sees DONE/ERROR; otherwise we get here only on a
        # premature exit, which the driver detects as failure.
        proc = launched_handle.pid_like
        await proc.wait()  # type: ignore[union-attr]

    try:
        await run_log_protocol_session(
            host, ctx,
            body=_claudecode_body,
            on_deliverable=config.on_deliverable,
            after_execute=config.after_execute,
        )
    except _SessionFailed as fail:
        raise RuntimeError(str(fail)) from None
    finally:
        if not ctx.should_continue():
            cancelled = True
        if launched_handle is not None:
            try:
                await host.terminate_subprocess(launched_handle, aggressive=cancelled)
            except Exception:
                _LOG.exception("terminate_subprocess failed")

        if config.supports_resume:
            try:
                await _capture_snapshot(
                    ctx, host,
                    end_state="cancelled" if cancelled else "done",
                    workdir_exclude=config.workdir_exclude,
                    session_blob_encrypt=config.session_blob_encrypt,
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


async def _archive_home_claude(host: Host) -> bytes:
    """tar.gz the sensitive ``home/.claude`` subtree and fetch it as bytes."""
    workdir = host.workdir.rstrip("/")
    tmpfile = f"{workdir}/.optio-claudecode-session.tar.gz"
    r = await host.run_command(
        f"tar -czf {shlex.quote(tmpfile)} -C {shlex.quote(workdir)} home/.claude"
    )
    if r.exit_code != 0:
        raise RuntimeError(
            f"tar home/.claude failed (exit {r.exit_code}): "
            f"{r.stderr.strip()[:200]}"
        )
    try:
        return await host.fetch_bytes_from_host(tmpfile)
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


async def _capture_snapshot(
    ctx: ProcessContext,
    host: Host,
    *,
    end_state: str,
    workdir_exclude: list[str] | None,
    session_blob_encrypt: "Callable[[bytes], bytes] | None" = None,
) -> None:
    # 1. tar the sensitive subtree into bytes.
    session_bytes = await _archive_home_claude(host)

    # 2. encrypt (or plaintext fallthrough).
    encrypt = session_blob_encrypt or (lambda b: b)
    payload = encrypt(session_bytes)
    expected_len = len(payload)

    # 3. write the session blob.
    async with ctx.store_blob("session") as swriter:
        await swriter.write(payload)
        session_blob_id = swriter.file_id
        written = getattr(swriter, "_position", None)
        if written is not None and written != expected_len:
            raise RuntimeError(
                f"snapshot session blob short-write: expected "
                f"{expected_len} bytes, GridIn._position is {written}"
            )

    # 4. defensive wipe so the workdir tar cannot carry sensitive state.
    workdir = host.workdir.rstrip("/")
    await host.run_command(f"rm -rf {shlex.quote(workdir)}/home/.claude")

    # 5. stream the plaintext workdir tar.
    async with ctx.store_blob("workdir") as wwriter:
        async for chunk in host.archive_workdir(workdir_exclude):
            await wwriter.write(chunk)
        workdir_blob_id = wwriter.file_id

    # 6. insert the snapshot doc.
    await insert_snapshot(
        ctx._db,
        prefix=ctx._prefix,
        process_id=ctx.process_id,
        end_state=end_state,
        session_blob_id=session_blob_id,
        workdir_blob_id=workdir_blob_id,
        deliverables_emitted=[],
    )

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

    # 8. surface the Resume affordance in the dashboard.
    await ctx.mark_has_saved_state()


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
    """Run on_resume_refresh (if any) and rewrite AGENTS.md when changed.

    Returns the list of filenames rewritten (currently at most
    ``["AGENTS.md"]``). A hook that raises is logged and ignored.
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


def create_claudecode_task(
    process_id: str,
    name: str,
    config: ClaudeCodeTaskConfig,
    description: str | None = None,
) -> TaskInstance:
    """Return a TaskInstance that runs one optio-claudecode session."""

    async def _execute(ctx: ProcessContext) -> None:
        await run_claudecode_session(ctx, config)

    return TaskInstance(
        execute=_execute,
        process_id=process_id,
        name=name,
        description=description,
        ui_widget="iframe",
        supports_resume=config.supports_resume,
    )
