"""Steering scaffold unit tests against a fake conversation (no sleeps)."""

import asyncio
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
REQUEUED = "x-optio-requeued"
INTERRUPT = {"type": "x-optio-interrupt", "by": "user"}


class FakeConversation:
    """Records sends, interrupts and synthetic events in one ordered log.

    The test sets ``pending`` (busy) itself. ``interrupt()`` ends the turn
    at once (fires the turn-end event) unless ``turn_end_on_interrupt`` is
    False.

    ``uuids`` is a parallel array to ``sent``: the ``uuid`` kwarg (if any)
    ``Steering`` passed to that ``send()`` call — kept separate from ``log``
    so the many pre-existing exact-log assertions below don't have to change
    shape just because sends now carry an id (Fix 13a).

    Native-queue command lifecycle (Fix 13a, ``fire_lifecycle``) and
    ``cancel_async_message`` are opt-in via ``native_lifecycle=True`` so
    only the tests that need the "Send now up to k" machinery pay for it."""

    def __init__(self, *, turn_end_on_interrupt: bool = True, native_lifecycle: bool = False):
        self.handlers = []
        self.sent: list[str] = []
        self.uuids: list[str | None] = []
        self.interrupts = 0
        self.pending = False
        self.closed = False
        self.turn_end_on_interrupt = turn_end_on_interrupt
        self.log: list[tuple[str, object]] = []
        self.native_lifecycle = native_lifecycle
        self.cancel_calls: list[str] = []
        self.cancel_responses: dict[str, bool] = {}

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
            self.pending = False
            self.fire({"type": "turn-end"})

    async def cancel_async_message(self, message_uuid: str) -> bool:
        self.cancel_calls.append(message_uuid)
        cancelled = self.cancel_responses.get(message_uuid, False)
        if cancelled:
            self.fire_lifecycle(message_uuid, "cancelled")
        return cancelled


def _lifecycle(event: dict) -> tuple[str, str] | None:
    if event.get("type") != "command_lifecycle":
        return None
    return event["command_uuid"], event["state"]


