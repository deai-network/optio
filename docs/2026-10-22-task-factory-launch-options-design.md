# TaskFactory: a launch's ephemerality, overrides of the launch form, a deleted process's last view (design)

The owner, 2026-10-22, extending the TaskFactory (`docs/2026-10-20-task-factory-design.md`):

- "widen the config surface available for app devs. Currently, the tasks created by factories are
  always ephemeral. make that a binary input that is always there, rendered as a checkbox, with the
  appropriate explanation of course. So the client sends this flag to the engine, and then the
  engine sets ephemeral based on that." Unchecked, a launch is kept until it is dismissed, or for
  its factory's `ttl_seconds` when it has one (the owner's choice (a)). The checkbox starts where
  today's rule puts a launch: ephemeral without `ttl_seconds`, kept with it.
- "introduce an override feature, for tightening the config surface available for users ... it
  should be possible to pass in a prop/param called overrides. This overrides should be
  deep-merged into the auto-generated field definition map ... if we pass in an override:
  `{ dryRun: { visible: false, initialValue: true } }`, then this will be merged into the dryRun
  field definition, effectively hiding it from the user." Delivered by a hook, `useTaskFactory`
  ("`{ launch, isPending, dialog }`"), a button, and a map on the process widgets.
- "give process detail view a local cache for last 5 items, so that instead of 'process not
  found', it can switch to readonly mode, say 'Process deleted', but still show the old log".

Overrides tighten the form; they enforce nothing. A hidden field's value still comes from the
browser and is checked by optio-core as any value (1.4 of the TaskFactory design).

## 1. optio-core

### 1.1 A launch's `ephemeral`

`launch(process_id, resume=False, *, session_id, params=None, ephemeral=None)`, and the same
`ephemeral` on `launch_and_wait` and `launch_and_await_result`:

- **A factory:** `ephemeral=None` is the factory's default, `ttl_seconds is None`. `True`: the
  launch is ephemeral (deleted when its run ends, as today). `False`: the launch is kept; it is
  deleted by its TTL when the factory has one (its `expireAt` set when it ends, as any process
  with a TTL), and otherwise stays until it is dismissed (1.2).
- **Not a factory, with `ephemeral` not None:** refused, `reason="not-a-factory"`, as `params` is.
- **The reserved id:** `check_parameters` refuses a parameter whose id is `ephemeral`
  (`parameters[<i>]: id 'ephemeral' is reserved for the launch's own flag`): the form has a field
  of that id (3.1).
- The child's `ttlSeconds` stays the factory's, as today; an ephemeral launch never reaches it.

### 1.2 Dismissing a launch

`dismiss` of a factory's launch (`is_factory_launch`) whose run has ended (`done`, `failed`,
`cancelled`) deletes it with its descendants, as an ephemeral launch's end does, and succeeds.
A launch still running stays `not-dismissable`. (An ended ephemeral launch is already gone, so
every ended launch a caller sees is a kept one.) Dismissing any other process is unchanged: it
resets it to idle.

### 1.3 `ttlSeconds` on the wire

Every record already has `ttlSeconds`; it now reaches the clients: the engine's wire keys
(`_PROCESS_WIRE_KEYS`) and its generated `Process` model, and optio-api's stream projections (the
list's and the tree's snapshots and updates). The launch form starts its checkbox at
`ttlSeconds == null` and says how long a kept launch stays (3.1).

### 1.4 `task_for`: a launch's task built from its values

The owner, 2026-10-22 (Praeceptor's proposal generator, whose launches are an agent run or a DeepL
run by the chosen source): from the outside a factory is the same either way (values in, a new run
out); its author chooses how much a launch may differ. "if only mild differences is needed, he
writes execute which gets the params in context. if he wants completely different codepath or
widget then he writes task_for which can return a different task definition per launch".

- `TaskFactory.task_for: Callable[[dict[str, Any], str], TaskInstance] | None = None`; a factory has
  exactly one of `execute` and `task_for` (sync refuses both or neither, as other definition errors).
- At a launch, after the values are checked (and `without_no_values`): `instance = task_for(values,
  process_id)`, `process_id` the launch's (`<factory>_<8 hex>`), which an agent task needs for its
  directory. The launch's task is that instance with, set by optio whatever it returned:
  `process_id`; `params` = the values; `metadata` = the instance's, then the factory's over it, then
  `taskFactory`; `name` = `name_for(values)` when the factory has one, else the instance's; the
  factory's `ttl_seconds`. Everything else (`execute`, `ui_widget`, `supports_resume`, `cancellable`,
  …) is the instance's.
- An exception from `task_for` is not caught: the launch fails as any engine error does.

### 1.5 Resuming a kept launch

The owner, 2026-10-22: "why no resume?" (the TaskFactory design had none, a launch's task living in
memory only), then accepted resuming kept launches.

- A kept launch's record holds what rebuilding its task needs: its values (`params`), its factory
  (`metadata.taskFactory`), its process id. At `init()`, after the factories are synced, each kept
  launch whose run was interrupted (the states optio resumes or auto-resumes a task from) gets its
  task rebuilt from its factory and its stored values, as at the launch (`child_task`, or
  `task_for(values, process_id)`), and registered: it then resumes as any task does (auto-resume,
  `launch(id, resume=True)`), an agent's session included.
