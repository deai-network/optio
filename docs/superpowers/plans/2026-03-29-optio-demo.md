# optio-demo Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a `description` field to tasks across the stack, remove dead `propagation` code, then build optio-demo — a Python demo application exercising all optio-core features through whimsical task trees.

**Architecture:** Phase 0 adds one optional field (`description`) and removes dead code (`CancellationConfig.propagation`) across optio-core, optio-contracts, and optio-ui. Phase 1 creates a new `packages/optio-demo/` Python package with Docker Compose infrastructure and task definitions organized by theme.

**Tech Stack:** Python 3.11+ (optio-core, optio-demo), TypeScript (contracts, ui), React + Ant Design (ui), MongoDB 7, Redis 7, Docker Compose

**Spec:** `docs/superpowers/specs/2026-03-29-optio-demo-design.md`

---

## Phase 0: Add `description` field + remove `propagation` dead code

### Task 1: optio-core — flatten CancellationConfig, add description, update store

**Files:**
- Modify: `packages/optio-core/src/optio_core/models.py`
- Modify: `packages/optio-core/src/optio_core/store.py`
- Modify: `packages/optio-core/tests/test_models.py`

- [ ] **Step 1: Update models.py — remove CancellationConfig, flatten cancellable, add description**

Replace the entire `CancellationConfig` dataclass and update `TaskInstance`:

```python
"""Core data models for optio."""

from dataclasses import dataclass, field
from typing import Any, Callable, Awaitable
from datetime import datetime


@dataclass
class TaskInstance:
    """A unit of work provided by the application's task generator."""
    execute: Callable[..., Awaitable[None]]
    process_id: str
    name: str
    description: str | None = None
    params: dict[str, Any] = field(default_factory=dict)
    metadata: dict[str, Any] = field(default_factory=dict)
    schedule: str | None = None
    special: bool = False
    warning: str | None = None
    cancellable: bool = True


@dataclass
class ChildResult:
    """Result of a child process execution."""
    process_id: str
    state: str  # "done", "failed", "cancelled"
    error: str | None = None


@dataclass
class ChildProgressInfo:
    """Progress snapshot of a child process, delivered to parent callbacks."""
    process_id: str
    name: str
    state: str  # "scheduled", "running", "done", "failed", "cancelled"
    percent: float | None = None
    message: str | None = None


@dataclass
class ProcessStatus:
    """Runtime status of a process."""
    state: str = "idle"
    error: str | None = None
    running_since: datetime | None = None
    done_at: datetime | None = None
    duration: float | None = None
    failed_at: datetime | None = None
    stopped_at: datetime | None = None

    def to_dict(self) -> dict:
        return {
            "state": self.state,
            "error": self.error,
            "runningSince": self.running_since,
            "doneAt": self.done_at,
            "duration": self.duration,
            "failedAt": self.failed_at,
            "stoppedAt": self.stopped_at,
        }


@dataclass
class Progress:
    """Progress of a running process. percent=None means indeterminate."""
    percent: float | None = 0.0
    message: str | None = None

    def to_dict(self) -> dict:
        return {"percent": self.percent, "message": self.message}


@dataclass
class OptioConfig:
    """Configuration for optio initialization."""
    mongo_db: Any  # motor AsyncIOMotorDatabase
    prefix: str = "optio"
    redis_url: str | None = None
    services: dict[str, Any] = field(default_factory=dict)
    get_task_definitions: Callable[..., Awaitable[list[TaskInstance]]] | None = None
```

- [ ] **Step 2: Update store.py — add description to upsert and create_child**

In `upsert_process`, add `"description"` to the `$set` dict:

```python
        {
            "$set": {
                "processId": task.process_id,
                "name": task.name,
                "description": task.description,
                "params": task.params,
                "metadata": task.metadata,
                "cancellable": task.cancellable,
                "special": task.special,
                "warning": task.warning,
            },
```

In `create_child_process`, add `description` parameter and include it in the doc:

```python
async def create_child_process(
    db: AsyncIOMotorDatabase,
    prefix: str,
    parent_oid: ObjectId,
    root_oid: ObjectId,
    process_id: str,
    name: str,
    params: dict,
    depth: int,
    order: int,
    cancellable: bool = True,
    initial_state: str = "idle",
    metadata: dict | None = None,
    adhoc: bool = False,
    ephemeral: bool = False,
    description: str | None = None,
) -> dict:
    """Create a child process record."""
    coll = _collection(db, prefix)
    now = datetime.now(timezone.utc)
    doc = {
        "processId": process_id,
        "name": name,
        "description": description,
        "params": params,
        "metadata": metadata or {},
        "parentId": parent_oid,
        "rootId": root_oid,
        "depth": depth,
        "order": order,
        "cancellable": cancellable,
        "adhoc": adhoc,
        "ephemeral": ephemeral,
        "special": False,
        "warning": None,
        "status": ProcessStatus(state=initial_state).to_dict(),
        "progress": Progress().to_dict(),
        "log": [],
        "createdAt": now,
    }
    result = await coll.insert_one(doc)
    doc["_id"] = result.inserted_id
    return doc
```

- [ ] **Step 3: Fix all references to `task.cancellation.cancellable` → `task.cancellable`**

In `store.py` line 32, change:
```python
                "cancellable": task.cancellation.cancellable,
```
to:
```python
                "cancellable": task.cancellable,
```

