# ProcessLogPanel — log source disambiguation: Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make `ProcessLogPanel` reveal each log entry's source process (identity) and its position in the process tree (depth), so users scanning interleaved logs in `ProcessDetailView` can tell which process emitted each line.

**Architecture:** Add a small pure module (`log-visuals.ts`) that maps each process in a tree to `{ depth, color, label }` using DFS-order traversal and a fixed perceptually-distinct palette with a stride to space out neighbors. `ProcessLogPanel` consumes this map plus the existing log stream; each row gets a colored left bar, depth-based indent, and a transition label that prints only when the previous row's process differs. Also fixes a latent bug (panel reads `processName` while backend sends `processLabel`).

**Tech Stack:** TypeScript, React 19, Ant Design 5, Vitest, React Testing Library, pnpm workspaces.

**Spec:** `docs/2026-05-14-process-log-panel-disambiguation-design.md`.

---

## Pre-flight

These steps establish the working environment. Do them once before any task.

- [ ] **P0: Create a feature branch in-place** (no worktree).

```bash
git switch -c feat/process-log-panel-disambiguation
```

This is per the user's standing preference: feature branch always, not main, not worktree.

- [ ] **P1: Confirm package manager and base tooling.**

```bash
cd /home/csillag/deai/optio
pnpm -v           # any 9.x is fine
node -v           # 20.x or 22.x
```

- [ ] **P2: Install workspace deps if needed.**

```bash
pnpm install
```

- [ ] **P3: Baseline test run for optio-ui.**

```bash
cd /home/csillag/deai/optio
pnpm --filter optio-ui test
```

Expected: all green. If anything is red before you start, stop and surface it — don't trade off pre-existing failures with your own.

- [ ] **P4: Quick smoke that the playground vite serves.**

```bash
cd /home/csillag/deai/optio/packages/optio-dashboard
./node_modules/vite/bin/vite.js --config playground/vite.config.ts &
sleep 2
curl -s -o /dev/null -w "%{http_code}\n" http://localhost:5174/
kill %1
```

Expected: `200`. If not, the playground scaffold from the spec phase didn't land — investigate before continuing.

---

## File Structure

After this plan is complete, the following files exist:

```
docs/
  2026-05-14-process-log-panel-disambiguation-design.md   # (already committed)
  2026-05-14-process-log-panel-disambiguation-plan.md     # this file

packages/optio-ui/src/
  log-visuals.ts                                          # NEW: pure depth/color/label module
  hooks/useProcessStream.ts                               # MODIFIED: export LogEntry
  components/ProcessLogPanel.tsx                          # MODIFIED: bug fix + new rendering
  components/ProcessDetailView.tsx                        # MODIFIED: pass tree prop
  __tests__/log-visuals.test.ts                           # NEW
  __tests__/ProcessLogPanel.test.tsx                      # NEW

packages/optio-dashboard/
  package.json                                            # MODIFIED: add dev:playground script
  playground/
    README.md                                             # NEW
    index.html                                            # (already exists, unchanged)
    vite.config.ts                                        # (already exists, unchanged)
    i18n.ts                                               # (already exists, unchanged)
    main.tsx                                              # MODIFIED: registry-driven nav
    topics/
      log-panel/
        index.ts                                          # NEW: { name, App } export
        App.tsx                                           # NEW: hosts all variants
        fixtures.ts                                       # MOVED from playground/fixtures.ts
        variants/
          baseline.tsx                                    # MOVED from playground/variants/
          colored-tag.tsx                                 # MOVED
          path.tsx                                        # MOVED
          indent-bar.tsx                                  # MOVED

AGENTS.md                                                 # MODIFIED: short pointer to playground
```

Files removed (after move):
- `packages/optio-dashboard/playground/fixtures.ts`
- `packages/optio-dashboard/playground/variants/` (entire directory)

---

### Task 1: Fix `processName` → `processLabel` and export `LogEntry` from the hook

Smallest independent change. Restores the broken label display before any new rendering work goes in.

**Files:**
- Modify: `packages/optio-ui/src/hooks/useProcessStream.ts:19-26`
- Modify: `packages/optio-ui/src/components/ProcessLogPanel.tsx:15-20, 77-79`

- [ ] **Step 1.1: Read the current files** to confirm line numbers.

```bash
sed -n '15,30p' packages/optio-ui/src/hooks/useProcessStream.ts
sed -n '15,85p' packages/optio-ui/src/components/ProcessLogPanel.tsx
```

- [ ] **Step 1.2: Promote `LogEntry` in the hook to a named export.**

In `packages/optio-ui/src/hooks/useProcessStream.ts`, change the interface declaration:

```ts
// Before:
interface LogEntry {
  timestamp: string;
  level: string;
  message: string;
  data?: Record<string, unknown>;
  processId: string;
  processLabel: string;
}

// After:
export interface LogEntry {
  timestamp: string;
  level: string;
  message: string;
  data?: Record<string, unknown>;
  processId: string;
  processLabel: string;
}
```

- [ ] **Step 1.3: Replace the local `LogEntry` in the panel and switch the field name.**

In `packages/optio-ui/src/components/ProcessLogPanel.tsx`:

Replace the entire local `LogEntry` declaration:

```ts
// Delete:
interface LogEntry {
  timestamp: string;
  level: string;
  message: string;
  processName?: string;
}
```

Add an import at the top of the file (after the antd / react imports):

```ts
import type { LogEntry } from '../hooks/useProcessStream.js';
```

Update the render code (around lines 77-79) to read `processLabel`:

```tsx
// Before:
{entry.processName && (
  <Text type="secondary" style={{ fontSize: 11 }}>[{entry.processName}]</Text>
)}

// After:
{entry.processLabel && (
  <Text type="secondary" style={{ fontSize: 11 }}>[{entry.processLabel}]</Text>
)}
```

- [ ] **Step 1.4: Type-check.**

```bash
cd packages/optio-ui
./node_modules/.bin/tsc --noEmit
```

Expected: no errors. (Per user preference: use `node_modules/.bin/tsc` directly, not `npx tsc`.)

- [ ] **Step 1.5: Run optio-ui tests.**

```bash
pnpm --filter optio-ui test
```

Expected: all green. The existing `ProcessDetailView` test doesn't introspect log row internals, so this should not break it.

- [ ] **Step 1.6: Commit.**

```bash
git add packages/optio-ui/src/hooks/useProcessStream.ts \
        packages/optio-ui/src/components/ProcessLogPanel.tsx
git commit -m "optio-ui: ProcessLogPanel — read processLabel, drop local LogEntry duplicate"
```

---

### Task 2: New module `log-visuals.ts` — pure tree → visuals map

Pure logic, no React. Built test-first.

**Files:**
- Create: `packages/optio-ui/src/log-visuals.ts`
- Create: `packages/optio-ui/src/__tests__/log-visuals.test.ts`

- [ ] **Step 2.1: Write the failing test file.**

Create `packages/optio-ui/src/__tests__/log-visuals.test.ts`:

```ts
import { describe, it, expect } from 'vitest';
import { buildProcessVisuals, PALETTE, STRIDE } from '../log-visuals.js';
import type { ProcessTreeNode } from '../hooks/useProcessStream.js';

function leaf(id: string, depth: number): ProcessTreeNode {
  return {
    _id: id,
    parentId: null,
    name: id,
    status: { state: 'running' },
    progress: { percent: null },
    cancellable: false,
    depth,
    order: 0,
    children: [],
  };
}

function node(
  id: string,
  depth: number,
  children: ProcessTreeNode[],
): ProcessTreeNode {
  return { ...leaf(id, depth), children };
}

describe('buildProcessVisuals', () => {
  it('returns an empty map for a null tree', () => {
    const v = buildProcessVisuals(null);
    expect(v.size).toBe(0);
  });

  it('assigns the root depth 0 and PALETTE[0]', () => {
    const tree = leaf('root', 0);
    const v = buildProcessVisuals(tree);
    const root = v.get('root');
    expect(root).toBeDefined();
    expect(root!.depth).toBe(0);
    expect(root!.color).toBe(PALETTE[0]);
    expect(root!.label).toBe('root');
  });

  it('spaces siblings by STRIDE in the palette', () => {
    const tree = node('root', 0, [leaf('a', 1), leaf('b', 1)]);
    const v = buildProcessVisuals(tree);
    expect(v.get('root')!.color).toBe(PALETTE[0]);
    expect(v.get('a')!.color).toBe(PALETTE[(1 * STRIDE) % PALETTE.length]);
    expect(v.get('b')!.color).toBe(PALETTE[(2 * STRIDE) % PALETTE.length]);
  });

  it('records depth from the tree', () => {
    const tree = node('root', 0, [
      node('a', 1, [leaf('aa', 2)]),
    ]);
    const v = buildProcessVisuals(tree);
    expect(v.get('root')!.depth).toBe(0);
    expect(v.get('a')!.depth).toBe(1);
    expect(v.get('aa')!.depth).toBe(2);
  });

  it('wraps the palette after 10 processes', () => {
    // Build a flat chain of 11 nodes: root -> c1 -> c2 -> ... -> c10
    let inner: ProcessTreeNode = leaf('c10', 10);
    for (let i = 9; i >= 1; i--) {
      inner = node(`c${i}`, i, [inner]);
    }
    const tree = node('root', 0, [inner]);

    const v = buildProcessVisuals(tree);
    // DFS visits: root, c1, c2, ..., c10. That's 11 nodes.
    const colorAt = (idx: number) => PALETTE[(idx * STRIDE) % PALETTE.length];
    expect(v.get('root')!.color).toBe(colorAt(0));
    expect(v.get('c1')!.color).toBe(colorAt(1));
    expect(v.get('c10')!.color).toBe(colorAt(10));

    // The wraparound (index 10 == index 0 in palette terms when STRIDE*10 % 10 == 0)
    // is between root and c10. They are 10 DFS steps apart, not adjacent siblings.
    expect(v.get('c10')!.color).toBe(v.get('root')!.color);
  });

  it('is stable: appending a new leaf does not change existing colors', () => {
    const before = node('root', 0, [leaf('a', 1)]);
    const after = node('root', 0, [leaf('a', 1), leaf('b', 1)]);
    const v1 = buildProcessVisuals(before);
    const v2 = buildProcessVisuals(after);
    expect(v2.get('root')!.color).toBe(v1.get('root')!.color);
    expect(v2.get('a')!.color).toBe(v1.get('a')!.color);
  });
});
```

