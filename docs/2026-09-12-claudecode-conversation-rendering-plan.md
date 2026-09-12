# Claude Code conversation rendering Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make Claude Code conversation sessions show the agent's between-tool narration, real tool rows (status, result, live elapsed time) and background-task outcomes, per `docs/2026-09-12-claudecode-conversation-rendering-design.md`.

**Architecture:** Almost all logic lives in the pure claudecode event reducer (`packages/optio-conversation-ui/src/claudecode/events.ts`), which turns raw stream-json events into the engine-neutral `ChatState`. The shared view (`ConversationView.tsx`) gains a ticking elapsed counter, a verbose result block and a silent-mode background line. optio-claudecode (Python) only pins one env var and updates doc comments.

**Tech Stack:** TypeScript, React 19, antd, vitest + @testing-library/react (jsdom); Python 3.13, pytest.

## Global Constraints

- Work only in `superego:~/deai/optio` (fresh clone of `deai-network/optio`, `main`). Never copy files to the excavator host. Do not push.
- No `Co-Authored-By` or other self-credit lines in commits (optio `AGENTS.md`).
- Tests must not depend on wall-clock time: use explicit timestamps, the reducer's `now` parameter, or vitest fake timers (optio `AGENTS.md`, "Tests must survive CPU starvation").
- The engine keeps passing raw events through untouched; do not change `optio_claudecode/conversation.py` event payloads.
- Separator between parts of one reply bubble: exactly `"\n\n"`.
- Tool result text is trimmed to 2000 characters (`…` as the last character when cut).
- Duration format: `Ns` under a minute, `Mm SSs` under an hour, `Hh MMm` above.
- Activity text for a finished background task without a row: `✓ Background task finished: <summary>`; failed: `✗ Background task failed: <summary>`.
- Env var pinned in the conversation launch env: `CLAUDE_CODE_THINKING_DISPLAY_UPDATES=1`, overridable by `extra_env`.

## File Structure

| File | Responsibility | Tasks |
|---|---|---|
| `packages/optio-claudecode/src/optio_claudecode/host_actions.py` | conversation launch env: add the pin | 1 |
| `packages/optio-claudecode/src/optio_claudecode/types.py` | doc comments for `tool_verbosity` / `thinking_verbosity` | 1 |
| `packages/optio-claudecode/tests/test_host_actions.py` | pin tests | 1 |
| `packages/optio-conversation-ui/src/chat.ts` | shared chat model: new optional fields | 2, 3, 4 |
| `packages/optio-conversation-ui/src/claudecode/events.ts` | claudecode reducer | 2, 3, 4 |
| `packages/optio-conversation-ui/src/__tests__/claudecode-events.test.ts` | reducer tests | 2, 3, 4 |
| `packages/optio-conversation-ui/src/duration.ts` (new) | `formatDuration` | 5 |
| `packages/optio-conversation-ui/src/__tests__/duration.test.ts` (new) | its tests | 5 |
| `packages/optio-conversation-ui/src/ConversationView.tsx` | counter, result block, silent background line | 5 |
| `packages/optio-conversation-ui/src/__tests__/conversation-view.test.tsx` | view tests | 5 |
| `packages/optio-conversation-ui/src/__tests__/claudecode-widget.test.tsx` | end-to-end widget tests | 5 |

Commands used throughout (run from the directories shown):

- UI tests: `cd ~/deai/optio/packages/optio-conversation-ui && pnpm exec vitest run <file>`
- UI full suite: `cd ~/deai/optio/packages/optio-conversation-ui && pnpm test`
- UI typecheck: `cd ~/deai/optio/packages/optio-conversation-ui && pnpm build`
- Python: `cd ~/deai/optio && .venv/bin/pytest -q -p no:cacheprovider <path>`

---

### Task 1: Workspace, env pin and doc comments (optio-claudecode)

**Files:**
- Modify: `packages/optio-claudecode/src/optio_claudecode/host_actions.py` (`conversation_launch_env`, ~line 1195)
- Modify: `packages/optio-claudecode/src/optio_claudecode/types.py:209-215`
- Test: `packages/optio-claudecode/tests/test_host_actions.py` (after `test_conversation_launch_env_disables_autoupdater`, ~line 929)

**Interfaces:**
- Consumes: nothing.
- Produces: `conversation_launch_env(workdir, extra_env)` now always contains `CLAUDE_CODE_THINKING_DISPLAY_UPDATES` (value `"1"` unless `extra_env` overrides it).

- [ ] **Step 1: Install the workspace and take baselines**

```bash
cd ~/deai/optio && make install
cd ~/deai/optio/packages/optio-conversation-ui && pnpm test 2>&1 | tail -4
cd ~/deai/optio && .venv/bin/pytest -q -p no:cacheprovider packages/optio-claudecode/tests/test_host_actions.py 2>&1 | tail -2
```

Expected: `make install` exits 0; both suites pass. Write the pass counts down; later tasks compare against them.

- [ ] **Step 2: Write the failing tests**

Append after `test_conversation_launch_env_disables_autoupdater` in `tests/test_host_actions.py`:

```python
def test_conversation_launch_env_pins_thinking_display_updates():
    """Since CLI 2.1.267 the model's between-tool narration arrives as thinking
    updates; pin the switch that requests them so a CLI default change cannot
    silently drop the narration channel."""
    env = host_actions.conversation_launch_env("/wd", None)
    assert env["CLAUDE_CODE_THINKING_DISPLAY_UPDATES"] == "1"


def test_conversation_launch_env_extra_env_overrides_thinking_display_pin():
    env = host_actions.conversation_launch_env(
        "/wd", {"CLAUDE_CODE_THINKING_DISPLAY_UPDATES": "0"},
    )
    assert env["CLAUDE_CODE_THINKING_DISPLAY_UPDATES"] == "0"
```

- [ ] **Step 3: Run them to verify they fail**

Run: `cd ~/deai/optio && .venv/bin/pytest -q -p no:cacheprovider packages/optio-claudecode/tests/test_host_actions.py -k thinking_display`
Expected: the first FAILS with `KeyError: 'CLAUDE_CODE_THINKING_DISPLAY_UPDATES'`; the second passes already (extra env wins today). That is fine: it guards the override once the pin exists.

- [ ] **Step 4: Implement the pin**

In `conversation_launch_env`, change the returned dict from

```python
    return {
        "HOME": home_dir,
        "PATH": f"{home_local_bin}:{base_path}",
        # Same rationale as _build_claude_shell_command: the autoupdater can
        # only EACCES against the --rox cache; provisioning owns freshness.
        "DISABLE_AUTOUPDATER": "1",
        **extra,
    }
```

to

```python
    return {
        "HOME": home_dir,
        "PATH": f"{home_local_bin}:{base_path}",
        # Same rationale as _build_claude_shell_command: the autoupdater can
        # only EACCES against the --rox cache; provisioning owns freshness.
        "DISABLE_AUTOUPDATER": "1",
        # Since CLI 2.1.267 the model writes its between-tool narration as
        # "thinking updates" (text-bearing thinking blocks); this switch (on by
        # default) requests them. Pinned so a default change cannot silently
        # drop the narration. Never enable showThinkingSummaries alongside it:
        # in -p mode that returns no thinking text at all, narration included
        # (docs/2026-09-12-claudecode-conversation-rendering-design.md).
        "CLAUDE_CODE_THINKING_DISPLAY_UPDATES": "1",
        **extra,
    }
```

- [ ] **Step 5: Update the doc comments in `types.py`**

Replace

