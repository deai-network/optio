# Engine RPC migration — phase 2 implementation plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Stand up clamator RPC end-to-end with co-existence — engine hosts a `RedisRpcServer` exposing all 8 engine methods; API constructs an `EngineCache` of `EngineClient` instances; HTTP behavior is unchanged (still goes through legacy `${prefix}:commands` redis stream).

**Architecture:** Phase 2 introduces `EngineService` (Python, in `optio-core`) implementing the codegenned ABC, hooks it into `Optio.init/run/shutdown` alongside the legacy `CommandConsumer`, and exposes `optio_core.rpc_server` via PEP 562 module `__getattr__`. On the API side it adds `engine-cache.ts` (framework-agnostic factory), wires it through all four adapters, changes their return shape to `{ engine | getEngine, closeAll }`, and re-exports `EngineClient` + `createEngineCache`. Final piece is an interop substrate at `packages/optio-demo/interop/` driven by `run-interop.sh` plus a `make test-interop` target body. HTTP handlers continue to call `publishLaunch / publishCancel / publishDismiss / publishResync` — phase 2 changes nothing about HTTP request behavior.

**Tech Stack:** Python (motor, redis.asyncio, pydantic 2, pytest, pytest-asyncio), `clamator-protocol` 0.1.5, `clamator-over-redis` 0.1.5, TypeScript (pnpm workspace, vitest, ioredis, @ts-rest/core), `@clamator/protocol` 0.1.5, `@clamator/over-redis` 0.1.5, GNU make, bash, docker compose.

**Working dir:** `/home/csillag/deai/optio` on a fresh feature branch `csillag/rpc-migration-phase-2` created off current `csillag/redis-migration-2` (which carries phase-1 work). Do **not** rebase onto `main` until phase 1 is merged.

**Reference docs:**
- `docs/2026-05-08-engine-rpc-migration-design.md` — parent spec.
- `docs/2026-05-08-engine-rpc-migration-phase-2-design.md` — phase-2 decisions and commit sequence.
- `docs/2026-05-08-engine-rpc-migration-phase-1-plan.md` — phase-1 plan (style reference; plan format).

---

## File map

**Created (Python)**
- `packages/optio-core/src/optio_core/_engine_service.py` — `EngineService` subclass of codegenned ABC; implements all 8 methods; calls public `Optio` API; never writes Mongo directly.
- `packages/optio-core/tests/test_engine_service.py` — per-method success + failure-reason coverage; idempotency-on-redelivery; mutual-exclusivity validation in `init`.

**Created (TypeScript)**
- `packages/optio-api/src/engine-cache.ts` — `createEngineCache(redis): EngineCache` factory.
- `packages/optio-api/src/__tests__/engine-cache.test.ts` — cache reuse + idempotent close.
- `packages/optio-demo/interop/package.json` — private workspace package (`optio-demo-interop`).
- `packages/optio-demo/interop/tsconfig.json` — extends repo base.
- `packages/optio-demo/interop/run.ts` — interop scenarios entrypoint.

**Created (shell)**
- `packages/optio-demo/run-interop.sh` — orchestrator (docker, engine subprocess, node runner, teardown).

**Modified (Python)**
- `packages/optio-core/src/optio_core/lifecycle.py` — `init()` accepts `rpc_server` param; constructs `RedisRpcServer` when `redis_url` set; registers `EngineService`; lifecycle hooks in `run()` and `shutdown()`.
- `packages/optio-core/src/optio_core/__init__.py` — module-level PEP 562 `__getattr__` exposing `rpc_server`.

**Modified (TypeScript)**
- `packages/optio-api/src/adapters/fastify.ts` — wire `createEngineCache`; `app.addHook('onClose', cache.closeAll)`; new return shape.
- `packages/optio-api/src/adapters/express.ts` — wire `createEngineCache`; new return shape (caller wires `closeAll`).
- `packages/optio-api/src/adapters/nextjs-app.ts` — wire `createEngineCache`; new return shape.
- `packages/optio-api/src/adapters/nextjs-pages.ts` — wire `createEngineCache`; new return shape.
- `packages/optio-api/src/index.ts` — re-export `EngineClient`, `createEngineCache`, type `EngineCache`.
- `packages/optio-api/src/adapters/__tests__/fastify.test.ts` — return-shape assertions; cache reuse; `closeAll` idempotency.
- `packages/optio-api/src/adapters/__tests__/express.test.ts` — same.
- `packages/optio-api/src/adapters/__tests__/nextjs-app.test.ts` — same.
- `packages/optio-api/src/adapters/__tests__/nextjs-pages.test.ts` — same.

**Modified (build / config)**
- `Makefile` (root) — fill in `test-interop` target body.
- `pnpm-workspace.yaml` (root) — add `packages/optio-demo/interop` glob (only if not already covered by an existing glob).

**Modified (docs)**
- `packages/optio-core/README.md` — new "RPC service" section.
- `packages/optio-core/AGENTS.md` — `rpc_server` attribute, new `init()` parameter.
- `packages/optio-api/README.md` — `OptioApiOptions` doc, return shape, `EngineClient` sharing example.
- `packages/optio-api/AGENTS.md` — return shape, new exports, `engine-cache.ts` in custom-adapter section.
- `AGENTS.md` (root) — `optio_core.rpc_server` in Python API table; return-shape note.
- `docs/2026-05-08-engine-rpc-migration-design.md` — rename `_command_consumer.py` → `consumer.py` in §4 and §8 phase 5.

---

## Pre-flight (one-time, before Task 1)

- [ ] **Step 1: Create the phase-2 feature branch off the current branch**

```bash
cd /home/csillag/deai/optio
git status                                # confirm clean working tree
git rev-parse --abbrev-ref HEAD           # expected: csillag/redis-migration-2
git checkout -b csillag/rpc-migration-phase-2
```

- [ ] **Step 2: Confirm phase-1 codegen output is present**

```bash
ls packages/optio-core/src/optio_core/_generated/engine.py
ls packages/optio-api/src/_generated/engine.ts
```
Both files must exist (committed in phase 1).

- [ ] **Step 3: Confirm clamator runtime deps are installed**

```bash
cd packages/optio-core && pip install -e . && cd -
pnpm install
```
Phase 1 added `clamator-protocol`/`clamator-over-redis` to `optio-core/pyproject.toml` and `@clamator/protocol`/`@clamator/over-redis` to `optio-api/package.json`. The `pip install -e .` re-installs the editable optio-core to pick up clamator deps in case the worktree drifted (per memory: editable installs are global; reinstall before pytest).

- [ ] **Step 4: Smoke-check the codegen output**

Run: `python -c "from optio_core._generated.engine import EngineService, LaunchParams; p = LaunchParams.model_validate({'processId': 'x'}); print(p.process_id)"`
Expected: prints `x`.

This confirms Pydantic field aliasing works (verification gate Q12 from spec).

---

## Task 1: Engine — `EngineService` impl + lifecycle integration + module attribute (single commit)

**Files:**
- Create: `packages/optio-core/src/optio_core/_engine_service.py`
- Create: `packages/optio-core/tests/test_engine_service.py`
- Modify: `packages/optio-core/src/optio_core/lifecycle.py`
- Modify: `packages/optio-core/src/optio_core/__init__.py`

This is a large task (~14 sub-steps) but produces one logical commit. Internal TDD cycles run tests as they go; the commit step is the very last action.

- [ ] **Step 1: Write the failing test for `EngineService.launch` success path**

Create `packages/optio-core/tests/test_engine_service.py` (new file). For now include only the launch success test:

```python
"""Tests for EngineService — phase 2 of engine RPC migration."""

import pytest
from unittest.mock import AsyncMock, MagicMock
from bson import ObjectId

from optio_core._generated.engine import (
    LaunchParams, LaunchResult,
    CancelParams, CancelResult,
    DismissParams, DismissResult,
    GroupCancelParams, GroupCancelResult,
    GroupCancelAndWaitParams, GroupCancelAndWaitResult,
    BlockLaunchesParams, BlockLaunchesResult,
    UnblockLaunchesParams, UnblockLaunchesResult,
    ResyncParams,
)


@pytest.fixture
def sample_idle_proc():
    """Return a sample idle process doc as Mongo would store it."""
    return {
        "_id": ObjectId(),
        "processId": "p1",
        "name": "Test process",
        "supportsResume": False,
        "cancellable": True,
        "status": {"state": "idle", "updatedAt": "2026-05-08T00:00:00Z"},
        "metadata": {"tag": "demo"},
    }


@pytest.fixture
def sample_running_proc(sample_idle_proc):
    proc = dict(sample_idle_proc)
    proc["status"] = {"state": "running", "updatedAt": "2026-05-08T00:00:00Z"}
    return proc


@pytest.fixture
def fake_optio(sample_idle_proc):
    """A MagicMock Optio with the methods EngineService calls."""
    optio = MagicMock()
    optio._config = MagicMock()
    coll = AsyncMock()
    db = MagicMock()
    db.__getitem__.return_value = coll
    optio._config.mongo_db = db
    optio._config.prefix = "test"

    # Default: collection returns the idle proc for any find_one.
    coll.find_one = AsyncMock(return_value=sample_idle_proc)

    optio.launch = AsyncMock(return_value=None)
    optio.cancel = AsyncMock(return_value=None)
    optio.dismiss = AsyncMock(return_value=None)
    optio.resync = AsyncMock(return_value=None)
    optio.group_cancel = AsyncMock(return_value=3)
    optio.group_cancel_and_wait = AsyncMock(return_value=2)
    optio.unblock_launches = AsyncMock(return_value=1)
    optio._load_persisted_blocks = AsyncMock()
    optio.collection = coll
    return optio


@pytest.mark.asyncio
async def test_launch_success(fake_optio, sample_idle_proc):
    """launch on an idle process returns ok=True with the post-mutation doc."""
    from optio_core._engine_service import EngineService

    # Sequence: first find_one returns idle, second (post-launch) returns running.
    running = dict(sample_idle_proc)
    running["status"] = {"state": "scheduled", "updatedAt": "2026-05-08T00:01:00Z"}
    fake_optio.collection.find_one = AsyncMock(side_effect=[sample_idle_proc, running])

    svc = EngineService(fake_optio)
    result = await svc.launch(LaunchParams(process_id="p1"))

    assert isinstance(result, LaunchResult)
    assert result.root.ok is True
    assert result.root.process.process_id == "p1"
    fake_optio.launch.assert_awaited_once_with("p1", resume=False)
```

