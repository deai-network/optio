"""Process execution context — the interface task functions receive."""

import asyncio
import logging as _logging
import os
import time
from contextlib import asynccontextmanager
from typing import Any, Callable, Awaitable, TYPE_CHECKING
from bson import ObjectId
from motor.motor_asyncio import AsyncIOMotorDatabase, AsyncIOMotorGridFSBucket

from optio_core.models import Progress, ChildResult, ChildProgressInfo, InnerAuth

if TYPE_CHECKING:
    from optio_core.executor import Executor

_log = _logging.getLogger("optio_core.context")


class _GridInWrapper:
    """Thin wrapper around a motor GridIn upload stream.

    Exposes ``file_id`` as a convenience alias for the underlying ``_id``
    attribute so callers don't need to use a private-looking name.
    """

    def __init__(self, stream) -> None:
        self._stream = stream

    @property
    def file_id(self) -> ObjectId:
        return self._stream._id

    def __getattr__(self, name: str):
        return getattr(self._stream, name)


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
        resume: bool = False,
    ):
        self.process_id = process_id
        self.params = params
        self.metadata = metadata or {}
        self.services = services
        self.resume = resume
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
        from optio_core.store import _collection
        await _collection(self._db, self._prefix).update_one(
            {"_id": self._process_oid},
            {"$set": {"ephemeral": True}},
        )

    async def set_widget_upstream(
        self,
        url: str,
        inner_auth: InnerAuth | None = None,
    ) -> None:
        """Register the upstream URL and (optional) inner auth for the widget proxy."""
        from optio_core.store import update_widget_upstream
        await update_widget_upstream(
            self._db, self._prefix, self._process_oid, url, inner_auth,
        )

    async def clear_widget_upstream(self) -> None:
        """Clear widgetUpstream so the proxy returns 404 for this process."""
        from optio_core.store import clear_widget_upstream
        await clear_widget_upstream(self._db, self._prefix, self._process_oid)

    async def set_widget_data(self, data) -> None:
        """Overwrite widgetData. Must be JSON-serializable. Optio does not interpret."""
        from optio_core.store import update_widget_data
        await update_widget_data(self._db, self._prefix, self._process_oid, data)

    async def clear_widget_data(self) -> None:
        """Clear widgetData."""
        from optio_core.store import clear_widget_data
        await clear_widget_data(self._db, self._prefix, self._process_oid)

    async def mark_has_saved_state(self) -> None:
        """Flag that a resumable task has durable state.

        No-op with a warning when the task is not declared `supports_resume=True`.
        Idempotent: a second call with the same value issues no redundant update.
        """
        await self._set_has_saved_state(True)

    async def clear_has_saved_state(self) -> None:
        """Flag that a resumable task no longer has durable state.

        No-op with a warning when the task is not declared `supports_resume=True`.
        Idempotent: a second call with the same value issues no redundant update.
        """
        await self._set_has_saved_state(False)

    async def _set_has_saved_state(self, value: bool) -> None:
        from optio_core.store import _collection
        coll = _collection(self._db, self._prefix)
        current = await coll.find_one(
            {"_id": self._process_oid},
            {"supportsResume": 1, "hasSavedState": 1},
        )
        if current is None:
            _log.warning(
                "mark/clear_has_saved_state: process %s not found", self._process_oid,
            )
            return
        if not current.get("supportsResume", False):
            _log.warning(
                "mark/clear_has_saved_state called on task %s which has supports_resume=False; ignored",
                self.process_id,
            )
            return
        if bool(current.get("hasSavedState", False)) == value:
            return  # Idempotent: no redundant write.
        await coll.update_one(
            {"_id": self._process_oid},
            {"$set": {"hasSavedState": value}},
        )

    def _gridfs(self) -> AsyncIOMotorGridFSBucket:
        return AsyncIOMotorGridFSBucket(self._db)

    @asynccontextmanager
    async def store_blob(self, name: str):
        """Open a GridFS upload stream tagged with processId + prefix.

        Usage:
            async with ctx.store_blob("session") as writer:
                await writer.write(chunk)
                # ... more writes
            # After the `async with` block exits cleanly, writer.file_id is the
            # ObjectId of the stored file.
        """
        bucket = self._gridfs()
        metadata = {
            "processId": str(self._process_oid),
            "prefix": self._prefix,
            "name": name,
        }
        async with bucket.open_upload_stream(name, metadata=metadata) as stream:
            yield _GridInWrapper(stream)

    @asynccontextmanager
    async def load_blob(self, file_id: ObjectId):
        """Open a GridFS download stream for `file_id`.

        Usage:
            async with ctx.load_blob(file_id) as reader:
                chunk = await reader.read(1 << 20)
        """
        bucket = self._gridfs()
        stream = await bucket.open_download_stream(file_id)
        try:
            yield stream
        finally:
            stream.close()

    async def delete_blob(self, file_id: ObjectId) -> None:
        """Delete a GridFS file. No-op if the file does not exist."""
        import gridfs.errors
        bucket = self._gridfs()
        try:
            await bucket.delete(file_id)
        except gridfs.errors.NoFile:
            pass  # already gone; nothing to do
        except Exception:
            _log.warning("delete_blob(%s): suppressed error during cleanup", file_id, exc_info=True)

    async def run_child(
        self,
        execute: Callable[..., Awaitable[None]],
        process_id: str,
        name: str,
        params: dict[str, Any] | None = None,
        survive_failure: bool = False,
        survive_cancel: bool = False,
        on_child_progress: Callable | None = None,
        description: str | None = None,
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
            description=description,
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
            from optio_core.store import update_progress, append_log
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
            from optio_core.store import update_progress, append_log
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
        description: str | None = None,
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
                    description=description,
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
