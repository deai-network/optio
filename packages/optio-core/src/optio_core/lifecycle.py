"""Optio lifecycle management — init, run, shutdown."""

import asyncio
import logging
import signal
from datetime import datetime, timezone
from typing import Any, Callable, Awaitable
from bson import ObjectId
from motor.motor_asyncio import AsyncIOMotorDatabase
try:
    from redis.asyncio import Redis
except ImportError:
    Redis = None  # type: ignore[assignment,misc]

from optio_core.models import TaskInstance, OptioConfig, ProcessStatus
from optio_core.store import (
    upsert_process, remove_stale_processes,
    get_process_by_process_id, update_status, clear_result_fields,
    append_log,
)
from optio_core.state_machine import ACTIVE_STATES, CANCELLABLE_STATES
from optio_core.executor import Executor
from optio_core.consumer import CommandConsumer
from optio_core.scheduler import ProcessScheduler

logger = logging.getLogger("optio_core_core")


class Optio:
    """Main orchestration class tying all components together."""

    def __init__(self):
        self._config: OptioConfig | None = None
        self._redis: Redis | None = None
        self._executor: Executor | None = None
        self._consumer: CommandConsumer | None = None
        self._scheduler: ProcessScheduler | None = None
        self._tasks: list[TaskInstance] = []
        self._running = False
        self._heartbeat_task: asyncio.Task | None = None

    async def init(
        self,
        mongo_db: AsyncIOMotorDatabase,
        prefix: str = "optio",
        redis_url: str | None = None,
        services: dict[str, Any] | None = None,
        get_task_definitions: Callable[..., Awaitable[list[TaskInstance]]] | None = None,
    ) -> None:
        """Initialize optio.

        Args:
            mongo_db: Motor async MongoDB database.
            prefix: Namespace for collections and streams.
            redis_url: Redis connection URL. If None, Redis features (command
                consumer, custom commands) are disabled and processes are
                managed via direct method calls.
            services: Custom services dict passed to task execute functions.
            get_task_definitions: Async function returning task definitions.
        """
        services = services or {}
        self._config = OptioConfig(
            mongo_db=mongo_db,
            prefix=prefix,
            redis_url=redis_url,
            services=services,
            get_task_definitions=get_task_definitions,
        )

        # Connect to Redis (if configured)
        if redis_url:
            if Redis is None:
                raise ImportError(
                    "Redis support requires the 'redis' extra: "
                    "pip install optio[redis]"
                )
            self._redis = Redis.from_url(redis_url)

            # Create consumer
            db_name = mongo_db.name
            stream_name = f"{db_name}/{prefix}:commands"
            self._consumer = CommandConsumer(self._redis, stream_name)
            self._consumer.on("launch", self._handle_launch)
            self._consumer.on("cancel", self._handle_cancel)
            self._consumer.on("dismiss", self._handle_dismiss)
            self._consumer.on("resync", self._handle_resync)
            await self._consumer.setup()

        # Create executor
        self._executor = Executor(mongo_db, prefix, services)

        # Run migrations
        from optio_core.migrations import fw_migrations
        await fw_migrations.run(mongo_db, prefix=f"{prefix}_fw")

        # Create scheduler
        self._scheduler = ProcessScheduler(
            launch_fn=self._handle_launch_by_process_id,
        )

        # Reconcile any processes left in active states by a previous session.
        # Spec: docs/2026-04-22-process-reconciliation-design.md
        await self._reconcile_interrupted_processes()

        # Run initial sync
        await self._sync_definitions()

        redis_info = f", redis='{redis_url}'" if redis_url else ", no Redis"
        logger.info(f"Optio initialized: db='{mongo_db.name}', prefix='{prefix}'{redis_info}")

    def on_command(self, command_type: str, handler: Callable[..., Awaitable]) -> None:
        """Register a custom command handler (must be called before run)."""
        if self._consumer is None:
            raise RuntimeError("Custom commands require Redis")
        self._consumer.on(command_type, handler)

    async def adhoc_define(
        self,
        task: TaskInstance,
        parent_id: ObjectId | None = None,
        ephemeral: bool = False,
    ) -> dict:
        """Define an ad-hoc process. Returns the process document.

        Creates the process in DB and registers the execute function.
        The process starts in 'idle' state — use the standard 'launch'
        command to start it.
        """
        from optio_core.store import (
            upsert_process, get_process_by_id, create_child_process,
        )

        if parent_id is None:
            # Root ad-hoc process
            proc = await upsert_process(self._config.mongo_db, self._config.prefix, task)
            # Set adhoc and ephemeral flags (upsert_process sets defaults on insert)
            coll = self._config.mongo_db[f"{self._config.prefix}_processes"]
            await coll.update_one(
                {"_id": proc["_id"]},
                {"$set": {"adhoc": True, "ephemeral": ephemeral}},
            )
            proc["adhoc"] = True
            proc["ephemeral"] = ephemeral
        else:
            # Child ad-hoc process
            parent = await get_process_by_id(
                self._config.mongo_db, self._config.prefix, parent_id,
            )
            if parent is None:
                raise ValueError(f"Parent process {parent_id} not found")
            proc = await create_child_process(
                self._config.mongo_db, self._config.prefix,
                parent_oid=parent_id,
                root_oid=parent.get("rootId", parent["_id"]),
                process_id=task.process_id,
                name=task.name,
                params=task.params,
                depth=parent.get("depth", 0) + 1,
                order=0,
                metadata=task.metadata,
                adhoc=True,
                ephemeral=ephemeral,
            )

        self._executor._task_registry[task.process_id] = task.execute
        return proc

    async def adhoc_delete(self, process_id: str) -> None:
        """Delete an ad-hoc process from DB and task registry."""
        from optio_core.store import delete_process
        await delete_process(self._config.mongo_db, self._config.prefix, process_id)
        self._executor._task_registry.pop(process_id, None)

    async def launch(self, process_id: str) -> None:
        """Fire-and-forget launch. Returns immediately, process runs in background."""
        asyncio.create_task(self._executor.launch_process(process_id))

    async def launch_and_wait(self, process_id: str) -> None:
        """Launch and wait for the process to complete. Full progress tracking."""
        await self._executor.launch_process(process_id)

    async def cancel(self, process_id: str) -> None:
        """Cancel a running or scheduled process."""
        await self._handle_cancel({"processId": process_id})

    async def dismiss(self, process_id: str) -> None:
        """Dismiss a completed process (reset to idle)."""
        await self._handle_dismiss({"processId": process_id})

    async def resync(self, clean: bool = False) -> None:
        """Re-sync task definitions from the generator."""
        await self._handle_resync({"clean": clean})

    async def get_process(self, process_id: str) -> dict | None:
        """Get a process by its process_id string."""
        return await get_process_by_process_id(
            self._config.mongo_db, self._config.prefix, process_id,
        )

    async def list_processes(
        self,
        state: str | None = None,
        root_id: str | None = None,
        metadata: dict[str, str] | None = None,
    ) -> list[dict]:
        """List processes with optional filters."""
        from bson import ObjectId as OID
        from optio_core.store import list_processes as _list_processes
        return await _list_processes(
            self._config.mongo_db,
            self._config.prefix,
            state=state,
            root_id=OID(root_id) if root_id else None,
            metadata=metadata,
        )

    async def run(self) -> None:
        """Start the main loop. Blocks until shutdown."""
        self._running = True
        self._shutdown_event = asyncio.Event()

        # Set up signal handlers (only works in main thread)
        try:
            loop = asyncio.get_event_loop()
            for sig in (signal.SIGTERM, signal.SIGINT):
                loop.add_signal_handler(
                    sig, lambda: asyncio.create_task(self.shutdown()),
                )
        except (NotImplementedError, RuntimeError):
            pass  # Signal handlers not available (e.g., in tests)

        # Start scheduler
        await self._scheduler.start()

        # Start heartbeat (if Redis is configured)
        if self._redis:
            self._heartbeat_task = asyncio.create_task(self._heartbeat_loop())

        try:
            if self._consumer:
                await self._consumer.run()
            else:
                await self._shutdown_event.wait()
        finally:
            await self._scheduler.stop()

    async def shutdown(self, grace_seconds: float = 5.0) -> None:
        """Graceful shutdown.

        Args:
            grace_seconds: how long to wait for cooperating tasks to unwind
                after their cancellation flag is set. Tasks still running after
                the grace period are force-finalized to 'failed' in Mongo.
        """
        logger.info("Shutdown requested")
        self._running = False

        if self._heartbeat_task:
            self._heartbeat_task.cancel()
            try:
                await self._heartbeat_task
            except asyncio.CancelledError:
                pass
            self._heartbeat_task = None

        if self._consumer:
            self._consumer.stop()

        if hasattr(self, '_shutdown_event'):
            self._shutdown_event.set()

        # Cancel all running processes
        if self._executor:
            for oid, flag in list(self._executor._cancellation_flags.items()):
                flag.set()

            # Wait for cooperating processes to exit
            step = 0.1
            steps = max(1, int(grace_seconds / step))
            for _ in range(steps):
                if not self._executor._cancellation_flags:
                    break
                await asyncio.sleep(step)

            # Force-finalize any task that did not unwind in time.
            # Spec: docs/2026-04-22-process-reconciliation-design.md
            remaining = list(self._executor._cancellation_flags.keys())
            if remaining:
                await self._force_finalize_stuck_processes(remaining)

        if self._redis:
            await self._redis.aclose()

        logger.info("Shutdown complete")

    async def _reconcile_interrupted_processes(self) -> None:
        """Mark processes left in active states by a previous session as failed.

        Spec: docs/2026-04-22-process-reconciliation-design.md (Rule 1).

        On a fresh server start `Executor._cancellation_flags` is empty, so any
        Mongo record whose state is in `ACTIVE_STATES` was interrupted and
        cannot be running anywhere. Reset each one to 'failed' with an error
        explaining what happened, clear `widgetUpstream` (whose worker is
        definitely gone), and append a log entry. `widgetData` is preserved
        intentionally — the widget-extensions spec keeps it across terminal
        states for post-mortem inspection.
        """
        coll = self._config.mongo_db[f"{self._config.prefix}_processes"]
        cursor = coll.find(
            {"status.state": {"$in": list(ACTIVE_STATES)}},
            {"_id": 1, "status.state": 1},
        )
        stale = [(doc["_id"], doc["status"]["state"]) async for doc in cursor]
        if not stale:
            return

        now = datetime.now(timezone.utc)
        error_msg = "Process was interrupted by server restart"
        for oid, prev_state in stale:
            await update_status(
                self._config.mongo_db, self._config.prefix, oid,
                ProcessStatus(state="failed", error=error_msg, failed_at=now),
            )
            await coll.update_one({"_id": oid}, {"$set": {"widgetUpstream": None}})
            await append_log(
                self._config.mongo_db, self._config.prefix, oid,
                "event", f"State reconciled: {prev_state} -> failed (server restart)",
            )
        logger.info(f"Reconciled {len(stale)} interrupted process(es) to 'failed'")

    async def _force_finalize_stuck_processes(self, oids: list[ObjectId]) -> None:
        """Mark processes that did not unwind during shutdown as failed.

        Spec: docs/2026-04-22-process-reconciliation-design.md (Rule 2).

        Uses a conditional Mongo update so we do not overwrite a terminal
        state a task may have flushed at the last moment. The same
        conditional scopes the widgetUpstream clearing — a task that won
        the race to terminal owns its widgetUpstream transition (via the
        executor's teardown path).
        """
        coll = self._config.mongo_db[f"{self._config.prefix}_processes"]
        now = datetime.now(timezone.utc)
        error_msg = "Task did not exit within shutdown grace period"
        status_doc = ProcessStatus(
            state="failed", error=error_msg, failed_at=now,
        ).to_dict()

        forced = 0
        for oid in oids:
            result = await coll.update_one(
                {"_id": oid, "status.state": {"$in": list(ACTIVE_STATES)}},
                {"$set": {"status": status_doc, "widgetUpstream": None}},
            )
            if result.modified_count:
                forced += 1
                await append_log(
                    self._config.mongo_db, self._config.prefix, oid,
                    "event", "State forced: running -> failed (shutdown grace period exceeded)",
                )
            self._executor._cancellation_flags.pop(oid, None)

        if forced:
            logger.warning(
                f"Force-finalized {forced} process(es) that did not exit within grace period"
            )

    async def _heartbeat_loop(self) -> None:
        """Periodically set a heartbeat key in Redis with TTL."""
        db_name = self._config.mongo_db.name
        prefix = self._config.prefix
        key = f"{db_name}/{prefix}:heartbeat"
        while self._running:
            try:
                await self._redis.set(key, "1", ex=15)
            except Exception as e:
                logger.warning(f"Heartbeat failed: {e}")
            await asyncio.sleep(5)

    async def _sync_definitions(self) -> None:
        """Run the task generator and sync with database."""
        if self._config.get_task_definitions is None:
            return

        self._tasks = await self._config.get_task_definitions(self._config.services)

        for task in self._tasks:
            await upsert_process(self._config.mongo_db, self._config.prefix, task)

        valid_ids = {t.process_id for t in self._tasks}
        removed = await remove_stale_processes(
            self._config.mongo_db, self._config.prefix, valid_ids,
        )
        if removed:
            logger.info(f"Removed {removed} stale process records")

        self._executor.register_tasks(self._tasks)
        await self._scheduler.sync_schedules(self._tasks)

        logger.info(f"Synced {len(self._tasks)} task definitions")

    async def _handle_launch(self, payload: dict) -> None:
        process_id = payload.get("processId")
        if process_id:
            await self._handle_launch_by_process_id(process_id)

    async def _handle_launch_by_process_id(self, process_id: str) -> None:
        # Run in a background task so the consumer can continue
        asyncio.create_task(self._executor.launch_process(process_id))

    async def _handle_cancel(self, payload: dict) -> None:
        process_id = payload.get("processId")
        if not process_id:
            return

        proc = await get_process_by_process_id(
            self._config.mongo_db, self._config.prefix, process_id,
        )
        if proc is None:
            return

        current_state = proc["status"]["state"]
        if current_state not in CANCELLABLE_STATES:
            return

        if current_state == "scheduled":
            # Not yet running — go directly to cancelled
            from datetime import datetime, timezone
            await update_status(
                self._config.mongo_db, self._config.prefix, proc["_id"],
                ProcessStatus(state="cancelled", stopped_at=datetime.now(timezone.utc)),
            )
            return

        await update_status(
            self._config.mongo_db, self._config.prefix, proc["_id"],
            ProcessStatus(state="cancel_requested"),
        )

        found = self._executor.request_cancel(proc["_id"])
        if found:
            await update_status(
                self._config.mongo_db, self._config.prefix, proc["_id"],
                ProcessStatus(state="cancelling"),
            )

    async def _handle_dismiss(self, payload: dict) -> None:
        process_id = payload.get("processId")
        if not process_id:
            return

        proc = await get_process_by_process_id(
            self._config.mongo_db, self._config.prefix, process_id,
        )
        if proc is None:
            return

        if proc["status"]["state"] not in {"done", "failed", "cancelled"}:
            return

        await clear_result_fields(
            self._config.mongo_db, self._config.prefix, proc["_id"],
        )
        await update_status(
            self._config.mongo_db, self._config.prefix, proc["_id"],
            ProcessStatus(state="idle"),
        )

    async def _handle_resync(self, payload: dict) -> None:
        clean = payload.get("clean", False)
        if clean:
            # Nuke all process records before re-importing
            coll = self._config.mongo_db[f"{self._config.prefix}_processes"]
            deleted = await coll.delete_many({})
            logger.info(f"Nuked {deleted.deleted_count} process records")
        await self._sync_definitions()
