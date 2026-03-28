# optio-demo Design Spec

A new package in the optio monorepo: a Python demo application that exercises all optio-core task/process features through whimsical, multi-level task trees. Intended for human observation via optio-dashboard.

**Reference:** `docs/superpowers/specs/2026-03-28-optio-core-feature-catalog.md`

---

## Phase 0: Add `description` Field to Tasks

Before building the demo, add an optional `description` field to tasks across the stack. This lets the demo store richer content — what each task does, what feature it exercises, flavor text.

### Changes by package

**optio-core** (`packages/optio-core/`):
- `models.py`: Add `description: str | None = None` to `TaskInstance`.
- `store.py`: Include `description` in `upsert_process` `$set` fields and in `create_child_process` document creation.
- `context.py`: Add `description` parameter to `run_child()`. Pass it through to `executor.execute_child()`.
- `executor.py`: Add `description` parameter to `execute_child()`. Pass it to `create_child_process()`.
- `context.py` (ParallelGroup): Add `description` parameter to `spawn()`. Pass it through to `run_child()`.

**optio-contracts** (`packages/optio-contracts/`):
- `src/schemas/process.ts`: Add `description: z.string().nullable()` to the process schema.

**optio-api** (`packages/optio-api/`):
- No changes needed. Handlers pass through DB documents which will now include `description`.

**optio-ui** (`packages/optio-ui/`):
- Process detail view: display `description` below the process name (when present).
- Process list item: show `description` as a tooltip on hover over the process name (when present).

**AGENTS.md files**: Update the root and per-package AGENTS.md to document the new field.

---

## Phase 1: The Demo Application

### Package structure

```
packages/optio-demo/
  pyproject.toml
  Makefile
  docker-compose.yml
  README.md
  AGENTS.md
  src/optio_demo/
    __init__.py               # empty, makes it a package
    __main__.py               # entry point: connect, init, run
    tasks/
      __init__.py             # get_task_definitions() — assembles all tasks
      terraforming.py         # "Terraforming Mars"
      home.py                 # "Organizing Your Home"
      heist.py                # "The Great Museum Heist"
      festival.py             # "Intergalactic Music Festival" (generated)
      wakeup.py               # "Your 15-min Wake-up Call" (cron)
```

### Infrastructure

**docker-compose.yml**: MongoDB 7 and Redis 7, standard ports (27017, 6379). No persistent volumes — demo data is ephemeral.

**Makefile**:
- `install`: `docker compose up -d` + `pip install -e ../optio-core[redis]` + `pip install -e .`
- `run`: `python -m optio_demo`
- `run-dashboard`: `npx optio-dashboard` with `MONGODB_URL` and `REDIS_URL` env vars matching docker-compose

**pyproject.toml**: Depends on `optio-core[redis]`. No other dependencies beyond what optio-core already requires.

**Entry point** (`__main__.py`): Reads `MONGODB_URL` (default `mongodb://localhost:27017/optio-demo`), `REDIS_URL` (default `redis://localhost:6379`), `OPTIO_PREFIX` (default `optio`). Connects to MongoDB, creates an `Optio` instance, passes `services={"optio": fw}` (for any tasks needing it), calls `init()` with the task generator, then `run()`.

### Timing

Tasks should run long enough for comfortable human observation via the dashboard:
- **Bottom-level children**: 5-10 seconds each
- **Mid-level parents**: naturally 1-5 minutes depending on number of children
- **Top-level tasks**: 5-30 minutes depending on depth and breadth
- **Progress reports**: every 0.5-2 seconds so progress bars visibly move

### Task themes

#### 1. Terraforming Mars (~30 minutes)

The big showcase. A 4-level task tree exercising the most features.

**Top level**: `process_id="terraforming-mars"`, `name="Terraforming Mars"`, `description="The full terraforming pipeline: survey, build, terraform. Exercises deep nesting, all three progress helpers, cooperative cancellation, survive_failure, and metadata inheritance."`, `metadata={"planet": "mars", "mission_type": "terraforming", "priority": "critical"}`.

Uses `sequential_progress(ctx, 3)` across three phases.

**Phase 1: Survey the Planet** (~5 min)
Sequential children. Each runs 5-10 seconds with detailed progress.
- "Mapping Geological Structures" — scans sectors, reports "Sector 3/12 mapped..."
- "Analyzing Atmospheric Composition" — measures gases, reports percentages
- "Detecting Subsurface Water" — deep radar sweep, reports depth layers
- "Cataloging Mineral Deposits" — identifies resources per region