```python
    # Conversation-UI tool-call rendering: "verbose" = full input table,
    # "description-only" = one summary line, "silent" = nothing. Carried to the
    # widget via widgetData; only affects conversation_ui rendering.
    tool_verbosity: ToolVerbosity = "description-only"
    # Whether the conversation widget shows the agent's reasoning/thinking traces.
    # Default hidden — thinking is noisy; opt in per task.
    thinking_verbosity: ThinkingVerbosity = "hidden"
```

with

```python
    # Conversation-UI tool-call rendering, one row per call with a live elapsed
    # time: "silent" = no rows (a finished background job still gets one muted
    # line), "description-while-active" = a line while the call runs,
    # "description-only" = a persistent line, "verbose" = the line plus the
    # args and the result (collapsed once finished). Carried to the widget via
    # widgetData; only affects conversation_ui rendering.
    tool_verbosity: ToolVerbosity = "description-only"
    # Whether the conversation widget shows the agent's reasoning/thinking traces.
    # Default hidden — thinking is noisy; opt in per task. No effect for Claude
    # Code in conversation mode: its text-bearing thinking blocks are the
    # between-tool narration and always render as replies, and the real
    # reasoning is not available in -p mode.
    thinking_verbosity: ThinkingVerbosity = "hidden"
```

- [ ] **Step 6: Run the tests to verify they pass**

Run: `cd ~/deai/optio && .venv/bin/pytest -q -p no:cacheprovider packages/optio-claudecode/tests/test_host_actions.py`
Expected: all pass (baseline + 2).

- [ ] **Step 7: Commit**

```bash
cd ~/deai/optio
git add packages/optio-claudecode/src/optio_claudecode/host_actions.py packages/optio-claudecode/src/optio_claudecode/types.py packages/optio-claudecode/tests/test_host_actions.py
git commit -m "fix(optio-claudecode): pin CLAUDE_CODE_THINKING_DISPLAY_UPDATES in conversation mode

The model's between-tool narration arrives as thinking updates since CLI
2.1.267; pin the switch that requests them. Document that thinking_verbosity
has no effect for Claude Code conversation mode and what each tool_verbosity
level shows."
```

---

### Task 2: Narration from thinking blocks (reducer)

**Files:**
- Modify: `packages/optio-conversation-ui/src/chat.ts:7` (assistant item)
- Modify: `packages/optio-conversation-ui/src/claudecode/events.ts` (helpers, `finalizeAt`, `finalizePending`, `assistant` and `stream_event` cases)
- Test: `packages/optio-conversation-ui/src/__tests__/claudecode-events.test.ts`

**Interfaces:**
- Consumes: nothing new.
- Produces:
  - `ChatItem` assistant variant gains `openPart?: number` (reducer-private: start offset in `text` of the block currently streaming; not rendered).
  - In `events.ts` (module-private): `PART_SEPARATOR = '\n\n'`, `type AssistantItem = Extract<ChatItem, { kind: 'assistant' }>`, `blockText(block): string | null`, `replaceAt(items, idx, item): ChatItem[]`, `appendDelta(items, seq, delta): ChatItem[]`, `applyBlockText(items, seq, text, msgId?): ChatItem[]`. Tasks 3 and 4 reuse `replaceAt` and `AssistantItem`.

- [ ] **Step 1: Write the failing tests**

In `claudecode-events.test.ts`, add builders after `const result = ...` (line 12):

```ts
const thinking = (text: string, msgId?: string) => ({ type: 'assistant', message: { role: 'assistant', id: msgId, content: [{ type: 'thinking', thinking: text, signature: 'sig' }] } });
const thinkingDelta = (text: string) => ({ type: 'stream_event', event: { type: 'content_block_delta', delta: { type: 'thinking_delta', thinking: text } } });
```

Add a new `describe` block after the `reduceEvent` block (after line 317):

```ts
describe('narration from thinking blocks', () => {
  // Since CLI 2.1.267 the model writes its between-tool narration as a
  // text-bearing thinking block; the real reasoning is an empty thinking block.

  it('a text-bearing thinking block becomes an assistant bubble', () => {
    const s = run([user('q'), thinking("I'll check the VPN first.", 'm1')]);
    const bubbles = ofKind(s, 'assistant');
    expect(bubbles.map((b) => b.text)).toEqual(["I'll check the VPN first."]);
    expect(bubbles[0].pending).toBe(true);
  });

  it('an empty thinking block (hidden reasoning) is ignored', () => {
    const s = run([thinking('', 'm1'), thinking('   ', 'm1')]);
    expect(s.items).toEqual([]);
  });

  it('thinking_delta streams narration and the final thinking event does not duplicate it', () => {
    const mid = run([messageStart('m1'), thinkingDelta("I'll "), thinkingDelta('check.')]);
    expect(ofKind(mid, 'assistant').map((b) => b.text)).toEqual(["I'll check."]);
    const s = reduceEvent(mid, thinking("I'll check.", 'm1'), 4);
    expect(ofKind(s, 'assistant').map((b) => b.text)).toEqual(["I'll check."]);
  });

  it('narration and text in one message form one bubble with two parts (live)', () => {
    const s = run([
      messageStart('m1'),
      thinking('', 'm1'),
      thinkingDelta('Checking the log.'),
      thinking('Checking the log.', 'm1'),
      delta('All good.'),
      assistantText('All good.', 'm1'),
    ]);
    expect(ofKind(s, 'assistant').map((b) => b.text)).toEqual(['Checking the log.\n\nAll good.']);
  });

  it('narration and text in one message form one bubble with two parts (replay, no deltas)', () => {
    const s = run([thinking('Checking the log.', 'm1'), assistantText('All good.', 'm1')]);
    expect(ofKind(s, 'assistant').map((b) => b.text)).toEqual(['Checking the log.\n\nAll good.']);
  });

  it('result keeps narration that precedes the result text in the same message', () => {
    const s = run([user('q'), thinking('Checking the log.', 'm1'), assistantText('All good.', 'm1'), result('All good.')]);
    const bubbles = ofKind(s, 'assistant');
    expect(bubbles.map((b) => b.text)).toEqual(['Checking the log.\n\nAll good.']);
    expect(bubbles[0].pending).toBe(false);
  });

  it('narration in different messages stays in separate bubbles', () => {
    const s = run([thinking('Step one.', 'm1'), thinking('Step two.', 'm2')]);
    expect(ofKind(s, 'assistant').map((b) => b.text)).toEqual(['Step one.', 'Step two.']);
  });

  it('finalized bubbles carry no openPart bookkeeping', () => {
    const s = run([messageStart('m1'), delta('10'), messageStart('m2'), delta('9'), result('9')]);
    for (const b of ofKind(s, 'assistant')) expect('openPart' in b).toBe(false);
  });
});
```

- [ ] **Step 2: Run them to verify they fail**

Run: `cd ~/deai/optio/packages/optio-conversation-ui && pnpm exec vitest run src/__tests__/claudecode-events.test.ts`
Expected: the new narration tests FAIL (no bubble from thinking blocks; the replay case shows only `All good.`). Two of them pass already and guard later steps: "an empty thinking block (hidden reasoning) is ignored" and "finalized bubbles carry no openPart bookkeeping". All pre-existing tests still pass.

- [ ] **Step 3: Add the field to `chat.ts`**

Replace line 7

```ts
  | { kind: 'assistant'; text: string; pending: boolean; seq: number; msgId: string | null }
```

with

```ts
  | {
      kind: 'assistant';
      text: string;
      pending: boolean;
      seq: number;
      msgId: string | null;
      // Reducer-private (claudecode): start offset in `text` of the content
      // block currently streaming via deltas; absent when none is open. Not
      // rendered.
      openPart?: number;
    }
```

- [ ] **Step 4: Implement the helpers in `events.ts`**

After the `HARNESS_PREFIX` constant add:

