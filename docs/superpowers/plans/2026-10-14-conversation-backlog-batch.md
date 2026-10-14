# Live conversation: the backlog in one step — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A live claudecode conversation shows its history in one step (host's waiting card, then the final state), and a resume keeps the saved view until the live view is really ready.

**Architecture:** The claudecode listener frames each connection's replay with named SSE events `backlog-start` / `backlog-end`. `ClaudeCodeView` collects between them and applies the batch in one dispatch, showing the host placeholder until the first end and calling `WidgetProps.onReady`. optio-ui's `useProcessWidgetState` mounts a resumed process's live widget hidden next to the suspended variant and switches on `onReady` (or a 10 s safety net), using keyed slots so the live widget is never remounted.

**Tech Stack:** Python 3 / aiohttp (optio-claudecode), React 18 + TypeScript + vitest + Testing Library (optio-ui, optio-conversation-ui), pytest.

**Spec:** `docs/2026-10-14-conversation-backlog-batch-design.md` (optio repo, branch `csillag/backlog-batch`).

## Global Constraints

- Markers, verbatim: `event: backlog-start` + `data: {"after": <Last-Event-ID resumed after, 0 for none>}`; `event: backlog-end` + `data: {"seq": <last replayed seq, or "after" when none>}`. Neither has an `id:` line. Both are sent on every connection, also with nothing between them.
- View fallback: 2000 ms without `backlog-end`, armed by the `EventSource` `open` event, the first message, or a `backlog-start`; never by creating the `EventSource`.
- Handover safety net: 10 000 ms after the hidden live widget mounted.
- Slots: a shown slot is a `div` with `display: contents`; a hidden slot a `div` with `display: none`, `inert` and `aria-hidden="true"`. Keys: `suspended`, `live`, `placeholder`.
- Host: excavator; repos `~/deai/optio` and `~/deai/excavator` are the live checkouts (owner-approved). TS edits hot-reload into the owner's browser. Any Python edit under `~/deai/optio` restarts the engine: Python work happens in the worktree `~/optio-backlog`, and lands only with the owner's go-ahead.
- Never run `pnpm` in `~/deai`; call package-local binaries (`./node_modules/.bin/vitest`, `./node_modules/.bin/tsc`).
- Run the affected test files only, never whole suites.
- Commits: conventional prefix (`feat(optio-ui): ...`), no `Co-Authored-By` trailer (deai repos forbid it). Nothing is pushed.

## Review Focus

1. A process that is suspended again, or fails, while its live widget is mounted hidden: the hidden live widget goes away and the host sees the suspended variant (or nothing), never a stuck handover. Tests in Task 3.
2. `onReady` called late (after the reveal, after a new suspend) or twice: no effect, and a later resume still waits for its own `onReady`. Test in Task 3.
3. The host re-renders during the handover with a new placeholder element: the live widget is not remounted. Test in Task 3.
4. An `EventSource` reconnect after the view went live: the conversation stays on screen, the catch-up lands in one step, `onReady` is not called again. Test in Task 4.
5. A listener without markers: the first message goes live at once (as today) and `onReady` is called once. Test in Task 4.

---

### Task 1: claudecode listener frames the replay

**Files:**
- Modify: `packages/optio-claudecode/src/optio_claudecode/conversation_listener.py` (`_handle_events`)
- Test: `packages/optio-claudecode/tests/test_conversation_listener.py`
- Modify (test helper): `packages/optio-claudecode/tests/test_conversation_ui_session.py` (its SSE frame parser near line 205)

**Interfaces:**
- Produces: the wire format in Global Constraints. Later tasks rely only on it.

- [ ] **Step 1: Worktree on the branch**

`~/deai/optio` is detached on another branch for the owner's test, so `csillag/backlog-batch` is free:
`git -C ~/deai/optio worktree add ~/optio-backlog csillag/backlog-batch`.
Test command used below (run in `~/optio-backlog/packages/optio-claudecode`):
`PYTHONPATH=src:../optio-agents/src ~/deai/optio/.venv/bin/python -m pytest -q -p no:cacheprovider <files>`