**Phase 2: Build Infrastructure** (~10 min)
Parallel group with `max_concurrency=3`, uses `average_progress`.
- "Constructing Habitat Domes" — 6 domes built sequentially (nested children)
- "Deploying Mining Robot Swarm" — initializes robots in parallel (nested parallel group, `max_concurrency=5`)
- "Installing Power Grid" — sequential: solar panels, nuclear reactor, grid connection
- "Establishing Communications Array" — simple task, reports progress
- "Building Spaceport" — simple task
- "Fighting Off Alien Invaders" — always fails with `survive_failure=True`. Description notes this exercises survive_failure. Parent logs the failure and continues.

**Phase 3: Terraform** (~15 min)
Deep nesting (3-4 levels), uses `mapped_progress` for different sub-phases.
- "Atmosphere Processing" -> "Gas Injection Sequence" -> individual gas tasks (O2, N2, CO2 conversion)
- "Temperature Regulation" -> "Deploy Orbital Mirrors" (parallel) + "Activate Thermal Generators" (sequential)
- "Ecosystem Seeding" -> "Microbe Deployment" -> "Plant Introduction" -> "Animal Release"

All children check `should_continue()` for cooperative cancellation. Cancelling the top-level task should cascade down through the entire tree.

**Features exercised**: sequential_progress, average_progress, mapped_progress, parallel groups with max_concurrency, 4-level nesting, survive_failure, cooperative cancellation cascade, metadata inheritance, params (sector counts, robot counts, etc.), descriptions.

#### 2. Organizing Your Home (~10 minutes)

Mixed sequential/parallel, error handling, non-cancellable task, indeterminate progress.

**Top level**: `process_id="organizing-home"`, `name="Organizing Your Home"`, `description="Domestic chaos management. Exercises mixed sequential/parallel children, survive_failure, indeterminate progress, and non-cancellable tasks."`, `metadata={"location": "home", "difficulty": "extreme"}`.

**Phase 1: Cleaning Up Your Mess** (~3 min)
Sequential children:
- "Collecting Scattered Socks" — progress reports per room
- "Washing the Dishes" — progress per dish batch
- "Vacuuming Under the Couch" — discovers lost items, reports findings

**Phase 2: Triaging Your Clothes** (~4 min)
Parallel group:
- "Sorting Shirts by Color" — orderly progress
- "Folding Pants" — orderly progress
- "Deciding What to Throw Away" — always fails ("Cannot decide, emotional attachment too strong") with `survive_failure=True`. Parent continues despite the failure.
- "Ironing the Fancy Stuff" — orderly progress

**Phase 3: Petting Your Cats** (~3 min)
Sequential children, none of which check `should_continue()` — they ignore cancellation entirely:
- "Locating Mr. Whiskers" — indeterminate progress (`percent=None`, "Checking behind the couch...", "Checking on top of the fridge...")
- "Extended Belly Rub Session" — long task with progress, does not check should_continue()
- "Negotiating Treat Distribution" — progress with messages

Note: The top-level "Organizing Your Home" task uses `CancellationConfig(cancellable=False)` — the dashboard hides the cancel button for the entire task. This is a UI hint; the children's ignoring of `should_continue()` is the behavioral side.

