"""Optio lifecycle management — init, run, shutdown."""

import asyncio
import logging
import re as _re
import signal
import time
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager, AsyncExitStack
from datetime import datetime, timezone
from typing import Any, Callable, Awaitable
from bson import ObjectId
from motor.motor_asyncio import AsyncIOMotorDatabase
try:
    from redis.asyncio import Redis
except ImportError:
    Redis = None  # type: ignore[assignment,misc]

from optio_core.models import (
    TaskInstance, OptioConfig, ProcessStatus, ProcessMetadataFilter,
    matches_filter, LaunchBlocked,
    LaunchOutcome, CancelOutcome, DismissOutcome,
)
from optio_core.store import (
    upsert_process, remove_stale_processes, find_stale_process_ids,
    get_process_by_process_id, update_status, clear_result_fields,
    append_log, compute_expire_at,
)
from optio_core.state_machine import ACTIVE_STATES, CANCELLABLE_STATES, END_STATES
from optio_core.executor import Executor
from clamator_protocol import RpcServerCore
from clamator_over_redis import RedisRpcServer
from optio_core.scheduler import ProcessScheduler

logger = logging.getLogger("optio_core_core")


_OBJECTID_RE = _re.compile(r"^[a-fA-F0-9]{24}$")


def _is_objectid(s: str) -> bool:
    return bool(_OBJECTID_RE.match(s))


from dataclasses import dataclass


@dataclass
class _BlockEntry:
    """One in-memory launch-block entry."""
    filter: ProcessMetadataFilter
    reason: str | None