- [ ] **Step 2: Keep the existing helpers about data events**

Both SSE parsers (`_read_events` in `test_conversation_listener.py`, the frame parser in `test_conversation_ui_session.py`) skip any frame that has an `event:` line, so existing tests keep counting data events only. Add `_read_frames(resp, n) -> list[tuple[str | None, str | None, dict]]` to `test_conversation_listener.py`: (event name or None, id or None, parsed data) for the next n frames, pings skipped.

- [ ] **Step 3: Write the failing tests** (in `test_conversation_listener.py`)

```python
async def test_backlog_markers_frame_the_replay(listener):
    conv, lst, url = listener
    conv.fire({"type": "user", "n": 1}); conv.fire({"type": "result", "n": 2})
    async with aiohttp.ClientSession() as s:
        async with s.get(f"{url}/events", headers=_auth("pw")) as resp:
            frames = await _read_frames(resp, 4)
            assert [(e, i) for e, i, _ in frames] == [("backlog-start", None), (None, "1"), (None, "2"), ("backlog-end", None)]
            assert frames[0][2] == {"after": 0} and frames[3][2] == {"seq": 2}
            conv.fire({"type": "result", "n": 3})
            assert (await _read_frames(resp, 1))[0][:2] == (None, "3")

async def test_backlog_markers_with_an_empty_buffer(listener):
    # frames == [("backlog-start", None, {"after": 0}), ("backlog-end", None, {"seq": 0})]

async def test_backlog_markers_on_a_reconnect(listener):
    # two events, Last-Event-ID "1": [("backlog-start", None, {"after": 1}), (None, "2", ...), ("backlog-end", None, {"seq": 2})]
```

- [ ] **Step 4: Run them to verify they fail**

Expected: the three new tests FAIL (no `backlog-start` frame); the rest of both files PASS.

- [ ] **Step 5: Implement in `_handle_events`**

Write `event: backlog-start\ndata: {json.dumps({"after": last_id})}\n\n` after subscribing and before the replay loop, and `event: backlog-end\ndata: {json.dumps({"seq": sent_through})}\n\n` right after it. Nothing else changes.

- [ ] **Step 6: Run `tests/test_conversation_listener.py tests/test_conversation_ui_session.py`**

Expected: all PASS.

- [ ] **Step 7: Commit** (in `~/optio-backlog`, on `csillag/backlog-batch`)

```bash
git add packages/optio-claudecode
git commit -m "feat(optio-claudecode): frame each connection's replay with backlog-start / backlog-end"
```

### Task 2: Bring the checkouts back and land the listener (owner go-ahead)

**Files:** none.