- [ ] **Step 2.2: Run the test to confirm it fails to import the module.**

```bash
cd packages/optio-ui
./node_modules/.bin/vitest run src/__tests__/log-visuals.test.ts
```

Expected: FAIL — `Cannot find module '../log-visuals.js'` or similar.

- [ ] **Step 2.3: Implement `log-visuals.ts`.**

Create `packages/optio-ui/src/log-visuals.ts`:

```ts
import type { ProcessTreeNode } from './hooks/useProcessStream.js';

/**
 * Perceptually-distinct color palette used to give each process in a tree
 * a stable visual identity in log views.
 *
 * The PALETTE is paired with a STRIDE: each process is assigned a sequential
 * DFS index and colored as PALETTE[(index * STRIDE) % PALETTE.length].
 * gcd(PALETTE.length, STRIDE) must equal 1 so that all palette slots are
 * visited before any repeats. STRIDE > 1 ensures that adjacent indices (e.g.
 * sibling processes whose log lines tend to interleave) land far apart in
 * the palette, avoiding near-identical hues.
 */
export const PALETTE: readonly string[] = [
  '#ef4444', // red
  '#10b981', // emerald
  '#3b82f6', // blue
  '#f59e0b', // amber
  '#8b5cf6', // violet
  '#06b6d4', // cyan
  '#ec4899', // pink
  '#84cc16', // lime
  '#f97316', // orange
  '#a855f7', // purple
];

export const STRIDE = 3;

export interface ProcessVisual {
  depth: number;
  color: string;
  label: string;
}

export function buildProcessVisuals(
  tree: ProcessTreeNode | null,
): Map<string, ProcessVisual> {
  const out = new Map<string, ProcessVisual>();
  if (!tree) return out;

  let index = 0;
  const visit = (node: ProcessTreeNode): void => {
    out.set(node._id, {
      depth: node.depth,
      color: PALETTE[(index * STRIDE) % PALETTE.length],
      label: node.name,
    });
    index += 1;
    for (const child of node.children) visit(child);
  };
  visit(tree);
  return out;
}
```

- [ ] **Step 2.4: Run the test to confirm it passes.**

```bash
cd packages/optio-ui
./node_modules/.bin/vitest run src/__tests__/log-visuals.test.ts
```

Expected: PASS (6 tests).

- [ ] **Step 2.5: Type-check the package.**

```bash
cd packages/optio-ui
./node_modules/.bin/tsc --noEmit
```

Expected: no errors.

- [ ] **Step 2.6: Commit.**

```bash
git add packages/optio-ui/src/log-visuals.ts \
        packages/optio-ui/src/__tests__/log-visuals.test.ts
git commit -m "optio-ui: log-visuals module — DFS-order palette + depth lookup"
```

---

### Task 3: `ProcessLogPanel` consumes `tree` and renders indent + bar + transition label

This is the rendering change. Built test-first.

**Files:**
- Modify: `packages/optio-ui/src/components/ProcessLogPanel.tsx` (full panel rewrite of the render loop)
- Create: `packages/optio-ui/src/__tests__/ProcessLogPanel.test.tsx`

- [ ] **Step 3.1: Write the failing test file.**

Create `packages/optio-ui/src/__tests__/ProcessLogPanel.test.tsx`:

