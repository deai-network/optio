# Conversation steering: one-at-a-time delivery of queued messages (Claude Code)

Status: owner-chosen design, 2026-09-15. It amends docs/2026-09-13-conversation-steering-design.md for agents whose busy_send is `joins-next-step` (Claude Code).

## Problem
Claude Code folds every prompt waiting in its native stdin queue into ONE user message when it starts a turn, but injects queued prompts one by one at tool boundaries. So the same queued messages sometimes reach the model separately and sometimes merged. Manual test: #1 and #2 queued, then Interrupt and send #3, gave "#1+#2" folded and #3 separate. The owner wants every message delivered as its own user message, consistently, without giving up Claude Code's native queue (and so its pickup at tool boundaries).

## Facts it rests on (CLI 2.1.270, live probes; see ~/deai/optio-steering/.superpowers/sdd/2026-09-13-conversation-steering-plan-stage1/cli-queue-lifecycle.md (git-ignored scratch), section "Individual delivery")
- No `priority` value prevents the fold at turn start. `now` interrupts the turn and jumps the queue, which breaks the order. `later` is never taken at tool boundaries.
- With at most ONE optio message in the CLI queue at any time, every message arrives as its own user message: with tools (each at the next tool boundary), text-only (each as its own turn), and after an interrupt.
- Messages carrying a uuid get `command_lifecycle` events (queued, started, completed, cancelled, discarded, refused). Fix 13a stamps the steering id as the uuid.

## Design
Steering (optio_agents.steering) keeps an ordered list of messages the operator sent while the agent was busy. For native-queue agents, at most one of them is "in flight", meaning written to the CLI's stdin.
- **Send when ready (busy):** emit x-optio-queued {id, text} as today. If nothing is in flight, write the message to stdin now (uuid = id, trailing blank line) and mark it in flight. Otherwise append it to the pending list.
- **Advance:** when the in-flight message's command_lifecycle reaches `started`, or any final state (completed, cancelled, discarded, refused), write the next pending message and mark it in flight. As a safety net, if a turn ends or the agent goes idle while nothing is in flight and the pending list is not empty, write the next one. This also covers conversations whose CLI emits no lifecycle events.
- **Idle send:** unchanged. Written immediately, and the CLI starts a turn.
- **Send now on message k (`/steer` with empty text and `upTo`):** if a turn is running, interrupt it. The in-flight message runs next, and the rest follow one at a time in order. Messages after k are never in the CLI queue before their predecessors started, so they are taken "when the agent is ready" (the next tool boundary or turn). No cancel or re-send is needed.
- **Interrupt and send (text):** append the text to the list (x-optio-queued), then interrupt. Delivery continues one at a time, so the new text arrives last, as its own message.
- **Interrupt (plain):** unchanged. Anything pending continues one at a time afterwards.
- **Session end:** messages still pending stay undelivered and render "Not delivered", as today.
- The Task 2 / Fix 8 owner rulings stay: at most one x-optio-interrupt per turn, the one deadline, and the flag clearing.

## What goes away
Fix 13a's cancel_async_message + re-send path for `upTo` is no longer used for native-queue agents: the CLI queue never holds more than one message. Remove it from Steering. The UI keeps handling `x-optio-requeued`, because buffers recorded today contain it and replays still need it.

## UI
No behaviour change is expected. Every waiting message shows as a queued bubble until its `started` (Fix 13b). Send now keeps posting `upTo`.

## Costs, accepted
With no tool calls in between, N queued messages take N consecutive turns. A burst needs one tool boundary per message to be absorbed mid-turn.

## Testing
- Steering unit tests with a FakeConversation that emits lifecycle events: one in flight at a time; advancing on started and on each final state; the safety net on turn end or idle without lifecycle; order for Send now on k and for Interrupt and send; pending messages at session end.
- A real-driver test in optio-claudecode against the recorded shapes.
- Live check by the owner: the 3-message scenario (queue #1 and #2, then Interrupt and send #3) gives three separate messages in order.
