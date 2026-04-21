# optio-demo

A demo application that exercises all [optio-core](../optio-core) features through a collection of whimsical long-running task trees. Run it, open the dashboard, and watch processes unfold in real time — or cancel, relaunch, and dismiss them from the UI.

## Prerequisites

- **Docker** — for MongoDB and Redis (started automatically by `make install`)
- **Python 3.11+** — for the demo worker
- **Node.js** — for the dashboard (`npx optio-dashboard`)

## Quick start

```bash
# 1. Start infrastructure and install Python dependencies
make install

# 2. Start the demo worker
make run

# 3. In a second terminal, launch the dashboard
make run-dashboard
```

Then open [http://localhost:3000](http://localhost:3000).

From the dashboard you can **launch**, **cancel**, and **dismiss** any process. Click a process name to drill into its task tree with live progress and a log panel.

## Task themes

### Terraforming Mars (~30 min)

The flagship showcase. A four-level nested task tree covering all progress helpers (`sequential_progress`, `average_progress`, `mapped_progress`), parallel groups with concurrency limits, `survive_failure` subtasks, and a full cooperative cancellation cascade. Metadata is set at the root and inherited by children.

### Organizing Your Home (~10 min)

A household chore tree mixing sequential and parallel subtasks. Demonstrates `survive_failure` (some chores are optional), non-cancellable tasks (you can't un-wash the dishes mid-stream), and indeterminate progress bars for tasks that can't report a percentage.

### The Great Museum Heist (~8 min)

A parallel heist plan where things go wrong. Exercises parallel failure propagation, cascading errors across sibling tasks, and the `warning` field — the dashboard shows a confirmation popover before you relaunch a task that carries a warning.

### Intergalactic Music Festival

Eight generated concert tasks, one per venue (Europa, Titan, Ganymede, …). Each is created from the same template but with different `params` and `metadata` (genre, audience size, song count, encore flag). Demonstrates generating task lists programmatically and conditional child execution driven by params.

### Your 15-min Wake-up Call

A cron-scheduled task that fires every 15 minutes. Demonstrates `cron` scheduling and automatic re-launch — after each run completes it returns to `scheduled` state and fires again at the next interval.

## Feature coverage

| Feature | Terraforming Mars | Organizing Home | Museum Heist | Music Festival | Wake-up Call |
|---------|:-----------------:|:---------------:|:------------:|:--------------:|:------------:|
| Sequential progress | x | | | x | |
| Average progress | x | | | | |
| Mapped progress | x | | | | |
| Parallel groups | x | x | x | | |
| max_concurrency | x | | | | |
| survive_failure | x | x | | | |
| Non-cancellable tasks | | x | | | |
| Indeterminate progress | | x | | | |
| Parallel failure / cascading errors | | | x | | |
| warning field | | | x | | |
| params / metadata | | | | x | |
| Metadata inheritance | x | | | x | |
| Cron scheduling | | | | | x |
| Cooperative cancellation | x | | | | |
| Deep nesting (4 levels) | x | | | | |
| Generated tasks (loop) | | | | x | |

## Widget smoke test (Task 21)

Exercises all four widget primitives end-to-end via a live marimo notebook.

1. `docker compose up` in this directory to start MongoDB + Redis.
2. `pnpm --filter optio-dashboard dev` in another terminal to serve the dashboard.
3. `python -m optio_demo` in a third terminal to run the demo worker (make sure `pip install -e .` or equivalent has been run so the `optio-demo` package is available).
4. Open the dashboard in a browser. Authenticate if prompted.
5. Find the "Marimo Notebook" task in the process list. Click launch.
6. Click the running process — the iframe widget should mount and show a live marimo notebook.
7. Interact with the notebook. Reactive updates flow through the widget proxy.
8. Cancel the process. The "session ended" banner overlays the iframe; the marimo subprocess is terminated.
9. Dismiss. The iframe unmounts; `widgetUpstream` and `widgetData` are cleared on the process document.