```ts
// Parts of one assistant message (narration, then the answer) render in one
// bubble, separated by a blank line.
const PART_SEPARATOR = '\n\n';

type AssistantItem = Extract<ChatItem, { kind: 'assistant' }>;

// Text a content block contributes to the reply bubble: a text block's text,
// or the narration a text-bearing thinking block carries. Since CLI 2.1.267
// the model writes its between-tool narration as such "thinking updates"; the
// real reasoning arrives as a separate thinking block with empty text.
function blockText(block: any): string | null {
  if (block?.type === 'text' && typeof block.text === 'string') return block.text;
  if (block?.type === 'thinking' && typeof block.thinking === 'string' && block.thinking.trim() !== '') {
    return block.thinking;
  }
  return null;
}

function replaceAt(items: ChatItem[], idx: number, item: ChatItem): ChatItem[] {
  return [...items.slice(0, idx), item, ...items.slice(idx + 1)];
}
```

Replace `finalizeAt` with:

```ts
function finalizeAt(items: ChatItem[], idx: number): ChatItem[] {
  const current = items[idx] as AssistantItem;
  if (!current.pending) return items;
  const next: AssistantItem = { ...current, pending: false };
  delete next.openPart;
  return replaceAt(items, idx, next);
}
```

Replace `upsertPending` (and its comment) with these two functions:

```ts
// A streamed delta (text_delta or thinking_delta) extends the open part of the
// pending bubble; the first delta of a block opens a new part, separated from
// earlier parts by a blank line. A pending bubble that is no longer the tail is
// finalized where it stands and a fresh bubble opens at the end.
function appendDelta(items: ChatItem[], seq: number, delta: string): ChatItem[] {
  const idx = pendingIndex(items);
  if (idx !== -1 && isTail(items, idx)) {
    const cur = items[idx] as AssistantItem;
    if (cur.openPart !== undefined) return replaceAt(items, idx, { ...cur, text: cur.text + delta });
    const sep = cur.text === '' ? '' : PART_SEPARATOR;
    return replaceAt(items, idx, {
      ...cur,
      text: cur.text + sep + delta,
      openPart: cur.text.length + sep.length,
    });
  }
  if (idx !== -1) items = finalizeAt(items, idx);
  return [...items, { kind: 'assistant', text: delta, pending: true, seq, msgId: null, openPart: 0 }];
}

// A content block's final assistant event (it carries the block's full text).
// Within the same message it replaces the part its deltas streamed, or appends
// a new part when nothing streamed (replays hold no stream_events). A different
// message, or a pending bubble that is no longer the tail, opens a fresh bubble.
function applyBlockText(items: ChatItem[], seq: number, text: string, msgId?: string): ChatItem[] {
  const idx = pendingIndex(items);
  if (idx !== -1) {
    const cur = items[idx] as AssistantItem;
    const sameMessage = cur.msgId === null || msgId == null || cur.msgId === msgId;
    if (isTail(items, idx) && sameMessage) {
      const base =
        cur.openPart !== undefined
          ? cur.text.slice(0, cur.openPart)
          : cur.text + (cur.text === '' ? '' : PART_SEPARATOR);
      const next: AssistantItem = { ...cur, text: base + text, msgId: msgId ?? cur.msgId };
      delete next.openPart;
      return replaceAt(items, idx, next);
    }
    items = finalizeAt(items, idx);
  }
  return [...items, { kind: 'assistant', text, pending: true, seq, msgId: msgId ?? null }];
}
```

Replace `finalizePending` with:

```ts
// Finalize the in-flight assistant bubble (pending -> false). The result text
// replaces the bubble's text unless the bubble already ends with it: narration
// parts earlier in the same message must survive the end of the turn. Creates
// a finalized bubble if there is result text but no pending bubble (e.g. a
// replay that skipped partials).
function finalizePending(items: ChatItem[], seq: number, resultText: string | null): ChatItem[] {
  const idx = pendingIndex(items);
  if (idx === -1) {
    if (resultText === null || resultText === '') return items;
    return [...items, { kind: 'assistant', text: resultText, pending: false, seq, msgId: null }];
  }
  const current = items[idx] as AssistantItem;
  const keep = resultText === null || resultText === '' || current.text.endsWith(resultText);
  const next: AssistantItem = { ...current, text: keep ? current.text : resultText, pending: false };
  delete next.openPart;
  return replaceAt(items, idx, next);
}
```

- [ ] **Step 5: Use them in the `assistant` and `stream_event` cases**

In `case 'assistant'`, replace the text branch

```ts
        if (block?.type === 'text' && typeof block.text === 'string') {
          // The agent is answering now — clear any in-flight tool announcement,
          // then replace the pending bubble's text (the event carries the full
          // text so far, so accumulated stream_event deltas aren't double-counted
          // — within one message; a different message id opens a new bubble).
          items = upsertPending(withoutTools(items), seq, block.text, 'replace', msgId);
        } else if (block?.type === 'tool_use') {
```

with

```ts
        const text = blockText(block);
        if (text !== null) {
          // The agent is answering (or narrating) — clear any in-flight tool
          // announcement, then complete this block's part of the bubble.
          items = applyBlockText(withoutTools(items), seq, text, msgId);
        } else if (block?.type === 'tool_use') {
```

In `case 'stream_event'`, replace

```ts
      const delta = ev.event?.delta?.text;
      if (typeof delta !== 'string' || delta === '') return state;
      // The answer is streaming — clear any in-flight tool announcement.
      return { ...state, items: upsertPending(withoutTools(state.items), seq, delta, 'append') };
```

with

```ts
      // text_delta carries `text`; thinking_delta carries `thinking` (narration;
      // the hidden reasoning block streams no text). signature/input_json deltas
      // carry neither.
      const d = ev.event?.delta;
      const delta = d?.type === 'thinking_delta' ? d.thinking : d?.text;
      if (typeof delta !== 'string' || delta === '') return state;
      // The answer is streaming — clear any in-flight tool announcement.
      return { ...state, items: appendDelta(withoutTools(state.items), seq, delta) };
```

- [ ] **Step 6: Run the tests to verify they pass**

Run: `cd ~/deai/optio/packages/optio-conversation-ui && pnpm exec vitest run src/__tests__/claudecode-events.test.ts`
Expected: all pass, pre-existing and new.

- [ ] **Step 7: Typecheck and commit**

```bash
cd ~/deai/optio/packages/optio-conversation-ui && pnpm build
cd ~/deai/optio
git add packages/optio-conversation-ui/src/chat.ts packages/optio-conversation-ui/src/claudecode/events.ts packages/optio-conversation-ui/src/__tests__/claudecode-events.test.ts
git commit -m "fix(optio-conversation-ui): render Claude Code narration from thinking blocks

Since CLI 2.1.267 the model's between-tool narration arrives as text-bearing
thinking blocks, which the claudecode reducer dropped. Treat them (and
thinking_delta) like text; drop empty reasoning blocks. Blocks of one message
render as parts of one bubble, live and on replay, and the end-of-turn result
no longer overwrites narration that precedes it."
```

Expected: `pnpm build` exits 0.

---

### Task 3: Real tool rows (reducer)

**Files:**
- Modify: `packages/optio-conversation-ui/src/chat.ts` (tool item)
- Modify: `packages/optio-conversation-ui/src/claudecode/events.ts`
- Test: `packages/optio-conversation-ui/src/__tests__/claudecode-events.test.ts`

