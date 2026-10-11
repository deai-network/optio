"""Resurrect hook for optio-claudecode (TaskInstance.resurrect).

Saves the work a failed run left on its host as a fresh snapshot, then
removes the task directory; optio-core resumes from that snapshot afterwards.
Spec: docs/2026-10-07-resurrect-failed-session-design.md
"""

from __future__ import annotations

import re
import shlex

from bson import ObjectId
from motor.motor_asyncio import AsyncIOMotorDatabase

from optio_core import NothingToResurrect
from optio_core.context import ProcessContext

from optio_claudecode import host_actions
from optio_claudecode import session as S
from optio_claudecode.pending_captures import (
    load_pending_capture,
    pending_conversation_blob_id,
    settle_pending_capture,
)
from optio_claudecode.snapshots import _collection as _snapshots, load_latest_snapshot


async def find_unreferenced_session_blob(
    db: AsyncIOMotorDatabase, prefix: str, *, process_oid: ObjectId, process_id: str,
) -> ObjectId | None:
    """Newest session blob of this process stored after its latest snapshot
    and referenced by no snapshot: what a capture cut off after its session
    step left behind."""
    latest = await load_latest_snapshot(db, prefix=prefix, process_id=process_id)
    query: dict = {
        "metadata.processId": str(process_oid),
        "metadata.prefix": prefix,
        "metadata.name": "session",
    }
    if latest is not None:
        query["uploadDate"] = {"$gt": latest["capturedAt"]}
    referenced = set(await _snapshots(db, prefix).distinct(
        "sessionBlobId", {"processId": process_id},
    ))
    async for f in db["fs.files"].find(query).sort("uploadDate", -1):
        if f["_id"] not in referenced:
            return f["_id"]
    return None


async def _adoptable_conversation_blob(
    ctx: ProcessContext, record: dict | None, *, committed: bool,
) -> ObjectId | None:
    """The conversation blob the failed run's cut-off capture stored, named
    by its pending ``record``: it holds the newest history, so the resurrected
    snapshot references it rather than a copy of the older record. None when
    that capture committed (its snapshot owns the blob), named none, or the
    blob is gone or already referenced by a snapshot."""
    blob_id = None if committed else pending_conversation_blob_id(record)
    if blob_id is None:
        return None
    if await ctx._db["fs.files"].find_one({"_id": blob_id}, projection={"_id": 1}) is None:
        return None
    if await _snapshots(ctx._db, ctx._prefix).find_one(
        {"processId": ctx.process_id, "conversationBlobId": blob_id}, projection={"_id": 1},
    ) is not None:
        return None
    return blob_id


async def _probe(host, condition: str, what: str) -> bool:
    """Run ``<condition> && echo YES || true`` on the host: True on YES,
    False on a clean answer without it. The command cannot fail on its own
    (``|| true``), so a non-zero exit means it did not run (connection,
    shell, ``cd``): raise rather than take it for "absent", which would end
    in NothingToResurrect, a cleared flag and a Resume that wipes the work."""
    r = await host.run_command(f"{condition} && echo YES || true", cwd="/")
    if r.exit_code != 0:
        raise RuntimeError(
            f"{what} probe failed (exit {r.exit_code}): {r.stderr.strip()[:500]}"
        )
    return "YES" in r.stdout


async def _workdir_has_content(host) -> bool:
    w = shlex.quote(host.workdir)
    return await _probe(
        host, f'test -d {w} && [ -n "$(ls -A {w} 2>/dev/null)" ]', "workdir",
    )


def _home_claude(host) -> str:
    return host.workdir.rstrip("/") + "/home/.claude"


async def _credentials_present(host) -> bool:
    """The capture's own credentials guard: without them it refuses."""
    return await _probe(
        host,
        f"test -s {shlex.quote(_home_claude(host) + '/.credentials.json')}",
        "credentials",
    )