- [ ] **Step 1: Wait for the owner** to finish the `csillag/workdir-capture-cap` live test and give the go-ahead: the switch restarts the engine once (their branch and Task 1 are Python).
- [ ] **Step 2:** `git -C ~/deai/optio worktree remove ~/optio-backlog`, then `git -C ~/deai/optio checkout csillag/backlog-batch` and `git -C ~/deai/excavator checkout csillag/widget-placeholder`.
- [ ] **Step 3: Verify** the engine log shows `Shutdown complete`, then `Source tools MCP server:`; `sh /tmp/excavator_login_check.sh` prints `sign-in 200`; Vite's checker prints `Found 0 errors`. `python3 /tmp/wp_measure_events.py <a live conversation's _id> 10`, extended to print named events, shows `backlog-start` first and `backlog-end` after the replay. (The current view ignores them.)

### Task 3: optio-ui keeps the saved view until the live one is ready

**Files:**
- Modify: `packages/optio-ui/src/widgets/registry.ts` (`WidgetProps`)
- Modify: `packages/optio-ui/src/components/ProcessWidget.tsx` (`useProcessWidgetState`)
- Test: `packages/optio-ui/src/__tests__/useProcessWidget.test.tsx`

**Interfaces:**
- Produces: `WidgetProps.onReady?: () => void` (given to the live widget only during a resume handover); `UseProcessWidgetStateResult` unchanged in shape and modes; `widget` is now always a slots element (keys `suspended` | `live` | `placeholder`).
- Consumes: `options.placeholder` (already on the branch).

- [ ] **Step 1: Write the failing tests** (a new `describe('useProcessWidgetState: the resume handover', ...)`; a Host component renders the hook's `widget`; the live test widget counts its mounts in a `useEffect` and exposes the `onReady` it got)

- `it('mounts the live widget hidden next to the shown suspended variant')`: suspended → resumed with `widgetData`: mode `'resuming'`; the suspended widget's slot has `style.display === 'contents'`, the live widget's slot `display: none` with `inert`; the live widget got a function `onReady`.
- `it('onReady shows the live widget without remounting it and drops the suspended one')`: after `act(() => onReady())`: mode `'live'`, suspended gone, live slot `display: contents`, mounts `=== 1`.
- `it('shows the live widget after 10 s without onReady')`: fake timers; at 9 999 ms still `'resuming'`, at 10 000 ms `'live'`, mounts `=== 1`.
- `it('a late onReady changes nothing, and the next resume waits for its own')` (Review Focus 2).
- `it('suspended again during the handover: the suspended variant, no hidden live widget')` and `it('failed during the handover: nothing')` (Review Focus 1).
- `it('a new placeholder element during the handover does not remount the live widget')` (Review Focus 3).
- `it('gives onReady only during a handover')`: a live widget shown without a prior suspended view gets no `onReady` prop.

- [ ] **Step 2: Run them to verify they fail**

Run: `cd ~/deai/optio/packages/optio-ui && ./node_modules/.bin/vitest run src/__tests__/useProcessWidget.test.tsx`
Expected: the new tests FAIL, the existing ones PASS.

- [ ] **Step 3: Implement**

- `WidgetProps.onReady?: () => void` with a doc comment as in the spec.
- In `useProcessWidgetState`, all hooks run before any return: move today's gating into a plain function (no hooks) that also says whether a handover is on. A handover is on when the latch shows this process was shown suspended, the process is widget-live with `widgetData`, and it has not been released yet. Release (by `onReady` or the 10 s timer, set in a `useEffect` keyed on the handover's process id) is recorded in a ref and triggers a re-render (a `useReducer` counter). `onReady` checks that a handover for its process is still on before releasing, so a late call does nothing; the release flag resets whenever the hook returns the suspended variant again.
- A small `WidgetSlots` component renders `{key, node, shown}` slots per Global Constraints; every return path uses it (live: `[live]`, suspended/resuming: `[suspended]`, handover: `[suspended shown, live hidden + onReady]`, waiting: `[placeholder]`).

- [ ] **Step 4: Run the hook tests plus `src/__tests__/ProcessDetailView.test.tsx src/__tests__/spinnerSlot.test.tsx`, then `./node_modules/.bin/tsc --noEmit -p .`**

Expected: all PASS; tsc prints nothing.

- [ ] **Step 5: Excavator still green:** `cd ~/deai/excavator/packages/frontend && ./node_modules/.bin/vitest run src/features/chat` (77 pass).

- [ ] **Step 6: Commit**

```bash
git add packages/optio-ui
git commit -m "feat(optio-ui): a resume keeps the suspended view until the live widget is ready"
```

### Task 4: ClaudeCodeView applies the backlog in one step

**Files:**
- Modify: `packages/optio-conversation-ui/src/claudecode/ClaudeCodeView.tsx`
- Modify: `packages/optio-conversation-ui/src/ConversationWidget.tsx` (`ConversationWidgetBody`)
- Create: `packages/optio-conversation-ui/src/__tests__/claudecode-backlog.test.tsx`
- Modify: the `MockEventSource` class of every existing test that drives `ClaudeCodeView` gets a no-op `addEventListener() {}`: `claudecode-custom-controls`, `claudecode-model-widget`, `claudecode-widget`, `conversation-upload`, `hide-empty-runs-wiring`, `set-busy`, `spinnerSlot`, `switch-bands`, `theme-toggle`, `welcome-x`, `wideness` (`.test.tsx`), and any other file failing with "addEventListener is not a function".

**Interfaces:**
- Consumes: `WidgetProps.onReady` (Task 3), `WidgetProps.placeholder` (on the branch), the wire format (Task 1).

- [ ] **Step 1: Write the failing tests** in `claudecode-backlog.test.tsx`, with a mock `EventSource` that records `addEventListener` handlers and offers `open()`, `emit(ev, seq)` and `emitNamed(name, data)`; render `ClaudeCodeView` inside a React `Profiler` that counts commits.

- `it('collects from backlog-start and applies the backlog in one commit at backlog-end')`: start, three messages: none of their texts in the DOM; `act(() => emitNamed('backlog-end', { seq: 3 }))` → all three present, commits during that act `=== 1`.
- `it('ends in the same state as one dispatch per event')`: same events, markers vs. none; equal conversation text.
- `it('shows the placeholder until the first backlog-end')`, and `it('without a placeholder, the empty conversation until then')`.
- `it('after the end, a message renders at once')`.
- `it('a reconnect keeps the conversation on screen and applies the catch-up at its end')` (Review Focus 4): after the first load, `backlog-start` `{after: 3}`, message 4: messages 1–3 shown, 4 not, no placeholder; at the end 4 shows; `onReady` called once in total.
- `it('a message before any backlog-start goes live at once and calls onReady once')` (Review Focus 5).
- `it('2 s after open without markers: live with what it has')`, `it('2 s after backlog-start without an end: applies what it has')`, `it('creating the EventSource arms no fallback')` (fake timers; 2000 ms boundaries; the last one: no `open()`, 5000 ms later the placeholder is still shown and `onReady` was not called).
- `it('other protocols call onReady when they mount')`: `ConversationWidget` with `widgetData.protocol: 'grok'` and an `onReady` spy → called once.

- [ ] **Step 2: Run to verify they fail**

Run: `cd ~/deai/optio/packages/optio-conversation-ui && ./node_modules/.bin/vitest run src/__tests__/claudecode-backlog.test.tsx`
Expected: FAIL.

- [ ] **Step 3: Implement**

- `ChatAction` gains `{ batch: { ev: unknown; seq: number }[] }`; `chatReducer` folds a batch through `reduceEvent` in order.
- A phase ref (`'connecting' | 'collecting' | 'live'`), a buffer ref, one fallback timer ref (2000 ms, per Global Constraints), and a `loaded` state. `onmessage`: the custom-controls bookkeeping first, as now; then collecting → buffer; connecting → live at once (`loaded`), dispatch; live → dispatch. `backlog-start` → collecting, empty buffer, arm the timer. `backlog-end` or timer → one batch dispatch, live, `loaded`. `onopen` arms the timer while connecting. Clear the timer on close.
- Render the placeholder instead of the conversation while `!loaded && props.placeholder !== undefined` (after all hooks).
- Call `props.onReady` once, in an effect after `loaded` first becomes true (latest callback through a ref).
- `ConversationWidgetBody` calls `props.onReady` once at mount for every protocol other than claudecode.

- [ ] **Step 4: Run the new file, the modified existing files and `src/__tests__/suspendedConversation.test.tsx`, then `./node_modules/.bin/tsc --noEmit -p .`**

Expected: all PASS; tsc prints nothing.

- [ ] **Step 5: Commit**

```bash
git add packages/optio-conversation-ui
git commit -m "feat(optio-conversation-ui): the claudecode view applies its backlog in one step and reports ready"
```

### Task 5: Live check

- [ ] **Step 1:** Vite serves the new modules (`curl -sk https://localhost:5173/@fs/home/csillag/deai/optio/packages/optio-conversation-ui/src/claudecode/ClaudeCodeView.tsx | grep -c backlog-end` ≥ 1); the frontend log shows `Found 0 errors`.
- [ ] **Step 2:** `cd ~/deai/excavator && make lint` passes; `vitest run src/features/chat` passes.
- [ ] **Step 3:** Hand over to the owner for the eye test: open a running chat (waiting card, then the final state, no scrolling build-up); suspend and resume a chat (the saved view stays until the live one appears, already at its end).
