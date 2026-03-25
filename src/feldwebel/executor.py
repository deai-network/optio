"""Task executor — runs task functions with state management."""

import asyncio
import time
from datetime import datetime, timezone
from typing import Any, Callable, Awaitable
from bson import ObjectId
from motor.motor_asyncio import AsyncIOMotorDatabase

from feldwebel.models import TaskInstance, ProcessStatus, Progress
from feldwebel.state_machine import LAUNCHABLE_STATES
from feldwebel.store import (
    get_process_by_process_id,
    update_status, clear_result_fields,
    create_child_process,
)
from feldwebel.context import ProcessContext


class Executor:
    """Executes task functions with lifecycle management."""

    def __init__(self, db: AsyncIOMotorDatabase, prefix: str, services: dict[str, Any]):
        self._db = db
        self._prefix = prefix
        self._services = services
        self._cancellation_flags: dict[ObjectId, asyncio.Event] = {}
        self._task_registry: dict[str, Callable] = {}

    def register_tasks(self, tasks: list[TaskInstance]) -> None:
        """Register task execute functions by processId."""
        self._task_registry = {t.process_id: t.execute for t in tasks}

    async def launch_process(self, process_id: str) -> str | None:
        """Launch a top-level process by processId. Returns end state or None."""
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

        return await self._execute_process(proc, self._task_registry.get(process_id))

    async def _execute_process(
        self, proc: dict, execute_fn: Callable | None,
    ) -> str:
        """Execute a process."""
        oid = proc["_id"]
        root_oid = proc.get("rootId", oid)

        cancel_flag = asyncio.Event()
        self._cancellation_flags[oid] = cancel_flag

        # Transition to running
        now = datetime.now(timezone.utc)
        await update_status(
            self._db, self._prefix, oid,
            ProcessStatus(state="running", running_since=now),
        )

        # Create context
        ctx = ProcessContext(
            process_oid=oid,
            process_id=proc["processId"],
            root_oid=root_oid,
            depth=proc.get("depth", 0),
            params=proc.get("params", {}),
            services=self._services,
            db=self._db,
            prefix=self._prefix,
            cancellation_flag=cancel_flag,
            child_counter={"next": 0},
        )
        ctx._executor = self

        if execute_fn is None:
            await update_status(
                self._db, self._prefix, oid,
                ProcessStatus(
                    state="failed", error="No execute function found",
                    failed_at=datetime.now(timezone.utc),
                ),
            )
            self._cancellation_flags.pop(oid, None)
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
            )
            self._cancellation_flags.pop(oid, None)
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
            )
        elif end_state == "cancelled":
            await update_status(
                self._db, self._prefix, oid,
                ProcessStatus(
                    state="cancelled",
                    stopped_at=datetime.now(timezone.utc),
                ),
            )

        self._cancellation_flags.pop(oid, None)
        return end_state

    async def execute_child(
        self,
        parent_ctx: ProcessContext,
        execute: Callable[..., Awaitable[None]],
        process_id: str,
        name: str,
        params: dict,
        survive_failure: bool = False,
        survive_cancel: bool = False,
    ) -> str:
        """Execute a child process (called from ProcessContext.run_child)."""
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
        )

        end_state = await self._execute_process(child_doc, execute)

        if end_state == "failed" and not survive_failure:
            raise RuntimeError(f"Child process '{name}' failed")
        if end_state == "cancelled" and not survive_cancel:
            parent_ctx._cancellation_flag.set()

        return end_state

    def request_cancel(self, process_oid: ObjectId) -> bool:
        """Request cancellation of a running process."""
        flag = self._cancellation_flags.get(process_oid)
        if flag is not None:
            flag.set()
            return True
        return False