**Interfaces:**
- Consumes (Task 2): `replaceAt`, `AssistantItem`, `applyBlockText`, `appendDelta`.
- Produces:
  - Tool `ChatItem` gains optional `callId?: string`, `result?: string`, `startedAt?: number`, `endedAt?: number`, `background?: boolean`.
  - `reduceEvent(state, ev, seq, now = Date.now())`: new optional 4th parameter, the clock used when an event has no `timestamp`.
  - In `events.ts` (module-private): `type ToolItem = Extract<ChatItem, { kind: 'tool' }>`, `eventTime(ev, now): number`, `trimResult(s): string`, `toolResultText(content): string`, `toolRow(block, seq, at): ChatItem`, `applyToolResults(items, content, at): ChatItem[]`, `freezeRunning(items, at, includeBackground): ChatItem[]`. Task 4 uses `ToolItem`, `eventTime`, `trimResult`.

- [ ] **Step 1: Write the failing tests**

Add builders after the Task 2 builders:

```ts
const T0 = '2026-09-12T10:00:00.000Z';
const T5 = '2026-09-12T10:00:05.000Z';
const toolCall = (id: string, name: string, input: unknown, timestamp?: string) => ({ type: 'assistant', timestamp, message: { role: 'assistant', content: [{ type: 'tool_use', id, name, input }] } });
const toolResult = (id: string, content: unknown, isError = false, timestamp?: string) => ({ type: 'user', timestamp, message: { role: 'user', content: [{ type: 'tool_result', tool_use_id: id, content, is_error: isError }] } });
```

Replace these five entries of the `cases` table (they encoded the old transient rows):

```ts
  {
    name: 'a new tool call adds a row; earlier rows stay',
    events: [toolUse('ToolSearch', { query: 'x' }), toolUse('WebSearch', { query: 'y' })],
    check: (s) => {
      expect(ofKind(s, 'tool').map((t) => t.name)).toEqual(['ToolSearch', 'WebSearch']);
    },
  },
  {
    name: 'a permission request keeps the earlier tool row',
    events: [
      toolUse('WebSearch', { query: 'y' }),
      controlRequest('perm-1', 'WebSearch', { query: 'y' }),
    ],
    check: (s) => {
      expect(ofKind(s, 'tool')).toHaveLength(1);
      expect(ofKind(s, 'permission')).toHaveLength(1);
    },
  },
  {
    name: 'an assistant answer keeps the tool row above it',
    events: [toolUse('Read', { file_path: '/x' }), assistantText('here is the answer')],
    check: (s) => {
      expect(s.items.map((i) => i.kind)).toEqual(['tool', 'assistant']);
    },
  },
  {
    name: 'a trailing tool use stays, frozen, at session close',
    events: [toolUse('Bash', { command: 'echo DONE >> ./optio.log' }), { type: 'x-optio-closed', reason: 'process ended' }],
    check: (s) => {
      expect(ofKind(s, 'tool')).toHaveLength(1);
      expect(ofKind(s, 'tool')[0].endedAt).toBeTypeOf('number');
      expect(ofKind(s, 'closed')).toHaveLength(1);
    },
  },
  {
    name: 'result keeps a running tool row and freezes it',
    events: [toolUse('Bash', { command: 'x' }), result('done')],
    check: (s) => {
      expect(ofKind(s, 'tool')).toHaveLength(1);
      expect(ofKind(s, 'tool')[0].endedAt).toBeTypeOf('number');
    },
  },
```

(Old names, for finding them: "a new tool announcement supersedes the previous one (ephemeral)", "a permission request clears any in-flight tool announcement", "assistant answer text clears the in-flight tool announcement", "a trailing tool use (e.g. echo DONE) is cleared by session close", "result clears a lingering tool announcement".)

Add a new `describe` block:

```ts
describe('real tool rows', () => {
  it('tool rows persist across later tool calls and replies', () => {
    const s = run([toolCall('t1', 'Bash', { command: 'ls' }), toolCall('t2', 'Read', { file_path: '/x' }), assistantText('done reading')]);
    expect(ofKind(s, 'tool').map((t) => t.name)).toEqual(['Bash', 'Read']);
    expect(ofKind(s, 'tool').map((t) => t.callId)).toEqual(['t1', 't2']);
    expect(ofKind(s, 'assistant')).toHaveLength(1);
  });

  it('a tool_result finishes its row with status, result and endedAt', () => {
    const s = run([toolCall('t1', 'Bash', { command: 'ls' }, T0), toolResult('t1', 'file1\nfile2\n', false, T5)]);
    const [row] = ofKind(s, 'tool');
    expect(row).toMatchObject({ status: 'done', result: 'file1\nfile2', startedAt: Date.parse(T0), endedAt: Date.parse(T5) });
    expect(ofKind(s, 'user')).toEqual([]);
  });

  it('an error tool_result marks the row failed', () => {
    const s = run([toolCall('t1', 'Bash', { command: 'false' }), toolResult('t1', 'exit 1', true)]);
    expect(ofKind(s, 'tool')[0].status).toBe('failed');
  });

  it('block-list result content contributes its text blocks', () => {
    const s = run([toolCall('t1', 'Read', {}), toolResult('t1', [{ type: 'text', text: 'a' }, { type: 'image' }, { type: 'text', text: 'b' }])]);
    expect(ofKind(s, 'tool')[0].result).toBe('a\nb');
  });

  it('long results are trimmed to 2000 characters', () => {
    const s = run([toolCall('t1', 'Bash', {}), toolResult('t1', 'x'.repeat(5000))]);
    const r = ofKind(s, 'tool')[0].result!;
    expect(r).toHaveLength(2000);
    expect(r.endsWith('…')).toBe(true);
  });

  it('a tool_result for an unknown id changes nothing', () => {
    const before = run([toolCall('t1', 'Bash', {})]);
    const after = reduceEvent(before, toolResult('nope', 'x'), 9);
    expect(after.items).toEqual(before.items);
  });

  it('startedAt falls back to the reducer clock when the event has no timestamp', () => {
    const s = reduceEvent(initialChatState, toolCall('t1', 'Bash', {}), 1, 12_345);
    expect(ofKind(s, 'tool')[0].startedAt).toBe(12_345);
  });

  it('result freezes a running row at the result time and leaves finished rows alone', () => {
    const s = run([
      toolCall('t1', 'Bash', {}, T0),
      toolResult('t1', 'ok', false, T5),
      toolCall('t2', 'Bash', {}, T5),
      { type: 'result', subtype: 'success', result: 'done', timestamp: '2026-09-12T10:00:09.000Z' },
    ]);
    const [a, b] = ofKind(s, 'tool');
    expect(a.endedAt).toBe(Date.parse(T5));
    expect(b.endedAt).toBe(Date.parse('2026-09-12T10:00:09.000Z'));
    expect(b.status).toBeUndefined();
  });
});
```

- [ ] **Step 2: Run them to verify they fail**

Run: `cd ~/deai/optio/packages/optio-conversation-ui && pnpm exec vitest run src/__tests__/claudecode-events.test.ts`
Expected: the five rewritten cases and the new `real tool rows` tests FAIL (rows are dropped; no status/result/timestamps); everything else passes.

- [ ] **Step 3: Extend the tool item in `chat.ts`**

In the tool variant, after the `status?: ...` line add:

```ts
      // Wire id of the call (claudecode tool_use.id): matches its tool_result
      // and background-task events.
      callId?: string;
      // The call's output (claudecode: tool_result text, or a background task's
      // summary), trimmed; rendered under the args in verbose.
      result?: string;
      // Epoch ms. startedAt drives the live elapsed counter; endedAt freezes it
      // (result, background completion, end of turn, session close).
      startedAt?: number;
      endedAt?: number;
      // A backgrounded shell command: its immediate tool_result does not finish
      // the row; the background-task notification does.
      background?: boolean;
```

- [ ] **Step 4: Add the tool helpers to `events.ts`**

After `replaceAt` add:

