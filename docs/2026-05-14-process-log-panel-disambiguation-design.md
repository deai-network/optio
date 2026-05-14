# ProcessLogPanel — log source disambiguation

**Status:** Design
**Date:** 2026-05-14

## Problem

`ProcessLogPanel` in `ProcessDetailView` shows log entries from every process in
the selected subtree, interleaved chronologically. Today the panel has no
working visual cue for which process emitted a given line, so users cannot tell
where a message came from when scanning a busy log.

There is a latent bug compounding this: the panel reads `entry.processName`,
but the backend sends `processLabel` (see `optio-api/src/handlers.ts:185`,
`optio-api/src/stream-poller.ts:151`, `optio-contracts/src/api-to-frontend.ts:101`).
The local `LogEntry` re-declaration in `ProcessLogPanel.tsx:19` hides the
mismatch from TypeScript, so the process label tag never renders at runtime.

## Goal

Each log row visibly carries both:

- **Source identity** — which process emitted it.
- **Tree position** — where that process sits in the hierarchy.

Without making each row significantly taller, and without requiring the user to
hover, click, or switch panels.

Non-goals: filtering, grouping/clustering, lane-based layout, search. Those can
follow as separate changes if needed once identity + position is solved.

## Approach

Three candidate visual treatments were prototyped against a real-component
playground (see "Playground as reusable scaffold" below): colored process tag,
ancestor path text, and depth-indent plus colored left bar. The third was
selected after side-by-side review.

Each row in the panel renders as:

- Horizontal **indent** proportional to that process's depth in the tree.
- A 3px **colored left bar**, stable per process.
- The **process label** inline, only on rows where the previous row's process
  differs (transition marker). Consecutive rows from the same process share the
  bar; the eye reads them as a run without label repetition.
- Existing timestamp, level tag, and message remain.

The panel needs the tree (for depth and identity index) in addition to the
already-passed `logs`. `ProcessDetailView` already has the tree from
`useProcessStream`, so plumbing is trivial.

## Color and depth assignment

A pure function `buildProcessVisuals(tree)` returns
`Map<processId, { depth, color, label }>`. Called via `useMemo` in the panel,
keyed on tree identity, so recomputation only happens when the tree updates.

- DFS over the tree assigns each process a sequential index (0, 1, 2, ...).
- `color = PALETTE[(index * STRIDE) % PALETTE.length]`.
- `PALETTE` is a fixed array of 10 perceptually-distinct colors:
  `#ef4444` (red), `#10b981` (emerald), `#3b82f6` (blue), `#f59e0b` (amber),
  `#8b5cf6` (violet), `#06b6d4` (cyan), `#ec4899` (pink), `#84cc16` (lime),
  `#f97316` (orange), `#a855f7` (purple).
- `STRIDE = 3`. `gcd(10, 3) = 1` so all 10 palette slots are visited before any
  repeat. Siblings in DFS order land 3 slots apart, preventing near-identical
  hues for processes whose log lines typically interleave.
- `depth` comes from `node.depth` (already on every `ProcessUpdate`).
- Children order is stable (sorted by `order` field in `buildTree`), so the
  DFS index is stable across renders. New children appended at the end of the
  tree get the next unused index without reshuffling existing assignments.

Indent is `min(depth, 8) * 16px`. The SSE stream caps subscriptions at
`maxDepth = 10`, so the cap leaves a small safety margin without runaway indent
at the few deepest levels that may appear.

## Bug fix prerequisite

`ProcessLogPanel.tsx`:

- Drop the local `LogEntry` interface (with `processName?: string`).
- Export `LogEntry` from `useProcessStream.ts` (it already declares the
  interface internally; promote it to `export interface LogEntry`) and import
  it from the panel. Avoids both the contracts-package touch and the type
  duplication that produced the original bug.
- Read `entry.processLabel`, not `entry.processName`.

This fix ships as part of the same change set since the new panel uses the
label anyway; leaving the duplicate type around invites the next drift.

## Component changes

### `packages/optio-ui/src/log-visuals.ts` (new)

Exports:

```ts
export const PALETTE: readonly string[];
export const STRIDE: number;
export interface ProcessVisual {
  depth: number;
  color: string;
  label: string;
}
export function buildProcessVisuals(
  tree: ProcessTreeNode | null,
): Map<string, ProcessVisual>;
```

Pure module so tests can hit it directly without React.

### `packages/optio-ui/src/components/ProcessLogPanel.tsx`

- New prop `tree: ProcessTreeNode | null`.
- Remove local `LogEntry` interface; import from the hook (or contracts).
- `const visuals = useMemo(() => buildProcessVisuals(tree), [tree])`.
- Render loop tracks `prevProcessId` so the label only prints on transition.
- Each row is a flex container:
  - `paddingLeft: min(depth, 8) * 16`
  - left bar `<div style={{ width: 3, background: color, alignSelf: 'stretch', marginRight: 8 }} />`
  - inner row identical structure to today (timestamp · level tag · optional
    label · message)
- Unknown processId fallback: `{ depth: 0, color: '#666', label: entry.processLabel }`.
  This handles a log entry that arrives microseconds before the corresponding
  tree update has been applied to state, and unknown-id cases where the entry's
  process was pruned by `maxDepth`.

### `packages/optio-ui/src/components/ProcessDetailView.tsx`

