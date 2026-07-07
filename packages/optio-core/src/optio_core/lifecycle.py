"""Optio lifecycle management — init, run, shutdown."""

import asyncio
import logging
import os as _os
import re as _re
import signal
import time
import uuid

# Cancel-trace instrumentation. Off by default. Enable with
# `OPTIO_CANCEL_TRACE=1` to see every state-write inside lifecycle.cancel
# (and the matching executor terminal-state writes). Used to diagnose
# cancel-propagation races; safe to leave compiled in because each log
# call is guarded by the module-level flag below.
_CANCEL_TRACE = _os.environ.get("OPTIO_CANCEL_TRACE", "0").lower() in ("1", "true", "yes")
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
    TaskInstance, OptioConfig, MongoStore, ProcessStatus, ProcessMetadataFilter,
    matches_filter, LaunchBlocked,
    LaunchOutcome, CancelOutcome, DismissOutcome,
)
from optio_core.store import (
    upsert_process, remove_stale_processes, find_stale_process_ids,
    get_process_by_process_id, update_status, clear_result_fields,
    append_log, compute_expire_at, purge_processes, set_auto_resume_scheduled,
)
from optio_core.state_machine import (
    ACTIVE_STATES, CANCELLABLE_STATES, DISMISSABLE_STATES, END_STATES,
    LAUNCHABLE_STATES,
)
from optio_core.exceptions import LaunchError
from optio_core.executor import Executor
from clamator_protocol import RpcServerCore
from clamator_over_redis import RedisRpcServer
from optio_core.scheduler import ProcessScheduler

logger = logging.getLogger("optio_core_core")


