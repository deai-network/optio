"""Task executor — runs task functions with state management."""

import asyncio
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Callable, Awaitable
from bson import ObjectId
from motor.motor_asyncio import AsyncIOMotorDatabase

from optio_core.models import (
    TaskInstance, ProcessStatus, Progress, ProcessMetadataFilter, matches_filter,
)
from optio_core.state_machine import LAUNCHABLE_STATES
from optio_core.store import (
    get_process_by_process_id,
    update_status, clear_result_fields,
    create_child_process, append_log,
    clear_widget_upstream, compute_expire_at,
)
from optio_core.context import ProcessContext


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
    ):
        self._db = db
        self._prefix = prefix
        self._services = services
        self._optio = optio
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
        """Delete the process if it's marked ephemeral."""
        proc = await get_process_by_process_id(self._db, self._prefix, process_id)
        if proc is not None and proc.get("ephemeral"):
            from optio_core.store import delete_process
            await delete_process(self._db, self._prefix, process_id)
            self._task_registry.pop(process_id, None)

    async def launch_process(self, process_id: str, resume: bool = False) -> str | None:
        """Launch a top-level process by processId. Returns end state or None.

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

        task = self._task_registry.get(process_id)
        return await self._execute_process(
            proc, task.execute if task else None, resume=resume,
        )

    async def _execute_process(
        self, proc: dict, execute_fn: Callable | None,
        parent_ctx: ProcessContext | None = None,
        resume: bool = False,
    ) -> str:
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
                return "failed"

            start_time = time.monotonic()
            end_state = "done"

            try:
                await execute_fn(ctx)
                if cancel_flag.is_set():
                    end_state = "cancelled"
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
                await self._cleanup_ephemeral(proc["processId"])
                return "failed"

            await ctx.flush_final_progress()
            elapsed = time.monotonic() - start_time

            if end_state == "done":
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
            await self._cleanup_ephemeral(proc["processId"])
            return end_state
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
    ) -> str:
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

        end_state = await self._execute_process(child_doc, execute, parent_ctx=parent_ctx)

        if parent_ctx._on_child_progress is not None:
            parent_ctx._notify_child_state_change(process_id, end_state)

        if end_state == "failed" and not survive_failure:
            raise RuntimeError(f"Child process '{name}' failed")
        if end_state == "cancelled" and not survive_cancel:
            parent_ctx._cancellation_flag.set()

        return end_state

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
            return False
        entry.flag.set()
        if entry.deadline is None:
            entry.deadline = deadline
        return True

    async def force_cancel(self, oid: ObjectId) -> None:
        """Hard-cancel a process whose cooperative deadline has expired.

        Calls Task.cancel() on the tracked asyncio Task, awaits a bounded
        unwind, then writes the conditional 'failed' terminal state to
        Mongo via _write_force_cancelled_state. Used only by the
        Optio-level supervisor and by shutdown.
        """
        from optio_core._force_cancel import _write_force_cancelled_state

        task = self._running_tasks.get(oid)
        if task is not None and not task.done():
            task.cancel()
            try:
                await asyncio.wait_for(asyncio.shield(task), timeout=2.0)
            except (asyncio.TimeoutError, asyncio.CancelledError, Exception):
                # TimeoutError: thread-blocked or stubborn — proceed regardless.
                # CancelledError: task acknowledged cancellation — proceed.
                # Other exceptions are not our concern; the conditional Mongo
                # update below is the source of truth.
                pass
        await _write_force_cancelled_state(self._db, self._prefix, oid)