```tsx
import { describe, it, expect, afterEach } from 'vitest';
import { render, cleanup, screen, within } from '@testing-library/react';
import React from 'react';
import { ProcessLogPanel } from '../components/ProcessLogPanel.js';
import type { LogEntry, ProcessTreeNode } from '../hooks/useProcessStream.js';
import { PALETTE, STRIDE } from '../log-visuals.js';

// i18next is initialized by the consuming app at runtime; tests rely on the
// raw key fallback for 'common.noData'. To avoid coupling to that, none of
// these tests exercise the empty-logs path.

function leaf(id: string, depth: number, name = id): ProcessTreeNode {
  return {
    _id: id,
    parentId: null,
    name,
    status: { state: 'running' },
    progress: { percent: null },
    cancellable: false,
    depth,
    order: 0,
    children: [],
  };
}

function tree2level(): ProcessTreeNode {
  return {
    ...leaf('root', 0, 'root'),
    children: [
      leaf('a', 1, 'alpha'),
      leaf('b', 1, 'beta'),
    ],
  };
}

function entry(
  processId: string,
  processLabel: string,
  message: string,
  level = 'info',
  timestampMs = 0,
): LogEntry {
  return {
    timestamp: new Date(timestampMs).toISOString(),
    level,
    message,
    processId,
    processLabel,
  };
}

afterEach(() => cleanup());

describe('ProcessLogPanel', () => {
  it('renders a label tag only on transition rows', () => {
    const logs: LogEntry[] = [
      entry('a', 'alpha', 'first'),
      entry('a', 'alpha', 'second'),
      entry('b', 'beta', 'third'),
      entry('b', 'beta', 'fourth'),
      entry('a', 'alpha', 'fifth'),
    ];
    render(<ProcessLogPanel logs={logs} tree={tree2level()} />);

    // 'alpha' label appears on rows 1 and 5 (transitions); 'beta' on row 3.
    expect(screen.getAllByText('alpha')).toHaveLength(2);
    expect(screen.getAllByText('beta')).toHaveLength(1);
  });

  it('applies depth-based padding to rows', () => {
    const logs: LogEntry[] = [
      entry('root', 'root', 'r'),
      entry('a', 'alpha', 'a'),
    ];
    const { container } = render(
      <ProcessLogPanel logs={logs} tree={tree2level()} />,
    );

    const rows = container.querySelectorAll('[data-testid="log-row"]');
    expect(rows.length).toBe(2);
    expect((rows[0] as HTMLElement).style.paddingLeft).toBe('0px');
    expect((rows[1] as HTMLElement).style.paddingLeft).toBe('16px');
  });

  it('renders a colored left bar matching the assigned color', () => {
    const logs: LogEntry[] = [entry('a', 'alpha', 'a')];
    const { container } = render(
      <ProcessLogPanel logs={logs} tree={tree2level()} />,
    );

    const row = container.querySelector('[data-testid="log-row"]') as HTMLElement;
    const bar = row.querySelector('[data-testid="log-bar"]') as HTMLElement;
    // DFS order: root (0), a (1). a's color = PALETTE[(1 * STRIDE) % len].
    const expected = PALETTE[(1 * STRIDE) % PALETTE.length];
    expect(bar.style.backgroundColor || bar.getAttribute('style') || '').toContain(
      // JSDOM normalizes some color formats; check the literal hex appears
      // in the rendered inline style attribute.
      expected,
    );
  });

  it('caps indent at 8 * 16 = 128px for deep trees', () => {
    // Build a deep chain root -> d1 -> ... -> d12
    let cur: ProcessTreeNode = leaf('d12', 12);
    for (let i = 11; i >= 1; i--) {
      cur = { ...leaf(`d${i}`, i), children: [cur] };
    }
    const tree: ProcessTreeNode = { ...leaf('root', 0), children: [cur] };

    const logs: LogEntry[] = [entry('d12', 'd12', 'deep')];
    const { container } = render(<ProcessLogPanel logs={logs} tree={tree} />);

    const row = container.querySelector('[data-testid="log-row"]') as HTMLElement;
    expect(row.style.paddingLeft).toBe('128px');
  });

  it('falls back gracefully for an unknown processId', () => {
    const logs: LogEntry[] = [entry('ghost', 'ghost-label', 'orphan')];
    const { container } = render(
      <ProcessLogPanel logs={logs} tree={tree2level()} />,
    );

    const row = container.querySelector('[data-testid="log-row"]') as HTMLElement;
    expect(row.style.paddingLeft).toBe('0px');
    // Label comes from the entry itself when tree has no entry.
    expect(within(row).getByText('ghost-label')).toBeTruthy();
  });

  it('renders flat (no indent, no bar) when tree is null', () => {
    const logs: LogEntry[] = [entry('a', 'alpha', 'a')];
    const { container } = render(<ProcessLogPanel logs={logs} tree={null} />);

    const row = container.querySelector('[data-testid="log-row"]') as HTMLElement;
    expect(row.style.paddingLeft).toBe('0px');
    expect(row.querySelector('[data-testid="log-bar"]')).toBeNull();
  });
});
```

- [ ] **Step 3.2: Run the test file to confirm failures.**

```bash
cd packages/optio-ui
./node_modules/.bin/vitest run src/__tests__/ProcessLogPanel.test.tsx
```

Expected: FAIL — several assertions, including missing `data-testid="log-row"`, missing `tree` prop on the component type, and likely a label-on-transition mismatch.

- [ ] **Step 3.3: Rewrite `ProcessLogPanel.tsx` with the new rendering.**

Replace the entire file at `packages/optio-ui/src/components/ProcessLogPanel.tsx` with:

```tsx
import { Tag, Typography, Empty } from 'antd';
import { useEffect, useMemo, useRef } from 'react';
import { useTranslation } from 'react-i18next';
import type { LogEntry, ProcessTreeNode } from '../hooks/useProcessStream.js';
import { buildProcessVisuals, type ProcessVisual } from '../log-visuals.js';

const { Text } = Typography;

const LEVEL_COLORS: Record<string, string> = {
  event: 'cyan',
  info: 'blue',
  debug: 'default',
  warning: 'gold',
  error: 'red',
};

const INDENT_PX = 16;
const MAX_INDENT_DEPTH = 8;
const UNKNOWN_COLOR = '#666';

interface ProcessLogPanelProps {
  logs: LogEntry[];
  tree: ProcessTreeNode | null;
  /**
   * When true, the panel fills its parent's height (use with a flex-sized
   * container) instead of the default `maxHeight: 400`. Auto-scroll still
   * sticks to the bottom while the user hasn't manually scrolled up.
   */
  fillParent?: boolean;
}

function visualFor(
  visuals: Map<string, ProcessVisual>,
  entry: LogEntry,
): ProcessVisual {
  return (
    visuals.get(entry.processId) ?? {
      depth: 0,
      color: UNKNOWN_COLOR,
      label: entry.processLabel,
    }
  );
}

export function ProcessLogPanel({ logs, tree, fillParent }: ProcessLogPanelProps) {
  const { t } = useTranslation();
  const listRef = useRef<HTMLDivElement>(null);
  const isAtBottomRef = useRef(true);

  const visuals = useMemo(() => buildProcessVisuals(tree), [tree]);

  const handleScroll = () => {
    if (listRef.current) {
      const { scrollTop, scrollHeight, clientHeight } = listRef.current;
      isAtBottomRef.current = scrollHeight - scrollTop - clientHeight < 30;
    }
  };

  useEffect(() => {
    if (listRef.current && isAtBottomRef.current) {
      listRef.current.scrollTop = listRef.current.scrollHeight;
    }
  }, [logs.length]);

  if (logs.length === 0) {
    return <Empty description={t('common.noData')} />;
  }

  return (
    <div
      ref={listRef}
      onScroll={handleScroll}
      style={{
        ...(fillParent ? { height: '100%' } : { maxHeight: 400 }),
        overflow: 'auto',
        border: '1px solid #303030',
        borderRadius: 4,
        padding: 8,
        fontFamily: 'monospace',
        fontSize: 12,
      }}
    >
      {logs.map((entry, idx) => {
        const v = visualFor(visuals, entry);
        const prev = idx > 0 ? logs[idx - 1] : null;
        const transition = !prev || prev.processId !== entry.processId;
        const indent = Math.min(v.depth, MAX_INDENT_DEPTH) * INDENT_PX;
        const showBar = tree !== null;

        return (
          <div
            key={idx}
            data-testid="log-row"
            style={{
              display: 'flex',
              alignItems: 'baseline',
              marginBottom: 2,
              paddingLeft: indent,
            }}
          >
            {showBar && (
              <div
                data-testid="log-bar"
                style={{
                  width: 3,
                  alignSelf: 'stretch',
                  background: v.color,
                  marginRight: 8,
                  flex: '0 0 auto',
                }}
              />
            )}
            <div
              style={{
                display: 'flex',
                gap: 8,
                alignItems: 'baseline',
                flex: 1,
                minWidth: 0,
              }}
            >
              <Text type="secondary" style={{ whiteSpace: 'nowrap', fontSize: 11 }}>
                {new Date(entry.timestamp).toLocaleTimeString()}
              </Text>
              <Tag color={LEVEL_COLORS[entry.level] ?? 'default'} style={{ fontSize: 10 }}>
                {entry.level.toUpperCase()}
              </Tag>
              {transition && (
                <Text style={{ color: v.color, fontSize: 11, fontWeight: 600, whiteSpace: 'nowrap' }}>
                  {v.label}
                </Text>
              )}
              <Text>{entry.message}</Text>
            </div>
          </div>
        );
      })}
    </div>
  );
}
```

Notes for the implementer:

- The colored left bar is rendered only when a `tree` is provided. With `tree={null}` the panel falls back to a flat layout that still uses `processLabel` for the transition label.
- `MAX_INDENT_DEPTH = 8` mirrors the spec; the SSE `maxDepth = 10` cap leaves a 2-level safety margin.
- Inline color on the bar is intentional: the palette is data-driven, not theme-driven, so CSS classes don't help.

- [ ] **Step 3.4: Run the panel test file; expect all pass.**

```bash
cd packages/optio-ui
./node_modules/.bin/vitest run src/__tests__/ProcessLogPanel.test.tsx
```

Expected: PASS (6 tests).

- [ ] **Step 3.5: Run the full optio-ui test suite to confirm no regressions.**

```bash
pnpm --filter optio-ui test
```

Expected: all green. If `ProcessDetailView.test.tsx` breaks, it's because the panel now requires a `tree` prop — fix in Task 4.

> If you see a TypeScript error about `tree` being required at the existing call sites in `ProcessDetailView.tsx`, that's expected and gets resolved by Task 4. You may proceed to Task 4 before this test suite goes green, but commit only after both tasks are complete and the suite passes. To keep TDD discipline, finish Task 4 next without intermediate commits.

- [ ] **Step 3.6: Type-check.**

```bash
cd packages/optio-ui
./node_modules/.bin/tsc --noEmit
```

If `ProcessDetailView.tsx` errors due to the new required `tree` prop, proceed to Task 4 first.

- [ ] **Step 3.7: Do not commit yet** — Task 4 lands in the same commit since the panel API change and its call site go together.

---

### Task 4: `ProcessDetailView` passes `tree` to the panel

Trivial wiring, but its own test update.

**Files:**
- Modify: `packages/optio-ui/src/components/ProcessDetailView.tsx:65-89`
- Read: `packages/optio-ui/src/__tests__/ProcessDetailView.test.tsx` (no edits expected; verify it still passes)

- [ ] **Step 4.1: Update both `<ProcessLogPanel>` call sites.**

In `packages/optio-ui/src/components/ProcessDetailView.tsx`, change:

```tsx
// Widget branch (around line 71):
<ProcessLogPanel logs={logs} fillParent />
// becomes:
<ProcessLogPanel logs={logs} tree={tree} fillParent />

// Default branch (around line 87):
<ProcessLogPanel logs={logs} />
// becomes:
<ProcessLogPanel logs={logs} tree={tree} />
```

`tree` is already in scope from the `useProcessStream` destructure at line 25.

- [ ] **Step 4.2: Type-check.**

```bash
cd packages/optio-ui
./node_modules/.bin/tsc --noEmit
```

Expected: no errors.

- [ ] **Step 4.3: Run the full optio-ui suite.**

```bash
pnpm --filter optio-ui test
```

Expected: all green. Both the new `ProcessLogPanel.test.tsx` and the existing `ProcessDetailView.test.tsx` should pass.

- [ ] **Step 4.4: Commit Tasks 3 and 4 together.**

```bash
git add packages/optio-ui/src/components/ProcessLogPanel.tsx \
        packages/optio-ui/src/components/ProcessDetailView.tsx \
        packages/optio-ui/src/__tests__/ProcessLogPanel.test.tsx
git commit -m "optio-ui: ProcessLogPanel — indent + colored bar + transition label, tree-aware"
```

---

### Task 5: Playground reorg — `topics/log-panel/` + registry-driven `main.tsx`

Move the existing playground files into a topic subfolder so the scaffold can host additional topics later.

**Files:**
- Create: `packages/optio-dashboard/playground/topics/log-panel/index.ts`
- Create: `packages/optio-dashboard/playground/topics/log-panel/App.tsx`
- Move: `packages/optio-dashboard/playground/fixtures.ts` → `packages/optio-dashboard/playground/topics/log-panel/fixtures.ts`
- Move: `packages/optio-dashboard/playground/variants/baseline.tsx` → `packages/optio-dashboard/playground/topics/log-panel/variants/baseline.tsx`
- Move: `packages/optio-dashboard/playground/variants/colored-tag.tsx` → `packages/optio-dashboard/playground/topics/log-panel/variants/colored-tag.tsx`
- Move: `packages/optio-dashboard/playground/variants/path.tsx` → `packages/optio-dashboard/playground/topics/log-panel/variants/path.tsx`
- Move: `packages/optio-dashboard/playground/variants/indent-bar.tsx` → `packages/optio-dashboard/playground/topics/log-panel/variants/indent-bar.tsx`
- Modify: `packages/optio-dashboard/playground/main.tsx` (registry + nav)
- Create: `packages/optio-dashboard/playground/README.md`

- [ ] **Step 5.1: Make the directories.**

```bash
mkdir -p packages/optio-dashboard/playground/topics/log-panel/variants
```

- [ ] **Step 5.2: Move the files with git so history is preserved.**

```bash
cd packages/optio-dashboard/playground
git mv fixtures.ts topics/log-panel/fixtures.ts
git mv variants/baseline.tsx topics/log-panel/variants/baseline.tsx
git mv variants/colored-tag.tsx topics/log-panel/variants/colored-tag.tsx
git mv variants/path.tsx topics/log-panel/variants/path.tsx
git mv variants/indent-bar.tsx topics/log-panel/variants/indent-bar.tsx
rmdir variants
cd ../../..
```

Note: these files were created during brainstorming and are currently uncommitted. If `git mv` fails because they are untracked, fall back to `git add . && git status` first to stage them, or use plain `mv` followed by `git add`. Either way, the end state is identical.

- [ ] **Step 5.3: Update each moved file's relative imports.**

Each `variants/*.tsx` previously imported `../fixtures.js`. After the move, the new location (`topics/log-panel/variants/`) sits one level below `topics/log-panel/`, so `../fixtures.js` still resolves correctly. **No edits needed** — confirm by inspection:

```bash
grep -rn "from '" packages/optio-dashboard/playground/topics/log-panel/variants/
```

Expected: all imports use `'../fixtures.js'` (still valid).

Confirm `baseline.tsx` still reaches the real component. The path was `../../../optio-ui/src/components/ProcessLogPanel.js`; from the new depth (`topics/log-panel/variants/`), the correct relative path is `../../../../../optio-ui/src/components/ProcessLogPanel.js`. Update it:

```tsx
// In topics/log-panel/variants/baseline.tsx, change:
import { ProcessLogPanel } from '../../../optio-ui/src/components/ProcessLogPanel.js';
// to:
import { ProcessLogPanel } from '../../../../../optio-ui/src/components/ProcessLogPanel.js';
```

- [ ] **Step 5.4: Update the baseline variant to pass `tree`.**

Now that `ProcessLogPanel` requires `tree`, the baseline import call site needs the fixture tree:

```tsx
// topics/log-panel/variants/baseline.tsx
import { ProcessLogPanel } from '../../../../../optio-ui/src/components/ProcessLogPanel.js';
import { logs, tree } from '../fixtures.js';

export function Baseline() {
  return (
    <div>
      <h3>Baseline — current ProcessLogPanel</h3>
      <p style={{ color: '#999' }}>
        Real panel rendered with fixture tree + interleaved logs from 6 processes.
      </p>
      <ProcessLogPanel logs={logs as any} tree={tree as any} />
    </div>
  );
}
```

