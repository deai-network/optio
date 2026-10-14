# Live conversation: the backlog in one step

Design, agreed with the owner on 2026-10-14. It builds on the widget placeholder
(`useProcessWidgetState`'s `placeholder`, `WidgetProps.placeholder`, branch
`csillag/widget-placeholder`).

## What the user gets

Opening a live conversation that already has history looks like opening a suspended one:
the host's waiting card, then the conversation as it stands, scrolled to its end. After
that, new events stream in one by one as today.

A resume hands over from the saved conversation to the live one without anything in
between: the saved view stays on screen until the live view shows the same conversation.

## Why it renders event by event today

- **Listener:** on connect, the claudecode conversation listener replays its buffer (at
  most `BUFFER_MAXLEN` = 1000 events) as one SSE message per event, then tails
  (`optio_claudecode/conversation_listener.py`, `_handle_events`). This is fast: on the
  Plakomm analyzer, 1000 events (1.1 MB) arrived through Vite in 76 ms.
- **View:** `ClaudeCodeView` dispatches each message on its own. The browser delivers
  each SSE message as a separate task, and React cannot batch across tasks, so the
  conversation renders its intermediate states.
- **Scrolling:** `ConversationView`'s ResizeObserver pins to the bottom on every height
  increase, so every intermediate state scrolls.
- **After a resume**, the listener is re-primed with the prior history, so the live view
  that takes over from the saved one replays it the same way, from an empty conversation.

`ClaudeCodeReplayView` (the saved conversation) folds the whole record with the same
reducer in one `useMemo`: one render, one scroll.

## Decisions (owner, 2026-10-14)

1. **claudecode first.** All six SSE agents (claudecode, codex, cursor, kimicode,
   antigravity, grok) share the pattern and get the same fix afterwards.
2. **The listener frames each connection's backlog** with two named SSE events,
   `backlog-start` and `backlog-end` (the owner added the start marker: it makes every
   connection, a reconnect included, describe itself).
3. **The host's waiting card shows until the backlog is applied.**
4. **Seamless resume handover in this version.**

## Design

### 1. The wire protocol

For every connection to `/events`, the listener sends:

```
event: backlog-start
data: {"after": <Last-Event-ID it resumes after, 0 for none>}

id: <seq>
data: <event>            (the replayed events, as today)
...

event: backlog-end
data: {"seq": <seq of the last replayed event, or "after" when none>}

id: <seq>
data: <event>            (live events, as today)
```

- Both markers are always sent, also with nothing in between (a new session, a reconnect
  that is up to date).
- Neither carries an `id:` line, so the browser's `Last-Event-ID` stays on the last real
  event.
- A browser `EventSource` delivers named events only to listeners registered for that
  name: views that do not know the markers are unaffected.
- The order stays subscribe, replay, end, tail. Events that arrive during the replay wait
  in the queue and follow the end marker as live events, deduplicated by seq as today.

### 2. optio-claudecode: the listener

`_handle_events` writes `backlog-start` before the replay loop and `backlog-end` after it.
Nothing else changes.

### 3. optio-conversation-ui: `ClaudeCodeView`

- **Collecting.** The view starts out collecting; a `backlog-start` (a reconnect) sets it
  collecting again. While collecting, messages go to a buffer instead of being
  dispatched. The custom-controls bookkeeping in the message handler runs for every
  message at once, as now.
- **Applying.** On `backlog-end`, one dispatch of `{ batch: [{ ev, seq }, ...] }` folds the
  buffer through `reduceEvent` in order: the state that one dispatch per event would
  give, in one render. Then the view is live: later messages dispatch one by one.
- **What shows.** Until the first `backlog-end`, the host's placeholder
  (`WidgetProps.placeholder`) if there is one, else today's empty conversation. During a
  reconnect's catch-up the conversation stays on screen; the missed events land in one
  step.
- **Ready.** Once the first backlog is applied and rendered, the view calls
  `WidgetProps.onReady` (section 4), if given.
- **Fallback.** If no `backlog-end` follows within 2 s of a start (the stream opening, or a
  `backlog-start`), the view applies what it has and goes live: an older listener, or a
  proxy that drops named events, must not leave the card up forever.
- **Scrolling.** `ConversationView` does not change: the backlog grows it once, and its
  ResizeObserver pins to the bottom once, before the frame is painted.

The other five protocol views do not batch yet; the conversation widget's dispatcher
(`ConversationWidgetBody`) calls `onReady` for them as soon as they mount, so they hand
over at once, as today.

### 4. optio-ui: the resume handover

- **`WidgetProps.onReady?: () => void`.** Given to a live widget only while it takes over
  from the suspended variant. The widget calls it once it shows the process. Absent
  otherwise.
- **`useProcessWidgetState`.** Today, when a resumed process's live widget becomes
  available (widget-live with `widgetData`), the hook switches from `'resuming'` to
  `'live'`. Instead it first mounts the live widget hidden next to the shown suspended
  variant, with `onReady`; the mode stays `'resuming'`. On `onReady`, or 5 s after the live
  widget mounted, it shows the live widget and drops the suspended one (mode `'live'`).
- **No remount.** The live widget must stay mounted across that switch, or it would
  reconnect and replay again. The hook returns its widgets in keyed slots: a shown slot
  is a `display: contents` wrapper (no box, so the host's layout is as today), a hidden
  one `display: none`. React keeps the `live` slot, and the widget in it, from hidden to
  shown. Every mode uses the slots, so the tree is the same throughout.
- **Scrolling at the reveal.** Hidden, the live conversation has no height; revealed, its
  ResizeObserver sees the growth and pins to the bottom before the first paint.
- Hosts that never show a suspended variant see no handover.

## Out of scope

- The other five agents (decision 1).
- `BUFFER_MAXLEN`: a long session's live view and its saved record both start at the
  last ~1000 events.

## Tests

- **Listener (pytest, from a separate optio worktree, so the running engine is not
  restarted):** start, events, end, in that order; an empty buffer still gets both
  markers; a reconnect with `Last-Event-ID` gets `after` and only newer events; the markers
  have no `id:` line; live events follow the end.
- **`ClaudeCodeView` (vitest):** nothing is dispatched before the end; the placeholder
  shows until the first end; one batch gives the same items as one dispatch per event;
  live dispatch after the end; a reconnect keeps the conversation on screen and applies
  the catch-up at its end; the 2 s fallback (fake timers); without a placeholder, the
  empty conversation; `onReady` after the first backlog.
- **`useProcessWidgetState` (vitest):** during a resume, the live widget mounts hidden while
  the suspended variant stays shown and the mode stays `'resuming'`; `onReady` shows the
  live widget without remounting it (a mount counter) and drops the suspended one; the
  5 s timeout; without a handover, hosts render the same widgets as before (a shown slot
  adds no box).
- **Dispatcher:** the other protocol views call `onReady` at mount.
- **Live:** the event-stream measurement shows both markers arriving through Vite; the
  owner's eye test on a data chat and on a resume.

## Rollout

- Local branch `csillag/backlog-batch` on top of `csillag/widget-placeholder`, in the
  live `~/deai/optio` checkout on the excavator host.
- The TypeScript changes hot-reload. The listener is Python: saving it restarts the
  engine once, which saves and resumes the running sessions. That edit lands only with
  the owner's go-ahead.
- No release: Excavator consumes optio main directly.
