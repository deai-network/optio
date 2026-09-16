"""Steering scaffold unit tests against a fake conversation (no sleeps)."""

import asyncio
import inspect
import logging

import pytest

from optio_agents.conversation import Conversation, ConversationClosed
from optio_agents.steering import (
    BUSY_SEND_VALUES,
    BusySendDeclaration,
    SendOutcome,
    Steering,
    resolve_busy_send,
)

QUEUED = "x-optio-queued"
TAKEN = "x-optio-taken"
REQUEUED = "x-optio-requeued"
INTERRUPT = {"type": "x-optio-interrupt", "by": "user"}


class FakeConversation:
    """Records sends, interrupts and synthetic events in one ordered log.

    The test sets ``pending`` (busy) itself. ``interrupt()`` ends the turn
    at once (fires the turn-end event) unless ``turn_end_on_interrupt`` is
    False. That turn end leaves the agent idle, unless
    ``busy_after_interrupt`` is True: then it stays busy, the way Claude
    Code's CLI runs the message still in its queue right after the
    interrupted turn (cli-queue-lifecycle.md, "Interrupt with exactly one
    queued").

    ``uuids`` is a parallel array to ``sent``: the ``uuid`` kwarg (if any)
    ``Steering`` passed to that ``send()`` call — kept separate from ``log``
    so the many pre-existing exact-log assertions below don't have to change
    shape just because sends now carry an id (Fix 13a).

    ``native_lifecycle=True`` wires Steering's ``command_lifecycle`` hook
    (see ``make``); ``fire_lifecycle`` then plays the agent's per-message
    lifecycle events, which drive one-at-a-time delivery (Fix 17)."""

    def __init__(
        self, *, turn_end_on_interrupt: bool = True, native_lifecycle: bool = False,
        busy_after_interrupt: bool = False,
    ):
        self.handlers = []
        self.sent: list[str] = []
        self.uuids: list[str | None] = []
        self.interrupts = 0
        self.pending = False
        self.closed = False
        self.turn_end_on_interrupt = turn_end_on_interrupt
        self.busy_after_interrupt = busy_after_interrupt
        self.log: list[tuple[str, object]] = []
        self.native_lifecycle = native_lifecycle

    def on_event(self, handler):
        self.handlers.append(handler)
        return lambda: self.handlers.remove(handler)

    def fire(self, event: dict) -> None:
        for h in list(self.handlers):
            h(event)

    def fire_lifecycle(self, command_uuid: str, state: str) -> None:
        self.fire({"type": "command_lifecycle", "command_uuid": command_uuid, "state": state})

    def emit(self, event: dict) -> None:  # the wrapper's synthetic-event hook
        self.log.append(("event", event))
        self.fire(event)

    def is_pending(self) -> bool:
        return self.pending

    async def send(self, text: str, *, uuid: str | None = None) -> None:
        if self.closed:
            raise ConversationClosed("closed")
        self.log.append(("send", text))
        self.sent.append(text)
        self.uuids.append(uuid)
        self.pending = True

    async def interrupt(self) -> None:
        if self.closed:
            raise ConversationClosed("closed")
        self.log.append(("interrupt", None))
        self.interrupts += 1
        if self.turn_end_on_interrupt and self.pending:
            self.pending = self.busy_after_interrupt
            self.fire({"type": "turn-end"})


def _lifecycle(event: dict) -> tuple[str, str] | None:
    if event.get("type") != "command_lifecycle":
        return None
    return event["command_uuid"], event["state"]