```ts
type ToolItem = Extract<ChatItem, { kind: 'tool' }>;

const RESULT_MAX = 2000;

// Epoch ms of an event (stream-json events carry an ISO `timestamp`), else the
// reducer's clock.
function eventTime(ev: any, now: number): number {
  const t = typeof ev?.timestamp === 'string' ? Date.parse(ev.timestamp) : NaN;
  return Number.isFinite(t) ? t : now;
}

function trimResult(s: string): string {
  const t = s.trim();
  return t.length > RESULT_MAX ? t.slice(0, RESULT_MAX - 1) + '…' : t;
}

// tool_result content is a string or a list of blocks; keep the text blocks.
function toolResultText(content: unknown): string {
  if (typeof content === 'string') return content;
  if (!Array.isArray(content)) return '';
  return content
    .filter((b: any) => b?.type === 'text' && typeof b.text === 'string')
    .map((b: any) => b.text)
    .join('\n');
}

function toolRow(block: any, seq: number, at: number): ChatItem {
  const row: ToolItem = { kind: 'tool', name: String(block.name ?? ''), input: block.input, seq, startedAt: at };
  if (typeof block.id === 'string') row.callId = block.id;
  return row;
}

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
    out = replaceAt(out, idx, {
      ...row,
      status: b.is_error ? 'failed' : 'done',
      result: trimResult(toolResultText(b.content)),
      endedAt: at,
    });
  }
  return out;
}

// Stop the counters of rows still running: at the end of a turn (background
// rows keep counting: their task outlives the turn) or at session close (all).
function freezeRunning(items: ChatItem[], at: number, includeBackground: boolean): ChatItem[] {
  let changed = false;
  const out = items.map((i) => {
    if (i.kind !== 'tool' || i.startedAt === undefined || i.endedAt !== undefined) return i;
    if (i.status === 'done' || i.status === 'failed') return i;
    if (i.background && !includeBackground) return i;
    changed = true;
    return { ...i, endedAt: at };
  });
  return changed ? out : items;
}
```

- [ ] **Step 5: Wire them in and drop the transient-row logic**

1. Change the signature: `export function reduceEvent(state: ChatState, ev: any, seq: number, now: number = Date.now()): ChatState {`
2. In `case 'user'`, replace its first line (`const { text, uploads } = parseUploadNotice(extractText(ev.message?.content));`) with:

```ts
      // Tool results arrive as user events; finish their rows first. Such an
      // event carries no text, so it adds no bubble below.
      const withResults = applyToolResults(state.items, ev.message?.content, eventTime(ev, now));
      if (withResults !== state.items) state = { ...state, items: withResults };
      const { text, uploads } = parseUploadNotice(extractText(ev.message?.content));
```

3. In `case 'assistant'`: add `const at = eventTime(ev, now);` after the `msgId` line; change `applyBlockText(withoutTools(items), ...)` to `applyBlockText(items, ...)`; replace the tool branch body and comment with:

```ts
        } else if (block?.type === 'tool_use') {
          // A persistent row per call; its tool_result (a later user event)
          // finishes it.
          items = [...items, toolRow(block, seq, at)];
        }
```

4. In `case 'stream_event'`: `appendDelta(withoutTools(state.items), seq, delta)` becomes `appendDelta(state.items, seq, delta)`; drop the "clear any in-flight tool announcement" comment line.
5. Replace `case 'result'` with:

```ts
    case 'result': {
      const resultText = typeof ev.result === 'string' ? ev.result : null;
      const items = freezeRunning(state.items, eventTime(ev, now), false);
      // An API/model error arrives as a result with is_error — surface it as a
      // distinct, explained error item instead of a plain agent bubble.
      if (ev.is_error) {
        const msg = explainApiError(resultText ?? '', ev.api_error_status);
        return { ...state, items: [...items, { kind: 'error', text: msg, seq }], busy: false };
      }
      return { ...state, items: finalizePending(items, seq, resultText), busy: false };
    }
```

6. In `case 'control_request'`: `items: [...withoutTools(state.items), item]` becomes `items: [...state.items, item]`, and its comment ends at "the agent is parked on the gate."
7. Replace `case 'x-optio-closed'` with:

```ts
    case 'x-optio-closed': {
      // Session ended: stop every running counter, background rows included.
      const item: ChatItem = { kind: 'closed', reason: String(ev.reason ?? ''), seq };
      const items = freezeRunning(state.items, eventTime(ev, now), true);
      return { ...state, items: [...items, item], busy: false, closed: true };
    }
```

8. Delete the `withoutTools` function. Replace the comment block above `insertBeforePending` (it still describes tool announcements as ephemeral) with its first paragraph only (ending at "must not pull later, unrelated user events above newer content."), and change the `isTail` comment's middle sentence to: "Tool rows don't count: they are progress rows, not newer conversation content."

- [ ] **Step 6: Run the tests to verify they pass**

Run: `cd ~/deai/optio/packages/optio-conversation-ui && pnpm exec vitest run src/__tests__/claudecode-events.test.ts`
Expected: all pass. `grep -n withoutTools src/claudecode/events.ts` prints nothing.

- [ ] **Step 7: Typecheck and commit**

```bash
cd ~/deai/optio/packages/optio-conversation-ui && pnpm build
cd ~/deai/optio
git add packages/optio-conversation-ui/src/chat.ts packages/optio-conversation-ui/src/claudecode/events.ts packages/optio-conversation-ui/src/__tests__/claudecode-events.test.ts
git commit -m "fix(optio-conversation-ui): real tool rows for Claude Code

Claude Code tool rows were transient (dropped by the next reply, tool,
permission card, result or close), never got a status and never showed a
result, so the tool_verbosity levels could not behave as documented. Rows now
persist, carry the tool_use id, and a tool_result sets done/failed, the
trimmed result and endedAt. Rows get startedAt from the event timestamp; the
end of a turn and session close freeze running rows instead of removing them."
```

---

### Task 4: Background tasks (reducer)

**Files:**
- Modify: `packages/optio-conversation-ui/src/chat.ts` (`ChatState`)
- Modify: `packages/optio-conversation-ui/src/claudecode/events.ts`
- Test: `packages/optio-conversation-ui/src/__tests__/claudecode-events.test.ts`

**Interfaces:**
- Consumes (Tasks 2-3): `replaceAt`, `ToolItem`, `eventTime`, `trimResult`, `applyToolResults` (honours `background`), `freezeRunning`.
- Produces:
  - `ChatState` gains `finishedTaskIds?: string[]` (reducer-private).
  - In `events.ts` (module-private): `interface TaskNotice { taskId: string; toolUseId: string | null; status: string; summary: string }`, `tagValue(xml, tag): string | null`, `parseTaskNotification(text): TaskNotice | null`, `applyTaskNotice(state, notice, at, seq): ChatState`.

- [ ] **Step 1: Write the failing tests**

Add builders:

```ts
const taskStarted = (taskId: string, toolUseId: string) => ({ type: 'system', subtype: 'task_started', task_id: taskId, tool_use_id: toolUseId, description: 'bg job', is_backgrounded: true, task_type: 'local_bash' });
const taskNotification = (taskId: string, toolUseId: string, status: string, summary: string, timestamp?: string) => ({ type: 'system', subtype: 'task_notification', task_id: taskId, tool_use_id: toolUseId, status, output_file: '/tmp/x.output', summary, timestamp });
const injectedNotification = (taskId: string, toolUseId: string, status: string, summary: string) => ({
  type: 'user',
  origin: { kind: 'task-notification' },
  message: {
    role: 'user',
    content: `<task-notification>\n<task-id>${taskId}</task-id>\n<tool-use-id>${toolUseId}</tool-use-id>\n<output-file>/tmp/x.output</output-file>\n<status>${status}</status>\n<summary>${summary}</summary>\n</task-notification>`,
  },
});
```

