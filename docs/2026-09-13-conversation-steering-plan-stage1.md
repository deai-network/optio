# Conversation Steering, Stage 1 (Claude Code) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Stage 1 of `docs/2026-09-13-conversation-steering-design.md`: a busy Claude Code conversation offers **Send when ready** and **Interrupt and send**, queued messages show as queued bubbles until Claude takes them, and interrupts render as a cut-off answer with a jagged edge plus one "⏹ Interrupted by you" row instead of error noise. Live and replay render the same.

**Architecture:** A new engine-neutral module `optio_agents.steering` (capability `busy_send`, per agent + model declaration, `Steering` with `send_when_ready` / `interrupt_and_send` / `interrupt`, optio's own queue, the 15 s bounded wait, and the synthetic events `x-optio-queued` / `x-optio-taken` / `x-optio-interrupt`) wraps a wrapper's existing `Conversation`. Claude Code declares `joins-next-step`; its conversation listener routes `POST /send` and a new `POST /steer` through it, and the synthetic events enter the conversation's own event stream, so the listener buffers and persists them like native events. In `optio-conversation-ui` the claudecode reducer pins queued bubbles at the bottom, moves them to the take point, and turns an operator interrupt into the new rendering; `ConversationView` gets the queued bubble, the jagged edge, and a busy input bar built on vultus `CombinedActionButton` with the new `keepOriginalDefault` prop.

**Tech Stack:** Python 3.13 (asyncio, aiohttp, pytest + pytest-asyncio), TypeScript + React 19 + antd 5 (vitest, jsdom, @testing-library/react), vultus-antd (unitas repo).

## Global Constraints

- **Hosts.** Every repo lives on host `excavator`. You work from `superego`, which has no checkout. Run every repo command through `ssh excavator '…'`. Paths:
  - optio worktree: `excavator:~/deai/optio-steering`, branch `conversation-steering`.
  - unitas: `excavator:~/deai/unitas`. Task 1 creates branch `keep-original-default` there.
  - **Never modify, check out, build or install anything in `excavator:~/deai/optio`.** It is the main checkout that the live dev stack runs from. The only exception is Task 0's one-line append to the git exclude file that the two checkouts share.
- **Editing remote files.** Mirror each file under `/tmp/steer-edit/` on superego: `/tmp/steer-edit/optio/<repo path>` for optio, `/tmp/steer-edit/unitas/<repo path>` for unitas.
  - Existing file: `mkdir -p "$(dirname /tmp/steer-edit/optio/P)" && scp excavator:deai/optio-steering/P /tmp/steer-edit/optio/P`, edit it with your normal tools, then `scp /tmp/steer-edit/optio/P excavator:deai/optio-steering/P`.
  - New file: write it locally, then scp it.
  - Always fetch a fresh copy before editing.
  - Code steps below give either a whole file or an exact `old` → `new` replacement. Each `old` occurs exactly once in the file.
- **No package installs.** Never run `pnpm`, `npm install` or `pip install`, in the worktree or in unitas. A pnpm install relinks the `node_modules` that optio, unitas and excavator share.
- **Python tests** (run one package per command: each package's `pyproject.toml` sets `asyncio_mode = "auto"`, and the worktree root has no pytest config):
  `ssh excavator 'cd ~/deai/optio-steering && PYTHONPATH=packages/optio-agents/src:packages/optio-claudecode/src:packages/optio-host/src:packages/optio-core/src ~/deai/optio/.venv/bin/pytest -q -p no:cacheprovider <paths>'`
  Below this is written `PYTEST <paths>`.
- **UI tests:** `ssh excavator 'cd ~/deai/optio-steering/packages/optio-conversation-ui && ./node_modules/.bin/vitest run --maxWorkers=3 <files>'`, written `UITEST <files>`. Typecheck: `ssh excavator 'cd ~/deai/optio-steering/packages/optio-conversation-ui && ./node_modules/.bin/tsc -p .'`, written `UITSC`. This is what `pnpm build` (`tsc`) runs; tsconfig has `noEmit`.
- **unitas tests:** `ssh excavator 'cd ~/deai/unitas/packages/vultus-antd && node_modules/.bin/vitest run --maxWorkers=2 --testTimeout=30000 <files>'`, written `VTEST <files>`. Typecheck: `ssh excavator 'cd ~/deai/unitas/packages/vultus-antd && node_modules/.bin/tsc -p tsconfig.json'`, written `VTSC`.
- **Memory.** The host has 3.8 GB RAM and other agents run suites there. Keep the worker caps above and never run two suites at once.
- **Commits (optio):**
  - No `Co-Authored-By` and no other self-credit line.
  - Stage files by explicit path, never `git add -A`.
  - Never amend and never push. Pushes need the owner's go.
- **Commits (unitas):** the Co-Authored-By trailer is allowed. Don't push, don't publish, and don't bump the version: vultus-antd stays at the unpublished `0.1.1`.
- **Tests never depend on wall-clock time** (optio AGENTS.md). No sleep-then-assert: wait on events or conditions with a 60 s hang ceiling. The only timeout a test sets is the product's own `turn_end_timeout_s`, set to `0.0` to take the "no turn end" path deterministically.
- **TDD.** Write the failing test, watch it fail, implement, watch it pass, commit.
- **AGENTS.md.** When a package's public API changes, update its `AGENTS.md` (and the root `AGENTS.md` where it summarises that package) in the same commit. `optio-conversation-ui` has no AGENTS.md; its `README.md` is updated instead.
- **Verbatim values** (from the spec):
  - busy_send values: `joins-next-step`, `queues-to-end`, `cuts-in`, `rejected`, `unsafe`. A model without its own value inherits the agent's; an agent without a declaration gets `unsafe`.
  - Synthetic events: `{"type":"x-optio-queued","id":…,"text":…}`, `{"type":"x-optio-taken","ids":[…]}`, `{"type":"x-optio-interrupt","by":"user"}`.
  - Messages optio holds are sent as one prompt, in the order written, joined by a blank line (`"\n\n"`).
  - After an interrupt, wait at most 15 s for the turn end, then send anyway and log it.
  - `POST /send` returns `{ok, id, queued}`. `POST /steer` returns `{ok, id}`; empty text means "deliver what is queued" (Send now).
  - Captions: `Send`, `Send when ready`, `Interrupt and send`, `Interrupt`, `Queued — the agent reads it when ready`, `Send now`, `⏹ Interrupted by you`.
- **Coordination.** The controller holds this work's crew line in `~/commissura/crew.md`. Don't edit crew.md.

## File structure

| File | Responsibility |
|---|---|
| unitas `packages/vultus-antd/src/CombinedActionButton.tsx`, `ActionButton.tsx` | `keepOriginalDefault` prop |
| `packages/optio-agents/src/optio_agents/steering.py` (new) | capability, declaration, `Steering` scaffold |
| `packages/optio-claudecode/src/optio_claudecode/steering.py` (new) | Claude Code's `BUSY_SEND`, turn-end predicate, `make_steering` |
| `packages/optio-claudecode/src/optio_claudecode/conversation.py` | `emit_event` hook; `is_pending` reset on idle |
| `packages/optio-claudecode/src/optio_claudecode/conversation_listener.py` | `/send` via `send_when_ready`, new `/steer`, `/interrupt` via steering |
| `packages/optio-conversation-ui/src/chat.ts` | shared model fields + queue helpers |
| `packages/optio-conversation-ui/src/claudecode/events.ts` | placement, queued/taken, interrupt handling |
| `packages/optio-conversation-ui/src/ConversationView.tsx` | queued bubble, jagged edge, muted row, busy bar |
| `packages/optio-conversation-ui/src/claudecode/ClaudeCodeView.tsx` | `onSteer` → `/steer`, `/send` id → local echo |
| `packages/optio-conversation-ui/src/__tests__/fixtures/claudecode-{steer-streaming,interrupt-streaming,interrupt-tool,steer-then-interrupt}.jsonl` (new) | trimmed real recordings |

Task order: 0 → 1 → 2 → 3 → 4 → 5 → 6 → 7 → 8. Task 7 needs Task 1's prop. Tasks 2–4 (Python) and 5–6 (reducer) do not depend on each other.

---

### Task 0: Worktree setup and baselines

**Files:** none committed. Creates two symlinks and adds two exclude patterns.

**Interfaces:**
- Consumes: nothing.
- Produces: a runnable `UITEST` / `UITSC` in the worktree, plus baseline files on superego: `/tmp/steer-baseline-{py-agents,py-claudecode,ui,tsc,vultus,vtsc}.txt`.

- [ ] **Step 1: Check the worktree**

Run: `ssh excavator 'cd ~/deai/optio-steering && git branch --show-current && git status --short && git log --oneline -3'`
Expected: branch `conversation-steering`, clean tree, HEAD is the stage-1 plan commit on top of `49ed7d55 docs: design for conversation steering …`.

- [ ] **Step 2: Link node_modules from the main checkout (never install)**

The worktree has no node_modules. vitest only needs the root and the conversation-ui package directories. The package's own links (`optio-ui -> ../../optio-ui`, `vultus-antd -> ../../../../unitas/packages/vultus-antd`, the pnpm store) are relative, so they resolve from the main checkout's real directory. That works because this plan never changes optio-ui or optio-agents-ui.

Run:
```bash
ssh excavator 'cd ~/deai/optio-steering && ln -s ~/deai/optio/node_modules node_modules && ln -s ~/deai/optio/packages/optio-conversation-ui/node_modules packages/optio-conversation-ui/node_modules && ls -la node_modules packages/optio-conversation-ui/node_modules'
```
Expected: two symlinks pointing into `~/deai/optio`.

- [ ] **Step 3: Keep the symlinks out of git**

`.gitignore` has `node_modules/`, and a trailing-slash pattern matches only directories, not symlinks. The worktree's `info/exclude` is the file both checkouts share (`~/deai/optio/.git/info/exclude`). In the main checkout these paths are real directories that `.gitignore` already covers, so the two lines change nothing there.

Run:
```bash
ssh excavator 'cd ~/deai/optio-steering && printf "%s\n" "/node_modules" "/packages/optio-conversation-ui/node_modules" >> "$(git rev-parse --git-path info/exclude)" && git status --short'
```
Expected: empty output (no `node_modules` entries).

- [ ] **Step 4: Confirm Python imports the worktree sources**

Run:
```bash
ssh excavator 'cd ~/deai/optio-steering && PYTHONPATH=packages/optio-agents/src:packages/optio-claudecode/src:packages/optio-host/src:packages/optio-core/src ~/deai/optio/.venv/bin/python -c "import optio_agents, optio_claudecode; print(optio_agents.__file__, optio_claudecode.__file__)"'
```
Expected: both paths start with `/home/csillag/deai/optio-steering/packages/`.

- [ ] **Step 5: Python baselines**

Run each command separately, one after the other:
```bash
ssh excavator 'cd ~/deai/optio-steering && PYTHONPATH=packages/optio-agents/src:packages/optio-claudecode/src:packages/optio-host/src:packages/optio-core/src ~/deai/optio/.venv/bin/pytest -q -rf -p no:cacheprovider packages/optio-agents/tests 2>&1 | tail -25' | tee /tmp/steer-baseline-py-agents.txt
ssh excavator 'cd ~/deai/optio-steering && PYTHONPATH=packages/optio-agents/src:packages/optio-claudecode/src:packages/optio-host/src:packages/optio-core/src ~/deai/optio/.venv/bin/pytest -q -rf -p no:cacheprovider packages/optio-claudecode/tests 2>&1 | tail -25' | tee /tmp/steer-baseline-py-claudecode.txt
```
Record the pass/fail counts and the failing test ids. Some integration tests need MongoDB. Failures here are the baseline, not something to fix.

- [ ] **Step 6: UI and typecheck baselines**

```bash
ssh excavator 'cd ~/deai/optio-steering/packages/optio-conversation-ui && ./node_modules/.bin/vitest run --maxWorkers=3 2>&1 | tail -30' | tee /tmp/steer-baseline-ui.txt
ssh excavator 'cd ~/deai/optio-steering/packages/optio-conversation-ui && ./node_modules/.bin/tsc -p . 2>&1 | tail -30' | tee /tmp/steer-baseline-tsc.txt
```

- [ ] **Step 7: unitas baselines**

```bash
ssh excavator 'cd ~/deai/unitas && git status --short && git branch --show-current'
ssh excavator 'cd ~/deai/unitas/packages/vultus-antd && node_modules/.bin/vitest run --maxWorkers=2 --testTimeout=30000 2>&1 | tail -15' | tee /tmp/steer-baseline-vultus.txt
ssh excavator 'cd ~/deai/unitas/packages/vultus-antd && node_modules/.bin/tsc -p tsconfig.json 2>&1 | tail -15' | tee /tmp/steer-baseline-vtsc.txt
```
Expected: unitas is on `main` with a clean tree. If it is not, stop and report.

No commit in this task.

---
### Task 1: vultus-antd `keepOriginalDefault` (unitas)

**Files:**
- Modify: `~/deai/unitas/packages/vultus-antd/src/CombinedActionButton.tsx` (whole file below)
- Modify: `~/deai/unitas/packages/vultus-antd/src/ActionButton.tsx`
- Modify: `~/deai/unitas/packages/vultus-antd/src/__tests__/CombinedActionButton.test.tsx`
- Modify: `~/deai/unitas/packages/vultus-antd/README.md`

**Interfaces:**
- Consumes: nothing.
- Produces: `CombinedActionButton` props `{ actions: ActionStatus[]; size?: 'small' | 'middle' | 'large'; keepOriginalDefault?: boolean }` and `ActionButton` props `{ action: ActionStatus | ActionStatus[]; size?; block?; keepOriginalDefault?: boolean }`. Off (default) keeps today's behaviour: a menu pick becomes the main action and stays there. On: after the picked action fires, or its confirmation is dismissed, the main half returns to the first enabled action. Task 7 uses `<CombinedActionButton size="small" keepOriginalDefault actions={…} />` from `'vultus-antd'`.

> Warning: optio's and excavator's `node_modules` link `vultus-antd` to this working tree, and the live excavator dev stack hot-reloads from it. Write the test first (test files are never imported by the app), then apply the source change in one go. Never leave `CombinedActionButton.tsx` half-edited. When the task is done, leave unitas checked out on `keep-original-default`: the optio UI tasks need the prop. Merging into unitas `main` is the controller's call.

- [ ] **Step 1: Branch**

Run: `ssh excavator 'cd ~/deai/unitas && git status --short && git checkout -b keep-original-default && git branch --show-current'`
Expected: clean tree; prints `keep-original-default`.

- [ ] **Step 2: Write the failing tests**

In `src/__tests__/CombinedActionButton.test.tsx`, add this import after `import { ActionButton } from '../ActionButton.js';`:
```tsx
import { CombinedActionButton } from '../CombinedActionButton.js';
```
Append at the end of the file:
```tsx
describe('ActionButton — combined mode, keepOriginalDefault', () => {
  afterEach(() => { Modal.destroyAll(); });

  it('a menu pick fires the action and the main half returns to the first enabled action', async () => {
    const a = makeStatus({ id: 'a', label: 'A' });
    const b = makeStatus({ id: 'b', label: 'B' });
    const { container } = render(wrap(<ActionButton action={[a, b]} keepOriginalDefault />));
    fireEvent.click(chevronBtn(container));
    fireEvent.click(await screen.findByRole('menuitem', { name: 'B' }));
    expect(b.fire).toHaveBeenCalledTimes(1);
    expect(a.fire).not.toHaveBeenCalled();
    await waitFor(() => expect(mainBtn(container).textContent).toContain('A'));
    // The main half fires A again, not the earlier pick.
    fireEvent.click(mainBtn(container));
    expect(a.fire).toHaveBeenCalledTimes(1);
    expect(b.fire).toHaveBeenCalledTimes(1);
  });

  it('the original default is the first ENABLED action', async () => {
    const a = makeStatus({ id: 'a', label: 'A', disabled: true });
    const b = makeStatus({ id: 'b', label: 'B' });
    const c = makeStatus({ id: 'c', label: 'C' });
    const { container } = render(wrap(<ActionButton action={[a, b, c]} keepOriginalDefault />));
    expect(mainBtn(container).textContent).toContain('B');
    fireEvent.click(chevronBtn(container));
    fireEvent.click(await screen.findByRole('menuitem', { name: 'C' }));
    expect(c.fire).toHaveBeenCalledTimes(1);
    await waitFor(() => expect(mainBtn(container).textContent).toContain('B'));
  });

  it('a popconfirm action picked from the menu fires on OK, then the main half returns', async () => {
    const a = makeStatus({ id: 'a', label: 'A' });
    const b = makeStatus({
      id: 'b', label: 'Delete', variant: 'danger',
      confirmation: { kind: 'popconfirm', question: 'Sure?' },
    });
    const { container } = render(wrap(<ActionButton action={[a, b]} keepOriginalDefault />));
    fireEvent.click(chevronBtn(container));
    fireEvent.click(await screen.findByRole('menuitem', { name: 'Delete' }));
    expect(await screen.findByText('Sure?')).toBeInTheDocument();
    expect(b.fire).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole('button', { name: /^OK$/i }));
    await waitFor(() => expect(b.fire).toHaveBeenCalledTimes(1));
    await waitFor(() => expect(mainBtn(container).textContent).toContain('A'));
  });

  it('a typing-confirm action picked from the menu returns to the default when cancelled', async () => {
    const a = makeStatus({ id: 'a', label: 'A' });
    const b = makeStatus({
      id: 'b', label: 'Delete', variant: 'danger',
      confirmation: { kind: 'typing', title: 'Delete entity', entityName: 'orders', description: 'Type the name to confirm.' },
    });
    const { container } = render(wrap(<ActionButton action={[a, b]} keepOriginalDefault />));
    fireEvent.click(chevronBtn(container));
    fireEvent.click(await screen.findByRole('menuitem', { name: 'Delete' }));
    expect(await screen.findByText('Delete entity')).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: /cancel/i }));
    await waitFor(() => expect(mainBtn(container).textContent).toContain('A'));
    expect(b.fire).not.toHaveBeenCalled();
  });

  it('CombinedActionButton accepts the prop directly', async () => {
    const a = makeStatus({ id: 'a', label: 'A' });
    const b = makeStatus({ id: 'b', label: 'B' });
    const { container } = render(wrap(<CombinedActionButton actions={[a, b]} keepOriginalDefault />));
    fireEvent.click(chevronBtn(container));
    fireEvent.click(await screen.findByRole('menuitem', { name: 'B' }));
    expect(b.fire).toHaveBeenCalledTimes(1);
    await waitFor(() => expect(mainBtn(container).textContent).toContain('A'));
  });
});
```
The existing test `'menu-item click fires that action and moves selection to it'` already covers the default (sticky) behaviour. Leave it unchanged.

- [ ] **Step 3: Run the tests to verify they fail**

Run: `VTEST src/__tests__/CombinedActionButton.test.tsx`
Expected: the five new tests FAIL. The main half still shows the picked action, and TypeScript-unaware vitest lets the unknown prop through. All existing tests PASS.

- [ ] **Step 4: Implement**

Replace the whole of `src/CombinedActionButton.tsx` with:
```tsx
import { useState } from 'react';
import { Button, Dropdown, Modal, Popconfirm, Tooltip, theme } from 'antd';
import type { ActionStatus } from 'vultus-core';
import { ConfirmTypingModal } from './ConfirmTypingModal.js';
import { ActionButton } from './ActionButton.js';
import { ReasonMarkdown } from './ReasonMarkdown.js';

interface Props {
  actions: ActionStatus[];
  size?: 'small' | 'middle' | 'large';
  // Off (default): a menu pick becomes the main action and stays there.
  // On: the main half returns to the first enabled action (the original
  // default) once the picked action fires or its confirmation is dismissed.
  keepOriginalDefault?: boolean;
}

export function CombinedActionButton({ actions, size, keepOriginalDefault = false }: Props) {
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [selectionSource, setSelectionSource] = useState<'auto' | 'manual'>('auto');
  const [typingOpen, setTypingOpen] = useState(false);
  const [popconfirmOpen, setPopconfirmOpen] = useState(false);
  const { token } = theme.useToken();

  const visible = actions.filter((a) => !a.invisible);
  if (visible.length === 0) return null;
  if (visible.length === 1) return <ActionButton action={visible[0]} size={size} />;

  // Manual picks stay put even when the picked action becomes disabled
  // (operator's choice is respected — button disables but selection holds).
  // Auto picks re-evaluate to first-enabled each render, so the default
  // tracks state changes without any effect.
  const selectedIndex = (() => {
    if (selectionSource === 'manual' && selectedId !== null) {
      const idx = visible.findIndex((a) => a.id === selectedId);
      if (idx !== -1) return idx;
    }
    const firstEnabled = visible.findIndex((a) => !a.disabled);
    return firstEnabled !== -1 ? firstEnabled : 0;
  })();

  const active = visible[selectedIndex];

  // keepOriginalDefault: drop the pick, so the main half re-evaluates to the
  // first enabled action (the auto rule above). A no-op otherwise.
  const settle = () => {
    if (!keepOriginalDefault) return;
    setSelectedId(null);
    setSelectionSource('auto');
  };

  // Fire an explicit action (not the resolved `active`). Menu rows cannot
  // "select then read active" in one handler — `active` only recomputes on
  // the next render — so the action is passed in explicitly. Selecting the
  // action also locks it as a manual pick (intent = the click, regardless of
  // whether the operator follows through with confirmation), mirroring the
  // old lockActive behavior. Used by both the main button and the menu rows.
  // With keepOriginalDefault the pick lasts only until the action fires or
  // its confirmation closes.
  const fire = (action: ActionStatus) => {
    if (action.disabled || action.pending) return;
    setSelectedId(action.id);
    setSelectionSource('manual');
    if (!action.confirmation) {
      action.fire();
      settle();
      return;
    }
    if (action.confirmation.kind === 'typing') {
      setTypingOpen(true);
      return;
    }
    if (action.confirmation.kind === 'cascade-modal') {
      const conf = action.confirmation;
      Modal.confirm({
        title: conf.title,
        content: conf.content,
        okText: action.label,
        okButtonProps: { danger: action.variant === 'danger' },
        onOk: () => { action.fire(); settle(); },
        onCancel: settle,
      });
      return;
    }
    // popconfirm: open the controlled bubble on the main half.
    setPopconfirmOpen(true);
  };

  const menuItems = visible.map((a, i) => {
    // primary → label tinted with the same token antd's primary button fills
    // with (colorPrimary), bold; deliberately no solid fill so it does not
    // collide with the selectedKeys highlight on the active row. danger uses
    // antd's native menu-item danger styling (set below via `danger`).
    const labelText =
      a.variant === 'primary'
        ? <span style={{ color: token.colorPrimary, fontWeight: 600 }}>{a.label}</span>
        : <span>{a.label}</span>;
    return {
      key: String(i),
      icon: a.icon,
      danger: a.variant === 'danger',
      label: a.reason
        ? <Tooltip title={<ReasonMarkdown>{a.reason}</ReasonMarkdown>}>{labelText}</Tooltip>
        : labelText,
      disabled: a.disabled,
      onClick: () => fire(a),
    };
  });

  // Compose the main half explicitly via buttonsRender so the chevron stays
  // interactive even when the active half is disabled (design §3.2 requires
  // the menu to open in the all-disabled case for per-item reason tooltips),
  // the icon renders via Button's `icon` prop, and Popconfirm wraps only the
  // main half (chevron click cannot trigger it).
  const renderMainButton = () => {
    const rawButton = (
      <Button
        icon={active.icon}
        type={active.variant === 'primary' ? 'primary' : 'default'}
        danger={active.variant === 'danger'}
        size={size}
        loading={active.pending}
        disabled={active.disabled || active.pending}
        // Single path for every confirmation kind. For popconfirm, fire()
        // opens the controlled bubble (the Popconfirm no longer auto-opens
        // on child click now that it is controlled).
        onClick={() => fire(active)}
        data-action-id={active.id}
      >
        {active.label}
      </Button>
    );

    // Mirror single-action: disabled buttons have pointer-events:none which
    // swallows tooltip hover; wrap in Tooltip><span> to restore it.
    const withTooltip = active.reason
      ? (
          <Tooltip title={<ReasonMarkdown>{active.reason}</ReasonMarkdown>}>
            <span style={{ display: 'inline-block', cursor: active.disabled ? 'not-allowed' : undefined }}>
              {rawButton}
            </span>
          </Tooltip>
        )
      : rawButton;

    if (active.confirmation?.kind === 'popconfirm') {
      return (
        <Popconfirm
          title={<div style={{ maxWidth: 280, whiteSpace: 'normal' }}>{active.confirmation.question}</div>}
          // Controlled: fire() drives `open` (from the main button OR a menu
          // row). We only handle the close transition here — opening is always
          // explicit via fire(). onConfirm closes via the same false transition.
          open={popconfirmOpen}
          onOpenChange={(next) => { if (!next) { setPopconfirmOpen(false); settle(); } }}
          onConfirm={() => { active.fire(); settle(); }}
          okButtonProps={{ danger: active.variant === 'danger' }}
          disabled={active.disabled}
        >
          {withTooltip}
        </Popconfirm>
      );
    }

    return withTooltip;
  };

  const dropdownButton = (
    <Dropdown.Button
      // Click trigger (default is hover) — touch devices can't hover, and
      // chevron is meant to be clicked anyway.
      trigger={['click']}
      // Size the whole compound (esp. the chevron half) — without this the
      // dropdown trigger renders at the default size even when the main button
      // is `small`. The main half re-applies size via renderMainButton.
      size={size}
      menu={{ items: menuItems, selectedKeys: [String(selectedIndex)] }}
      type={active.variant === 'primary' ? 'primary' : 'default'}
      danger={active.variant === 'danger'}
      buttonsRender={([_left, right]) => [renderMainButton(), right]}
      // Override antd's hardcoded `block: true` on the inner Space.Compact
      // wrapper. Block-mode adds `display:flex; width:100%` which makes the
      // button greedy under a flex parent (eg. the entity-detail heading's
      // `space-between` layout) and crushes the title to its left.
      // restProps in dropdown-button.js are spread AFTER its hardcoded block
      // pair so this `block={false}` actually wins.
      // @ts-expect-error — antd's DropdownButtonProps does not declare block,
      // but Space.Compact does and restProps is forwarded to it verbatim.
      block={false}
    />
  );

  if (active.confirmation?.kind === 'typing') {
    const conf = active.confirmation;
    return (
      <>
        {dropdownButton}
        <ConfirmTypingModal
          open={typingOpen}
          title={conf.title}
          entityName={conf.entityName}
          description={conf.description}
          onConfirm={() => { setTypingOpen(false); active.fire(); settle(); }}
          onCancel={() => { setTypingOpen(false); settle(); }}
        />
      </>
    );
  }

  return dropdownButton;
}
```

In `src/ActionButton.tsx`, replace:
```tsx
interface Props {
  action: ActionStatus | ActionStatus[];
  size?: 'small' | 'middle' | 'large';
  block?: boolean;
}

export function ActionButton({ action, size, block }: Props) {
```
with:
```tsx
interface Props {
  action: ActionStatus | ActionStatus[];
  size?: 'small' | 'middle' | 'large';
  block?: boolean;
  // Array form only: forwarded to CombinedActionButton (see there).
  keepOriginalDefault?: boolean;
}

export function ActionButton({ action, size, block, keepOriginalDefault }: Props) {
```
and replace:
```tsx
    return <CombinedActionButton actions={action} size={size} />;
```
with:
```tsx
    return <CombinedActionButton actions={action} size={size} keepOriginalDefault={keepOriginalDefault} />;
```

In `README.md`, replace:
```
- `./markdown` — `Markdown` and the `MarkdownProps` type, standalone.
```
with:
```
- `./markdown` — `Markdown` and the `MarkdownProps` type, standalone.

## CombinedActionButton selection

By default a menu pick becomes the main action and stays there. Pass
`keepOriginalDefault` (to `CombinedActionButton`, or to `ActionButton` with an
array) to return the main half to the first enabled action once the picked
action fires or its confirmation is dismissed.
```

- [ ] **Step 5: Run the tests and typecheck**

Run: `VTEST src/__tests__/CombinedActionButton.test.tsx src/__tests__/ActionButton.test.tsx`
Expected: PASS, all tests.
Run: `VTSC`
Expected: same output as `/tmp/steer-baseline-vtsc.txt` (no new errors).
Run the whole package: `VTEST`
Expected: pass/fail counts equal to `/tmp/steer-baseline-vultus.txt` plus the 5 new passes.

- [ ] **Step 6: Commit (unitas)**

```bash
ssh excavator 'cd ~/deai/unitas && git add packages/vultus-antd/src/CombinedActionButton.tsx packages/vultus-antd/src/ActionButton.tsx packages/vultus-antd/src/__tests__/CombinedActionButton.test.tsx packages/vultus-antd/README.md && git commit -m "feat(vultus-antd): keepOriginalDefault on CombinedActionButton

Off by default (a menu pick sticks, as before). On, the main half returns
to the first enabled action once a picked action fires or its confirmation
is dismissed. Forwarded by ActionButton for the array form. Part of the
unpublished 0.1.1." && git log --oneline -1'
```

---
### Task 2: optio-agents steering scaffold

**Files:**
- Create: `packages/optio-agents/src/optio_agents/steering.py`
- Create: `packages/optio-agents/tests/test_steering.py`
- Modify: `packages/optio-agents/src/optio_agents/__init__.py`
- Modify: `packages/optio-agents/AGENTS.md`, `AGENTS.md` (root)

**Interfaces:**
- Consumes: `optio_agents.conversation.ConversationClosed`. From the wrapped conversation it needs `async send(text)`, `async interrupt()`, `is_pending() -> bool`, `on_event(handler) -> unsubscribe`, and optionally a `closed` attribute.
- Produces (`optio_agents.steering`, all re-exported from `optio_agents`):
  - `BusySend = Literal["joins-next-step", "queues-to-end", "cuts-in", "rejected", "unsafe"]`
  - `BUSY_SEND_VALUES: tuple[str, ...]`
  - `@dataclass(frozen=True) BusySendDeclaration(agent: BusySend, models: Mapping[str, BusySend] = {})` with `.for_model(model: str | None) -> BusySend`. A trailing `[variant]` suffix on the model is ignored.
  - `resolve_busy_send(declaration: BusySendDeclaration | None, model: str | None) -> BusySend` (no declaration → `"unsafe"`)
  - `@dataclass(frozen=True) SendOutcome(id: str, queued: bool)`
  - `class Steering(conversation, *, busy_send: Callable[[], BusySend], emit: Callable[[dict], None], is_turn_end: Callable[[dict], bool], turn_end_timeout_s: float = 15.0, new_id: Callable[[], str] | None = None)` with:
    - `async send_when_ready(text: str) -> SendOutcome`
    - `async interrupt_and_send(text: str) -> str | None`
    - `async interrupt() -> None`
    - `async settle() -> None` (waits for a turn-end flush in progress)
    - `held_ids -> list[str]`
    - `close() -> None` (unsubscribes)
  - Constants `QUEUED_EVENT = "x-optio-queued"`, `TAKEN_EVENT = "x-optio-taken"`, `INTERRUPT_EVENT = "x-optio-interrupt"`, `PROMPT_SEPARATOR = "\n\n"`, `TURN_END_TIMEOUT_S = 15.0`, `NATIVE_QUEUE = frozenset({"joins-next-step", "queues-to-end"})`.
  - Stages 2 and 3 build every other wrapper's steering from these pieces. Only the declaration, `emit` and `is_turn_end` differ per wrapper.

- [ ] **Step 1: Write the failing tests**

Create `packages/optio-agents/tests/test_steering.py`:
```python
"""Steering scaffold unit tests against a fake conversation (no sleeps)."""

import logging

import pytest

from optio_agents.conversation import ConversationClosed
from optio_agents.steering import (
    BUSY_SEND_VALUES,
    BusySendDeclaration,
    SendOutcome,
    Steering,
    resolve_busy_send,
)

QUEUED = "x-optio-queued"
TAKEN = "x-optio-taken"
INTERRUPT = {"type": "x-optio-interrupt", "by": "user"}


class FakeConversation:
    """Records sends, interrupts and synthetic events in one ordered log.

    The test sets ``pending`` (busy) itself. ``interrupt()`` ends the turn
    at once (fires the turn-end event) unless ``turn_end_on_interrupt`` is
    False."""

    def __init__(self, *, turn_end_on_interrupt: bool = True):
        self.handlers = []
        self.sent: list[str] = []
        self.interrupts = 0
        self.pending = False
        self.closed = False
        self.turn_end_on_interrupt = turn_end_on_interrupt
        self.log: list[tuple[str, object]] = []

    def on_event(self, handler):
        self.handlers.append(handler)
        return lambda: self.handlers.remove(handler)

    def fire(self, event: dict) -> None:
        for h in list(self.handlers):
            h(event)

    def emit(self, event: dict) -> None:  # the wrapper's synthetic-event hook
        self.log.append(("event", event))
        self.fire(event)

    def is_pending(self) -> bool:
        return self.pending

    async def send(self, text: str) -> None:
        if self.closed:
            raise ConversationClosed("closed")
        self.log.append(("send", text))
        self.sent.append(text)
        self.pending = True

    async def interrupt(self) -> None:
        if self.closed:
            raise ConversationClosed("closed")
        self.log.append(("interrupt", None))
        self.interrupts += 1
        if self.turn_end_on_interrupt and self.pending:
            self.pending = False
            self.fire({"type": "turn-end"})


def make(conv, busy_send="joins-next-step", **kw):
    ids = iter(f"id{n}" for n in range(1, 100))
    return Steering(
        conv,
        busy_send=lambda: busy_send,
        emit=conv.emit,
        is_turn_end=lambda e: e.get("type") == "turn-end",
        new_id=lambda: next(ids),
        **kw,
    )


def events(conv):
    return [e for kind, e in conv.log if kind == "event"]


# -- capability declaration --------------------------------------------------

def test_model_inherits_the_agent_value_and_an_undeclared_agent_is_unsafe():
    decl = BusySendDeclaration(agent="joins-next-step", models={"model-x": "queues-to-end"})
    assert decl.for_model(None) == "joins-next-step"
    assert decl.for_model("claude-opus-5") == "joins-next-step"
    assert decl.for_model("model-x") == "queues-to-end"
    assert decl.for_model("model-x[1m]") == "queues-to-end"
    assert resolve_busy_send(decl, "model-x") == "queues-to-end"
    assert resolve_busy_send(None, "anything") == "unsafe"


def test_declaration_rejects_unknown_values():
    with pytest.raises(ValueError):
        BusySendDeclaration(agent="sometimes")
    with pytest.raises(ValueError):
        BusySendDeclaration(agent="unsafe", models={"m": "bogus"})
    assert BUSY_SEND_VALUES == ("joins-next-step", "queues-to-end", "cuts-in", "rejected", "unsafe")


# -- send when ready -----------------------------------------------------------

@pytest.mark.parametrize("cap", BUSY_SEND_VALUES)
async def test_idle_send_is_a_plain_send_without_events(cap):
    conv = FakeConversation()
    s = make(conv, cap)
    assert await s.send_when_ready("hi") == SendOutcome(id="id1", queued=False)
    assert conv.log == [("send", "hi")]


@pytest.mark.parametrize("cap", ["joins-next-step", "queues-to-end"])
async def test_busy_send_to_a_native_queue_goes_straight_through_as_queued(cap):
    conv = FakeConversation()
    conv.pending = True
    s = make(conv, cap)
    assert await s.send_when_ready("steer") == SendOutcome(id="id1", queued=True)
    # The queued event precedes the send, so it precedes the agent's echo.
    assert conv.log == [
        ("event", {"type": QUEUED, "id": "id1", "text": "steer"}),
        ("send", "steer"),
    ]
    assert s.held_ids == []


@pytest.mark.parametrize("cap", ["cuts-in", "rejected", "unsafe"])
async def test_busy_send_is_held_until_the_turn_ends_then_sent_as_one_prompt(cap):
    conv = FakeConversation()
    conv.pending = True
    s = make(conv, cap)
    assert await s.send_when_ready("a") == SendOutcome(id="id1", queued=True)
    assert await s.send_when_ready("b") == SendOutcome(id="id2", queued=True)
    assert conv.sent == [] and s.held_ids == ["id1", "id2"]
    conv.pending = False
    conv.fire({"type": "turn-end"})
    await s.settle()
    assert conv.sent == ["a\n\nb"]
    assert conv.log[-2:] == [
        ("event", {"type": TAKEN, "ids": ["id1", "id2"]}),
        ("send", "a\n\nb"),
    ]
    assert s.held_ids == []


async def test_a_turn_end_with_nothing_held_sends_nothing():
    conv = FakeConversation()
    s = make(conv, "unsafe")
    conv.fire({"type": "turn-end"})
    await s.settle()
    assert conv.log == []


# -- interrupt and send ----------------------------------------------------------

async def test_interrupt_and_send_interrupts_waits_for_the_turn_end_then_sends():
    conv = FakeConversation()
    conv.pending = True
    s = make(conv, "joins-next-step")
    assert await s.interrupt_and_send("now") == "id1"
    assert conv.log == [("event", INTERRUPT), ("interrupt", None), ("send", "now")]


async def test_interrupt_and_send_delivers_what_optio_holds_first_in_one_prompt():
    conv = FakeConversation()
    conv.pending = True
    s = make(conv, "unsafe")
    await s.send_when_ready("a")
    await s.send_when_ready("b")
    assert await s.interrupt_and_send("c") == "id3"
    assert conv.log[-4:] == [
        ("event", INTERRUPT),
        ("interrupt", None),
        ("event", {"type": TAKEN, "ids": ["id1", "id2"]}),
        ("send", "a\n\nb\n\nc"),
    ]
    await s.settle()  # the turn-end flush the interrupt scheduled finds nothing held
    assert conv.sent == ["a\n\nb\n\nc"]


async def test_cuts_in_sends_natively_without_an_optio_interrupt():
    conv = FakeConversation()
    conv.pending = True
    s = make(conv, "cuts-in")
    await s.send_when_ready("a")
    await s.interrupt_and_send("b")
    assert conv.interrupts == 0
    assert conv.log == [
        ("event", {"type": QUEUED, "id": "id1", "text": "a"}),
        ("event", INTERRUPT),
        ("event", {"type": TAKEN, "ids": ["id1"]}),
        ("send", "a\n\nb"),
    ]


async def test_send_now_with_empty_text_delivers_only_the_queue():
    conv = FakeConversation()
    conv.pending = True
    s = make(conv, "unsafe")
    await s.send_when_ready("a")
    assert await s.interrupt_and_send("") is None
    await s.settle()
    assert conv.sent == ["a"]

    native = FakeConversation()
    native.pending = True
    s2 = make(native, "joins-next-step")
    assert await s2.interrupt_and_send("") is None
    assert native.interrupts == 1 and native.sent == []


async def test_no_turn_end_after_the_interrupt_sends_anyway_and_logs(caplog):
    conv = FakeConversation(turn_end_on_interrupt=False)
    conv.pending = True
    s = make(conv, "joins-next-step", turn_end_timeout_s=0.0)
    with caplog.at_level(logging.WARNING, logger="optio_agents.steering"):
        assert await s.interrupt_and_send("late") == "id1"
    assert conv.sent == ["late"]
    assert "no turn end" in caplog.text


async def test_idle_interrupt_and_send_sends_without_interrupting():
    conv = FakeConversation()
    s = make(conv, "joins-next-step")
    await s.interrupt_and_send("x")
    assert conv.log == [("send", "x")]


# -- interrupt -----------------------------------------------------------------

async def test_interrupt_emits_the_marker_only_while_a_turn_runs():
    conv = FakeConversation()
    s = make(conv)
    await s.interrupt()
    assert conv.log == [("interrupt", None)]
    conv.pending = True
    await s.interrupt()
    assert conv.log[1:] == [("event", INTERRUPT), ("interrupt", None)]


# -- lifecycle -----------------------------------------------------------------

async def test_a_closed_conversation_raises_and_emits_nothing():
    conv = FakeConversation()
    conv.closed = True
    conv.pending = True
    s = make(conv)
    with pytest.raises(ConversationClosed):
        await s.send_when_ready("a")
    with pytest.raises(ConversationClosed):
        await s.interrupt_and_send("a")
    with pytest.raises(ConversationClosed):
        await s.interrupt()
    assert events(conv) == []


def test_close_unsubscribes_and_is_idempotent():
    conv = FakeConversation()
    s = make(conv)
    assert len(conv.handlers) == 1
    s.close()
    s.close()
    assert conv.handlers == []


def test_top_level_exports():
    import optio_agents
    for name in ("Steering", "SendOutcome", "BusySend", "BusySendDeclaration",
                 "resolve_busy_send", "BUSY_SEND_VALUES", "steering"):
        assert hasattr(optio_agents, name), name
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `PYTEST packages/optio-agents/tests/test_steering.py`
Expected: collection ERROR, `ModuleNotFoundError: No module named 'optio_agents.steering'`.

- [ ] **Step 3: Implement**

Create `packages/optio-agents/src/optio_agents/steering.py`:
```python
"""Conversation steering: send when ready, interrupt and send, interrupt.

The engine-neutral scaffolding every wrapper's conversation listener uses for
its ``POST /send``, ``/steer`` and ``/interrupt`` routes. A wrapper declares
how its agent treats a message sent while a turn runs (``busy_send``, per
agent with per-model overrides); ``Steering`` adds what the agent lacks:
optio's own queue for agents that cannot take a busy send, the bounded wait
for the turn end after an interrupt, and the synthetic events the
conversation UI renders (``x-optio-queued``, ``x-optio-taken``,
``x-optio-interrupt``). The events go through the wrapper's ``emit`` hook
into its own event stream, so the listener buffers (and a resume persists)
them like native events.

See docs/2026-09-13-conversation-steering-design.md §2 and §3.
"""

from __future__ import annotations

import asyncio
import logging
import re
import uuid
from dataclasses import dataclass, field
from typing import Callable, Literal, Mapping

from optio_agents.conversation import ConversationClosed

_LOG = logging.getLogger(__name__)

# What the agent does with a message sent while a turn runs:
#   joins-next-step  taken at the next tool result, same turn (Claude Code, codex)
#   queues-to-end    the agent's own queue, runs after the turn (grok)
#   cuts-in          cancels the running turn and starts a new one (cursor)
#   rejected         refused while busy (kimicode)
#   unsafe           no defined behaviour (antigravity; any unmeasured agent)
BusySend = Literal["joins-next-step", "queues-to-end", "cuts-in", "rejected", "unsafe"]
BUSY_SEND_VALUES: tuple[str, ...] = (
    "joins-next-step", "queues-to-end", "cuts-in", "rejected", "unsafe",
)
# The agent holds a busy send itself: optio sends it straight through.
NATIVE_QUEUE: frozenset[str] = frozenset({"joins-next-step", "queues-to-end"})

QUEUED_EVENT = "x-optio-queued"
TAKEN_EVENT = "x-optio-taken"
INTERRUPT_EVENT = "x-optio-interrupt"
# Messages optio delivers together form one prompt, in the order written.
PROMPT_SEPARATOR = "\n\n"
# Upper bound on the wait for the turn end after an interrupt.
TURN_END_TIMEOUT_S = 15.0

_VARIANT_SUFFIX = re.compile(r"\[[^\]]*\]$")


@dataclass(frozen=True)
class BusySendDeclaration:
    """A wrapper's measured ``busy_send``: the agent's value, plus overrides
    for models a recording showed behaving differently."""

    agent: BusySend
    models: Mapping[str, BusySend] = field(default_factory=dict)

    def __post_init__(self) -> None:
        for value in (self.agent, *self.models.values()):
            if value not in BUSY_SEND_VALUES:
                raise ValueError(f"unknown busy_send value: {value!r}")

    def for_model(self, model: str | None) -> BusySend:
        """The model's own value, else the agent's. A runtime ``[variant]``
        suffix (e.g. ``claude-opus-4-8[1m]``) is ignored."""
        if model:
            key = _VARIANT_SUFFIX.sub("", model)
            if key in self.models:
                return self.models[key]
        return self.agent


def resolve_busy_send(declaration: BusySendDeclaration | None, model: str | None) -> BusySend:
    """An agent without a declaration is ``unsafe``: always correct, only slower."""
    if declaration is None:
        return "unsafe"
    return declaration.for_model(model)


@dataclass(frozen=True)
class SendOutcome:
    """Result of send_when_ready: the message's id, and whether it waits
    (the agent or optio holds it) rather than starting a turn now."""

    id: str
    queued: bool


class Steering:
    """send_when_ready / interrupt_and_send / interrupt over one Conversation.

    ``busy_send`` is read on every call (it can follow the running model).
    ``emit`` puts a synthetic event into the wrapper's event stream.
    ``is_turn_end`` recognises the native event that ends a turn.
    """

    def __init__(
        self,
        conversation,
        *,
        busy_send: Callable[[], BusySend],
        emit: Callable[[dict], None],
        is_turn_end: Callable[[dict], bool],
        turn_end_timeout_s: float = TURN_END_TIMEOUT_S,
        new_id: Callable[[], str] | None = None,
    ) -> None:
        self._conv = conversation
        self._busy_send = busy_send
        self._emit = emit
        self._is_turn_end = is_turn_end
        self._timeout_s = turn_end_timeout_s
        self._new_id = new_id or (lambda: uuid.uuid4().hex)
        # optio's own queue (cuts-in / rejected / unsafe): (id, text), in order.
        self._held: list[tuple[str, str]] = []
        self._lock = asyncio.Lock()
        self._turn_end = asyncio.Event()
        self._flush_task: asyncio.Task | None = None
        self._unsubscribe = conversation.on_event(self._on_event)

    @property
    def held_ids(self) -> list[str]:
        return [qid for qid, _ in self._held]

    def close(self) -> None:
        unsubscribe, self._unsubscribe = self._unsubscribe, (lambda: None)
        unsubscribe()

    # -- turn end --------------------------------------------------------------

    def _on_event(self, event: dict) -> None:
        if not self._is_turn_end(event):
            return
        self._turn_end.set()
        if self._held and (self._flush_task is None or self._flush_task.done()):
            self._flush_task = asyncio.ensure_future(self._flush_after_turn())

    async def _flush_after_turn(self) -> None:
        async with self._lock:
            try:
                await self._deliver([])
            except ConversationClosed:
                _LOG.warning("steering: conversation closed before held messages were delivered")

    async def settle(self) -> None:
        """Wait for a turn-end flush in progress (tests, orderly teardown)."""
        task = self._flush_task
        if task is not None:
            await task

    # -- helpers ---------------------------------------------------------------

    def _check_open(self) -> None:
        if getattr(self._conv, "closed", False):
            raise ConversationClosed("conversation closed")

    async def _deliver(self, extra: list[str]) -> None:
        """Send everything optio holds plus ``extra`` as ONE prompt, and report
        the held ids in one x-optio-taken (sent one by one, a cuts-in agent
        would cancel each previous message)."""
        ids = [qid for qid, _ in self._held]
        texts = [text for _, text in self._held] + [t for t in extra if t]
        self._held = []
        if ids:
            self._emit({"type": TAKEN_EVENT, "ids": ids})
        if texts:
            await self._conv.send(PROMPT_SEPARATOR.join(texts))

    async def _interrupt_and_wait(self) -> None:
        self._turn_end.clear()
        await self._conv.interrupt()
        if self._turn_end.is_set():
            return
        try:
            await asyncio.wait_for(self._turn_end.wait(), self._timeout_s)
        except asyncio.TimeoutError:
            _LOG.warning(
                "steering: no turn end within %.0f s of the interrupt; sending anyway",
                self._timeout_s,
            )

    # -- the three operations ---------------------------------------------------

    async def send_when_ready(self, text: str) -> SendOutcome:
        """Idle: a plain send. Busy: the agent's own queue takes it
        (joins-next-step, queues-to-end), or optio holds it until the turn
        ends. A busy send reports x-optio-queued first."""
        self._check_open()
        qid = self._new_id()
        async with self._lock:
            busy = self._conv.is_pending()
            if not busy and not self._held:
                await self._conv.send(text)
                return SendOutcome(id=qid, queued=False)
            if self._busy_send() in NATIVE_QUEUE and not self._held:
                self._emit({"type": QUEUED_EVENT, "id": qid, "text": text})
                await self._conv.send(text)
                return SendOutcome(id=qid, queued=True)
            self._held.append((qid, text))
            self._emit({"type": QUEUED_EVENT, "id": qid, "text": text})
            if not busy:
                # The turn already ended and a flush is due: deliver now.
                await self._deliver([])
            return SendOutcome(id=qid, queued=True)

    async def interrupt_and_send(self, text: str) -> str | None:
        """Stop the running step, then deliver what optio holds plus ``text``
        as one prompt. ``cuts-in`` agents cancel natively on the send itself;
        every other agent is interrupted and given at most
        ``turn_end_timeout_s`` to end the turn (its own queue goes first).
        Empty ``text`` is Send now: returns None."""
        self._check_open()
        qid = self._new_id() if text else None
        async with self._lock:
            if self._conv.is_pending():
                self._emit({"type": INTERRUPT_EVENT, "by": "user"})
                if self._busy_send() != "cuts-in":
                    await self._interrupt_and_wait()
            await self._deliver([text])
        return qid

    async def interrupt(self) -> None:
        """Stop only. Emits x-optio-interrupt while a turn runs. Deliberately
        lock-free, so it never waits behind an interrupt_and_send."""
        self._check_open()
        if self._conv.is_pending():
            self._emit({"type": INTERRUPT_EVENT, "by": "user"})
        await self._conv.interrupt()
```

In `packages/optio-agents/src/optio_agents/__init__.py`, replace:
```python
from optio_agents import session_controls
```
with:
```python
from optio_agents import session_controls
from optio_agents import steering
from optio_agents.steering import (
    BUSY_SEND_VALUES,
    BusySend,
    BusySendDeclaration,
    SendOutcome,
    Steering,
    resolve_busy_send,
)
```
and replace:
```python
    "session_controls",
    "SessionControl",
```
with:
```python
    "session_controls",
    "steering",
    "Steering",
    "SendOutcome",
    "BusySend",
    "BusySendDeclaration",
    "resolve_busy_send",
    "BUSY_SEND_VALUES",
    "SessionControl",
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `PYTEST packages/optio-agents/tests/test_steering.py packages/optio-agents/tests/test_package_exports.py`
Expected: PASS, all tests.

- [ ] **Step 5: Document the new public API**

In `packages/optio-agents/AGENTS.md`, replace:
```
## Dependency direction
```
with:
````
## Conversation steering (`optio_agents.steering`)

Design: `docs/2026-09-13-conversation-steering-design.md`. The scaffolding
behind every conversation listener's `POST /send`, `POST /steer` and
`POST /interrupt`.

* `BusySend` — what the agent does with a message sent while a turn runs:
  `joins-next-step` (taken at the next tool result, same turn),
  `queues-to-end` (the agent's own queue, after the turn), `cuts-in`
  (cancels the turn, starts a new one), `rejected`, `unsafe` (undefined).
* `BusySendDeclaration(agent, models={})` — a wrapper's measured value;
  `.for_model(model)` returns the model override or the agent value (a
  `[variant]` suffix is ignored). `resolve_busy_send(None, model)` is
  `unsafe`: an unmeasured agent is always correct, only slower. Add a model
  override only when a recording shows that model behaving differently.
* `Steering(conversation, *, busy_send, emit, is_turn_end,
  turn_end_timeout_s=15.0, new_id=None)` over any `Conversation`
  (`send`, `interrupt`, `is_pending`, `on_event`, optional `closed`):
  * `await send_when_ready(text) -> SendOutcome(id, queued)` — idle: plain
    send. Busy + `joins-next-step`/`queues-to-end`: emits
    `{"type":"x-optio-queued","id","text"}`, then sends (the agent holds it).
    Busy otherwise: emits x-optio-queued and holds it in optio's queue; at
    the turn end (`is_turn_end(event)`) everything held goes as ONE prompt
    joined by a blank line, announced by one `{"type":"x-optio-taken","ids"}`.
  * `await interrupt_and_send(text) -> id | None` — busy: emits
    `{"type":"x-optio-interrupt","by":"user"}`; `cuts-in` then sends
    natively; others `interrupt()` and wait for the turn end (at most
    `turn_end_timeout_s`, then send anyway and log a warning). Then held
    messages + `text` go as one prompt. Empty `text` = Send now (returns None).
  * `await interrupt()` — stop only; emits x-optio-interrupt while busy.
  * `await settle()`, `held_ids`, `close()` (unsubscribes).
* `emit` must put the event into the wrapper's own event stream (in order
  with native events) so the conversation listener buffers and persists it.

## Dependency direction
````

In the root `AGENTS.md`, replace:
```
## TypeScript: optio-contracts
```
with:
```
## Python: engine wrappers — conversation steering

`optio_agents.steering` is the shared scaffolding for sending while the agent
works: each wrapper declares `busy_send` (`joins-next-step` | `queues-to-end` |
`cuts-in` | `rejected` | `unsafe`, per agent with per-model overrides, no
declaration = `unsafe`), and `Steering` implements `send_when_ready`,
`interrupt_and_send` and `interrupt` over its `Conversation`, with optio's own
queue, the 15 s bounded wait after an interrupt, and the synthetic events
`x-optio-queued` / `x-optio-taken` / `x-optio-interrupt`. Details:
`packages/optio-agents/AGENTS.md`. Design:
`docs/2026-09-13-conversation-steering-design.md`.

---

## TypeScript: optio-contracts
```

- [ ] **Step 6: Run the package suite**

Run: `PYTEST packages/optio-agents/tests`
Expected: the failures listed in `/tmp/steer-baseline-py-agents.txt` and no others, plus the new tests passing.

- [ ] **Step 7: Commit**

```bash
ssh excavator 'cd ~/deai/optio-steering && git add packages/optio-agents/src/optio_agents/steering.py packages/optio-agents/src/optio_agents/__init__.py packages/optio-agents/tests/test_steering.py packages/optio-agents/AGENTS.md AGENTS.md && git commit -m "feat(optio-agents): conversation steering scaffold

busy_send capability (per agent + model, unmeasured = unsafe) and Steering:
send_when_ready, interrupt_and_send, interrupt over any Conversation, with
optio own queue flushed as one prompt, the 15 s bounded wait after an
interrupt, and x-optio-queued / x-optio-taken / x-optio-interrupt events." && git log --oneline -1'
```

---
### Task 3: Claude Code conversation hooks and its steering factory

**Files:**
- Modify: `packages/optio-claudecode/src/optio_claudecode/conversation.py`
- Create: `packages/optio-claudecode/src/optio_claudecode/steering.py`
- Test: `packages/optio-claudecode/tests/test_conversation_driver.py` (append)

**Interfaces:**
- Consumes: Task 2's `BusySendDeclaration`, `Steering`.
- Produces:
  - `ClaudeCodeConversation.emit_event(event: dict) -> None` puts a synthetic event on the same queue as native stdout events, so `on_event` subscribers (the listener) see it in order.
  - `ClaudeCodeConversation.is_pending()` becomes False on `system/session_state_changed` `state: "idle"`.
  - `optio_claudecode.steering.BUSY_SEND = BusySendDeclaration(agent="joins-next-step")`.
  - `optio_claudecode.steering.is_turn_end(event) -> bool` (`type == "result"`).
  - `optio_claudecode.steering.make_steering(conversation, **kwargs) -> Steering`. It needs `send`, `interrupt`, `is_pending`, `on_event`, `emit_event` and a `runtime_model` attribute.

Why the idle reset: recording s4 (CLI 2.1.270) shows a message sent while a turn runs *joining* that turn. Two sends produce one `result`. The send/result count alone would leave `is_pending()` True forever. Steering would then treat an idle session as busy, and every Interrupt and send would wait the full 15 s. The CLI brackets every turn with `session_state_changed` running/idle, and idle means nothing awaits a result.

- [ ] **Step 1: Write the failing tests**

Append to `packages/optio-claudecode/tests/test_conversation_driver.py`:
```python
# -- steering hooks (docs/2026-09-13-conversation-steering-design.md) ---------

from optio_claudecode.steering import BUSY_SEND, is_turn_end, make_steering


@pytest.mark.asyncio
async def test_idle_state_clears_pending_after_a_merged_turn(convo):
    # A message sent while a turn runs joins that turn: two sends, ONE result
    # (CLI 2.1.270). session_state_changed idle ends the count.
    c, handle = convo
    seen_result, seen_idle = asyncio.Event(), asyncio.Event()

    def watch(ev):
        if ev.get("type") == "result":
            seen_result.set()
        if ev.get("subtype") == "session_state_changed" and ev.get("state") == "idle":
            seen_idle.set()

    c.on_event(watch)
    reader = asyncio.create_task(c.run_reader())
    await c.send("first")
    await c.send("steer")
    handle.stdout.feed({"type": "result", "subtype": "success", "result": "done", "is_error": False})
    await asyncio.wait_for(seen_result.wait(), 60)
    assert c.is_pending()  # the count alone still waits for a second result
    handle.stdout.feed({"type": "system", "subtype": "session_state_changed", "state": "idle"})
    await asyncio.wait_for(seen_idle.wait(), 60)
    assert not c.is_pending()
    handle.stdout.eof()
    await reader


@pytest.mark.asyncio
async def test_emit_event_reaches_subscribers_unmodified(convo):
    c, handle = convo
    events = []
    c.on_event(events.append)
    c.emit_event({"type": "x-optio-queued", "id": "q1", "text": "hi"})
    reader = asyncio.create_task(c.run_reader())
    handle.stdout.eof()
    await reader
    assert events[0] == {"type": "x-optio-queued", "id": "q1", "text": "hi"}
    assert events[-1]["type"] == "x-optio-closed"


def test_claudecode_declares_joins_next_step_and_ends_turns_on_result():
    assert BUSY_SEND.for_model(None) == "joins-next-step"
    assert BUSY_SEND.for_model("claude-sonnet-5") == "joins-next-step"
    assert is_turn_end({"type": "result", "subtype": "error_during_execution"})
    assert not is_turn_end({"type": "system", "subtype": "session_state_changed", "state": "idle"})


@pytest.mark.asyncio
async def test_steering_busy_send_goes_straight_to_claude_as_queued(convo):
    c, handle = convo
    events = []
    c.on_event(events.append)
    steering = make_steering(c, new_id=lambda: "q1")
    reader = asyncio.create_task(c.run_reader())
    await c.send("long task")
    await asyncio.wait_for(handle.stdin.lines.get(), 60)
    out = await steering.send_when_ready("steer")
    assert (out.id, out.queued) == ("q1", True)
    sent = await asyncio.wait_for(handle.stdin.lines.get(), 60)
    assert sent["message"]["content"][0]["text"] == "steer"
    handle.stdout.eof()
    await reader
    assert {"type": "x-optio-queued", "id": "q1", "text": "steer"} in events


@pytest.mark.asyncio
async def test_steering_interrupt_and_send_waits_for_the_result(convo):
    c, handle = convo
    events = []
    c.on_event(events.append)
    steering = make_steering(c, new_id=lambda: "s1")
    reader = asyncio.create_task(c.run_reader())
    await c.send("long task")
    await asyncio.wait_for(handle.stdin.lines.get(), 60)
    task = asyncio.create_task(steering.interrupt_and_send("now"))
    ctrl = await asyncio.wait_for(handle.stdin.lines.get(), 60)
    assert ctrl["request"]["subtype"] == "interrupt"
    handle.stdout.feed({"type": "control_response", "response": {
        "subtype": "success", "request_id": ctrl["request_id"]}})
    handle.stdout.feed({"type": "result", "subtype": "error_during_execution",
                        "is_error": True, "terminal_reason": "aborted_streaming"})
    msg = await asyncio.wait_for(handle.stdin.lines.get(), 60)
    assert msg["type"] == "user"
    assert msg["message"]["content"][0]["text"] == "now"
    assert await asyncio.wait_for(task, 60) == "s1"
    handle.stdout.eof()
    await reader
    assert events[0] == {"type": "x-optio-interrupt", "by": "user"}
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `PYTEST packages/optio-claudecode/tests/test_conversation_driver.py`
Expected: collection ERROR, `ModuleNotFoundError: No module named 'optio_claudecode.steering'`.

- [ ] **Step 3: Implement**

Create `packages/optio-claudecode/src/optio_claudecode/steering.py`:
```python
"""Claude Code's busy-send capability and its steering scaffold.

Measured with CLI 2.1.270 (docs/2026-09-13-conversation-steering-design.md):
a message sent while a turn runs is taken at the next tool result, in the
same turn, and echoed as a ``user`` event (joins-next-step). An interrupt ends
the turn with a ``result`` (``error_during_execution``); the CLI then runs
whatever it still holds.
"""
from __future__ import annotations

from optio_agents.steering import BusySendDeclaration, Steering

BUSY_SEND = BusySendDeclaration(agent="joins-next-step")


def is_turn_end(event: dict) -> bool:
    """A turn ends with its ``result`` event (success or error)."""
    return event.get("type") == "result"


def make_steering(conversation, **kwargs) -> Steering:
    """Steering over a ClaudeCodeConversation, or anything with its surface:
    send, interrupt, is_pending, on_event, emit_event, runtime_model."""
    return Steering(
        conversation,
        busy_send=lambda: BUSY_SEND.for_model(getattr(conversation, "runtime_model", None)),
        emit=conversation.emit_event,
        is_turn_end=is_turn_end,
        **kwargs,
    )
```

In `packages/optio-claudecode/src/optio_claudecode/conversation.py`, replace:
```python
            model = obj.get("model")
            if isinstance(model, str) and model:
                self.runtime_model = model
                self.runtime_model_observed.set()
        self._event_queue.put_nowait(obj)
```
with:
```python
            model = obj.get("model")
            if isinstance(model, str) and model:
                self.runtime_model = model
                self.runtime_model_observed.set()
        elif t == "system" and obj.get("subtype") == "session_state_changed":
            # The CLI brackets every turn with running/idle. A message sent
            # while a turn runs joins that turn (one result for two sends), so
            # the send/result count alone would stay "pending" forever; idle
            # means nothing awaits a result any more.
            if obj.get("state") == "idle":
                self._pending = 0
        self._event_queue.put_nowait(obj)
```
and replace:
```python
    def begin_restart(self) -> None:
```
with:
```python
    def emit_event(self, event: dict) -> None:
        """Fan out a synthetic ``x-optio-*`` event to on_event subscribers, in
        order with the native stream. The steering scaffold's hook: the
        conversation listener buffers it like any event, so a reload (and a
        resume) replays it."""
        self._event_queue.put_nowait(event)

    def begin_restart(self) -> None:
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `PYTEST packages/optio-claudecode/tests/test_conversation_driver.py`
Expected: PASS, all tests.

- [ ] **Step 5: Commit**

```bash
ssh excavator 'cd ~/deai/optio-steering && git add packages/optio-claudecode/src/optio_claudecode/steering.py packages/optio-claudecode/src/optio_claudecode/conversation.py packages/optio-claudecode/tests/test_conversation_driver.py && git commit -m "feat(optio-claudecode): busy_send joins-next-step and steering hooks

ClaudeCodeConversation.emit_event puts synthetic events into its own event
stream; is_pending resets on session_state_changed idle, since a mid-turn
send joins the running turn (two sends, one result). make_steering builds
the shared Steering for Claude Code." && git log --oneline -1'
```

AGENTS.md for this package is updated in Task 4, together with the endpoints that make the new surface reachable.

---

### Task 4: Listener `/send` via steering, new `/steer`, docs

**Files:**
- Modify: `packages/optio-claudecode/src/optio_claudecode/conversation_listener.py`
- Test: `packages/optio-claudecode/tests/test_conversation_listener.py`
- Modify: `packages/optio-claudecode/AGENTS.md`, `AGENTS.md` (root), `docs/writing-agent-wrappers.md`

**Interfaces:**
- Consumes: Task 3's `make_steering`; Task 2's `Steering`, `SendOutcome`.
- Produces:
  - `ConversationListener(conversation, *, password, initial_events=None, download_reader=None, max_download_bytes=10_000_000, steering: Steering | None = None)`. The default is `make_steering(conversation)`.
  - HTTP:
    - `POST /send {text}` → `200 {"ok": true, "id": str, "queued": bool}`; 400 on bad text; 409 when closed.
    - `POST /steer {text}` (str, may be `""`) → `200 {"ok": true, "id": str | null}`; 400 when text is not a str; 409 when closed.
    - `POST /interrupt` → steering.interrupt(), unchanged response.
  - Task 8's `ClaudeCodeView` reads `id` and `queued` from `/send` and calls `/steer`.

Buffer semantics checked for this task:
- The listener buffers every event except `stream_event`.
- `export_buffer()` persists everything except `x-optio-closed`.
- `x-optio-resumed` is appended on re-prime and persisted.

The steering events enter through `conversation.emit_event` → `on_event` → `_broadcast`, so they are buffered, replayed on reload, and persisted across a resume with no listener change. Only the routes change.

- [ ] **Step 1: Write the failing tests**

In `packages/optio-claudecode/tests/test_conversation_listener.py`, replace the whole `FakeConversation` class with:
```python
class FakeConversation:
    def __init__(self):
        self.handlers = []
        self.perm_handler = None
        self.sent = []
        self.interrupts = 0
        self.closed = False
        # Steering surface: busy is set by the test; interrupt() ends a busy
        # turn at once with a result event (the CLI's turn end).
        self.pending = False
        self.runtime_model = None

    def on_event(self, h):
        self.handlers.append(h)
        return lambda: self.handlers.remove(h)

    def on_permission_request(self, h):
        self.perm_handler = h
        return lambda: None

    def is_pending(self):
        return self.pending

    def emit_event(self, event):
        self.fire(event)

    async def send(self, text):
        if self.closed:
            raise ConversationClosed("closed")
        self.sent.append(text)

    async def interrupt(self):
        if self.closed:
            raise ConversationClosed("closed")
        self.interrupts += 1
        if self.pending:
            self.pending = False
            self.fire({"type": "result", "subtype": "error_during_execution",
                       "is_error": True, "terminal_reason": "aborted_streaming"})

    def fire(self, event):
        for h in list(self.handlers):
            h(event)
```
Append to the file:
```python
# -- steering routes (docs/2026-09-13-conversation-steering-design.md) --------

async def test_send_returns_id_and_queued_and_buffers_the_queued_event(listener):
    conv, lst, url = listener
    async with aiohttp.ClientSession() as s:
        r = await s.post(f"{url}/send", json={"text": "hi"}, headers=_auth("pw"))
        idle = await r.json()
        assert r.status == 200 and idle["ok"] is True and idle["queued"] is False
        assert isinstance(idle["id"], str) and idle["id"]
        conv.pending = True
        r = await s.post(f"{url}/send", json={"text": "steer"}, headers=_auth("pw"))
        busy = await r.json()
        assert busy["queued"] is True and busy["id"] != idle["id"]
    assert conv.sent == ["hi", "steer"]
    queued = [e for _, e in lst._buffer if e.get("type") == "x-optio-queued"]
    assert queued == [{"type": "x-optio-queued", "id": busy["id"], "text": "steer"}]


async def test_steer_interrupts_waits_for_the_turn_end_then_sends(listener):
    conv, lst, url = listener
    conv.pending = True
    async with aiohttp.ClientSession() as s:
        r = await s.post(f"{url}/steer", json={"text": "now"}, headers=_auth("pw"))
        body = await r.json()
    assert r.status == 200 and body["ok"] is True and isinstance(body["id"], str)
    assert conv.interrupts == 1 and conv.sent == ["now"]
    types = [e.get("type") for _, e in lst._buffer]
    assert types.index("x-optio-interrupt") < types.index("result")


async def test_steer_with_empty_text_only_interrupts(listener):
    conv, lst, url = listener
    conv.pending = True
    async with aiohttp.ClientSession() as s:
        r = await s.post(f"{url}/steer", json={"text": ""}, headers=_auth("pw"))
        body = await r.json()
    assert r.status == 200 and body == {"ok": True, "id": None}
    assert conv.interrupts == 1 and conv.sent == []


async def test_steer_rejects_bad_text_closed_and_unauthorized(listener):
    conv, lst, url = listener
    async with aiohttp.ClientSession() as s:
        r = await s.post(f"{url}/steer", json={"text": 5}, headers=_auth("pw"))
        assert r.status == 400
        r = await s.post(f"{url}/steer", json={"text": "x"})
        assert r.status == 401
        conv.closed = True
        r = await s.post(f"{url}/steer", json={"text": "x"}, headers=_auth("pw"))
        assert r.status == 409


async def test_interrupt_emits_the_marker_only_while_busy(listener):
    conv, lst, url = listener
    async with aiohttp.ClientSession() as s:
        await s.post(f"{url}/interrupt", json={}, headers=_auth("pw"))
        assert not any(e.get("type") == "x-optio-interrupt" for _, e in lst._buffer)
        conv.pending = True
        await s.post(f"{url}/interrupt", json={}, headers=_auth("pw"))
    assert [e for _, e in lst._buffer if e.get("type") == "x-optio-interrupt"] == [
        {"type": "x-optio-interrupt", "by": "user"},
    ]
    assert conv.interrupts == 2


async def test_steering_events_persist_across_a_resume():
    conv = FakeConversation()
    lst = ConversationListener(conv, password="pw")
    conv.pending = True
    await lst._steering.send_when_ready("steer")
    await lst._steering.interrupt()
    exported = lst.export_buffer()
    types = [e["type"] for _, e in exported]
    assert "x-optio-queued" in types and "x-optio-interrupt" in types
    lst2 = ConversationListener(
        FakeConversation(), password="pw",
        initial_events=[(x[0], x[1]) for x in exported],
    )
    assert [e["type"] for _, e in lst2._buffer] == types + ["x-optio-resumed"]
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `PYTEST packages/optio-claudecode/tests/test_conversation_listener.py`
Expected: the new tests FAIL (the `/send` response has no `id`; `/steer` returns 404/405; `lst._steering` is an AttributeError). The existing tests still PASS.

- [ ] **Step 3: Implement**

In `conversation_listener.py`, replace the docstring lines:
```
  POST /send       — {text}                      -> conversation.send
  POST /interrupt  — {}                          -> conversation.interrupt
```
with:
```
  POST /send       — {text}  -> steering.send_when_ready; {ok, id, queued}
  POST /steer      — {text}  -> steering.interrupt_and_send; {ok, id}
                     (empty text: deliver what is queued; id is null)
  POST /interrupt  — {}      -> steering.interrupt (stop only)
```
Replace:
```python
from optio_agents.conversation import ConversationClosed, PermissionDecision
```
with:
```python
from optio_agents.conversation import ConversationClosed, PermissionDecision
from optio_agents.steering import Steering

from optio_claudecode.steering import make_steering
```
Replace:
```python
        max_download_bytes: int = 10_000_000,
    ) -> None:
```
with:
```python
        max_download_bytes: int = 10_000_000,
        steering: "Steering | None" = None,
    ) -> None:
```
Replace:
```python
        self._unsubscribe = conversation.on_event(self._on_event)
        conversation.on_permission_request(self._on_permission_request)
```
with:
```python
        self._unsubscribe = conversation.on_event(self._on_event)
        conversation.on_permission_request(self._on_permission_request)
        # Send when ready / Interrupt and send / Interrupt. Its synthetic
        # events come back through conversation.emit_event -> _on_event, so
        # they are buffered and persisted like native events.
        self._steering = steering if steering is not None else make_steering(conversation)
```
Replace:
```python
        try:
            await self._conversation.send(text)
        except ConversationClosed:
            return web.json_response({"ok": False, "reason": "closed"}, status=409)
        return web.json_response({"ok": True})

    async def _handle_interrupt(self, request: web.Request) -> web.Response:
        if not self._authorized(request):
            return web.json_response({"ok": False}, status=401)
        try:
            await self._conversation.interrupt()
        except ConversationClosed:
            return web.json_response({"ok": False, "reason": "closed"}, status=409)
        return web.json_response({"ok": True})
```
with:
```python
        try:
            outcome = await self._steering.send_when_ready(text)
        except ConversationClosed:
            return web.json_response({"ok": False, "reason": "closed"}, status=409)
        return web.json_response({"ok": True, "id": outcome.id, "queued": outcome.queued})

    async def _handle_steer(self, request: web.Request) -> web.Response:
        if not self._authorized(request):
            return web.json_response({"ok": False}, status=401)
        try:
            payload = await request.json()
        except Exception:  # noqa: BLE001
            return web.json_response({"ok": False, "reason": "bad-json"}, status=400)
        text = payload.get("text", "")
        if not isinstance(text, str):
            return web.json_response({"ok": False, "reason": "bad-text"}, status=400)
        try:
            qid = await self._steering.interrupt_and_send(text)
        except ConversationClosed:
            return web.json_response({"ok": False, "reason": "closed"}, status=409)
        return web.json_response({"ok": True, "id": qid})

    async def _handle_interrupt(self, request: web.Request) -> web.Response:
        if not self._authorized(request):
            return web.json_response({"ok": False}, status=401)
        try:
            await self._steering.interrupt()
        except ConversationClosed:
            return web.json_response({"ok": False, "reason": "closed"}, status=409)
        return web.json_response({"ok": True})