The `as any` casts work around the fixture's local `LogEntry`/`ProcessNode` declarations not being identical to the package's; they have the same shape, and the playground is a development-only surface.

- [ ] **Step 5.5: Create the topic entry point.**

Create `packages/optio-dashboard/playground/topics/log-panel/App.tsx`:

```tsx
import { Baseline } from './variants/baseline.js';
import { ColoredTag } from './variants/colored-tag.js';
import { PathText } from './variants/path.js';
import { IndentBar } from './variants/indent-bar.js';

export function App() {
  return (
    <div>
      <h2 style={{ marginTop: 0 }}>ProcessLogPanel — disambiguation variants</h2>
      <p style={{ color: '#999' }}>
        Same fixtures across all panels. 6-process tree, ~40 interleaved log entries.
        Scroll any panel independently.
      </p>
      <div style={{ display: 'grid', gap: 24 }}>
        <Baseline />
        <ColoredTag />
        <PathText />
        <IndentBar />
      </div>
    </div>
  );
}
```

Create `packages/optio-dashboard/playground/topics/log-panel/index.ts`:

```ts
import { App } from './App.js';

export default { name: 'Log panel', slug: 'log-panel', App };
```

- [ ] **Step 5.6: Rewrite `playground/main.tsx` as a registry-driven side-nav.**

Replace `packages/optio-dashboard/playground/main.tsx` with:

```tsx
import React, { useEffect, useState } from 'react';
import ReactDOM from 'react-dom/client';
import { ConfigProvider, theme } from 'antd';
import './i18n.js';
import logPanel from './topics/log-panel/index.js';

interface Topic {
  name: string;
  slug: string;
  App: React.ComponentType;
}

const TOPICS: Topic[] = [logPanel];

function currentSlug(): string {
  const hash = window.location.hash.replace(/^#/, '');
  return TOPICS.some((t) => t.slug === hash) ? hash : TOPICS[0].slug;
}

function App() {
  const [slug, setSlug] = useState<string>(currentSlug());

  useEffect(() => {
    const handler = () => setSlug(currentSlug());
    window.addEventListener('hashchange', handler);
    return () => window.removeEventListener('hashchange', handler);
  }, []);

  const topic = TOPICS.find((t) => t.slug === slug) ?? TOPICS[0];
  const Current = topic.App;

  return (
    <div style={{ display: 'flex', minHeight: '100vh' }}>
      <nav
        style={{
          width: 220,
          borderRight: '1px solid #303030',
          padding: 16,
          flex: '0 0 auto',
        }}
      >
        <strong style={{ display: 'block', marginBottom: 12 }}>Topics</strong>
        {TOPICS.map((t) => (
          <a
            key={t.slug}
            href={`#${t.slug}`}
            style={{
              display: 'block',
              padding: '6px 8px',
              marginBottom: 4,
              borderRadius: 4,
              textDecoration: 'none',
              color: t.slug === slug ? '#fff' : '#69c0ff',
              background: t.slug === slug ? '#177ddc' : 'transparent',
            }}
          >
            {t.name}
          </a>
        ))}
      </nav>
      <main style={{ flex: 1, padding: 16 }}>
        <Current />
      </main>
    </div>
  );
}

ReactDOM.createRoot(document.getElementById('root')!).render(
  <React.StrictMode>
    <ConfigProvider theme={{ algorithm: theme.darkAlgorithm }}>
      <App />
    </ConfigProvider>
  </React.StrictMode>,
);
```

- [ ] **Step 5.7: Write the README.**

Create `packages/optio-dashboard/playground/README.md`:

```markdown
# optio-ui visual playground

Render real `optio-ui` components against fixtures, side-by-side. Useful for
brainstorming visual variants of a component before committing to a design.

## Run

```bash
pnpm --filter optio-dashboard dev:playground
```

Then open http://localhost:5174/.

## Add a new topic

1. Create a directory under `topics/`:

   ```
   playground/topics/<your-topic>/
     index.ts        # default export: { name, slug, App }
     App.tsx         # top-level layout for the topic
     fixtures.ts     # local fixtures
     variants/       # optional: side-by-side variants
   ```

2. `index.ts` exports a default with the contract:

   ```ts
   import { App } from './App.js';
   export default { name: 'Your topic', slug: 'your-topic', App };
   ```

3. Register the topic in `playground/main.tsx`:

   ```ts
   import yourTopic from './topics/your-topic/index.js';
   const TOPICS: Topic[] = [logPanel, yourTopic];
   ```

That's it. The side-nav picks it up automatically.

## Notes

- The playground is not a production artifact. The dashboard's real entry
  point is `src/app/`, which has its own `vite.config.ts` and build.
- Topics own their fixtures. Don't reach across topics; copy if needed.
- The vite config aliases `optio-ui/` to the package source so HMR works on
  optio-ui edits.
