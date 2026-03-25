"""Feldwebel lifecycle management — init, run, shutdown."""

import asyncio
import logging
import signal
from typing import Any, Callable, Awaitable
from motor.motor_asyncio import AsyncIOMotorDatabase
from redis.asyncio import Redis

from feldwebel.models import TaskInstance, FeldwebelConfig, ProcessStatus
from feldwebel.store import (
    upsert_process, remove_stale_processes,
    get_process_by_process_id, update_status, clear_result_fields,
)
from feldwebel.state_machine import CANCELLABLE_STATES
from feldwebel.executor import Executor
from feldwebel.consumer import CommandConsumer
from feldwebel.scheduler import ProcessScheduler

logger = logging.getLogger("feldwebel")


class Feldwebel:
    """Main orchestration class tying all components together."""

    def __init__(self):
        self._config: FeldwebelConfig | None = None
        self._redis: Redis | None = None
        self._executor: Executor | None = None
        self._consumer: CommandConsumer | None = None
        self._scheduler: ProcessScheduler | None = None
        self._tasks: list[TaskInstance] = []
        self._running = False

    async def init(
        self,
        mongo_db: AsyncIOMotorDatabase,
        redis_url: str,
        prefix: str,
        services: dict[str, Any] | None = None,
        get_task_definitions: Callable[..., Awaitable[list[TaskInstance]]] | None = None,
    ) -> None:
        """Initialize feldwebel."""
        services = services or {}
        self._config = FeldwebelConfig(
            mongo_db=mongo_db,
            redis_url=redis_url,
            prefix=prefix,
            services=services,
            get_task_definitions=get_task_definitions,
        )

        # Connect to Redis
        self._redis = Redis.from_url(redis_url)

        # Create executor
        self._executor = Executor(mongo_db, prefix, services)

        # Create consumer
        stream_name = f"{prefix}:commands"
        self._consumer = CommandConsumer(self._redis, stream_name)
        self._consumer.on("launch", self._handle_launch)
        self._consumer.on("cancel", self._handle_cancel)
        self._consumer.on("dismiss", self._handle_dismiss)
        self._consumer.on("resync", self._handle_resync)
        await self._consumer.setup()

        # Create scheduler
        self._scheduler = ProcessScheduler(
            launch_fn=self._handle_launch_by_process_id,
        )

        # Run initial sync
        await self._sync_definitions()

        logger.info(f"Feldwebel initialized with prefix '{prefix}'")

    async def run(self) -> None:
        """Start the main loop. Blocks until shutdown."""
        self._running = True

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

        # Run consumer (blocks)
        try:
            await self._consumer.run()
        finally:
            await self._scheduler.stop()

    async def shutdown(self) -> None:
        """Graceful shutdown."""
        logger.info("Shutdown requested")
        self._running = False
        if self._consumer:
            self._consumer.stop()

        # Cancel all running processes
        if self._executor:
            for oid, flag in list(self._executor._cancellation_flags.items()):
                flag.set()

            # Wait briefly for processes to exit
            for _ in range(50):  # 5 seconds max
                if not self._executor._cancellation_flags:
                    break
                await asyncio.sleep(0.1)

        if self._redis:
            await self._redis.aclose()

        logger.info("Shutdown complete")

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
        await self._sync_definitions()
