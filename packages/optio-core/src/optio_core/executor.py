"""Task executor — runs task functions with state management."""

import asyncio
import logging
import os as _os
import sys
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Callable, Awaitable
from bson import ObjectId
from motor.motor_asyncio import AsyncIOMotorDatabase
from pymongo.errors import PyMongoError

logger = logging.getLogger(__name__)
_trace_logger = logging.getLogger("optio_core.cancel_trace")
_CANCEL_TRACE = _os.environ.get("OPTIO_CANCEL_TRACE", "0").lower() in ("1", "true", "yes")


def _trace(fmt: str, *args: object) -> None:
    """Cancel-trace log helper, gated on OPTIO_CANCEL_TRACE env var."""
    if _CANCEL_TRACE:
        _trace_logger.warning(fmt, *args)

from optio_core.models import (
    TaskInstance, ProcessStatus, Progress, ProcessMetadataFilter, matches_filter,
    ChildOutcome,
)
from optio_core.state_machine import LAUNCHABLE_STATES
from optio_core.store import (
    get_process_by_process_id,
    update_status, clear_result_fields,
    create_child_process, append_log,
    clear_widget_upstream, compute_expire_at,
    finalize_if_active, _collection,
)
from optio_core.context import ProcessContext
from optio_core.exceptions import ChildProcessFailed


@dataclass
class _CancelEntry:
    """Tracks cooperative-cancel state for one running process.

    `flag` is the cooperative cancellation Event consumed by ProcessContext.
    `deadline` is a monotonic timestamp; None until cancel() is called. Once
    set, it is not refreshed by subsequent calls (first wins).
    """
    flag: asyncio.Event
    deadline: float | None = None


# Rule 3 retry backoff: first retry on the next supervisor tick, then doubling
# from this delay up to the cap. No attempt limit.
_FINAL_RETRY_FIRST_DELAY = 1.0
_FINAL_RETRY_MAX_DELAY = 30.0


@dataclass
class _UnrecordedFinal:
    """A final state that could not be written to Mongo, kept for retry.

    `status` is the terminal status still to write (None once written).
    `cascade` asks for the force-cancel cascade to direct active children to
    be re-run. Spec: docs/2026-10-03-lost-final-state-writes-design.md
    """
    status: ProcessStatus | None
    cascade: bool = False
    attempts: int = 0
    next_attempt: float = 0.0