class Optio:
    """Main orchestration class tying all components together."""

    def __init__(self):
        self._config: OptioConfig | None = None
        self._redis: Redis | None = None
        self._executor: Executor | None = None
        self.rpc_server: "RpcServerCore | None" = None
        self._engine_service = None  # Set by init() when an rpc_server exists.
        self._owned_rpc_server: bool = False
        self._scheduler: ProcessScheduler | None = None
        self._running = False
        self._heartbeat_task: asyncio.Task | None = None
        self._supervisor_task: asyncio.Task | None = None
        self._launch_blocks: dict[uuid.UUID, _BlockEntry] = {}

    async def init(
        self,
        mongo_db: AsyncIOMotorDatabase,
        prefix: str = "optio",
        redis_url: str | None = None,
        rpc_server: "RpcServerCore | None" = None,
        services: dict[str, Any] | None = None,
        get_task_definitions: Callable[
            [dict[str, Any], ProcessMetadataFilter | None],
            Awaitable[list[TaskInstance]],
        ] | None = None,
        cancel_grace_seconds: float = 5.0,
    ) -> None:
        """Initialize optio.

        Args:
            mongo_db: Motor async MongoDB database.
            prefix: Namespace for collections and streams.
            redis_url: Redis connection URL. If None and rpc_server is None,
                Redis features (command consumer, RPC server) are disabled and
                processes are managed via direct method calls. Mutually
                exclusive with rpc_server.
            rpc_server: Pre-built clamator RpcServerCore. If supplied, optio
                does not construct one itself; the caller owns its lifecycle
                (start/stop). Mutually exclusive with redis_url. Used by
                tests and apps that share a server across services.
            services: Custom services dict passed to task execute functions.
            get_task_definitions: Async function (services, metadata_filter)
                returning task definitions.
            cancel_grace_seconds: Cooperative-cancel deadline (seconds). After
                this elapses without the task unwinding, the supervisor
                force-cancels via asyncio.Task.cancel() and writes a terminal
                'failed' state. Default 5.0. Same value applies to every
                cancel during this Optio lifetime.
        """
        if redis_url is not None and rpc_server is not None:
            raise ValueError(
                "redis_url and rpc_server are mutually exclusive — pass only one"
            )

        services = services or {}
        self._config = OptioConfig(
            mongo_db=mongo_db,
            prefix=prefix,
            redis_url=redis_url,
            services=services,
            get_task_definitions=get_task_definitions,
            cancel_grace_seconds=cancel_grace_seconds,
        )

        # Connect to Redis (if configured)
        if redis_url:
            if Redis is None:
                raise ImportError(
                    "Redis support requires the 'redis' extra: "
                    "pip install optio[redis]"
                )
            self._redis = Redis.from_url(redis_url)

            # Clamator RPC server — the sole inbound control channel.
            db_name = mongo_db.name
            self.rpc_server = RedisRpcServer(
                redis=self._redis,
                key_prefix=f"{db_name}/{prefix}",
                consumer_claim_idle_ms=600_000,  # 10 min — accommodates groupCancelAndWait
            )
            self._owned_rpc_server = True

            # Defer OptioEngineService import to avoid circular module load.
            from optio_core._engine_service import OptioEngineService
            from optio_core._generated.optio_engine import optio_engine_contract
            self._engine_service = OptioEngineService(self)
            self.rpc_server.register_service(optio_engine_contract, self._engine_service)

        elif rpc_server is not None:
            # App-provided server (test or shared-server mode). No legacy consumer.
            self.rpc_server = rpc_server
            self._owned_rpc_server = False
            from optio_core._engine_service import OptioEngineService
            from optio_core._generated.optio_engine import optio_engine_contract
            self._engine_service = OptioEngineService(self)
            self.rpc_server.register_service(optio_engine_contract, self._engine_service)

        # Create executor
        self._executor = Executor(mongo_db, prefix, services, optio=self)

        # Run migrations
        from optio_core.migrations import fw_migrations
        await fw_migrations.run(mongo_db, prefix=f"{prefix}_fw")

        # Load persisted launch blocks ("perma-bans"). Spec:
        # docs/2026-04-30-persistent-launch-blocks-design.md.
        await self._load_persisted_blocks()

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

    @asynccontextmanager
    async def block_launches(
        self,
        launch_filter: ProcessMetadataFilter,
        *,
        persist: bool = False,
        reason: str | None = None,
    ) -> AsyncIterator[None]:
        """Async context manager: while active, reject launches whose
        task metadata matches `launch_filter` (raises LaunchBlocked).

        Multiple concurrent block_launches() calls — overlapping or
        identical filters — stack independently. Each context owns
        its own block; exiting one does not lift another's block.

        An empty filter `{}` matches every task metadata — registering
        it blocks all launches.

        When `persist=True`, a Mongo record is written on entry and the
        block remains active after the context manager exits. `reason`
        is stored on the record (default None). Spec:
        docs/2026-04-30-persistent-launch-blocks-design.md.
        """
        if persist:
            from optio_core import _launch_block_store as _lb_store
            coll = _lb_store.collection(
                self._config.mongo_db, self._config.prefix,
            )
            await _lb_store.upsert_block(coll, launch_filter, reason)
        token = uuid.uuid4()
        self._launch_blocks[token] = _BlockEntry(filter=launch_filter, reason=reason)
        try:
            yield
        finally:
            if not persist:
                self._launch_blocks.pop(token, None)

    async def unblock_launches(
        self,
        launch_filter: ProcessMetadataFilter,
    ) -> int:
        """Remove every persistent record and every in-memory block entry
        whose filter equals `launch_filter` by exact dict equality. Returns
        the count of in-memory entries removed.

        Spec: docs/2026-04-30-persistent-launch-blocks-design.md.
        """
        from optio_core import _launch_block_store as _lb_store
        coll = _lb_store.collection(
            self._config.mongo_db, self._config.prefix,
        )
        await _lb_store.delete_by_filter(coll, launch_filter)

        tokens = [
            t for t, entry in self._launch_blocks.items()
            if entry.filter == launch_filter
        ]
        for t in tokens:
            self._launch_blocks.pop(t, None)
        return len(tokens)

    def _check_launch_blocks(self, metadata: ProcessMetadataFilter | None) -> None:
        """Raise LaunchBlocked if `metadata` matches any registered block.

        Fast path: empty `_launch_blocks` returns immediately.
        """
        if not self._launch_blocks:
            return
        md = metadata or {}
        for entry in self._launch_blocks.values():
            if matches_filter(md, entry.filter):
                msg = f"Launch blocked by filter {entry.filter}; task metadata={md}"
                if entry.reason is not None:
                    msg += f"; reason={entry.reason}"
                raise LaunchBlocked(msg)

    async def _load_persisted_blocks(self) -> None:
        """Load every record from `{prefix}_launch_blocks` into the in-memory
        `_launch_blocks` dict. Each record gets a fresh UUID token. Empty or
        missing collection produces an empty load.
        """
        from optio_core import _launch_block_store as _lb_store
        coll = _lb_store.collection(
            self._config.mongo_db, self._config.prefix,
        )
        rows = await _lb_store.load_all(coll)
        for row in rows:
            token = uuid.uuid4()
            self._launch_blocks[token] = _BlockEntry(
                filter=row.filter, reason=row.reason,
            )
        if rows:
            logger.info(f"Loaded {len(rows)} persistent launch block(s)")

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
        self._check_launch_blocks(task.metadata)
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

        self._executor._task_registry[task.process_id] = task
        return proc

    async def adhoc_delete(self, process_id: str) -> None:
        """Delete an ad-hoc process from DB and task registry."""
        from optio_core.store import delete_process
        await delete_process(self._config.mongo_db, self._config.prefix, process_id)
        self._executor._task_registry.pop(process_id, None)

    async def launch(self, process_id: str, resume: bool = False) -> None:
        """Fire-and-forget launch. Returns immediately, process runs in background.

        If resume is True, the task is launched with ctx.resume=True so it can
        restore previous state rather than start fresh.

        Raises LaunchBlocked if a registered launch block matches the task's metadata.
        """
        task = self._executor._task_registry.get(process_id)
        if task is not None:
            self._check_launch_blocks(task.metadata)
        asyncio.create_task(self._executor.launch_process(process_id, resume=resume))

    async def launch_and_wait(self, process_id: str, resume: bool = False) -> None:
        """Launch and wait for the process to complete. Full progress tracking.

        If resume is True, the task is launched with ctx.resume=True so it can
        restore previous state rather than start fresh.

        Raises LaunchBlocked if a registered launch block matches the task's metadata.
        """
        task = self._executor._task_registry.get(process_id)
        if task is not None:
            self._check_launch_blocks(task.metadata)
        await self._executor.launch_process(process_id, resume=resume)

    async def cancel(self, process_id: str) -> CancelOutcome:
        """Cancel a running or scheduled process. Returns a CancelOutcome
        with ok=False/reason for unknown or non-cancellable inputs."""
        proc = await self._resolve(process_id)
        if proc is None:
            return CancelOutcome(ok=False, reason="not-found")
        if not proc.get("cancellable", True) or proc["status"]["state"] not in CANCELLABLE_STATES:
            return CancelOutcome(ok=False, reason="not-cancellable")

        current_state = proc["status"]["state"]
        if current_state == "scheduled":
            now = datetime.now(timezone.utc)
            expire_at = compute_expire_at(proc.get("ttlSeconds"), now=now)
            await update_status(
                self._config.mongo_db, self._config.prefix, proc["_id"],
                ProcessStatus(state="cancelled", stopped_at=now),
                expire_at=expire_at,
            )
            return CancelOutcome(ok=True)

        await update_status(
            self._config.mongo_db, self._config.prefix, proc["_id"],
            ProcessStatus(state="cancel_requested"),
        )

        found = self._executor.request_cancel_with_deadline(
            proc["_id"],
            deadline=time.monotonic() + self._config.cancel_grace_seconds,
        )
        if found:
            await update_status(
                self._config.mongo_db, self._config.prefix, proc["_id"],
                ProcessStatus(state="cancelling"),
            )
        return CancelOutcome(ok=True)

    async def cancel_and_wait(self, process_id: str) -> str | None:
        """Cancel and wait until the process reaches a terminal state.

        Returns the terminal state ('cancelled', 'failed', 'done', ...) or
        None if the process does not exist. Raises asyncio.TimeoutError if
        the process has not reached a terminal state within
        cancel_grace_seconds + 25s — strictly a backstop against supervisor
        or DB anomalies.
        """
        proc = await get_process_by_process_id(
            self._config.mongo_db, self._config.prefix, process_id,
        )
        if proc is None:
            return None

        await self.cancel(process_id)

        ceiling = self._config.cancel_grace_seconds + 25.0
        deadline = time.monotonic() + ceiling
        while True:
            proc = await get_process_by_process_id(
                self._config.mongo_db, self._config.prefix, process_id,
            )
            if proc is None:
                return None
            state = proc["status"]["state"]
            if state not in ACTIVE_STATES:
                return state
            if time.monotonic() >= deadline:
                raise asyncio.TimeoutError(
                    f"Process {process_id} did not reach terminal state within {ceiling}s"
                )
            await asyncio.sleep(0.1)

    async def _group_cancel_issue(
        self,
        metadata_filter: ProcessMetadataFilter,
        block_new_launches: bool,
    ) -> list[str]:
        """Snapshot, cancel, optionally leak-sweep. Returns the list of
        process_ids that were cancelled (snapshot + leaked).

        Caller is responsible for the launch guard's AsyncExitStack —
        this helper assumes the guard is already active when called with
        block_new_launches=True.
        """
        # 1. Snapshot active processes matching the filter.
        procs = await self.list_processes(metadata=metadata_filter)
        active = [p for p in procs if p["status"]["state"] in ACTIVE_STATES]

        # 2. Issue cancellations in parallel. cancel() is non-blocking
        #    and idempotent.
        if active:
            await asyncio.gather(
                *(self.cancel(p["processId"]) for p in active)
            )

        pending_ids = [p["processId"] for p in active]

        # 3. Leak sweep (only with block_new_launches=True). Catches
        #    launches that passed _check_launch_blocks before the guard
        #    registered but completed their upsert after our snapshot.
        if block_new_launches:
            await asyncio.sleep(0.1)
            latest = await self.list_processes(metadata=metadata_filter)
            known = set(pending_ids)
            leaked = [
                p for p in latest
                if p["status"]["state"] in ACTIVE_STATES
                and p["processId"] not in known
            ]
            if leaked:
                await asyncio.gather(
                    *(self.cancel(p["processId"]) for p in leaked)
                )
                pending_ids.extend(p["processId"] for p in leaked)

        return pending_ids

    async def group_cancel(
        self,
        metadata_filter: ProcessMetadataFilter,
        block_new_launches: bool = False,
        *,
        persist: bool = False,
        reason: str | None = None,
    ) -> int:
        """Cancel every active process matching `metadata_filter`. Does NOT
        wait for terminal state.

        Returns the count of processes for which cancellation was issued.

        See docs/2026-04-30-group-cancel-design.md and
        docs/2026-04-30-persistent-launch-blocks-design.md (for `persist`).
        """
        if not metadata_filter:
            raise ValueError(
                "group_cancel requires a non-empty metadata_filter; "
                "use Optio.shutdown() to drain everything."
            )
        if persist and not block_new_launches:
            raise ValueError(
                "group_cancel: persist=True requires block_new_launches=True"
            )
        async with AsyncExitStack() as stack:
            if block_new_launches:
                await stack.enter_async_context(
                    self.block_launches(
                        metadata_filter, persist=persist, reason=reason,
                    )
                )
            pending_ids = await self._group_cancel_issue(metadata_filter, block_new_launches)
            return len(pending_ids)

    async def group_cancel_and_wait(
        self,
        metadata_filter: ProcessMetadataFilter,
        block_new_launches: bool = False,
        *,
        persist: bool = False,
        reason: str | None = None,
    ) -> int:
        """Cancel every active process matching `metadata_filter` and wait
        for all of them to reach a terminal state. See
        docs/2026-04-30-group-cancel-design.md and
        docs/2026-04-30-persistent-launch-blocks-design.md (for `persist`).

        Returns the count of processes for which cancellation was issued.

        Do not call from inside a task whose metadata matches the filter —
        use group_cancel for self-cancel.
        """
        if not metadata_filter:
            raise ValueError(
                "group_cancel_and_wait requires a non-empty metadata_filter; "
                "use Optio.shutdown() to drain everything."
            )
        if persist and not block_new_launches:
            raise ValueError(
                "group_cancel_and_wait: persist=True requires block_new_launches=True"
            )
        async with AsyncExitStack() as stack:
            if block_new_launches:
                await stack.enter_async_context(
                    self.block_launches(
                        metadata_filter, persist=persist, reason=reason,
                    )
                )
            pending = await self._group_cancel_issue(
                metadata_filter, block_new_launches,
            )
            if not pending:
                return 0

            ceiling = self._config.cancel_grace_seconds + 25.0
            deadline = time.monotonic() + ceiling
            i = 0
            while i < len(pending):
                proc = await self.get_process(pending[i])
                if proc is None or proc["status"]["state"] not in ACTIVE_STATES:
                    i += 1
                    continue
                if time.monotonic() >= deadline:
                    remaining = len(pending) - i
                    raise asyncio.TimeoutError(
                        f"group_cancel_and_wait: {remaining} process(es) "
                        f"did not reach a terminal state within {ceiling}s "
                        f"(filter={metadata_filter})"
                    )
                await asyncio.sleep(0.1)
            return len(pending)

    async def dismiss(self, process_id: str) -> None:
        """Dismiss a completed process (reset to idle)."""
        await self._handle_dismiss({"processId": process_id})

    async def resync(
        self,
        clean: bool = False,
        metadata_filter: ProcessMetadataFilter | None = None,
    ) -> None:
        """Re-sync task definitions from the generator.

        With no `metadata_filter`, the full task set is regenerated and stale
        records / schedules / registry entries are pruned. With a filter,
        regeneration is scoped to tasks whose `metadata` matches; out-of-scope
        state is preserved.

        `clean=True` deletes process records before re-importing. When combined
        with a filter, only in-scope records are deleted.
        """
        await self._handle_resync({"clean": clean, "metadataFilter": metadata_filter})

    async def _resolve(self, id_str: str) -> dict | None:
        """Accept an ObjectId hex string or a processId; return the matching
        Mongo process doc, or None if neither lookup matches."""
        coll = self._config.mongo_db[f"{self._config.prefix}_processes"]
        if _is_objectid(id_str):
            doc = await coll.find_one({"_id": ObjectId(id_str)})
            if doc:
                return doc
        return await coll.find_one({"processId": id_str})

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

        # Start the clamator RPC server first (non-blocking; spawns its own tasks)
        if self.rpc_server is not None and self._owned_rpc_server:
            await self.rpc_server.start()

        # Start scheduler
        await self._scheduler.start()

        # Start heartbeat (if Redis is configured)
        if self._redis:
            self._heartbeat_task = asyncio.create_task(self._heartbeat_loop())

        self._supervisor_task = asyncio.create_task(self._supervisor_loop())

        try:
            await self._shutdown_event.wait()
        finally:
            await self._scheduler.stop()

    async def shutdown(self, grace_seconds: float | None = None) -> None:
        """Graceful shutdown unified on the deadline-cancel mechanism.

        Args:
            grace_seconds: How long to wait for cooperating tasks to unwind
                after the cooperative flag + deadline are set. Defaults to
                config.cancel_grace_seconds. Tasks past their deadline are
                force-cancelled by the supervisor (or, if the supervisor has
                already stopped, by direct executor.force_cancel calls below).
        """
        logger.info("Shutdown requested")
        self._running = False

        # 1. Heartbeat
        if self._heartbeat_task:
            self._heartbeat_task.cancel()
            try:
                await self._heartbeat_task
            except asyncio.CancelledError:
                pass
            self._heartbeat_task = None

        # 2. Signal shutdown to the main run() loop
        if hasattr(self, '_shutdown_event'):
            self._shutdown_event.set()

        # 2b. RPC server (clamator)
        if self.rpc_server is not None and self._owned_rpc_server:
            grace_ms = int(
                (grace_seconds if grace_seconds is not None else self._config.cancel_grace_seconds) * 1000
            )
            await self.rpc_server.stop(grace_ms=grace_ms)

        # 3. Cancel everything via the unified mechanism.
        grace = (
            grace_seconds
            if grace_seconds is not None
            else self._config.cancel_grace_seconds
        )
        if self._executor:
            now_mono = time.monotonic()
            for oid in list(self._executor._cancellation_flags.keys()):
                entry = self._executor._cancellation_flags.get(oid)
                if entry is None:
                    continue
                entry.flag.set()
                if entry.deadline is None:
                    entry.deadline = now_mono + grace

            # Wait for entries to drain. The supervisor handles force-cancel.
            ceiling = time.monotonic() + grace + 5.0
            while self._executor._cancellation_flags and time.monotonic() < ceiling:
                await asyncio.sleep(0.1)

            # Belt and braces: anything still left, force-cancel directly.
            # (Handles the case where the supervisor was slow or already stopped.)
            for oid in list(self._executor._cancellation_flags.keys()):
                await self._executor.force_cancel(oid)

        # 4. Stop supervisor (after final force-cancel pass).
        if self._supervisor_task:
            self._supervisor_task.cancel()
            try:
                await self._supervisor_task
            except asyncio.CancelledError:
                pass
            self._supervisor_task = None

        # 5. Redis
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
        # Re-read each record for ttlSeconds so we can set expireAt for TTL eviction.
        for oid, prev_state in stale:
            ttl_doc = await coll.find_one({"_id": oid}, {"ttlSeconds": 1})
            expire_at = compute_expire_at((ttl_doc or {}).get("ttlSeconds"), now=now)
            await update_status(
                self._config.mongo_db, self._config.prefix, oid,
                ProcessStatus(state="failed", error=error_msg, failed_at=now),
                expire_at=expire_at,
            )
            await coll.update_one({"_id": oid}, {"$set": {"widgetUpstream": None}})
            await append_log(
                self._config.mongo_db, self._config.prefix, oid,
                "event", f"State reconciled: {prev_state} -> failed (server restart)",
            )
        logger.info(f"Reconciled {len(stale)} interrupted process(es) to 'failed'")

    async def _supervisor_loop(self) -> None:
        """Scan for past-deadline cancellations every 500 ms; force-cancel them."""
        while self._running:
            try:
                now = time.monotonic()
                if self._executor is not None:
                    for oid, entry in list(self._executor._cancellation_flags.items()):
                        if entry.deadline is None:
                            continue
                        if now < entry.deadline:
                            continue
                        await self._executor.force_cancel(oid)
            except Exception as e:
                logger.exception(f"Supervisor loop error: {e}")
            await asyncio.sleep(0.5)

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

    async def _cancel_stale_processes(
        self,
        stale: list[tuple[ObjectId, str, str]],
        grace_seconds: float,
    ) -> None:
        """Cooperatively cancel stale non-terminal tasks; wait until terminal or grace.

        For each (oid, process_id, state) whose state is non-terminal, set the
        cooperative cancel flag with a deadline. Then poll Mongo until either
        the record reaches a terminal state (done/failed/cancelled), the
        record disappears, or `grace_seconds` elapses. On grace timeout, log
        a warning; the caller (resync) proceeds with deletion regardless.
        """
        non_terminal = ACTIVE_STATES
        targets = [(oid, pid) for (oid, pid, st) in stale if st in non_terminal]
        if not targets:
            return

        deadline = time.monotonic() + grace_seconds
        for oid, _pid in targets:
            self._executor.request_cancel_with_deadline(oid, deadline=deadline)

        coll = self._config.mongo_db[f"{self._config.prefix}_processes"]

        async def _wait_for_terminal(oid: ObjectId) -> bool:
            while time.monotonic() < deadline:
                doc = await coll.find_one({"_id": oid}, {"status.state": 1})
                if doc is None:
                    return True  # already gone
                state = doc["status"]["state"]
                if state in ("done", "failed", "cancelled"):
                    return True
                await asyncio.sleep(0.05)
            return False

        results = await asyncio.gather(
            *[_wait_for_terminal(oid) for (oid, _pid) in targets]
        )
        timed_out = [pid for ((_oid, pid), ok) in zip(targets, results) if not ok]
        if timed_out:
            logger.warning(
                f"cancel-stale grace exceeded for processIds={timed_out!r}; "
                f"deletion will proceed regardless"
            )

    async def _sync_definitions(
        self,
        metadata_filter: ProcessMetadataFilter | None = None,
    ) -> None:
        """Run the task generator and sync with database, optionally scoped."""
        if self._config.get_task_definitions is None:
            return

        tasks = await self._config.get_task_definitions(
            self._config.services, metadata_filter,
        )

        # Framework guarantees only in-scope tasks reach downstream layers,
        # so callback authors may ignore `metadata_filter` if they prefer.
        if metadata_filter:
            tasks = [t for t in tasks if matches_filter(t.metadata, metadata_filter)]

        for task in tasks:
            await upsert_process(self._config.mongo_db, self._config.prefix, task)

        valid_ids = {t.process_id for t in tasks}

        # B1: cooperatively cancel stale non-terminal tasks before their
        # records are deleted, so running tasks don't continue writing
        # log/status updates to a record that no longer exists.
        stale = await find_stale_process_ids(
            self._config.mongo_db, self._config.prefix, valid_ids, metadata_filter,
        )
        if stale:
            await self._cancel_stale_processes(
                stale, self._config.cancel_grace_seconds,
            )

        removed = await remove_stale_processes(
            self._config.mongo_db, self._config.prefix, valid_ids, metadata_filter,
        )
        if removed:
            logger.info(f"Removed {removed} stale process records")

        self._executor.register_tasks(tasks, metadata_filter)
        await self._scheduler.sync_schedules(tasks, metadata_filter)

        scope = "(all)" if not metadata_filter else f"(filter={metadata_filter})"
        logger.info(f"Synced {len(tasks)} task definitions {scope}")

    async def _handle_launch_by_process_id(self, process_id: str, resume: bool = False) -> None:
        # Background-spawn the executor; scheduler hook (Task 8 introduces an outcome-aware adapter).
        asyncio.create_task(self._executor.launch_process(process_id, resume=resume))

    async def _handle_dismiss(self, payload: dict) -> None:
        process_id = payload.get("processId")
        if not process_id:
            return

        proc = await get_process_by_process_id(
            self._config.mongo_db, self._config.prefix, process_id,
        )
        if proc is None:
            return

        if proc["status"]["state"] not in END_STATES:
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
        metadata_filter = payload.get("metadataFilter") or None  # treat {} as None

        if clean:
            coll = self._config.mongo_db[f"{self._config.prefix}_processes"]
            if metadata_filter:
                mongo_query: dict[str, Any] = {"parentId": None}
                for k, v in metadata_filter.items():
                    mongo_query[f"metadata.{k}"] = v
                deleted = await coll.delete_many(mongo_query)
            else:
                deleted = await coll.delete_many({})
            logger.info(f"Nuked {deleted.deleted_count} process records")

        await self._sync_definitions(metadata_filter)