def make(conv, busy_send="joins-next-step", **kw):
    ids = iter(f"id{n}" for n in range(1, 100))
    extra = {}
    if conv.native_lifecycle:
        extra["command_lifecycle"] = _lifecycle
    return Steering(
        conv,
        busy_send=lambda: busy_send,
        emit=conv.emit,
        is_turn_end=lambda e: e.get("type") == "turn-end",
        new_id=lambda: next(ids),
        **extra,
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

def test_conversation_protocol_send_declares_an_advisory_keyword_only_uuid():
    # Fix 23 review round 1: a fake's send() is never executed as part of
    # the Protocol declaration (a Protocol method body is `...`), so a fake
    # that merely accepts `uuid` proves nothing about the Protocol itself --
    # only that the fake happens to agree with Steering's call shape. Assert
    # the contract on the Protocol's own signature instead: this fails
    # before Fix 23 (send(self, text) -> None, no `uuid` parameter at all)
    # and passes after, independent of any fake.
    params = inspect.signature(Conversation.send).parameters
    assert "uuid" in params
    assert params["uuid"].kind is inspect.Parameter.KEYWORD_ONLY
    assert params["uuid"].default is None
    # Delivery against that exact signature is already covered by
    # test_idle_send_is_a_plain_send_without_events and
    # test_idle_send_uuid_is_the_steering_id_and_gets_no_blank_line below,
    # both of which exercise FakeConversation.send(text, *, uuid=None) --
    # the same shape the Protocol now declares.


@pytest.mark.parametrize("cap", BUSY_SEND_VALUES)
async def test_idle_send_is_a_plain_send_without_events(cap):
    conv = FakeConversation()
    s = make(conv, cap)
    assert await s.send_when_ready("hi") == SendOutcome(id="id1", queued=False)
    assert conv.log == [("send", "hi")]


async def test_idle_send_uuid_is_the_steering_id_and_gets_no_blank_line():
    # Fix 13a: every send through Steering carries its steering id as the
    # CLI uuid; an idle send's text is otherwise unchanged (no separator
    # needed -- nothing else will ever fold with it).
    conv = FakeConversation()
    s = make(conv)
    outcome = await s.send_when_ready("hi")
    assert conv.uuids == [outcome.id] == ["id1"]
    assert conv.sent == ["hi"]


@pytest.mark.parametrize("cap", ["joins-next-step", "queues-to-end"])
async def test_busy_send_to_a_native_queue_goes_straight_through_as_queued(cap):
    conv = FakeConversation()
    conv.pending = True
    s = make(conv, cap)
    assert await s.send_when_ready("steer") == SendOutcome(id="id1", queued=True)
    # The queued event precedes the send, so it precedes the agent's echo.
    # x-optio-queued keeps the original text; the wire send gets a trailing
    # blank line (Fix 13a) so the agent's own fold does not run it on.
    assert conv.log == [
        ("event", {"type": QUEUED, "id": "id1", "text": "steer"}),
        ("send", "steer\n\n"),
    ]
    assert s.held_ids == []


@pytest.mark.parametrize("cap", ["joins-next-step", "queues-to-end"])
async def test_busy_native_queue_send_gets_the_steering_id_and_a_blank_line(cap):
    # Fix 13a, owner ruling 1: several queued messages otherwise reach the
    # model with no separator between them ("This is a test messageSecond
    # test message..."), because the CLI folds separately-sent queued
    # messages into one turn with one text block each. A trailing blank
    # line on each survives the fold. x-optio-queued keeps the ORIGINAL
    # text (no appended newlines).
    conv = FakeConversation()
    conv.pending = True
    s = make(conv, cap)
    outcome = await s.send_when_ready("steer")
    assert conv.uuids == [outcome.id]
    assert conv.sent == ["steer\n\n"]
    assert conv.log[0] == ("event", {"type": QUEUED, "id": outcome.id, "text": "steer"})


async def test_busy_native_queue_send_does_not_double_an_existing_blank_line():
    conv = FakeConversation()
    conv.pending = True
    s = make(conv, "joins-next-step")
    await s.send_when_ready("steer\n\n")
    assert conv.sent == ["steer\n\n"]


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
    # Fix 17: the text is appended (x-optio-queued, still after the interrupt
    # marker: final-review M3) before the interrupt, and written once the
    # turn has ended, since nothing else is in flight.
    assert conv.log == [
        ("event", INTERRUPT),
        ("event", {"type": QUEUED, "id": "id1", "text": "now"}),
        ("interrupt", None),
        ("send", "now\n\n"),  # blank line: this joins the native queue (Fix 13a)
    ]


async def test_interrupt_and_send_uuid_and_blank_line_while_busy():
    # Fix 13a: "Interrupt and send with text is unchanged apart from the
    # uuid and the blank line."
    conv = FakeConversation()
    conv.pending = True
    s = make(conv, "joins-next-step")
    qid = await s.interrupt_and_send("now")
    assert conv.uuids == [qid]
    assert conv.sent == ["now\n\n"]


async def test_idle_interrupt_and_send_uuid_without_a_blank_line():
    conv = FakeConversation()
    s = make(conv, "joins-next-step")
    qid = await s.interrupt_and_send("x")
    assert conv.uuids == [qid]
    assert conv.sent == ["x"]


async def test_interrupt_and_send_delivers_what_optio_holds_first_in_one_prompt():
    conv = FakeConversation()
    conv.pending = True
    s = make(conv, "unsafe")
    await s.send_when_ready("a")
    await s.send_when_ready("b")
    assert await s.interrupt_and_send("c") == "id3"
    assert conv.log[-5:] == [
        ("event", INTERRUPT),
        ("interrupt", None),
        ("event", {"type": QUEUED, "id": "id3", "text": "c"}),
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
        ("event", {"type": QUEUED, "id": "id2", "text": "b"}),
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


@pytest.mark.parametrize("cap", ["cuts-in", "rejected", "unsafe"])
async def test_send_now_with_nothing_held_on_a_non_native_queue_does_nothing(cap):
    # Send now (empty text) while busy and nothing is held must not cut off
    # the turn now running: there is nothing to interrupt and nothing to
    # send. Realistic trigger: Send now clicked right after a turn-end flush
    # started a new turn, before the UI has processed x-optio-taken.
    conv = FakeConversation()
    conv.pending = True
    s = make(conv, cap)
    assert await s.interrupt_and_send("") is None
    assert conv.log == []
    assert conv.interrupts == 0


async def test_no_turn_end_after_the_interrupt_sends_anyway_and_logs(caplog):
    conv = FakeConversation(turn_end_on_interrupt=False)
    conv.pending = True
    s = make(conv, "joins-next-step", turn_end_timeout_s=0.0)
    with caplog.at_level(logging.WARNING, logger="optio_agents.steering"):
        assert await s.interrupt_and_send("late") == "id1"
    assert conv.sent == ["late\n\n"]
    assert "no turn end" in caplog.text


async def test_a_hanging_interrupt_call_is_bounded_by_the_same_deadline(caplog):
    # Claude Code's interrupt() awaits a control-ack future with no timeout
    # of its own; a live but unresponsive agent must not hold Steering's
    # lock (and every later send/steer behind it) forever.
    class HangingConversation(FakeConversation):
        async def interrupt(self) -> None:
            self.log.append(("interrupt", None))
            self.interrupts += 1
            await asyncio.Event().wait()  # never resolves

    conv = HangingConversation()
    conv.pending = True
    s = make(conv, "joins-next-step", turn_end_timeout_s=0.0)
    with caplog.at_level(logging.WARNING, logger="optio_agents.steering"):
        assert await s.interrupt_and_send("late") == "id1"
    assert conv.sent == ["late\n\n"]
    assert "no turn end" in caplog.text


async def test_idle_interrupt_and_send_sends_without_interrupting():
    conv = FakeConversation()
    s = make(conv, "joins-next-step")
    await s.interrupt_and_send("x")
    # No interrupt marker (idle), but the text still gets its own
    # x-optio-queued (final-review M3) so the reducer has an id to dedupe.
    assert conv.log == [
        ("event", {"type": QUEUED, "id": "id1", "text": "x"}),
        ("send", "x"),
    ]


# -- one at a time onto a native queue (Fix 17, owner ruling 2026-09-15) -----
# docs/2026-09-15-steering-individual-delivery-design.md: at most ONE optio
# message waits in the agent's own queue; the next is written when the one
# in flight reports command_lifecycle "started" or any final state, with a
# safety net on turn end or idle. No sleeps: each step is driven by an event
# and awaited with Steering.settle().


async def _queue_three(s):
    return [(await s.send_when_ready(text)).id for text in ("one", "two", "three")]


async def test_only_one_native_queue_message_is_in_flight_at_a_time():
    conv = FakeConversation(native_lifecycle=True)
    conv.pending = True
    s = make(conv)
    id1, id2, id3 = await _queue_three(s)
    # Every message is announced when it is sent, under the id that is also its uuid ...
    assert events(conv) == [
        {"type": QUEUED, "id": id1, "text": "one"},
        {"type": QUEUED, "id": id2, "text": "two"},
        {"type": QUEUED, "id": id3, "text": "three"},
    ]
    # ... but only the first is written to the agent.
    assert conv.sent == ["one\n\n"] and conv.uuids == [id1]
    assert s.in_flight_id == id1 and s.pending_ids == [id2, id3]


async def test_started_writes_the_next_message_in_order():
    conv = FakeConversation(native_lifecycle=True)
    conv.pending = True
    s = make(conv)
    id1, id2, id3 = await _queue_three(s)
    conv.fire_lifecycle(id1, "queued")  # entering the agent's queue is no reason to advance
    await s.settle()
    assert conv.sent == ["one\n\n"]

    conv.fire_lifecycle(id1, "started")
    await s.settle()
    assert conv.sent == ["one\n\n", "two\n\n"] and conv.uuids == [id1, id2]
    assert s.in_flight_id == id2 and s.pending_ids == [id3]

    conv.fire_lifecycle(id2, "started")
    await s.settle()
    assert conv.sent == ["one\n\n", "two\n\n", "three\n\n"]
    assert conv.uuids == [id1, id2, id3]
    assert s.in_flight_id == id3 and s.pending_ids == []


@pytest.mark.parametrize("state", ["completed", "cancelled", "discarded", "refused"])
async def test_a_final_state_without_started_also_writes_the_next_message(state):
    # cli-queue-lifecycle.md: a final state can arrive without "started";
    # waiting for "started" alone would stall delivery for good.
    conv = FakeConversation(native_lifecycle=True)
    conv.pending = True
    s = make(conv)
    id1, id2, id3 = await _queue_three(s)
    conv.fire_lifecycle(id1, state)
    await s.settle()
    assert conv.sent == ["one\n\n", "two\n\n"]
    assert s.in_flight_id == id2 and s.pending_ids == [id3]


async def test_lifecycle_of_a_message_not_in_flight_does_not_advance():
    conv = FakeConversation(native_lifecycle=True)
    conv.pending = True
    s = make(conv)
    id1, id2, id3 = await _queue_three(s)
    conv.fire_lifecycle(id2, "started")  # not written yet, so not in flight
    conv.fire_lifecycle("cli-minted", "completed")  # a command optio never sent
    await s.settle()
    assert conv.sent == ["one\n\n"] and s.in_flight_id == id1


@pytest.mark.parametrize("event", [{"type": "turn-end"}, {"type": "idle"}])
async def test_the_safety_net_writes_the_next_message_once_the_agent_is_idle(event):
    # No command_lifecycle hook: an agent that reports no lifecycle at all.
    # An idle agent holds nothing in its queue, so a turn end, or any later
    # event, that finds it idle lets the next message go.
    conv = FakeConversation()
    conv.pending = True
    s = make(conv)
    id1, id2, id3 = await _queue_three(s)
    conv.fire({"type": "turn-end"})  # e.g. a merged turn's result: still busy
    await s.settle()
    assert conv.sent == ["one\n\n"] and s.in_flight_id == id1

    conv.pending = False
    conv.fire(event)
    await s.settle()
    assert conv.sent == ["one\n\n", "two\n\n"]
    assert s.in_flight_id == id2 and s.pending_ids == [id3]


async def test_a_send_that_finds_the_agent_idle_still_waits_behind_pending_messages():
    # The agent went idle, but the event that says so has not reached
    # Steering yet: a new message must not overtake the ones still pending.
    conv = FakeConversation()
    conv.pending = True
    s = make(conv)
    id1, id2, id3 = await _queue_three(s)
    conv.pending = False
    outcome = await s.send_when_ready("four")
    assert outcome.queued is True
    assert conv.sent == ["one\n\n", "two\n\n"]
    assert s.in_flight_id == id2 and s.pending_ids == [id3, outcome.id]


@pytest.mark.parametrize("k", [0, 1, 2])
async def test_send_now_on_message_k_only_interrupts_and_delivery_continues_in_order(k):
    # Send now on message k: interrupt; the message in flight runs next and
    # the rest follow one at a time. No cancel, no re-send, no
    # x-optio-requeued (Fix 13a's path is gone).
    conv = FakeConversation(native_lifecycle=True, busy_after_interrupt=True)
    conv.pending = True
    s = make(conv)
    ids = await _queue_three(s)
    conv.log.clear()

    assert await s.interrupt_and_send("", up_to=ids[k]) is None
    await s.settle()
    assert conv.log == [("event", INTERRUPT), ("interrupt", None)]
    assert s.in_flight_id == ids[0] and s.pending_ids == ids[1:]

    conv.fire_lifecycle(ids[0], "started")
    await s.settle()
    conv.fire_lifecycle(ids[1], "started")
    await s.settle()
    assert conv.sent == ["one\n\n", "two\n\n", "three\n\n"]
    assert conv.uuids == ids
    assert REQUEUED not in [e["type"] for e in events(conv)]


async def test_interrupt_and_send_appends_then_interrupts_and_the_text_arrives_last():
    # The owner's live scenario (design doc, Testing): #1 and #2 queued,
    # then Interrupt and send #3: three separate messages, in order.
    conv = FakeConversation(native_lifecycle=True, busy_after_interrupt=True)
    conv.pending = True
    s = make(conv)
    id1 = (await s.send_when_ready("#1")).id
    id2 = (await s.send_when_ready("#2")).id
    conv.log.clear()

    assert await s.interrupt_and_send("#3") == "id3"
    assert conv.log == [
        ("event", INTERRUPT),
        ("event", {"type": QUEUED, "id": "id3", "text": "#3"}),
        ("interrupt", None),
    ]
    assert s.in_flight_id == id1 and s.pending_ids == [id2, "id3"]

    conv.fire_lifecycle(id1, "started")  # #1 runs right after the interrupted turn
    await s.settle()
    conv.fire_lifecycle(id2, "started")
    await s.settle()
    assert conv.sent == ["#1\n\n", "#2\n\n", "#3\n\n"]
    assert conv.uuids == [id1, id2, "id3"]


async def test_a_plain_interrupt_leaves_pending_messages_going_one_at_a_time():
    conv = FakeConversation(native_lifecycle=True, busy_after_interrupt=True)
    conv.pending = True
    s = make(conv)
    id1, id2, id3 = await _queue_three(s)
    await s.interrupt()
    await s.settle()
    assert conv.sent == ["one\n\n"] and s.pending_ids == [id2, id3]

    conv.fire_lifecycle(id1, "started")
    await s.settle()
    conv.fire_lifecycle(id2, "started")
    await s.settle()
    assert conv.sent == ["one\n\n", "two\n\n", "three\n\n"]


async def test_pending_messages_stay_undelivered_when_the_session_ends(caplog):
    conv = FakeConversation(native_lifecycle=True)
    conv.pending = True
    s = make(conv)
    id1, id2, id3 = await _queue_three(s)
    conv.closed = True
    with caplog.at_level(logging.WARNING, logger="optio_agents.steering"):
        conv.fire_lifecycle(id1, "discarded")  # teardown: it was still queued
        conv.fire({"type": "x-optio-closed", "reason": "process ended"})
        await s.settle()
    assert conv.sent == ["one\n\n"]
    assert s.pending_ids == [id2, id3]
    assert "not delivered" in caplog.text


# -- session end vs. the advance path (Fix 19 controller note 2026-09-15) ----
# Fix 17's reviewer flagged: at session end the graceful interrupt makes the
# CLI act on the in-flight message (start it, or cancel it under
# cancel_queued). That command_lifecycle event triggers the SAME
# lifecycle-driven advance path (_on_event -> _schedule_advance -> _advance
# -> _write_next_locked) that delivers the next message mid-turn. Once
# begin_session_end() has made the conversation ``closed``, that path must
# write NOTHING (checked before the pop, so the pending list survives intact
# for the resume re-queue), for BOTH a "started" and a "cancelled" in-flight
# lifecycle.


@pytest.mark.parametrize("in_flight_state", ["started", "cancelled"])
async def test_session_end_blocks_the_lifecycle_triggered_advance_path(caplog, in_flight_state):
    conv = FakeConversation(native_lifecycle=True)
    conv.pending = True
    s = make(conv)
    id1, id2, id3 = await _queue_three(s)  # id1 in flight; id2, id3 pending
    conv.closed = True  # begin_session_end()'s effect on ClaudeCodeConversation.closed
    with caplog.at_level(logging.WARNING, logger="optio_agents.steering"):
        conv.fire_lifecycle(id1, in_flight_state)
        await s.settle()
    assert conv.sent == ["one\n\n"]  # id2 ("two") is never written
    assert s.pending_ids == [id2, id3]  # untouched, in order, for the resume re-queue
    assert "not delivered" in caplog.text


# -- transport reset on relaunch (Fix 25, final-review-2 I3 / ledger line 399) -
# A model/effort relaunch SIGTERMs the CLI mid-turn and reattaches a whole new
# process underneath the same Conversation. The dead process took the
# in-flight message with it, so neither its command_lifecycle nor the turn's
# own result will ever arrive: without reset_transport(), _in_flight would
# stay set forever and _advance would never write id2/id3 again.


async def test_reset_transport_rewrites_the_in_flight_message_then_resumes_in_order():
    conv = FakeConversation(native_lifecycle=True)
    conv.pending = True
    s = make(conv)
    id1, id2, id3 = await _queue_three(s)  # id1 in flight; id2, id3 pending
    assert s.in_flight_id == id1 and s.pending_ids == [id2, id3]

    # The transport under conv was just replaced (a real relaunch reattaches
    # a whole new handle underneath the same Conversation); model what
    # reaches the fresh one from here on.
    conv.sent.clear()
    conv.uuids.clear()
    await s.reset_transport()
    assert s.in_flight_id == id1 and s.pending_ids == [id2, id3]
    assert conv.sent == ["one\n\n"] and conv.uuids == [id1]  # rewritten, not dropped

    conv.fire_lifecycle(id1, "started")
    await s.settle()
    assert conv.sent == ["one\n\n", "two\n\n"] and conv.uuids == [id1, id2]

    conv.fire_lifecycle(id2, "started")
    await s.settle()
    assert conv.sent == ["one\n\n", "two\n\n", "three\n\n"]
    assert conv.uuids == [id1, id2, id3]  # #1, #2, #3, in order, exactly once each


# -- W1 (wave-2 re-review of Fix 25): the dying process's own last events ----
# must not beat reset_transport() to clearing _in_flight, or the message the
# fix exists to rescue is silently lost one step earlier: an abort result
# followed by a trailing idle, or a shutdown sweep's own "cancelled" for the
# in-flight uuid, both land on the OLD process before session.py ever calls
# reset_transport(). begin_transport_reset() must latch the window so
# neither can beat it.


async def test_begin_transport_reset_survives_an_abort_result_then_a_trailing_idle():
    conv = FakeConversation(native_lifecycle=True)
    conv.pending = True
    s = make(conv)
    id1, id2, id3 = await _queue_three(s)  # id1 in flight; id2, id3 pending
    conv.sent.clear()
    conv.uuids.clear()

    s.begin_transport_reset()
    # The dying process's own last gasp, both received before session.py
    # gets to call reset_transport(): an abort result, then a trailing idle
    # (is_pending() goes False) -- exactly what the plain idle heuristic in
    # _on_event would otherwise read as "id1 was taken or dropped".
    conv.pending = False
    conv.fire({"type": "result", "subtype": "error_during_execution"})
    conv.fire({"type": "system", "subtype": "session_state_changed", "state": "idle"})
    assert s.in_flight_id == id1  # NOT cleared: the latch held

    await s.reset_transport()
    # Review of fix 29, finding 1: unlike the shutdown-sweep case below, no
    # command_lifecycle for id1 ever fired here, so nothing told the UI it
    # was undelivered — id1 is rewritten under the SAME id, and no
    # x-optio-requeued is needed (or emitted).
    assert conv.sent == ["one\n\n"] and conv.uuids == [id1]  # id1 rewritten, not dropped
    assert REQUEUED not in [e["type"] for e in events(conv)]

    conv.fire_lifecycle(id1, "started")
    await s.settle()
    conv.fire_lifecycle(id2, "started")
    await s.settle()
    assert conv.sent == ["one\n\n", "two\n\n", "three\n\n"]
    assert conv.uuids == [id1, id2, id3]  # #1, #2, #3, in order, exactly once each


async def test_begin_transport_reset_survives_a_shutdown_sweep_cancelled():
    conv = FakeConversation(native_lifecycle=True)
    conv.pending = True
    s = make(conv)
    id1, id2, id3 = await _queue_three(s)  # id1 in flight; id2, id3 pending
    conv.sent.clear()
    conv.uuids.clear()

    s.begin_transport_reset()
    # A shutting-down CLI sweeping its own queue on the way out: "cancelled"
    # is in _LIFECYCLE_ADVANCE_STATES, so without the latch this clears
    # _in_flight exactly as a real Send-now cancellation would.
    conv.fire_lifecycle(id1, "cancelled")
    assert s.in_flight_id == id1  # NOT cleared: the latch held

    await s.reset_transport()
    # Review of fix 29, finding 1: the shutdown sweep's own "cancelled" for
    # id1 already reached the UI (the conversation's raw event stream —
    # here, conv.fire() ran it through every handler exactly as a real
    # wrapper's _route would), which the reducer turns into a muted "Not
    # delivered: one" note. Rewriting id1 under the SAME id would strand
    # that note forever, so reset_transport() gives it a NEW id and
    # announces the swap with x-optio-requeued instead — the same event
    # Fix 19's own requeue() emits, which the reducer already knows how to
    # turn a "Not delivered" note back into a queued bubble with.
    new_id1 = conv.uuids[0]
    assert new_id1 is not None and new_id1 != id1
    assert conv.sent == ["one\n\n"]
    assert s.in_flight_id == new_id1
    assert {"type": REQUEUED, "id": id1, "new_id": new_id1} in events(conv)

    conv.fire_lifecycle(new_id1, "started")
    await s.settle()
    conv.fire_lifecycle(id2, "started")
    await s.settle()
    assert conv.sent == ["one\n\n", "two\n\n", "three\n\n"]
    assert conv.uuids == [new_id1, id2, id3]  # #1, #2, #3, in order, exactly once each


# -- W2 (wave-2 re-review): a "started" observed during the window must
# still prevent reset_transport() from resending -- the dying process took
# the message into its transcript, and --continue restores that transcript,
# so writing it again would deliver it twice.


async def test_begin_transport_reset_does_not_resend_a_message_confirmed_started():
    conv = FakeConversation(native_lifecycle=True)
    conv.pending = True
    s = make(conv)
    id1, id2, id3 = await _queue_three(s)  # id1 in flight; id2, id3 pending
    conv.sent.clear()
    conv.uuids.clear()

    s.begin_transport_reset()
    # The reader is drained (Fix 29/W2's own fix, in session.py), so this
    # "started" -- unlike q-cancel3's sweep -- reaches Steering before
    # reset_transport() runs.
    conv.fire_lifecycle(id1, "started")
    assert s.in_flight_id == id1  # still tracked, but now known taken

    await s.reset_transport()
    # id1 is NOT written again; reset_transport() moves straight on to id2,
    # the next deliverable message, since nothing is in flight any more.
    assert conv.sent == ["two\n\n"] and conv.uuids == [id2]
    assert s.in_flight_id == id2 and s.pending_ids == [id3]

    conv.fire_lifecycle(id2, "started")
    await s.settle()
    assert conv.sent == ["two\n\n", "three\n\n"] and conv.uuids == [id2, id3]


# -- review of fix 29, finding 3: the latch only guarded _on_event. --------
# send_when_ready, interrupt_and_send (via _interrupt_and_send_native) and
# requeue each also read a plain is_pending() to decide "the agent is idle,
# _in_flight is stale" -- exactly what the W2 drain leaves False for a few
# seconds mid-relaunch, before reset_transport() has run. Landing in that
# window must queue behind the latched in-flight message, not clear it and
# write straight to the dying transport.


async def test_send_when_ready_during_a_relaunch_does_not_clear_the_latched_in_flight():
    conv = FakeConversation(native_lifecycle=True)
    conv.pending = True
    s = make(conv)
    id1, id2, id3 = await _queue_three(s)  # id1 in flight; id2, id3 pending
    conv.sent.clear()
    conv.uuids.clear()

    s.begin_transport_reset()
    conv.pending = False  # the W2-drained abort result + trailing idle
    outcome = await s.send_when_ready("four")
    id4 = outcome.id
    assert outcome.queued is True
    assert s.in_flight_id == id1  # NOT cleared: the latch still held
    assert conv.sent == []  # id4 NOT written straight to the dying transport
    assert s.pending_ids == [id2, id3, id4]

    await s.reset_transport()
    assert conv.sent == ["one\n\n"] and conv.uuids == [id1]

    conv.fire_lifecycle(id1, "started")
    await s.settle()
    conv.fire_lifecycle(id2, "started")
    await s.settle()
    conv.fire_lifecycle(id3, "started")
    await s.settle()
    assert conv.sent == ["one\n\n", "two\n\n", "three\n\n", "four\n\n"]
    assert conv.uuids == [id1, id2, id3, id4]  # #1-#4, in order, exactly once each


async def test_interrupt_and_send_during_a_relaunch_does_not_clear_the_latched_in_flight():
    conv = FakeConversation(native_lifecycle=True)
    conv.pending = True
    s = make(conv)
    id1, id2, id3 = await _queue_three(s)  # id1 in flight; id2, id3 pending
    conv.sent.clear()
    conv.uuids.clear()

    s.begin_transport_reset()
    conv.pending = False  # the W2-drained abort result + trailing idle
    id4 = await s.interrupt_and_send("four")
    assert s.in_flight_id == id1  # NOT cleared: the latch still held
    assert conv.sent == []  # id4 NOT written straight to the dying transport
    assert s.pending_ids == [id2, id3, id4]

    await s.reset_transport()
    assert conv.sent == ["one\n\n"] and conv.uuids == [id1]

    conv.fire_lifecycle(id1, "started")
    await s.settle()
    conv.fire_lifecycle(id2, "started")
    await s.settle()
    conv.fire_lifecycle(id3, "started")
    await s.settle()
    assert conv.sent == ["one\n\n", "two\n\n", "three\n\n", "four\n\n"]
    assert conv.uuids == [id1, id2, id3, id4]  # #1-#4, in order, exactly once each


async def test_requeue_during_a_relaunch_does_not_clear_the_latched_in_flight():
    conv = FakeConversation(native_lifecycle=True)
    conv.pending = True
    s = make(conv)
    id1, id2, id3 = await _queue_three(s)  # id1 in flight; id2, id3 pending
    conv.sent.clear()
    conv.uuids.clear()

    s.begin_transport_reset()
    conv.pending = False  # the W2-drained abort result + trailing idle
    new_ids = await s.requeue([("old1", "zero")])
    assert s.in_flight_id == id1  # NOT cleared: the latch still held
    assert conv.sent == []  # not written straight to the dying transport
    assert s.pending_ids == [new_ids[0], id2, id3]  # requeued goes to the front

    await s.reset_transport()
    assert conv.sent == ["one\n\n"] and conv.uuids == [id1]

    conv.fire_lifecycle(id1, "started")
    await s.settle()
    conv.fire_lifecycle(new_ids[0], "started")
    await s.settle()
    conv.fire_lifecycle(id2, "started")
    await s.settle()
    assert conv.sent == ["one\n\n", "zero\n\n", "two\n\n", "three\n\n"]
    assert conv.uuids == [id1, new_ids[0], id2, id3]


# -- re-queue on resume (Fix 19, owner ruling 2026-09-15, finding 6 #2) -------
# Messages a previous run left queued and undelivered go out again, in their
# original order, each under a NEW id announced by x-optio-requeued (never a
# second x-optio-queued), through the one-at-a-time path.


async def test_requeue_announces_each_message_under_a_new_id_and_writes_one_at_a_time():
    conv = FakeConversation(native_lifecycle=True)
    conv.pending = True  # the resume notice's turn runs
    s = make(conv)
    assert await s.requeue([("old1", "one"), ("old2", "two")]) == ["id1", "id2"]
    assert conv.log == [
        ("event", {"type": REQUEUED, "id": "old1", "new_id": "id1"}),
        ("event", {"type": REQUEUED, "id": "old2", "new_id": "id2"}),
        ("send", "one\n\n"),
    ]
    assert conv.uuids == ["id1"]
    assert s.in_flight_id == "id1" and s.pending_ids == ["id2"]
    conv.fire_lifecycle("id1", "started")
    await s.settle()
    assert conv.sent == ["one\n\n", "two\n\n"] and conv.uuids == ["id1", "id2"]
    assert QUEUED not in [e["type"] for e in events(conv)]


async def test_requeue_while_idle_writes_the_first_at_once():
    conv = FakeConversation(native_lifecycle=True)
    s = make(conv)
    await s.requeue([("old1", "one"), ("old2", "two")])
    assert conv.sent == ["one\n\n"] and s.pending_ids == ["id2"]


async def test_requeued_messages_go_ahead_of_messages_sent_since_the_resume():
    conv = FakeConversation(native_lifecycle=True)
    conv.pending = True
    s = make(conv)
    first = await s.send_when_ready("new-a")   # in flight
    second = await s.send_when_ready("new-b")  # pending
    assert await s.requeue([("old1", "one")]) == ["id3"]
    assert s.in_flight_id == first.id
    assert s.pending_ids == ["id3", second.id]


async def test_requeue_of_nothing_does_nothing():
    conv = FakeConversation(native_lifecycle=True)
    s = make(conv)
    assert await s.requeue([]) == []
    assert conv.log == []


async def test_requeue_after_close_raises():
    conv = FakeConversation(native_lifecycle=True)
    conv.closed = True
    s = make(conv)
    with pytest.raises(ConversationClosed):
        await s.requeue([("old1", "one")])
    assert conv.log == []


async def test_requeue_on_a_non_native_agent_holds_them_until_the_turn_ends():
    conv = FakeConversation()
    conv.pending = True
    s = make(conv, busy_send="unsafe")
    await s.requeue([("old1", "one"), ("old2", "two")])
    assert conv.sent == [] and s.held_ids == ["id1", "id2"]
    conv.pending = False
    conv.fire({"type": "turn-end"})
    await s.settle()
    assert conv.sent == ["one\n\ntwo"]
    assert {"type": TAKEN, "ids": ["id1", "id2"]} in events(conv)


# -- interrupt -----------------------------------------------------------------

async def test_interrupt_emits_the_marker_only_while_a_turn_runs():
    conv = FakeConversation()
    s = make(conv)
    await s.interrupt()
    assert conv.log == [("interrupt", None)]
    conv.pending = True
    await s.interrupt()
    assert conv.log[1:] == [("event", INTERRUPT), ("interrupt", None)]


async def test_a_double_interrupt_in_the_same_turn_emits_only_one_marker():
    # The turn does not end between the two clicks (Claude Code's is_pending
    # stays true until the result arrives), so this exercises the dedup, not
    # a fresh turn.
    conv = FakeConversation(turn_end_on_interrupt=False)
    conv.pending = True
    s = make(conv)
    await s.interrupt()
    await s.interrupt()
    assert conv.interrupts == 1
    assert events(conv) == [INTERRUPT]


async def test_interrupt_during_interrupt_and_sends_wait_yields_one_marker():
    # interrupt() is deliberately lock-free, so it can run while
    # interrupt_and_send is still awaiting the turn end.
    interrupt_started = asyncio.Event()
    release = asyncio.Event()

    class PausingConversation(FakeConversation):
        async def interrupt(self) -> None:
            self.log.append(("interrupt", None))
            self.interrupts += 1
            interrupt_started.set()
            await release.wait()
            if self.pending:
                self.pending = False
                self.fire({"type": "turn-end"})

    conv = PausingConversation()
    conv.pending = True
    s = make(conv, "joins-next-step")

    task = asyncio.ensure_future(s.interrupt_and_send("now"))
    await interrupt_started.wait()
    await s.interrupt()  # a second Interrupt while the first is still in flight
    release.set()

    assert await task == "id1"
    assert events(conv) == [INTERRUPT, {"type": QUEUED, "id": "id1", "text": "now"}]
    assert conv.interrupts == 1
    assert conv.sent == ["now\n\n"]


# -- final-review M1: the interrupt flag must not survive a merged turn's ----
# -- idle gap into the next turn --------------------------------------------

async def test_interrupted_flag_clears_once_idle_after_a_merged_turns_result():
    # Mirrors the real Claude Code driver (final-review M1): a merged turn
    # (Send when ready taken mid-turn) has two sends and one result, so
    # is_pending() can stay True for a few ms after that result's turn-end
    # event, until a later, non-turn-end event (idle) actually clears it. An
    # Interrupt landing in that window must not leave `_interrupted` set once
    # idle catches up — otherwise the NEXT turn's first Interrupt is a silent
    # no-op (no marker, no underlying interrupt() call).
    conv = FakeConversation(turn_end_on_interrupt=False)
    conv.pending = True
    s = make(conv, "joins-next-step")

    conv.fire({"type": "turn-end"})  # this turn's result; still "pending"
    await s.interrupt()              # window between result and idle
    assert events(conv) == [INTERRUPT]
    assert conv.interrupts == 1

    conv.pending = False
    conv.fire({"type": "idle"})      # not itself a turn-end event

    conv.pending = True               # the next turn starts
    await s.interrupt()
    assert events(conv) == [INTERRUPT, INTERRUPT]
    assert conv.interrupts == 2


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