async def _stop_leftovers(host) -> None:
    """Kill what the failed run may have left: the tmux/ttyd/claude tree on
    the task's socket, and `tail -F <workdir>/optio.log` readers. The pkill
    pattern is anchored and escaped so it cannot match the shell running it.
    No tmux on the worker (conversation mode): no tmux session to stop."""
    tmux_path = await host_actions.find_tmux(host)
    socket = host_actions._tmux_socket_path(host)
    if tmux_path is not None and await host_actions.tmux_session_alive(
        host, tmux_path, socket, "optio",
    ):
        await host_actions.teardown_session_tree(
            host,
            tmux_path=tmux_path,
            tmux_socket=socket,
            tmux_session="optio",
            claude_path=S._claude_bin_path(host),
            ttyd_handle=None,
            aggressive=True,
        )
    log_path = f"{host.workdir.rstrip('/')}/optio.log"
    pattern = "^tail -F -n \\+1 " + re.escape(log_path) + "$"
    await host.run_command(f"pkill -f -- {shlex.quote(pattern)} || true", cwd="/")


async def resurrect_claudecode_session(ctx: ProcessContext, config) -> None:
    host = S._build_host(config, ctx.process_id)
    await host.connect()
    try:
        if not await _workdir_has_content(host):
            raise NothingToResurrect("workdir no longer on the host (or empty)")
        ctx.report_progress(None, "Resurrecting: stopping leftovers of the failed run…")
        await _stop_leftovers(host)

        # Read before the settle deletes it: the record names the conversation
        # blob to adopt.
        record = await load_pending_capture(ctx._db, ctx._prefix, ctx.process_id)
        committed = await settle_pending_capture(ctx)
        credentials = await _credentials_present(host)
        if committed and not credentials:
            # This run's capture was cut off after inserting its snapshot (it
            # removes home/.claude before recording): the work is saved.
            await ctx.clear_unsaved_work()
            await host.cleanup_taskdir(aggressive=False)
            return

        exclude = S._excludes_with_rescue_marker(config)
        # The conversation record (no live listener to export one): the
        # cut-off capture's blob when it left one, else a copy of the latest
        # snapshot's (S._carry_conversation_forward). The settle above keeps
        # that blob, and _store_workdir_snapshot never discards the one it
        # records.
        adopted = await _adoptable_conversation_blob(ctx, record, committed=committed)
        if credentials:
            conversation_blob_id = (
                adopted if adopted is not None else await S._carry_conversation_forward(ctx)
            )
            saved = await S._capture_snapshot(
                ctx, host,
                end_state="resurrected",
                workdir_exclude=exclude,
                session_blob_encrypt=config.session_blob_encrypt,
                conversation_blob_id=conversation_blob_id,
            )
            if not saved:
                # The capture's own guard refused after our check: nothing
                # was stored (the copy made here is dropped), so the host is
                # left as it is.
                if conversation_blob_id is not None and conversation_blob_id != adopted:
                    await ctx.delete_blob(conversation_blob_id)
                raise NothingToResurrect("credentials disappeared during the save")
        else:
            # The capture's session step ran (its rm -rf home/.claude may have
            # been cut off part-way); use the session blob it stored.
            session_blob_id = await find_unreferenced_session_blob(
                ctx._db, ctx._prefix,
                process_oid=ctx._process_oid, process_id=ctx.process_id,
            )
            if session_blob_id is None:
                # Nothing to save, and the host is left as it is. A snapshot
                # whose capture was cut off before flagging it is still
                # resumable: flag it so Resume is offered.
                if await load_latest_snapshot(
                    ctx._db, prefix=ctx._prefix, process_id=ctx.process_id,
                ) is not None:
                    await ctx.mark_has_saved_state()
                raise NothingToResurrect("session state not found")
            # A leftover home/.claude must not enter the plaintext workdir blob.
            await host.run_command(f"rm -rf {shlex.quote(_home_claude(host))}", cwd="/")
            conversation_blob_id = (
                adopted if adopted is not None else await S._carry_conversation_forward(ctx)
            )
            await S._store_workdir_snapshot(
                ctx, host,
                end_state="resurrected",
                workdir_exclude=exclude,
                session_blob_id=session_blob_id,
                conversation_blob_id=conversation_blob_id,
            )
        await host.cleanup_taskdir(aggressive=False)
    finally:
        await host.disconnect()