Add a new `describe` block:

```ts
describe('background tasks', () => {
  const started = [
    toolCall('t1', 'Bash', { command: './harness.sh --lean' }, T0),
    taskStarted('b1', 't1'),
    toolResult('t1', 'Command running in background with ID: b1', false, T5),
  ];

  it('a backgrounded call stays running after its immediate tool_result', () => {
    const [row] = ofKind(run(started), 'tool');
    expect(row.background).toBe(true);
    expect(row.status).toBe('running');
    expect(row.endedAt).toBeUndefined();
  });

  it('task_notification finishes the row with the summary and its time', () => {
    const s = run([...started, taskNotification('b1', 't1', 'completed', 'Lean-verify all 15', '2026-09-12T10:06:12.000Z')]);
    const [row] = ofKind(s, 'tool');
    expect(row).toMatchObject({ status: 'done', result: 'Lean-verify all 15', endedAt: Date.parse('2026-09-12T10:06:12.000Z') });
  });

  it('a failed task marks the row failed', () => {
    const s = run([...started, taskNotification('b1', 't1', 'failed', 'Build test venv')]);
    expect(ofKind(s, 'tool')[0].status).toBe('failed');
  });

  it('an injected <task-notification> turn finishes the row and adds no user bubble', () => {
    const s = run([...started, injectedNotification('b1', 't1', 'completed', 'Rewrite harness')]);
    expect(ofKind(s, 'user')).toEqual([]);
    expect(ofKind(s, 'tool')[0]).toMatchObject({ status: 'done', result: 'Rewrite harness' });
  });

  it('the same task reported by both routes applies once', () => {
    const s = run([
      ...started,
      taskNotification('b1', 't1', 'completed', 'first report'),
      injectedNotification('b1', 't1', 'completed', 'second report'),
    ]);
    expect(ofKind(s, 'tool')[0].result).toBe('first report');
    expect(ofKind(s, 'activity')).toEqual([]);
    expect(s.finishedTaskIds).toEqual(['b1']);
  });

  it('without a matching row, a muted activity row reports the finished task', () => {
    const ok = run([taskNotification('b9', 't9', 'completed', 'Nightly export')]);
    expect(ofKind(ok, 'activity').map((a) => a.text)).toEqual(['✓ Background task finished: Nightly export']);
    const bad = run([injectedNotification('b8', 't8', 'failed', 'Nightly import')]);
    expect(ofKind(bad, 'activity').map((a) => a.text)).toEqual(['✗ Background task failed: Nightly import']);
  });

  it('the end of the turn does not freeze a background row; session close does', () => {
    const mid = run([...started, result('started it')]);
    expect(ofKind(mid, 'tool')[0].endedAt).toBeUndefined();
    const closed = reduceEvent(mid, { type: 'x-optio-closed', reason: 'stopped' }, 99, 777);
    expect(ofKind(closed, 'tool')[0].endedAt).toBe(777);
  });

  it('task_started arriving after the tool_result reopens the row', () => {
    const s = run([
      toolCall('t1', 'Bash', {}, T0),
      toolResult('t1', 'Command running in background with ID: b1', false, T5),
      taskStarted('b1', 't1'),
    ]);
    const [row] = ofKind(s, 'tool');
    expect(row).toMatchObject({ background: true, status: 'running' });
    expect(row.endedAt).toBeUndefined();
    expect(row.result).toBeUndefined();
  });

  it('other system events are still ignored', () => {
    const s = run([{ type: 'system', subtype: 'status' }, { type: 'system', subtype: 'thinking_tokens' }]);
    expect(s).toEqual(initialChatState);
  });
});
```

- [ ] **Step 2: Run them to verify they fail**

Run: `cd ~/deai/optio/packages/optio-conversation-ui && pnpm exec vitest run src/__tests__/claudecode-events.test.ts`
Expected: the `background tasks` tests FAIL (system events ignored; the injected turn becomes a user bubble), except "other system events are still ignored", which passes already.

- [ ] **Step 3: Extend `ChatState` in `chat.ts`**

```ts
export interface ChatState {
  items: ChatItem[];
  busy: boolean;
  closed: boolean;
  controls: SessionControl[];
  // Reducer-private (claudecode): background task ids already applied, so the
  // system event and the injected notification turn for one task apply once.
  finishedTaskIds?: string[];
}
```

(`initialChatState` stays unchanged: the field is absent until a task finishes.)

- [ ] **Step 4: Add the notification helpers to `events.ts`**

After `freezeRunning` add:

```ts
interface TaskNotice {
  taskId: string;
  toolUseId: string | null;
  status: string;
  summary: string;
}

function tagValue(xml: string, tag: string): string | null {
  const m = xml.match(new RegExp(`<${tag}>([\\s\\S]*?)</${tag}>`));
  return m ? m[1].trim() : null;
}

// The <task-notification> element the CLI injects as a user turn when a
// background command ends.
function parseTaskNotification(text: string): TaskNotice | null {
  const taskId = tagValue(text, 'task-id');
  if (!taskId) return null;
  return {
    taskId,
    toolUseId: tagValue(text, 'tool-use-id'),
    status: tagValue(text, 'status') ?? '',
    summary: tagValue(text, 'summary') ?? '',
  };
}

// A background task ended (system/task_notification or an injected
// <task-notification> turn): finish its Bash row, or add a muted activity row
// when no row exists (e.g. a replay without the call). Each task applies once.
function applyTaskNotice(state: ChatState, n: TaskNotice, at: number, seq: number): ChatState {
  const seen = state.finishedTaskIds ?? [];
  if (seen.includes(n.taskId)) return state;
  const failed = n.status === 'failed';
  const idx = n.toolUseId
    ? state.items.findIndex((i) => i.kind === 'tool' && i.callId === n.toolUseId)
    : -1;
  let items: ChatItem[];
  if (idx !== -1) {
    const row = state.items[idx] as ToolItem;
    items = replaceAt(state.items, idx, {
      ...row,
      background: true,
      status: failed ? 'failed' : 'done',
      result: trimResult(n.summary),
      endedAt: at,
    });
  } else {
    const text = failed
      ? `✗ Background task failed: ${n.summary}`
      : `✓ Background task finished: ${n.summary}`;
    items = [...state.items, { kind: 'activity', text, seq }];
  }
  return { ...state, items, finishedTaskIds: [...seen, n.taskId] };
}
```

- [ ] **Step 5: Handle the events**

1. In `case 'user'`, right after the `withResults` lines from Task 3, add:

```ts
      // A background command ended and the CLI injected its notification as a
      // user turn: apply it to the Bash row; never render it as a user bubble.
      const rawText = extractText(ev.message?.content);
      if (ev.origin?.kind === 'task-notification' || rawText.trimStart().startsWith('<task-notification>')) {
        const notice = parseTaskNotification(rawText);
        return notice ? applyTaskNotice(state, notice, eventTime(ev, now), seq) : state;
      }
```

and change the next line to reuse it: `const { text, uploads } = parseUploadNotice(rawText);`

2. Add a `case 'system'` before `default:`:

```ts
    case 'system': {
      // Background shell commands: task_started marks the Bash row (its
      // immediate tool_result then does not finish it); task_notification ends
      // it. Other system subtypes (init, status, thinking_tokens, ...) are
      // ignored; the model fold above already read init's model.
      if (ev.subtype === 'task_started' && ev.is_backgrounded && typeof ev.tool_use_id === 'string') {
        if (state.finishedTaskIds?.includes(String(ev.task_id))) return state;
        const idx = state.items.findIndex((i) => i.kind === 'tool' && i.callId === ev.tool_use_id);
        if (idx === -1) return state;
        const next: ToolItem = { ...(state.items[idx] as ToolItem), background: true, status: 'running' };
        delete next.endedAt;
        delete next.result;
        return { ...state, items: replaceAt(state.items, idx, next) };
      }
      if (ev.subtype === 'task_notification' && typeof ev.task_id === 'string') {
        return applyTaskNotice(
          state,
          {
            taskId: ev.task_id,
            toolUseId: typeof ev.tool_use_id === 'string' ? ev.tool_use_id : null,
            status: String(ev.status ?? ''),
            summary: String(ev.summary ?? ''),
          },
          eventTime(ev, now),
          seq,
        );
      }
      return state;
    }
```

3. Update the `default:` comment to `// x-optio-unparseable, unknown control traffic, etc.`

- [ ] **Step 6: Run the tests to verify they pass**

Run: `cd ~/deai/optio/packages/optio-conversation-ui && pnpm exec vitest run src/__tests__/claudecode-events.test.ts`
Expected: all pass, including the pre-existing "unhandled event types are ignored" case.

- [ ] **Step 7: Typecheck and commit**

```bash
cd ~/deai/optio/packages/optio-conversation-ui && pnpm build
cd ~/deai/optio
git add packages/optio-conversation-ui/src/chat.ts packages/optio-conversation-ui/src/claudecode/events.ts packages/optio-conversation-ui/src/__tests__/claudecode-events.test.ts
git commit -m "fix(optio-conversation-ui): show Claude Code background-task outcomes

Background shell commands were invisible (system task_* events ignored) or
showed up as raw <task-notification> XML in a user bubble. task_started keeps
the Bash row running past its immediate tool_result; task_notification or the
injected notification turn finishes it with the summary (each task applied
once); with no matching row a muted activity row reports it."
```

---

### Task 5: Elapsed counter, result block and silent background line (view)

**Files:**
- Create: `packages/optio-conversation-ui/src/duration.ts`
- Create: `packages/optio-conversation-ui/src/__tests__/duration.test.ts`
- Modify: `packages/optio-conversation-ui/src/ConversationView.tsx` (imports, component state, `case 'tool'`)
- Test: `packages/optio-conversation-ui/src/__tests__/conversation-view.test.tsx`
- Test: `packages/optio-conversation-ui/src/__tests__/claudecode-widget.test.tsx`

**Interfaces:**
- Consumes (Tasks 3-4): tool item fields `startedAt`, `endedAt`, `result`, `background`, `status`; the reducer behaviour end to end (widget tests).
- Produces: `export function formatDuration(ms: number): string` in `src/duration.ts`; DOM test ids `tool-elapsed`, `tool-result`, `background-finished`.

- [ ] **Step 1: Write the failing tests**

Create `src/__tests__/duration.test.ts`:

```ts
import { describe, it, expect } from 'vitest';
import { formatDuration } from '../duration.js';

describe('formatDuration', () => {
  it.each([
    [0, '0s'],
    [-5, '0s'],
    [42_000, '42s'],
    [59_999, '59s'],
    [60_000, '1m 00s'],
    [184_000, '3m 04s'],
    [3_599_000, '59m 59s'],
    [3_600_000, '1h 00m'],
    [4_020_000, '1h 07m'],
  ])('%i ms -> %s', (ms, expected) => {
    expect(formatDuration(ms)).toBe(expected);
  });
});
```

In `conversation-view.test.tsx`, change the imports to

```ts
import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest';
import { render, screen, fireEvent, waitFor, act } from '@testing-library/react';
```

and append:

```ts
describe('ConversationView tool rows: elapsed time, results, background jobs', () => {
  afterEach(() => vi.useRealTimers());

  it('a running timed row shows a live elapsed counter', () => {
    vi.useFakeTimers();
    vi.setSystemTime(100_000);
    const state = makeState([{ kind: 'tool', name: 'Bash', input: { command: 'sleep 99' }, seq: 1, startedAt: 88_000 }]);
    renderView(makeProps({ state, toolVerbosity: 'description-only' }));
    expect(screen.getByTestId('tool-elapsed').textContent).toContain('12s');
    act(() => {
      vi.advanceTimersByTime(3000);
    });
    expect(screen.getByTestId('tool-elapsed').textContent).toContain('15s');
  });

  it('a finished row shows its final duration and stops counting', () => {
    vi.useFakeTimers();
    vi.setSystemTime(500_000);
    const state = makeState([
      { kind: 'tool', name: 'Bash', input: { command: 'make' }, seq: 1, status: 'done', startedAt: 0, endedAt: 184_000 },
    ]);
    renderView(makeProps({ state, toolVerbosity: 'description-only' }));
    expect(screen.getByTestId('tool-elapsed').textContent).toContain('3m 04s');
    act(() => {
      vi.advanceTimersByTime(5000);
    });
    expect(screen.getByTestId('tool-elapsed').textContent).toContain('3m 04s');
  });

  it('rows without startedAt show no counter', () => {
    renderView(makeProps({ state: makeState([{ kind: 'tool', name: 'Bash', input: {}, seq: 1 }]) }));
    expect(screen.queryByTestId('tool-elapsed')).toBeNull();
  });

  it('verbose shows the result under the args once the row is expanded', () => {
    const state = makeState([
      { kind: 'tool', name: 'Bash', input: { command: 'ls' }, seq: 1, status: 'done', result: 'file1', startedAt: 0, endedAt: 1000 },
    ]);
    renderView(makeProps({ state, toolVerbosity: 'verbose' }));
    expect(screen.queryByTestId('tool-result')).toBeNull(); // finished -> collapsed
    fireEvent.click(screen.getByText('Bash'));
    expect(screen.getByTestId('tool-result').textContent).toBe('file1');
  });

  it('silent hides tool rows but keeps a finished background job as one muted line', () => {
    const state = makeState([
      { kind: 'tool', name: 'Bash', input: { command: 'ls' }, seq: 1, status: 'done', startedAt: 0, endedAt: 1000 },
      { kind: 'tool', name: 'Bash', input: { command: './harness.sh' }, seq: 2, status: 'done', background: true, result: 'Lean-verify all 15', startedAt: 0, endedAt: 372_000 },
    ]);
    renderView(makeProps({ state, toolVerbosity: 'silent' }));
    expect(screen.queryByTestId('tool-call')).toBeNull();
    const line = screen.getByTestId('background-finished').textContent!;
    expect(line).toContain('Background task finished: Lean-verify all 15');
    expect(line).toContain('6m 12s');
  });
});
```

In `claudecode-widget.test.tsx`, append inside the `describe('ConversationWidget', ...)` block (after the last `it`):

