# TaskFactory: tasks launched with parameters (design)

The owner, 2026-10-20, for Praeceptor's need of a launch-time parameterised process ("Drop
proposals" with chosen sources and a unit range; later, re-runs with chosen instructions): "in
optio-core We extend the definition of processes. ... something that declares a bunch of
parameters (vultus field declarations), and when fed with that data, gets you an ad-hoc ephemeral
task that is called with those parameters baked in. Then in optio-ui we implement a form that takes
such a [definition]'s data and renders the right input widgets for the field definitions, and
launches it." Its name (the owner: "If we already have TaskInstance, then the new name should be
TaskFactory or TaskGenerator. You pick."): **TaskFactory** (a "generator" means something else in
Python). Launches are ephemeral processes (the owner: "the solution for this is ephemeral
processes").

Today optio launches take no values: `launch(process_id, resume, *, session_id)`; a task's
`params` are fixed at definition and reset by every sync. Excavator's debug recipe runs work around
it outside optio (its own form, its own API route and engine RPC building an ad-hoc process per
launch); this design puts that pattern into optio.

## 1. optio-core

### 1.1 The declaration

```python
@dataclass
class TaskFactory:
    execute: Callable[[ProcessContext], Awaitable[None]]  # a launch's task; reads ctx.params
    process_id: str
    name: str
    parameters: list[dict[str, Any]]   # vultus field definitions as plain data (1.3)
    description: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)
    special: bool = False
    warning: str | None = None         # shown before the form, as a task's
    cancellable: bool = True
    ui_widget: str | None = None       # a launch's widget
    ttl_seconds: int | None = None     # None: a launch is ephemeral (deleted when it ends);
                                       # set: a launch is kept that long after it ends
    auto_cancel_children: bool = True
    validate: Callable[[dict[str, Any]], str | None] | None = None  # beyond the fields' own
                                       # checks: a message refuses the values (1.4)
    name_for: Callable[[dict[str, Any]], str] | None = None  # a launch's name; default `name`
```

- `get_task_definitions` may return `TaskFactory`s among its `TaskInstance`s.
- A factory is synced as a process record like a task's, with two more fields, `taskFactory: true`
  and `parameters` (both `$set` on every sync). It never runs itself.
- A factory has no schedule, resume, resurrect or auto-resume: it has no such fields.
- Sync refuses (ValueError, as other definition errors) a factory whose `parameters` are not
  valid (1.3).

### 1.2 Launching

`launch(process_id, resume=False, *, session_id, params=None)`, and the same `params` on
`launch_and_wait` and `launch_and_await_result`:

- **A factory, without `params`:** refused, `LaunchOutcome(ok=False, reason="parameters-required")`.
- **A factory, with `params`:** the values are checked (1.4). Then a child process is made:
  - its `TaskInstance` has the factory's `execute`, `params` = the values, the factory's metadata
    plus `taskFactory: <factory process id>`, and the factory's other fields;
  - its process id is the factory's plus `:` and 8 random hex digits;
  - its name is `name_for(values)`, or the factory's name;
  - it is defined with `adhoc_define(child, parent_id=<the factory record's _id>,
    ephemeral=<ttl_seconds is None>)`, with `ttl_seconds` copied, and launched with the
    launch's `session_id`.
- **A launch's outcome:** `LaunchOutcome(ok=True, process=<the child's record>)`, so a caller can
  open it.
- **Not a factory, with `params`:** refused, `reason="not-a-factory"`.
- `resume=True` with a factory is refused, `reason="no-resume-support"` (a launch never resumes).
- An engine restart while a launch runs: it is cancelled as any task. Its `TaskInstance` lived only
  in memory (ad-hoc), so it cannot be launched again; its record is removed if ephemeral, else
  kept until its TTL.

### 1.3 The parameters

vultus-core's `FieldDefinition`, as plain JSON (the subset a definition can carry as data; no
functions): `kind` one of `boolean`, `oneOf`, `manyOf`, `numeric`, `range`, `text`; `id`
(unique among the factory's); `label`; the kind's own static options (`choices` with `value` and
`label` for `oneOf`/`manyOf`; `min`, `max`, `step` for `numeric`/`range`; `maxLength` for `text`);
`initialValue` (the form's default, checked like a value); `required` (default true). A `label`
(a field's, a choice's) is a string or a map of language code to string, resolved by the UI for
its language (that language, else `en`, else the first). The other vultus kinds (`mixed-boolean`,
the tree kinds) are out until a factory needs one.

### 1.4 Checking the values

Before the child exists:
- every key is a parameter's id, and every required parameter has a value;
- each value has its kind's type and options: a `oneOf` value one of its choices, a `manyOf` a
  list of distinct choices, a `numeric` a number within `min`/`max`, a `range` a `[from, to]` pair
  within them with `from <= to`, a `boolean` a bool, a `text` a string within `maxLength`;
- then the factory's `validate`, if any: a returned message refuses.

A refusal is `reason="invalid-parameters"` with a message naming the parameter (`"<id>: <what>"`),
which the UI shows on that field.

## 2. optio-contracts, the engine RPC, optio-api

- `ProcessSchema`: `taskFactory?: boolean`, `parameters?: unknown[]` (the field definitions as
  data; optio-ui types them with vultus-core).
- The `launch` route's body: `params?: Record<string, unknown>`.
- `LaunchFailureReason`: `parameters-required`, `not-a-factory`, `invalid-parameters`; the error
  body `{ reason, message }` as today.
- The engine RPC's `LaunchParams`: `params` (optional).
- optio-api passes `params` through, as everything (its no-logic rule); `launch` answers the
  child's record for a factory.

## 3. optio-ui

- **The launch form:** a factory's launch button opens a modal (`TaskFactoryLaunchModal`):
  - its fields: the factory's `parameters`, rendered with vultus-antd `Field`s, their initial values
    the definitions' `initialValue`;
  - the factory's `warning`, if any, at its top;
  - one vultus action submits `launch(id, { params, sessionId })`; an `invalid-parameters` refusal
    shows on its field; `parameters-required` and the others as the button's errors do today;
  - on success it closes; the launch shows under its factory in the tree, and `onLaunched(child)`
    (optional) tells the app.
- Where: `ProcessItem`'s and `ProcessDetailView`'s launch controls, wherever a factory record is.
- `useProcessActions().launch(id, { resume?, params? })`.
- Labels resolved by the UI language (1.3).
- **New peer:** `vultus-antd` (the widgets; vultus-core stays a peer).

## 4. Testing (no wall-clock dependence: AGENTS.md)

- optio-core: the checks (1.4) by a table; sync of a factory record; launch refusals; a launch
  makes the child with the values in `ctx.params`, as a child of the factory, ephemeral or with its
  TTL; `name_for`; the factory never runs.
- optio-api: `params` passed through; the new reasons mapped to 400/409.
- optio-ui: the form renders each kind, submits the values, shows `invalid-parameters` on its
  field, opens from the list and the detail view.

## 5. Docs and release

- The root `AGENTS.md` and the packages' (`optio-core`, `optio-contracts`, `optio-api`,
  `optio-ui`) in the same commits as their API changes.
- Released per `docs/release-cookbook.md`: optio-core, optio-contracts, optio-api, optio-ui (minor
  versions: new API, nothing removed).

## 6. Out

- Scheduling a factory with values; resuming or resurrecting a launch.
- Parameter kinds beyond 1.3; values computed by functions (vultus `ValueOrFn`).
- Who may launch a factory: as a task's launch (optio-api's access hooks apply unchanged).
