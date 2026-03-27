"""Process execution context — the interface task functions receive."""

import asyncio
import os
import time
from typing import Any, Callable, Awaitable, TYPE_CHECKING
from bson import ObjectId
from motor.motor_asyncio import AsyncIOMotorDatabase

from optio.models import Progress, ChildResult, ChildProgressInfo

if TYPE_CHECKING:
    from optio.executor import Executor


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
        metadata: dict[str, Any] | None = None,
    ):
        self.process_id = process_id
        self.params = params
        self.metadata = metadata or {}
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
        _ms = int(os.environ.get("OPTIO_PROGRESS_FLUSH_INTERVAL_MS", "100"))
        self._flush_interval: float = _ms / 1000.0
        self._flush_task: asyncio.Task | None = None

        # Child progress callback
        self._child_progress_snapshots: list["ChildProgressInfo"] = []
        self._on_child_progress: Callable | None = None
        self._child_callback_interval: float = 0.1  # 100ms = 10/sec
        self._last_child_callback_time: float = 0
        self._pending_child_callback: bool = False
        self._child_callback_task: asyncio.Task | None = None

        # Set by executor to route progress to parent
        self._parent_listener: Callable | None = None

        # Set by executor after creation
        self._executor: "Executor | None" = None

    def report_progress(self, percent: float | None, message: str | None = None) -> None:
        """Update progress. percent=None for indeterminate. Buffered and flushed asynchronously."""
        self._pending_progress = Progress(percent=percent, message=message)
        now = time.monotonic()
        if now - self._last_flush_time >= self._flush_interval:
            self._schedule_flush()
        # Notify parent listener if wired
        if self._parent_listener is not None:
            self._parent_listener(percent, message)

    def should_continue(self) -> bool:
        """Returns False if cancellation has been requested."""
        return not self._cancellation_flag.is_set()

    async def mark_ephemeral(self) -> None:
        """Mark this process for deletion after completion."""
        from optio.store import _collection
        await _collection(self._db, self._prefix).update_one(
            {"_id": self._process_oid},
            {"$set": {"ephemeral": True}},
        )

    async def run_child(
        self,
        execute: Callable[..., Awaitable[None]],
        process_id: str,
        name: str,
        params: dict[str, Any] | None = None,
        survive_failure: bool = False,
        survive_cancel: bool = False,
        on_child_progress: Callable | None = None,
    ) -> str:
        """Launch a sequential child process. Blocks until child completes."""
        if self._executor is None:
            raise RuntimeError("Executor not set on context")
        if on_child_progress is not None:
            self._set_child_callback(on_child_progress)
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
        on_child_progress: Callable | None = None,
    ) -> "ParallelGroup":
        """Create a parallel execution scope."""
        return ParallelGroup(
            ctx=self,
            max_concurrency=max_concurrency,
            survive_failure=survive_failure,
            survive_cancel=survive_cancel,
            on_child_progress=on_child_progress,
        )

    def _schedule_flush(self) -> None:
        if self._flush_task is None or self._flush_task.done():
            self._flush_task = asyncio.create_task(self._flush_progress())

    async def _flush_progress(self) -> None:
        if self._pending_progress is not None:
            from optio.store import update_progress, append_log
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
            from optio.store import update_progress, append_log
            await update_progress(
                self._db, self._prefix, self._process_oid, self._pending_progress,
            )
            if self._pending_progress.message:
                await append_log(
                    self._db, self._prefix, self._process_oid,
                    "info", self._pending_progress.message,
                )
            self._pending_progress = None

    def _set_child_callback(self, callback: Callable) -> None:
        """Set the on_child_progress callback for this context."""
        self._on_child_progress = callback

    def _notify_child_progress(self, child_id: str, name: str, state: str,
                               percent: float | None, message: str | None) -> None:
        """Called when a child reports progress. Updates snapshot and fires throttled callback."""
        # Update or add snapshot
        for info in self._child_progress_snapshots:
            if info.process_id == child_id:
                info.percent = percent
                info.message = message
                info.state = state
                break
        else:
            self._child_progress_snapshots.append(
                ChildProgressInfo(process_id=child_id, name=name, state=state,
                                  percent=percent, message=message)
            )
        self._fire_child_callback_throttled()

    def _notify_child_state_change(self, child_id: str, state: str) -> None:
        """Called when a child changes state (done/failed/cancelled). Fires callback immediately."""
        for info in self._child_progress_snapshots:
            if info.process_id == child_id:
                info.state = state
                if state in ("done", "failed", "cancelled"):
                    info.percent = 100.0
                break
        if self._on_child_progress is not None:
            self._cancel_pending_child_callback()
            self._on_child_progress(list(self._child_progress_snapshots))
            self._last_child_callback_time = time.monotonic()

    def _fire_child_callback_throttled(self) -> None:
        """Fire the child callback, respecting the 10/sec throttle."""
        if self._on_child_progress is None:
            return
        now = time.monotonic()
        if now - self._last_child_callback_time >= self._child_callback_interval:
            self._cancel_pending_child_callback()
            self._on_child_progress(list(self._child_progress_snapshots))
            self._last_child_callback_time = now
        elif not self._pending_child_callback:
            self._pending_child_callback = True
            remaining = self._child_callback_interval - (now - self._last_child_callback_time)
            self._child_callback_task = asyncio.create_task(self._deferred_child_callback(remaining))

    async def _deferred_child_callback(self, delay: float) -> None:
        """Fire buffered child callback after delay."""
        await asyncio.sleep(delay)
        self._pending_child_callback = False
        if self._on_child_progress is not None:
            self._on_child_progress(list(self._child_progress_snapshots))
            self._last_child_callback_time = time.monotonic()

    def _cancel_pending_child_callback(self) -> None:
        if self._child_callback_task and not self._child_callback_task.done():
            self._child_callback_task.cancel()
        self._pending_child_callback = False

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
        on_child_progress: Callable | None = None,
    ):
        self._ctx = ctx
        self._max_concurrency = max_concurrency
        self._survive_failure = survive_failure
        self._survive_cancel = survive_cancel
        self._semaphore = asyncio.Semaphore(max_concurrency)
        self._tasks: list[asyncio.Task] = []
        self._results: list[ChildResult] = []
        self._failed = False
        if on_child_progress is not None:
            self._ctx._set_child_callback(on_child_progress)

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