def _trace(fmt: str, *args: object) -> None:
    """Cancel-trace log helper, gated on OPTIO_CANCEL_TRACE env var."""
    if _CANCEL_TRACE:
        logger.warning(fmt, *args)


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
        self._shutting_down = False
        self._shutdown_task: "asyncio.Task | None" = None
        self._heartbeat_task: asyncio.Task | None = None
        self._supervisor_task: asyncio.Task | None = None
        self._auto_resume_task: asyncio.Task | None = None
        self._launch_blocks: dict[uuid.UUID, _BlockEntry] = {}
        # In-process upload-writer registry: process ObjectId -> writer
        # callable (filename, bytes) -> relpath. Populated by running tasks
        # via ctx.register_upload_writer; read by materialize_upload (the
        # clamator RPC handler) to resolve a writer by process_id. In-memory
        # only — the writer holds this worker's live Host.
        self._upload_writers: dict[ObjectId, Callable[[str, bytes], Awaitable[str]]] = {}

    @property
    def mongo_store(self) -> MongoStore:
        """The (db, prefix) binding this instance was initialized with.

        Named ``mongo_store`` (not ``store``) because ``optio_core.store`` is a
        submodule — a module-level ``store`` attribute would be shadowed by it.
        Hand it as-is to helpers that need db+prefix (e.g.
        ``optio_claudecode.delete_seed``) instead of re-deriving the prefix
        from your own config. Raises if accessed before ``init()``.
        """
        if self._config is None:
            raise RuntimeError("optio.mongo_store accessed before init()")
        return MongoStore(db=self._config.mongo_db, prefix=self._config.prefix)

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
        auto_resume_delay_seconds: float = 300.0,
        cancel_wait_ceiling_margin_seconds: float = 25.0,
        shutdown_drain_margin_seconds: float = 5.0,
        terminal_poll_interval_seconds: float = 0.1,
        force_cancel_shield_seconds: float = 2.0,
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
            auto_resume_delay_seconds=auto_resume_delay_seconds,
            cancel_wait_ceiling_margin_seconds=cancel_wait_ceiling_margin_seconds,
            shutdown_drain_margin_seconds=shutdown_drain_margin_seconds,
            terminal_poll_interval_seconds=terminal_poll_interval_seconds,
            force_cancel_shield_seconds=force_cancel_shield_seconds,
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
        self._executor = Executor(
            mongo_db, prefix, services, optio=self,
            notify_parent_abnormal=self.cancel,
            notify_parent_failure=self._cancel_active_children,
        )

        # Run migrations
        from optio_core.migrations import fw_migrations
        await fw_migrations.run(mongo_db, prefix=f"{prefix}_fw")

        # Load persisted launch blocks ("perma-bans"). Spec:
        # docs/2026-04-30-persistent-launch-blocks-design.md.
        await self._load_persisted_blocks()

        # Create scheduler
        self._scheduler = ProcessScheduler(
            launch_fn=self._scheduler_launch_adapter,
        )

        # Reconcile any processes left in active states by a previous session.
        # Spec: docs/2026-04-22-process-reconciliation-design.md
        await self._reconcile_interrupted_processes()

        # Run initial sync. NOTE: at this point _scheduler.start() has not
        # been called yet, so sync_schedules early-returns and no cron
        # triggers reach apscheduler. run() compensates by re-running
        # sync_definitions immediately after start(). Starting apscheduler
        # in init() instead leaks its anyio task_group across the
        # init/run scope boundary and tears down Optio.run() prematurely
        # during shutdown — verified by 13 test_deadline_cancel /
        # test_group_cancel failures.
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

    def _matches_block(self, metadata: ProcessMetadataFilter | None) -> bool:
        """Return True if `metadata` matches any registered launch block.
        Non-raising sibling of `_check_launch_blocks`. Delegates so that any
        monkeypatching of `_check_launch_blocks` in tests is honored here too."""
        try:
            self._check_launch_blocks(metadata)
        except LaunchBlocked:
            return True
        return False

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

    async def launch(
        self, process_id: str, resume: bool = False, *, session_id: str | None,
    ) -> LaunchOutcome:
        """Fire-and-forget launch. Returns LaunchOutcome with a typed reason on
        precondition failure (not-found, not-launchable, no-resume-support,
        launch-blocked); on success the executor task is scheduled in the
        background and the outcome is ok=True with `proc` populated from a
        post-schedule re-read (state will typically be 'scheduled' or
        already advanced to 'running' depending on event-loop timing)."""
        # Stop sign: once shutdown has begun, refuse new launches so nothing
        # half-boots into a shutting-down engine. Covers scheduler, RPC, and
        # programmatic launch paths.
        if self._shutting_down:
            return LaunchOutcome(ok=False, reason="shutting-down")
        proc = await self._resolve(process_id)
        if proc is None:
            return LaunchOutcome(ok=False, reason="not-found")
        if proc["status"]["state"] not in LAUNCHABLE_STATES:
            return LaunchOutcome(ok=False, reason="not-launchable")
        if resume and not proc.get("supportsResume", False):
            return LaunchOutcome(ok=False, reason="no-resume-support")
        # supportsResume is the task CAPABILITY ("this kind of task can resume");
        # hasSavedState is the per-instance REALITY ("a durable capture exists").
        # A fresh, never-captured process launched with resume=True must come up
        # fresh, not pretend to resume: there is no snapshot to restore, and the
        # task's resume-gated setup (hooks keyed on ctx.resume) would otherwise
        # misfire as if state had been restored. Downgrade rather than reject —
        # the caller legitimately wants to launch; it just has nothing to resume.
        if resume and not proc.get("hasSavedState", False):
            resume = False

        task = self._executor._task_registry.get(proc["processId"])
        if task is not None and self._matches_block(task.metadata):
            return LaunchOutcome(ok=False, reason="launch-blocked")

        oid_str = str(proc["_id"])
        asyncio.create_task(
            self._executor.launch_process(oid_str, resume=resume, session_id=session_id),
        )
        # A launch (manual or timer-driven) supersedes any pending auto-resume
        # stamp on this process — clear it so the post-boot sweep won't
        # double-launch it.
        if proc.get("autoResumeScheduled"):
            await set_auto_resume_scheduled(
                self._config.mongo_db, self._config.prefix, proc["_id"], False,
            )
        # Yield once so the executor's first state-write (idle→scheduled)
        # lands before we re-read; then return the post-launch snapshot
        # via OID (unambiguous).
        await asyncio.sleep(0)
        post = await self._resolve(oid_str)
        return LaunchOutcome(ok=True, proc=post)

    async def launch_and_wait(
        self, process_id: str, resume: bool = False, *, session_id: str | None,
    ) -> None:
        """Launch and wait for the process to complete. Full progress tracking.

        If resume is True, the task is launched with ctx.resume=True so it can
        restore previous state rather than start fresh.

        Raises LaunchBlocked if a registered launch block matches the task's metadata.
        """
        task = self._executor._task_registry.get(process_id)
        if task is not None:
            self._check_launch_blocks(task.metadata)
        await self._executor.launch_process(process_id, resume=resume, session_id=session_id)

    async def launch_and_await_result(
        self, process_id: str, resume: bool = False, *,
        session_id: str | None, timeout: float | None = None,
    ) -> Any:
        """Launch like ``launch()`` and wait for the task to call
        ``ctx.publish_result(obj)``; return that object while the task keeps
        running.

        Raises LaunchError(reason) when the launch is refused,
        ResultNotPublished when the task ends without publishing, and
        asyncio.TimeoutError on ``timeout`` expiry (the task keeps running).
        """
        # Resolve the canonical processId first: the future map is keyed by
        # processId, but callers may pass OID hex (dual-form, like launch()).
        proc = await self._resolve(process_id)
        canonical = proc["processId"] if proc is not None else process_id
        fut = self._executor.ensure_result_future(canonical)
        outcome = await self.launch(
            process_id, resume=resume, session_id=session_id,
        )
        if not outcome.ok:
            self._executor._result_futures.pop(canonical, None)
            raise LaunchError(process_id, outcome.reason or "unknown")
        if timeout is not None:
            return await asyncio.wait_for(fut, timeout)
        return await fut

    def get_published_result(self, process_id: str) -> Any | None:
        """Live published object for a running process, or None.

        Phase II attachment point: RPC/listener handlers look the live
        object up here by process_id.
        """
        return self._executor.get_published_result(process_id)

    async def materialize_upload(
        self, process_id: str, data: bytes, filename: str,
    ) -> str:
        """Resolve the in-process upload writer for ``process_id`` and invoke it.

        The writer was registered by the running task via
        ``ctx.register_upload_writer``; it writes ``data`` into the task's
        workdir (local or remote Host) and returns the workdir-relative path.
        ``process_id`` may be an OID hex or a processId — resolved the same
        way ``cancel`` does. Raises ``NoUploadWriter`` when the process is
        unknown or registered no writer (not accepting uploads / already gone).
        """
        from optio_core.exceptions import NoUploadWriter
        proc = await self._resolve(process_id)
        if proc is None:
            raise NoUploadWriter(process_id)
        writer = self._upload_writers.get(proc["_id"])
        if writer is None:
            raise NoUploadWriter(process_id)
        return await writer(filename, data)

    async def read_blob_bytes(self, file_id: ObjectId) -> bytes:
        """Read a GridFS blob's full contents by id.

        Used by the engine-service ``materialize_upload`` handler to pull the
        staged upload bytes the API streamed into GridFS. Mirrors the bucket
        binding ``ProcessContext.load_blob`` uses, but at the Optio level —
        the RPC path holds no task context.
        """
        from motor.motor_asyncio import AsyncIOMotorGridFSBucket
        bucket = AsyncIOMotorGridFSBucket(self._config.mongo_db)
        stream = await bucket.open_download_stream(file_id)
        try:
            return await stream.read()
        finally:
            stream.close()

    async def _cancel_active_children(
        self,
        parent_process_id: str,
        *,
        inherit_deadline: float | None = None,
    ) -> None:
        """Cancel the active direct children of ``parent_process_id``
        cooperatively, using a shared deadline budget. Does NOT cancel
        the parent itself.

        Honors the parent task's ``auto_cancel_children`` setting
        (assumes True if the task is not in the registry — fail-safe
        toward propagation).

        Used by:
          - ``cancel()`` for its downward-propagation step.
          - The alpha-cascade failure-breach callback (parallel_group
            failure breach, non-group run_child with survive_failure=False).

        Safe to invoke multiple times concurrently: ``cancel()`` on an
        already-cancelled / not-cancellable child is a no-op.
        """
        proc = await self._resolve(parent_process_id)
        if proc is None:
            return
        task = self._executor._task_registry.get(proc["processId"])
        auto = task.auto_cancel_children if task is not None else True
        if not auto:
            return
        effective_deadline = (
            inherit_deadline
            if inherit_deadline is not None
            else time.monotonic() + self._config.cancel_grace_seconds
        )
        from optio_core.store import list_direct_children
        children = await list_direct_children(
            self._config.mongo_db, self._config.prefix,
            proc["_id"], states=ACTIVE_STATES,
        )
        if not children:
            return
        _trace(
            "CANCEL-TRACE %s: propagating to children=%s",
            proc["processId"], [c["processId"] for c in children],
        )
        await asyncio.gather(
            *(
                self.cancel(str(c["_id"]), inherit_deadline=effective_deadline)
                for c in children
            ),
            return_exceptions=True,
        )

    async def cancel(
        self,
        process_id: str,
        *,
        inherit_deadline: float | None = None,
    ) -> CancelOutcome:
        """Cancel a running or scheduled process and propagate to direct
        active children when the process's TaskInstance has
        `auto_cancel_children=True`.

        `inherit_deadline` is for internal recursion. External callers
        omit it; a fresh deadline is computed from `cancel_grace_seconds`.
        The same monotonic deadline is threaded through the entire subtree
        so descendants share one grace budget.
        """
        proc = await self._resolve(process_id)
        if proc is None:
            _trace("CANCEL-TRACE %s: not-found", process_id)
            return CancelOutcome(ok=False, reason="not-found")
        _state_now = proc["status"]["state"]
        _inh = "inherit" if inherit_deadline is not None else "fresh"
        _trace(
            "CANCEL-TRACE %s: enter state=%s cancellable=%s (%s deadline)",
            process_id, _state_now, proc.get("cancellable", True), _inh,
        )
        if not proc.get("cancellable", True) or _state_now not in CANCELLABLE_STATES:
            _trace(
                "CANCEL-TRACE %s: early-return not-cancellable (state=%s cancellable=%s)",
                process_id, _state_now, proc.get("cancellable", True),
            )
            return CancelOutcome(ok=False, reason="not-cancellable")

        # Establish the effective deadline once for this cancel sweep.
        effective_deadline = (
            inherit_deadline
            if inherit_deadline is not None
            else time.monotonic() + self._config.cancel_grace_seconds
        )

        # Atomic conditional state-transition: only one concurrent caller
        # can flip an active process. Losers return not-cancellable.
        from optio_core.store import _collection
        coll = _collection(self._config.mongo_db, self._config.prefix)
        current_state = proc["status"]["state"]
        if current_state == "scheduled":
            now = datetime.now(timezone.utc)
            expire_at = compute_expire_at(proc.get("ttlSeconds"), now=now)
            new_status = ProcessStatus(state="cancelled", stopped_at=now).to_dict()
            set_doc: dict = {"status": new_status}
            if expire_at is not None:
                set_doc["expireAt"] = expire_at
            result = await coll.update_one(
                {"_id": proc["_id"], "status.state": "scheduled"},
                {"$set": set_doc},
            )
            _trace(
                "CANCEL-TRACE %s: scheduled→cancelled match=%d",
                process_id, result.modified_count,
            )
            if result.modified_count == 0:
                return CancelOutcome(ok=False, reason="not-cancellable")
        else:
            result = await coll.update_one(
                {"_id": proc["_id"], "status.state": {"$in": list(CANCELLABLE_STATES)}},
                {"$set": {"status": ProcessStatus(state="cancel_requested").to_dict()}},
            )
            _trace(
                "CANCEL-TRACE %s: →cancel_requested match=%d (was %s)",
                process_id, result.modified_count, current_state,
            )
            if result.modified_count == 0:
                return CancelOutcome(ok=False, reason="not-cancellable")
            found = self._executor.request_cancel_with_deadline(
                proc["_id"], deadline=effective_deadline,
            )
            _trace(
                "CANCEL-TRACE %s: request_cancel_with_deadline found=%s",
                process_id, found,
            )
            if found:
                # Conditional: only advance to 'cancelling' if the row is
                # still in 'cancel_requested'. If the executor has already
                # raced ahead to a terminal state (e.g. the running task's
                # execute_fn observed the cancel flag and finalized to
                # 'cancelled' between our W1 above and now), we must NOT
                # overwrite the terminal state with 'cancelling'.
                result = await coll.update_one(
                    {"_id": proc["_id"], "status.state": "cancel_requested"},
                    {"$set": {"status": ProcessStatus(state="cancelling").to_dict()}},
                )
                _trace(
                    "CANCEL-TRACE %s: →cancelling match=%d",
                    process_id, result.modified_count,
                )

        # Downward propagation: delegate to the shared helper. Honors
        # auto_cancel_children and shares the deadline budget.
        await self._cancel_active_children(
            str(proc["_id"]),
            inherit_deadline=effective_deadline,
        )
        _trace("CANCEL-TRACE %s: exit ok=True", process_id)
        # Re-read by OID for the post-cancel snapshot; state has advanced
        # to cancel_requested / cancelling / cancelled depending on the
        # arm taken above.
        post = await self._resolve(str(proc["_id"]))
        return CancelOutcome(ok=True, proc=post)

    async def cancel_and_wait(self, process_id: str) -> str | None:
        """Cancel and wait until the process reaches a terminal state.

        Accepts processId OR OID hex (via the widened lookup primitive).

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

        # Use the resolved OID for the cancel + poll so orphan-duplicate
        # processIds can't misroute either operation.
        oid_str = str(proc["_id"])
        await self.cancel(oid_str)

        ceiling = (
            self._config.cancel_grace_seconds
            + self._config.cancel_wait_ceiling_margin_seconds
        )
        deadline = time.monotonic() + ceiling
        while True:
            proc = await get_process_by_process_id(
                self._config.mongo_db, self._config.prefix, oid_str,
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
            await asyncio.sleep(self._config.terminal_poll_interval_seconds)

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
        #    and idempotent. Pass OID so duplicate processIds (orphans
        #    from re-registrations) can't misroute the cancel.
        if active:
            await asyncio.gather(
                *(self.cancel(str(p["_id"])) for p in active)
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
                    *(self.cancel(str(p["_id"])) for p in leaked)
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
        purge_records: bool = False,
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
            if purge_records:
                await purge_processes(
                    self._config.mongo_db, self._config.prefix, metadata_filter,
                )
            return len(pending_ids)

    async def group_cancel_and_wait(
        self,
        metadata_filter: ProcessMetadataFilter,
        block_new_launches: bool = False,
        *,
        persist: bool = False,
        reason: str | None = None,
        purge_records: bool = False,
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

            ceiling = (
            self._config.cancel_grace_seconds
            + self._config.cancel_wait_ceiling_margin_seconds
        )
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
                await asyncio.sleep(self._config.terminal_poll_interval_seconds)
            if purge_records:
                await purge_processes(
                    self._config.mongo_db, self._config.prefix, metadata_filter,
                )
            return len(pending)

    async def dismiss(self, process_id: str) -> DismissOutcome:
        """Dismiss a completed process (reset to idle). Returns DismissOutcome
        with `proc` populated from a post-dismiss re-read on success."""
        proc = await self._resolve(process_id)
        if proc is None:
            return DismissOutcome(ok=False, reason="not-found")
        if proc["status"]["state"] not in DISMISSABLE_STATES:
            return DismissOutcome(ok=False, reason="not-dismissable")

        await clear_result_fields(
            self._config.mongo_db, self._config.prefix, proc["_id"],
        )
        await update_status(
            self._config.mongo_db, self._config.prefix, proc["_id"],
            ProcessStatus(state="idle"),
        )
        post = await self._resolve(str(proc["_id"]))
        return DismissOutcome(ok=True, proc=post)

    async def resync(
        self,
        clean: bool = False,
        metadata_filter: ProcessMetadataFilter | None = None,
    ) -> dict[str, str]:
        """Re-sync task definitions from the generator.

        With no `metadata_filter`, the full task set is regenerated and stale
        records / schedules / registry entries are pruned. With a filter,
        regeneration is scoped to tasks whose `metadata` matches; out-of-scope
        state is preserved.

        `clean=True` deletes process records before re-importing. When combined
        with a filter, only in-scope records are deleted.

        Returns a `{processId: oid_hex}` map covering every task synced this
        call. Callers that want to immediately launch one of the synced
        tasks should pass `oid_hex` from this map to `launch()` rather than
        the processId — avoids the ambiguity that arises when prior runs
        left orphan docs with the same processId.
        """
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

        return await self._sync_definitions(metadata_filter)

    async def _resolve(self, id_str: str) -> dict | None:
        """Accept an ObjectId hex string or a processId; return the matching
        Mongo process doc, or None if neither lookup matches.

        ProcessId fallback returns the NEWEST doc by _id (orphan-resilient):
        when multiple docs share the same processId from prior task
        re-registrations, the live one (newest OID) wins. Callers that
        already hold a doc/OID should pass the OID hex to avoid the
        ambiguity altogether.
        """
        coll = self._config.mongo_db[f"{self._config.prefix}_processes"]
        if _is_objectid(id_str):
            doc = await coll.find_one({"_id": ObjectId(id_str)})
            if doc:
                return doc
        return await coll.find_one({"processId": id_str}, sort=[("_id", -1)])

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

            def _request_shutdown() -> None:
                # Store the task so run() can await it to completion before the
                # loop is torn down; guard against a second signal.
                if self._shutdown_task is None:
                    self._shutdown_task = asyncio.create_task(self.shutdown())

            for sig in (signal.SIGTERM, signal.SIGINT):
                loop.add_signal_handler(sig, _request_shutdown)
        except (NotImplementedError, RuntimeError):
            pass  # Signal handlers not available (e.g., in tests)

        # Start the clamator RPC server first (non-blocking; spawns its own tasks)
        if self.rpc_server is not None and self._owned_rpc_server:
            await self.rpc_server.start()

        # Start scheduler — must run in the same async context as
        # Optio.run() so apscheduler's anyio task_group lives inside the
        # run-loop's scope, not init()'s. Re-sync immediately after start
        # so cron triggers registered during init() actually reach the
        # now-live apscheduler instance (the init-time sync_schedules
        # was a no-op because apscheduler was None at that point).
        await self._scheduler.start()
        await self._sync_definitions()

        # Start heartbeat (if Redis is configured)
        if self._redis:
            self._heartbeat_task = asyncio.create_task(self._heartbeat_loop())

        self._supervisor_task = asyncio.create_task(self._supervisor_loop())
        # One-shot post-boot auto-resume timer. Fires once after
        # auto_resume_delay_seconds, re-launching stamped+saved processes.
        self._auto_resume_task = asyncio.create_task(self._auto_resume_timer())

        try:
            await self._shutdown_event.wait()
        finally:
            # A signal dispatches shutdown() as a detached task. Wait for its
            # cooperative drain to finish before returning — otherwise main()
            # returns and asyncio.run() tear-down cancels the drain mid-capture.
            # (When shutdown() is called directly rather than via signal,
            # _shutdown_task is None and the caller already awaited it.)
            if self._shutdown_task is not None:
                try:
                    await self._shutdown_task
                except Exception:
                    logger.exception("shutdown task raised")
            await self._scheduler.stop()

    async def _stamp_auto_resume_if_eligible(self, oid: ObjectId) -> None:
        """Stamp `autoResumeScheduled=True` on a process iff it is a top-level
        (depth 0) process whose task opted into auto_resume.

        Called during shutdown for every process being cancelled. The depth
        check enforces the top-level-only restriction at the point where the
        concrete process depth is known (a task definition does not know the
        depth of its future instances).
        """
        coll = self._config.mongo_db[f"{self._config.prefix}_processes"]
        doc = await coll.find_one({"_id": oid}, {"processId": 1, "depth": 1})
        if doc is None or doc.get("depth", 0) != 0:
            return
        task = self._executor._task_registry.get(doc["processId"])
        if task is None or not getattr(task, "auto_resume", False):
            return
        await set_auto_resume_scheduled(
            self._config.mongo_db, self._config.prefix, oid, True,
        )
        await append_log(
            self._config.mongo_db, self._config.prefix, oid,
            "event", "Scheduled for auto-resume after restart",
        )

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
        # Stop sign: refuse new launches (scheduler, RPC, programmatic) for the
        # entire drain so nothing half-boots into a shutting-down engine.
        self._shutting_down = True

        try:
            # 1. Heartbeat
            if self._heartbeat_task:
                self._heartbeat_task.cancel()
                try:
                    await self._heartbeat_task
                except asyncio.CancelledError:
                    pass
                self._heartbeat_task = None

            # 1b. Auto-resume one-shot timer (cancel if it hasn't fired yet).
            if self._auto_resume_task:
                self._auto_resume_task.cancel()
                try:
                    await self._auto_resume_task
                except asyncio.CancelledError:
                    pass
                self._auto_resume_task = None

            # 2. RPC server (clamator)
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
                    await self._stamp_auto_resume_if_eligible(oid)
                    entry.flag.set()
                    if entry.deadline is None:
                        entry.deadline = now_mono + grace

                # Wait for entries to drain. The supervisor handles force-cancel.
                ceiling = (
                    time.monotonic() + grace
                    + self._config.shutdown_drain_margin_seconds
                )
                while self._executor._cancellation_flags and time.monotonic() < ceiling:
                    await asyncio.sleep(self._config.terminal_poll_interval_seconds)

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
        finally:
            # Unblock run() ONLY after the drain has fully run (or failed). The
            # finally guarantees run() still unblocks if the drain raised —
            # otherwise run() would hang until the external SIGKILL backstop.
            # (Moved here from an early "step 2": setting it before the drain let
            # run() return and asyncio.run() tear-down cancel the drain.)
            if hasattr(self, '_shutdown_event'):
                self._shutdown_event.set()

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
            await coll.update_one(
                {"_id": oid},
                {"$set": {"widgetUpstream": None, "autoResumeScheduled": False}},
            )
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
                        _trace(
                            "CANCEL-TRACE supervisor: oid=%s deadline EXPIRED (now=%.3f deadline=%.3f overshoot=%.3fs); calling force_cancel",
                            oid, now, entry.deadline, now - entry.deadline,
                        )
                        await self._executor.force_cancel(oid)
            except Exception as e:
                logger.exception(f"Supervisor loop error: {e}")
            await asyncio.sleep(0.5)

    async def _auto_resume_timer(self) -> None:
        """One-shot: wait auto_resume_delay_seconds, then sweep for stamped
        processes and re-launch them. Cancelled cleanly at shutdown if it has
        not fired yet (stamps persist for the next boot's timer)."""
        delay = self._config.auto_resume_delay_seconds
        try:
            await asyncio.sleep(delay)
        except asyncio.CancelledError:
            return
        if not self._running:
            return
        try:
            await self._auto_resume_scheduled_processes()
        except Exception:
            logger.exception("auto-resume sweep failed")

    async def _auto_resume_scheduled_processes(self) -> None:
        """Re-launch every process stamped for auto-resume that genuinely saved
        its state: autoResumeScheduled=True AND state='cancelled' AND
        hasSavedState=True. Force-killed (failed) and unsaved processes are
        excluded by the query. On a blocked / non-launchable target, log, clear
        the stamp, and skip (no retry). launch() clears the stamp on success."""
        coll = self._config.mongo_db[f"{self._config.prefix}_processes"]
        cursor = coll.find(
            {
                "autoResumeScheduled": True,
                "status.state": "cancelled",
                "hasSavedState": True,
            },
            {"_id": 1, "processId": 1},
        )
        docs = [doc async for doc in cursor]
        if not docs:
            return
        logger.info(f"Auto-resuming {len(docs)} scheduled process(es)")
        for doc in docs:
            outcome = await self.launch(str(doc["_id"]), resume=True, session_id=None)
            if outcome.ok:
                logger.info(f"Auto-resumed {doc['processId']}")
            else:
                logger.warning(
                    f"Auto-resume skipped {doc['processId']}: {outcome.reason}"
                )
                await set_auto_resume_scheduled(
                    self._config.mongo_db, self._config.prefix, doc["_id"], False,
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
                await asyncio.sleep(self._config.terminal_poll_interval_seconds)
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
    ) -> dict[str, str]:
        """Run the task generator and sync with database, optionally scoped.

        Returns a `{processId: oid_hex}` map covering every task synced this
        call. Callers that immediately want to launch by OID can read the
        OID from this map instead of doing a follow-up processId-keyed
        lookup. Empty dict if no generator is configured.
        """
        if self._config.get_task_definitions is None:
            return {}

        tasks = await self._config.get_task_definitions(
            self._config.services, metadata_filter,
        )

        # Framework guarantees only in-scope tasks reach downstream layers,
        # so callback authors may ignore `metadata_filter` if they prefer.
        if metadata_filter:
            tasks = [t for t in tasks if matches_filter(t.metadata, metadata_filter)]

        # Validate auto_resume coherence: you cannot resume a task that does
        # not support resume. Catch the misconfiguration loudly at sync time
        # (engine startup) rather than silently no-op'ing at restart.
        for task in tasks:
            if task.auto_resume and not task.supports_resume:
                raise ValueError(
                    f"Task '{task.process_id}': auto_resume=True requires "
                    f"supports_resume=True"
                )

        pid_to_oid: dict[str, str] = {}
        for task in tasks:
            proc = await upsert_process(self._config.mongo_db, self._config.prefix, task)
            pid_to_oid[task.process_id] = str(proc["_id"])

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
        return pid_to_oid

    async def _scheduler_launch_adapter(self, process_id: str) -> None:
        """Scheduler hook: funnel through Optio.launch, log on failure.
        APScheduler discards the return value; the warning preserves visibility
        of skipped scheduled fires (e.g. launch blocks active at fire time)."""
        outcome = await self.launch(process_id, session_id=None)
        if not outcome.ok:
            logger.warning(
                f"Scheduled launch of {process_id} skipped: {outcome.reason}"
            )