**Features exercised**: mixed sequential/parallel, survive_failure in parallel group, CancellationConfig(cancellable=False), indeterminate progress (None percent), cancellation ignored (children don't check should_continue), descriptions.

#### 3. The Great Museum Heist (~8 minutes)

Parallel coordination, cancel propagation, cascading failure, warning field.

**Top level**: `process_id="museum-heist"`, `name="The Great Museum Heist"`, `description="A daring parallel operation. Exercises parallel group cancellation, cascading failure through nested children, and the warning field."`, `warning="This is a highly illegal operation"`, `metadata={"target": "louvre", "crew_size": "4"}`.

Parallel group (all phases run simultaneously):
- "Disabling Security Cameras" — sequential: hack mainframe -> loop cameras -> erase logs. Each 10-20 seconds.
- "Cracking the Vault" — deep nesting: pick outer lock -> pick inner lock -> bypass laser grid. "Bypass Laser Grid" fails ("Triggered silent alarm!"), cascading up (no survive_failure). This causes the entire heist to fail.
- "Distracting the Guards" — parallel children: "Fake Pizza Delivery", "Set Off Car Alarm Across Street", "Release Trained Pigeons"
- "Getaway Driver Waiting" — simple task that reports indeterminate progress ("Engine running...", "Checking mirrors...", "Getting nervous...")

Since "Cracking the Vault" fails and the parallel group uses `survive_failure=False` (default), the entire heist fails. The description explains this is intentional.

**Features exercised**: parallel group failure (default, not survived), cascading failure through nested children (3 levels), warning field, indeterminate progress, cancel propagation (user can also cancel mid-heist to see all parallel children cancel).

#### 4. Intergalactic Music Festival (~15 minutes total across all concerts)

Generated from a template. 8 concerts on different moons/planets.

**Generator**: A for-loop iterating over a list of venues, each with different `params` and `metadata`:

```python
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
```

Each generates a `TaskInstance` with:
- `process_id=f"concert-{id}"`, `name=f"Concert on {name}"`, `description=f"A {genre} concert for {audience} fans. {songs} songs planned{', plus encore' if encore else ''}. Exercises generated tasks with varying params and metadata."`
- `params={"audience_size": ..., "num_songs": ..., "encore": ...}`
- `metadata={"venue": ..., "sector": "outer-solar-system", "genre": ...}`

All share the same execute function which:
- Runs "Sound Check" child (5-10 sec)
- Runs "Opening Act" child (5-10 sec)
- Runs "Main Performance" with sequential children per song ("Song 1/N: [generated song name]", each 3-5 sec)
- Conditionally runs "Encore" child if `params["encore"]` is True (5-10 sec)
- Uses `sequential_progress` to track overall concert progress

**Features exercised**: generated tasks from template (for-loop), params access, metadata (different per task), metadata inheritance to children, conditional child execution based on params, descriptions.

#### 5. Your 15-min Wake-up Call (cron)

**Top level**: `process_id="wakeup-call"`, `name="Your 15-min Wake-up Call"`, `description="Fires every 15 minutes via cron. Exercises cron scheduling and re-launch behavior."`, `schedule="*/15 * * * *"`.

Simple single-level task: reports the current time, plays an "alarm sequence" (progress 0-100% over ~15 seconds with messages like "BEEP", "BEEP BEEP", "WAKE UP!", "Fine, snoozing...").

**Features exercised**: cron scheduling, automatic re-launch.

### Feature coverage checklist

| Feature (from catalog) | Where exercised |
|---|---|
| TaskInstance: params | All themes (Terraforming params, Festival generated params) |
| TaskInstance: metadata | All themes, especially Festival (varied metadata) |
| TaskInstance: schedule | Wake-up Call |
| TaskInstance: warning | Museum Heist |
| TaskInstance: special | Not included (UI-only flag, low value for demo) |
| TaskInstance: cancellation.cancellable=False | Home: Petting Your Cats |
| ProcessContext: report_progress(percent, message) | All tasks |
| ProcessContext: report_progress(None) indeterminate | Home (Locating Mr. Whiskers), Heist (Getaway Driver) |
| ProcessContext: should_continue() | Terraforming (all children), Heist (cancel mid-heist) |
| ProcessContext: run_child() | All themes |
| ProcessContext: parallel_group() | Terraforming Phase 2, Home Phase 2, Heist, Terraforming Phase 3 |
| State: full lifecycle | All tasks (idle -> scheduled -> running -> done) |
| State: re-launch from done | Any task (manual from dashboard) |
| State: re-launch from failed | Heist (fails, re-launch from dashboard) |
| State: dismiss | Any task (manual from dashboard) |
| Children: sequential | Terraforming Phase 1, Home Phase 1 & 3, Festival concerts |
| Children: parallel with max_concurrency | Terraforming Phase 2 (max 3), Phase 3 nested parallel |
| Children: 3-4 level nesting | Terraforming Phase 3, Heist vault cracking |
| Children: metadata inheritance | Terraforming (all children inherit planet/mission_type), Festival |
| Children: params not inherited | All children get their own params |
| Cancellation: cooperative | Terraforming (cancel -> entire tree cancels) |
| Cancellation: cancel parallel group | Heist (cancel -> all parallel children cancel) |
| Cancellation: non-cancellable (UI hint) | Home (top-level cancellable=False) |
| Cancellation: ignored (no should_continue check) | Home: Petting Your Cats children |
| Progress: sequential_progress | Terraforming top-level, Festival concerts |
| Progress: average_progress | Terraforming Phase 2 |
| Progress: mapped_progress | Terraforming Phase 3 |
| Error: simple failure | Heist: Bypass Laser Grid |
| Error: cascading failure | Heist: Vault -> Inner Lock -> Laser Grid cascades |
| Error: survive_failure | Terraforming: Fighting Off Aliens, Home: Deciding What to Throw Away |
| Error: parallel group failure (default) | Heist: entire heist fails |
| Logging: event logs | All tasks (automatic) |
| Logging: progress messages | All tasks |
| Logging: error logs | Heist failure, Home failure |
| Cron scheduling | Wake-up Call |
| Lifecycle: launch/dismiss/re-launch | All tasks (manual from dashboard) |
| description field (Phase 0) | All tasks |

### What's not covered

- `TaskInstance.special` — low-value UI flag, not worth a demo task
- Ad-hoc processes (`adhoc_define`/`adhoc_delete`) — framework internals
- Ephemeral processes (`mark_ephemeral`) — framework internals
- `cancellation.propagation` config — stored but not actively used by executor
