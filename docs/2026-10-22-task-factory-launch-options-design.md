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
- **`launchOverrides`** on `ProcessItem`, `ProcessList`, `FilteredProcessList`, `ProcessTreeView` and
  `ProcessDetailView`: `Readonly<Record<string /* a factory's processId */, TaskFactoryOverrides>>`,
  passed down as `onLaunched` is; a factory's play button takes its factory's entry.
- The play buttons (`LaunchControls`) use the same hook; without overrides, the full form.

### 3.4 Removing a kept launch

An ended launch (`isTaskFactoryLaunch` and terminal) has a remove button where its controls are
(`LaunchControls`: the list's rows, the tree's nodes, the detail view): a vultus action,
`t('processes.removeLaunch', 'Remove')`, an icon button with that tooltip, which calls `dismiss`;
a refusal through the error routes (`optioErrorRoutes`, the dismiss route).

### 3.5 The detail view's last view of a deleted process

- **The cache:** module-level, in memory, the last 5 processes `ProcessDetailView` has shown, each
  with the last snapshot it saw (the stream's `tree` and `logs`), updated as the stream changes and
  moved to the front when shown again. A reload empties it; nothing is written to the browser's
  storage.
- **A process the stream reports gone** (`processNotFound`) that is in the cache: the view shows its
  snapshot read-only (`ProcessTreeView` without actions, `ProcessLogPanel`) under a notice,
  `t('processes.deleted', 'Process deleted.')`; no widget, no action.
- **One not in the cache:** `t('processes.notFound', 'Process not found.')`, as today's text.

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
  notice with the last log, not found without an entry).

## 5. Docs and release

- The packages' `AGENTS.md` and READMEs (`optio-core`, `optio-contracts`, `optio-api`, `optio-ui`) in
  the same commits as their API changes; the optio-ui key list (its `AGENTS.md`) gets the new keys.
- Released per `docs/release-cookbook.md`: optio-core, optio-contracts, optio-api (minor: new API),
  optio-ui 0.11.0 (with the filter slots of the same branch, which break 0.10.0's `filters`).

## 6. Out

- Overrides enforced by the engine; overrides of a factory's form stored with the factory.
- `ephemeral` for launches of ordinary tasks.
- Keeping the detail view's cache across reloads.