Pass `tree` to `<ProcessLogPanel>` in both the widget-layout branch (which
also passes `fillParent`) and the default branch. `fillParent` semantics
are unchanged.

## Edge cases

- **Tree absent / null** (initial load before SSE delivers any tree update):
  panel renders flat — no indent, no bar, no transition label. Same shape as
  today minus the broken `processName` tag.
- **Log entry for unknown processId**: fallback visual as above. Common during
  the brief window between log arrival and tree state commit; also when a
  process was pruned by `maxDepth`.
- **Tree depth greater than 8**: indent capped at 8 levels (128px). Color bar
  still distinguishes processes at deeper levels.
- **More than 10 distinct processes in a tree**: palette wraps. Stride 3 means
  the first repeat is between DFS-index 0 and DFS-index 10, which are far apart
  in the tree — not siblings — so adjacent log lines from those two processes
  having the same color is unlikely. If real trees routinely exceed 10
  processes we revisit by widening the palette; not anticipated in current use.
- **Sibling processes with identical names**: color still disambiguates.
- **Empty logs**: existing `Empty` component path unchanged.
- **First row of the log**: no `prev`, so the transition label always prints.

## Testing

### Unit tests

`packages/optio-ui/src/__tests__/log-visuals.test.ts` (new):

- Empty tree → empty map.
- Single-node tree → root gets depth 0, `PALETTE[0]`.
- Two-level tree → first sibling gets `PALETTE[3]`, second `PALETTE[6]`.
- 11-node tree → 11th entry's color is `PALETTE[0]` (wraps); assert that the
  wraparound pair is not adjacent in DFS order.
- Re-call with same tree returns same colors for same ids.
- Re-call with extended tree (new leaf appended) leaves existing ids' colors
  unchanged.

`packages/optio-ui/src/__tests__/ProcessLogPanel.test.tsx` (updated/extended):

- Renders label tag on transition row only.
- Skips label tag when consecutive rows share processId.
- Applies `paddingLeft` matching `depth * 16` (capped at 128).
- Unknown processId → fallback color and label drawn from entry.
- `tree={null}` → flat render, no bar, no label.
- Existing assertions for empty-logs / scroll-on-append remain green.

### Smoke

`ProcessDetailView` test (if one exists, otherwise added): asserts that
`<ProcessLogPanel>` receives the `tree` prop in both the widget and default
branches.

### Manual

Playground topic stays in the repo as the durable visual reference (see next
section). Final post-implementation visual confirmation: open
`pnpm --filter optio-dashboard dev:playground`, switch to the log-panel topic,
verify the indent-bar variant matches the panel as rendered inside a real
`ProcessDetailView` against a running optio-demo backend.

## Playground as reusable scaffold

The browser playground used during brainstorming becomes a permanent fixture
under `packages/optio-dashboard/playground/`, organized so that future visual
brainstorming work can add new topics without rebuilding the scaffold.

### Directory layout

```
packages/optio-dashboard/playground/
  README.md                    # how to add a topic, conventions
  vite.config.ts               # shared, aliases optio-ui/* to its src
  index.html                   # shared
  i18n.ts                      # shared, minimal i18next bootstrap
  main.tsx                     # topic registry + hash-routed left nav
  topics/
    log-panel/
      index.ts                 # exports { name, App }
      fixtures.ts              # tree + LogEntry[] fixtures
      variants/
        baseline.tsx
        colored-tag.tsx
        path.tsx
        indent-bar.tsx
```

### Topic contract

Each topic directory exports a default-shaped module:

```ts
// topics/<topic>/index.ts
import { App } from './App.js';
export default { name: 'Log panel', App };
```

`main.tsx` maintains an explicit list of imports:

```ts
import logPanel from './topics/log-panel/index.js';
const TOPICS = [logPanel];
```

A new topic is added by creating a directory under `topics/`, exporting a
default with `{ name, App }`, and appending to `TOPICS`. No plugin loader.
The left nav is hash-routed (`#<topic-slug>`), so the variant selector inside a
topic can use the rest of the hash if needed.

### Script

`packages/optio-dashboard/package.json` gains:

```json
"dev:playground": "vite --config playground/vite.config.ts"
```

So the entry point is `pnpm --filter optio-dashboard dev:playground`.

### Discovery

Root `AGENTS.md` gets a short note under "Debug tools":

> **UI brainstorming.** Render real `optio-ui` components against fixtures
> with `pnpm --filter optio-dashboard dev:playground`. Add a new topic under
> `packages/optio-dashboard/playground/topics/<name>/`; see the playground
> `README.md` for the contract. Useful when comparing visual variants side
> by side before committing to a design.

### Boundaries

- Playground code is **not** gitignored; it ships in the repo as reference.
- Playground is **not** a release artifact; the dashboard's production build
  ignores it (it sits outside `src/app/`, the existing vite root for the real
  dashboard).
- Topics carry their own fixtures. No shared fixture module — each topic owns
  what it needs, keeping topics independently understandable and removable.

## Out of scope

- Filtering or pinning logs to a specific process / subtree.
- Search.
- Grouping (Slack-thread-style lanes).
- Backfilling color into other surfaces (e.g. `ProcessTreeView`). If the tree
  ever wants the same palette, the `log-visuals` module is the place to share
  it; this design leaves that connection unmade.
