# optio-demo — LLM Reference

## Package

- **name**: `optio-demo`
- **type**: Python application (not a library)
- **entry point**: `python -m optio_demo`
- **dependencies**: `optio-core[redis]`, `motor` (via optio-core), MongoDB + Redis via Docker

## Purpose

Exercises all optio-core features through whimsical themed task trees. Not intended to be imported by other packages — it is a runnable demo.

## File structure

```
src/optio_demo/
  __main__.py          # Entry point: connects to MongoDB/Redis, initialises Optio, calls fw.run()
  tasks/
    __init__.py        # Aggregates all task modules; exports get_task_definitions()
    terraforming.py    # Terraforming Mars (~30 min) — deep nesting, all progress helpers
    home.py            # Organizing Your Home (~10 min) — mixed seq/parallel, non-cancellable
    heist.py           # The Great Museum Heist (~8 min) — parallel failure, warning field
    festival.py        # Intergalactic Music Festival — 8 generated concert tasks
    wakeup.py          # Your 15-min Wake-up Call — cron-scheduled, auto-relaunches
docker-compose.yml     # Starts MongoDB (27017) and Redis (6379)
Makefile               # install / run / run-dashboard targets
```

## How task modules work

Each module exports a synchronous `get_tasks() -> list[TaskInstance]` function. The aggregator in `tasks/__init__.py` calls all of them and combines the results:

```python
async def get_task_definitions(services: dict) -> list[TaskInstance]:
    return [
        *terraforming_tasks(),
        *home_tasks(),
        ...
    ]
```

A `TaskInstance` is a plain dataclass from `optio_core.models`. Minimal shape:

```python
TaskInstance(
    id="my-task",
    name="My Task",
    fn=my_async_fn,           # async def fn(ctx) -> None
    cancellable=True,
    cron=None,                # e.g. "*/15 * * * *" for cron scheduling
    params={},
    metadata={},
)
```

Task functions receive a `ctx` object with:
- `ctx.report_progress(pct, message)` — report numeric or None progress
- `ctx.should_continue()` — cooperative cancellation check
- `ctx.spawn(...)` / `ctx.run_child(...)` — launch child tasks
- `ctx.params`, `ctx.metadata` — task-level data

Progress helpers (`sequential_progress`, `average_progress`, `mapped_progress`) are imported from `optio_core.progress_helpers` and wrap child execution to roll up progress automatically.

## Adding a new task module

1. Create `src/optio_demo/tasks/my_theme.py` with a `get_tasks() -> list[TaskInstance]` function.
2. Import and spread it in `tasks/__init__.py`.

## Infrastructure

`docker-compose.yml` starts both dependencies:

```bash
make install   # docker compose up -d && pip install -e ../optio-core[redis] && pip install -e .
make run       # python -m optio_demo
make run-dashboard   # MONGODB_URL=... npx optio-dashboard
```

Environment variables (all optional, shown with defaults):
- `MONGODB_URL` — `mongodb://localhost:27017/optio-demo`
- `REDIS_URL` — `redis://localhost:6379`
- `OPTIO_PREFIX` — `optio`

## Opencode demo

Task `opencode-demo` runs a short local opencode session that asks the
human for a favorite color and ships a deliverable containing the
color and the number 42.  Reference consumer for `optio-opencode`;
exercises the full iframe + proxy + log-tail stack.  Requires opencode
to be installed and authenticated on the developer's machine.
