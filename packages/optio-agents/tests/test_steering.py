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