- Not rebuildable (its factory gone or no longer a factory, its stored values refused by the
  factory's checks or `validate`, `task_for` raising): the launch is settled `failed`, its message
  naming why, as an orphan is settled.
- `launch(<a kept launch's id>, resume=True)` resumes it when its task supports resume; without
  `resume` a launch still runs once (`not-launchable`).
- Ephemeral launches are unchanged: deleted at `init()`.
- optio-ui: a kept launch that can resume shows the resume control a task shows (its play button
  for a new launch never: that is its factory's).

## 2. optio-contracts, the engine RPC, optio-api

- `ProcessSchema`: `ttlSeconds?: number | null`.
- The engine RPC's `LaunchParams` and the `launch` route's body: `ephemeral?: boolean`.
- optio-api passes `ephemeral` through, as `params`; `dismiss` of a launch answers as any dismiss.
- The generated RPC code (clamator) is regenerated with the contract.

## 3. optio-ui

### 3.1 The launch form's fields

- **Its fields:** the factory's `parameters` (as vultus-core definitions, `fieldDefinitionOf`),
  then a built-in boolean `ephemeral`, a checkbox, last:
  - its label `t('processes.launchEphemeral', 'Remove when finished')`;
  - its description (vultus `description`, the field's help), by the factory's TTL:
    `t('processes.launchEphemeralHelp', …)` "Checked: the launch disappears from the list when it
    ends. Unchecked: it stays, with its log, until it is removed." and, with a TTL,
    `t('processes.launchEphemeralHelpTtl', …)` "… until it is removed, at most {{duration}} after
    it ends." (`duration` in words: seconds, minutes, hours or days, the largest that divides it);
  - its initial value `ttlSeconds == null`.
- **What the form sends:** the `ephemeral` field's value as the launch's `ephemeral`; the other
  fields' values as its `params`.
- **A field's label, visibility and initial value** come from its definition as merged (3.2),
  resolved as vultus resolves them (`resolveIn`, `initialValueOf`, with the field context): a field
  that is not visible (`visible: false` or `hidden: true`) is hidden whole, label and all, and its
  value is still sent.
- **A refusal naming a field that is not visible** (`invalid-parameters`, `"<id>: <what>"`) shows in
  the form as `"<label>: <what>"`, as one naming no field does.

### 3.2 Overrides

```ts
/** Field id -> a partial vultus-core definition, merged into that field's definition. */
type TaskFactoryOverrides = Readonly<Record<string, Partial<FieldDefinition>>>;
```

- **The merge**, into each field's finished definition (after `fieldDefinitionOf`, the built-in
  `ephemeral` field included): plain objects merge key by key, recursively; anything else (a
  value, an array such as `choices`, a function) replaces. Any vultus option may be overridden,
  functions of the field context included (a wire parameter cannot carry those).
- **Refused, thrown when the form renders** (an app's mistake, not the user's): an override naming
  a field the form does not have; an override of `kind` or `id`.
- One pure function, `mergeOverrides(definitions, overrides)`, exported for apps and tests.

### 3.3 The hook and the button

- **`useTaskFactory(factoryId, { overrides?, onLaunched? })` → `{ launch, isPending, dialog }`:**
  - `factoryId`: the factory's process id as the app declared it (`'pius-x-cath.drop-proposals'`),
    or its record's `_id`; the hook reads the record (`useProcess`, which takes either);
  - `launch`: a vultus action (`ActionStatus`) that opens the launch form; disabled, with its
    reason, while the record loads, when the process is not a factory, and while `isPending`;
  - `isPending`: the launch request is in flight (from the form's submit to the engine's answer);
    `launch.pending` is not that, since `launch` only opens the form;
  - `dialog`: the form's modal, for the app to render once (a hook renders nothing itself).
- **`TaskFactoryLaunchButton({ factoryId, overrides?, onLaunched?, size? })`:** the hook's action as
  an `ActionButton`, and its dialog.
- **Delivery of overrides:** they reach the form through `useTaskFactory`, `TaskFactoryLaunchButton`
  and `ProcessItem`'s `launchOverrides`: `Readonly<Record<string /* a factory's processId */,
  TaskFactoryOverrides>>`, which `ProcessItem` gives its `LaunchControls`; a factory's play button
  takes its factory's entry. `ProcessList`, `FilteredProcessList`, `ProcessTreeView` and
  `ProcessDetailView` have none: their factory forms are plain.
- The play buttons (`LaunchControls`) use the same hook; without overrides, the full form.

### 3.4 Removing a kept launch

An ended launch (`isTaskFactoryLaunch` and terminal) has a remove button where its controls are
(`LaunchControls`: the list's rows, the tree's nodes, the detail view): a vultus action,
`t('processes.removeLaunch', 'Remove')`, an icon button with that tooltip, which calls `dismiss`;
a refusal through the error routes (`optioErrorRoutes`, the dismiss route).

### 3.5 The detail view's last view of a deleted process

- **A factory launch is its own view's root** (the owner, 2026-10-22: "yes, add the tree-subtree fix
  so that we can see the real conversation for the launches"): the stream of a launch carries its
  factory's tree (the root's), so the detail view of a factory launch (depth 1) takes the launch's
  node from it: its subtree, the logs of that subtree, its widget (an agent launch's conversation)
  and its actions. Any other process keeps its root's view.
- **The cache:** module-level, in memory, the last 5 processes `ProcessDetailView` has shown, each
  with the last snapshot it saw (the subtree of the process asked for and its logs: a launch's own,
  not its factory's other launches), updated as the stream changes and
  moved to the front when shown again. A reload empties it; nothing is written to the browser's
  storage.
- **A process the stream reports gone** (`processNotFound`) that is in the cache: the view shows its
  snapshot read-only (`ProcessTreeView` without actions, `ProcessLogPanel`) under a notice,
  `t('processes.deleted', 'Process deleted.')`; no widget, no action.
- **One not in the cache:** `t('processes.notFound', 'Process not found.')`, as today's text.

### 3.6 The process filters' state in a store

The owner, 2026-10-22: "we are not saving filter state ... ProcessFilter is not integrated to
history because it's intended to be a generic component, usable by any app, therefore can't be
bound to a specific router. but we still need a solution ... an optional prop for a data store
plug". A filter change replaces the stored value (the owner: "replace"): the URL always shows the
current filters, Back does not step through them.

- **`WithFilteredProcesses`'s `store?: ArgStore`** (vultus-core's interface: `read`, `write`,
  `subscribe`): where every filter's value lives, under its field id (`search`, `filterGroup`,
  `showDetails`, `showSpecial`, and the app filters' ids); a value at its default is absent. Without
  one, a `memoryStore()` of its own, made once per mount: today's behaviour (local, gone on unmount).
- **The fields** are rendered in vultus's `stored` mode, under an `ArgStoreProvider` of that store
  that `WithFilteredProcesses` sets up; their writes are `'replace'` (vultus's for stored fields).
- **The search:** quaesitor-textus's `WithSearch` controlled, `query` read from the store and
  `onSetQuery` writing it.
- **`useProcessFilter()`** keeps its shape: the values come from the store, the setters write it,
  `filterFn` and `metadataFilter` are computed from it.
- **vultus-core (unitas):** `useStoredFields(definitions)` also returns `values`: each field's
  value by id, as its `<Field stored>` shows it (sanitized, its default where absent), one
  subscription for all, each value keeping its identity while unchanged (as `useStoredField`'s).
  optio-ui reads the filters' values with it.

## 4. Testing (no wall-clock dependence: AGENTS.md)

- optio-core: `ephemeral` None/True/False on a factory (the child's `ephemeral` flag, its TTL); on a
  task (`not-a-factory`); the reserved id refused at sync; dismiss of an ended launch deletes it with
  its descendants, of a running one is refused, of a task resets it as before.
- optio-api: `ephemeral` passed through; `ttlSeconds` in the list and tree streams.
- optio-ui: the checkbox's initial value by `ttlSeconds`, its help with and without a TTL; the flag
  and the params sent apart; `mergeOverrides` (merge, replace, the two refusals); a hidden field
  hidden whole and still sent; a refusal on a hidden field in the form; `useTaskFactory`'s states
  (loading, not a factory, pending) and its dialog; `launchOverrides` reaching a list row's form;
  the remove button on an ended launch only; the cache (5 entries, the oldest dropped, the deleted
  notice with the last log, not found without an entry); the filters with a given store (a value
  written there and read back, a default absent, the search too), and without one (as before).
- vultus-core: `useStoredFields(...).values` (defaults, sanitized values, identity kept).
- optio-core (`task_for`): a factory with `task_for` launches the instance it returns, with optio's
  fields set over it; both or neither of `execute`/`task_for` refused at sync; different values give
  different kinds of task (an execute and a widget each). Resume: a kept launch interrupted by a
  restart is rebuilt at `init()` and resumes (with `execute` and with `task_for`); one whose factory
  is gone, or whose values are now refused, is settled failed with the reason; ephemeral ones still
  deleted; `resume=True` on a kept launch resumes it, without it `not-launchable`.
- optio-ui: a resumable kept launch shows the resume control, never a play button.

## 5. Docs and release

- The packages' `AGENTS.md` and READMEs (`optio-core`, `optio-contracts`, `optio-api`, `optio-ui`) in
  the same commits as their API changes; the optio-ui key list (its `AGENTS.md`) gets the new keys.
- Released per `docs/release-cookbook.md`: optio-core, optio-contracts, optio-api (minor: new API),
  optio-ui 0.11.0 (with the filter slots of the same branch, which break 0.10.0's `filters`), after
  the vultus-core release with `useStoredFields(...).values` (optio-ui's range raised to it).

## 6. Out

- Overrides enforced by the engine; overrides of a factory's form stored with the factory.
- `ephemeral` for launches of ordinary tasks.
- Keeping the detail view's cache across reloads.