- [ ] **Step 2: Run the test to confirm RED**

```bash
cd /home/csillag/deai/optio/packages/optio-core
pytest tests/test_engine_service.py::test_launch_success -v
```
Expected: FAIL with `ModuleNotFoundError: No module named 'optio_core._engine_service'`.

- [ ] **Step 3: Create `_engine_service.py` skeleton with `launch` only**

Create `packages/optio-core/src/optio_core/_engine_service.py`:

```python
"""EngineService — clamator RPC implementation for the optio engine.

Phase 2 of the engine-RPC migration. Co-exists with the legacy
${prefix}:commands stream consumer; HTTP handlers still route through
the legacy stream until phase 3.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from bson import ObjectId

from optio_core._generated.engine import (
    EngineService as EngineServiceBase,
    LaunchParams, LaunchResult,
    CancelParams, CancelResult,
    DismissParams, DismissResult,
    GroupCancelParams, GroupCancelResult,
    GroupCancelAndWaitParams, GroupCancelAndWaitResult,
    BlockLaunchesParams, BlockLaunchesResult,
    UnblockLaunchesParams, UnblockLaunchesResult,
    ResyncParams,
)
from optio_core.models import LaunchBlocked

if TYPE_CHECKING:
    from optio_core.lifecycle import Optio


# State allowlists from packages/optio-api/src/handlers.ts. Mirrored here so
# the engine — not the API — owns the rule (parent spec authority statement).
LAUNCHABLE_STATES = {"idle", "done", "failed", "cancelled"}
CANCELLABLE_STATES = {"scheduled", "running", "cancel_requested"}
DISMISSABLE_STATES = {"done", "failed", "cancelled"}

_OBJECTID_RE = __import__("re").compile(r"^[a-fA-F0-9]{24}$")


def _is_objectid(s: str) -> bool:
    return bool(_OBJECTID_RE.match(s))


def _to_process_dict(doc: dict) -> dict:
    """Render a Mongo process doc as the wire-shape Process payload.

    Returns a dict that LaunchResult1.process / CancelResult1.process /
    DismissResult1.process etc. can validate. Generated Process model uses
    by-alias field names (e.g. _id, processId, supportsResume), so we just
    pass the doc through after stringifying the ObjectId.
    """
    out = dict(doc)
    if "_id" in out and isinstance(out["_id"], ObjectId):
        out["_id"] = str(out["_id"])
    return out


class EngineService(EngineServiceBase):
    """Concrete EngineService backing the clamator engine contract."""

    def __init__(self, optio: "Optio") -> None:
        self._optio = optio

    # --------------------------------------------------------------- launch
    async def launch(self, params: LaunchParams) -> LaunchResult:
        proc = await self._resolve(params.process_id)
        if proc is None:
            return LaunchResult.model_validate({"ok": False, "reason": "not-found"})
        if proc["status"]["state"] not in LAUNCHABLE_STATES:
            return LaunchResult.model_validate({"ok": False, "reason": "not-launchable"})
        if params.resume and not proc.get("supportsResume", False):
            return LaunchResult.model_validate({"ok": False, "reason": "no-resume-support"})

        try:
            await self._optio.launch(proc["processId"], resume=bool(params.resume))
        except LaunchBlocked:
            return LaunchResult.model_validate({"ok": False, "reason": "launch-blocked"})

        updated = await self._resolve(proc["processId"])
        return LaunchResult.model_validate({"ok": True, "process": _to_process_dict(updated)})

    # ------------------------------------------------------------- internals
    async def _resolve(self, id_str: str) -> dict | None:
        """Accept ObjectId hex or processId string; return the doc or None."""
        coll = self._optio._config.mongo_db[
            f"{self._optio._config.prefix}_processes"
        ]
        if _is_objectid(id_str):
            doc = await coll.find_one({"_id": ObjectId(id_str)})
            if doc:
                return doc
        return await coll.find_one({"processId": id_str})

    # ------------------------------------- placeholders for the other 7 methods
    async def cancel(self, params: CancelParams) -> CancelResult:
        raise NotImplementedError

    async def dismiss(self, params: DismissParams) -> DismissResult:
        raise NotImplementedError

    async def resync(self, params: ResyncParams) -> None:
        raise NotImplementedError

    async def group_cancel(self, params: GroupCancelParams) -> GroupCancelResult:
        raise NotImplementedError

    async def group_cancel_and_wait(
        self, params: GroupCancelAndWaitParams
    ) -> GroupCancelAndWaitResult:
        raise NotImplementedError

    async def block_launches(self, params: BlockLaunchesParams) -> BlockLaunchesResult:
        raise NotImplementedError

    async def unblock_launches(
        self, params: UnblockLaunchesParams
    ) -> UnblockLaunchesResult:
        raise NotImplementedError
```

The `_resolve` helper uses MagicMock-friendly access (`db[key]`) so the test fixture's mock chain works. The fixture has `db.__getitem__.return_value = coll`.

- [ ] **Step 4: Run the test — confirm GREEN**

```bash
cd /home/csillag/deai/optio/packages/optio-core
pytest tests/test_engine_service.py::test_launch_success -v
```
Expected: PASS.

- [ ] **Step 5: Add launch failure-path tests**

Append to `packages/optio-core/tests/test_engine_service.py`:

```python
@pytest.mark.asyncio
async def test_launch_not_found(fake_optio):
    from optio_core._engine_service import EngineService
    fake_optio.collection.find_one = AsyncMock(return_value=None)
    svc = EngineService(fake_optio)
    result = await svc.launch(LaunchParams(process_id="missing"))
    assert result.root.ok is False
    assert result.root.reason == "not-found"
    fake_optio.launch.assert_not_awaited()


@pytest.mark.asyncio
async def test_launch_not_launchable(fake_optio, sample_running_proc):
    from optio_core._engine_service import EngineService
    fake_optio.collection.find_one = AsyncMock(return_value=sample_running_proc)
    svc = EngineService(fake_optio)
    result = await svc.launch(LaunchParams(process_id="p1"))
    assert result.root.ok is False
    assert result.root.reason == "not-launchable"
    fake_optio.launch.assert_not_awaited()


@pytest.mark.asyncio
async def test_launch_no_resume_support(fake_optio, sample_idle_proc):
    from optio_core._engine_service import EngineService
    sample_idle_proc["supportsResume"] = False
    fake_optio.collection.find_one = AsyncMock(return_value=sample_idle_proc)
    svc = EngineService(fake_optio)
    result = await svc.launch(LaunchParams(process_id="p1", resume=True))
    assert result.root.ok is False
    assert result.root.reason == "no-resume-support"


@pytest.mark.asyncio
async def test_launch_blocked(fake_optio, sample_idle_proc):
    from optio_core._engine_service import EngineService
    fake_optio.collection.find_one = AsyncMock(return_value=sample_idle_proc)
    fake_optio.launch = AsyncMock(side_effect=LaunchBlocked("blocked", reason="x"))
    svc = EngineService(fake_optio)
    result = await svc.launch(LaunchParams(process_id="p1"))
    assert result.root.ok is False
    assert result.root.reason == "launch-blocked"
```

Run: `pytest tests/test_engine_service.py -v`
Expected: all 4 launch tests PASS.

- [ ] **Step 6: Implement `cancel` + tests**

Replace the `cancel` placeholder in `_engine_service.py` with:

```python
async def cancel(self, params: CancelParams) -> CancelResult:
    proc = await self._resolve(params.process_id)
    if proc is None:
        return CancelResult.model_validate({"ok": False, "reason": "not-found"})
    if not proc.get("cancellable", True) or proc["status"]["state"] not in CANCELLABLE_STATES:
        return CancelResult.model_validate({"ok": False, "reason": "not-cancellable"})

    await self._optio.cancel(proc["processId"])

    updated = await self._resolve(proc["processId"])
    return CancelResult.model_validate({"ok": True, "process": _to_process_dict(updated)})
```

Append tests to `test_engine_service.py`:

```python
@pytest.mark.asyncio
async def test_cancel_success(fake_optio, sample_running_proc):
    from optio_core._engine_service import EngineService
    cancelled = dict(sample_running_proc)
    cancelled["status"] = {"state": "cancelled", "updatedAt": "2026-05-08T00:02:00Z"}
    fake_optio.collection.find_one = AsyncMock(side_effect=[sample_running_proc, cancelled])
    svc = EngineService(fake_optio)
    result = await svc.cancel(CancelParams(process_id="p1"))
    assert result.root.ok is True
    assert result.root.process.status.state == "cancelled"


@pytest.mark.asyncio
async def test_cancel_not_found(fake_optio):
    from optio_core._engine_service import EngineService
    fake_optio.collection.find_one = AsyncMock(return_value=None)
    svc = EngineService(fake_optio)
    result = await svc.cancel(CancelParams(process_id="missing"))
    assert result.root.ok is False
    assert result.root.reason == "not-found"


@pytest.mark.asyncio
async def test_cancel_not_cancellable_by_state(fake_optio, sample_idle_proc):
    from optio_core._engine_service import EngineService
    fake_optio.collection.find_one = AsyncMock(return_value=sample_idle_proc)
    svc = EngineService(fake_optio)
    result = await svc.cancel(CancelParams(process_id="p1"))
    assert result.root.ok is False
    assert result.root.reason == "not-cancellable"


@pytest.mark.asyncio
async def test_cancel_not_cancellable_by_flag(fake_optio, sample_running_proc):
    from optio_core._engine_service import EngineService
    sample_running_proc["cancellable"] = False
    fake_optio.collection.find_one = AsyncMock(return_value=sample_running_proc)
    svc = EngineService(fake_optio)
    result = await svc.cancel(CancelParams(process_id="p1"))
    assert result.root.ok is False
    assert result.root.reason == "not-cancellable"
```

Run: `pytest tests/test_engine_service.py -v` — all PASS.

- [ ] **Step 7: Implement `dismiss` + tests**

Replace `dismiss` placeholder:

```python
async def dismiss(self, params: DismissParams) -> DismissResult:
    proc = await self._resolve(params.process_id)
    if proc is None:
        return DismissResult.model_validate({"ok": False, "reason": "not-found"})
    if proc["status"]["state"] not in DISMISSABLE_STATES:
        return DismissResult.model_validate({"ok": False, "reason": "not-dismissable"})

    await self._optio.dismiss(proc["processId"])

    updated = await self._resolve(proc["processId"])
    return DismissResult.model_validate({"ok": True, "process": _to_process_dict(updated)})
```

Append tests:

```python
@pytest.mark.asyncio
async def test_dismiss_success(fake_optio, sample_idle_proc):
    from optio_core._engine_service import EngineService
    sample_idle_proc["status"] = {"state": "done", "updatedAt": "2026-05-08T00:00:00Z"}
    after = dict(sample_idle_proc)
    after["status"] = {"state": "idle", "updatedAt": "2026-05-08T00:03:00Z"}
    fake_optio.collection.find_one = AsyncMock(side_effect=[sample_idle_proc, after])
    svc = EngineService(fake_optio)
    result = await svc.dismiss(DismissParams(process_id="p1"))
    assert result.root.ok is True


@pytest.mark.asyncio
async def test_dismiss_not_found(fake_optio):
    from optio_core._engine_service import EngineService
    fake_optio.collection.find_one = AsyncMock(return_value=None)
    svc = EngineService(fake_optio)
    result = await svc.dismiss(DismissParams(process_id="missing"))
    assert result.root.ok is False
    assert result.root.reason == "not-found"


@pytest.mark.asyncio
async def test_dismiss_not_dismissable(fake_optio, sample_running_proc):
    from optio_core._engine_service import EngineService
    fake_optio.collection.find_one = AsyncMock(return_value=sample_running_proc)
    svc = EngineService(fake_optio)
    result = await svc.dismiss(DismissParams(process_id="p1"))
    assert result.root.ok is False
    assert result.root.reason == "not-dismissable"
```

Run: all PASS.

- [ ] **Step 8: Implement `resync` + test**

Replace `resync` placeholder:

```python
async def resync(self, params: ResyncParams) -> None:
    await self._optio.resync(
        clean=bool(params.clean),
        metadata_filter=params.metadata_filter,
    )
```

Append:

```python
@pytest.mark.asyncio
async def test_resync_default(fake_optio):
    from optio_core._engine_service import EngineService
    svc = EngineService(fake_optio)
    result = await svc.resync(ResyncParams())
    assert result is None
    fake_optio.resync.assert_awaited_once_with(clean=False, metadata_filter=None)


@pytest.mark.asyncio
async def test_resync_with_filter(fake_optio):
    from optio_core._engine_service import EngineService
    svc = EngineService(fake_optio)
    result = await svc.resync(ResyncParams.model_validate(
        {"clean": True, "metadataFilter": {"tag": "demo"}}
    ))
    assert result is None
    fake_optio.resync.assert_awaited_once_with(clean=True, metadata_filter={"tag": "demo"})
```

Run: PASS.

- [ ] **Step 9: Implement `group_cancel` + `group_cancel_and_wait` + tests**

Replace placeholders:

```python
async def group_cancel(self, params: GroupCancelParams) -> GroupCancelResult:
    if params.persist and not params.block_new_launches:
        return GroupCancelResult.model_validate(
            {"ok": False, "reason": "invalid-persist-without-block"}
        )
    count = await self._optio.group_cancel(
        metadata_filter=params.metadata_filter,
        block_new_launches=bool(params.block_new_launches),
        persist=bool(params.persist),
        reason=params.reason,
    )
    return GroupCancelResult.model_validate({"ok": True, "cancelledCount": count})


async def group_cancel_and_wait(
    self, params: GroupCancelAndWaitParams
) -> GroupCancelAndWaitResult:
    if params.persist and not params.block_new_launches:
        return GroupCancelAndWaitResult.model_validate(
            {"ok": False, "reason": "invalid-persist-without-block"}
        )
    count = await self._optio.group_cancel_and_wait(
        metadata_filter=params.metadata_filter,
        block_new_launches=bool(params.block_new_launches),
        persist=bool(params.persist),
        reason=params.reason,
    )
    return GroupCancelAndWaitResult.model_validate({"ok": True, "cancelledCount": count})
```

Note the keyword args (`block_new_launches=`, etc.) match `Optio.group_cancel` / `Optio.group_cancel_and_wait` in `lifecycle.py`. If those signatures don't match, fix the call site here — the engine's existing public API is the authority.

Append tests:

```python
@pytest.mark.asyncio
async def test_group_cancel_success(fake_optio):
    from optio_core._engine_service import EngineService
    svc = EngineService(fake_optio)
    result = await svc.group_cancel(GroupCancelParams.model_validate(
        {"metadataFilter": {"tag": "demo"}}
    ))
    assert result.root.ok is True
    assert result.root.cancelled_count == 3


@pytest.mark.asyncio
async def test_group_cancel_invalid_persist(fake_optio):
    from optio_core._engine_service import EngineService
    svc = EngineService(fake_optio)
    result = await svc.group_cancel(GroupCancelParams.model_validate(
        {"metadataFilter": {"tag": "demo"}, "persist": True}
    ))
    assert result.root.ok is False
    assert result.root.reason == "invalid-persist-without-block"
    fake_optio.group_cancel.assert_not_awaited()


@pytest.mark.asyncio
async def test_group_cancel_and_wait_success(fake_optio):
    from optio_core._engine_service import EngineService
    svc = EngineService(fake_optio)
    result = await svc.group_cancel_and_wait(GroupCancelAndWaitParams.model_validate(
        {"metadataFilter": {"tag": "demo"}}
    ))
    assert result.root.ok is True
    assert result.root.cancelled_count == 2


@pytest.mark.asyncio
async def test_group_cancel_and_wait_invalid_persist(fake_optio):
    from optio_core._engine_service import EngineService
    svc = EngineService(fake_optio)
    result = await svc.group_cancel_and_wait(GroupCancelAndWaitParams.model_validate(
        {"metadataFilter": {"tag": "demo"}, "persist": True}
    ))
    assert result.root.ok is False
    assert result.root.reason == "invalid-persist-without-block"
```

Run: all PASS.

- [ ] **Step 10: Implement `block_launches` + `unblock_launches` + tests**

Replace placeholders:

```python
async def block_launches(self, params: BlockLaunchesParams) -> BlockLaunchesResult:
    from optio_core import _launch_block_store as _lb_store
    coll = _lb_store.collection(
        self._optio._config.mongo_db,
        self._optio._config.prefix,
    )
    await _lb_store.upsert_block(coll, params.launch_filter, params.reason)
    await self._optio._load_persisted_blocks()
    return BlockLaunchesResult.model_validate({"ok": True})


async def unblock_launches(
    self, params: UnblockLaunchesParams
) -> UnblockLaunchesResult:
    removed = await self._optio.unblock_launches(params.launch_filter)
    return UnblockLaunchesResult(removed=removed)
```

Append tests:

```python
@pytest.mark.asyncio
async def test_block_launches_success(fake_optio, monkeypatch):
    from optio_core._engine_service import EngineService

    fake_coll = AsyncMock()

    def fake_collection(db, prefix):
        return fake_coll

    upsert_called = AsyncMock()

    import optio_core._launch_block_store as lb_store
    monkeypatch.setattr(lb_store, "collection", fake_collection)
    monkeypatch.setattr(lb_store, "upsert_block", upsert_called)

    svc = EngineService(fake_optio)
    result = await svc.block_launches(BlockLaunchesParams.model_validate(
        {"launchFilter": {"tag": "demo"}, "reason": "x"}
    ))
    assert result.root.ok is True
    upsert_called.assert_awaited_once_with(fake_coll, {"tag": "demo"}, "x")
    fake_optio._load_persisted_blocks.assert_awaited()


@pytest.mark.asyncio
async def test_unblock_launches_returns_count(fake_optio):
    from optio_core._engine_service import EngineService
    svc = EngineService(fake_optio)
    result = await svc.unblock_launches(
        UnblockLaunchesParams.model_validate({"launchFilter": {"tag": "demo"}})
    )
    assert result.removed == 1
```

Run: PASS.

- [ ] **Step 11: Add idempotency-on-redelivery test**

Append:

```python
@pytest.mark.asyncio
async def test_launch_redelivery_returns_not_launchable(fake_optio, sample_idle_proc):
    """First launch transitions idle→scheduled; redelivered launch sees scheduled and returns not-launchable."""
    from optio_core._engine_service import EngineService

    after = dict(sample_idle_proc)
    after["status"] = {"state": "scheduled", "updatedAt": "2026-05-08T00:01:00Z"}

    # Two find_one's per call (pre-check + post-mutation re-read).
    # First call: idle, scheduled. Second call: scheduled (redelivery sees the scheduled doc).
    fake_optio.collection.find_one = AsyncMock(side_effect=[
        sample_idle_proc, after,  # first call
        after,                    # second call's pre-check sees scheduled
    ])

    svc = EngineService(fake_optio)
    first = await svc.launch(LaunchParams(process_id="p1"))
    second = await svc.launch(LaunchParams(process_id="p1"))

    assert first.root.ok is True
    assert second.root.ok is False
    assert second.root.reason == "not-launchable"
```

Run: PASS.

- [ ] **Step 12: Modify `lifecycle.py` — `init()` signature + mutual-exclusivity + RpcServer construction**

Edit `packages/optio-core/src/optio_core/lifecycle.py`. Find the existing `init` signature (around line 60):

```python
    async def init(
        self,
        mongo_db: AsyncIOMotorDatabase,
        prefix: str = "optio",
        redis_url: str | None = None,
        services: dict[str, Any] | None = None,
        get_task_definitions: Callable[
            [dict[str, Any], ProcessMetadataFilter | None],
            Awaitable[list[TaskInstance]],
        ] | None = None,
        cancel_grace_seconds: float = 5.0,
    ) -> None:
```