(This was already shown in the updated `$set` block above, but verify it's the only reference.)

- [ ] **Step 4: Update __init__.py exports — remove CancellationConfig**

In `packages/optio-core/src/optio_core/__init__.py`, remove `CancellationConfig` from any exports if present. Check the current imports:

```python
from optio_core.models import TaskInstance, ChildResult
```

No change needed — `CancellationConfig` wasn't exported.

- [ ] **Step 5: Update test_models.py**

```python
"""Tests for data models."""

from optio_core.models import (
    TaskInstance, ChildResult, ProcessStatus, Progress,
)


async def dummy_execute(ctx):
    pass


def test_task_instance_defaults():
    task = TaskInstance(execute=dummy_execute, process_id="test", name="Test Task")
    assert task.process_id == "test"
    assert task.name == "Test Task"
    assert task.description is None
    assert task.params == {}
    assert task.schedule is None
    assert task.special is False
    assert task.warning is None
    assert task.cancellable is True


def test_task_instance_with_description():
    task = TaskInstance(
        execute=dummy_execute, process_id="test", name="Test",
        description="A detailed description",
    )
    assert task.description == "A detailed description"


def test_process_status_to_dict():
    status = ProcessStatus(state="running")
    d = status.to_dict()
    assert d["state"] == "running"
    assert d["error"] is None


def test_progress_to_dict():
    progress = Progress(percent=42.5, message="Working...")
    d = progress.to_dict()
    assert d["percent"] == 42.5
    assert d["message"] == "Working..."


def test_child_result():
    result = ChildResult(process_id="child_1", state="done")
    assert result.state == "done"
    assert result.error is None


def test_child_result_failed():
    result = ChildResult(process_id="child_1", state="failed", error="boom")
    assert result.error == "boom"
```

- [ ] **Step 6: Fix any other references to CancellationConfig in tests**

In `test_no_redis.py`, replace:
```python
from optio_core.models import TaskInstance, CancellationConfig
```
with:
```python
from optio_core.models import TaskInstance
```

And replace:
```python
                     cancellation=CancellationConfig(cancellable=True)),
```
with:
```python
                     cancellable=True),
```

- [ ] **Step 7: Run optio-core tests**

Run: `cd packages/optio-core && python -m pytest tests/test_models.py tests/test_no_redis.py -v`
Expected: All tests pass.

- [ ] **Step 8: Commit**

```bash
git add packages/optio-core/
git commit -m "feat(optio-core): add description field, flatten cancellable, remove dead propagation code"
```

---

### Task 2: optio-core — add description to run_child, spawn, execute_child

**Files:**
- Modify: `packages/optio-core/src/optio_core/context.py`
- Modify: `packages/optio-core/src/optio_core/executor.py`

- [ ] **Step 1: Update run_child() in context.py**

Add `description: str | None = None` parameter:

```python
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
```

- [ ] **Step 2: Update ParallelGroup.spawn() in context.py**

Add `description: str | None = None` parameter:

```python
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
```

- [ ] **Step 3: Update execute_child() in executor.py**

Add `description: str | None = None` parameter and pass it to `create_child_process`:

```python
    async def execute_child(
        self,
        parent_ctx: ProcessContext,
        execute: Callable[..., Awaitable[None]],
        process_id: str,
        name: str,
        params: dict,
        survive_failure: bool = False,
        survive_cancel: bool = False,
        description: str | None = None,
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
            metadata=parent_ctx.metadata,
            description=description,
        )
```

The rest of `execute_child` remains unchanged.

- [ ] **Step 4: Run all optio-core tests**

Run: `cd packages/optio-core && python -m pytest tests/ -v`
Expected: All tests pass (description is optional with default None, so existing tests don't break).

- [ ] **Step 5: Commit**

```bash
git add packages/optio-core/
git commit -m "feat(optio-core): add description parameter to run_child, spawn, execute_child"
```

---

### Task 3: optio-contracts — add description to process schema

**Files:**
- Modify: `packages/optio-contracts/src/schemas/process.ts`

- [ ] **Step 1: Add description field to ProcessSchema**

After `name: z.string(),` add:

```typescript
  description: z.string().nullable().optional(),
```

The full "Definition metadata" section becomes:

```typescript
  // Definition metadata
  cancellable: z.boolean(),
  special: z.boolean().optional(),
  warning: z.string().optional(),
  description: z.string().nullable().optional(),
```

- [ ] **Step 2: Build contracts**

Run: `cd packages/optio-contracts && node_modules/.bin/tsc --noEmit`
Expected: No errors.

- [ ] **Step 3: Commit**

```bash
git add packages/optio-contracts/
git commit -m "feat(optio-contracts): add description field to process schema"
```

---

### Task 4: optio-ui — show description in list tooltip and tree view

**Files:**
- Modify: `packages/optio-ui/src/components/ProcessList.tsx`
- Modify: `packages/optio-ui/src/components/ProcessTreeView.tsx`

- [ ] **Step 1: Add description tooltip to ProcessItem in ProcessList.tsx**

Wrap the `nameElement` in a `Tooltip` when `process.description` exists. Replace the `nameElement` definition and usage:

Current code (lines 42-48):
```typescript
  const nameElement = onProcessClick ? (
    <Button type="link" style={{ padding: 0, height: 'auto' }} onClick={() => onProcessClick(process._id)}>
      {process.name}
    </Button>
  ) : (
    <Text>{process.name}</Text>
  );
```

Replace with:
```typescript
  const nameContent = onProcessClick ? (
    <Button type="link" style={{ padding: 0, height: 'auto' }} onClick={() => onProcessClick(process._id)}>
      {process.name}
    </Button>
  ) : (
    <Text>{process.name}</Text>
  );

  const nameElement = process.description ? (
    <Tooltip title={process.description}>{nameContent}</Tooltip>
  ) : nameContent;
```

- [ ] **Step 2: Add description tooltip to tree nodes in ProcessTreeView.tsx**

In the `treeNodeToDataNode` function, wrap the name `Text` in a `Tooltip` when description exists. Replace the name line:

Current code (line 61):
```typescript
        <Text style={{ whiteSpace: 'nowrap' }}>{node.name}</Text>
```

Replace with:
```typescript
        {node.description ? (
          <Tooltip title={node.description}>
            <Text style={{ whiteSpace: 'nowrap' }}>{node.name}</Text>
          </Tooltip>
        ) : (
          <Text style={{ whiteSpace: 'nowrap' }}>{node.name}</Text>
        )}
```

Also add `description` to the `ProcessNode` interface:

```typescript
interface ProcessNode {
  _id: string;
  name: string;
  description?: string | null;
  status: { state: string; error?: string; runningSince?: string };
  progress: { percent: number | null; message?: string };
  cancellable?: boolean;
  children?: ProcessNode[];
}
```

- [ ] **Step 3: Build UI**

Run: `cd packages/optio-ui && node_modules/.bin/tsc --noEmit`
Expected: No errors.

- [ ] **Step 4: Commit**

```bash
git add packages/optio-ui/
git commit -m "feat(optio-ui): show description as tooltip on process name"
```

---

### Task 5: Update AGENTS.md and README files for Phase 0

**Files:**
- Modify: `AGENTS.md` (root)
- Modify: `packages/optio-core/AGENTS.md`
- Modify: `packages/optio-core/README.md`
- Modify: `packages/optio-contracts/AGENTS.md`
- Modify: `packages/optio-ui/AGENTS.md`

- [ ] **Step 1: Update all documentation**

This is a documentation-only task. For each file:

**Root AGENTS.md**:
- In the TaskInstance section, replace `CancellationConfig` with flattened `cancellable: bool = True`. Add `description: str | None = None`. Remove the `CancellationConfig` class definition and its table.
- In the MongoDB process document schema table, add `description` field row.
- In the TypeScript process schema section, add `description`.
- In the SSE payload shapes, add `description` where `name` appears.
- In the ProcessNode interface, add `description`.

**optio-core AGENTS.md and README.md**:
- Update TaskInstance definition and field table: remove `cancellation: CancellationConfig`, add `cancellable: bool = True` and `description: str | None = None`.
- Remove `CancellationConfig` class and table.
- Update `run_child()` signature to include `description` parameter.
- Update `parallel_group().spawn()` signature to include `description` parameter.

**optio-contracts AGENTS.md**:
- Add `description` to ProcessSchema field table.

**optio-ui AGENTS.md**:
- Note that `description` is shown as tooltip on process names in both list and tree views.
- Add `description` to ProcessNode interface.

- [ ] **Step 2: Commit**

```bash
git add AGENTS.md packages/optio-core/AGENTS.md packages/optio-core/README.md packages/optio-contracts/AGENTS.md packages/optio-ui/AGENTS.md
git commit -m "docs: update AGENTS.md and README for description field and propagation removal"
```

---

## Phase 1: The Demo Application

### Task 6: Scaffold optio-demo package

**Files:**
- Create: `packages/optio-demo/pyproject.toml`
- Create: `packages/optio-demo/docker-compose.yml`
- Create: `packages/optio-demo/Makefile`
- Create: `packages/optio-demo/src/optio_demo/__init__.py`
- Create: `packages/optio-demo/src/optio_demo/__main__.py`
- Create: `packages/optio-demo/src/optio_demo/tasks/__init__.py`
- Create stub files for all task modules

- [ ] **Step 1: Create pyproject.toml**

```toml
[build-system]
requires = ["setuptools>=68.0", "wheel"]
build-backend = "setuptools.build_meta"

[project]
name = "optio-demo"
version = "0.1.0"
description = "Demo application exercising all optio-core features"
license = "Apache-2.0"
requires-python = ">=3.11"
dependencies = [
    "optio-core[redis]",
]

[tool.setuptools.packages.find]
where = ["src"]
```

- [ ] **Step 2: Create docker-compose.yml**

```yaml
services:
  mongodb:
    image: mongo:7
    ports:
      - "27017:27017"

  redis:
    image: redis:7
    ports:
      - "6379:6379"
```

- [ ] **Step 3: Create Makefile**

```makefile
.PHONY: install run run-dashboard

install:
	docker compose up -d
	pip install -e ../optio-core[redis]
	pip install -e .

run:
	python -m optio_demo

run-dashboard:
	MONGODB_URL=mongodb://localhost:27017/optio-demo npx optio-dashboard
```

- [ ] **Step 4: Create src/optio_demo/__init__.py**

```python
```

(Empty file — just marks the package.)

- [ ] **Step 5: Create src/optio_demo/__main__.py**

```python
"""Optio demo application — exercises all optio-core features."""

import asyncio
import os
import logging

from motor.motor_asyncio import AsyncIOMotorClient
from optio_core.lifecycle import Optio

from optio_demo.tasks import get_task_definitions

logging.basicConfig(level=logging.INFO)


async def main():
    mongo_url = os.environ.get("MONGODB_URL", "mongodb://localhost:27017/optio-demo")
    redis_url = os.environ.get("REDIS_URL", "redis://localhost:6379")
    prefix = os.environ.get("OPTIO_PREFIX", "optio")

    db_name = mongo_url.rsplit("/", 1)[-1]
    client = AsyncIOMotorClient(mongo_url)
    db = client[db_name]

    fw = Optio()
    await fw.init(
        mongo_db=db,
        prefix=prefix,
        redis_url=redis_url,
        services={"optio": fw},
        get_task_definitions=get_task_definitions,
    )

    await fw.run()


if __name__ == "__main__":
    asyncio.run(main())
```

- [ ] **Step 6: Create src/optio_demo/tasks/__init__.py**

```python
"""Task definitions for the optio demo application."""

from optio_core.models import TaskInstance

from optio_demo.tasks.terraforming import get_tasks as terraforming_tasks
from optio_demo.tasks.home import get_tasks as home_tasks
from optio_demo.tasks.heist import get_tasks as heist_tasks
from optio_demo.tasks.festival import get_tasks as festival_tasks
from optio_demo.tasks.wakeup import get_tasks as wakeup_tasks


async def get_task_definitions(services: dict) -> list[TaskInstance]:
    return [
        *terraforming_tasks(),
        *home_tasks(),
        *heist_tasks(),
        *festival_tasks(),
        *wakeup_tasks(),
    ]
```

- [ ] **Step 7: Create stub files**

Each stub follows this pattern:

`src/optio_demo/tasks/terraforming.py`:
```python
"""Terraforming Mars — the big showcase task tree."""
from optio_core.models import TaskInstance

def get_tasks() -> list[TaskInstance]:
    return []
```

`src/optio_demo/tasks/home.py`:
```python
"""Organizing Your Home — mixed seq/parallel, error handling, non-cancellable."""
from optio_core.models import TaskInstance

def get_tasks() -> list[TaskInstance]:
    return []
```

`src/optio_demo/tasks/heist.py`:
```python
"""The Great Museum Heist — parallel failure, cascading errors, warning."""
from optio_core.models import TaskInstance

def get_tasks() -> list[TaskInstance]:
    return []
```

`src/optio_demo/tasks/festival.py`:
```python
"""Intergalactic Music Festival — generated tasks from template."""
from optio_core.models import TaskInstance

def get_tasks() -> list[TaskInstance]:
    return []
```

`src/optio_demo/tasks/wakeup.py`:
```python
"""Your 15-min Wake-up Call — cron scheduled task."""
from optio_core.models import TaskInstance

def get_tasks() -> list[TaskInstance]:
    return []
```

- [ ] **Step 8: Verify the app starts**

Run: `cd packages/optio-demo && make install && make run`
Expected: App starts, logs "Optio initialized", waits for commands. Press Ctrl+C to stop.

- [ ] **Step 9: Commit**

```bash
git add packages/optio-demo/
git commit -m "feat: scaffold optio-demo package with infrastructure"
```

---

### Task 7: Terraforming Mars

**Files:**
- Modify: `packages/optio-demo/src/optio_demo/tasks/terraforming.py`

This is the big showcase (~30 min total). 4-level task tree with all three progress helpers, parallel groups, survive_failure, cooperative cancellation, and metadata inheritance.

- [ ] **Step 1: Write terraforming.py**

```python
"""Terraforming Mars — the big showcase task tree.

Exercises: sequential_progress, average_progress, mapped_progress,
parallel groups with max_concurrency, 4-level nesting, survive_failure,
cooperative cancellation cascade, metadata inheritance, descriptions.
~30 minutes total runtime.
"""

import asyncio
from optio_core.models import TaskInstance
from optio_core.progress_helpers import sequential_progress, average_progress, mapped_progress


# ---------------------------------------------------------------------------
# Leaf-level helpers
# ---------------------------------------------------------------------------

async def _timed_work(ctx, steps: int, delay: float, messages: list[str] | None = None):
    """Generic worker that reports progress over `steps` iterations."""
    for i in range(steps):
        if not ctx.should_continue():
            return
        pct = (i + 1) / steps * 100
        msg = messages[i] if messages and i < len(messages) else f"Step {i + 1}/{steps}"
        ctx.report_progress(pct, msg)
        await asyncio.sleep(delay)


# ---------------------------------------------------------------------------
# Phase 1: Survey the Planet (~5 min)
# ---------------------------------------------------------------------------

async def _map_geology(ctx):
    sectors = 12
    for i in range(sectors):
        if not ctx.should_continue():
            return
        ctx.report_progress((i + 1) / sectors * 100, f"Sector {i + 1}/{sectors} mapped — found basalt formations")
        await asyncio.sleep(5)


async def _analyze_atmosphere(ctx):
    gases = [
        ("CO2", "95.3%"), ("N2", "2.7%"), ("Ar", "1.6%"),
        ("O2", "0.13%"), ("CO", "0.07%"), ("H2O", "0.03%"),
    ]
    for i, (gas, pct) in enumerate(gases):
        if not ctx.should_continue():
            return
        ctx.report_progress((i + 1) / len(gases) * 100, f"Measuring {gas}: {pct} of atmosphere")
        await asyncio.sleep(7)


async def _detect_water(ctx):
    layers = ["Surface scan", "10m depth", "50m depth", "200m depth", "1km depth — aquifer detected!"]
    for i, layer in enumerate(layers):
        if not ctx.should_continue():
            return
        ctx.report_progress((i + 1) / len(layers) * 100, layer)
        await asyncio.sleep(8)


async def _catalog_minerals(ctx):
    regions = [
        "Olympus Mons slope", "Valles Marineris floor", "Hellas Basin",
        "Utopia Planitia", "Jezero Crater", "Syrtis Major",
    ]
    for i, region in enumerate(regions):
        if not ctx.should_continue():
            return
        ctx.report_progress((i + 1) / len(regions) * 100, f"{region}: iron oxide, silicates, perchlorates")
        await asyncio.sleep(6)


async def _survey_phase(ctx):
    cb = sequential_progress(ctx, 4)
    await ctx.run_child(_map_geology, "tf-survey-geology", "Mapping Geological Structures",
                        description="Orbital and ground-based geological survey of 12 sectors.",
                        on_child_progress=cb)
    await ctx.run_child(_analyze_atmosphere, "tf-survey-atmo", "Analyzing Atmospheric Composition",
                        description="Mass spectrometry analysis of atmospheric gases.",
                        on_child_progress=cb)
    await ctx.run_child(_detect_water, "tf-survey-water", "Detecting Subsurface Water",
                        description="Ground-penetrating radar sweep to 1km depth.",
                        on_child_progress=cb)
    await ctx.run_child(_catalog_minerals, "tf-survey-minerals", "Cataloging Mineral Deposits",
                        description="Resource assessment across 6 key regions.",
                        on_child_progress=cb)


# ---------------------------------------------------------------------------
# Phase 2: Build Infrastructure (~10 min)
# ---------------------------------------------------------------------------

async def _build_habitat_domes(ctx):
    """Builds 6 domes sequentially — nested children."""
    cb = sequential_progress(ctx, 6)
    for i in range(6):
        if not ctx.should_continue():
            return

        async def _build_one_dome(dome_ctx, dome_num=i + 1):
            stages = ["Foundation", "Frame assembly", "Pressure seal", "Life support install"]
            for j, stage in enumerate(stages):
                if not dome_ctx.should_continue():
                    return
                dome_ctx.report_progress((j + 1) / len(stages) * 100, f"Dome {dome_num}: {stage}")
                await asyncio.sleep(5)

        await ctx.run_child(_build_one_dome, f"tf-dome-{i+1}", f"Habitat Dome {i+1}",
                            description=f"Constructing dome {i+1} of 6 with full life support.",
                            on_child_progress=cb)


async def _deploy_robots(ctx):
    """Deploys 10 mining robots in parallel — nested parallel group."""
    cb = average_progress(ctx)
    async with ctx.parallel_group(max_concurrency=5, on_child_progress=cb) as group:
        for i in range(10):
            async def _init_robot(r_ctx, robot_id=i + 1):
                steps = ["Unpacking", "Calibrating sensors", "Test drill", "Deploying to sector"]
                for j, step in enumerate(steps):
                    if not r_ctx.should_continue():
                        return
                    r_ctx.report_progress((j + 1) / len(steps) * 100, f"Robot #{robot_id}: {step}")
                    await asyncio.sleep(3)

            await group.spawn(_init_robot, f"tf-robot-{i+1}", f"Mining Robot #{i+1}",
                              description=f"Self-replicating mining robot #{i+1}. Deploys to assigned sector.")


async def _install_power_grid(ctx):
    cb = sequential_progress(ctx, 3)

    async def _solar_panels(s_ctx):
        for i in range(8):
            if not s_ctx.should_continue():
                return
            s_ctx.report_progress((i + 1) / 8 * 100, f"Solar array {i + 1}/8 deployed")
            await asyncio.sleep(5)

    async def _nuclear_reactor(n_ctx):
        stages = ["Excavating containment pit", "Assembling reactor core", "Loading fuel rods",
                  "Connecting coolant loops", "Running safety checks", "Reactor online!"]
        for i, stage in enumerate(stages):
            if not n_ctx.should_continue():
                return
            n_ctx.report_progress((i + 1) / len(stages) * 100, stage)
            await asyncio.sleep(6)

    async def _grid_connection(g_ctx):
        await _timed_work(g_ctx, 5, 4, [
            "Running main trunk line", "Connecting habitat domes",
            "Connecting spaceport", "Connecting mining stations", "Grid test — all green!",
        ])

    await ctx.run_child(_solar_panels, "tf-solar", "Solar Panel Arrays",
                        description="8 solar arrays providing backup power.", on_child_progress=cb)
    await ctx.run_child(_nuclear_reactor, "tf-reactor", "Nuclear Reactor",
                        description="Primary power source. 6-stage construction.", on_child_progress=cb)
    await ctx.run_child(_grid_connection, "tf-grid", "Grid Connection",
                        description="Connecting all infrastructure to the power grid.", on_child_progress=cb)


async def _comms_array(ctx):
    await _timed_work(ctx, 6, 5, [
        "Erecting antenna tower", "Installing dish array", "Calibrating deep-space link",
        "Establishing Mars-Earth relay", "Testing bandwidth", "Communications online!",
    ])


async def _build_spaceport(ctx):
    await _timed_work(ctx, 8, 4, [
        "Grading landing pad", "Pouring heat-resistant surface", "Building control tower",
        "Installing fuel depot", "Setting up cargo handling", "Painting runway markings",
        "Testing landing guidance", "Spaceport operational!",
    ])


async def _fight_aliens(ctx):
    """Always fails — exercises survive_failure."""
    ctx.report_progress(10, "Detecting alien signals...")
    await asyncio.sleep(3)
    ctx.report_progress(30, "Alien warship entering orbit!")
    await asyncio.sleep(3)
    ctx.report_progress(50, "Activating defense grid...")
    await asyncio.sleep(3)
    raise Exception("Defense grid overwhelmed! Alien invaders too powerful! (This failure is intentional — testing survive_failure)")


async def _infrastructure_phase(ctx):
    cb = average_progress(ctx)
    async with ctx.parallel_group(max_concurrency=3, survive_failure=True, on_child_progress=cb) as group:
        await group.spawn(_build_habitat_domes, "tf-infra-domes", "Constructing Habitat Domes",
                          description="6 pressurized domes with full life support. Sequential nested children.")
        await group.spawn(_deploy_robots, "tf-infra-robots", "Deploying Mining Robot Swarm",
                          description="10 robots deployed in parallel (max 5 concurrent). Nested parallel group.")
        await group.spawn(_install_power_grid, "tf-infra-power", "Installing Power Grid",
                          description="Solar + nuclear + grid connection. Sequential nested children.")
        await group.spawn(_comms_array, "tf-infra-comms", "Establishing Communications Array",
                          description="Deep-space communication link to Earth.")
        await group.spawn(_build_spaceport, "tf-infra-spaceport", "Building Spaceport",
                          description="Landing facilities and cargo handling.")
        await group.spawn(_fight_aliens, "tf-infra-aliens", "Fighting Off Alien Invaders",
                          description="This task always fails. Exercises survive_failure — parent continues despite this failure.")

    # Log which children failed
    failed = [r for r in group.results if r.state != "done"]
    if failed:
        ctx.report_progress(None, f"Infrastructure complete with {len(failed)} issue(s): {', '.join(r.process_id for r in failed)}")


# ---------------------------------------------------------------------------
# Phase 3: Terraform (~15 min)
# ---------------------------------------------------------------------------

async def _atmosphere_processing(ctx):
    """Deep nesting: atmosphere -> gas injection -> individual gases."""
    cb = sequential_progress(ctx, 3)

    async def _gas_injection(g_ctx):
        gcb = sequential_progress(g_ctx, 4)

        async def _inject_gas(ig_ctx, gas_name, duration):
            steps = int(duration / 3)
            for i in range(steps):
                if not ig_ctx.should_continue():
                    return
                ig_ctx.report_progress((i + 1) / steps * 100, f"Injecting {gas_name}: {(i + 1) * 100 // steps}% of target volume")
                await asyncio.sleep(3)

        await g_ctx.run_child(lambda c: _inject_gas(c, "O2", 30), "tf-gas-o2", "Oxygen Generation",
                              description="Electrolyzing water ice to produce O2.", on_child_progress=gcb)
        await g_ctx.run_child(lambda c: _inject_gas(c, "N2", 24), "tf-gas-n2", "Nitrogen Release",
                              description="Heating nitrate minerals to release N2.", on_child_progress=gcb)
        await g_ctx.run_child(lambda c: _inject_gas(c, "CO2 conversion", 21), "tf-gas-co2", "CO2 Conversion",
                              description="Converting excess CO2 to O2 via catalytic process.", on_child_progress=gcb)
        await g_ctx.run_child(lambda c: _inject_gas(c, "Water vapor", 18), "tf-gas-h2o", "Water Vapor Injection",
                              description="Sublimating polar ice for greenhouse effect.", on_child_progress=gcb)

    async def _pressure_monitoring(p_ctx):
        for i in range(10):
            if not p_ctx.should_continue():
                return
            pressure = 6.1 + i * 5  # millibars
            p_ctx.report_progress((i + 1) / 10 * 100, f"Atmospheric pressure: {pressure:.1f} mbar (target: 56 mbar)")
            await asyncio.sleep(6)

    async def _ozone_layer(o_ctx):
        await _timed_work(o_ctx, 8, 5, [
            "Deploying ozone generators to stratosphere", "UV catalysis initiated",
            "Ozone layer forming at 25km", "Coverage: 25%", "Coverage: 50%",
            "Coverage: 75%", "Coverage: 95%", "Ozone layer stable — UV shielding active!",
        ])

    await ctx.run_child(_gas_injection, "tf-atmo-inject", "Gas Injection Sequence",
                        description="4-gas injection pipeline. 4th-level nesting.", on_child_progress=cb)
    await ctx.run_child(_pressure_monitoring, "tf-atmo-pressure", "Atmospheric Pressure Monitoring",
                        description="Tracking pressure rise toward habitable levels.", on_child_progress=cb)
    await ctx.run_child(_ozone_layer, "tf-atmo-ozone", "Ozone Layer Formation",
                        description="UV shielding via stratospheric ozone generators.", on_child_progress=cb)


async def _temperature_regulation(ctx):
    cb_mirrors = mapped_progress(ctx, 0.0, 0.5)
    cb_thermal = mapped_progress(ctx, 0.5, 1.0)

    async def _orbital_mirrors(m_ctx):
        m_cb = average_progress(m_ctx)
        async with m_ctx.parallel_group(max_concurrency=3, on_child_progress=m_cb) as group:
            for i in range(6):
                async def _deploy_mirror(dm_ctx, mirror_id=i + 1):
                    await _timed_work(dm_ctx, 4, 5, [
                        f"Mirror {mirror_id}: Launching to orbit",
                        f"Mirror {mirror_id}: Unfolding reflective surface",
                        f"Mirror {mirror_id}: Aligning focus point",
                        f"Mirror {mirror_id}: Operational — warming sector {mirror_id}",
                    ])
                await group.spawn(_deploy_mirror, f"tf-mirror-{i+1}", f"Orbital Mirror {i+1}",
                                  description=f"Solar reflector #{i+1}. Focuses sunlight on polar regions.")

    async def _thermal_generators(t_ctx):
        t_cb = sequential_progress(t_ctx, 4)
        for i in range(4):
            async def _build_gen(bg_ctx, gen_id=i + 1):
                await _timed_work(bg_ctx, 5, 4, [
                    f"Generator {gen_id}: Drilling geothermal well",
                    f"Generator {gen_id}: Installing heat exchanger",
                    f"Generator {gen_id}: Connecting to grid",
                    f"Generator {gen_id}: Warming surface zone",
                    f"Generator {gen_id}: Target temperature reached!",
                ])
            await t_ctx.run_child(_build_gen, f"tf-therm-{i+1}", f"Thermal Generator {i+1}",
                                  description=f"Geothermal generator #{i+1}. Sequential build.",
                                  on_child_progress=t_cb)

    await ctx.run_child(_orbital_mirrors, "tf-temp-mirrors", "Deploying Orbital Mirrors",
                        description="6 mirrors in parallel (max 3 concurrent). Uses average_progress.",
                        on_child_progress=cb_mirrors)
    await ctx.run_child(_thermal_generators, "tf-temp-thermal", "Activating Thermal Generators",
                        description="4 geothermal generators built sequentially.",
                        on_child_progress=cb_thermal)


async def _ecosystem_seeding(ctx):
    """4-level deep: seeding -> microbes -> plants -> animals."""
    cb = sequential_progress(ctx, 3)

    async def _microbe_deployment(m_ctx):
        microbes = ["Cyanobacteria", "Nitrogen-fixing bacteria", "Extremophile archaea",
                    "Soil-building fungi", "Methanotrophs"]
        for i, microbe in enumerate(microbes):
            if not m_ctx.should_continue():
                return
            m_ctx.report_progress((i + 1) / len(microbes) * 100, f"Seeding {microbe} colonies")
            await asyncio.sleep(6)

    async def _plant_introduction(p_ctx):
        plants = ["Hardy lichens", "Moss varieties", "Tundra grasses",
                  "Engineered shrubs", "Pine seedlings", "Flowering plants"]
        for i, plant in enumerate(plants):
            if not p_ctx.should_continue():
                return
            p_ctx.report_progress((i + 1) / len(plants) * 100, f"Planting {plant} — survival rate: {70 + i * 5}%")
            await asyncio.sleep(5)

    async def _animal_release(a_ctx):
        animals = ["Tardigrades (soil fauna)", "Earthworms", "Pollinating insects",
                   "Small birds", "Rabbits (controlled)", "Hardy goats"]
        for i, animal in enumerate(animals):
            if not a_ctx.should_continue():
                return
            a_ctx.report_progress((i + 1) / len(animals) * 100, f"Releasing {animal} into habitat zones")
            await asyncio.sleep(7)

    await ctx.run_child(_microbe_deployment, "tf-eco-microbes", "Microbe Deployment",
                        description="Foundation organisms for soil building.", on_child_progress=cb)
    await ctx.run_child(_plant_introduction, "tf-eco-plants", "Plant Introduction",
                        description="Progressive plant varieties from hardy to flowering.", on_child_progress=cb)
    await ctx.run_child(_animal_release, "tf-eco-animals", "Animal Release",
                        description="Fauna introduction from soil organisms to mammals.", on_child_progress=cb)


async def _terraform_phase(ctx):
    cb_atmo = mapped_progress(ctx, 0.0, 0.4)
    cb_temp = mapped_progress(ctx, 0.4, 0.7)
    cb_eco = mapped_progress(ctx, 0.7, 1.0)

    await ctx.run_child(_atmosphere_processing, "tf-terra-atmo", "Atmosphere Processing",
                        description="Gas injection, pressure monitoring, ozone formation. Deepest nesting (4 levels).",
                        on_child_progress=cb_atmo)
    await ctx.run_child(_temperature_regulation, "tf-terra-temp", "Temperature Regulation",
                        description="Orbital mirrors (parallel) + thermal generators (sequential). Uses mapped_progress.",
                        on_child_progress=cb_temp)
    await ctx.run_child(_ecosystem_seeding, "tf-terra-eco", "Ecosystem Seeding",
                        description="Microbes -> plants -> animals. Progressive biosphere.",
                        on_child_progress=cb_eco)


# ---------------------------------------------------------------------------
# Top-level task
# ---------------------------------------------------------------------------

async def _terraforming_mars(ctx):
    cb = sequential_progress(ctx, 3)

    ctx.report_progress(0, "Initiating Mars terraforming sequence...")
    await ctx.run_child(_survey_phase, "tf-phase-survey", "Phase 1: Survey the Planet",
                        description="Geological, atmospheric, hydrological, and mineral surveys. ~5 minutes.",
                        on_child_progress=cb)

    await ctx.run_child(_infrastructure_phase, "tf-phase-infra", "Phase 2: Build Infrastructure",
                        description="Parallel construction (max 3 concurrent). Includes intentional failure (aliens). ~10 minutes.",
                        on_child_progress=cb)

    await ctx.run_child(_terraform_phase, "tf-phase-terraform", "Phase 3: Terraform",
                        description="Atmosphere, temperature, and ecosystem transformation. Deepest nesting. ~15 minutes.",
                        on_child_progress=cb)

    ctx.report_progress(100, "Mars terraforming complete! Welcome to New Earth.")


def get_tasks() -> list[TaskInstance]:
    return [
        TaskInstance(
            execute=_terraforming_mars,
            process_id="terraforming-mars",
            name="Terraforming Mars",
            description=(
                "The full terraforming pipeline: survey, build, terraform. "
                "Exercises deep nesting (4 levels), all three progress helpers, "
                "cooperative cancellation, survive_failure, parallel groups with "
                "max_concurrency, and metadata inheritance. ~30 minutes."
            ),
            metadata={"planet": "mars", "mission_type": "terraforming", "priority": "critical"},
        ),
    ]
```

- [ ] **Step 2: Verify the app starts with the new task**

Run: `cd packages/optio-demo && make run`
Expected: App starts, shows "Synced 1 task definitions". Ctrl+C to stop.

- [ ] **Step 3: Commit**

```bash
git add packages/optio-demo/src/optio_demo/tasks/terraforming.py
git commit -m "feat(optio-demo): add Terraforming Mars task tree"
```

---

### Task 8: Organizing Your Home

**Files:**
- Modify: `packages/optio-demo/src/optio_demo/tasks/home.py`

- [ ] **Step 1: Write home.py**

```python
"""Organizing Your Home — mixed seq/parallel, error handling, non-cancellable.

Exercises: mixed sequential/parallel children, survive_failure in parallel group,
cancellable=False, indeterminate progress, cancellation ignored. ~10 minutes.
"""

import asyncio
from optio_core.models import TaskInstance
from optio_core.progress_helpers import sequential_progress, average_progress


# ---------------------------------------------------------------------------
# Phase 1: Cleaning Up Your Mess (~3 min)
# ---------------------------------------------------------------------------

async def _collect_socks(ctx):
    rooms = ["Living room", "Bedroom", "Bathroom", "Kitchen (why?!)",
             "Under the bed", "Behind the TV", "Inside the couch cushions"]
    for i, room in enumerate(rooms):
        if not ctx.should_continue():
            return
        ctx.report_progress((i + 1) / len(rooms) * 100, f"{room}: found {2 + i} socks")
        await asyncio.sleep(5)


async def _wash_dishes(ctx):
    batches = ["Coffee mugs (7)", "Plates from last night (4)", "Mystery Tupperware (3)",
               "The pot you've been 'soaking' for 3 days", "Wine glasses (careful!)"]
    for i, batch in enumerate(batches):
        if not ctx.should_continue():
            return
        ctx.report_progress((i + 1) / len(batches) * 100, f"Washing: {batch}")
        await asyncio.sleep(6)


async def _vacuum_couch(ctx):
    findings = [
        "Vacuuming cushion 1... found: 3 coins, 1 pen",
        "Vacuuming cushion 2... found: TV remote! (missing since Tuesday)",
        "Vacuuming cushion 3... found: ancient popcorn civilization",
        "Under the couch: dust bunnies the size of actual bunnies",
        "Behind the couch: the other sock! And a pizza box from... let's not discuss",
    ]
    for i, finding in enumerate(findings):
        if not ctx.should_continue():
            return
        ctx.report_progress((i + 1) / len(findings) * 100, finding)
        await asyncio.sleep(7)


async def _cleaning_phase(ctx):
    cb = sequential_progress(ctx, 3)
    await ctx.run_child(_collect_socks, "home-clean-socks", "Collecting Scattered Socks",
                        description="Room-by-room sock recovery mission.",
                        on_child_progress=cb)
    await ctx.run_child(_wash_dishes, "home-clean-dishes", "Washing the Dishes",
                        description="Batch-by-batch dish assault.",
                        on_child_progress=cb)
    await ctx.run_child(_vacuum_couch, "home-clean-vacuum", "Vacuuming Under the Couch",
                        description="Archaeological expedition beneath the cushions.",
                        on_child_progress=cb)


# ---------------------------------------------------------------------------
# Phase 2: Triaging Your Clothes (~4 min)
# ---------------------------------------------------------------------------

async def _sort_shirts(ctx):
    colors = ["Whites", "Darks", "Colors", "The grey area (literally)",
              "Band t-shirts (sacred, do not fold)", "Work shirts"]
    for i, color in enumerate(colors):
        if not ctx.should_continue():
            return
        ctx.report_progress((i + 1) / len(colors) * 100, f"Sorting: {color}")
        await asyncio.sleep(5)


async def _fold_pants(ctx):
    types = ["Jeans (the easy ones)", "Dress pants (careful with the crease)",
             "Sweatpants (just roll them)", "Shorts (summer optimism)",
             "The pants that don't fit but you're keeping 'just in case'"]
    for i, ptype in enumerate(types):
        if not ctx.should_continue():
            return
        ctx.report_progress((i + 1) / len(types) * 100, f"Folding: {ptype}")
        await asyncio.sleep(6)


async def _decide_throwaway(ctx):
    """Always fails — cannot decide what to throw away."""
    items = ["That hoodie from 2015", "The shirt with the stain you can't identify"]
    for i, item in enumerate(items):
        ctx.report_progress((i + 1) / len(items) * 50, f"Considering: {item}...")
        await asyncio.sleep(5)
    raise Exception("Cannot decide — emotional attachment too strong! (This failure is intentional — testing survive_failure)")


async def _iron_fancy(ctx):
    items = ["Interview shirt", "Date night blouse", "The linen pants (impossible)",
             "Tablecloth (why is this with the clothes?)"]
    for i, item in enumerate(items):
        if not ctx.should_continue():
            return
        ctx.report_progress((i + 1) / len(items) * 100, f"Ironing: {item}")
        await asyncio.sleep(7)


async def _triage_phase(ctx):
    cb = average_progress(ctx)
    async with ctx.parallel_group(survive_failure=True, on_child_progress=cb) as group:
        await group.spawn(_sort_shirts, "home-triage-shirts", "Sorting Shirts by Color",
                          description="Color-coded organization system.")
        await group.spawn(_fold_pants, "home-triage-pants", "Folding Pants",
                          description="Various pants, various techniques.")
        await group.spawn(_decide_throwaway, "home-triage-decide", "Deciding What to Throw Away",
                          description="This task always fails. Emotional attachment wins every time. Tests survive_failure in parallel group.")
        await group.spawn(_iron_fancy, "home-triage-iron", "Ironing the Fancy Stuff",
                          description="The clothes you actually want to look good in.")

    failed = [r for r in group.results if r.state != "done"]
    if failed:
        ctx.report_progress(None, f"Triage mostly done. Couldn't complete: {', '.join(r.process_id for r in failed)}")


# ---------------------------------------------------------------------------
# Phase 3: Petting Your Cats (~3 min)
# None of these children check should_continue() — they ignore cancellation.
# ---------------------------------------------------------------------------

async def _locate_whiskers(ctx):
    """Indeterminate progress — cat location unknown."""
    locations = [
        "Checking behind the couch...", "Checking on top of the fridge...",
        "Checking inside the laundry basket...", "Checking the bathtub (why?)...",
        "Checking the neighbor's yard...", "Found Mr. Whiskers! (He was on the bookshelf the whole time)",
    ]
    for i, loc in enumerate(locations):
        if i < len(locations) - 1:
            ctx.report_progress(None, loc)  # indeterminate — we don't know how long this takes
        else:
            ctx.report_progress(100, loc)
        await asyncio.sleep(5)


async def _belly_rub(ctx):
    """Long task, does NOT check should_continue()."""
    phases = [
        "Approaching cautiously...", "Initial ear scratch — purring detected",
        "Moving to belly — risky maneuver", "Belly rub accepted! Purring intensifies",
        "Cat has entered zen mode", "You have also entered zen mode",
        "Time has lost all meaning", "Cat kicks your hand — session over",
    ]
    for i, phase in enumerate(phases):
        ctx.report_progress((i + 1) / len(phases) * 100, phase)
        await asyncio.sleep(6)


async def _negotiate_treats(ctx):
    """Does NOT check should_continue()."""
    stages = [
        "Mr. Whiskers: staring intently at treat cupboard",
        "You: 'You already had treats today'",
        "Mr. Whiskers: meowing louder",
        "You: 'Fine, just one'",
        "Mr. Whiskers: inhales three treats before you can react",
        "Treaty signed: 3 treats per session, max 2 sessions per day",
    ]
    for i, stage in enumerate(stages):
        ctx.report_progress((i + 1) / len(stages) * 100, stage)
        await asyncio.sleep(5)


async def _cat_phase(ctx):
    cb = sequential_progress(ctx, 3)
    await ctx.run_child(_locate_whiskers, "home-cat-locate", "Locating Mr. Whiskers",
                        description="Indeterminate progress — cat's location is fundamentally unknowable until observed.",
                        on_child_progress=cb)
    await ctx.run_child(_belly_rub, "home-cat-belly", "Extended Belly Rub Session",
                        description="Does not check should_continue(). Cannot be interrupted. This is the way.",
                        on_child_progress=cb)
    await ctx.run_child(_negotiate_treats, "home-cat-treats", "Negotiating Treat Distribution",
                        description="Diplomatic negotiations between human and feline. Ignores cancellation.",
                        on_child_progress=cb)


# ---------------------------------------------------------------------------
# Top-level task
# ---------------------------------------------------------------------------

async def _organizing_home(ctx):
    cb = sequential_progress(ctx, 3)

    ctx.report_progress(0, "Taking a deep breath... here we go.")
    await ctx.run_child(_cleaning_phase, "home-phase-clean", "Phase 1: Cleaning Up Your Mess",
                        description="Sock recovery, dishwashing, couch archaeology. ~3 minutes.",
                        on_child_progress=cb)

    await ctx.run_child(_triage_phase, "home-phase-triage", "Phase 2: Triaging Your Clothes",
                        description="Parallel sorting/folding/ironing. One child always fails (emotional attachment). ~4 minutes.",
                        on_child_progress=cb)

    await ctx.run_child(_cat_phase, "home-phase-cats", "Phase 3: Petting Your Cats",
                        description="Non-cancellable phase. Children ignore should_continue(). Indeterminate progress. ~3 minutes.",
                        on_child_progress=cb)

    ctx.report_progress(100, "Home is... acceptable. Mr. Whiskers approves.")


def get_tasks() -> list[TaskInstance]:
    return [
        TaskInstance(
            execute=_organizing_home,
            process_id="organizing-home",
            name="Organizing Your Home",
            description=(
                "Domestic chaos management. Exercises mixed sequential/parallel children, "
                "survive_failure, indeterminate progress, cancellable=False (dashboard hides "
                "cancel button), and children that ignore cancellation. ~10 minutes."
            ),
            metadata={"location": "home", "difficulty": "extreme"},
            cancellable=False,
        ),
    ]
```

- [ ] **Step 2: Verify**

Run: `cd packages/optio-demo && make run`
Expected: "Synced 2 task definitions". Ctrl+C.

- [ ] **Step 3: Commit**

```bash
git add packages/optio-demo/src/optio_demo/tasks/home.py
git commit -m "feat(optio-demo): add Organizing Your Home task tree"
```

---

### Task 9: The Great Museum Heist

**Files:**
- Modify: `packages/optio-demo/src/optio_demo/tasks/heist.py`

- [ ] **Step 1: Write heist.py**

```python
"""The Great Museum Heist — parallel failure, cascading errors, warning.

Exercises: parallel group failure (default, not survived), cascading failure
through nested children (3 levels), warning field, indeterminate progress,
cancel propagation. ~8 minutes.
"""

import asyncio
from optio_core.models import TaskInstance
from optio_core.progress_helpers import average_progress


async def _disable_cameras(ctx):
    """Sequential: hack -> loop -> erase."""

    async def _hack_mainframe(h_ctx):
        steps = ["Connecting to museum WiFi", "Bypassing firewall", "Accessing camera server",
                 "Extracting admin credentials", "Logging in..."]
        for i, step in enumerate(steps):
            if not h_ctx.should_continue():
                return
            h_ctx.report_progress((i + 1) / len(steps) * 100, step)
            await asyncio.sleep(5)

    async def _loop_cameras(l_ctx):
        cams = ["Lobby cam", "Hall A cam", "Hall B cam", "Vault corridor cam",
                "Loading dock cam", "Roof cam"]
        for i, cam in enumerate(cams):
            if not l_ctx.should_continue():
                return
            l_ctx.report_progress((i + 1) / len(cams) * 100, f"Looping {cam} — replaying empty footage")
            await asyncio.sleep(4)

    async def _erase_logs(e_ctx):
        steps = ["Identifying log files", "Wiping access logs", "Clearing audit trail",
                 "Planting false timestamps"]
        for i, step in enumerate(steps):
            if not e_ctx.should_continue():
                return
            e_ctx.report_progress((i + 1) / len(steps) * 100, step)
            await asyncio.sleep(5)

    cb = __import__('optio_core.progress_helpers', fromlist=['sequential_progress']).sequential_progress(ctx, 3)
    await ctx.run_child(_hack_mainframe, "heist-cam-hack", "Hacking Museum Mainframe",
                        description="WiFi infiltration and credential theft.", on_child_progress=cb)
    await ctx.run_child(_loop_cameras, "heist-cam-loop", "Looping Security Cameras",
                        description="Replacing live feeds with pre-recorded empty footage.", on_child_progress=cb)
    await ctx.run_child(_erase_logs, "heist-cam-erase", "Erasing Security Logs",
                        description="Removing all evidence of system access.", on_child_progress=cb)


async def _crack_vault(ctx):
    """Deep nesting: outer lock -> inner lock -> laser grid (fails!).
    Cascading failure — no survive_failure."""

    async def _pick_outer_lock(o_ctx):
        steps = ["Examining lock mechanism", "Inserting tension wrench",
                 "Raking pins... click", "Outer lock open!"]
        for i, step in enumerate(steps):
            if not o_ctx.should_continue():
                return
            o_ctx.report_progress((i + 1) / len(steps) * 100, step)
            await asyncio.sleep(5)

    async def _pick_inner_lock(i_ctx):
        steps = ["This one's electronic", "Attaching bypass device",
                 "Brute-forcing 6-digit code...", "Code cracked: 847291", "Inner lock open!"]
        for i, step in enumerate(steps):
            if not i_ctx.should_continue():
                return
            i_ctx.report_progress((i + 1) / len(steps) * 100, step)
            await asyncio.sleep(5)

    async def _bypass_laser_grid(l_ctx):
        """Always fails — triggers cascading failure up the chain."""
        steps = ["Mapping laser pattern", "Calculating safe path",
                 "Deploying mirror array", "Redirecting beam 1...", "Redirecting beam 2..."]
        for i, step in enumerate(steps):
            if not l_ctx.should_continue():
                return
            l_ctx.report_progress((i + 1) / len(steps) * 100, step)
            await asyncio.sleep(4)
        raise Exception("Triggered silent alarm! Mirror alignment off by 0.3 degrees! (Intentional — tests cascading failure)")

    await ctx.run_child(_pick_outer_lock, "heist-vault-outer", "Picking Outer Lock",
                        description="Mechanical tumbler lock. Old school.")
    await ctx.run_child(_pick_inner_lock, "heist-vault-inner", "Picking Inner Lock",
                        description="Electronic lock with 6-digit code.")
    await ctx.run_child(_bypass_laser_grid, "heist-vault-laser", "Bypassing Laser Grid",
                        description="This always fails! Cascading failure: laser grid -> vault -> entire heist. No survive_failure.")


async def _distract_guards(ctx):
    """Parallel distractions."""
    cb = average_progress(ctx)
    async with ctx.parallel_group(on_child_progress=cb) as group:
        async def _pizza(p_ctx):
            steps = ["Ordering pizza to museum entrance", "Pizza arriving...",
                     "Guard: 'I didn't order this'", "Delivery person: 'Someone did, it's paid for'",
                     "Guard distracted for 8 minutes arguing about anchovies"]
            for i, step in enumerate(steps):
                if not p_ctx.should_continue():
                    return
                p_ctx.report_progress((i + 1) / len(steps) * 100, step)
                await asyncio.sleep(5)

        async def _car_alarm(c_ctx):
            steps = ["Locating target vehicle across the street", "Triggering alarm remotely",
                     "BEEP BEEP BEEP BEEP", "Guard looking out window",
                     "Guard going outside to investigate"]
            for i, step in enumerate(steps):
                if not c_ctx.should_continue():
                    return
                c_ctx.report_progress((i + 1) / len(steps) * 100, step)
                await asyncio.sleep(4)

        async def _pigeons(pg_ctx):
            steps = ["Releasing trained pigeons at loading dock",
                     "Pigeons entering through ventilation",
                     "Pigeons causing chaos in gift shop",
                     "Guard running to gift shop: 'NOT THE POSTCARDS!'",
                     "Gift shop fully occupied — path clear"]
            for i, step in enumerate(steps):
                if not pg_ctx.should_continue():
                    return
                pg_ctx.report_progress((i + 1) / len(steps) * 100, step)
                await asyncio.sleep(5)

        await group.spawn(_pizza, "heist-distract-pizza", "Fake Pizza Delivery",
                          description="Anchovy-based social engineering.")
        await group.spawn(_car_alarm, "heist-distract-alarm", "Setting Off Car Alarm",
                          description="Remote-triggered distraction across the street.")
        await group.spawn(_pigeons, "heist-distract-pigeons", "Releasing Trained Pigeons",
                          description="Avian chaos agents deployed to the gift shop.")


async def _getaway_driver(ctx):
    """Indeterminate progress — just waiting."""
    messages = [
        "Engine running...", "Checking mirrors...", "Adjusting seat (nervous habit)...",
        "Listening to police scanner... all clear", "Drumming on steering wheel...",
        "Getting nervous...", "Checking watch...", "This is taking too long...",
        "Considering a career change...", "Still waiting...",
    ]
    for msg in messages:
        if not ctx.should_continue():
            return
        ctx.report_progress(None, msg)
        await asyncio.sleep(5)


async def _museum_heist(ctx):
    """All phases run in parallel. The vault cracking fails, taking down the whole heist."""
    cb = average_progress(ctx)
    ctx.report_progress(0, "The heist begins at midnight...")

    async with ctx.parallel_group(on_child_progress=cb) as group:
        await group.spawn(_disable_cameras, "heist-cameras", "Disabling Security Cameras",
                          description="Sequential: hack mainframe -> loop cameras -> erase logs.")
        await group.spawn(_crack_vault, "heist-vault", "Cracking the Vault",
                          description="Deep nesting with cascading failure. Laser grid fails, taking down the whole vault operation.")
        await group.spawn(_distract_guards, "heist-guards", "Distracting the Guards",
                          description="Three parallel distractions: pizza, car alarm, pigeons.")
        await group.spawn(_getaway_driver, "heist-getaway", "Getaway Driver Waiting",
                          description="Indeterminate progress — just anxiously waiting outside.")

    # We'll never reach here — the parallel group raises because vault fails
    ctx.report_progress(100, "Heist complete!")


def get_tasks() -> list[TaskInstance]:
    return [
        TaskInstance(
            execute=_museum_heist,
            process_id="museum-heist",
            name="The Great Museum Heist",
            description=(
                "A daring parallel operation that always fails. Exercises parallel group "
                "failure (default, not survived), cascading failure through 3 levels of "
                "nested children, the warning field, and indeterminate progress. ~8 minutes."
            ),
            warning="This is a highly illegal operation",
            metadata={"target": "louvre", "crew_size": "4"},
        ),
    ]
```

- [ ] **Step 2: Fix the import in _disable_cameras**

The `__import__` hack is ugly. Add the import at the top of the file instead:

```python
from optio_core.progress_helpers import average_progress, sequential_progress
```

And replace the line in `_disable_cameras`:
```python
    cb = sequential_progress(ctx, 3)
```

- [ ] **Step 3: Verify and commit**

Run: `cd packages/optio-demo && make run`
Expected: "Synced 3 task definitions".

```bash
git add packages/optio-demo/src/optio_demo/tasks/heist.py
git commit -m "feat(optio-demo): add The Great Museum Heist task tree"
```

---

### Task 10: Intergalactic Music Festival (generated)

**Files:**
- Modify: `packages/optio-demo/src/optio_demo/tasks/festival.py`

- [ ] **Step 1: Write festival.py**

```python
"""Intergalactic Music Festival — generated tasks from template.

Exercises: generated tasks via for-loop, params access, metadata (varied per task),
metadata inheritance to children, conditional child execution. ~2 minutes per concert.
"""

import asyncio
import random
from optio_core.models import TaskInstance
from optio_core.progress_helpers import sequential_progress


VENUES = [
    {"id": "europa", "name": "Europa", "genre": "Space Jazz", "audience": 5000, "songs": 8, "encore": True},
    {"id": "titan", "name": "Titan", "genre": "Methane Blues", "audience": 12000, "songs": 12, "encore": True},
    {"id": "ganymede", "name": "Ganymede", "genre": "Low-G Punk", "audience": 3000, "songs": 6, "encore": False},
    {"id": "callisto", "name": "Callisto", "genre": "Cryo-Folk", "audience": 8000, "songs": 10, "encore": True},
    {"id": "io", "name": "Io", "genre": "Volcanic Metal", "audience": 2000, "songs": 5, "encore": False},
    {"id": "enceladus", "name": "Enceladus", "genre": "Geyser Ambient", "audience": 15000, "songs": 15, "encore": True},
    {"id": "triton", "name": "Triton", "genre": "Retrograde Techno", "audience": 7000, "songs": 9, "encore": True},
    {"id": "phobos", "name": "Phobos", "genre": "Orbital Ska", "audience": 1000, "songs": 4, "encore": False},
]

SONG_NAMES = [
    "Stellar Drift", "Nebula Rain", "Cosmic Lullaby", "Gravity Well Blues",
    "Ion Storm Serenade", "Redshift Romance", "Pulsar Heartbeat", "Dark Matter Waltz",
    "Solar Flare Stomp", "Asteroid Belt Shuffle", "Wormhole Express", "Supernova Sunrise",
    "Quasar Quickstep", "Comet Tail Tango", "Black Hole Ballad",
]


async def _sound_check(ctx):
    checks = ["Testing microphones", "Checking speakers", "Tuning instruments",
              "Adjusting monitor mix", "Sound check complete — levels are perfect"]
    for i, check in enumerate(checks):
        if not ctx.should_continue():
            return
        ctx.report_progress((i + 1) / len(checks) * 100, check)
        await asyncio.sleep(2)


async def _opening_act(ctx):
    steps = ["Opening act takes the stage", "Playing their hit single",
             "Crowd warming up...", "Standing ovation (polite)", "Opening act exits — main event incoming!"]
    for i, step in enumerate(steps):
        if not ctx.should_continue():
            return
        ctx.report_progress((i + 1) / len(steps) * 100, step)
        await asyncio.sleep(3)


async def _play_song(ctx):
    """Plays one song. Song name comes from params."""
    song_name = ctx.params.get("song_name", "Untitled")
    song_num = ctx.params.get("song_num", 1)
    total = ctx.params.get("total_songs", 1)
    genre = ctx.metadata.get("genre", "Music")

    phases = [
        f"({genre}) {song_name} — intro",
        f"({genre}) {song_name} — verse 1",
        f"({genre}) {song_name} — chorus",
        f"({genre}) {song_name} — bridge",
        f"({genre}) {song_name} — finale!",
    ]
    for i, phase in enumerate(phases):
        if not ctx.should_continue():
            return
        ctx.report_progress((i + 1) / len(phases) * 100, phase)
        await asyncio.sleep(1.5)


async def _encore(ctx):
    steps = ["Crowd chanting 'ENCORE! ENCORE!'", "Band returns to stage",
             "Playing the fan favorite...", "Extended guitar solo",
             "Fireworks! Confetti! Standing ovation!", "Best concert in the solar system!"]
    for i, step in enumerate(steps):
        if not ctx.should_continue():
            return
        ctx.report_progress((i + 1) / len(steps) * 100, step)
        await asyncio.sleep(2)


async def _concert(ctx):
    """Generic concert execute function. Reads params for customization."""
    num_songs = ctx.params.get("num_songs", 5)
    do_encore = ctx.params.get("encore", False)
    venue = ctx.params.get("venue", "Unknown")

    # +2 for sound check and opening act, +1 for encore if applicable
    total_phases = 2 + num_songs + (1 if do_encore else 0)
    cb = sequential_progress(ctx, total_phases)

    ctx.report_progress(0, f"Welcome to {venue}! {ctx.params.get('audience_size', '???')} fans in attendance!")

    await ctx.run_child(_sound_check, f"concert-{ctx.process_id}-soundcheck", "Sound Check",
                        description=f"Audio setup for {venue} venue.", on_child_progress=cb)

    await ctx.run_child(_opening_act, f"concert-{ctx.process_id}-opener", "Opening Act",
                        description="Local band warming up the crowd.", on_child_progress=cb)

    # Shuffle song names for variety
    rng = random.Random(ctx.process_id)  # deterministic per venue
    songs = rng.sample(SONG_NAMES, min(num_songs, len(SONG_NAMES)))

    for i, song_name in enumerate(songs):
        await ctx.run_child(
            _play_song,
            f"concert-{ctx.process_id}-song-{i+1}",
            f"Song {i+1}/{num_songs}: {song_name}",
            params={"song_name": song_name, "song_num": i + 1, "total_songs": num_songs},
            description=f"Track {i+1} of the main set.",
            on_child_progress=cb,
        )

    if do_encore:
        await ctx.run_child(_encore, f"concert-{ctx.process_id}-encore", "Encore!",
                            description="The crowd demands more!", on_child_progress=cb)


def get_tasks() -> list[TaskInstance]:
    tasks = []
    for venue in VENUES:
        encore_text = ", plus encore" if venue["encore"] else ""
        tasks.append(TaskInstance(
            execute=_concert,
            process_id=f"concert-{venue['id']}",
            name=f"Concert on {venue['name']}",
            description=(
                f"A {venue['genre']} concert for {venue['audience']} fans. "
                f"{venue['songs']} songs planned{encore_text}. "
                f"Generated task — same execute function, different params and metadata."
            ),
            params={
                "venue": venue["name"],
                "audience_size": venue["audience"],
                "num_songs": venue["songs"],
                "encore": venue["encore"],
            },
            metadata={
                "venue": venue["name"],
                "sector": "outer-solar-system",
                "genre": venue["genre"],
            },
        ))
    return tasks
```

- [ ] **Step 2: Verify and commit**

Run: `cd packages/optio-demo && make run`
Expected: "Synced 11 task definitions" (1 terraforming + 1 home + 1 heist + 8 concerts).

```bash
git add packages/optio-demo/src/optio_demo/tasks/festival.py
git commit -m "feat(optio-demo): add Intergalactic Music Festival generated tasks"
```

---

### Task 11: Your 15-min Wake-up Call (cron)

**Files:**
- Modify: `packages/optio-demo/src/optio_demo/tasks/wakeup.py`

- [ ] **Step 1: Write wakeup.py**

```python
"""Your 15-min Wake-up Call — cron scheduled task.

Exercises: cron scheduling, automatic re-launch. Fires every 15 minutes.
"""

import asyncio
from datetime import datetime, timezone
from optio_core.models import TaskInstance


async def _wakeup(ctx):
    now = datetime.now(timezone.utc).strftime("%H:%M:%S UTC")
    ctx.report_progress(0, f"ALARM TRIGGERED at {now}")

    sequence = [
        (10, "beep."),
        (20, "beep beep."),
        (30, "BEEP BEEP."),
        (40, "BEEP BEEP BEEP!"),
        (50, "WAKE UP!"),
        (60, "SERIOUSLY, WAKE UP!"),
        (70, "I'M NOT GOING AWAY!"),
        (80, "..."),
        (90, "Fine. Snoozing for 15 minutes."),
        (100, "See you again soon. *evil laugh*"),
    ]
    for pct, msg in sequence:
        if not ctx.should_continue():
            return
        ctx.report_progress(pct, msg)
        await asyncio.sleep(1.5)


def get_tasks() -> list[TaskInstance]:
    return [
        TaskInstance(
            execute=_wakeup,
            process_id="wakeup-call",
            name="Your 15-min Wake-up Call",
            description=(
                "Fires every 15 minutes via cron. Exercises cron scheduling and "
                "automatic re-launch. Will keep going until you stop the app."
            ),
            schedule="*/15 * * * *",
        ),
    ]
```

- [ ] **Step 2: Verify and commit**

Run: `cd packages/optio-demo && make run`
Expected: "Synced 12 task definitions". Scheduled task fires within 15 minutes.

```bash
git add packages/optio-demo/src/optio_demo/tasks/wakeup.py
git commit -m "feat(optio-demo): add cron-scheduled wake-up call task"
```

---

### Task 12: README.md and AGENTS.md for optio-demo

**Files:**
- Create: `packages/optio-demo/README.md`
- Create: `packages/optio-demo/AGENTS.md`

- [ ] **Step 1: Write README.md**

Write a README covering:
- What optio-demo is (demo app exercising all optio-core features)
- Prerequisites (Docker, Python 3.11+, Node.js for dashboard)
- Quick start (`make install`, `make run`, `make run-dashboard`)
- Task themes (brief description of each)
- Feature coverage table (which theme exercises which features)

- [ ] **Step 2: Write AGENTS.md**

Write an AGENTS.md covering:
- Package purpose
- File structure
- How task modules work (each exports `get_tasks() -> list[TaskInstance]`)
- How to add new tasks
- Dependencies (optio-core[redis], MongoDB, Redis)

- [ ] **Step 3: Commit**

```bash
git add packages/optio-demo/README.md packages/optio-demo/AGENTS.md
git commit -m "docs(optio-demo): add README and AGENTS.md"
```

---

## Self-Review

### Spec coverage

| Spec Requirement | Task |
|---|---|
| Phase 0: description field on TaskInstance | Task 1 |
| Phase 0: description in store (upsert, create_child) | Task 1 |
| Phase 0: description in run_child/spawn/execute_child | Task 2 |
| Phase 0: description in contracts schema | Task 3 |
| Phase 0: description in UI (tooltip) | Task 4 |
| Phase 0: remove propagation dead code | Task 1 |
| Phase 0: update AGENTS.md/README | Task 5 |
| Phase 1: package scaffold | Task 6 |
| Phase 1: Terraforming Mars (~30 min) | Task 7 |
| Phase 1: Organizing Your Home (~10 min) | Task 8 |
| Phase 1: Museum Heist (~8 min) | Task 9 |
| Phase 1: Festival (generated, 8 concerts) | Task 10 |
| Phase 1: Wake-up Call (cron) | Task 11 |
| Phase 1: README + AGENTS.md | Task 12 |

### Placeholder scan

No TBDs, TODOs, or vague instructions found. All code blocks are complete.

### Type consistency

- `TaskInstance` uses `cancellable: bool = True` consistently (flattened from `CancellationConfig`) across Tasks 1, 7, 8.
- `description: str | None = None` parameter consistent across models.py, store.py, context.py (run_child, spawn), executor.py.
- `get_tasks() -> list[TaskInstance]` signature consistent across all task modules. `adhoc_ephemeral` was removed (not needed).
- All `process_id` values use kebab-case with unique prefixes per theme (tf-, home-, heist-, concert-).