```ts
  it('narration from a thinking block renders as a reply even with thinkingVerbosity hidden', () => {
    render(<ConversationWidget {...makeProps({ process: { _id: 'p1', name: 'n', widgetData: { thinkingVerbosity: 'hidden' }, status: { state: 'running' } } })} />);
    fire({ type: 'assistant', message: { role: 'assistant', id: 'm1', content: [{ type: 'thinking', thinking: '', signature: 's' }] } });
    fire({ type: 'assistant', message: { role: 'assistant', id: 'm1', content: [{ type: 'thinking', thinking: 'Checking the VPN before the harness run.', signature: 's' }] } });
    expect(screen.getByText('Checking the VPN before the harness run.')).toBeTruthy();
  });

  it('description-only keeps one row per call and marks finished calls', () => {
    render(<ConversationWidget {...propsV('description-only')} />);
    fire({ type: 'assistant', message: { role: 'assistant', content: [{ type: 'tool_use', id: 't1', name: 'Bash', input: { command: 'ls' } }] } });
    fire({ type: 'user', message: { role: 'user', content: [{ type: 'tool_result', tool_use_id: 't1', content: 'a b', is_error: false }] } });
    fire({ type: 'assistant', message: { role: 'assistant', content: [{ type: 'tool_use', id: 't2', name: 'Read', input: { file_path: '/x' } }] } });
    const rows = screen.getAllByTestId('tool-call');
    expect(rows).toHaveLength(2);
    expect(rows[0].getAttribute('data-tool-status')).toBe('done');
    expect(rows[1].getAttribute('data-tool-status')).toBe('running');
  });

  it('a background task notification never renders as a user bubble', () => {
    render(<ConversationWidget {...propsV('description-only')} />);
    fire({ type: 'assistant', message: { role: 'assistant', content: [{ type: 'tool_use', id: 't1', name: 'Bash', input: { command: './harness.sh' } }] } });
    fire({ type: 'system', subtype: 'task_started', task_id: 'b1', tool_use_id: 't1', is_backgrounded: true });
    fire({ type: 'user', origin: { kind: 'task-notification' }, message: { role: 'user', content: '<task-notification>\n<task-id>b1</task-id>\n<tool-use-id>t1</tool-use-id>\n<status>completed</status>\n<summary>Rewrite harness</summary>\n</task-notification>' } });
    expect(screen.queryByText(/task-notification/)).toBeNull();
    expect(screen.getByTestId('tool-call').getAttribute('data-tool-status')).toBe('done');
  });
```

- [ ] **Step 2: Run them to verify they fail**

Run: `cd ~/deai/optio/packages/optio-conversation-ui && pnpm exec vitest run src/__tests__/duration.test.ts src/__tests__/conversation-view.test.tsx src/__tests__/claudecode-widget.test.tsx`
Expected: `duration.test.ts` fails to import `../duration.js`; the new view tests FAIL (no `tool-elapsed`, `tool-result` or `background-finished` elements). The three new widget tests PASS already (Tasks 2-4 did the reducer work); they guard the end-to-end wiring. All pre-existing tests pass.

- [ ] **Step 3: Create `src/duration.ts`**

```ts
// Compact elapsed time for tool rows: "42s", "3m 04s", "1h 07m".
export function formatDuration(ms: number): string {
  const total = Math.max(0, Math.floor(ms / 1000));
  if (total < 60) return `${total}s`;
  const seconds = total % 60;
  const hours = Math.floor(total / 3600);
  if (hours === 0) return `${Math.floor(total / 60)}m ${String(seconds).padStart(2, '0')}s`;
  const minutes = Math.floor(total / 60) % 60;
  return `${hours}h ${String(minutes).padStart(2, '0')}m`;
}
```

- [ ] **Step 4: Add the ticking clock to `ConversationView`**

Add the import after the `FileDownloadContext` import:

```ts
import { formatDuration } from './duration.js';
```

After the `toggleTool` definition (~line 336) add:

```ts
  // Live elapsed counters: tick once a second while any timed tool row is
  // still running (no endedAt); no interval otherwise.
  const [now, setNow] = useState(() => Date.now());
  const counting = state.items.some(
    (i) => i.kind === 'tool' && i.startedAt !== undefined && i.endedAt === undefined,
  );
  useEffect(() => {
    if (!counting) return;
    setNow(Date.now());
    const timer = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(timer);
  }, [counting]);
```

- [ ] **Step 5: Render elapsed time, the result block and the silent background line**

Replace `case 'tool'` in the item renderer with:

```tsx
      case 'tool': {
        const { finished, failed } = toolLifecycle(item);
        const elapsed =
          item.startedAt !== undefined ? formatDuration((item.endedAt ?? now) - item.startedAt) : null;
        if (toolVerbosity === 'silent') {
          // A finished background job is an event, not tool noise: keep one
          // muted line for it even when tool rows are hidden.
          if (!(item.background && finished)) return null;
          return (
            <div key={item.seq} data-testid="background-finished" style={{ color: token.colorTextTertiary, fontSize: 12 }}>
              {failed ? '✗' : '✓'} Background task {failed ? 'failed' : 'finished'}: {item.result ?? item.name}
              {elapsed ? ` · ${elapsed}` : ''}
            </div>
          );
        }
        // description-while-active: only render WHILE the tool runs.
        if (toolVerbosity === 'description-while-active' && finished) return null;

        let summary = toolSummary(item.input);
        if (!summary && item.preview) summary = item.preview.split('\n')[0].slice(0, 120);
        const glyph = !finished ? '⟳' : failed ? '✗' : '✓';

        // verbose shows the args/result detail; a FINISHED verbose tool collapses
        // to its line (click to re-expand). Non-verbose levels are line-only.
        const collapsible = toolVerbosity === 'verbose' && finished;
        const open = toolVerbosity === 'verbose' && (!finished || expandedTools.has(item.seq));
        return (
          <div key={item.seq} data-testid="tool-call" data-tool-status={finished ? (failed ? 'failed' : 'done') : 'running'}
               style={{ color: token.colorTextTertiary, fontSize: 12 }}>
            <div
              style={{ fontFamily: 'monospace', cursor: collapsible ? 'pointer' : 'default' }}
              onClick={collapsible ? () => toggleTool(item.seq) : undefined}
            >
              {glyph} <strong>{item.name}</strong>{summary ? `: ${summary}` : ''}
              {item.background && finished && item.result ? ` · ${item.result}` : ''}
              {elapsed ? <span data-testid="tool-elapsed">{` · ${elapsed}`}</span> : null}
              {collapsible ? <span style={{ marginLeft: 6 }}>{expandedTools.has(item.seq) ? '▾' : '▸'}</span> : null}
            </div>
            {open ? renderDetail(item.input, item.preview, token) : null}
            {open && item.result ? (
              <div
                data-testid="tool-result"
                style={{ fontFamily: 'monospace', fontSize: 12, color: token.colorTextSecondary, whiteSpace: 'pre-wrap', overflowWrap: 'anywhere', marginTop: 4 }}
              >
                {item.result}
              </div>
            ) : null}
          </div>
        );
      }
```

- [ ] **Step 6: Run the tests to verify they pass**

Run: `cd ~/deai/optio/packages/optio-conversation-ui && pnpm exec vitest run src/__tests__/duration.test.ts src/__tests__/conversation-view.test.tsx src/__tests__/claudecode-widget.test.tsx`
Expected: all pass.

- [ ] **Step 7: Full verification**

```bash
cd ~/deai/optio/packages/optio-conversation-ui && pnpm test 2>&1 | tail -4 && pnpm build
cd ~/deai/optio && .venv/bin/pytest -q -p no:cacheprovider packages/optio-claudecode/tests/test_host_actions.py 2>&1 | tail -2
```

Expected: the UI suite passes with the Task 1 baseline count plus all new tests (no failures); `pnpm build` exits 0; the Python file passes. If any other engine's tests fail, stop: the shared `ChatItem`/`ConversationView` changes must be additive.

- [ ] **Step 8: Commit**

```bash
cd ~/deai/optio
git add packages/optio-conversation-ui/src/duration.ts packages/optio-conversation-ui/src/__tests__/duration.test.ts packages/optio-conversation-ui/src/ConversationView.tsx packages/optio-conversation-ui/src/__tests__/conversation-view.test.tsx packages/optio-conversation-ui/src/__tests__/claudecode-widget.test.tsx
git commit -m "feat(optio-conversation-ui): elapsed time, results and background jobs in tool rows

Timed tool rows show a live elapsed counter (one interval while any row runs)
and keep their final duration; verbose shows a finished call's result under
its args; with silent verbosity a finished background job still gets one
muted line."
```
