"""Task executor — runs task functions with state management."""

import asyncio
import logging
import os as _os
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Callable, Awaitable
from bson import ObjectId
from motor.motor_asyncio import AsyncIOMotorDatabase

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
        self._task_registry: dict[str, TaskInstance] = {}

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

    async def _cleanup_ephemeral(self, process_id: str) -> None:
        """Delete the process if it's marked ephemeral. Accepts processId OR OID hex."""
        proc = await get_process_by_process_id(self._db, self._prefix, process_id)
        if proc is not None and proc.get("ephemeral"):
            from optio_core.store import delete_process
            # delete_process accepts dual-form; pass OID to avoid orphan
            # ambiguity. Registry pop must use the resolved doc's processId.
            await delete_process(self._db, self._prefix, str(proc["_id"]))
            self._task_registry.pop(proc["processId"], None)

    async def launch_process(self, process_id: str, resume: bool = False) -> str | None:
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
        )
        return state

    async def _execute_process(
        self, proc: dict, execute_fn: Callable | None,
        parent_ctx: ProcessContext | None = None,
        resume: bool = False,
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

        try:
            now = datetime.now(timezone.utc)
            await update_status(
                self._db, self._prefix, oid,
                ProcessStatus(state="running", running_since=now),
            )
            await append_log(self._db, self._prefix, oid, "event", "State changed to running")

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
            )
            ctx._executor = self

            if parent_ctx is not None and parent_ctx._on_child_progress is not None:
                child_process_id = proc["processId"]
                child_name = proc["name"]
                def _listener(percent, message, _pid=child_process_id, _name=child_name):
                    parent_ctx._notify_child_progress(_pid, _name, "running", percent, message)
                ctx._parent_listener = _listener

            if execute_fn is None:
                await update_status(
                    self._db, self._prefix, oid,
                    ProcessStatus(
                        state="failed", error="No execute function found",
                        failed_at=datetime.now(timezone.utc),
                    ),
                    expire_at=compute_expire_at(ttl_seconds),
                )
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
                    await ctx.flush_final_progress()
                    await update_status(
                        self._db, self._prefix, oid,
                        ProcessStatus(
                            state="cancelled",
                            stopped_at=datetime.now(timezone.utc),
                        ),
                        expire_at=compute_expire_at(ttl_seconds),
                    )
                    await append_log(
                        self._db, self._prefix, oid, "event",
                        "State changed to cancelled (raised CancelledError)",
                    )
                    await clear_widget_upstream(self._db, self._prefix, oid)
                    await self._cleanup_ephemeral(str(oid))
                raise
            except Exception as e:
                await ctx.flush_final_progress()
                await update_status(
                    self._db, self._prefix, oid,
                    ProcessStatus(
                        state="failed", error=str(e),
                        failed_at=datetime.now(timezone.utc),
                    ),
                    expire_at=compute_expire_at(ttl_seconds),
                )
                await append_log(self._db, self._prefix, oid, "error", str(e))
                await clear_widget_upstream(self._db, self._prefix, oid)
                await self._cleanup_ephemeral(str(oid))
                return ("failed", e)

            await ctx.flush_final_progress()
            elapsed = time.monotonic() - start_time

            if end_state == "done":
                _trace(
                    "CANCEL-TRACE %s: executor write done", proc["processId"],
                )
                await update_status(
                    self._db, self._prefix, oid,
                    ProcessStatus(
                        state="done",
                        done_at=datetime.now(timezone.utc),
                        duration=round(elapsed, 2),
                    ),
                    expire_at=compute_expire_at(ttl_seconds),
                )
                await append_log(self._db, self._prefix, oid, "event", "State changed to done")
            elif end_state == "cancelled":
                _trace(
                    "CANCEL-TRACE %s: executor write cancelled (after execute_fn returned)",
                    proc["processId"],
                )
                await update_status(
                    self._db, self._prefix, oid,
                    ProcessStatus(
                        state="cancelled",
                        stopped_at=datetime.now(timezone.utc),
                    ),
                    expire_at=compute_expire_at(ttl_seconds),
                )
                await append_log(self._db, self._prefix, oid, "event", "State changed to cancelled")

            await clear_widget_upstream(self._db, self._prefix, oid)
            await self._cleanup_ephemeral(str(oid))
            return (end_state, None)
        finally:
            self._cancellation_flags.pop(oid, None)
            self._running_tasks.pop(oid, None)

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
        if abnormal_failed:
            if self._notify_parent_failure is not None:
                _trace(
                    "CANCEL-TRACE %s: failed child %s → scheduling notify_parent_failure(parent=%s)",
                    process_id, name, parent_ctx.process_id,
                )
                asyncio.create_task(
                    self._notify_parent_failure(parent_ctx.process_id)
                )

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

    async def force_cancel(self, oid: ObjectId) -> None:
        """Hard-cancel a process whose cooperative deadline has expired.

        Calls Task.cancel() on the tracked asyncio Task, awaits a bounded
        unwind, then writes the conditional 'failed' terminal state to
        Mongo via _write_force_cancelled_state. After the local terminal
        write, cascade unconditionally to direct active children —
        captures both auto-propagate descendants (already in supervisor
        map; idempotent) and opt-out descendants (only this cascade
        reaches them).
        """
        from optio_core._force_cancel import _write_force_cancelled_state
        from optio_core.store import list_direct_children
        from optio_core.state_machine import ACTIVE_STATES

        task = self._running_tasks.get(oid)
        _trace(
            "force_cancel oid=%s task=%s done=%s",
            oid, task, task.done() if task else None,
        )
        if task is not None and not task.done():
            _trace("force_cancel oid=%s: calling task.cancel() + 2s shield wait", oid)
            task.cancel()
            try:
                await asyncio.wait_for(asyncio.shield(task), timeout=2.0)
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
        await _write_force_cancelled_state(self._db, self._prefix, oid)

        # Cascade to direct active children. Unconditional — force is force.
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
