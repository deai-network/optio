"""Deferred migrations: run after the initial task-definition sync.

Parallel to `optio_core.migrations.fw_migrations` (which runs before the sync,
against the bare database), a *deferred* migration runs once
`Lifecycle.init` has synced task definitions, so it can resolve a process id
to its `TaskInstance` — e.g. to reach a cipher held on `TaskInstance.session_crypto`.
Startup blocks until the registry finishes, so nothing can launch or resume a
process while a deferred migration is rewriting its records.

Same register/run-once semantics as `fw_migrations`, built on the same
quaestor machinery, but tracked in its own collection
(`{prefix}_fw_deferred_migrations`) so the two registries never collide or
re-run each other's entries.

A migration that raises fails engine start, as a failing `fw_migrations`
entry does, and is retried on the next start — migrations must be
idempotent per item.

Spec: docs/2026-10-11-conversation-buffer-encryption-design.md §5.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Awaitable, Callable

from motor.motor_asyncio import AsyncIOMotorDatabase
from quaestor.registry import MigrationDefinition
from quaestor.runner import run_migrations

if TYPE_CHECKING:
    from optio_core.models import TaskInstance


@dataclass
class DeferredMigrationContext:
    """Passed to every deferred migration function.

    db: the motor database.
    prefix: the app's collection-name prefix (the plain one, e.g. "myapp" —
        not the "{prefix}_fw_deferred" tracking namespace).
    task_for: resolves a process id to its current `TaskInstance`, or
        `None` when no task definition exists for it. Mirrors how
        `Lifecycle.resurrect` resolves a process's task, via the executor's
        task registry.
    """
    db: AsyncIOMotorDatabase
    prefix: str
    task_for: Callable[[str], "TaskInstance | None"]


DeferredMigrationFn = Callable[[DeferredMigrationContext], Awaitable[None]]


class DeferredMigrationRegistry:
    """Collects deferred migration functions and runs them once, after the
    initial task-definition sync.

    Usage (package import time, same as `fw_migrations`):

        from optio_core import deferred_migrations

        @deferred_migrations.register("my_migration", depends_on=["other"])
        async def my_migration(ctx: DeferredMigrationContext) -> None:
            ...
    """

    def __init__(self) -> None:
        self._fns: dict[str, DeferredMigrationFn] = {}
        self._depends_on: dict[str, list[str]] = {}

    def register(
        self,
        name: str,
        depends_on: list[str] | None = None,
    ) -> Callable[[DeferredMigrationFn], DeferredMigrationFn]:
        """Decorator registering `async def fn(ctx: DeferredMigrationContext) -> None`."""
        def decorator(fn: DeferredMigrationFn) -> DeferredMigrationFn:
            if name in self._fns:
                raise ValueError(f"Deferred migration '{name}' is already registered")
            self._fns[name] = fn
            self._depends_on[name] = depends_on or []
            return fn
        return decorator

    def unregister(self, name: str) -> None:
        """Drop a registration. Test-only: lets a test register on the
        module-global registry and undo it in teardown."""
        self._fns.pop(name, None)
        self._depends_on.pop(name, None)

    async def run(
        self,
        db: AsyncIOMotorDatabase,
        prefix: str,
        task_for: Callable[[str], "TaskInstance | None"],
    ) -> list[str]:
        """Run every unapplied deferred migration, in dependency order.

        Each registered fn is invoked with a single `DeferredMigrationContext`
        shared across this run. Applied migrations are recorded in
        `{prefix}_fw_deferred_migrations` (quaestor's `run_migrations`,
        given tracking prefix `f"{prefix}_fw_deferred"`); a second `run()`
        does not re-invoke an already-applied fn. A raising fn propagates
        the exception and is not recorded, so it is retried on the next run.
        """
        ctx = DeferredMigrationContext(db=db, prefix=prefix, task_for=task_for)
        defs = {
            name: MigrationDefinition(
                name=name,
                execute=lambda _db, _fn=fn: _fn(ctx),
                depends_on=self._depends_on[name],
            )
            for name, fn in self._fns.items()
        }
        return await run_migrations(db, f"{prefix}_fw_deferred", defs)


deferred_migrations = DeferredMigrationRegistry()
