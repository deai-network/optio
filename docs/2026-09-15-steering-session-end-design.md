# Conversation steering: session end and resume (Claude Code)

Status: design for owner approval, 2026-09-15. It amends docs/2026-09-13-conversation-steering-design.md and builds on docs/2026-09-15-steering-individual-delivery-design.md (one-at-a-time delivery, Fix 17).

## Problems (manual test, confirmed in the data)
1. A session that ends mid-turn (thinking, streaming or a tool) shows nothing marking the cut-off. A thinking-only turn leaves no bubble at all.
2. After resume, the agent continues the prompt it was working on (the CLI transcript plus --continue), but messages still QUEUED at session end are lost: they lived only in the CLI's memory and in Steering's memory.
3. A message that was streaming at session end is lost entirely: stream events are never buffered, and the CLI is SIGKILLed.

## How a session ends today (see ~/deai/optio-steering/.superpowers/sdd/2026-09-13-conversation-steering-plan-stage1/session-end-lifecycle.md)
Suspend and stop are both lifecycle.cancel. The driver cancels the body (protocol/session.py:382-391), and the body cancels the reader (optio_claudecode/session.py:812). The reader emits x-optio-closed (conversation.py:467) while the CLI is still alive, then the CLI gets an immediate SIGKILL (session.py:890), then export_buffer (session.py:927), then the snapshot. On resume, the CLI relaunches with --continue, the listener appends x-optio-resumed (conversation_listener.py:104), and optio sends "System: you have been resumed" (session.py:687).

## Design
All new events are emitted SYNCHRONOUSLY in the listener before x-optio-closed passes, so they are in the buffer that export_buffer persists.

### 1. Session-end marker (owner ruling: caption "⏹ Interrupted: session ended")
In ConversationListener._on_event (conversation_listener.py:139), just before x-optio-closed is buffered and broadcast, and only if a turn is running (the conversation is pending, or the listener saw a turn start without its result), emit {"type":"x-optio-interrupt","by":"session"}. The reducer handles it like the operator's interrupt marker (the cut-off bubble gets interrupted: true: jagged edge and trailing "…"; a running tool row is marked stopped), but the row reads "⏹ Interrupted: session ended", in the same place and muted style as "⏹ Interrupted by you". No too-late undo applies: no result follows.

### 2. Partial answer (owner ruling: renders like an interruption)
The listener keeps, per message, the text accumulated from the stream_event text deltas of the message being streamed (reset at message_start and at the message's final assistant event). At session end, before the marker from 1, if a partial exists it emits {"type":"x-optio-partial","id":<message id>,"text":<text so far>} (buffered). The reducer treats it as the text of that assistant message and marks it interrupted, so it renders with the jagged edge and "…". Hidden thinking streams empty deltas, so a thinking-only turn has no partial: only the marker row appears. Live and replay agree, because the partial is buffered.

### 3. Re-queue on resume (owner ruling: automatic, in order, no double delivery)
On resume, after the "System: you have been resumed" message is sent, optio computes the undelivered messages from the restored buffer: every x-optio-queued id with no command_lifecycle 'started' and no confirming echo (following x-optio-requeued mappings; 'discarded' counts as undelivered). It re-sends them in their original order through Steering's one-at-a-time delivery (Fix 17), each under a NEW uuid, and emits {"type":"x-optio-requeued","id":<old id>,"new_id":<new uuid>} for each, which the UI already handles (the bubble stays queued and is re-keyed). x-optio-resumed carries the list {"requeued":[old ids]} for the record. Messages the CLI had already started are in its transcript and are NOT re-sent. The x-optio-closed handling that turns queued bubbles into 'Not delivered' must not persist for re-queued ids: after x-optio-requeued, the bubble is queued again.

### 4. Graceful interrupt before the kill (owner ruling: YES)
Today the CLI is SIGKILLed, so the model's transcript has no record of the partial answer. Sending an interrupt control_request (bounded, e.g. 2-3 s) before the kill would let the CLI record the partial, so the model knows what it had said. The cost: seconds out of the 30 s cancel grace shared with the snapshot. OWNER RULING (2026-09-15): YES. At session end, if a turn is running, send the interrupt control_request first and wait for that turn's end (its result) for at most 3 s, then close and SIGKILL as today. The CLI then records the partial in its transcript, so the model knows after resume what it had said. The UI must not depend on this: the marker (1) is always emitted before x-optio-closed while a turn runs, and the partial (2) is emitted only if the message's final assistant event was not seen (the graceful interrupt usually makes the CLI flush it as a real event, so don't duplicate it). If the turn does not end within 3 s, fall back to today's behaviour.

## Testing
- Listener unit tests: the marker is emitted before x-optio-closed only while a turn runs; the partial is emitted with the accumulated text and reset per message; both are present in export_buffer.
- Reducer tests: marker by:"session" gives the new caption, the interrupted bubble and the stopped tool; a partial renders as an interrupted bubble; live == replay.
- Resume tests: undelivered ids are computed correctly (started, echoed, requeued and discarded cases); re-sends are in order, after the resume notice, through one-at-a-time delivery; x-optio-requeued is emitted for each; nothing already started is re-sent.
- Live check by the owner: end a session while thinking, while streaming, and with messages queued; resume; see the row, the partial and the re-queued messages delivered in order.
