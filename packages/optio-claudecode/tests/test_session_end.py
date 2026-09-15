"""Fix 19 (owner ruling 2026-09-15, session-end design part 4): a cooperative
cancel while a turn runs interrupts that turn gracefully, bounded, before the
reader stops and claude is killed (session.py _end_turn_gracefully)."""

import asyncio
import logging

import pytest

from optio_agents.conversation import ConversationClosed
from optio_claudecode.session import GRACEFUL_INTERRUPT_TIMEOUT_S, _end_turn_gracefully


class FakeConversation:
    def __init__(self, *, pending=True, closed=False, hang=False, raises=None):
        self.log: list[str] = []
        self.pending = pending
        self.closed = closed
        self.hang = hang
        self.raises = raises

    def is_pending(self):
        return self.pending

    def begin_session_end(self):
        self.log.append("begin_session_end")

    async def interrupt_for_session_end(self):
        self.log.append("interrupt_for_session_end")
        if self.raises is not None:
            raise self.raises
        if self.hang:
            await asyncio.Event().wait()


class FakeListener:
    def __init__(self, log):
        self.log = log

    def announce_session_end(self):
        self.log.append("announce_session_end")


def test_the_graceful_interrupt_is_bounded_at_three_seconds():
    assert GRACEFUL_INTERRUPT_TIMEOUT_S == 3.0


async def test_a_running_turn_is_closed_to_sends_marked_then_interrupted():
    conv = FakeConversation()
    assert await _end_turn_gracefully(conv, FakeListener(conv.log)) is True
    assert conv.log == ["begin_session_end", "announce_session_end", "interrupt_for_session_end"]


async def test_without_a_listener_the_turn_is_still_interrupted():
    conv = FakeConversation()
    assert await _end_turn_gracefully(conv, None) is True
    assert conv.log == ["begin_session_end", "interrupt_for_session_end"]


@pytest.mark.parametrize("state", [{"pending": False}, {"closed": True}])
async def test_an_idle_or_closed_conversation_is_left_alone(state):
    conv = FakeConversation(**state)
    assert await _end_turn_gracefully(conv, FakeListener(conv.log)) is False
    assert conv.log == []


async def test_no_conversation_is_left_alone():
    assert await _end_turn_gracefully(None, None) is False


async def test_a_turn_that_does_not_end_in_time_falls_back_to_the_kill(caplog):
    conv = FakeConversation(hang=True)
    with caplog.at_level(logging.WARNING, logger="optio_claudecode.session"):
        assert await _end_turn_gracefully(conv, None, timeout_s=0.0) is False
    assert "did not end within" in caplog.text


async def test_a_conversation_that_closed_under_it_falls_back_quietly():
    conv = FakeConversation(raises=ConversationClosed("gone"))
    assert await _end_turn_gracefully(conv, None) is False