```

- [ ] **Step 5.8: Sanity-run the playground.**

```bash
cd packages/optio-dashboard
./node_modules/vite/bin/vite.js --config playground/vite.config.ts &
PG_PID=$!
sleep 2
curl -s -o /dev/null -w "%{http_code}\n" http://localhost:5174/
curl -s -o /dev/null -w "%{http_code}\n" http://localhost:5174/topics/log-panel/App.tsx
curl -s -o /dev/null -w "%{http_code}\n" http://localhost:5174/main.tsx
kill $PG_PID
```

Expected: `200`, `200`, `200`. If any are non-200, open the page in a browser and read the vite error overlay.

- [ ] **Step 5.9: Commit.**

```bash
git add packages/optio-dashboard/playground/
git commit -m "optio-dashboard: playground reorg — topics/ scaffold, registry-driven nav"
```

---

### Task 6: `dev:playground` script + root `AGENTS.md` pointer

Discoverability. Small and last.

**Files:**
- Modify: `packages/optio-dashboard/package.json`
- Modify: `AGENTS.md`
- Modify: `packages/optio-dashboard/AGENTS.md` (verify; only edit if it documents `dev` scripts)

- [ ] **Step 6.1: Read the dashboard `package.json` scripts block.**

```bash
sed -n '/"scripts":/,/}/p' packages/optio-dashboard/package.json
```

- [ ] **Step 6.2: Add the `dev:playground` script.**

In `packages/optio-dashboard/package.json`, edit the `"scripts"` object to add:

```json
"dev:playground": "vite --config playground/vite.config.ts"
```

Place it next to `"dev"`. Example final block:

```json
"scripts": {
  "dev": "vite",
  "dev:playground": "vite --config playground/vite.config.ts",
  "build": "vite build && tsc -p tsconfig.json",
  "build:app": "vite build",
  "build:server": "tsc -p tsconfig.json",
  "start": "node dist/cli.js"
}
```

- [ ] **Step 6.3: Verify the script runs.**

```bash
pnpm --filter optio-dashboard dev:playground &
sleep 2
curl -s -o /dev/null -w "%{http_code}\n" http://localhost:5174/
kill %1
```

Expected: `200`.

- [ ] **Step 6.4: Add the pointer to root `AGENTS.md`.**

Open `AGENTS.md` at the repository root. Find the `## Debug tools` section. After the existing `### MongoDB CLI` block (and any sibling debug-tool blocks), add:

```markdown
### UI brainstorming

Render real `optio-ui` components against fixtures (depth/color treatments,
component layouts, etc.):

```bash
pnpm --filter optio-dashboard dev:playground
```

Then open http://localhost:5174/. Add new topics under
`packages/optio-dashboard/playground/topics/<name>/`; see the playground
`README.md` for the contract. Use this when comparing visual variants of an
`optio-ui` component before committing to a design.
```

- [ ] **Step 6.5: Check `packages/optio-dashboard/AGENTS.md`.**

```bash
grep -n "scripts\|dev:" packages/optio-dashboard/AGENTS.md
```

If the file enumerates the package's `package.json` scripts, add `dev:playground` there. If it doesn't, leave it alone.

- [ ] **Step 6.6: Commit.**

```bash
git add packages/optio-dashboard/package.json AGENTS.md
# Only if step 6.5 produced an edit:
# git add packages/optio-dashboard/AGENTS.md
git commit -m "optio-dashboard: dev:playground script + AGENTS.md pointer"
```

---

## Final verification

After all six tasks land:

- [ ] **F1: Full optio-ui suite.**

```bash
pnpm --filter optio-ui test
```

Expected: all green, including the two new test files.

- [ ] **F2: Type-check optio-ui.**

```bash
cd packages/optio-ui && ./node_modules/.bin/tsc --noEmit
```

Expected: no errors.

- [ ] **F3: Type-check optio-dashboard.**

```bash
cd packages/optio-dashboard && ./node_modules/.bin/tsc --noEmit
```

Expected: no errors. (The playground is not part of the production tsconfig project, but the dashboard's own sources should still compile cleanly.)

- [ ] **F4: Playground visual check.**

```bash
pnpm --filter optio-dashboard dev:playground
```

Open http://localhost:5174/. The "Log panel" topic should show four panels:
- Baseline (the now-fixed `ProcessLogPanel` with `tree` + `processLabel`)
- Colored tag
- Path text
- Indent + colored bar

Confirm the **Baseline** panel now renders the indent + colored bar treatment too (since the real panel changed). The other three variants remain as standalone illustrations.

- [ ] **F5: Inspect the branch summary.**

```bash
git log --oneline main..HEAD
```

Expected five commits in this order:

1. `optio-ui: ProcessLogPanel — read processLabel, drop local LogEntry duplicate`
2. `optio-ui: log-visuals module — DFS-order palette + depth lookup`
3. `optio-ui: ProcessLogPanel — indent + colored bar + transition label, tree-aware`
4. `optio-dashboard: playground reorg — topics/ scaffold, registry-driven nav`
5. `optio-dashboard: dev:playground script + AGENTS.md pointer`

(Tasks 3 + 4 share one commit, so the count is five.)

- [ ] **F6: Hand back to the user with the branch name and commit list.**

Surface the branch (`feat/process-log-panel-disambiguation`) and the commit list. Do not merge or push without explicit instruction — per the user's standing preference, integration is its own decision via the `finishing-a-development-branch` flow.
