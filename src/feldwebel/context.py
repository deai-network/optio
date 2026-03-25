"""Process execution context — the interface task functions receive."""

import asyncio
import time
from typing import Any, Callable, Awaitable, TYPE_CHECKING
from bson import ObjectId
from motor.motor_asyncio import AsyncIOMotorDatabase

from feldwebel.models import Progress, ChildResult

if TYPE_CHECKING:
    from feldwebel.executor import Executor


class ProcessContext:
    """Context passed to task execute functions."""

    def __init__(
        self,
        process_oid: ObjectId,
        process_id: str,
        root_oid: ObjectId,
        depth: int,
        params: dict[str, Any],
        services: dict[str, Any],
        db: AsyncIOMotorDatabase,
        prefix: str,
        cancellation_flag: asyncio.Event,
        child_counter: dict,
    ):
        self.process_id = process_id
        self.params = params
        self.services = services
        self._process_oid = process_oid
        self._root_oid = root_oid
        self._depth = depth
        self._db = db
        self._prefix = prefix
        self._cancellation_flag = cancellation_flag
        self._child_counter = child_counter

        # Progress throttling
        self._pending_progress: Progress | None = None
        self._last_flush_time: float = 0
        self._flush_interval: float = 1.0
        self._flush_task: asyncio.Task | None = None

        # Set by executor after creation
        self._executor: "Executor | None" = None

    def report_progress(self, percent: float | None, message: str | None = None) -> None:
        """Update progress. percent=None for indeterminate. Buffered and flushed asynchronously (max once/sec)."""
        self._pending_progress = Progress(percent=percent, message=message)
        now = time.monotonic()
        if now - self._last_flush_time >= self._flush_interval:
            self._schedule_flush()

    def should_continue(self) -> bool:
        """Returns False if cancellation has been requested."""
        return not self._cancellation_flag.is_set()

    async def run_child(
        self,
        execute: Callable[..., Awaitable[None]],
        process_id: str,
        name: str,
        params: dict[str, Any] | None = None,
        survive_failure: bool = False,
        survive_cancel: bool = False,
    ) -> str:
        """Launch a sequential child process. Blocks until child completes."""
        if self._executor is None:
            raise RuntimeError("Executor not set on context")
        return await self._executor.execute_child(
            parent_ctx=self,
            execute=execute,
            process_id=process_id,
            name=name,
            params=params or {},
            survive_failure=survive_failure,
            survive_cancel=survive_cancel,
        )

    def parallel_group(
        self,
        max_concurrency: int = 10,
        survive_failure: bool = False,
        survive_cancel: bool = False,
    ) -> "ParallelGroup":
        """Create a parallel execution scope."""
        return ParallelGroup(
            ctx=self,
            max_concurrency=max_concurrency,
            survive_failure=survive_failure,
            survive_cancel=survive_cancel,
        )

    def _schedule_flush(self) -> None:
        if self._flush_task is None or self._flush_task.done():
            self._flush_task = asyncio.create_task(self._flush_progress())

    async def _flush_progress(self) -> None:
        if self._pending_progress is not None:
            from feldwebel.store import update_progress, append_log
            await update_progress(
                self._db, self._prefix, self._process_oid, self._pending_progress,
            )
            if self._pending_progress.message:
                await append_log(
                    self._db, self._prefix, self._process_oid,
                    "info", self._pending_progress.message,
                )
            self._last_flush_time = time.monotonic()
            self._pending_progress = None

    async def flush_final_progress(self) -> None:
        """Force flush any pending progress (called when process ends)."""
        if self._flush_task and not self._flush_task.done():
            self._flush_task.cancel()
            try:
                await self._flush_task
            except asyncio.CancelledError:
                pass
        if self._pending_progress is not None:
            from feldwebel.store import update_progress, append_log
            await update_progress(
                self._db, self._prefix, self._process_oid, self._pending_progress,
            )
            if self._pending_progress.message:
                await append_log(
                    self._db, self._prefix, self._process_oid,
                    "info", self._pending_progress.message,
                )
            self._pending_progress = None

    def _next_child_order(self) -> int:
        order = self._child_counter.get("next", 0)
        self._child_counter["next"] = order + 1
        return order


class ParallelGroup:
    """Context manager for parallel child process execution."""

    def __init__(
        self,
        ctx: ProcessContext,
        max_concurrency: int,
        survive_failure: bool,
        survive_cancel: bool,
    ):
        self._ctx = ctx
        self._max_concurrency = max_concurrency
        self._survive_failure = survive_failure
        self._survive_cancel = survive_cancel
        self._semaphore = asyncio.Semaphore(max_concurrency)
        self._tasks: list[asyncio.Task] = []
        self._results: list[ChildResult] = []
        self._failed = False

    @property
    def results(self) -> list[ChildResult]:
        return self._results

    async def spawn(
        self,
        execute: Callable[..., Awaitable[None]],
        process_id: str,
        name: str,
        params: dict[str, Any] | None = None,
    ) -> None:
        """Add a child to the group. Blocks if max_concurrency reached."""
        await self._semaphore.acquire()

        async def _run():
            try:
                state = await self._ctx.run_child(
                    execute=execute,
                    process_id=process_id,
                    name=name,
                    params=params,
                    survive_failure=True,
                    survive_cancel=True,
                )
                self._results.append(ChildResult(
                    process_id=process_id,
                    state=state,
                    error=None if state == "done" else f"Child {state}",
                ))
                if state == "failed" and not self._survive_failure:
                    self._failed = True
                if state == "cancelled" and not self._survive_cancel:
                    self._failed = True
            finally:
                self._semaphore.release()

        task = asyncio.create_task(_run())
        self._tasks.append(task)

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        if self._tasks:
            await asyncio.gather(*self._tasks, return_exceptions=True)
        if self._failed:
            failed = [r for r in self._results if r.state != "done"]
            raise RuntimeError(
                f"Parallel group failed: {len(failed)} children did not complete successfully"
            )
        return False