def make(conv, busy_send="joins-next-step", **kw):
    ids = iter(f"id{n}" for n in range(1, 100))
    extra = {}
    if conv.native_lifecycle:
        extra["command_lifecycle"] = _lifecycle
        extra["cancel_async_message"] = conv.cancel_async_message
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
    # x-optio-queued (final-review M3) lands after the interrupt marker and
    # the underlying interrupt() call, and before the send: the reducer
    # dedupes the steer's local echo against it by id.
    assert conv.log == [
        ("event", INTERRUPT),
        ("interrupt", None),
        ("event", {"type": QUEUED, "id": "id1", "text": "now"}),
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


# -- send now up to k (Fix 13a, owner ruling 2) -------------------------------
# "With messages 1, 2 and 3 queued, 'Send now' on message 2 must deliver
# messages 1 and 2 (in order) and keep message 3 queued for the agent to take
# when ready."


async def _queue_three(s):
    return [(await s.send_when_ready(text)).id for text in ("one", "two", "three")]


async def test_send_now_up_to_cancels_only_the_message_after_k_then_resends_it():
    conv = FakeConversation(native_lifecycle=True)
    conv.pending = True
    s = make(conv, "joins-next-step")
    id1, id2, id3 = await _queue_three(s)
    conv.cancel_responses[id3] = True
    conv.log.clear()

    # id3's 'started' has not been seen: interrupt_and_send blocks in the
    # wait-for-k's-turn-to-start step until the CLI reports it, so drive it
    # from a background task (no sleeps: one bare yield reaches that await,
    # since nothing before it suspends on a FakeConversation).
    task = asyncio.ensure_future(s.interrupt_and_send("", up_to=id2))
    await asyncio.sleep(0)
    assert conv.cancel_calls == [id3]  # not id1 or id2
    assert conv.interrupts == 1
    assert events(conv) == [INTERRUPT]

    conv.fire_lifecycle(id2, "started")
    assert await task is None

    assert len(events(conv)) == 2
    requeued = events(conv)[1]
    assert requeued["type"] == REQUEUED and requeued["id"] == id3
    new_id = requeued["new_id"]
    assert new_id != id3
    assert conv.sent[-1] == "three\n\n"  # same text, still blank-line terminated
    assert conv.uuids[-1] == new_id


async def test_send_now_up_to_does_not_resend_when_cancel_reports_false():
    # cancelled:false means the message was already taken (or unknown) and
    # will be delivered normally: no re-send, no x-optio-requeued.
    conv = FakeConversation(native_lifecycle=True)
    conv.pending = True
    s = make(conv, "joins-next-step")
    id1, id2, id3 = await _queue_three(s)
    conv.cancel_responses[id3] = False
    conv.log.clear()

    assert await s.interrupt_and_send("", up_to=id2) is None

    assert conv.cancel_calls == [id3]
    assert conv.interrupts == 1
    assert events(conv) == [INTERRUPT]
    assert conv.sent == ["one\n\n", "two\n\n", "three\n\n"]  # id3 sent only once


async def test_send_now_up_to_skips_a_message_already_started():
    conv = FakeConversation(native_lifecycle=True)
    conv.pending = True
    s = make(conv, "joins-next-step")
    id1, id2, id3 = await _queue_three(s)
    conv.fire_lifecycle(id3, "started")  # e.g. taken at a tool boundary already
    conv.log.clear()

    assert await s.interrupt_and_send("", up_to=id2) is None
    assert conv.cancel_calls == []


async def test_send_now_up_to_naming_the_last_message_cancels_nothing():
    conv = FakeConversation(native_lifecycle=True)
    conv.pending = True
    s = make(conv, "joins-next-step")
    id1, id2, id3 = await _queue_three(s)
    conv.log.clear()

    assert await s.interrupt_and_send("", up_to=id3) is None

    assert conv.cancel_calls == []
    assert conv.interrupts == 1  # still a plain "send now"
    assert events(conv) == [INTERRUPT]


async def test_up_to_is_ignored_without_native_lifecycle_support():
    # A wrapper that never wired command_lifecycle/cancel_async_message (no
    # engine but Claude Code has this yet) degrades to today's meaning:
    # deliver everything queued, exactly as if up_to were absent.
    conv = FakeConversation()  # native_lifecycle=False: no cancel support
    conv.pending = True
    s = make(conv, "joins-next-step")
    id1, id2, id3 = await _queue_three(s)
    conv.log.clear()

    assert await s.interrupt_and_send("", up_to=id2) is None
    assert conv.interrupts == 1


async def test_a_hanging_cancel_in_send_now_up_to_is_bounded_by_one_deadline(caplog):
    # ClaudeCodeConversation.cancel_async_message awaits a control-ack future
    # with no timeout of its own; a live CLI that never answers it must not
    # hold Steering's lock (and every later /send and /steer queued behind
    # it) forever. The cancel loop, the interrupt and the turn-end wait all
    # share ONE turn_end_timeout_s deadline (review of Fix 13a, round 1),
    # not three stacked waits.
    class HangingCancelConversation(FakeConversation):
        async def cancel_async_message(self, message_uuid: str) -> bool:
            self.cancel_calls.append(message_uuid)
            await asyncio.Event().wait()  # never resolves
            return True  # pragma: no cover

    conv = HangingCancelConversation(native_lifecycle=True)
    conv.pending = True
    s = make(conv, "joins-next-step", turn_end_timeout_s=0.0)
    id1, id2, id3 = await _queue_three(s)
    conv.log.clear()

    with caplog.at_level(logging.WARNING, logger="optio_agents.steering"):
        assert await asyncio.wait_for(
            s.interrupt_and_send("", up_to=id2), timeout=5.0,
        ) is None
    assert conv.cancel_calls == [id3]
    assert "send-now-up-to" in caplog.text

    # The cancel never confirmed, so no interrupt and no re-send happened --
    # only the message actually confirmed cancelled would be resent, and
    # here that set is empty.
    assert conv.interrupts == 0
    assert conv.sent == ["one\n\n", "two\n\n", "three\n\n"]

    # And critically: the lock was released. A later call does not hang
    # behind the earlier one.
    conv.pending = False
    outcome = await asyncio.wait_for(s.send_when_ready("four"), timeout=5.0)
    assert outcome.id and not outcome.queued


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