```
Replace:
```python
        app.router.add_post("/send", self._handle_send)
```
with:
```python
        app.router.add_post("/send", self._handle_send)
        app.router.add_post("/steer", self._handle_steer)
```
Replace:
```python
        unsubscribe = self._unsubscribe
        self._unsubscribe = lambda: None
        unsubscribe()
```
with:
```python
        unsubscribe = self._unsubscribe
        self._unsubscribe = lambda: None
        unsubscribe()
        self._steering.close()
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `PYTEST packages/optio-claudecode/tests/test_conversation_listener.py packages/optio-claudecode/tests/test_conversation_driver.py`
Expected: PASS, all tests.

- [ ] **Step 5: Docs**

In `packages/optio-claudecode/AGENTS.md`, replace:
```
* `is_pending()` — `True` while a sent message has no `result` yet.
```
with:
```
* `is_pending()` — `True` while a sent message has no `result` yet. Reset by
  `system/session_state_changed` `idle`: a message sent mid-turn joins that
  turn, so two sends can share one `result`.
* `emit_event(event)` — put a synthetic `x-optio-*` event into the event
  stream, in order with native events (the steering scaffold's hook).
```
Replace:
```
| `POST /send` | `{text}` → `conversation.send(text)`. 409 when closed. |
| `POST /interrupt` | `{}` → `conversation.interrupt()`. No-op when idle. |
```
with:
```
| `POST /send` | `{text}` → Send when ready (`Steering.send_when_ready`). Returns `{ok, id, queued}`; `queued` is true when a turn was running (Claude holds the message and takes it at the next tool result). 409 when closed. |
| `POST /steer` | `{text}` → Interrupt and send (`Steering.interrupt_and_send`): interrupt the running turn, wait for its `result` (at most 15 s), then send `text`. Empty `text` = Send now: only interrupt, so Claude runs what it holds. Returns `{ok, id}` (`id` null for empty text). 409 when closed. |
| `POST /interrupt` | `{}` → `Steering.interrupt()`: stop only; emits `x-optio-interrupt` while a turn runs. No-op when idle. |
```
Replace:
```
* Resume: `export_buffer()` persists the buffer with the snapshot, minus
```
with:
```
* Steering events (`optio_claudecode.steering`, `busy_send` =
  `joins-next-step` via `BUSY_SEND`): `{"type": "x-optio-queued", "id",
  "text"}` for a Send when ready that arrived mid-turn, and `{"type":
  "x-optio-interrupt", "by": "user"}` before every interrupt optio sends
  while a turn runs. Both enter through `emit_event`, so they are buffered,
  replayed and persisted like native events.
* Resume: `export_buffer()` persists the buffer with the snapshot, minus
```
In the root `AGENTS.md`, replace:
```
`x-optio-queued` / `x-optio-taken` / `x-optio-interrupt`. Details:
`packages/optio-agents/AGENTS.md`. Design:
```
with:
```
`x-optio-queued` / `x-optio-taken` / `x-optio-interrupt`. A wrapper's
conversation listener exposes it as `POST /send` (send when ready, returns
`{id, queued}`), `POST /steer` (interrupt and send; empty text = send what is
queued) and `POST /interrupt`; stage 1 wires Claude Code
(`packages/optio-claudecode/AGENTS.md`). Details:
`packages/optio-agents/AGENTS.md`. Design:
```
In `docs/writing-agent-wrappers.md`, replace:
```
### C. Conversation UI
```
with:
```
### B.1 Busy sends and steering

**Goal.** While the agent works the operator always gets **Send when ready**
and **Interrupt and send**; the wrapper adds whatever scaffolding its agent
lacks.

**Interface to implement.** Declare `busy_send` for the agent (per-model
overrides only where a recording shows a model differs):
`BusySendDeclaration(agent="joins-next-step" | "queues-to-end" | "cuts-in" |
"rejected" | "unsafe", models={...})`. Not measured yet → declare nothing:
`resolve_busy_send(None, …)` is `unsafe`, always correct, only slower. Build
one `optio_agents.steering.Steering` per conversation (`busy_send` callable,
an `emit` hook that puts synthetic events into your own event stream, and
`is_turn_end(event)` for your native turn-end event) and route the
conversation listener's `POST /send` → `send_when_ready` (returns `{ok, id,
queued}`), `POST /steer` → `interrupt_and_send` (returns `{ok, id}`; empty
text = deliver what is queued) and `POST /interrupt` → `interrupt`. Steering
emits `x-optio-queued {id, text}`, `x-optio-taken {ids}` and
`x-optio-interrupt {by:"user"}`; your reducer maps them plus your agent's
native echo / cancel signals.

**Reference.** `optio-agents/…/steering.py`; Claude Code:
`optio-claudecode/…/steering.py` (`BUSY_SEND`, `make_steering`) and
`…/conversation_listener.py`.

### C. Conversation UI
```
And replace:
```
| `Conversation`, `PermissionRequest`, `PermissionDecision`, `ConversationClosed` | `optio-agents/…/conversation.py` |
```
with:
```
| `Conversation`, `PermissionRequest`, `PermissionDecision`, `ConversationClosed` | `optio-agents/…/conversation.py` |
| `BusySend`, `BusySendDeclaration`, `resolve_busy_send`, `Steering`, `SendOutcome` | `optio-agents/…/steering.py` |
```