class Executor:
    """Executes task functions with lifecycle management."""

    def __init__(
        self,
        db: AsyncIOMotorDatabase,
        prefix: str,
        services: dict[str, Any],
        optio: "Optio | None" = None,
        notify_parent_abnormal: Callable[..., Awaitable[Any]] | None = None,
        notify_parent_failure: Callable[..., Awaitable[Any]] | None = None,
    ):
        self._db = db
        self._prefix = prefix
        self._services = services
        self._optio = optio
        self._notify_parent_abnormal = notify_parent_abnormal
        self._notify_parent_failure = notify_parent_failure
        self._cancellation_flags: dict[ObjectId, _CancelEntry] = {}
        self._running_tasks: dict[ObjectId, asyncio.Task] = {}
        # Rules 3 and 4 of docs/2026-10-03-lost-final-state-writes-design.md:
        # final states that could not be written (retried by the supervisor
        # loop), and OIDs whose force_cancel is still in flight.
        self._unrecorded_finals: dict[ObjectId, _UnrecordedFinal] = {}
        self._force_cancelling: set[ObjectId] = set()
        self._task_registry: dict[str, TaskInstance] = {}
        # Task→launcher return channel (in-memory only, same-process).
        # Keyed by processId string. Registry holds published objects for
        # the lifetime of the run; futures exist only while a
        # launch_and_await_result caller is (about to be) waiting.
        self._result_registry: dict[str, Any] = {}
        self._result_futures: dict[str, asyncio.Future] = {}

    def register_tasks(
        self,
        tasks: list[TaskInstance],
        metadata_filter: ProcessMetadataFilter | None = None,
    ) -> None:
        """Register task definitions by processId.

        With no `metadata_filter`, the registry is fully replaced (current
        behaviour). With a filter, only entries whose existing `metadata`
        matches the filter are eligible for removal; everything outside the
        scope is preserved. Tasks in the new list are then upserted.
        """
        if not metadata_filter:
            self._task_registry = {t.process_id: t for t in tasks}
            return
        new_ids = {t.process_id for t in tasks}
        for pid in list(self._task_registry):
            existing = self._task_registry[pid]
            if matches_filter(existing.metadata, metadata_filter) and pid not in new_ids:
                del self._task_registry[pid]
        for t in tasks:
            self._task_registry[t.process_id] = t

    def publish_result(self, process_id: str, obj: Any) -> None:
        """Register a task-published result; resolve any waiting launcher."""
        if process_id in self._result_registry:
            raise RuntimeError(
                f"publish_result: '{process_id}' already published a result "
                "for this run"
            )
        self._result_registry[process_id] = obj
        fut = self._result_futures.get(process_id)
        if fut is not None and not fut.done():
            fut.set_result(obj)

    def get_published_result(self, process_id: str) -> Any | None:
        """Return the live published object for a running process, or None."""
        return self._result_registry.get(process_id)

    def ensure_result_future(self, process_id: str) -> "asyncio.Future[Any]":
        """Create (or return) the launcher-side future for process_id.

        Called by launch_and_await_result BEFORE scheduling the launch so a
        task that publishes immediately cannot race the waiter.
        """
        fut = self._result_futures.get(process_id)
        if fut is None or fut.done():
            fut = asyncio.get_event_loop().create_future()
            self._result_futures[process_id] = fut
        return fut

    async def _cleanup_ephemeral(self, process_id: str) -> None:
        """Delete the process if it's marked ephemeral. Accepts processId OR OID hex."""
        proc = await get_process_by_process_id(self._db, self._prefix, process_id)
        if proc is not None and proc.get("ephemeral"):
            from optio_core.store import delete_process
            # delete_process accepts dual-form; pass OID to avoid orphan
            # ambiguity. Registry pop must use the resolved doc's processId.
            await delete_process(self._db, self._prefix, str(proc["_id"]))
            self._task_registry.pop(proc["processId"], None)

    async def launch_process(
        self, process_id: str, resume: bool = False, *, session_id: str | None,
    ) -> str | None:
        """Launch a top-level process by processId OR OID hex (dual-form).

        If resume is True, ctx.resume will be True inside the execute function,
        signalling that the task should restore previous state rather than start fresh.
        """
        proc = await get_process_by_process_id(self._db, self._prefix, process_id)
        if proc is None:
            return None

        current_state = proc["status"]["state"]
        if current_state not in LAUNCHABLE_STATES:
            return None  # silently ignore (idempotent)

        await clear_result_fields(self._db, self._prefix, proc["_id"])
        await update_status(
            self._db, self._prefix, proc["_id"],
            ProcessStatus(state="scheduled"),
        )
        await append_log(self._db, self._prefix, proc["_id"], "event", "State changed to scheduled")

        # Use resolved doc's processId for the registry — caller may have
        # passed OID hex, but _task_registry is processId-keyed.
        task = self._task_registry.get(proc["processId"])
        state, _ = await self._execute_process(
            proc, task.execute if task else None, resume=resume,
            session_id=session_id,
        )
        return state

    async def _execute_process(
        self, proc: dict, execute_fn: Callable | None,
        parent_ctx: ProcessContext | None = None,
        resume: bool = False,
        *, session_id: str | None = None,
    ) -> tuple[str, BaseException | None]:
        """Execute a process."""
        oid = proc["_id"]
        root_oid = proc.get("rootId", oid)

        # B2: TTL — read ttl_seconds from the process record (DB is source of
        # truth; survives task-registry churn). Each terminal-state writer
        # below passes compute_expire_at(ttl_seconds) to update_status.
        ttl_seconds = proc.get("ttlSeconds")

        cancel_flag = asyncio.Event()
        self._cancellation_flags[oid] = _CancelEntry(flag=cancel_flag, deadline=None)
        current = asyncio.current_task()
        if current is None:
            raise RuntimeError("_execute_process must be called from within an asyncio Task")
        self._running_tasks[oid] = current
        # A new run of this row supersedes a final state still parked from an
        # earlier run (Rule 3): the row was launchable, so that write is moot.
        self._unrecorded_finals.pop(oid, None)

        # Rule 3 (docs/2026-10-03-lost-final-state-writes-design.md): `final`
        # is the terminal status this run means to write, noted as soon as
        # the outcome is known; `final_recorded` flips once that write has
        # landed. If the finally below is reached without it, the status is
        # parked for the supervisor loop to retry.
        final: ProcessStatus | None = None
        final_recorded = False

        try:
            now = datetime.now(timezone.utc)
            await update_status(
                self._db, self._prefix, oid,
                ProcessStatus(state="running", running_since=now),
            )
            await append_log(self._db, self._prefix, oid, "event", "State changed to running")

            effective_session_id = (
                parent_ctx.session_id if parent_ctx is not None else session_id
            )
            await _collection(self._db, self._prefix).update_one(
                {"_id": oid},
                {"$set": {"originatingSessionId": effective_session_id}},
            )

            ctx = ProcessContext(
                process_oid=oid,
                process_id=proc["processId"],
                root_oid=root_oid,
                depth=proc.get("depth", 0),
                params=proc.get("params", {}),
                metadata=proc.get("metadata", {}),
                services=self._services,
                db=self._db,
                prefix=self._prefix,
                cancellation_flag=cancel_flag,
                child_counter={"next": 0},
                resume=resume,
                session_id=effective_session_id,
            )
            ctx._executor = self

            if parent_ctx is not None and parent_ctx._on_child_progress is not None:
                child_process_id = proc["processId"]
                child_name = proc["name"]
                def _listener(percent, message, _pid=child_process_id, _name=child_name):
                    parent_ctx._notify_child_progress(_pid, _name, "running", percent, message)
                ctx._parent_listener = _listener

            if execute_fn is None:
                final = ProcessStatus(
                    state="failed", error="No execute function found",
                    failed_at=datetime.now(timezone.utc),
                )
                await update_status(
                    self._db, self._prefix, oid, final,
                    expire_at=compute_expire_at(ttl_seconds),
                )
                final_recorded = True
                return ("failed", None)

            start_time = time.monotonic()
            end_state = "done"

            try:
                await execute_fn(ctx)
                if cancel_flag.is_set():
                    end_state = "cancelled"
            except asyncio.CancelledError:
                # CancelledError is a BaseException, not Exception — the `except
                # Exception` arm below does NOT catch it. Without this arm,
                # a task body that raises CancelledError cooperatively (e.g.
                # optio-recipe-runner explicitly raises it after the cancel
                # flag fires) propagates out of `_execute_process` without
                # any terminal state write, leaving the row stuck at
                # `cancelling` — and the finally below pops the cancellation
                # entry from the supervisor map, so force_cancel can't rescue
                # it either.
                #
                # Distinguish two CancelledError sources:
                #   (a) Cooperative: a cancel was requested via
                #       lifecycle.cancel (so the entry has a deadline set)
                #       and the task body raised CancelledError BEFORE that
                #       deadline expired. Treat as a normal cancel → write
                #       `cancelled`.
                #   (b) Forced: either (i) the entry has no deadline (no
                #       cooperative cancel was requested — this is a
                #       force-cancel cascade against an opted-out subtree)
                #       or (ii) deadline expired and force_cancel injected
                #       task.cancel(). Let CancelledError propagate so
                #       _write_force_cancelled_state writes the canonical
                #       `failed` terminal state with the grace-exceeded
                #       error.
                #
                # Re-raise either way to honor the asyncio cancellation
                # contract — parents must see the unwind.
                entry = self._cancellation_flags.get(oid)
                cooperative = (
                    entry is not None and entry.deadline is not None
                    and time.monotonic() < entry.deadline
                )
                if cooperative:
                    _trace(
                        "CANCEL-TRACE %s: executor write cancelled (raised CancelledError)",
                        proc["processId"],
                    )
                    final = ProcessStatus(
                        state="cancelled",
                        stopped_at=datetime.now(timezone.utc),
                    )
                    await ctx.flush_final_progress()
                    await update_status(
                        self._db, self._prefix, oid, final,
                        expire_at=compute_expire_at(ttl_seconds),
                    )
                    final_recorded = True
                    await append_log(
                        self._db, self._prefix, oid, "event",
                        "State changed to cancelled (raised CancelledError)",
                    )
                    await clear_widget_upstream(self._db, self._prefix, oid)
                    await self._cleanup_ephemeral(str(oid))
                raise
            except Exception as e:
                final = ProcessStatus(
                    state="failed", error=str(e),
                    failed_at=datetime.now(timezone.utc),
                )
                await ctx.flush_final_progress()
                await update_status(
                    self._db, self._prefix, oid, final,
                    expire_at=compute_expire_at(ttl_seconds),
                )
                final_recorded = True
                await append_log(self._db, self._prefix, oid, "error", str(e))
                await clear_widget_upstream(self._db, self._prefix, oid)
                await self._cleanup_ephemeral(str(oid))
                return ("failed", e)

            # Note the outcome before flushing, so a flush that fails still
            # leaves the right state to retry; rebuilt after the flush so the
            # recorded timing matches what it was before.
            final = self._terminal_status(end_state, start_time)
            await ctx.flush_final_progress()
            final = self._terminal_status(end_state, start_time)

            if end_state == "done":
                _trace(
                    "CANCEL-TRACE %s: executor write done", proc["processId"],
                )
                await update_status(
                    self._db, self._prefix, oid, final,
                    expire_at=compute_expire_at(ttl_seconds),
                )
                final_recorded = True
                await append_log(self._db, self._prefix, oid, "event", "State changed to done")
            elif end_state == "cancelled":
                _trace(
                    "CANCEL-TRACE %s: executor write cancelled (after execute_fn returned)",
                    proc["processId"],
                )
                await update_status(
                    self._db, self._prefix, oid, final,
                    expire_at=compute_expire_at(ttl_seconds),
                )
                final_recorded = True
                await append_log(self._db, self._prefix, oid, "event", "State changed to cancelled")

            await clear_widget_upstream(self._db, self._prefix, oid)
            await self._cleanup_ephemeral(str(oid))
            return (end_state, None)
        finally:
            self._cancellation_flags.pop(oid, None)
            self._running_tasks.pop(oid, None)
            # Result channel teardown: drop the registry entry; fail any
            # still-waiting launcher with ResultNotPublished.
            _pid = proc["processId"]
            self._result_registry.pop(_pid, None)
            _fut = self._result_futures.pop(_pid, None)
            if _fut is not None and not _fut.done():
                from optio_core.exceptions import ResultNotPublished
                _fut.set_exception(ResultNotPublished(_pid))
            # Rule 3: the final write never landed (Mongo unreachable, or an
            # error before the outcome was known). A CancelledError unwind is
            # left alone: force_cancel, shutdown (Rule 2) or the next start
            # (Rule 1) owns that row.
            _exc = sys.exc_info()[1]
            if not final_recorded and not isinstance(_exc, asyncio.CancelledError):
                if final is None:
                    final = ProcessStatus(
                        state="failed",
                        error=f"Final state could not be recorded: {_exc!r}",
                        failed_at=datetime.now(timezone.utc),
                    )
                self._park_final(oid, final, cause=_exc)

    @staticmethod
    def _terminal_status(end_state: str, start_time: float) -> ProcessStatus:
        """Terminal status for a task body that returned ('done' or 'cancelled')."""
        now = datetime.now(timezone.utc)
        if end_state == "done":
            return ProcessStatus(
                state="done", done_at=now,
                duration=round(time.monotonic() - start_time, 2),
            )
        return ProcessStatus(state="cancelled", stopped_at=now)

    async def execute_child(
        self,
        parent_ctx: ProcessContext,
        execute: Callable[..., Awaitable[None]],
        process_id: str,
        name: str,
        params: dict,
        survive_failure: bool = False,
        survive_cancel: bool = False,
        description: str | None = None,
    ) -> ChildOutcome:
        """Execute a child process (called from ProcessContext.run_child)."""
        if self._optio is not None:
            self._optio._check_launch_blocks(parent_ctx.metadata)
        order = parent_ctx._next_child_order()

        child_doc = await create_child_process(
            self._db, self._prefix,
            parent_oid=parent_ctx._process_oid,
            root_oid=parent_ctx._root_oid,
            process_id=process_id,
            name=name,
            params=params,
            depth=parent_ctx._depth + 1,
            order=order,
            initial_state="scheduled",
            metadata=parent_ctx.metadata,
            description=description,
        )
        await append_log(self._db, self._prefix, parent_ctx._process_oid, "event", f"Spawned child: {name}")

        end_state, exc = await self._execute_process(child_doc, execute, parent_ctx=parent_ctx)

        if parent_ctx._on_child_progress is not None:
            parent_ctx._notify_child_state_change(process_id, end_state)

        abnormal_failed = end_state == "failed" and not survive_failure
        abnormal_cancelled = end_state == "cancelled" and not survive_cancel

        # Failure breach: cancel parent's OTHER active concurrent children
        # only. Do NOT set parent's flag, do NOT change parent's row state —
        # the ChildProcessFailed raise below communicates the failure to
        # parent's user code, and the parent's terminal state is then
        # determined by whether the user catches+returns or re-raises.
        #
        # Await it (rather than fire-and-forget): the callback snapshots the
        # active children via a live DB query, so it must run *now*, before the
        # ChildProcessFailed raise resumes the parent's user code. A scheduled
        # task can otherwise run late — after the parent has caught the failure
        # and spawned fresh recovery work — and wrongly cancel that new child
        # (it was never a concurrent sibling of the failed one). The callback
        # only *requests* cooperative cancels (it does not wait for siblings to
        # unwind), so awaiting here is cheap.
        if abnormal_failed:
            if self._notify_parent_failure is not None:
                _trace(
                    "CANCEL-TRACE %s: failed child %s → notify_parent_failure(parent=%s)",
                    process_id, name, parent_ctx.process_id,
                )
                await self._notify_parent_failure(parent_ctx.process_id)

        # Cancellation breach: cascade upward. Set parent's flag
        # synchronously so subsequent operations in the parent's user
        # code observe should_continue() == False, and schedule
        # Optio.cancel(parent) so the parent's row transitions through
        # cancel_requested/cancelling.
        if abnormal_cancelled:
            parent_ctx._cancellation_flag.set()
            if self._notify_parent_abnormal is not None:
                _trace(
                    "CANCEL-TRACE %s: cancelled child %s → scheduling notify_parent_abnormal(parent=%s)",
                    process_id, name, parent_ctx.process_id,
                )
                asyncio.create_task(
                    self._notify_parent_abnormal(parent_ctx.process_id)
                )

        if abnormal_failed:
            if exc is None:
                exc = RuntimeError(f"Child process '{name}' failed")
            raise ChildProcessFailed(name, process_id, exc) from exc

        return ChildOutcome(
            state=end_state,
            original_exception=exc if end_state == "failed" else None,
        )

    def request_cancel_with_deadline(
        self, process_oid: ObjectId, deadline: float
    ) -> bool:
        """Request cooperative cancel and record a force-cancel deadline.

        Sets the cooperative cancel flag. Records `deadline` (a monotonic
        timestamp) in the entry only if no deadline is set yet — first wins.
        Returns True if an entry was found, False otherwise.
        """
        entry = self._cancellation_flags.get(process_oid)
        if entry is None:
            _trace("request_cancel_with_deadline oid=%s: NOT FOUND in supervisor map",
                   process_oid)
            return False
        entry.flag.set()
        _existing = entry.deadline
        if entry.deadline is None:
            entry.deadline = deadline
        _trace(
            "request_cancel_with_deadline oid=%s: flag set; deadline=%.3f (now=%.3f budget=%.3fs) existing=%s",
            process_oid, entry.deadline, time.monotonic(),
            entry.deadline - time.monotonic(), _existing,
        )
        return True

    def owns(self, oid: ObjectId) -> bool:
        """True while this executor still has a task, a cancel entry or a
        force-cancel in flight for `oid`.

        A task registers its entry before it writes `running`, and writes its
        final state before it drops the entry. So a row in `running`,
        `cancel_requested` or `cancelling` for which this returns False has
        lost its final write (Rule 4 of
        docs/2026-10-03-lost-final-state-writes-design.md).
        """
        return (
            oid in self._cancellation_flags
            or oid in self._running_tasks
            or oid in self._force_cancelling
        )

    def _park_final(
        self, oid: ObjectId, status: ProcessStatus | None, *,
        cascade: bool = False, cause: BaseException | None = None,
    ) -> None:
        """Keep a final state (and/or a force-cancel cascade) that could not
        be written, for retry_unrecorded_finals. A status already parked for
        the same OID wins; cascade requests accumulate."""
        entry = self._unrecorded_finals.get(oid)
        if entry is None:
            self._unrecorded_finals[oid] = _UnrecordedFinal(status=status, cascade=cascade)
            logger.warning(
                "Could not record the final state (%s) of process %s: %r. "
                "Retrying in the background.",
                status.state if status is not None else "child cascade", oid, cause,
            )
            return
        if entry.status is None:
            entry.status = status
        entry.cascade = entry.cascade or cascade

    async def _record_final(self, oid: ObjectId, entry: _UnrecordedFinal) -> None:
        """Write a parked final state, then run a parked cascade. Raises
        PyMongoError while Mongo is still unreachable; the entry then keeps
        whatever is left to do."""
        if entry.status is not None:
            state = entry.status.state
            written = await finalize_if_active(self._db, self._prefix, oid, entry.status)
            entry.status = None
            if written:
                await append_log(
                    self._db, self._prefix, oid, "event",
                    f"State recorded late: {state} (database was unavailable)",
                )
                await self._cleanup_ephemeral(str(oid))
        if entry.cascade:
            await self._force_cancel_children(oid)
            entry.cascade = False

    async def retry_unrecorded_finals(self) -> None:
        """Retry the parked final states that are due (Rule 3). Called by the
        supervisor loop on every tick. A row that is no longer active is left
        alone and its entry dropped; while Mongo stays unreachable the next
        attempt backs off, doubling up to _FINAL_RETRY_MAX_DELAY."""
        now = time.monotonic()
        for oid, entry in list(self._unrecorded_finals.items()):
            if entry.next_attempt > now or self.owns(oid):
                continue
            try:
                await self._record_final(oid, entry)
            except PyMongoError as e:
                entry.attempts += 1
                entry.next_attempt = time.monotonic() + min(
                    _FINAL_RETRY_MAX_DELAY,
                    _FINAL_RETRY_FIRST_DELAY * 2 ** (entry.attempts - 1),
                )
                logger.debug(
                    "Final state of process %s still not recorded (retry %d): %r",
                    oid, entry.attempts, e,
                )
                continue
            self._unrecorded_finals.pop(oid, None)
            logger.warning(
                "Final state of process %s recorded late, after %d failed retries",
                oid, entry.attempts,
            )

    async def record_parked_final_now(self, oid: ObjectId) -> bool:
        """If a final state is parked for `oid`, write it now, ignoring the
        backoff. Returns True if one was parked. Raises PyMongoError while
        Mongo is still unreachable; the entry then stays parked."""
        entry = self._unrecorded_finals.get(oid)
        if entry is None:
            return False
        await self._record_final(oid, entry)
        self._unrecorded_finals.pop(oid, None)
        return True

    async def _force_cancel_children(self, oid: ObjectId) -> None:
        """Cascade a force-cancel to direct active children. Unconditional --
        force is force."""
        from optio_core.store import list_direct_children
        from optio_core.state_machine import ACTIVE_STATES

        children = await list_direct_children(
            self._db, self._prefix, oid, states=ACTIVE_STATES,
        )
        if children:
            _trace(
                "force_cancel oid=%s: cascading to children=%s",
                oid, [str(c["_id"]) for c in children],
            )
            await asyncio.gather(
                *(self.force_cancel(c["_id"]) for c in children),
                return_exceptions=True,
            )

    async def force_cancel(self, oid: ObjectId) -> None:
        """Hard-cancel a process whose cooperative deadline has expired.

        Calls Task.cancel() on the tracked asyncio Task, awaits a bounded
        unwind, then writes the conditional 'failed' terminal state to
        Mongo via _write_force_cancelled_state. After the local terminal
        write, cascade unconditionally to direct active children —
        captures both auto-propagate descendants (already in supervisor
        map; idempotent) and opt-out descendants (only this cascade
        reaches them).

        If Mongo is unreachable, the write and the cascade are parked for
        retry_unrecorded_finals instead of raising (Rule 3 of
        docs/2026-10-03-lost-final-state-writes-design.md).
        """
        from optio_core._force_cancel import (
            FORCE_CANCEL_ERROR, _write_force_cancelled_state,
        )

        task = self._running_tasks.get(oid)
        _trace(
            "force_cancel oid=%s task=%s done=%s",
            oid, task, task.done() if task else None,
        )
        # The row stays owned by this engine until our own write is done,
        # even after the task's finally has dropped its entries (Rule 4).
        self._force_cancelling.add(oid)
        try:
            if task is not None and not task.done():
                _trace("force_cancel oid=%s: calling task.cancel() + 2s shield wait", oid)
                task.cancel()
                try:
                    await asyncio.wait_for(
                        asyncio.shield(task),
                        timeout=self._optio._config.force_cancel_shield_seconds,
                    )
                    _trace("force_cancel oid=%s: task unwound within shield window", oid)
                except asyncio.TimeoutError:
                    _trace("force_cancel oid=%s: 2s shield TIMEOUT — task still running", oid)
                    pass
                except asyncio.CancelledError:
                    _trace("force_cancel oid=%s: task acknowledged Cancel", oid)
                    pass
                except Exception as _e:
                    _trace("force_cancel oid=%s: task raised %s: %s",
                           oid, type(_e).__name__, _e)
                    pass
            try:
                await _write_force_cancelled_state(self._db, self._prefix, oid)
            except PyMongoError as e:
                self._park_final(
                    oid,
                    ProcessStatus(
                        state="failed", error=FORCE_CANCEL_ERROR,
                        failed_at=datetime.now(timezone.utc),
                    ),
                    cascade=True, cause=e,
                )
                return
            try:
                await self._force_cancel_children(oid)
            except PyMongoError as e:
                self._park_final(oid, None, cascade=True, cause=e)
        finally:
            self._force_cancelling.discard(oid)