Add the `rpc_server` parameter and update the docstring. Replace the signature block (and the docstring's `Args:` block if present) with:

```python
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
```

(The body that follows the `OptioConfig(...)` assignment continues unchanged — see Step 13 for the additions inside that body.)

Also add at the top of the file (or just below the existing `from optio_core.consumer import CommandConsumer` import):

```python
from clamator_protocol import RpcServerCore
from clamator_over_redis import RedisRpcServer
```

And add three new instance attributes inside `__init__`:

```python
        self.rpc_server: "RpcServerCore | None" = None
        self._engine_service = None  # Set by init() when an rpc_server exists.
        self._owned_rpc_server: bool = False
```

(Insert these in `__init__`, alongside the existing `self._consumer: CommandConsumer | None = None` line.)

- [ ] **Step 13: Modify `init()` body — construct `RedisRpcServer` and register `EngineService`**

Inside `init()`, locate the `if redis_url:` block (around line 100). Replace it with:

```python
        # Connect to Redis (if configured)
        if redis_url:
            if Redis is None:
                raise ImportError(
                    "Redis support requires the 'redis' extra: "
                    "pip install optio[redis]"
                )
            self._redis = Redis.from_url(redis_url)

            # Legacy command stream (phase-2 co-existence; phase 5 removes).
            db_name = mongo_db.name
            stream_name = f"{db_name}/{prefix}:commands"
            self._consumer = CommandConsumer(self._redis, stream_name)
            self._consumer.on("launch", self._handle_launch)
            self._consumer.on("cancel", self._handle_cancel)
            self._consumer.on("dismiss", self._handle_dismiss)
            self._consumer.on("resync", self._handle_resync)
            await self._consumer.setup()

            # New clamator RPC server (phase 2 of engine-RPC migration).
            self.rpc_server = RedisRpcServer(
                redis=self._redis,
                key_prefix=f"{db_name}/{prefix}",
                consumer_claim_idle_ms=600_000,  # 10 min — accommodates groupCancelAndWait
            )
            self._owned_rpc_server = True

            # Defer EngineService import to avoid circular module load.
            from optio_core._engine_service import EngineService
            from optio_core._generated.engine import engine_contract
            self._engine_service = EngineService(self)
            self.rpc_server.register_service(engine_contract, self._engine_service)

        elif rpc_server is not None:
            # App-provided server (test or shared-server mode). No legacy consumer.
            self.rpc_server = rpc_server
            self._owned_rpc_server = False
            from optio_core._engine_service import EngineService
            from optio_core._generated.engine import engine_contract
            self._engine_service = EngineService(self)
            self.rpc_server.register_service(engine_contract, self._engine_service)
```

The exact `register_service` call signature follows clamator's `RpcServerCore.register_service(contract, service)` — confirm by reading `~/deai/clamator/py/packages/protocol/src/clamator_protocol/server_core.py` if uncertain.

- [ ] **Step 14: Modify `run()` and `shutdown()` — start/stop the rpc_server**

In `lifecycle.py` `run()` (around line 542), insert a `start()` call before the existing `await self._scheduler.start()`:

```python
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
        # ... rest of method unchanged
```

In `shutdown()` (around line 574), insert a `stop()` call after the consumer-stop block but before closing the redis client:

```python
        # 2. Consumer
        if self._consumer:
            self._consumer.stop()
        if hasattr(self, '_shutdown_event'):
            self._shutdown_event.set()

        # 2b. RPC server (clamator)
        if self.rpc_server is not None and self._owned_rpc_server:
            grace_ms = int(
                (grace_seconds if grace_seconds is not None else self._config.cancel_grace_seconds) * 1000
            )
            await self.rpc_server.stop(grace_ms=grace_ms)

        # 3. Cancel everything via the unified mechanism.
        # ... rest of method unchanged
```

- [ ] **Step 15: Modify `__init__.py` — PEP 562 module `__getattr__`**

Edit `packages/optio-core/src/optio_core/__init__.py`. Append to the file:

```python


def __getattr__(name: str):
    """Module-level attribute lookup for runtime-populated attributes.

    `rpc_server` is set on the singleton _instance during init(); a normal
    `rpc_server = _instance.rpc_server` binding at module import time would
    capture None forever. PEP 562 __getattr__ forwards reads on access.
    """
    if name == "rpc_server":
        return _instance.rpc_server
    raise AttributeError(f"module 'optio_core' has no attribute {name!r}")
```

- [ ] **Step 16: Add lifecycle tests — mutual exclusivity + module attribute**

Append to `packages/optio-core/tests/test_engine_service.py`:

```python
@pytest.mark.asyncio
async def test_init_redis_url_and_rpc_server_mutually_exclusive(mongo_db):
    from optio_core.lifecycle import Optio
    fw = Optio()
    fake_server = MagicMock()
    with pytest.raises(ValueError, match="mutually exclusive"):
        await fw.init(mongo_db=mongo_db, redis_url="redis://x", rpc_server=fake_server)


@pytest.mark.asyncio
async def test_module_rpc_server_attribute_reflects_instance(mongo_db):
    """optio_core.rpc_server should reflect the singleton's runtime state."""
    import optio_core
    # Pre-init the singleton state may be None; we test the forwarding.
    optio_core._instance.rpc_server = "sentinel"
    try:
        assert optio_core.rpc_server == "sentinel"
    finally:
        optio_core._instance.rpc_server = None
```

Run: `pytest tests/test_engine_service.py -v` — all PASS.

- [ ] **Step 17: Run the full optio-core test suite**

```bash
cd /home/csillag/deai/optio/packages/optio-core
pytest -v
```
Expected: all existing tests PASS plus all new `test_engine_service.py` tests PASS. No regressions.

- [ ] **Step 18: Commit**

```bash
cd /home/csillag/deai/optio
git add packages/optio-core/src/optio_core/_engine_service.py
git add packages/optio-core/tests/test_engine_service.py
git add packages/optio-core/src/optio_core/lifecycle.py
git add packages/optio-core/src/optio_core/__init__.py
git commit -m "feat(optio-core): add EngineService + RedisRpcServer lifecycle integration

Implements the codegenned EngineService ABC for all 8 RPC methods
(launch, cancel, dismiss, resync, group_cancel, group_cancel_and_wait,
block_launches, unblock_launches). Hooks RedisRpcServer into
Optio.init/run/shutdown alongside the legacy CommandConsumer; both
ingress paths are now alive. Exposes optio_core.rpc_server via
PEP 562 module __getattr__.

Phase 2 of docs/2026-05-08-engine-rpc-migration-design.md."
```

---

## Task 2: API — `engine-cache.ts` standalone module (single commit)

**Files:**
- Create: `packages/optio-api/src/engine-cache.ts`
- Create: `packages/optio-api/src/__tests__/engine-cache.test.ts`

- [ ] **Step 1: Write the failing cache test**

Create `packages/optio-api/src/__tests__/engine-cache.test.ts`:

```typescript
import { describe, it, expect, vi } from 'vitest';
import { createEngineCache } from '../engine-cache.js';

// Minimal Redis stub — engine-cache passes it to RedisRpcClient. We don't
// actually communicate; we just check that cache keys, lifecycle, and
// idempotency behave.
const fakeRedis: any = { duplicate: () => fakeRedis };

describe('createEngineCache', () => {
  it('returns the same EngineClient for the same (database, prefix)', () => {
    const cache = createEngineCache(fakeRedis);
    const a = cache.get('db1', 'optio');
    const b = cache.get('db1', 'optio');
    expect(a).toBe(b);
  });

  it('returns distinct clients for distinct keys', () => {
    const cache = createEngineCache(fakeRedis);
    const a = cache.get('db1', 'optio');
    const b = cache.get('db2', 'optio');
    const c = cache.get('db1', 'other');
    expect(a).not.toBe(b);
    expect(a).not.toBe(c);
    expect(b).not.toBe(c);
  });

  it('closeAll() stops every cached client', async () => {
    const cache = createEngineCache(fakeRedis);
    const a = cache.get('db1', 'optio');
    const b = cache.get('db2', 'optio');
    const stopA = vi.spyOn(a, 'stop').mockResolvedValue(undefined);
    const stopB = vi.spyOn(b, 'stop').mockResolvedValue(undefined);
    await cache.closeAll();
    expect(stopA).toHaveBeenCalledOnce();
    expect(stopB).toHaveBeenCalledOnce();
  });

  it('closeAll() called twice succeeds (idempotent)', async () => {
    const cache = createEngineCache(fakeRedis);
    cache.get('db1', 'optio');
    await cache.closeAll();
    await expect(cache.closeAll()).resolves.toBeUndefined();
  });
});
```

- [ ] **Step 2: Run the test — confirm RED**

```bash
cd /home/csillag/deai/optio/packages/optio-api
pnpm vitest run src/__tests__/engine-cache.test.ts
```
Expected: FAIL with `Failed to resolve import "../engine-cache.js"`.

- [ ] **Step 3: Implement `engine-cache.ts`**

Create `packages/optio-api/src/engine-cache.ts`:

```typescript
import type { Redis } from 'ioredis';
import { RedisRpcClient } from '@clamator/over-redis';
import { EngineClient } from './_generated/engine.js';

export interface EngineCache {
  get(database: string, prefix: string): EngineClient;
  closeAll(): Promise<void>;
}

// TODO: cache is unbounded by design. Multi-db deployments are expected to
// have a small (~10) number of (database, prefix) pairs. If the cache exceeds
// 100 entries in production, file an issue and revisit eviction strategy.
export function createEngineCache(redis: Redis): EngineCache {
  const map = new Map<string, EngineClient>();

  return {
    get(database, prefix) {
      const key = `${database}/${prefix}`;
      let engine = map.get(key);
      if (!engine) {
        engine = new EngineClient(new RedisRpcClient({ redis, keyPrefix: key }));
        engine.start();
        map.set(key, engine);
      }
      return engine;
    },

    async closeAll() {
      await Promise.all([...map.values()].map((e) => e.stop()));
      map.clear();
    },
  };
}
```

- [ ] **Step 4: Run the test — confirm GREEN**

```bash
cd /home/csillag/deai/optio/packages/optio-api
pnpm vitest run src/__tests__/engine-cache.test.ts
```
Expected: 4 tests PASS.

- [ ] **Step 5: Confirm the rest of the optio-api suite still passes**

```bash
pnpm -r --filter optio-api test
```
Expected: all PASS.

- [ ] **Step 6: Commit**

```bash
cd /home/csillag/deai/optio
git add packages/optio-api/src/engine-cache.ts
git add packages/optio-api/src/__tests__/engine-cache.test.ts
git commit -m "feat(optio-api): add framework-agnostic engine-cache

createEngineCache(redis): EngineCache lazily constructs and caches
EngineClient instances per (database, prefix). closeAll() drains every
cached client and is idempotent. Adapters consume in the next commit.

Phase 2 of docs/2026-05-08-engine-rpc-migration-design.md."
```

---

## Task 3: API — adapter wiring + return shape (single commit)

**Files:**
- Modify: `packages/optio-api/src/adapters/fastify.ts`
- Modify: `packages/optio-api/src/adapters/express.ts`
- Modify: `packages/optio-api/src/adapters/nextjs-app.ts`
- Modify: `packages/optio-api/src/adapters/nextjs-pages.ts`
- Modify: `packages/optio-api/src/index.ts`
- Modify: `packages/optio-api/src/adapters/__tests__/fastify.test.ts`
- Modify: `packages/optio-api/src/adapters/__tests__/express.test.ts`
- Modify: `packages/optio-api/src/adapters/__tests__/nextjs-app.test.ts`
- Modify: `packages/optio-api/src/adapters/__tests__/nextjs-pages.test.ts`

- [ ] **Step 1: Write the failing return-shape test in fastify**

Edit `packages/optio-api/src/adapters/__tests__/fastify.test.ts`. Add at the end of the file:

```typescript
import { EngineClient } from '../../_generated/engine.js';

describe('registerOptioApi return shape', () => {
  it('single-db mode returns { engine, closeAll }', async () => {
    const app = Fastify();
    const result = registerOptioApi(app, { db, redis, authenticate: () => 'operator' });
    expect(result).toBeDefined();
    expect(result.engine).toBeInstanceOf(EngineClient);
    expect(typeof result.closeAll).toBe('function');
    expect((result as any).getEngine).toBeUndefined();
    await app.close();
  });

  it('multi-db mode returns { getEngine, closeAll }', async () => {
    const app = Fastify();
    const result = registerOptioApi(app, { mongoClient, redis, authenticate: () => 'operator' });
    expect(result).toBeDefined();
    expect(typeof result.getEngine).toBe('function');
    expect(typeof result.closeAll).toBe('function');
    expect((result as any).engine).toBeUndefined();
    // Cache reuse:
    const a = result.getEngine!('db1', 'optio');
    const b = result.getEngine!('db1', 'optio');
    expect(a).toBe(b);
    await app.close();
  });

  it('closeAll called twice succeeds', async () => {
    const app = Fastify();
    const result = registerOptioApi(app, { db, redis, authenticate: () => 'operator' });
    await result.closeAll!();
    await expect(result.closeAll!()).resolves.toBeUndefined();
    await app.close();
  });
});
```

The variables `app`, `db`, `redis`, `mongoClient` should match the existing test file's setup; if those names differ, mirror what's already at the top of the test file.

- [ ] **Step 2: Run the test — confirm RED**

```bash
cd /home/csillag/deai/optio/packages/optio-api
pnpm vitest run src/adapters/__tests__/fastify.test.ts
```
Expected: FAIL — current `registerOptioApi` returns nothing.

- [ ] **Step 3: Update fastify adapter — wire cache + return shape**

Edit `packages/optio-api/src/adapters/fastify.ts`:

(a) At the top of the file (with the other imports), add:

```typescript
import { createEngineCache } from '../engine-cache.js';
```

(b) Inside `registerOptioApi`, immediately after the existing `const { redis } = opts;` line, add:

```typescript
  const cache = createEngineCache(redis);
  app.addHook('onClose', () => cache.closeAll());
```

(c) At the very end of `registerOptioApi`, after the `app.register(s.plugin(routes));` and the rest of the route registrations, add the return:

```typescript
  if ('db' in opts && opts.db) {
    const prefix = opts.prefix ?? 'optio';
    return {
      engine: cache.get(opts.db.databaseName, prefix),
      closeAll: () => cache.closeAll(),
    };
  }
  return {
    getEngine: (database: string, prefix: string) => cache.get(database, prefix),
    closeAll: () => cache.closeAll(),
  };
```

Do **not** modify the route handlers (`launch`, `cancel`, `dismiss`, `resync`); they continue calling `handlers.launchProcess(db, redis, database, prefix, ...)` etc.

- [ ] **Step 4: Update fastify return type signature**

If the existing function is typed `export function registerOptioApi(app, opts)` without an explicit return type, leave inference to do its thing. If there's an explicit return type, extend it to a discriminated union:

```typescript
export type OptioApiHandle =
  | { engine: EngineClient; closeAll: () => Promise<void> }
  | { getEngine: (database: string, prefix: string) => EngineClient; closeAll: () => Promise<void> };
```

Add the import at the top:

```typescript
import type { EngineClient } from '../_generated/engine.js';
```

- [ ] **Step 5: Run the fastify test — confirm GREEN**

```bash
pnpm vitest run src/adapters/__tests__/fastify.test.ts
```
Expected: PASS for all the new return-shape tests; existing tests unaffected.

- [ ] **Step 6: Update express adapter — same pattern**

Edit `packages/optio-api/src/adapters/express.ts`:

(a) Add the same import:

```typescript
import { createEngineCache } from '../engine-cache.js';
```

(b) Inside the registration function, after destructuring `{ redis } = opts`, add:

```typescript
  const cache = createEngineCache(redis);
```

Do NOT add an `onClose` hook — express has none. Caller wires `closeAll` manually.

(c) At the function end, return the same shape:

```typescript
  if ('db' in opts && opts.db) {
    const prefix = opts.prefix ?? 'optio';
    return {
      engine: cache.get(opts.db.databaseName, prefix),
      closeAll: () => cache.closeAll(),
    };
  }
  return {
    getEngine: (database: string, prefix: string) => cache.get(database, prefix),
    closeAll: () => cache.closeAll(),
  };
```

- [ ] **Step 7: Mirror the changes in `nextjs-app.ts` and `nextjs-pages.ts`**

For each adapter, add the import, instantiate the cache after the `redis` destructure, and return the same shape at the function end. No framework close hook.

- [ ] **Step 8: Update `packages/optio-api/src/index.ts`**

Append to the existing exports:

```typescript
// Engine RPC client and cache (phase 2 of engine-RPC migration).
export { EngineClient } from './_generated/engine.js';
export { createEngineCache, type EngineCache } from './engine-cache.js';
```

- [ ] **Step 9: Mirror the return-shape test into the other 3 adapter test files**

For `express.test.ts`, `nextjs-app.test.ts`, `nextjs-pages.test.ts`, add an analogous `describe('registerOptioApi return shape', ...)` block adapted to that adapter's setup. The structure:

```typescript
import { EngineClient } from '../../_generated/engine.js';

describe('registerOptioApi return shape', () => {
  it('single-db mode returns { engine, closeAll }', () => {
    // ...adapter-appropriate registration call
    // Express: const result = registerOptioApi(app, { db, redis, authenticate: ... });
    // Next.js app: const result = createOptioRouteHandlers({ db, redis, authenticate: ... });
    // Next.js pages: const result = createOptioHandler({ db, redis, authenticate: ... });
    expect(result.engine).toBeInstanceOf(EngineClient);
    expect(typeof result.closeAll).toBe('function');
  });

  it('multi-db mode returns { getEngine, closeAll }', () => {
    // ...
    expect(typeof result.getEngine).toBe('function');
    expect(typeof result.closeAll).toBe('function');
  });

  it('closeAll called twice succeeds', async () => {
    // ...
    await result.closeAll();
    await expect(result.closeAll()).resolves.toBeUndefined();
  });
});
```

For each adapter, mirror the test setup of that adapter's existing test file (db / mongoClient construction, registration call shape).

- [ ] **Step 10: Run the full optio-api suite**

```bash
cd /home/csillag/deai/optio/packages/optio-api
pnpm vitest run
```
Expected: all tests PASS — existing assertions unaffected, new return-shape assertions pass on all 4 adapters.

- [ ] **Step 11: Type-check across the workspace**

```bash
cd /home/csillag/deai/optio
pnpm -r build
```
Expected: green build. In particular `packages/optio-dashboard` (which calls `await registerOptioApi(...)` and discards the return) must still type-check — ignoring an inferred return is always fine.

- [ ] **Step 12: Commit**

```bash
git add packages/optio-api/src/adapters/fastify.ts
git add packages/optio-api/src/adapters/express.ts
git add packages/optio-api/src/adapters/nextjs-app.ts
git add packages/optio-api/src/adapters/nextjs-pages.ts
git add packages/optio-api/src/index.ts
git add packages/optio-api/src/adapters/__tests__/fastify.test.ts
git add packages/optio-api/src/adapters/__tests__/express.test.ts
git add packages/optio-api/src/adapters/__tests__/nextjs-app.test.ts
git add packages/optio-api/src/adapters/__tests__/nextjs-pages.test.ts
git commit -m "feat(optio-api): wire engine-cache through all adapters; new return shape

registerOptioApi (and createOptioRouteHandlers, createOptioHandler) now
return { engine, closeAll } in single-db mode and { getEngine, closeAll }
in multi-db mode. Fastify auto-wires cache.closeAll on onClose;
express/next callers wire closeAll manually. HTTP handlers are
unchanged — still publish to legacy redis stream.

Re-exports EngineClient and createEngineCache from optio-api root.

Phase 2 of docs/2026-05-08-engine-rpc-migration-design.md."
```

---

## Task 4: Interop substrate — `optio-demo/interop/` package + `run-interop.sh` + Makefile body (single commit)

**Files:**
- Create: `packages/optio-demo/interop/package.json`
- Create: `packages/optio-demo/interop/tsconfig.json`
- Create: `packages/optio-demo/interop/run.ts`
- Create: `packages/optio-demo/run-interop.sh`
- Modify: `Makefile` (root) — `test-interop` target body
- Possibly modify: `pnpm-workspace.yaml` if `packages/optio-demo/interop` is not already covered

- [ ] **Step 1: Confirm pnpm-workspace covers the new path**

```bash
cat /home/csillag/deai/optio/pnpm-workspace.yaml
```

If it contains `packages/*` it covers `packages/optio-demo` but NOT `packages/optio-demo/interop`. Add `packages/optio-demo/interop` explicitly:

```yaml
packages:
  - 'packages/*'
  - 'packages/optio-demo/interop'
```

- [ ] **Step 2: Create the interop package**

Create `packages/optio-demo/interop/package.json`:

```json
{
  "name": "optio-demo-interop",
  "private": true,
  "version": "0.0.0",
  "type": "module",
  "scripts": {
    "run": "tsx run.ts"
  },
  "dependencies": {
    "optio-api": "workspace:*",
    "optio-contracts": "workspace:*",
    "@clamator/over-redis": "^0.1.5",
    "@clamator/protocol": "^0.1.5",
    "ioredis": "^5.3.0"
  },
  "devDependencies": {
    "tsx": "^4.7.0",
    "typescript": "^5.4.0"
  }
}
```

Create `packages/optio-demo/interop/tsconfig.json`:

```json
{
  "extends": "../../../tsconfig.base.json",
  "compilerOptions": {
    "module": "esnext",
    "moduleResolution": "bundler",
    "target": "es2022",
    "strict": true,
    "noEmit": true,
    "skipLibCheck": true
  },
  "include": ["run.ts"]
}
```

If `tsconfig.base.json` does not exist at repo root, drop the `extends` line and inline the necessary options (the project already has a base config — confirm with `ls /home/csillag/deai/optio/tsconfig*.json`).

- [ ] **Step 3: Install the new workspace package**

```bash
cd /home/csillag/deai/optio
pnpm install
```
Expected: pnpm picks up `optio-demo-interop`, installs `tsx`, `typescript`, etc.

- [ ] **Step 4: Write the interop runner**

Create `packages/optio-demo/interop/run.ts`:

```typescript
/**
 * Phase-2 interop scenarios. Direct clamator client → optio-demo engine.
 * Verifies the wire works end-to-end and the legacy ${prefix}:commands
 * stream still functions during co-existence.
 *
 * Assumptions (set up by run-interop.sh before this script runs):
 *  - Redis is reachable at REDIS_URL (default redis://localhost:6379).
 *  - An optio-demo engine subprocess has been started with prefix=optio
 *    and database=optio-demo. Heartbeat key optio-demo/optio:heartbeat
 *    has been written.
 *  - At least one task in optio-demo declares processId=opencode-demo.
 */
import IORedis from 'ioredis';
import { RedisRpcClient } from '@clamator/over-redis';
import { EngineClient } from 'optio-api';

const REDIS_URL = process.env.REDIS_URL ?? 'redis://localhost:6379';
const DATABASE = 'optio-demo';
const PREFIX = 'optio';
const KEY_PREFIX = `${DATABASE}/${PREFIX}`;
const PROC = 'opencode-demo';

const redis = new IORedis(REDIS_URL);
const client = new RedisRpcClient({ redis, keyPrefix: KEY_PREFIX });
const engine = new EngineClient(client);
engine.start();

let exitCode = 0;
function fail(scenario: string, msg: string) {
  console.error(`✗ ${scenario}: ${msg}`);
  exitCode = 1;
}
function ok(scenario: string) {
  console.log(`✓ ${scenario}`);
}

async function waitForState(
  expected: string[],
  timeoutMs: number = 5000,
): Promise<string> {
  const deadline = Date.now() + timeoutMs;
  while (Date.now() < deadline) {
    const r = await engine.cancel({ processId: PROC }).catch(() => null);
    // We use the cancel call as a state probe — but that has side effects.
    // Better: read from Mongo via a thin client. For phase 2 simplicity, the
    // engine subprocess writes state transitions fast (single-task demo);
    // accept a small fixed delay instead.
    if (r && r.ok && expected.includes(r.process.status.state)) {
      return r.process.status.state;
    }
    await new Promise((res) => setTimeout(res, 100));
  }
  return '';
}

async function dismissIfTerminal() {
  // Helper: leave the proc in 'idle' between scenarios.
  await engine.dismiss({ processId: PROC }).catch(() => null);
}

async function main() {
  // Reset state baseline.
  await dismissIfTerminal();

  // 1. Launch success
  {
    const r = await engine.launch({ processId: PROC });
    if (!r.ok) fail('launch success', `expected ok=true, got reason=${r.reason}`);
    else ok('launch success');
  }

  // 2. Launch on running → not-launchable
  {
    const r = await engine.launch({ processId: PROC });
    if (r.ok) fail('launch not-launchable', 'expected ok=false');
    else if (r.reason !== 'not-launchable')
      fail('launch not-launchable', `expected reason=not-launchable, got ${r.reason}`);
    else ok('launch not-launchable');
  }

  // 3. Cancel success
  {
    const r = await engine.cancel({ processId: PROC });
    if (!r.ok) fail('cancel success', `expected ok=true, got reason=${r.reason}`);
    else ok('cancel success');
  }

  // 4. Dismiss success
  {
    const r = await engine.dismiss({ processId: PROC });
    if (!r.ok) fail('dismiss success', `expected ok=true, got reason=${r.reason}`);
    else ok('dismiss success');
  }

  // 5. Cancel idle → not-cancellable
  {
    const r = await engine.cancel({ processId: PROC });
    if (r.ok) fail('cancel not-cancellable', 'expected ok=false');
    else if (r.reason !== 'not-cancellable')
      fail('cancel not-cancellable', `expected not-cancellable, got ${r.reason}`);
    else ok('cancel not-cancellable');
  }

  // 6. Dismiss idle → not-dismissable
  {
    const r = await engine.dismiss({ processId: PROC });
    if (r.ok) fail('dismiss not-dismissable', 'expected ok=false');
    else if (r.reason !== 'not-dismissable')
      fail('dismiss not-dismissable', `expected not-dismissable, got ${r.reason}`);
    else ok('dismiss not-dismissable');
  }

  // 7. Launch nonexistent
  {
    const r = await engine.launch({ processId: 'no-such-process' });
    if (r.ok) fail('launch not-found', 'expected ok=false');
    else if (r.reason !== 'not-found')
      fail('launch not-found', `expected not-found, got ${r.reason}`);
    else ok('launch not-found');
  }

  // 8. Block / unblock cycle. Uses an empty filter ({}) which matches every
  // task — works regardless of whether opencode-demo carries metadata.
  {
    await dismissIfTerminal();  // ensure proc is idle / launchable.
    const block = await engine.blockLaunches({
      launchFilter: {},
      reason: 'phase-2-interop',
    });
    if (!block.ok) {
      fail('blockLaunches', `expected ok=true, got reason=${block.reason}`);
    } else {
      ok('blockLaunches');
      const launchBlocked = await engine.launch({ processId: PROC });
      if (launchBlocked.ok) {
        fail('launch-blocked', 'expected ok=false');
      } else if (launchBlocked.reason !== 'launch-blocked') {
        fail(
          'launch-blocked',
          `expected reason=launch-blocked, got ${launchBlocked.reason}`,
        );
      } else {
        ok('launch-blocked');
      }
      const unblock = await engine.unblockLaunches({ launchFilter: {} });
      if (unblock.removed < 1) fail('unblockLaunches', `expected removed>=1, got ${unblock.removed}`);
      else ok('unblockLaunches');

      // Re-launch should now succeed.
      const relaunch = await engine.launch({ processId: PROC });
      if (!relaunch.ok) fail('relaunch after unblock', `got reason=${relaunch.reason}`);
      else ok('relaunch after unblock');
    }
  }

  // 9. Resync notification
  {
    await engine.resync({});
    ok('resync notification (no-throw)');
  }

  // 10. groupCancel invalid persist
  {
    const r = await engine.groupCancel({
      metadataFilter: { tag: 'demo' },
      persist: true,
    });
    if (r.ok) fail('groupCancel invalid-persist', 'expected ok=false');
    else if (r.reason !== 'invalid-persist-without-block')
      fail('groupCancel invalid-persist', `expected invalid-persist-without-block, got ${r.reason}`);
    else ok('groupCancel invalid-persist');
  }

  // 11. Legacy stream regression — XADD a launch command and confirm engine consumed.
  {
    await dismissIfTerminal();
    const id = await redis.xadd(
      `${KEY_PREFIX}:commands`,
      '*',
      'type',
      'launch',
      'processId',
      PROC,
    );
    // Allow the engine consumer a brief window to consume the entry.
    await new Promise((res) => setTimeout(res, 1000));
    // Verify by reading the process state via cancel (bounces it; check ok).
    const r = await engine.cancel({ processId: PROC });
    if (!r.ok) fail('legacy stream regression', `expected post-launch cancellable, got reason=${r.reason}`);
    else ok(`legacy stream regression (xadd id=${id})`);
  }

  await engine.stop();
  await redis.quit();
  process.exit(exitCode);
}

main().catch((e) => {
  console.error('fatal:', e);
  process.exit(2);
});
```

- [ ] **Step 5: Write the run-interop.sh orchestrator**

Create `packages/optio-demo/run-interop.sh`:

```bash
#!/usr/bin/env bash
# Phase-2 interop test for optio engine ↔ TS clamator client.
# Spins ephemeral docker redis + python engine subprocess + node test runner.

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
DEMO_DIR="$REPO_ROOT/packages/optio-demo"
COMPOSE="docker compose -f $DEMO_DIR/docker-compose.yml"

cleanup() {
  set +e
  if [[ -n "${ENGINE_PID:-}" ]]; then
    kill "$ENGINE_PID" 2>/dev/null
    wait "$ENGINE_PID" 2>/dev/null
  fi
  $COMPOSE down -v >/dev/null 2>&1
}
trap cleanup EXIT

echo "[interop] bringing up redis..."
$COMPOSE up -d redis

echo "[interop] waiting for redis ready..."
for i in {1..50}; do
  if $COMPOSE exec -T redis redis-cli ping 2>/dev/null | grep -q PONG; then
    break
  fi
  sleep 0.2
done

echo "[interop] starting optio-demo engine..."
cd "$DEMO_DIR"
MONGODB_URL="mongodb://localhost:27017/optio-demo" \
  REDIS_URL="redis://localhost:6379" \
  OPTIO_PREFIX="optio" \
  python -m optio_demo &
ENGINE_PID=$!
cd "$REPO_ROOT"

echo "[interop] waiting for engine heartbeat..."
HEARTBEAT_KEY="optio-demo/optio:heartbeat"
for i in {1..150}; do
  if $COMPOSE exec -T redis redis-cli exists "$HEARTBEAT_KEY" 2>/dev/null | grep -q '^1$'; then
    echo "[interop] engine ready."
    break
  fi
  sleep 0.2
done

echo "[interop] running scenarios..."
cd "$DEMO_DIR/interop"
pnpm exec tsx run.ts
EXIT=$?

exit $EXIT
```

Make it executable:

```bash
chmod +x packages/optio-demo/run-interop.sh
```

- [ ] **Step 6: Fill in the `Makefile` test-interop body**

Edit the root `Makefile`. Find the `test-interop:` target (already declared in phase 1; body is the placeholder line `cd packages/optio-demo && bash run-interop.sh`). Replace with:

```make
test-interop:  ## End-to-end test: TS clamator client ↔ Py engine over real redis (clamator wire verification)
	bash packages/optio-demo/run-interop.sh
```

- [ ] **Step 7: Run `make test-interop` locally**

```bash
cd /home/csillag/deai/optio
make test-interop
```
Expected: docker compose up, engine boots, runner prints `✓` lines for every scenario, exits 0.

If a single scenario fails, debug — most likely cause is a state-machine mismatch (e.g. opencode-demo doesn't have `tag: demo` metadata; check `packages/optio-demo/src/optio_demo/tasks/opencode.py`). Adjust filters or scenarios in `run.ts` until green; do NOT broaden `EngineService` matchers.

- [ ] **Step 8: Verify both ingress paths**

```bash
docker exec optio-demo-redis redis-cli xrange "optio-demo/optio:cmds:engine" - + COUNT 1
docker exec optio-demo-redis redis-cli xrange "optio-demo/optio:commands" - + COUNT 1
```
Both streams should have entries after a green interop run.

(If `optio-demo-redis` isn't the docker container name, use `docker ps` to find it and substitute.)

- [ ] **Step 9: Commit**

```bash
cd /home/csillag/deai/optio
git add pnpm-workspace.yaml
git add packages/optio-demo/interop/
git add packages/optio-demo/run-interop.sh
git add Makefile
git commit -m "feat(optio-demo): interop substrate for engine-RPC migration

Adds packages/optio-demo/interop/ TS subpackage with phase-2 scenarios
covering the success matrix, all failure reasons, resync notification,
block/unblock cycle, and the legacy XADD regression check. The runner
shells out from run-interop.sh which orchestrates docker redis + python
engine subprocess + node test runner. Wires the test-interop target
in the root Makefile.

Phase 2 of docs/2026-05-08-engine-rpc-migration-design.md."
```

---

## Task 5: Documentation + parent-spec corrections (single commit)

**Files:**
- Modify: `packages/optio-core/README.md`
- Modify: `packages/optio-core/AGENTS.md`
- Modify: `packages/optio-api/README.md`
- Modify: `packages/optio-api/AGENTS.md`
- Modify: `AGENTS.md` (root)
- Modify: `docs/2026-05-08-engine-rpc-migration-design.md` (parent-spec corrections)

- [ ] **Step 1: optio-core README — add "RPC service" section**

Edit `packages/optio-core/README.md`. After the section that describes the legacy redis command stream (search for `Remote Control via Redis` or `commands` heading), insert a new section:

```markdown
## RPC service

In addition to the legacy `${prefix}:commands` redis stream consumer
(see "Remote Control via Redis"), `optio-core` hosts a clamator
`RedisRpcServer` listening on `${database}/${prefix}:cmds:engine`.
The server exposes the `engine` service whose contract lives in
`packages/optio-contracts/src/engine-to-api.ts`. The Python ABC and
Pydantic models are codegenned to
`src/optio_core/_generated/engine.py`.

The server is constructed automatically by `optio_core.init(...)` when
`redis_url` is supplied, and is exposed at `optio_core.rpc_server` for
applications that need to register additional services on the same
server before calling `optio_core.run()`:

```python
import optio_core

await optio_core.init(mongo_db=db, redis_url=URL, prefix='myapp')
optio_core.rpc_server.register_service(my_domain_contract, MyDomainService())
await optio_core.run()
```

During phases 2-4 of the engine-RPC migration the legacy
`${prefix}:commands` stream and the new RPC server co-exist; both
ingress paths are functional. Phase 5 retires the legacy stream.
```

- [ ] **Step 2: optio-core AGENTS.md — document `rpc_server` attribute and `init()` parameter**

Edit `packages/optio-core/AGENTS.md`. In the "Public API" or equivalent table, add a row for `rpc_server`. In the `init()` docstring summary, add `rpc_server` to the parameter list:

```markdown
- `rpc_server` (`RpcServerCore | None`): Pre-built clamator RPC server. Mutually exclusive with `redis_url`. When supplied, optio-core registers `EngineService` on it but does not own its lifecycle.
```

And add a top-level entry:

```markdown
- `optio_core.rpc_server`: The `RedisRpcServer` constructed during `init(redis_url=...)`, or the server passed via `init(rpc_server=...)`, or `None`. Apps register additional clamator services on this attribute before calling `optio_core.run()`.
```

- [ ] **Step 3: optio-api README — return shape and EngineClient sharing example**

Edit `packages/optio-api/README.md`. In the section that documents `registerOptioApi` (or `OptioApiOptions`), add:

```markdown
### Return value

`registerOptioApi`, `createOptioRouteHandlers`, and `createOptioHandler`
return an object that exposes the underlying clamator engine client(s)
plus a teardown function:

- **Single-db mode** (`db` supplied): `{ engine, closeAll }`
  - `engine: EngineClient` — typed client ready to call engine RPC methods.
  - `closeAll(): Promise<void>` — drains every cached client. Idempotent.
- **Multi-db mode** (`mongoClient` supplied): `{ getEngine, closeAll }`
  - `getEngine(database, prefix): EngineClient` — looks up or lazily
    constructs the client for `(database, prefix)`. Repeat lookups
    return the same instance.
  - `closeAll(): Promise<void>` — same as above.

Fastify wires `closeAll` into its `onClose` lifecycle hook
automatically. Express and Next.js have no equivalent; callers wire
`closeAll` into their shutdown handler manually:

```typescript
const { engine, closeAll } = registerOptioApi(app, { db, redis });
const server = app.listen(3000);
process.on('SIGTERM', async () => {
  server.close();
  await closeAll();
});
```

The returned `engine` (or `getEngine(...)`) can be shared with non-HTTP
code paths (custom RPC integrations, server-side scheduled jobs) so
they do not need to construct their own clamator client.
```

- [ ] **Step 4: optio-api AGENTS.md — return shape, exports, custom-adapter section**

Edit `packages/optio-api/AGENTS.md`. In the "Exports" section, add:

```markdown
- `EngineClient` (re-exported from `_generated/engine.ts`) — typed clamator client for the engine RPC contract. Use to call engine methods from non-HTTP code paths.
- `createEngineCache(redis: Redis): EngineCache` — framework-agnostic factory that lazily constructs and caches `EngineClient` instances per `(database, prefix)`. Custom adapters consume this rather than rolling their own cache.
- `EngineCache` — the type returned by `createEngineCache`. `get(database, prefix): EngineClient` and `closeAll(): Promise<void>`.
```

In the "Building Custom Adapters" section (or equivalent), add a paragraph noting that custom adapters MUST use `createEngineCache(opts.redis)` rather than constructing `RedisRpcClient` / `EngineClient` directly, and MUST wire their framework's shutdown into `cache.closeAll()` (where possible) or expose `closeAll` on the adapter's return value.

In the registration-function summary, document the new return shape:

```markdown
`registerOptioApi(app, opts)` returns:
- `{ engine: EngineClient, closeAll: () => Promise<void> }` in single-db mode.
- `{ getEngine: (database, prefix) => EngineClient, closeAll: () => Promise<void> }` in multi-db mode.
```

- [ ] **Step 5: Root AGENTS.md — Python API and return-shape note**

Edit `AGENTS.md` (root). In the Python API table, add:

```markdown
| `optio_core.rpc_server` | attribute | The clamator `RedisRpcServer` constructed during `init(redis_url=...)` or supplied via `init(rpc_server=...)`. Apps register extra services here before `optio_core.run()`. `None` if no Redis is configured. |
```

In the optio-api section, add a brief note:

```markdown
`registerOptioApi` (and Next.js equivalents) return a handle exposing the underlying `EngineClient`(s) and a `closeAll` teardown. See `packages/optio-api/README.md` for details.
```

- [ ] **Step 6: Parent-spec corrections — rename `_command_consumer.py` references**

Edit `docs/2026-05-08-engine-rpc-migration-design.md`. Find all references to `_command_consumer.py` (likely in §4 file-layout description and §8 phase 5 deliverables) and replace with `consumer.py`:

```bash
grep -n "_command_consumer" docs/2026-05-08-engine-rpc-migration-design.md
```

Use Edit on each match. Also fix any internal-class reference: the class is `CommandConsumer` (unchanged), only the filename was wrong.

After the edits:

```bash
grep -n "_command_consumer" docs/2026-05-08-engine-rpc-migration-design.md
```
Expected: only references inside the phase-2 corrections paragraph itself ("rename `_command_consumer.py` → `consumer.py`") if any; otherwise zero matches.

- [ ] **Step 7: Verify the full build and test pipeline**

```bash
cd /home/csillag/deai/optio
make build
make test
make test-interop
```
Expected: all green.

- [ ] **Step 8: Commit**

```bash
git add packages/optio-core/README.md
git add packages/optio-core/AGENTS.md
git add packages/optio-api/README.md
git add packages/optio-api/AGENTS.md
git add AGENTS.md
git add docs/2026-05-08-engine-rpc-migration-design.md
git commit -m "docs: phase-2 doc updates + parent-spec consumer.py rename

Documents optio_core.rpc_server, the new registerOptioApi return shape,
and the EngineClient sharing pattern. Aligns parent-spec filename
references with the actual on-disk name (consumer.py, not
_command_consumer.py).

Phase 2 of docs/2026-05-08-engine-rpc-migration-design.md."
```

---

## Final verification

After all 5 task commits land:

- [ ] **Step 1: Confirm git history**

```bash
cd /home/csillag/deai/optio
git log --oneline -10
```

Expected last 5 commits (most recent first):
```
docs: phase-2 doc updates + parent-spec consumer.py rename
feat(optio-demo): interop substrate for engine-RPC migration
feat(optio-api): wire engine-cache through all adapters; new return shape
feat(optio-api): add framework-agnostic engine-cache
feat(optio-core): add EngineService + RedisRpcServer lifecycle integration
```

- [ ] **Step 2: Final acceptance gates**

```bash
make build
make test
make test-interop
```

All green.

- [ ] **Step 3: Manual verification of co-existence**

After running `make test-interop`:
```bash
docker exec optio-demo-redis redis-cli xrange "optio-demo/optio:cmds:engine" - + COUNT 1
docker exec optio-demo-redis redis-cli xrange "optio-demo/optio:commands" - + COUNT 1
```

Both should show entries — proves both ingress paths are alive in phase 2.

- [ ] **Step 4: HTTP regression**

```bash
cd /home/csillag/deai/optio
pnpm --filter optio-api test
```

All adapter tests pass; the new return-shape assertions pass; existing HTTP behavior tests unaffected (HTTP still uses the legacy publishX path).

If everything is green, phase 2 is done. Phase 3 plan begins by switching the HTTP handlers from `publishX` to `engine.X` per-endpoint.

---

## Notes for the executing agent

- **Memory: editable Python installs are global.** Before running `pytest` from `packages/optio-core`, run `pip install -e .` from that directory in the same Python env. The phase-1 install may have been from a worktree.
- **Memory: don't use `npx`; use `node_modules/.bin/...` or pnpm scripts.**
- **Memory: MongoDB only via Docker.** Tests assume `docker compose up -d` from `packages/optio-demo` (already in `run-interop.sh`).
- **Don't touch HTTP handlers in this phase.** Any urge to also "wire engine.launch into the route handler while we're here" is phase 3 work. The whole point of the phased approach is per-phase reviewability; bleeding scope across phases breaks that.
- **No silent error swallow.** If the interop runner fails on any scenario, fix the root cause — do not relax assertions.
- **Status of memory record `feedback_no_auto_proceed`:** the current session has explicit user override ("no stops in between"). After this plan completes, default behavior resumes — ask before chaining further.