- [ ] **Step 6: Run the claudecode suite**

Run: `PYTEST packages/optio-claudecode/tests`
Expected: the failures listed in `/tmp/steer-baseline-py-claudecode.txt` and no others, plus the new tests passing.

- [ ] **Step 7: Commit**

```bash
ssh excavator 'cd ~/deai/optio-steering && git add packages/optio-claudecode/src/optio_claudecode/conversation_listener.py packages/optio-claudecode/tests/test_conversation_listener.py packages/optio-claudecode/AGENTS.md AGENTS.md docs/writing-agent-wrappers.md && git commit -m "feat(optio-claudecode): POST /steer and /send via send_when_ready

/send returns {ok, id, queued}; /steer interrupts, waits for the result
and sends (empty text = let Claude run what it holds); /interrupt emits
x-optio-interrupt while a turn runs. Steering events are buffered and
persisted like native events. Documents the busy_send capability." && git log --oneline -1'
```

---
### Task 5: Shared model and claudecode reducer, queue placement

**Files:**
- Modify: `packages/optio-conversation-ui/src/chat.ts`
- Modify: `packages/optio-conversation-ui/src/claudecode/events.ts`
- Create: `packages/optio-conversation-ui/src/__tests__/claudecode-steering.test.ts`
- Create: `packages/optio-conversation-ui/src/__tests__/claudecode-steering-real-wire.test.ts`
- Create: `packages/optio-conversation-ui/src/__tests__/fixtures/claudecode-steer-streaming.jsonl`
- Modify: `packages/optio-conversation-ui/src/__tests__/claudecode-events.test.ts` (one test changes)

**Interfaces:**
- Consumes: the wire events from Task 4: `x-optio-queued {id, text}` and `x-optio-taken {ids}`. Task 8 adds the view's local echo `{type: 'x-optio-local-user', text, id?, queued?}`.
- Produces (`chat.ts`):
  - user item gains `queued?: boolean`, `queueId?: string`; activity item gains `muted?: boolean`.
  - `isQueued(item): item is UserItem`
  - `appendItems(items, rows)` inserts in front of the first queued bubble.
  - `takeQueuedAt(items, idx, before = [])`
  - `takeQueuedIds(items, ids)`
  - `addQueued(items, id, text, seq)`
  - `dropUndelivered(items)` turns still-queued bubbles into muted `Not delivered: <text>` activity rows.
  - These helpers are engine-neutral; stage 2/3 reducers reuse them.

Placement rules implemented here (the same live and on replay):
1. A queued bubble is pinned at the bottom and never counts as newer content. New rows go in front of it, and a streaming answer keeps growing above it.
2. A queued bubble taken by the agent's echo (FIFO by text) or by `x-optio-taken` (by id) leaves the pinned group and lands at the take point: the end of the conversation content.
3. A user echo is pulled in front of the pending answer only when nothing but queued bubbles follows that answer. An echo after a tool row is a mid-turn take and lands after that row.
4. The view's local echo and `x-optio-queued` for the same id make one bubble, whichever arrives first. A local echo that arrives after its message was taken adds nothing. A local message sent while queued bubbles exist is pinned behind them.
5. `x-optio-closed` / `x-optio-resumed` replace still-queued bubbles with a muted `Not delivered: …` row. The CLI's queue died with its process.

- [ ] **Step 1: Build the s4 fixture (on superego) and copy it to excavator**

Write this script to `/tmp/steer-fixtures/trim.py` on superego. Task 6 uses it for the other three fixtures.
```python
"""Trim a Claude Code steer/interrupt recording into a conversation-ui fixture.

Input lines: {"t": ..., "ev": <stdout event>} | {"t": ..., "_sent": <stdin line>} | {"t": ..., "_end": ...}.
Output: the event stream the conversation listener would hold, one JSON object per line:
  - a user message sent while a turn runs -> {"type":"x-optio-queued","id":"q<n>","text":...}
  - an interrupt control_request         -> {"type":"x-optio-interrupt","by":"user"}
  - a user message sent while idle, _end -> dropped (the CLI echoes the message itself)
  - hook/status/thinking_tokens system events, rate_limit_event -> dropped
  - uuids, session ids, usage, caller, cwd/tools dropped; signatures -> "sig";
    long successful tool_result outputs -> "(output trimmed)"; /home/csillag -> /home/user
Event order, content blocks, deltas and timestamps are kept.
"""
import json
import sys

DROP_SYSTEM = {"hook_started", "hook_response", "status", "thinking_tokens"}


def scrub(obj):
    if isinstance(obj, str):
        return obj.replace("/home/csillag", "/home/user")
    if isinstance(obj, list):
        return [scrub(x) for x in obj]
    if isinstance(obj, dict):
        return {k: scrub(v) for k, v in obj.items()}
    return obj


def block(b):
    b = dict(b)
    b.pop("caller", None)
    if b.get("type") == "thinking":
        b["signature"] = "sig" if b.get("signature") else ""
    if b.get("type") == "tool_result" and not b.get("is_error"):
        c = b.get("content")
        if isinstance(c, str) and len(c) > 200:
            b["content"] = "(output trimmed)"
    return b


def message(m):
    out = {k: m[k] for k in ("model", "id", "type", "role") if k in m}
    c = m.get("content")
    out["content"] = [block(b) for b in c] if isinstance(c, list) else c
    return out


def stream(ev):
    e = ev["event"]
    t = e.get("type")
    if t == "message_start":
        e = {"type": t, "message": message(e["message"])}
    elif t == "content_block_start":
        e = {"type": t, "index": e.get("index"), "content_block": block(e["content_block"])}
    elif t == "content_block_delta":
        d = dict(e["delta"])
        if d.get("type") == "signature_delta":
            d["signature"] = "sig"
        e = {"type": t, "index": e.get("index"), "delta": d}
    elif t == "message_delta":
        e = {"type": t, "delta": e.get("delta", {})}
    else:
        e = {"type": t, **({"index": e["index"]} if "index" in e else {})}
    return {"type": "stream_event", "event": e}


def trim(ev):
    t = ev.get("type")
    if t == "rate_limit_event":
        return None
    if t == "system":
        st = ev.get("subtype")
        if st in DROP_SYSTEM:
            return None
        if st == "init":
            return {"type": "system", "subtype": "init", "model": ev.get("model")}
        if st == "session_state_changed":
            return {"type": "system", "subtype": st, "state": ev.get("state")}
        keep = ("type", "subtype", "task_id", "tool_use_id", "description", "is_backgrounded",
                "task_type", "status", "summary")
        return {k: ev[k] for k in keep if k in ev}
    if t == "stream_event":
        return stream(ev)
    if t in ("user", "assistant"):
        out = {"type": t, "message": message(ev["message"])}
        for k in ("parent_tool_use_id", "timestamp", "isReplay", "origin", "aborted"):
            if k in ev:
                out[k] = ev[k]
        return out
    if t == "result":
        keep = ("type", "subtype", "is_error", "result", "stop_reason", "terminal_reason", "num_turns")
        return {k: ev[k] for k in keep if k in ev}
    if t == "control_response":
        return {"type": t, "response": ev.get("response")}
    return None


def main(src, dst):
    busy = False
    queued = 0
    out = []
    for line in open(src, encoding="utf-8"):
        rec = json.loads(line)
        if "ev" in rec:
            ev = rec["ev"]
            if ev.get("type") == "system" and ev.get("subtype") == "session_state_changed":
                busy = ev.get("state") == "running"
            if ev.get("type") == "result":
                busy = False
            if ev.get("type") == "user" and not busy:
                busy = True
            kept = trim(ev)
            if kept is not None:
                out.append(kept)
        elif "_sent" in rec:
            sent = rec["_sent"]
            if sent.get("type") == "control_request" and sent.get("request", {}).get("subtype") == "interrupt":
                out.append({"type": "x-optio-interrupt", "by": "user"})
            elif sent.get("type") == "user" and busy:
                queued += 1
                text = sent["message"]["content"][0]["text"]
                out.append({"type": "x-optio-queued", "id": f"q{queued}", "text": text})
    with open(dst, "w", encoding="utf-8") as f:
        for ev in out:
            f.write(json.dumps(scrub(ev), ensure_ascii=False, separators=(",", ":")) + "\n")


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2])
```
Run on superego, then copy the result:
```bash
cd /tmp/steer-fixtures && python3 trim.py /tmp/steer-interrupt-test/s4-steer-while-streaming.jsonl claudecode-steer-streaming.jsonl
wc -l claudecode-steer-streaming.jsonl; grep -c csillag claudecode-steer-streaming.jsonl; grep -n x-optio claudecode-steer-streaming.jsonl
scp claudecode-steer-streaming.jsonl excavator:deai/optio-steering/packages/optio-conversation-ui/src/__tests__/fixtures/
```
Expected:
- 239 lines, `0` matches for `csillag`.
- Exactly one synthetic line: `{"type":"x-optio-queued","id":"q1","text":"(Steer: mention the year 1900.)"}`, at line 43, in the middle of the answer's text deltas.
- Its non-`stream_event` lines, in order: `session_state_changed running`, `init`, the prompt echo, a thinking-only `assistant`, `assistant` tool_use `cat …`, its `tool_result` (`(output trimmed)`), a thinking-only `assistant`, the `x-optio-queued`, `assistant` text (the paragraph), `assistant` tool_use `echo step2`, its `tool_result`, the steer echo `(Steer: mention the year 1900.)` (timestamp 00:09:07.962Z, earlier than the tool_result's), `assistant` text `By 1900, …`, `result` success, `session_state_changed idle`.

- [ ] **Step 2: Write the failing tests**

Create `src/__tests__/claudecode-steering.test.ts`:
```ts
import { describe, expect, it } from 'vitest';
import { initialChatState, reduceEvent } from '../claudecode/events.js';
import type { ChatItem, ChatState } from '../chat.js';
import { bundleUploadNotice } from '../uploads.js';

// Steering events through the claudecode reducer. Wire builders as in
// claudecode-events.test.ts; x-optio-* are the listener's synthetic events
// and the view's local echo.
const user = (text: string) => ({ type: 'user', message: { role: 'user', content: [{ type: 'text', text }] } });
const delta = (text: string) => ({ type: 'stream_event', event: { type: 'content_block_delta', delta: { type: 'text_delta', text } } });
const assistantText = (text: string, msgId?: string) => ({ type: 'assistant', message: { role: 'assistant', id: msgId, content: [{ type: 'text', text }] } });
const toolCall = (id: string, name: string, input: unknown) => ({ type: 'assistant', message: { role: 'assistant', content: [{ type: 'tool_use', id, name, input }] } });
const toolResult = (id: string, content: unknown, isError = false) => ({ type: 'user', message: { role: 'user', content: [{ type: 'tool_result', tool_use_id: id, content, is_error: isError }] } });
const result = (text: string) => ({ type: 'result', subtype: 'success', result: text });
const queued = (id: string, text: string) => ({ type: 'x-optio-queued', id, text });
const taken = (ids: string[]) => ({ type: 'x-optio-taken', ids });
const localUser = (text: string, id?: string, isQueued = false) => ({ type: 'x-optio-local-user', text, id, queued: isQueued });
const NOW = Date.parse('2026-09-13T17:49:00.000Z');

function run(events: any[], from: ChatState = initialChatState): ChatState {
  return events.reduce((s, ev, i) => reduceEvent(s, ev, i + 1, NOW), from);
}
type UserItem = Extract<ChatItem, { kind: 'user' }>;
const users = (s: ChatState) => s.items.filter((i) => i.kind === 'user') as UserItem[];
// Kinds, with queued bubbles told apart.
const kinds = (s: ChatState) => s.items.map((i) => (i.kind === 'user' && i.queued ? 'queued' : i.kind));
const texts = (s: ChatState) => s.items.map((i) => ('text' in i ? i.text : i.kind));

describe('claudecode steering: queued bubbles', () => {
  it('x-optio-queued adds a queued bubble pinned at the bottom', () => {
    const s = run([user('q'), delta('ans'), queued('q1', 'steer')]);
    expect(kinds(s)).toEqual(['user', 'assistant', 'queued']);
    expect(users(s)[1]).toMatchObject({ text: 'steer', queued: true, queueId: 'q1' });
  });

  it('the streaming answer keeps growing above a queued bubble: one bubble, not split, not repeated', () => {
    const s = run([user('q'), delta('Hel'), queued('q1', 'steer'), delta('lo'), assistantText('Hello', 'm1')]);
    expect(kinds(s)).toEqual(['user', 'assistant', 'queued']);
    const bubbles = s.items.filter((i) => i.kind === 'assistant');
    expect(bubbles).toHaveLength(1);
    expect(texts(s)[1]).toBe('Hello');
  });

  it('new rows go in front of queued bubbles', () => {
    const s = run([user('q'), queued('q1', 'steer'), toolCall('t1', 'Bash', { command: 'ls' })]);
    expect(kinds(s)).toEqual(['user', 'tool', 'queued']);
  });

  it('the echo takes the queued bubble to after the tool row it came with', () => {
    const s = run([
      user('q'), delta('para'), queued('q1', 'steer'), assistantText('para', 'm1'),
      toolCall('t1', 'Bash', {}), toolResult('t1', 'ok'), user('steer'),
      assistantText('more', 'm2'), result('more'),
    ]);
    expect(kinds(s)).toEqual(['user', 'assistant', 'tool', 'user', 'assistant']);
    expect(users(s)[1]).toMatchObject({ text: 'steer', queueId: 'q1' });
    expect(users(s)[1].queued).toBeUndefined();
    expect(texts(s).filter((_, i) => s.items[i].kind === 'assistant')).toEqual(['para', 'more']);
  });

  it('an echo matching the second queued bubble moves only that one', () => {
    const s = run([user('q'), queued('q1', 'a'), queued('q2', 'b'), user('b')]);
    expect(kinds(s)).toEqual(['user', 'user', 'queued']);
    expect(users(s).map((u) => u.text)).toEqual(['q', 'b', 'a']);
  });

  it('x-optio-taken takes held messages by id, in order', () => {
    const s = run([user('q'), queued('q1', 'a'), queued('q2', 'b'), taken(['q1', 'q2'])]);
    expect(kinds(s)).toEqual(['user', 'user', 'user']);
    expect(users(s).map((u) => u.text)).toEqual(['q', 'a', 'b']);
  });

  it('the local echo and x-optio-queued of one message make one bubble, in either order', () => {
    const a = run([user('q'), localUser('steer', 'q1', true), queued('q1', 'steer')]);
    const b = run([user('q'), queued('q1', 'steer'), localUser('steer', 'q1', true)]);
    expect(users(a)).toHaveLength(2);
    expect(users(a)[1]).toMatchObject({ text: 'steer', queued: true, queueId: 'q1' });
    expect(users(a)[1].local).toBeUndefined();
    expect(a.items).toEqual(b.items);
  });

  it('a local echo arriving after its message was taken adds nothing', () => {
    const s = run([user('q'), queued('q1', 'steer'), user('steer'), localUser('steer', 'q1', true)]);
    expect(users(s)).toHaveLength(2);
    expect(kinds(s)).toEqual(['user', 'user']);
  });

  it('a message sent behind queued ones waits behind them', () => {
    const s = run([user('q'), queued('q1', 'a'), localUser('b', 's1')]);
    expect(kinds(s)).toEqual(['user', 'queued', 'queued']);
  });

  it('an idle send is a plain local bubble, as before', () => {
    const s = run([localUser('hi', 's1')]);
    expect(users(s)[0]).toMatchObject({ text: 'hi', local: true, queueId: 's1' });
    expect(users(s)[0].queued).toBeUndefined();
  });

  it('x-optio-queued carrying an upload notice shows only the prompt text, and its echo takes it', () => {
    const prompt = bundleUploadNotice(['uploads/pic.png'], 'review it');
    const mid = run([user('q'), queued('q1', prompt)]);
    expect(users(mid)[1]).toMatchObject({ text: 'review it', queued: true });
    const s = run([user(prompt)], mid);
    expect(kinds(s)).toEqual(['user', 'activity', 'user']);
    expect(users(s)[1]).toMatchObject({ text: 'review it', queueId: 'q1' });
  });

  it('session end turns an undelivered queued bubble into a muted note', () => {
    for (const end of [{ type: 'x-optio-closed', reason: 'x' }, { type: 'x-optio-resumed' }]) {
      const s = run([user('q'), queued('q1', 'steer'), end]);
      expect(users(s).some((u) => u.queued)).toBe(false);
      const note = s.items.find((i) => i.kind === 'activity');
      expect(note).toMatchObject({ kind: 'activity', text: 'Not delivered: steer', muted: true });
    }
  });
});
```
Create `src/__tests__/claudecode-steering-real-wire.test.ts`:
```ts
// Real Claude Code stream-json (CLI 2.1.270, claude-sonnet-5) with optio's
// steering events, as the conversation listener buffers them. Trimmed from
// the design's synthetic runs (superego:/tmp/steer-interrupt-test) by the
// trim.py in docs/2026-09-13-conversation-steering-plan-stage1.md: hook,
// status, thinking_tokens and rate_limit events dropped; uuids, session ids,
// usage and tool callers dropped; signatures shortened; long tool outputs
// replaced by "(output trimmed)"; every message the driver sent mid-turn
// became the x-optio-queued the listener emits for it, every interrupt the
// x-optio-interrupt. Event order, content blocks, deltas and timestamps kept.
//  - claudecode-steer-streaming.jsonl (s4): a steer sent while the answer
//    streams; Claude takes it after the next tool result, same turn.

import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { describe, expect, it } from 'vitest';
import { initialChatState, reduceEvent } from '../claudecode/events.js';
import type { ChatItem, ChatState } from '../chat.js';

const HERE = path.dirname(fileURLToPath(import.meta.url));
function load(name: string): any[] {
  return fs
    .readFileSync(path.join(HERE, 'fixtures', name), 'utf-8')
    .trim()
    .split('\n')
    .map((line) => JSON.parse(line));
}

// A reload hours after the runs: the reducer clock is far from the wire times.
const NOW = Date.parse('2026-09-13T17:49:00.000Z');

// Live: every event, as the SSE live tail delivers it. Replay: what the
// listener's buffer holds (never stream_event), with the original seqs.
function live(events: any[], now = NOW): ChatState {
  return events.reduce((s, ev, i) => reduceEvent(s, ev, i + 1, now), initialChatState);
}
function replay(events: any[], now = NOW): ChatState {
  return events.reduce((s, ev, i) => (ev.type === 'stream_event' ? s : reduceEvent(s, ev, i + 1, now)), initialChatState);
}
// seq is a React key and differs by construction (live, a bubble takes the
// seq of its first delta; replayed, that of its assistant event).
function withoutSeq(items: ChatItem[]): unknown[] {
  return items.map(({ seq: _seq, ...rest }) => rest);
}
function ofKind<K extends ChatItem['kind']>(state: ChatState, kind: K): Extract<ChatItem, { kind: K }>[] {
  return state.items.filter((i) => i.kind === kind) as Extract<ChatItem, { kind: K }>[];
}
const firstText = (events: any[]) =>
  events.find((e) => e.type === 'assistant' && e.message.content[0]?.type === 'text').message.content[0].text as string;

describe('claudecode real wire: a steer while the answer streams', () => {
  const events = load('claudecode-steer-streaming.jsonl');
  const paragraph = firstText(events);
  const finalText = events.find((e) => e.type === 'result').result as string;

  it('live (with stream_events) and replay (without) give the same items', () => {
    expect(withoutSeq(live(events).items)).toEqual(withoutSeq(replay(events).items));
  });

  it('the answer is one bubble, neither split nor repeated; the steer lands after the tool row it came with', () => {
    const s = replay(events);
    expect(s.items.map((i) => i.kind)).toEqual(['user', 'tool', 'assistant', 'tool', 'user', 'assistant']);
    expect(ofKind(s, 'assistant').map((b) => b.text)).toEqual([paragraph, finalText]);
    const steer = ofKind(s, 'user')[1];
    expect(steer).toMatchObject({ text: '(Steer: mention the year 1900.)', queueId: 'q1' });
    expect(steer.queued).toBeUndefined();
    expect(s.busy).toBe(false);
  });

  it('while the steer waits it is the last item, below one pending answer', () => {
    const cut = events.findIndex((e) => e.type === 'assistant' && e.message.content[0]?.type === 'text');
    const s = live(events.slice(0, cut));
    expect(s.items[s.items.length - 1]).toMatchObject({ kind: 'user', queued: true, queueId: 'q1' });
    const bubbles = ofKind(s, 'assistant');
    expect(bubbles).toHaveLength(1);
    expect(bubbles[0].pending).toBe(true);
    expect(paragraph.startsWith(bubbles[0].text)).toBe(true);
  });
});
```
In `src/__tests__/claudecode-events.test.ts`, replace:
```ts
  it('still inserts the user echo before the pending bubble when it is the tail behind a tool row', () => {
    // Live streaming with a tool row after the pending bubble: tool rows count
    // as progress, not newer content (isTail), so the bubble is still the tail.
    const s = run([
      delta('working on it'),
      toolUse('Bash', { command: 'ls' }),
      user('the question'),
    ]);
    const kinds = s.items.map((i) => i.kind);
    expect(kinds.indexOf('user')).toBeLessThan(kinds.indexOf('assistant'));
  });
```
with:
```ts
  it('a user echo after a tool row lands after that row, not above the in-flight answer (mid-turn take)', () => {
    // Claude Code takes a message sent mid-turn at the next tool result and
    // echoes it then; it belongs after that tool row (steering design §4).
    const s = run([
      delta('working on it'),
      toolUse('Bash', { command: 'ls' }),
      user('the question'),
    ]);
    expect(s.items.map((i) => i.kind)).toEqual(['assistant', 'tool', 'user']);
  });
```

- [ ] **Step 3: Run the tests to verify they fail**

Run: `UITEST src/__tests__/claudecode-steering.test.ts src/__tests__/claudecode-steering-real-wire.test.ts src/__tests__/claudecode-events.test.ts`
Expected: FAIL. The x-optio-queued tests find no queued bubble; the real-wire test shows the paragraph split and the steer above the answer; the rewritten mid-turn test has `user` before `assistant`.

- [ ] **Step 4: Implement the shared model**

In `src/chat.ts`, replace:
```ts
  | { kind: 'user'; text: string; seq: number; local?: boolean }
```
with:
```ts
  | {
      kind: 'user';
      text: string;
      seq: number;
      local?: boolean;
      // Steering: a "Send when ready" message the agent has not taken yet.
      // Rendered muted and dashed ("Queued"), pinned at the bottom; it does
      // not count as newer content, so streaming continues above it.
      queued?: boolean;
      // The id optio gave the message (POST /send response, x-optio-queued,
      // x-optio-taken). Kept once the message is taken.
      queueId?: string;
    }
```
Replace:
```ts
  | { kind: 'activity'; text: string; seq: number }
```
with:
```ts
  // muted: a quiet one-line note (e.g. an operator interrupt, an undelivered
  // message) instead of the harness System: bubble.
  | { kind: 'activity'; text: string; seq: number; muted?: boolean }
```
Replace:
```ts
export const initialChatState: ChatState = {
```
with:
```ts
type UserItem = Extract<ChatItem, { kind: 'user' }>;

// -- Steering: queued bubbles (engine-neutral; every reducer uses these) ----

export function isQueued(item: ChatItem): item is UserItem {
  return item.kind === 'user' && item.queued === true;
}

// Queued bubbles are pinned at the bottom: new conversation content goes in
// front of the first one.
export function appendItems(items: ChatItem[], rows: ChatItem[]): ChatItem[] {
  if (rows.length === 0) return items;
  const q = items.findIndex(isQueued);
  if (q === -1) return [...items, ...rows];
  return [...items.slice(0, q), ...rows, ...items.slice(q)];
}

// The agent took the queued bubble at idx: it leaves the pinned group and
// lands at the take point (the end of the conversation content), with any
// rows that belong in front of it (an attachment row).
export function takeQueuedAt(items: ChatItem[], idx: number, before: ChatItem[] = []): ChatItem[] {
  const taken: UserItem = { ...(items[idx] as UserItem) };
  delete taken.queued;
  delete taken.local;
  const rest = [...items.slice(0, idx), ...items.slice(idx + 1)];
  return appendItems(rest, [...before, taken]);
}

// x-optio-taken: optio delivered the messages it held; take them in order.
export function takeQueuedIds(items: ChatItem[], ids: readonly string[]): ChatItem[] {
  let out = items;
  for (const id of ids) {
    const idx = out.findIndex((i) => isQueued(i) && i.queueId === id);
    if (idx !== -1) out = takeQueuedAt(out, idx);
  }
  return out;
}

// x-optio-queued: a Send when ready the agent has not taken yet. It
// supersedes the view's local echo of the same id (whichever arrives first
// holds the slot); a message already taken stays taken.
export function addQueued(items: ChatItem[], id: string, text: string, seq: number): ChatItem[] {
  const idx = items.findIndex((i) => i.kind === 'user' && i.queueId === id);
  if (idx === -1) return [...items, { kind: 'user', text, seq, queued: true, queueId: id }];
  const cur = items[idx] as UserItem;
  if (!cur.local) return items;
  const next: UserItem = { ...cur, queued: true };
  delete next.local;
  return [...items.slice(0, idx), next, ...items.slice(idx + 1)];
}

// The session ended (or a resumed run replaced it) before the agent took
// these: nothing will deliver them now. Each becomes a muted note.
export function dropUndelivered(items: ChatItem[]): ChatItem[] {
  if (!items.some(isQueued)) return items;
  const kept: ChatItem[] = [];
  const notes: ChatItem[] = [];
  for (const i of items) {
    if (isQueued(i)) notes.push({ kind: 'activity', text: `Not delivered: ${i.text}`, seq: i.seq, muted: true });
    else kept.push(i);
  }
  return [...kept, ...notes];
}

export const initialChatState: ChatState = {
```

- [ ] **Step 5: Implement the reducer placement**

In `src/claudecode/events.ts` apply these replacements, in order.

(a) Imports. Replace:
```ts
import { foldControlUpdate } from '../chat.js';
```
with:
```ts
import { addQueued, appendItems, dropUndelivered, foldControlUpdate, isQueued, takeQueuedAt, takeQueuedIds } from '../chat.js';
```
(b) Replace:
```ts
type AssistantItem = Extract<ChatItem, { kind: 'assistant' }>;
```
with:
```ts
type AssistantItem = Extract<ChatItem, { kind: 'assistant' }>;
type UserItem = Extract<ChatItem, { kind: 'user' }>;
```
(c) Replace:
```ts
// bubble is stale and must not act as an anchor anymore.
function isTail(items: ChatItem[], idx: number): boolean {
  return items.slice(idx + 1).every((i) => i.kind === 'tool');
}
```
with:
```ts
// bubble is stale and must not act as an anchor anymore. Queued bubbles
// don't count either: they are pinned below the conversation.
function isTail(items: ChatItem[], idx: number): boolean {
  return items.slice(idx + 1).every((i) => i.kind === 'tool' || isQueued(i));
}
```
(d) Replace:
```ts
  return [...items, { kind: 'assistant', text: delta, pending: true, seq, msgId: null, openPart: 0 }];
```
with:
```ts
  return appendItems(items, [{ kind: 'assistant', text: delta, pending: true, seq, msgId: null, openPart: 0 }]);
```
(e) Replace:
```ts
  return [...items, { kind: 'assistant', text, pending: true, seq, msgId: msgId ?? null }];
```
with:
```ts
  return appendItems(items, [{ kind: 'assistant', text, pending: true, seq, msgId: msgId ?? null }]);
```
(f) Replace:
```ts
    return [...items, { kind: 'assistant', text: resultText, pending: false, seq, msgId: null }];
```
with:
```ts
    return appendItems(items, [{ kind: 'assistant', text: resultText, pending: false, seq, msgId: null }]);
```
(g) In the comment above `insertBeforePending`, replace:
```ts
// the conversation's tail (tool rows after it count as progress, not newer
// content: see isTail). A stale pending
```
with:
```ts
// the conversation's last content (queued bubbles aside). A stale pending
```
and in its body replace:
```ts
  if (idx === -1 || !isTail(items, idx)) return [...items, ...rows];
```
with:
```ts
  // A message echoed after a tool row was taken mid-turn (Claude Code takes
  // a message sent mid-turn at the next tool result): it belongs after that
  // row, not above the in-flight answer.
  if (idx === -1 || !items.slice(idx + 1).every(isQueued)) return appendItems(items, rows);
```
(h) Replace:
```ts
    items = [...state.items, { kind: 'activity', text, seq }];
```
with:
```ts
    items = appendItems(state.items, [{ kind: 'activity', text, seq }]);
```
(i) Replace:
```ts
        let items = attach ? [...state.items, attach] : state.items;
        if (text !== '') items = [...items, { kind: 'activity', text, seq }];
```
with:
```ts
        let items = attach ? appendItems(state.items, [attach]) : state.items;
        if (text !== '') items = appendItems(items, [{ kind: 'activity', text, seq }]);
```
(j) Replace:
```ts
      // Wire echo of an optimistically-rendered local message (sent from this
      // widget): confirm the local bubble in place instead of inserting a
      // duplicate (the attachment row slots just before it). FIFO by text.
      const localIdx = state.items.findIndex(
        (i) => i.kind === 'user' && i.local === true && i.text === text,
      );
      if (localIdx !== -1) {
```
with:
```ts
      // Wire echo of a message already on screen: the widget's optimistic
      // local bubble, or a queued one. FIFO by text.
      const localIdx = state.items.findIndex(
        (i) => i.kind === 'user' && (i.local === true || i.queued === true) && i.text === text,
      );
      // A queued message the agent took now (after the tool row it came
      // with, or at the start of the next turn): it moves to the take point,
      // its attachment row in front.
      if (localIdx !== -1 && (state.items[localIdx] as UserItem).queued) {
        return { ...state, items: takeQueuedAt(state.items, localIdx, attach ? [attach] : []), busy: true };
      }
      // A local bubble: confirm it in place instead of inserting a duplicate
      // (the attachment row slots just before it).
      if (localIdx !== -1) {
```
(k) Replace the whole `x-optio-local-user` case:
```ts
    case 'x-optio-local-user': {
      const text = typeof ev.text === 'string' ? ev.text : '';
      if (text === '') return state;
      return {
        ...state,
        items: [...state.items, { kind: 'user', text, seq, local: true }],
        busy: true,
      };
    }
```
with:
```ts
    case 'x-optio-local-user': {
      const text = typeof ev.text === 'string' ? ev.text : '';
      if (text === '') return state;
      const id = typeof ev.id === 'string' && ev.id !== '' ? ev.id : undefined;
      // The listener's x-optio-queued (or the message's echo) got here first.
      if (id !== undefined && state.items.some((i) => i.kind === 'user' && i.queueId === id)) {
        return { ...state, busy: true };
      }
      const item: UserItem = { kind: 'user', text, seq, local: true };
      if (id !== undefined) item.queueId = id;
      // Queued (the /send response said so), or sent behind queued messages:
      // it waits, pinned at the bottom with them.
      if (ev.queued === true || state.items.some(isQueued)) item.queued = true;
      return { ...state, items: [...state.items, item], busy: true };
    }

    // Synthetic, listener-emitted (steering): a Send when ready that arrived
    // while the agent works. Upload notice lines are split off as in a user
    // echo, so the bubble shows (and the echo matches) the prompt text.
    case 'x-optio-queued': {
      const id = typeof ev.id === 'string' ? ev.id : '';
      const { text } = parseUploadNotice(typeof ev.text === 'string' ? ev.text : '');
      if (id === '' || text === '') return state;
      const items = addQueued(state.items, id, text, seq);
      return items === state.items ? state : { ...state, items };
    }

    // Synthetic, listener-emitted (steering): optio delivered the messages it
    // held, as one prompt. Claude Code holds its own queue, so its listener
    // never emits this; it is here for completeness of the shared contract.
    case 'x-optio-taken': {
      const ids = Array.isArray(ev.ids) ? ev.ids.filter((x: unknown): x is string => typeof x === 'string') : [];
      const items = takeQueuedIds(state.items, ids);
      return items === state.items ? state : { ...state, items, busy: true };
    }
```
(l) Replace:
```ts
      return { ...state, items: [...state.items, { kind: 'error', text, seq }] };
```
with:
```ts
      return { ...state, items: appendItems(state.items, [{ kind: 'error', text, seq }]) };
```
(m) Replace:
```ts
          items = [...items, toolRow(block, seq, at)];
```
with:
```ts
          items = appendItems(items, [toolRow(block, seq, at)]);
```
(n) Replace:
```ts
        return { ...state, items: [...items, { kind: 'error', text: msg, seq }], busy: false };
```
with:
```ts
        return { ...state, items: appendItems(items, [{ kind: 'error', text: msg, seq }]), busy: false };
```
(o) Replace:
```ts
      // busy stays true — the agent is parked on the gate.
      return { ...state, items: [...state.items, item] };
```
with:
```ts
      // busy stays true — the agent is parked on the gate.
      return { ...state, items: appendItems(state.items, [item]) };
```
(p) Replace:
```ts
      const items = freezeRunning(state.items, lastWireTime(state, now), 'session');
      return { ...state, items: [...items, item], busy: false, closed: true };
```
with:
```ts
      const items = dropUndelivered(freezeRunning(state.items, lastWireTime(state, now), 'session'));
      return { ...state, items: [...items, item], busy: false, closed: true };
```
(q) Replace:
```ts
      const items = freezeRunning(state.items, lastWireTime(state, now), 'session');
      return { ...state, items, busy: false };
```
with:
```ts
      // Messages the old process held died with it.
      const items = dropUndelivered(freezeRunning(state.items, lastWireTime(state, now), 'session'));
      return { ...state, items, busy: false };
```

- [ ] **Step 6: Run the tests to verify they pass**

Run: `UITEST src/__tests__/claudecode-steering.test.ts src/__tests__/claudecode-steering-real-wire.test.ts src/__tests__/claudecode-events.test.ts src/__tests__/claudecode-real-wire.test.ts src/__tests__/claudecode-widget.test.tsx`
Expected: PASS, all tests.
Run: `UITSC`
Expected: same output as `/tmp/steer-baseline-tsc.txt`.

- [ ] **Step 7: Commit**

```bash
ssh excavator 'cd ~/deai/optio-steering && git add packages/optio-conversation-ui/src/chat.ts packages/optio-conversation-ui/src/claudecode/events.ts packages/optio-conversation-ui/src/__tests__/claudecode-steering.test.ts packages/optio-conversation-ui/src/__tests__/claudecode-steering-real-wire.test.ts packages/optio-conversation-ui/src/__tests__/fixtures/claudecode-steer-streaming.jsonl packages/optio-conversation-ui/src/__tests__/claudecode-events.test.ts && git commit -m "feat(optio-conversation-ui): queued bubbles and mid-turn placement (claudecode)

Queued bubbles (x-optio-queued, the local echo with its /send id) stay
pinned at the bottom, so a streaming answer no longer splits or repeats;
the echo or x-optio-taken moves them to the take point. A mid-turn echo
lands after the tool row it came with. Fixture: real CLI 2.1.270 steer
while streaming, live == replay." && git log --oneline -1'
```

---
### Task 6: claudecode reducer, operator interrupts

**Files:**
- Modify: `packages/optio-conversation-ui/src/chat.ts`
- Modify: `packages/optio-conversation-ui/src/claudecode/events.ts`
- Modify: `packages/optio-conversation-ui/src/__tests__/claudecode-steering.test.ts` (append)
- Modify: `packages/optio-conversation-ui/src/__tests__/claudecode-steering-real-wire.test.ts` (append)
- Create: `packages/optio-conversation-ui/src/__tests__/fixtures/claudecode-interrupt-streaming.jsonl`, `claudecode-interrupt-tool.jsonl`, `claudecode-steer-then-interrupt.jsonl`

**Interfaces:**
- Consumes: Task 5's `appendItems`, `isQueued`, `takeQueuedAt`; the listener's `{type: 'x-optio-interrupt', by: 'user'}` (Task 4).
- Produces (`chat.ts`):
  - assistant item gains `interrupted?: boolean`.
  - `ChatState` gains reducer-private `interrupt?: { rowSeq: number }`.
  - `export const INTERRUPTED_BY_YOU = '⏹ Interrupted by you'`.
  - Task 7 renders `interrupted` (jagged edge) and `muted` activity rows.

Rules, all live == replay:
- On `x-optio-interrupt` while busy:
  - The in-flight answer (nothing but queued bubbles after it) stops pending and gets `interrupted: true`, keeping its text. A pending bubble with tool rows after it was already complete; it only stops pending.
  - Running non-background tool rows become `stopped`.
  - One muted `⏹ Interrupted by you` activity row is added, in front of queued bubbles.
  - The flag `interrupt: {rowSeq}` is set.
- While the flag is set:
  - Stream deltas are dropped. The interrupted message's final `assistant` event completes the interrupted bubble right before the row, or creates it on replay.
  - An `is_error` tool_result marks its row `stopped` and is not stored as the result.
  - The user texts `[Request interrupted by user]` and `[Request interrupted by user for tool use]` are swallowed.
  - A `result` clears the flag. If it is `error_during_execution` with `terminal_reason` `aborted_streaming` or `aborted_tools`, it adds no error item.
- `x-optio-closed`, `x-optio-resumed` and `session_state_changed idle` also clear the flag.
- A foreground call the CLI ran as a task (`task_started` with `is_backgrounded: false`) keeps its task id. The `task_notification status=stopped` the interrupt produces keeps it a foreground row, with no result.
- Without `x-optio-interrupt` (an interrupt optio did not send), today's rendering stays: the marker text bubble plus an error item.

- [ ] **Step 1: Build the three fixtures (on superego) and copy them**

Uses `/tmp/steer-fixtures/trim.py` from Task 5 Step 1 (recreate it from there if it is missing).
```bash
cd /tmp/steer-fixtures && R=/tmp/steer-interrupt-test && \
python3 trim.py $R/s1-stream-interrupt.jsonl claudecode-interrupt-streaming.jsonl && \
python3 trim.py $R/s2-tool-interrupt.jsonl claudecode-interrupt-tool.jsonl && \
python3 trim.py $R/s3-steer-then-interrupt.jsonl claudecode-steer-then-interrupt.jsonl && \
wc -l claudecode-interrupt-streaming.jsonl claudecode-interrupt-tool.jsonl claudecode-steer-then-interrupt.jsonl && \
grep -c csillag claudecode-interrupt-streaming.jsonl claudecode-interrupt-tool.jsonl claudecode-steer-then-interrupt.jsonl; \
grep -n x-optio claudecode-interrupt-streaming.jsonl claudecode-interrupt-tool.jsonl claudecode-steer-then-interrupt.jsonl
scp claudecode-interrupt-streaming.jsonl claudecode-interrupt-tool.jsonl claudecode-steer-then-interrupt.jsonl excavator:deai/optio-steering/packages/optio-conversation-ui/src/__tests__/fixtures/
```
Expected: 59, 46 and 124 lines; 0 `csillag` matches in each; synthetic lines:
- s1: one `x-optio-interrupt`, amid the answer's text deltas.
- s2: one `x-optio-interrupt`, after the Bash tool_use.
- s3: `x-optio-queued` `q1` `(Steer: also tell me today's date.)`, then `x-optio-interrupt`.

Shapes, non-`stream_event` lines only:
- **s1:** prompt; thinking-only assistant; `x-optio-interrupt`; `control_response`; assistant text (the partial answer, ending `the inv`, carrying `"aborted":true`); user `[Request interrupted by user]`; `result error_during_execution` / `aborted_streaming`; idle. Then a normal `Say OK.` turn.
- **s2:** prompt; assistant tool_use Bash (sleep 25, timestamp 00:12:57.307Z); `x-optio-interrupt`; `control_response`; `tool_result is_error` (the "doesn't want to proceed" rejection, 00:13:00.402Z); user `[Request interrupted by user for tool use]`; `result` / `aborted_tools`; idle. Then `Say OK.`.
- **s3:**
  - The prompt, then assistant narration text, tool_use `cat …` and its result, tool_use Read and its result, tool_use Bash (sleep 25).
  - `x-optio-queued`; `task_started` (`is_backgrounded:false`, task `byfqw3pc8`); `x-optio-interrupt`; `control_response`; `task_notification status=stopped`; the tool_result rejection (00:13:23.551Z); user `[… for tool use]`; `result` / `aborted_tools`.
  - Then `init`, the steer echo, the answer `Today's date: …`, `result` success, idle.

- [ ] **Step 2: Write the failing tests**

Append to `src/__tests__/claudecode-steering.test.ts`:
```ts
const interrupt = { type: 'x-optio-interrupt', by: 'user' };
const aborted = (reason = 'aborted_streaming') => ({ type: 'result', subtype: 'error_during_execution', is_error: true, terminal_reason: reason });

describe('claudecode steering: operator interrupts', () => {
  it('while idle adds nothing', () => {
    expect(run([interrupt])).toEqual(initialChatState);
  });

  it('cuts off the streaming answer, adds one muted row and swallows the CLI artefacts', () => {
    const s = run([
      user('q'), delta('Light'), interrupt, delta('house'),
      assistantText('Lighthouse', 'm1'), user('[Request interrupted by user]'), aborted(),
    ]);
    expect(kinds(s)).toEqual(['user', 'assistant', 'activity']);
    expect(s.items[1]).toMatchObject({ kind: 'assistant', text: 'Lighthouse', pending: false, interrupted: true, msgId: 'm1' });
    expect('openPart' in s.items[1]).toBe(false);
    expect(s.items[2]).toMatchObject({ kind: 'activity', text: '⏹ Interrupted by you', muted: true });
    expect(s.busy).toBe(false);
    expect(s.interrupt).toBeUndefined();
  });

  it('a second interrupt before the turn ends adds no second row', () => {
    const s = run([user('q'), delta('a'), interrupt, interrupt]);
    expect(s.items.filter((i) => i.kind === 'activity')).toHaveLength(1);
  });

  it('queued bubbles stay pinned below the interrupt row', () => {
    const s = run([user('q'), delta('ans'), queued('q1', 'later'), interrupt]);
    expect(kinds(s)).toEqual(['user', 'assistant', 'activity', 'queued']);
    expect(s.items[1]).toMatchObject({ interrupted: true });
  });

  it('a running tool stops, and the CLI rejection is not stored as its result', () => {
    const s = run([
      user('q'), toolCall('t1', 'Bash', { command: 'sleep 25' }), interrupt,
      toolResult('t1', "The user doesn't want to proceed with this tool use.", true),
      user('[Request interrupted by user for tool use]'), aborted('aborted_tools'),
    ]);
    const tool = s.items.find((i) => i.kind === 'tool') as Extract<ChatItem, { kind: 'tool' }>;
    expect(tool.status).toBe('stopped');
    expect(tool.result).toBeUndefined();
    expect(s.items.some((i) => i.kind === 'error')).toBe(false);
    expect(users(s).map((u) => u.text)).toEqual(['q']);
  });

  it('the flag ends at the result, so the next turn streams normally', () => {
    const s = run([user('q'), delta('a'), interrupt, aborted(), user('next'), delta('b')]);
    expect(s.interrupt).toBeUndefined();
    expect(s.items[s.items.length - 1]).toMatchObject({ kind: 'assistant', text: 'b', pending: true });
  });

  it('an error result other than an abort still shows after an interrupt', () => {
    const s = run([user('q'), interrupt, { type: 'result', subtype: 'error_during_execution', is_error: true, terminal_reason: 'model_error', result: 'boom' }]);
    expect(s.items.some((i) => i.kind === 'error')).toBe(true);
  });

  it('without x-optio-interrupt the CLI artefacts still show (an interrupt optio did not send)', () => {
    const s = run([user('q'), delta('a'), assistantText('a', 'm1'), user('[Request interrupted by user]'), aborted()]);
    expect(users(s).map((u) => u.text)).toContain('[Request interrupted by user]');
    expect(s.items.some((i) => i.kind === 'error')).toBe(true);
  });

  it('a foreground task the CLI reports stopped stays a foreground row', () => {
    const s = run([
      toolCall('t1', 'Bash', { command: 'sleep 25' }),
      { type: 'system', subtype: 'task_started', task_id: 'k1', tool_use_id: 't1', is_backgrounded: false, task_type: 'local_bash' },
      { type: 'system', subtype: 'task_notification', task_id: 'k1', tool_use_id: 't1', status: 'stopped', summary: 'sleep 25' },
    ]);
    const tool = s.items.find((i) => i.kind === 'tool') as Extract<ChatItem, { kind: 'tool' }>;
    expect(tool).toMatchObject({ taskId: 'k1', status: 'stopped' });
    expect(tool.background).toBeUndefined();
    expect(tool.result).toBeUndefined();
  });

  it('session close during an interrupt clears the flag', () => {
    const s = run([user('q'), delta('a'), interrupt, { type: 'x-optio-closed', reason: 'x' }]);
    expect(s.interrupt).toBeUndefined();
    expect('openPart' in s.items[1]).toBe(false);
  });
});
```
In `src/__tests__/claudecode-steering-real-wire.test.ts`, replace:
```ts
//  - claudecode-steer-streaming.jsonl (s4): a steer sent while the answer
//    streams; Claude takes it after the next tool result, same turn.
```
with:
```ts
//  - claudecode-steer-streaming.jsonl (s4): a steer sent while the answer
//    streams; Claude takes it after the next tool result, same turn.
//  - claudecode-interrupt-streaming.jsonl (s1): an interrupt while the answer
//    streams, then a normal turn.
//  - claudecode-interrupt-tool.jsonl (s2): an interrupt during a foreground
//    Bash call, then a normal turn.
//  - claudecode-steer-then-interrupt.jsonl (s3): a steer while a long Bash
//    call runs, then an interrupt; Claude runs the steer as the next turn.
```
and append:
```ts
describe('claudecode real wire: an interrupt while the answer streams', () => {
  const events = load('claudecode-interrupt-streaming.jsonl');
  const partial = firstText(events);

  it('live (with stream_events) and replay (without) give the same items', () => {
    expect(withoutSeq(live(events).items)).toEqual(withoutSeq(replay(events).items));
  });

  it('the cut-off answer keeps its text with the jagged edge; one muted row; no artefacts', () => {
    const s = replay(events);
    expect(s.items.map((i) => i.kind)).toEqual(['user', 'assistant', 'activity', 'user', 'assistant']);
    const [cut, ok] = ofKind(s, 'assistant');
    expect(cut).toMatchObject({ text: partial, pending: false, interrupted: true });
    expect(ok).toMatchObject({ text: 'OK.', pending: false });
    expect(ok.interrupted).toBeUndefined();
    expect(ofKind(s, 'activity')).toEqual([expect.objectContaining({ text: '⏹ Interrupted by you', muted: true })]);
    expect(ofKind(s, 'error')).toEqual([]);
    expect(ofKind(s, 'user').map((u) => u.text)).not.toContain('[Request interrupted by user]');
    expect(s.busy).toBe(false);
  });

  it('without x-optio-interrupt (an interrupt optio did not send) the artefacts still show as an error', () => {
    const s = replay(events.filter((e) => e.type !== 'x-optio-interrupt'));
    expect(ofKind(s, 'error')).toHaveLength(1);
    expect(ofKind(s, 'user').map((u) => u.text)).toContain('[Request interrupted by user]');
  });
});

describe('claudecode real wire: an interrupt during a tool', () => {
  const events = load('claudecode-interrupt-tool.jsonl');

  it('live (with stream_events) and replay (without) give the same items', () => {
    expect(withoutSeq(live(events).items)).toEqual(withoutSeq(replay(events).items));
  });

  it('the running call shows stopped, not failed; one muted row; no error', () => {
    const s = replay(events);
    expect(s.items.map((i) => i.kind)).toEqual(['user', 'tool', 'activity', 'user', 'assistant']);
    const [call] = ofKind(s, 'tool');
    expect(call).toMatchObject({
      name: 'Bash',
      status: 'stopped',
      startedAt: Date.parse('2026-09-13T00:12:57.307Z'),
      endedAt: Date.parse('2026-09-13T00:13:00.402Z'),
    });
    expect(call.result).toBeUndefined();
    expect(ofKind(s, 'error')).toEqual([]);
    expect(ofKind(s, 'user').map((u) => u.text)).not.toContain('[Request interrupted by user for tool use]');
  });
});

describe('claudecode real wire: a steer, then an interrupt during a tool', () => {
  const events = load('claudecode-steer-then-interrupt.jsonl');
  const finalText = events.find((e) => e.type === 'result' && e.subtype === 'success').result as string;

  it('live (with stream_events) and replay (without) give the same items', () => {
    expect(withoutSeq(live(events).items)).toEqual(withoutSeq(replay(events).items));
  });

  it('the long call stops as a foreground row, and the steer opens the next turn after the interrupt row', () => {
    const s = replay(events);
    expect(s.items.map((i) => i.kind)).toEqual(['user', 'assistant', 'tool', 'tool', 'tool', 'activity', 'user', 'assistant']);
    const [narration, answer] = ofKind(s, 'assistant');
    // The narration was complete before the tools ran: not cut off.
    expect(narration.pending).toBe(false);
    expect(narration.interrupted).toBeUndefined();
    expect(answer).toMatchObject({ text: finalText, pending: false });
    const sleep = ofKind(s, 'tool')[2];
    expect(sleep).toMatchObject({ name: 'Bash', status: 'stopped', taskId: 'byfqw3pc8', endedAt: Date.parse('2026-09-13T00:13:23.551Z') });
    expect(sleep.background).toBeUndefined();
    expect(sleep.result).toBeUndefined();
    const steer = ofKind(s, 'user')[1];
    expect(steer).toMatchObject({ text: "(Steer: also tell me today's date.)", queueId: 'q1' });
    expect(steer.queued).toBeUndefined();
    expect(ofKind(s, 'error')).toEqual([]);
    expect(s.busy).toBe(false);
  });

  it('until Claude takes it, the steer waits below the interrupt row', () => {
    const cut = events.findIndex((e) => e.type === 'result');
    const s = replay(events.slice(0, cut + 1));
    const kinds = s.items.map((i) => (i.kind === 'user' && i.queued ? 'queued' : i.kind));
    expect(kinds.slice(-2)).toEqual(['activity', 'queued']);
  });
});
```

- [ ] **Step 3: Run the tests to verify they fail**

Run: `UITEST src/__tests__/claudecode-steering.test.ts src/__tests__/claudecode-steering-real-wire.test.ts`
Expected: the new interrupt tests FAIL. There is no activity row and no `interrupted`; `[Request interrupted…]` shows as a user bubble; an error item appears; the s3 Bash row is marked `background`. The Task 5 tests still PASS.

- [ ] **Step 4: Implement the model additions**

In `src/chat.ts`, replace:
```ts
      openPart?: number;
```
with:
```ts
      openPart?: number;
      // The operator interrupted this answer: it keeps its text and renders
      // with a jagged bottom edge.
      interrupted?: boolean;
```
Replace:
```ts
  lastEventAt?: number;
```
with:
```ts
  lastEventAt?: number;
  // Reducer-private (claudecode): set by x-optio-interrupt until the
  // interrupted turn's result; rowSeq is the seq of its "Interrupted by you"
  // row. While set, the CLI's own cancel artefacts are swallowed and the
  // interrupted message's final text lands in front of that row.
  interrupt?: { rowSeq: number };
```
Replace:
```ts
// -- Steering: queued bubbles (engine-neutral; every reducer uses these) ----
```
with:
```ts
// The one row an operator interrupt adds (every engine's reducer uses it).
export const INTERRUPTED_BY_YOU = '⏹ Interrupted by you';

// -- Steering: queued bubbles (engine-neutral; every reducer uses these) ----
```

- [ ] **Step 5: Implement the reducer**

In `src/claudecode/events.ts` apply, in order:

(a) Replace:
```ts
import { addQueued, appendItems, dropUndelivered, foldControlUpdate, isQueued, takeQueuedAt, takeQueuedIds } from '../chat.js';
```
with:
```ts
import {
  INTERRUPTED_BY_YOU, addQueued, appendItems, dropUndelivered, foldControlUpdate, isQueued, takeQueuedAt, takeQueuedIds,
} from '../chat.js';
```
(b) Replace:
```ts
const PART_SEPARATOR = '\n\n';
```
with:
```ts
const PART_SEPARATOR = '\n\n';

// Claude Code's own cancel artefacts (CLI 2.1.270): the user text it adds to
// an interrupted turn, and the terminal reasons of that turn's error result.
const INTERRUPT_ECHO = /^\[Request interrupted by user( for tool use)?\]$/;
const ABORTED = new Set<string>(['aborted_streaming', 'aborted_tools']);
```
(c) Replace:
```ts
// Apply tool_result blocks to their rows (matched by tool_use_id). A background
// row ignores its immediate result: the task notification finishes it.
function applyToolResults(items: ChatItem[], content: unknown, at: number): ChatItem[] {
  if (!Array.isArray(content)) return items;
  let out = items;
  for (const b of content) {
    if (b?.type !== 'tool_result' || typeof b.tool_use_id !== 'string') continue;
    const idx = out.findIndex((i) => i.kind === 'tool' && i.callId === b.tool_use_id);
    if (idx === -1) continue;
    const row = out[idx] as ToolItem;
    if (row.background) continue;
```
with:
```ts
// Apply tool_result blocks to their rows (matched by tool_use_id). A background
// row ignores its immediate result: the task notification finishes it. While
// an operator interrupt is in flight, an error result is the CLI rejecting the
// call it cancelled: the row stops, and the boilerplate is not its result.
function applyToolResults(items: ChatItem[], content: unknown, at: number, interrupted = false): ChatItem[] {
  if (!Array.isArray(content)) return items;
  let out = items;
  for (const b of content) {
    if (b?.type !== 'tool_result' || typeof b.tool_use_id !== 'string') continue;
    const idx = out.findIndex((i) => i.kind === 'tool' && i.callId === b.tool_use_id);
    if (idx === -1) continue;
    const row = out[idx] as ToolItem;
    if (row.background) continue;
    if (interrupted && b.is_error) {
      out = replaceAt(out, idx, { ...row, status: 'stopped', endedAt: at });
      continue;
    }
```
(d) Replace:
```ts
function freezeRunning(items: ChatItem[], at: number, scope: 'turn' | 'session'): ChatItem[] {
  let changed = false;
  const out = items.map((i) => {
    if (i.kind !== 'tool' || (i.status !== undefined && i.status !== 'running')) return i;
    if (scope === 'turn') {
      if (i.background || i.startedAt === undefined || i.endedAt !== undefined) return i;
      changed = true;
      return { ...i, endedAt: at };
    }
```
with:
```ts
// 'interrupt' (an operator interrupt): every running row except background
// ones (their task outlives the turn) becomes 'stopped'.
function freezeRunning(items: ChatItem[], at: number, scope: 'turn' | 'interrupt' | 'session'): ChatItem[] {
  let changed = false;
  const out = items.map((i) => {
    if (i.kind !== 'tool' || (i.status !== undefined && i.status !== 'running')) return i;
    if (scope === 'turn') {
      if (i.background || i.startedAt === undefined || i.endedAt !== undefined) return i;
      changed = true;
      return { ...i, endedAt: at };
    }
    if (scope === 'interrupt' && i.background) return i;
```
(e) Replace:
```ts
  if (idx !== -1) {
    const row = state.items[idx] as ToolItem;
    items = replaceAt(state.items, idx, {
      ...row,
      background: true,
```
with:
```ts
  const found = idx !== -1 ? (state.items[idx] as ToolItem) : null;
  if (found && !found.background && found.taskId === n.taskId) {
    // A foreground call the CLI ran as a task (task_started with
    // is_backgrounded false), reported when an interrupt stops it: it stays a
    // foreground row, and the summary (its command) is not its result.
    items = replaceAt(state.items, idx, { ...found, status: toolStatus, endedAt: found.endedAt ?? at });
  } else if (idx !== -1) {
    const row = state.items[idx] as ToolItem;
    items = replaceAt(state.items, idx, {
      ...row,
      background: true,
```
(f) Replace:
```ts
export function reduceEvent(state: ChatState, ev: any, seq: number, now: number = Date.now()): ChatState {
```
with:
```ts
// -- Operator interrupts (x-optio-interrupt) ---------------------------------

// The index of the "Interrupted by you" row an in-flight interrupt added.
function interruptRowIndex(items: ChatItem[], rowSeq: number): number {
  return items.findIndex((i) => i.kind === 'activity' && i.seq === rowSeq);
}

// Rows the interrupted turn still produces go in front of its row.
function insertBeforeRow(items: ChatItem[], rowSeq: number, rows: ChatItem[]): ChatItem[] {
  const r = interruptRowIndex(items, rowSeq);
  if (r === -1) return appendItems(items, rows);
  return [...items.slice(0, r), ...rows, ...items.slice(r)];
}

// The interrupted message's final text (the CLI sends it after the
// interrupt): it completes the interrupted bubble right before the row,
// replacing the part its deltas streamed (live), or becomes that bubble
// (replay holds no deltas).
function applyInterruptedText(items: ChatItem[], rowSeq: number, seq: number, text: string, msgId?: string): ChatItem[] {
  const r = interruptRowIndex(items, rowSeq);
  const prev = r > 0 ? items[r - 1] : undefined;
  if (prev?.kind === 'assistant' && prev.interrupted && (prev.msgId === null || msgId == null || prev.msgId === msgId)) {
    const base =
      prev.openPart !== undefined
        ? prev.text.slice(0, prev.openPart)
        : prev.text + (prev.text === '' ? '' : PART_SEPARATOR);
    const next: AssistantItem = { ...prev, text: base + text, msgId: msgId ?? prev.msgId };
    delete next.openPart;
    return replaceAt(items, r - 1, next);
  }
  return insertBeforeRow(items, rowSeq, [
    { kind: 'assistant', text, pending: false, seq, msgId: msgId ?? null, interrupted: true },
  ]);
}

// The interrupted turn is over: drop the flag and the part offset the
// interrupted bubble kept for its final text.
function endInterrupt(state: ChatState): ChatState {
  if (!state.interrupt) return state;
  const items = state.items.map((i) => {
    if (i.kind !== 'assistant' || !i.interrupted || i.openPart === undefined) return i;
    const next: AssistantItem = { ...i };
    delete next.openPart;
    return next;
  });
  const next: ChatState = { ...state, items };
  delete next.interrupt;
  return next;
}

export function reduceEvent(state: ChatState, ev: any, seq: number, now: number = Date.now()): ChatState {
```
(g) Replace:
```ts
      const withResults = applyToolResults(state.items, ev.message?.content, eventTime(ev, now));
```
with:
```ts
      const withResults = applyToolResults(state.items, ev.message?.content, eventTime(ev, now), state.interrupt !== undefined);
```
(h) Replace:
```ts
      const rawText = extractText(ev.message?.content);
```
with:
```ts
      const rawText = extractText(ev.message?.content);
      // The CLI's own marker for the turn optio interrupted: its row says so.
      if (state.interrupt && INTERRUPT_ECHO.test(rawText.trim())) return state;
```
(i) Replace:
```ts
    // Synthetic, listener-emitted (steering): a Send when ready that arrived
```
with:
```ts
    // Synthetic, listener-emitted (steering): the operator interrupted
    // (Interrupt, Interrupt and send, Send now). The only source of the
    // "Interrupted by you" row. Nothing in flight, or an interrupt already in
    // flight: no-op.
    case 'x-optio-interrupt': {
      if (!state.busy || state.interrupt) return state;
      let items = state.items;
      const idx = pendingIndex(items);
      if (idx !== -1) {
        // The in-flight answer (only queued bubbles after it) is cut off: it
        // keeps its text (and its open part, which the final text event
        // replaces) and gets the jagged edge. A pending bubble with tool rows
        // after it was already complete: it just stops being pending.
        items = items.slice(idx + 1).every(isQueued)
          ? replaceAt(items, idx, { ...(items[idx] as AssistantItem), pending: false, interrupted: true })
          : finalizeAt(items, idx);
      }
      items = freezeRunning(items, lastWireTime(state, now), 'interrupt');
      items = appendItems(items, [{ kind: 'activity', text: INTERRUPTED_BY_YOU, seq, muted: true }]);
      return { ...state, items, interrupt: { rowSeq: seq } };
    }

    // Synthetic, listener-emitted (steering): a Send when ready that arrived
```
(j) Replace:
```ts
        if (text !== null) {
          // The agent is answering (or narrating) — complete this block's
          // part of the bubble.
          items = applyBlockText(items, seq, text, msgId);
        } else if (block?.type === 'tool_use') {
          // A persistent row per call; its tool_result (a later user event)
          // finishes it.
          items = appendItems(items, [toolRow(block, seq, at)]);
        }
```
with:
```ts
        if (text !== null) {
          // The agent is answering (or narrating) — complete this block's
          // part of the bubble. After an operator interrupt, the interrupted
          // message's text completes the cut-off bubble in front of its row.
          items = state.interrupt
            ? applyInterruptedText(items, state.interrupt.rowSeq, seq, text, msgId)
            : applyBlockText(items, seq, text, msgId);
        } else if (block?.type === 'tool_use') {
          // A persistent row per call; its tool_result (a later user event)
          // finishes it. A call the interrupted turn still announces is
          // stopped before it ran.
          items = state.interrupt
            ? insertBeforeRow(items, state.interrupt.rowSeq, [{ ...(toolRow(block, seq, at) as ToolItem), status: 'stopped', endedAt: at }])
            : appendItems(items, [toolRow(block, seq, at)]);
        }
```
(k) Replace:
```ts
    case 'stream_event': {
```
with:
```ts
    case 'stream_event': {
      // While an operator interrupt is in flight the interrupted message's
      // final assistant event carries its text; deltas that raced the
      // interrupt are dropped (replay never has them either).
      if (state.interrupt) return state;
```
(l) Replace:
```ts
    case 'result': {
      const resultText = typeof ev.result === 'string' ? ev.result : null;
      const items = freezeRunning(state.items, eventTime(ev, lastWireTime(state, now)), 'turn');
```
with:
```ts
    case 'result': {
      const at = eventTime(ev, lastWireTime(state, now));
      if (state.interrupt) {
        state = endInterrupt(state);
        // The turn optio interrupted ends with the CLI's abort error: that is
        // the operator's own interrupt, already shown by its row. No error
        // item; rows still running stop.
        if (ev.subtype === 'error_during_execution' && ABORTED.has(ev.terminal_reason)) {
          const items = freezeRunning(finalizePending(state.items, seq, null), at, 'interrupt');
          return { ...state, items, busy: false };
        }
      }
      const resultText = typeof ev.result === 'string' ? ev.result : null;
      const items = freezeRunning(state.items, at, 'turn');
```
(m) Replace:
```ts
    case 'x-optio-closed': {
```
with:
```ts
    case 'x-optio-closed': {
      state = endInterrupt(state);
```
(n) Replace:
```ts
    case 'x-optio-resumed': {
```
with:
```ts
    case 'x-optio-resumed': {
      state = endInterrupt(state);
```
(o) Replace:
```ts
        if (ev.state === 'idle') return state.busy ? { ...state, busy: false } : state;
```
with:
```ts
        if (ev.state === 'idle') {
          const ended = endInterrupt(state);
          return ended.busy ? { ...ended, busy: false } : ended;
        }
```
(p) Replace:
```ts
      if (ev.subtype === 'task_updated' && typeof ev.task_id === 'string') {
```
with:
```ts
      if (ev.subtype === 'task_started' && ev.is_backgrounded === false
        && typeof ev.tool_use_id === 'string' && typeof ev.task_id === 'string') {
        // A foreground call run as a task: remember its task id, so its
        // notification (sent when an interrupt stops it) keeps it foreground.
        const idx = state.items.findIndex((i) => i.kind === 'tool' && i.callId === ev.tool_use_id);
        if (idx === -1) return state;
        return { ...state, items: replaceAt(state.items, idx, { ...(state.items[idx] as ToolItem), taskId: ev.task_id }) };
      }
      if (ev.subtype === 'task_updated' && typeof ev.task_id === 'string') {
```

- [ ] **Step 6: Run the tests to verify they pass**

Run: `UITEST src/__tests__/claudecode-steering.test.ts src/__tests__/claudecode-steering-real-wire.test.ts src/__tests__/claudecode-events.test.ts src/__tests__/claudecode-real-wire.test.ts src/__tests__/claudecode-widget.test.tsx`
Expected: PASS, all tests.
Run: `UITSC`
Expected: same output as `/tmp/steer-baseline-tsc.txt`.

- [ ] **Step 7: Commit**

```bash
ssh excavator 'cd ~/deai/optio-steering && git add packages/optio-conversation-ui/src/chat.ts packages/optio-conversation-ui/src/claudecode/events.ts packages/optio-conversation-ui/src/__tests__/claudecode-steering.test.ts packages/optio-conversation-ui/src/__tests__/claudecode-steering-real-wire.test.ts packages/optio-conversation-ui/src/__tests__/fixtures/claudecode-interrupt-streaming.jsonl packages/optio-conversation-ui/src/__tests__/fixtures/claudecode-interrupt-tool.jsonl packages/optio-conversation-ui/src/__tests__/fixtures/claudecode-steer-then-interrupt.jsonl && git commit -m "feat(optio-conversation-ui): render operator interrupts (claudecode)

x-optio-interrupt cuts off the in-flight answer (kept, marked interrupted),
stops running calls and adds one muted Interrupted by you row; the CLI own
[Request interrupted] text, its rejection tool_result and its aborted
error result are swallowed for that turn. A foreground task stopped by the
interrupt stays a foreground row. Fixtures: real CLI 2.1.270 interrupts,
live == replay." && git log --oneline -1'
```

---
### Task 7: ConversationView — queued bubble, jagged edge, busy input bar

**Files:**
- Modify: `packages/optio-conversation-ui/src/ConversationView.tsx`
- Test: `packages/optio-conversation-ui/src/__tests__/conversation-view.test.tsx` (append)

**Interfaces:**
- Consumes:
  - Task 1's `CombinedActionButton` with `keepOriginalDefault` and the `ActionStatus` type, both from `'vultus-antd'`. The unitas checkout must be on `keep-original-default` (or have it merged).
  - Tasks 5 and 6: `ChatItem` fields `queued`, `interrupted`, `muted`.
- Produces: `ConversationViewProps.onSteer?: (text: string, attachments: Attachment[]) => Promise<boolean>`, which returns ok. The view calls `onSteer(text, attachments)` for Interrupt and send, and `onSteer('', [])` for a queued bubble's Send now.
- Test ids:
  - `queued-bubble`, `queued-send-now`, `answer-interrupted`, `activity-muted`
  - `conversation-send-combined` (the busy button's wrapper). Its main half is `[data-action-id="send-when-ready"]`, and its menu item is `Interrupt and send` (`data-action-id="interrupt-and-send"` when active).
  - `conversation-send` stays the single Send button.

How other engines' views degrade: they pass no `onSteer`, so the busy bar keeps today's single Send (their `/send` does whatever it does today) plus the red Interrupt, and Enter sends. Their reducers never set `queued` / `interrupted` / `muted`, so nothing else changes. If a queued bubble ever appears without `onSteer`, it shows no Send now link.

- [ ] **Step 1: Write the failing tests**

Append to `src/__tests__/conversation-view.test.tsx`:
```tsx
describe('ConversationView steering', () => {
  const queuedItem: ChatItem = { kind: 'user', text: 'do this next', seq: 1, queued: true, queueId: 'q1' };
  const busyState = (items: ChatItem[] = []) => makeState(items, { busy: true });

  it('renders a queued bubble muted and dashed, with its caption', () => {
    renderView(makeProps({ state: busyState([queuedItem]), busy: true }));
    const bubble = screen.getByTestId('queued-bubble');
    expect(bubble.textContent).toContain('do this next');
    expect(bubble.textContent).toContain('Queued — the agent reads it when ready');
    expect(bubble.getAttribute('style')).toContain('dashed');
    expect(bubble.style.opacity).toBe('0.6');
  });

  it('Send now on a queued bubble calls onSteer with no new text', () => {
    const onSteer = vi.fn(async () => true);
    renderView(makeProps({ state: busyState([queuedItem]), busy: true, onSteer }));
    fireEvent.click(screen.getByTestId('queued-send-now'));
    expect(onSteer).toHaveBeenCalledWith('', []);
  });

  it('without onSteer, or once closed, a queued bubble has no Send now link', () => {
    const r = renderView(makeProps({ state: busyState([queuedItem]), busy: true }));
    expect(screen.queryByTestId('queued-send-now')).toBeNull();
    rerenderView(r, makeProps({ state: makeState([queuedItem]), closed: true, onSteer: vi.fn(async () => true) }));
    expect(screen.queryByTestId('queued-send-now')).toBeNull();
  });

  it('an interrupted answer gets the jagged-edge class; a normal one does not', () => {
    renderView(makeProps({ state: makeState([
      { kind: 'assistant', text: 'cut off', pending: false, seq: 1, msgId: 'm1', interrupted: true },
      { kind: 'assistant', text: 'whole', pending: false, seq: 2, msgId: 'm2' },
    ]) }));
    const cut = screen.getAllByTestId('answer-interrupted');
    expect(cut).toHaveLength(1);
    expect(cut[0].className).toContain('optio-cc-interrupted');
    expect(cut[0].textContent).toContain('cut off');
    expect(document.getElementById('optio-cc-interrupted-style')).not.toBeNull();
  });

  it('a muted activity row renders as a plain muted line', () => {
    renderView(makeProps({ state: makeState([{ kind: 'activity', text: '⏹ Interrupted by you', seq: 1, muted: true }]) }));
    expect(screen.getByTestId('activity-muted').textContent).toBe('⏹ Interrupted by you');
  });

  it('idle: a single Send, even with onSteer', () => {
    renderView(makeProps({ onSteer: vi.fn(async () => true) }));
    expect(screen.getByTestId('conversation-send')).toBeTruthy();
    expect(screen.queryByTestId('conversation-send-combined')).toBeNull();
  });

  it('busy without onSteer: the single Send stays (engines without steering)', () => {
    renderView(makeProps({ busy: true, state: busyState() }));
    expect(screen.getByTestId('conversation-send')).toBeTruthy();
    expect(screen.queryByTestId('conversation-send-combined')).toBeNull();
    expect(screen.getByTestId('conversation-interrupt')).toBeTruthy();
  });

  it('busy with onSteer: [Send when ready | Interrupt and send] plus the red Interrupt; the main half stays Send when ready', async () => {
    const onSend = vi.fn(async () => true);
    const onSteer = vi.fn(async () => true);
    const { container } = renderView(makeProps({ busy: true, state: busyState(), onSend, onSteer }));
    expect(screen.queryByTestId('conversation-send')).toBeNull();
    expect(screen.getByTestId('conversation-interrupt')).toBeTruthy();
    const box = screen.getByTestId('conversation-input-box') as HTMLTextAreaElement;
    expect(box.placeholder).toContain('send when ready');
    expect(box.placeholder).toContain('interrupt and send');

    fireEvent.change(box, { target: { value: 'first' } });
    const main = () => container.querySelector('[data-action-id="send-when-ready"]') as HTMLElement;
    expect(main().textContent).toContain('Send when ready');
    fireEvent.click(main());
    await waitFor(() => expect(onSend).toHaveBeenCalledWith('first', []));
    await waitFor(() => expect(box.value).toBe(''));

    fireEvent.change(box, { target: { value: 'second' } });
    const combined = screen.getByTestId('conversation-send-combined');
    fireEvent.click(combined.querySelector('.ant-dropdown-trigger') as HTMLElement);
    fireEvent.click(await screen.findByRole('menuitem', { name: 'Interrupt and send' }));
    await waitFor(() => expect(onSteer).toHaveBeenCalledWith('second', []));
    // keepOriginalDefault: the main half is back on Send when ready.
    await waitFor(() => expect(main().textContent).toContain('Send when ready'));
  });

  it('Enter sends when ready; Cmd/Ctrl-Enter interrupts and sends while busy', async () => {
    const onSend = vi.fn(async () => true);
    const onSteer = vi.fn(async () => true);
    renderView(makeProps({ busy: true, state: busyState(), onSend, onSteer }));
    const box = screen.getByTestId('conversation-input-box') as HTMLTextAreaElement;
    fireEvent.change(box, { target: { value: 'a' } });
    fireEvent.keyDown(box, { key: 'Enter' });
    await waitFor(() => expect(onSend).toHaveBeenCalledWith('a', []));
    await waitFor(() => expect(box.value).toBe(''));
    fireEvent.change(box, { target: { value: 'b' } });
    fireEvent.keyDown(box, { key: 'Enter', ctrlKey: true });
    await waitFor(() => expect(onSteer).toHaveBeenCalledWith('b', []));
    await waitFor(() => expect(box.value).toBe(''));
    fireEvent.change(box, { target: { value: 'c' } });
    fireEvent.keyDown(box, { key: 'Enter', metaKey: true });
    await waitFor(() => expect(onSteer).toHaveBeenCalledWith('c', []));
    expect(onSend).toHaveBeenCalledTimes(1);
  });

  it('Cmd/Ctrl-Enter while idle is a plain send', async () => {
    const onSend = vi.fn(async () => true);
    const onSteer = vi.fn(async () => true);
    renderView(makeProps({ onSend, onSteer }));
    const box = screen.getByTestId('conversation-input-box') as HTMLTextAreaElement;
    fireEvent.change(box, { target: { value: 'x' } });
    fireEvent.keyDown(box, { key: 'Enter', ctrlKey: true });
    await waitFor(() => expect(onSend).toHaveBeenCalledWith('x', []));
    expect(onSteer).not.toHaveBeenCalled();
  });
});
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `UITEST src/__tests__/conversation-view.test.tsx`
Expected: the new tests FAIL (no `queued-bubble`, `answer-interrupted`, `activity-muted` or `conversation-send-combined`; Ctrl-Enter calls `onSend`). The existing tests PASS.

- [ ] **Step 3: Implement**

In `src/ConversationView.tsx` apply, in order:

(a) Replace:
```tsx
import type { GlobalToken } from 'antd';
```
with:
```tsx
import type { GlobalToken } from 'antd';
import { CombinedActionButton, type ActionStatus } from 'vultus-antd';
```
(b) Replace:
```tsx
  onInterrupt: () => void;
```
with:
```tsx
  onInterrupt: () => void;
  // Steering (optional). When set, a busy input bar offers "Send when ready"
  // (Enter → onSend) and "Interrupt and send" (Cmd/Ctrl-Enter → onSteer) in
  // one vultus multi-action button, and a queued bubble offers "Send now"
  // (onSteer('', [])). Absent: the bar keeps a single Send while busy (the
  // engine's /send decides what a busy send does) and queued bubbles show no
  // Send now link. Returns ok, like onSend.
  onSteer?: (text: string, attachments: Attachment[]) => Promise<boolean>;
```
(c) Replace:
```tsx
// Colors come from the antd theme (ConfigProvider algorithm), so the widget
```
with:
```tsx
// Jagged bottom edge on an answer the operator interrupted: a zigzag mask, so
// it follows the bubble's own background and border in either theme.
const INTERRUPTED_STYLE_ID = 'optio-cc-interrupted-style';
function ensureInterruptedStyle(): void {
  if (typeof document === 'undefined' || document.getElementById(INTERRUPTED_STYLE_ID)) return;
  const el = document.createElement('style');
  el.id = INTERRUPTED_STYLE_ID;
  el.textContent = `.optio-cc-interrupted {
    padding-bottom: 14px !important;
    -webkit-mask: conic-gradient(from -45deg at bottom, #0000, #000 1deg 89deg, #0000 90deg) 50% / 12px 100%;
    mask: conic-gradient(from -45deg at bottom, #0000, #000 1deg 89deg, #0000 90deg) 50% / 12px 100%;
  }`;
  document.head.appendChild(el);
}

// A vultus ActionStatus for the busy input bar's multi-action button. The
// view runs the (async) send itself, so both fire paths just start it.
function barAction(
  id: string,
  label: string,
  variant: 'primary' | 'default',
  disabled: boolean,
  run: () => void,
): ActionStatus {
  return { id, label, variant, pending: false, disabled, invisible: false, errors: [], fire: run, firePromise: async () => run() };
}

// Colors come from the antd theme (ConfigProvider algorithm), so the widget
```
(d) Replace:
```tsx
    ensureFlashStyle();
    ensureCopyStyle();
```
with:
```tsx
    ensureFlashStyle();
    ensureCopyStyle();
    ensureInterruptedStyle();
```
(e) Replace the whole `send` function:
```tsx
  async function send() {
    const body = text;
    if (!body || sending || closed) return;
    setSending(true);
    setError(null);
    const ok = await onSend(body, attachments);
    if (ok) {
      setText('');
      setAttachments([]);
    } else {
      setError('Send failed — retry.');
    }
    setSending(false);
    // Keep the keyboard on the input so the operator can keep typing after
    // Enter without a mouse click.
    inputRef.current?.focus();
  }
```
with:
```tsx
  // Steering applies only while a turn runs, and only for engines that wire it.
  const steerable = busy && !closed && props.onSteer !== undefined;

  // 'send' → onSend (Send; Send when ready while busy). 'steer' → onSteer
  // (Interrupt and send).
  async function submit(kind: 'send' | 'steer') {
    const body = text;
    if (!body || sending || closed) return;
    const deliver = kind === 'steer' ? props.onSteer : onSend;
    if (!deliver) return;
    setSending(true);
    setError(null);
    const ok = await deliver(body, attachments);
    if (ok) {
      setText('');
      setAttachments([]);
    } else {
      setError('Send failed — retry.');
    }
    setSending(false);
    // Keep the keyboard on the input so the operator can keep typing after
    // Enter without a mouse click.
    inputRef.current?.focus();
  }

  function send() {
    return submit('send');
  }
```
(f) Replace:
```tsx
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault();
      void send();
```
with:
```tsx
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault();
      // Cmd/Ctrl-Enter interrupts and sends while the agent works; Enter
      // sends (when ready, if busy), and so does Cmd/Ctrl-Enter when idle.
      void submit((e.metaKey || e.ctrlKey) && steerable ? 'steer' : 'send');
```
(g) Replace:
```tsx
      case 'user':
        return (
```
with:
```tsx
      case 'user':
        // A Send when ready the agent has not taken yet: user colours, dashed
        // and muted; the reducer keeps it pinned at the bottom.
        if (item.queued) {
          return (
            <div
              key={item.seq}
              data-testid="queued-bubble"
              style={{
                ...bubbleBase,
                alignSelf: 'flex-end',
                background: token.colorPrimaryBg,
                border: `1px dashed ${token.colorPrimaryBorder}`,
                borderRadius: '14px 14px 4px 14px',
                color: token.colorText,
                opacity: 0.6,
              }}
            >
              {item.text}
              <div style={{ fontSize: 12, color: token.colorTextSecondary, marginTop: 4, whiteSpace: 'normal' }}>
                Queued — the agent reads it when ready
                {props.onSteer && !closed ? (
                  <>
                    {' · '}
                    <a data-testid="queued-send-now" onClick={() => void props.onSteer?.('', [])}>
                      Send now
                    </a>
                  </>
                ) : null}
              </div>
            </div>
          );
        }
        return (
```
(h) Replace:
```tsx
      case 'assistant':
        return (
          <div
            key={item.seq}
            style={{
```
with:
```tsx
      case 'assistant':
        return (
          <div
            key={item.seq}
            // An answer the operator interrupted keeps its text and gets a
            // jagged bottom edge (the class is installed on mount).
            data-testid={item.interrupted ? 'answer-interrupted' : undefined}
            className={item.interrupted ? 'optio-cc-interrupted' : undefined}
            style={{
```
(i) Replace:
```tsx
      case 'activity':
        // Harness System: messages — neither the user nor the agent, so render
```
with:
```tsx
      case 'activity':
        // A muted note ("⏹ Interrupted by you", an undelivered message): one
        // quiet centred line, not a bubble.
        if (item.muted) {
          return (
            <div
              key={item.seq}
              data-testid="activity-muted"
              style={{ alignSelf: 'center', color: token.colorTextTertiary, fontSize: 12 }}
            >
              {item.text}
            </div>
          );
        }
        // Harness System: messages — neither the user nor the agent, so render
```
(j) Replace:
```tsx
            placeholder="Message agent…  (Enter to send, Shift+Enter for newline)"
```
with:
```tsx
            placeholder={
              steerable
                ? 'Message agent…  (Enter: send when ready, ⌘/Ctrl+Enter: interrupt and send, Shift+Enter: newline)'
                : 'Message agent…  (Enter to send, Shift+Enter for newline)'
            }
```
(k) Replace:
```tsx
            <Button
              size="small"
              data-testid="conversation-send"
              type="primary"
              onClick={() => void send()}
              disabled={sending || !text || closed}
            >
              Send
            </Button>
```
with:
```tsx
            {steerable ? (
              // Busy: one vultus multi-action button, [Send when ready |
              // Interrupt and send]. keepOriginalDefault keeps the main half
              // on Send when ready (so its width stays fixed) after the menu
              // action fires. The red Interrupt beside it stops and sends nothing.
              <span data-testid="conversation-send-combined">
                <CombinedActionButton
                  size="small"
                  keepOriginalDefault
                  actions={[
                    barAction('send-when-ready', 'Send when ready', 'primary', sending || !text, () => void submit('send')),
                    barAction('interrupt-and-send', 'Interrupt and send', 'default', sending || !text, () => void submit('steer')),
                  ]}
                />
              </span>
            ) : (
              <Button
                size="small"
                data-testid="conversation-send"
                type="primary"
                onClick={() => void send()}
                disabled={sending || !text || closed}
              >
                Send
              </Button>
            )}
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `UITEST src/__tests__/conversation-view.test.tsx`
Expected: PASS, all tests.
Run: `UITSC`
Expected: same output as `/tmp/steer-baseline-tsc.txt`. `ConversationView` now imports vultus-antd's main entry, which pulls unitas `src/` files into the program. If new errors appear only in `~/deai/unitas/...` files, stop and report them to the controller. Do not edit unitas to silence them.

- [ ] **Step 5: Commit**

```bash
ssh excavator 'cd ~/deai/optio-steering && git add packages/optio-conversation-ui/src/ConversationView.tsx packages/optio-conversation-ui/src/__tests__/conversation-view.test.tsx && git commit -m "feat(optio-conversation-ui): steering in ConversationView

Queued bubble (dashed, muted, Queued caption, Send now), jagged bottom edge
on interrupted answers, muted activity rows, and a busy input bar with the
vultus CombinedActionButton [Send when ready | Interrupt and send]
(keepOriginalDefault; Enter / Cmd-Ctrl-Enter) beside the red Interrupt.
New optional onSteer prop; views without it keep a single Send." && git log --oneline -1'
```

---
### Task 8: ClaudeCodeView wiring, widget tests, docs, full verification

**Files:**
- Modify: `packages/optio-conversation-ui/src/claudecode/ClaudeCodeView.tsx` (whole file below)
- Test: `packages/optio-conversation-ui/src/__tests__/claudecode-widget.test.tsx`
- Modify: `packages/optio-conversation-ui/README.md`, `docs/writing-agent-wrappers.md`

**Interfaces:**
- Consumes:
  - Task 4's endpoints: `POST send` → `{ok, id, queued}`; `POST steer {text}` → `{ok, id}`.
  - Task 5's local echo contract: `{type: 'x-optio-local-user', text, id?, queued?}`.
  - Task 7's `onSteer` prop.
- Produces: the finished stage-1 Claude Code widget. Other engines' views are untouched.

- [ ] **Step 1: Write the failing tests**

In `src/__tests__/claudecode-widget.test.tsx`, replace (inside `'clears the working indicator after a mid-turn send completes'`):
```tsx
    await act(async () => {
      fireEvent.click(screen.getByTestId('conversation-send'));
    });
```
with:
```tsx
    // Busy: the send is the main half of [Send when ready | Interrupt and send].
    await act(async () => {
      fireEvent.click(document.querySelector('[data-action-id="send-when-ready"]') as HTMLElement);
    });
```
Append these tests inside the `describe('ConversationWidget', …)` block, before its closing `});`:
```tsx
  it('a busy send shows the Queued bubble under the /send id; the listener event does not duplicate it; the echo takes it', async () => {
    const fetchMock = vi.fn(async () => new Response(JSON.stringify({ ok: true, id: 'q7', queued: true }), { status: 200 }));
    vi.stubGlobal('fetch', fetchMock);
    render(<ConversationWidget {...makeProps()} />);
    fire({ type: 'user', message: { role: 'user', content: [{ type: 'text', text: 'count to 10' }] } });
    fire({ type: 'stream_event', event: { type: 'content_block_delta', delta: { type: 'text_delta', text: '1 2 3' } } });
    const box = screen.getByTestId('conversation-input-box') as HTMLTextAreaElement;
    fireEvent.change(box, { target: { value: 'stop at 5' } });
    await act(async () => {
      fireEvent.click(document.querySelector('[data-action-id="send-when-ready"]') as HTMLElement);
    });
    await waitFor(() => expect(screen.getByTestId('queued-bubble').textContent).toContain('stop at 5'));
    fire({ type: 'x-optio-queued', id: 'q7', text: 'stop at 5' });
    expect(screen.getAllByTestId('queued-bubble')).toHaveLength(1);
    fire({ type: 'user', message: { role: 'user', content: [{ type: 'text', text: 'stop at 5' }] } });
    expect(screen.queryByTestId('queued-bubble')).toBeNull();
    expect(screen.getByText('stop at 5')).toBeTruthy();
  });

  it('Interrupt and send POSTs the text to /steer and echoes it', async () => {
    const fetchMock = vi.fn(async () => new Response(JSON.stringify({ ok: true, id: 's1' }), { status: 200 }));
    vi.stubGlobal('fetch', fetchMock);
    render(<ConversationWidget {...makeProps()} />);
    fire({ type: 'user', message: { role: 'user', content: [{ type: 'text', text: 'long job' }] } });
    const box = screen.getByTestId('conversation-input-box') as HTMLTextAreaElement;
    fireEvent.change(box, { target: { value: 'change of plan' } });
    fireEvent.click(screen.getByTestId('conversation-send-combined').querySelector('.ant-dropdown-trigger') as HTMLElement);
    fireEvent.click(await screen.findByRole('menuitem', { name: 'Interrupt and send' }));
    await waitFor(() => expect(fetchMock).toHaveBeenCalledOnce());
    const [url, init] = fetchMock.mock.calls[0] as unknown as [string, RequestInit];
    expect(url).toBe('/api/widget/db/gm/p1/steer');
    expect(JSON.parse(init.body as string)).toEqual({ text: 'change of plan' });
    await waitFor(() => expect(screen.getByText('change of plan')).toBeTruthy());
  });

  it('Send now on a queued bubble POSTs an empty text to /steer', async () => {
    const fetchMock = vi.fn(async () => new Response(JSON.stringify({ ok: true, id: null }), { status: 200 }));
    vi.stubGlobal('fetch', fetchMock);
    render(<ConversationWidget {...makeProps()} />);
    fire({ type: 'user', message: { role: 'user', content: [{ type: 'text', text: 'long job' }] } });
    fire({ type: 'x-optio-queued', id: 'q1', text: 'later' });
    fireEvent.click(screen.getByTestId('queued-send-now'));
    await waitFor(() => expect(fetchMock).toHaveBeenCalledOnce());
    const [url, init] = fetchMock.mock.calls[0] as unknown as [string, RequestInit];
    expect(url).toBe('/api/widget/db/gm/p1/steer');
    expect(JSON.parse(init.body as string)).toEqual({ text: '' });
  });

  it('an interrupt renders the cut-off answer with a jagged edge and one muted row, no error', () => {
    render(<ConversationWidget {...makeProps()} />);
    fire({ type: 'user', message: { role: 'user', content: [{ type: 'text', text: 'essay please' }] } });
    fire({ type: 'stream_event', event: { type: 'content_block_delta', delta: { type: 'text_delta', text: 'Lighthouses stand' } } });
    fire({ type: 'x-optio-interrupt', by: 'user' });
    fire({ type: 'assistant', message: { role: 'assistant', id: 'm1', content: [{ type: 'text', text: 'Lighthouses stand tall' }] } });
    fire({ type: 'user', message: { role: 'user', content: [{ type: 'text', text: '[Request interrupted by user]' }] } });
    fire({ type: 'result', subtype: 'error_during_execution', is_error: true, terminal_reason: 'aborted_streaming' });
    expect(screen.getByTestId('answer-interrupted').textContent).toContain('Lighthouses stand tall');
    expect(screen.getByTestId('activity-muted').textContent).toBe('⏹ Interrupted by you');
    expect(screen.queryByTestId('conversation-error-item')).toBeNull();
    expect(screen.queryByText('[Request interrupted by user]')).toBeNull();
  });
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `UITEST src/__tests__/claudecode-widget.test.tsx`
Expected:
- FAIL: 'a busy send…' (no `[data-action-id="send-when-ready"]`, since ClaudeCodeView passes no `onSteer` yet), 'Interrupt and send…' (no `conversation-send-combined`), 'Send now…' (no `queued-send-now`), and the modified mid-turn test.
- PASS: the interrupt test (the reducer from Task 6 already handles it).

- [ ] **Step 3: Implement**

Replace the whole of `src/claudecode/ClaudeCodeView.tsx` with:
```tsx
import { useEffect, useReducer, useRef } from 'react';
import type { WidgetProps } from 'optio-ui';
import type { ChatState, SessionControl } from '../chat.js';
import { initialChatState, reduceEvent } from './events.js';
import type { Attachment } from '../attachments.js';
import { resolveUploadUrl, uploadFiles, bundleUploadNotice } from '../uploads.js';
import { blobDownload } from '../FileDownloadContext.js';
import { ConversationView } from '../ConversationView.js';
import { NativeSpinner } from '../spinners/NativeSpinner.js';

interface ChatAction {
  ev: unknown;
  seq: number;
}

function chatReducer(state: ChatState, action: ChatAction): ChatState {
  return reduceEvent(state, action.ev, action.seq);
}

export function ClaudeCodeView(props: WidgetProps) {
  const toolVerbosity = ((props.process.widgetData as any)?.toolVerbosity ?? 'description-only') as
    'silent' | 'description-while-active' | 'description-only' | 'verbose';
  const thinkingVerbosity = ((props.process.widgetData as any)?.thinkingVerbosity ?? 'hidden') as
    'hidden' | 'visible';
  const initialControls = ((props.process.widgetData as any)?.controls ?? []) as SessionControl[];
  const showSessionControls = Boolean((props.process.widgetData as any)?.showSessionControls);
  const [state, dispatch] = useReducer(chatReducer, { ...initialChatState, controls: initialControls });
  const localSeqRef = useRef(0);
  const showFileUpload = Boolean((props.process.widgetData as any)?.showFileUpload);
  const maxUploadBytes = Number((props.process.widgetData as any)?.maxUploadBytes ?? 10_000_000);
  const fileDownload = Boolean((props.process.widgetData as any)?.fileDownload);
  const nativeSpinner = Boolean((props.process.widgetData as any)?.nativeSpinner);

  const { widgetProxyUrl } = props; // ends with '/' — trailing slash is load-bearing

  useEffect(() => {
    console.info('[optio-conversation-ui] claudecode conversation widget activated:', `${widgetProxyUrl}events`);
    const es = new EventSource(`${widgetProxyUrl}events`);
    es.onmessage = (ev: MessageEvent) => {
      let parsed: unknown;
      try {
        parsed = JSON.parse(ev.data);
      } catch {
        return;
      }
      // The reducer sniffs the runtime model from system/init & message.model
      // and folds it into the model control (only while the control has no
      // value yet — an operator pick wins).
      dispatch({ ev: parsed, seq: Number(ev.lastEventId) });
    };
    return () => es.close();
  }, [widgetProxyUrl]);

  // The optimistic local-user echo (dispatched on a successful send) sets
  // state.busy immediately, so busy is purely reducer-driven — no separate
  // send flag that a busy-change effect could fail to clear on a mid-turn send.
  const busy = state.busy;

  // POST a JSON body. Returns the parsed response on 2xx ({} when the body is
  // not a JSON object), null on failure.
  async function postJson(path: string, body: unknown): Promise<Record<string, unknown> | null> {
    try {
      const resp = await fetch(`${widgetProxyUrl}${path}`, {
        method: 'POST',
        headers: { 'content-type': 'application/json' },
        body: JSON.stringify(body),
      });
      if (!resp.ok) return null;
      try {
        const parsed: unknown = await resp.json();
        return parsed && typeof parsed === 'object' ? (parsed as Record<string, unknown>) : {};
      } catch {
        return {};
      }
    } catch {
      return null;
    }
  }

  async function post(path: string, body: unknown): Promise<boolean> {
    return (await postJson(path, body)) !== null;
  }

  // When files are attached, upload them through the generic route first, then
  // bundle one `System:` notice line per stored file into the prompt so the
  // agent can Read them from the workdir. null: nothing left to send.
  async function preparePrompt(body: string, attachments: Attachment[]): Promise<string | null> {
    if (attachments.length === 0) return body;
    const uploadUrl = resolveUploadUrl(props.process.widgetData, widgetProxyUrl);
    if (!uploadUrl) return null;
    const { ok: stored, failed } = await uploadFiles(uploadUrl, attachments, maxUploadBytes);
    for (const f of failed) {
      // Surface each failed upload as an immediate, transient error row.
      localSeqRef.current -= 1;
      dispatch({ ev: { type: 'x-optio-local-error', text: `Upload failed: ${f.name} — ${f.error}` }, seq: localSeqRef.current });
    }
    // Everything failed and no prompt to send → don't send an empty turn.
    if (stored.length === 0 && body.trim() === '') return null;
    return bundleUploadNotice(stored, body);
  }

  // Optimistic local echo: show the operator's text (not the System: preamble)
  // now. It carries the listener's id for the message, so the listener's
  // x-optio-queued for the same id does not add a second bubble; `queued`
  // makes it the Queued bubble until Claude takes it. The wire echo confirms
  // it in place (or moves a queued one to where Claude took it). Negative
  // seqs keep React keys unique and clear of wire seqs.
  function localEcho(text: string, resp: Record<string, unknown>, queued: boolean) {
    localSeqRef.current -= 1;
    dispatch({
      ev: { type: 'x-optio-local-user', text, id: typeof resp.id === 'string' ? resp.id : undefined, queued },
      seq: localSeqRef.current,
    });
  }

  async function onFileDownload(relpath: string, filename: string) {
    try {
      const r = await fetch(`${widgetProxyUrl}download?path=${encodeURIComponent(relpath)}`);
      if (!r.ok) return;
      const mime = r.headers.get('content-type') || 'application/octet-stream';
      const bytes = new Uint8Array(await r.arrayBuffer());
      blobDownload(bytes, mime, filename);
    } catch {
      /* ignore — surfaced to the operator as a non-download */
    }
  }

  return (
    <ConversationView
      state={state}
      closed={state.closed}
      busy={busy}
      toolVerbosity={toolVerbosity}
      thinkingVerbosity={thinkingVerbosity}
      showFileUpload={showFileUpload}
      maxUploadBytes={maxUploadBytes}
      fileDownload={fileDownload}
      nativeSpinner={nativeSpinner ? <NativeSpinner engine="claudecode" /> : undefined}
      onSend={async (body, attachments) => {
        // Send (idle) or Send when ready (busy): the listener answers
        // {id, queued}; queued means Claude holds it until the next tool result.
        const prompt = await preparePrompt(body, attachments);
        if (prompt === null) return false;
        const resp = await postJson('send', { text: prompt });
        if (resp === null) return false;
        localEcho(body, resp, resp.queued === true);
        return true;
      }}
      onSteer={async (body, attachments) => {
        // Interrupt and send (body), or Send now on a queued bubble (no body):
        // POST /steer stops the running step, then Claude runs what it holds
        // and the new text follows.
        const prompt = body === '' && attachments.length === 0 ? '' : await preparePrompt(body, attachments);
        if (prompt === null) return false;
        const resp = await postJson('steer', { text: prompt });
        if (resp === null) return false;
        localEcho(body, resp, false);
        return true;
      }}
      onInterrupt={() => void post('interrupt', {})}
      onPermission={(requestId, behavior) => {
        // Claude Code's can_use_tool schema wants a human-readable reason on
        // deny; send a default so a bare click satisfies it. (The wire also
        // carries a free-form message if a reason field is added later.)
        const body =
          behavior === 'deny'
            ? { request_id: requestId, behavior, message: 'Denied by the operator.' }
            : { request_id: requestId, behavior };
        void post('permission', body);
      }}
      onFileDownload={onFileDownload}
      controls={showSessionControls ? state.controls : undefined}
      onControlChange={(id, value) => {
        // Optimistic patch through the reducer, then POST /control; a model
        // change makes the engine relaunch claude (restart-based).
        localSeqRef.current -= 1;
        dispatch({ ev: { type: 'x-optio-control-update', id, value }, seq: localSeqRef.current });
        void post('control', { id, value });
      }}
      themeMode={(props as any).themeMode}
      onToggleTheme={(props as any).onToggleTheme}
    />
  );
}
```
(`localEcho` with an empty body is a no-op: the reducer ignores an empty `x-optio-local-user`.)

- [ ] **Step 4: Run the tests to verify they pass**

Run: `UITEST src/__tests__/claudecode-widget.test.tsx src/__tests__/conversation-upload.test.tsx src/__tests__/claudecode-model-widget.test.tsx`
Expected: PASS, all tests.

- [ ] **Step 5: Docs**

In `packages/optio-conversation-ui/README.md`, replace:
```
- Streamed chat transcript (replay + live) with optimistic local echo.
```
with:
```
- Streamed chat transcript (replay + live) with optimistic local echo.
- Steering while the agent works (claudecode, via the view's `onSteer`): the busy input bar is `[Send when ready | Interrupt and send]` (Enter / ⌘/Ctrl-Enter) beside the red Interrupt; a message the agent has not taken yet is a dashed "Queued" bubble with **Send now**; an interrupted answer keeps its text with a jagged bottom edge, followed by one muted "⏹ Interrupted by you" row. Views without `onSteer` keep a single Send.
```
Replace:
```
  - `reduceClaudecodeEvent(state, ev, seq, now?)`: `now` (epoch ms, default `Date.now()`) is the clock used only until the stream has shown a user/assistant timestamp; pass it for deterministic replays and tests.
```
with:
```
  - `reduceClaudecodeEvent(state, ev, seq, now?)`: `now` (epoch ms, default `Date.now()`) is the clock used only until the stream has shown a user/assistant timestamp; pass it for deterministic replays and tests.
  - Steering fields (all optional): user items `queued` (not taken yet; pinned at the bottom) and `queueId` (optio's id for the message); assistant items `interrupted` (cut off by the operator); activity items `muted` (a quiet one-line note). The claudecode reducer maps the listener's `x-optio-queued`, `x-optio-taken` and `x-optio-interrupt` events onto them.
```
In `docs/writing-agent-wrappers.md`, replace:
```
   reducer, and wires the `ConversationViewProps` callbacks (`onSend`,
   `onInterrupt`, `onPermission`, `onFileDownload`, and `onControlChange` for the
   generic session controls) to the agent's endpoints. It then hands all rendering
   to the shared `ConversationView`.
```
with:
```
   reducer, and wires the `ConversationViewProps` callbacks (`onSend`,
   `onInterrupt`, `onPermission`, `onFileDownload`, `onControlChange` for the
   generic session controls, and `onSteer` → `POST /steer` once the wrapper has
   steering, see B.1) to the agent's endpoints. It then hands all rendering
   to the shared `ConversationView`.
```

- [ ] **Step 6: Full verification against the Task 0 baselines**

Run each command separately, one after the other, and compare each with its baseline file:
```bash
ssh excavator 'cd ~/deai/optio-steering/packages/optio-conversation-ui && ./node_modules/.bin/vitest run --maxWorkers=3 2>&1 | tail -30'        # vs /tmp/steer-baseline-ui.txt
ssh excavator 'cd ~/deai/optio-steering/packages/optio-conversation-ui && ./node_modules/.bin/tsc -p . 2>&1 | tail -30'                            # vs /tmp/steer-baseline-tsc.txt
ssh excavator 'cd ~/deai/optio-steering && PYTHONPATH=packages/optio-agents/src:packages/optio-claudecode/src:packages/optio-host/src:packages/optio-core/src ~/deai/optio/.venv/bin/pytest -q -rf -p no:cacheprovider packages/optio-agents/tests 2>&1 | tail -25'       # vs /tmp/steer-baseline-py-agents.txt
ssh excavator 'cd ~/deai/optio-steering && PYTHONPATH=packages/optio-agents/src:packages/optio-claudecode/src:packages/optio-host/src:packages/optio-core/src ~/deai/optio/.venv/bin/pytest -q -rf -p no:cacheprovider packages/optio-claudecode/tests 2>&1 | tail -25'   # vs /tmp/steer-baseline-py-claudecode.txt
ssh excavator 'cd ~/deai/unitas/packages/vultus-antd && node_modules/.bin/vitest run --maxWorkers=2 --testTimeout=30000 2>&1 | tail -15'           # vs /tmp/steer-baseline-vultus.txt
```
Expected: no failure that is not in the baseline; the pass counts grew by the tests this plan added. For any new failure, use superpowers:systematic-debugging before changing code.

Then loop the new Python and reducer tests under CPU contention, as optio AGENTS.md requires for anything timing-adjacent. Keep the hogs to 2 on this 3.8 GB host:
```bash
ssh excavator 'hogs=""; for i in 1 2; do python3 -c "while True: pass" & hogs="$hogs $!"; done; cd ~/deai/optio-steering && for n in $(seq 1 10); do PYTHONPATH=packages/optio-agents/src:packages/optio-claudecode/src:packages/optio-host/src:packages/optio-core/src ~/deai/optio/.venv/bin/pytest -q -p no:cacheprovider packages/optio-agents/tests/test_steering.py 2>&1 | tail -1; PYTHONPATH=packages/optio-agents/src:packages/optio-claudecode/src:packages/optio-host/src:packages/optio-core/src ~/deai/optio/.venv/bin/pytest -q -p no:cacheprovider packages/optio-claudecode/tests/test_conversation_listener.py packages/optio-claudecode/tests/test_conversation_driver.py 2>&1 | tail -1; done; kill $hogs'
```
Expected: every line reports passed, 0 failed.

- [ ] **Step 7: Commit**

```bash
ssh excavator 'cd ~/deai/optio-steering && git add packages/optio-conversation-ui/src/claudecode/ClaudeCodeView.tsx packages/optio-conversation-ui/src/__tests__/claudecode-widget.test.tsx packages/optio-conversation-ui/README.md docs/writing-agent-wrappers.md && git commit -m "feat(optio-conversation-ui): wire Claude Code steering in ClaudeCodeView

onSteer posts /steer (Interrupt and send; Send now with no text); the
/send response id and queued flag go into the local echo, so the listener
x-optio-queued supersedes it instead of adding a second bubble." && git log --oneline -3 && git status --short'
```
Expected: clean tree.

---

## Out of scope for stage 1 (from the spec)

- codex and grok (stage 2), and cursor, kimicode, antigravity and opencode (stage 3). They reuse `optio_agents.steering` and the `chat.ts` helpers.
- Editing or cancelling queued messages; per-agent captions.
- Bumping `optio-conversation-ui`'s `vultus-antd` dependency to `^0.1.1`. That needs a `pnpm install` to keep `pnpm-lock.yaml` consistent, which the worktree must not run. It belongs to the release step together with the unitas 0.1.1 publish.
