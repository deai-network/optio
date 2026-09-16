"""ClaudeCodeConversation unit tests against an in-process fake handle."""

import asyncio
import json

import pytest

from optio_agents.conversation import ConversationClosed, PermissionDecision
from optio_claudecode.conversation import ClaudeCodeConversation


class _FakeStdin:
    def __init__(self):
        self.lines: asyncio.Queue[dict] = asyncio.Queue()

    def write(self, data: bytes) -> None:
        self.lines.put_nowait(json.loads(data.decode()))

    async def drain(self) -> None:
        pass


class _FakeStdout:
    def __init__(self):
        self.queue: asyncio.Queue[bytes | None] = asyncio.Queue()

    def feed(self, obj: dict) -> None:
        self.queue.put_nowait((json.dumps(obj) + "\n").encode())

    def eof(self) -> None:
        self.queue.put_nowait(None)

    def __aiter__(self):
        return self

    async def __anext__(self):
        item = await self.queue.get()
        if item is None:
            raise StopAsyncIteration
        return item


class _FakeHandle:
    def __init__(self):
        self.stdin = _FakeStdin()
        self.stdout = _FakeStdout()


@pytest.fixture
def convo():
    handle = _FakeHandle()
    c = ClaudeCodeConversation(permission_gate=True)
    c.attach(handle)
    return c, handle


@pytest.mark.asyncio
async def test_gate_off_denies_can_use_tool_defensively():
    handle = _FakeHandle()
    c = ClaudeCodeConversation()  # permission_gate=False (default)
    c.attach(handle)
    reader = asyncio.create_task(c.run_reader())
    handle.stdout.feed({"type": "control_request", "request_id": "perm-x",
                        "request": {"subtype": "can_use_tool",
                                    "tool_name": "Bash", "input": {}}})
    resp = await asyncio.wait_for(handle.stdin.lines.get(), 60)
    assert resp["type"] == "control_response"
    assert resp["response"]["request_id"] == "perm-x"
    assert resp["response"]["response"]["behavior"] == "deny"
    handle.stdout.eof()
    await reader


@pytest.mark.asyncio
async def test_send_writes_user_message_and_pending(convo):
    c, handle = convo
    reader = asyncio.create_task(c.run_reader())
    await c.send("hello")
    sent = await asyncio.wait_for(handle.stdin.lines.get(), 60)
    assert sent["type"] == "user"
    assert sent["message"]["content"][0]["text"] == "hello"
    assert c.is_pending()
    handle.stdout.feed({"type": "result", "subtype": "success",
                        "result": "hi back", "is_error": False})
    # The reader clears pending when it processes the result; poll for that
    # transition rather than assuming it lands within a fixed wall-clock delay
    # (which flakes when the reader task is CPU-starved).
    import time
    end = time.monotonic() + 60
    while time.monotonic() < end:
        if not c.is_pending():
            break
        await asyncio.sleep(0.02)
    assert not c.is_pending()
    handle.stdout.eof()
    await reader


@pytest.mark.asyncio
async def test_send_carries_a_uuid_and_no_priority(convo):
    # Fix 13a: every user message optio writes carries a uuid, without a
    # priority (the CLI schema defaults an omitted one to "next" already).
    # A caller outside Steering (session.py's agent feedback, the first
    # prompt) that doesn't pass one still gets a fresh uuid4 here.
    c, handle = convo
    reader = asyncio.create_task(c.run_reader())
    await c.send("hello")
    sent = await asyncio.wait_for(handle.stdin.lines.get(), 60)
    assert "priority" not in sent
    minted = sent["uuid"]
    import uuid as uuid_lib
    assert uuid_lib.UUID(minted)  # a well-formed uuid4 string

    await c.send("steer", uuid="the-steering-id")
    sent2 = await asyncio.wait_for(handle.stdin.lines.get(), 60)
    assert sent2["uuid"] == "the-steering-id"  # the uuid IS the steering id
    assert minted != "the-steering-id"

    handle.stdout.feed({"type": "result", "subtype": "success",
                        "result": "hi back", "is_error": False})
    handle.stdout.eof()
    await reader


@pytest.mark.asyncio
async def test_cancel_async_message_round_trip(convo):
    c, handle = convo
    reader = asyncio.create_task(c.run_reader())
    await c.send("queued", uuid="q-1")
    await asyncio.wait_for(handle.stdin.lines.get(), 60)
    task = asyncio.create_task(c.cancel_async_message("q-1"))
    ctrl = await asyncio.wait_for(handle.stdin.lines.get(), 60)
    assert ctrl["type"] == "control_request"
    assert ctrl["request"] == {"subtype": "cancel_async_message", "message_uuid": "q-1"}
    handle.stdout.feed({"type": "control_response", "response": {
        "subtype": "success", "request_id": ctrl["request_id"],
        "response": {"cancelled": True}}})
    assert await asyncio.wait_for(task, 60) is True
    handle.stdout.eof()
    await reader


@pytest.mark.asyncio
async def test_cancel_async_message_false_when_already_started(convo):
    c, handle = convo
    reader = asyncio.create_task(c.run_reader())
    await c.send("queued", uuid="q-2")
    await asyncio.wait_for(handle.stdin.lines.get(), 60)
    task = asyncio.create_task(c.cancel_async_message("q-2"))
    ctrl = await asyncio.wait_for(handle.stdin.lines.get(), 60)
    handle.stdout.feed({"type": "control_response", "response": {
        "subtype": "success", "request_id": ctrl["request_id"],
        "response": {"cancelled": False}}})
    assert await asyncio.wait_for(task, 60) is False
    handle.stdout.eof()
    await reader


@pytest.mark.asyncio
async def test_on_event_transparent_and_on_message(convo):
    c, handle = convo
    events, texts = [], []
    c.on_event(events.append)
    c.on_message(texts.append)
    reader = asyncio.create_task(c.run_reader())
    handle.stdout.feed({"type": "system", "subtype": "init", "session_id": "s"})
    handle.stdout.feed({"type": "assistant", "message": {"role": "assistant",
                        "content": [{"type": "text", "text": "partial"}]}})
    handle.stdout.feed({"type": "result", "subtype": "success",
                        "result": "final answer", "is_error": False})
    handle.stdout.eof()
    await reader
    types = [e["type"] for e in events]
    assert types[:3] == ["system", "assistant", "result"]
    assert types[-1] == "x-optio-closed"
    assert texts == ["final answer"]


@pytest.mark.asyncio
async def test_unparseable_line_becomes_synthetic_event(convo):
    c, handle = convo
    events = []
    c.on_event(events.append)
    reader = asyncio.create_task(c.run_reader())
    handle.stdout.queue.put_nowait(b"this is not json\n")
    handle.stdout.eof()
    await reader
    assert events[0]["type"] == "x-optio-unparseable"


@pytest.mark.asyncio
async def test_raising_handler_does_not_kill_dispatch(convo):
    c, handle = convo
    good = []
    c.on_event(lambda e: (_ for _ in ()).throw(RuntimeError("boom")))
    c.on_event(good.append)
    reader = asyncio.create_task(c.run_reader())
    handle.stdout.feed({"type": "system", "subtype": "init"})
    handle.stdout.eof()
    await reader
    assert any(e["type"] == "system" for e in good)


@pytest.mark.asyncio
async def test_permission_roundtrip_and_late_registration(convo):
    c, handle = convo
    reader = asyncio.create_task(c.run_reader())
    handle.stdout.feed({"type": "control_request", "request_id": "perm-1",
                        "request": {"subtype": "can_use_tool",
                                    "tool_name": "Bash",
                                    "input": {"command": "rm -rf /"}}})
    await asyncio.sleep(0.05)  # arrives before any handler → queued

    async def handler(req):
        assert req.tool_name == "Bash"
        return PermissionDecision(behavior="deny", message="nope")

    c.on_permission_request(handler)
    resp = await asyncio.wait_for(handle.stdin.lines.get(), 60)
    assert resp["type"] == "control_response"
    assert resp["response"]["request_id"] == "perm-1"
    assert resp["response"]["response"]["behavior"] == "deny"
    assert resp["response"]["response"]["message"] == "nope"
    handle.stdout.eof()
    await reader


@pytest.mark.asyncio
async def test_interrupt_handshake(convo):
    c, handle = convo
    reader = asyncio.create_task(c.run_reader())
    await c.send("long task")
    intr = asyncio.create_task(c.interrupt())
    sent = await asyncio.wait_for(handle.stdin.lines.get(), 60)   # user msg
    ctrl = await asyncio.wait_for(handle.stdin.lines.get(), 60)   # control_request
    assert ctrl["request"]["subtype"] == "interrupt"
    handle.stdout.feed({"type": "control_response", "response": {
        "subtype": "success", "request_id": ctrl["request_id"]}})
    await asyncio.wait_for(intr, 60)
    handle.stdout.eof()
    await reader


@pytest.mark.asyncio
async def test_interrupt_with_cancel_queued_asks_the_cli_to_drop_its_queue(convo):
    # Fix 19: at session end the message in flight must not start a new turn
    # the kill would cut (cli-queue-lifecycle.md, interrupt_receipt_v1).
    c, handle = convo
    reader = asyncio.create_task(c.run_reader())
    await c.send("long task")
    await asyncio.wait_for(handle.stdin.lines.get(), 60)   # user msg
    intr = asyncio.create_task(c.interrupt(cancel_queued=True))
    ctrl = await asyncio.wait_for(handle.stdin.lines.get(), 60)
    assert ctrl["request"] == {"subtype": "interrupt", "cancel_queued": True}
    handle.stdout.feed({"type": "control_response", "response": {
        "subtype": "success", "request_id": ctrl["request_id"], "response": {"cancelled": []}}})
    await asyncio.wait_for(intr, 60)
    handle.stdout.eof()
    await reader


@pytest.mark.asyncio
async def test_interrupt_for_session_end_returns_after_the_turns_result(convo):
    c, handle = convo
    seen = []
    c.on_event(seen.append)
    reader = asyncio.create_task(c.run_reader())
    await c.send("long task")
    await asyncio.wait_for(handle.stdin.lines.get(), 60)
    ending = asyncio.create_task(c.interrupt_for_session_end())
    ctrl = await asyncio.wait_for(handle.stdin.lines.get(), 60)
    assert ctrl["request"] == {"subtype": "interrupt", "cancel_queued": True}
    handle.stdout.feed({"type": "control_response", "response": {
        "subtype": "success", "request_id": ctrl["request_id"], "response": {"cancelled": []}}})
    handle.stdout.feed({"type": "assistant", "message": {"id": "m1", "role": "assistant",
                        "content": [{"type": "text", "text": "Because I could"}]}})
    handle.stdout.feed({"type": "result", "subtype": "error_during_execution",
                        "is_error": True, "terminal_reason": "aborted_streaming"})
    await asyncio.wait_for(ending, 60)
    # It returned only once the result had been dispatched (the CLI has then
    # written the partial into its transcript).
    assert any(e.get("type") == "result" for e in seen)
    handle.stdout.eof()
    await reader


@pytest.mark.asyncio
async def test_interrupt_for_session_end_is_a_no_op_while_idle(convo):
    c, handle = convo
    await asyncio.wait_for(c.interrupt_for_session_end(), 60)
    assert handle.stdin.lines.qsize() == 0


@pytest.mark.asyncio
async def test_begin_session_end_refuses_sends_but_not_the_interrupt(convo):
    c, handle = convo
    reader = asyncio.create_task(c.run_reader())
    await c.send("long task")
    await asyncio.wait_for(handle.stdin.lines.get(), 60)
    c.begin_session_end()
    assert c.closed
    with pytest.raises(ConversationClosed):
        await c.send("too late")
    intr = asyncio.create_task(c.interrupt(cancel_queued=True))
    ctrl = await asyncio.wait_for(handle.stdin.lines.get(), 60)
    assert ctrl["type"] == "control_request"
    handle.stdout.feed({"type": "control_response", "response": {
        "subtype": "success", "request_id": ctrl["request_id"], "response": {"cancelled": []}}})
    await asyncio.wait_for(intr, 60)
    handle.stdout.eof()
    await reader


@pytest.mark.asyncio
async def test_send_after_close_raises(convo):
    c, handle = convo
    reader = asyncio.create_task(c.run_reader())
    handle.stdout.eof()
    await reader
    assert c.closed
    with pytest.raises(ConversationClosed):
        await c.send("too late")


@pytest.mark.asyncio
async def test_close_sets_close_requested(convo):
    c, handle = convo
    await c.close()
    assert c.close_requested.is_set()


# -- steering hooks (docs/2026-09-13-conversation-steering-design.md) ---------

from optio_claudecode.steering import BUSY_SEND, is_turn_end, make_steering, undelivered_queued


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


def _lifecycle(command_uuid: str, state: str) -> dict:
    # The recorded shape (cli-queue-lifecycle.md section 1): exactly type,
    # command_uuid, state, uuid (a fresh per-event id) and session_id.
    return {"type": "command_lifecycle", "command_uuid": command_uuid, "state": state,
            "uuid": f"lc-{command_uuid}-{state}", "session_id": "s"}


@pytest.mark.asyncio
async def test_steering_writes_one_queued_message_at_a_time_over_the_real_driver(convo):
    # Fix 17 (docs/2026-09-15-steering-individual-delivery-design.md): at
    # most ONE optio message waits in the CLI's own queue. The next goes on
    # the in-flight message's "started" (recording o-tool) or on a final
    # state that came without "started".
    c, handle = convo
    events = []
    c.on_event(events.append)
    ids = iter(["q1", "q2", "q3"])
    steering = make_steering(c, new_id=lambda: next(ids))
    reader = asyncio.create_task(c.run_reader())

    await c.send("long task")
    await asyncio.wait_for(handle.stdin.lines.get(), 60)
    for text in ("one", "two", "three"):
        assert (await steering.send_when_ready(text)).queued is True
    first = await asyncio.wait_for(handle.stdin.lines.get(), 60)
    assert (first["uuid"], first["message"]["content"][0]["text"]) == ("q1", "one\n\n")
    assert handle.stdin.lines.qsize() == 0  # two and three wait in optio

    handle.stdout.feed(_lifecycle("q1", "queued"))
    handle.stdout.feed(_lifecycle("q1", "started"))
    second = await asyncio.wait_for(handle.stdin.lines.get(), 60)
    assert (second["uuid"], second["message"]["content"][0]["text"]) == ("q2", "two\n\n")

    handle.stdout.feed(_lifecycle("q2", "completed"))  # final, without "started"
    third = await asyncio.wait_for(handle.stdin.lines.get(), 60)
    assert (third["uuid"], third["message"]["content"][0]["text"]) == ("q3", "three\n\n")

    handle.stdout.feed(_lifecycle("q3", "started"))
    handle.stdout.feed({"type": "result", "subtype": "success", "result": "done", "is_error": False})
    handle.stdout.eof()
    await reader
    await steering.settle()
    assert handle.stdin.lines.qsize() == 0
    assert [e for e in events if e.get("type") == "x-optio-queued"] == [
        {"type": "x-optio-queued", "id": "q1", "text": "one"},
        {"type": "x-optio-queued", "id": "q2", "text": "two"},
        {"type": "x-optio-queued", "id": "q3", "text": "three"},
    ]


@pytest.mark.asyncio
async def test_interrupt_and_send_after_two_queued_gives_three_separate_messages_over_the_real_driver(convo):
    # The owner's live scenario (design doc, Testing): #1 and #2 queued,
    # then Interrupt and send #3. Recorded shape o-int: the interrupt reply
    # lists the one message in flight under still_queued, the interrupted
    # result follows, then that message's "started".
    c, handle = convo
    ids = iter(["m1", "m2", "m3"])
    steering = make_steering(c, new_id=lambda: next(ids))
    reader = asyncio.create_task(c.run_reader())

    await c.send("long task")
    await asyncio.wait_for(handle.stdin.lines.get(), 60)
    await steering.send_when_ready("#1")
    await steering.send_when_ready("#2")
    m1 = await asyncio.wait_for(handle.stdin.lines.get(), 60)
    assert m1["uuid"] == "m1"

    task = asyncio.create_task(steering.interrupt_and_send("#3"))
    ctrl = await asyncio.wait_for(handle.stdin.lines.get(), 60)
    assert ctrl["request"]["subtype"] == "interrupt"  # #3 was appended, not written
    handle.stdout.feed({"type": "control_response", "response": {
        "subtype": "success", "request_id": ctrl["request_id"],
        "response": {"still_queued": ["m1"]}}})
    handle.stdout.feed({"type": "result", "subtype": "error_during_execution",
                        "is_error": True, "terminal_reason": "aborted_streaming"})
    assert await asyncio.wait_for(task, 60) == "m3"
    await steering.settle()
    assert handle.stdin.lines.qsize() == 0  # #1 runs next; #2 and #3 still wait

    handle.stdout.feed(_lifecycle("m1", "started"))
    m2 = await asyncio.wait_for(handle.stdin.lines.get(), 60)
    assert (m2["uuid"], m2["message"]["content"][0]["text"]) == ("m2", "#2\n\n")
    handle.stdout.feed(_lifecycle("m1", "completed"))  # no longer in flight: ignored
    handle.stdout.feed({"type": "result", "subtype": "success", "result": "ok", "is_error": False})
    handle.stdout.feed(_lifecycle("m2", "started"))
    m3 = await asyncio.wait_for(handle.stdin.lines.get(), 60)
    assert (m3["uuid"], m3["message"]["content"][0]["text"]) == ("m3", "#3\n\n")
    handle.stdout.eof()
    await reader


@pytest.mark.asyncio
async def test_session_end_writes_no_further_queued_message_over_the_real_driver(convo):
    # Fix 19: the graceful interrupt sweeps the message in flight
    # (cancel_queued -> command_lifecycle "cancelled", never "started").
    # Steering advances on that final state, but begin_session_end() makes
    # the write fail, so "two" stays undelivered for the resume to re-queue
    # and the CLI never starts a turn the kill would cut.
    c, handle = convo
    ids = iter(["q1", "q2"])
    steering = make_steering(c, new_id=lambda: next(ids))
    reader = asyncio.create_task(c.run_reader())
    await c.send("long task")
    await asyncio.wait_for(handle.stdin.lines.get(), 60)
    await steering.send_when_ready("one")
    await steering.send_when_ready("two")
    first = await asyncio.wait_for(handle.stdin.lines.get(), 60)
    assert first["uuid"] == "q1"

    c.begin_session_end()
    ending = asyncio.create_task(c.interrupt_for_session_end())
    ctrl = await asyncio.wait_for(handle.stdin.lines.get(), 60)
    assert ctrl["request"] == {"subtype": "interrupt", "cancel_queued": True}
    handle.stdout.feed(_lifecycle("q1", "cancelled"))
    handle.stdout.feed({"type": "control_response", "response": {
        "subtype": "success", "request_id": ctrl["request_id"], "response": {"cancelled": ["q1"]}}})
    handle.stdout.feed({"type": "result", "subtype": "error_during_execution",
                        "is_error": True, "terminal_reason": "aborted_streaming"})
    await asyncio.wait_for(ending, 60)
    await steering.settle()
    assert handle.stdin.lines.qsize() == 0  # "two" was never written
    handle.stdout.eof()
    await reader


@pytest.mark.asyncio
@pytest.mark.parametrize("in_flight_state", ["started", "cancelled"])
async def test_session_end_undelivered_queued_lists_the_in_flight_and_pending_messages(convo, in_flight_state):
    # Fix 19 review round 1, finding 2: the CONTROLLER NOTE requires checking
    # not just that session end writes nothing further, but that the re-queue
    # computation still lists the in-flight message (when the graceful
    # interrupt swept it, never "started") and every still-pending one, in
    # their original order. optio-agents cannot import undelivered_queued
    # (optio-claudecode depends on optio-agents, not the reverse), so this
    # feeds the events the real driver actually emitted into it here.
    c, handle = convo
    events: list[dict] = []
    c.on_event(events.append)
    ids = iter(["q1", "q2", "q3"])
    steering = make_steering(c, new_id=lambda: next(ids))
    reader = asyncio.create_task(c.run_reader())
    await c.send("long task")
    await asyncio.wait_for(handle.stdin.lines.get(), 60)
    await steering.send_when_ready("one")
    await steering.send_when_ready("two")
    await steering.send_when_ready("three")
    first = await asyncio.wait_for(handle.stdin.lines.get(), 60)
    assert first["uuid"] == "q1"

    c.begin_session_end()
    ending = asyncio.create_task(c.interrupt_for_session_end())
    ctrl = await asyncio.wait_for(handle.stdin.lines.get(), 60)
    assert ctrl["request"] == {"subtype": "interrupt", "cancel_queued": True}
    handle.stdout.feed(_lifecycle("q1", in_flight_state))
    handle.stdout.feed({"type": "control_response", "response": {
        "subtype": "success", "request_id": ctrl["request_id"], "response": {"cancelled": []}}})
    handle.stdout.feed({"type": "result", "subtype": "error_during_execution",
                        "is_error": True, "terminal_reason": "aborted_streaming"})
    await asyncio.wait_for(ending, 60)
    await steering.settle()
    assert handle.stdin.lines.qsize() == 0  # nothing further was written
    handle.stdout.eof()
    await reader

    undelivered = undelivered_queued(list(enumerate(events, 1)))
    if in_flight_state == "cancelled":
        assert undelivered == [("q1", "one"), ("q2", "two"), ("q3", "three")]
    else:
        assert undelivered == [("q2", "two"), ("q3", "three")]


@pytest.mark.asyncio
async def test_requeue_follows_the_resume_notice_one_at_a_time_over_the_real_driver(convo):
    # Fix 19: after "System: you have been resumed", what the last run left
    # undelivered goes out again in order, each under a new uuid, one at a
    # time (Fix 17's path), announced by x-optio-requeued.
    c, handle = convo
    events = []
    c.on_event(events.append)
    ids = iter(["n1", "n2"])
    steering = make_steering(c, new_id=lambda: next(ids))
    reader = asyncio.create_task(c.run_reader())

    await c.send("System: you have been resumed")
    notice = await asyncio.wait_for(handle.stdin.lines.get(), 60)
    assert notice["message"]["content"][0]["text"] == "System: you have been resumed"
    assert await steering.requeue([("q1", "one"), ("q2", "two")]) == ["n1", "n2"]
    first = await asyncio.wait_for(handle.stdin.lines.get(), 60)
    assert (first["uuid"], first["message"]["content"][0]["text"]) == ("n1", "one\n\n")
    assert handle.stdin.lines.qsize() == 0

    handle.stdout.feed(_lifecycle("n1", "started"))
    second = await asyncio.wait_for(handle.stdin.lines.get(), 60)
    assert (second["uuid"], second["message"]["content"][0]["text"]) == ("n2", "two\n\n")
    handle.stdout.eof()
    await reader
    await steering.settle()
    assert [e for e in events if e.get("type") == "x-optio-requeued"] == [
        {"type": "x-optio-requeued", "id": "q1", "new_id": "n1"},
        {"type": "x-optio-requeued", "id": "q2", "new_id": "n2"},
    ]


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
    assert sent["message"]["content"][0]["text"] == "steer\n\n"  # blank line: native queue (Fix 13a)
    handle.stdout.eof()
    await reader
    assert {"type": "x-optio-queued", "id": "q1", "text": "steer"} in events


@pytest.mark.asyncio
async def test_steering_interrupt_and_send_waits_for_the_result(convo):
    # Regression for review finding 2 (Task 3 fix round 1): the previous
    # version of this test fed control_response AND result before ever
    # reading stdin again, so a wrong is_turn_end would only make Steering
    # fall back to its 15 s timeout — the test would still pass. Use an
    # event-based sync point instead: after the interrupt is acked, assert
    # the send is still withheld, then feed the result and assert it lands.
    c, handle = convo
    events = []
    ack_seen = asyncio.Event()

    def watch(ev):
        events.append(ev)
        if ev.get("type") == "control_response":
            ack_seen.set()

    c.on_event(watch)
    steering = make_steering(c, new_id=lambda: "s1")
    reader = asyncio.create_task(c.run_reader())
    await c.send("long task")
    await asyncio.wait_for(handle.stdin.lines.get(), 60)
    task = asyncio.create_task(steering.interrupt_and_send("now"))
    ctrl = await asyncio.wait_for(handle.stdin.lines.get(), 60)
    assert ctrl["request"]["subtype"] == "interrupt"
    handle.stdout.feed({"type": "control_response", "response": {
        "subtype": "success", "request_id": ctrl["request_id"]}})
    await asyncio.wait_for(ack_seen.wait(), 60)
    # The interrupt ack alone must not release the held send: only the
    # turn's own result (is_turn_end) may.
    assert not task.done()
    assert handle.stdin.lines.qsize() == 0
    handle.stdout.feed({"type": "result", "subtype": "error_during_execution",
                        "is_error": True, "terminal_reason": "aborted_streaming"})
    msg = await asyncio.wait_for(handle.stdin.lines.get(), 60)
    assert msg["type"] == "user"
    assert msg["message"]["content"][0]["text"] == "now\n\n"  # blank line: native queue (Fix 13a)
    assert await asyncio.wait_for(task, 60) == "s1"
    handle.stdout.eof()
    await reader
    assert events[0] == {"type": "x-optio-interrupt", "by": "user"}


@pytest.mark.asyncio
async def test_pending_survives_a_stale_idle_racing_interrupt_and_send(convo):
    # Regression for review finding 1 (Task 3 fix round 1). Recorded order
    # (s1: result at 16.167, idle at 16.171; s2: 26.077, 26.082): the CLI's
    # own idle for a turn arrives a few ms after that turn's result.
    # interrupt_and_send reacts to the result immediately (is_pending()
    # already False, nothing to interrupt) and writes the next prompt within
    # microseconds; the stale idle — and then running, once the CLI takes
    # the new turn — must not erase that brand-new turn's pending state.
    c, handle = convo
    steering = make_steering(c, new_id=lambda: "s2")
    reader = asyncio.create_task(c.run_reader())
    await c.send("first task")
    await asyncio.wait_for(handle.stdin.lines.get(), 60)

    result_seen = asyncio.Event()
    c.on_event(lambda ev: result_seen.set() if ev.get("type") == "result" else None)
    handle.stdout.feed({"type": "result", "subtype": "success",
                        "result": "done", "is_error": False})
    await asyncio.wait_for(result_seen.wait(), 60)
    assert not c.is_pending()

    # interrupt_and_send sees the turn already ended: nothing to interrupt,
    # so it just writes the new prompt straight away.
    await steering.interrupt_and_send("now")
    sent = await asyncio.wait_for(handle.stdin.lines.get(), 60)
    assert sent["message"]["content"][0]["text"] == "now"
    assert c.is_pending()

    running_seen = asyncio.Event()

    def watch_running(ev):
        if ev.get("subtype") == "session_state_changed" and ev.get("state") == "running":
            running_seen.set()

    c.on_event(watch_running)
    handle.stdout.feed({"type": "system", "subtype": "session_state_changed", "state": "idle"})
    handle.stdout.feed({"type": "system", "subtype": "session_state_changed", "state": "running"})
    await asyncio.wait_for(running_seen.wait(), 60)

    assert c.is_pending()
    intr = asyncio.create_task(steering.interrupt())
    ctrl = await asyncio.wait_for(handle.stdin.lines.get(), 60)
    assert ctrl["type"] == "control_request"
    assert ctrl["request"]["subtype"] == "interrupt"
    handle.stdout.feed({"type": "control_response", "response": {
        "subtype": "success", "request_id": ctrl["request_id"]}})
    await asyncio.wait_for(intr, 60)
    handle.stdout.eof()
    await reader


@pytest.mark.asyncio
async def test_interrupt_after_a_merged_turns_idle_gate_still_writes_a_control_request(convo):
    # final-review M1. A merged turn (two sends, one result) leaves
    # is_pending() True until the idle a few ms later (see
    # test_idle_state_clears_pending_after_a_merged_turn above). An Interrupt
    # landing in that window must not leave Steering._interrupted set once
    # idle actually clears is_pending() -- otherwise the NEXT turn's own
    # interrupt() is a silent no-op: no marker, and (checked here) no
    # control_request reaches the CLI at all.
    c, handle = convo
    steering = make_steering(c, new_id=lambda: "s3")
    reader = asyncio.create_task(c.run_reader())

    await c.send("first")
    await asyncio.wait_for(handle.stdin.lines.get(), 60)
    await c.send("steer")  # joins the same turn: one result covers both
    await asyncio.wait_for(handle.stdin.lines.get(), 60)

    result_seen = asyncio.Event()
    c.on_event(lambda ev: result_seen.set() if ev.get("type") == "result" else None)
    handle.stdout.feed({"type": "result", "subtype": "success",
                        "result": "done", "is_error": False})
    await asyncio.wait_for(result_seen.wait(), 60)
    assert c.is_pending()  # merged turn: still waiting on the second send

    intr1 = asyncio.create_task(steering.interrupt())
    ctrl1 = await asyncio.wait_for(handle.stdin.lines.get(), 60)
    assert ctrl1["request"]["subtype"] == "interrupt"
    handle.stdout.feed({"type": "control_response", "response": {
        "subtype": "success", "request_id": ctrl1["request_id"]}})
    await asyncio.wait_for(intr1, 60)

    idle_seen = asyncio.Event()
    c.on_event(lambda ev: idle_seen.set()
               if ev.get("subtype") == "session_state_changed" and ev.get("state") == "idle"
               else None)
    handle.stdout.feed({"type": "system", "subtype": "session_state_changed", "state": "idle"})
    await asyncio.wait_for(idle_seen.wait(), 60)
    assert not c.is_pending()

    # A new turn starts.
    await c.send("second task")
    await asyncio.wait_for(handle.stdin.lines.get(), 60)
    running_seen = asyncio.Event()
    c.on_event(lambda ev: running_seen.set()
               if ev.get("subtype") == "session_state_changed" and ev.get("state") == "running"
               else None)
    handle.stdout.feed({"type": "system", "subtype": "session_state_changed", "state": "running"})
    await asyncio.wait_for(running_seen.wait(), 60)
    assert c.is_pending()

    intr2 = asyncio.create_task(steering.interrupt())
    ctrl2 = await asyncio.wait_for(handle.stdin.lines.get(), 60)
    assert ctrl2["request"]["subtype"] == "interrupt"
    handle.stdout.feed({"type": "control_response", "response": {
        "subtype": "success", "request_id": ctrl2["request_id"]}})
    await asyncio.wait_for(intr2, 60)

    handle.stdout.eof()
    await reader


# -- session_state_changed one-time warning (fix-3-brief) --------------------

@pytest.mark.asyncio
async def test_result_without_any_state_event_logs_one_warning(convo, caplog):
    # Production's launch env did not set CLAUDE_CODE_EMIT_SESSION_STATE_EVENTS,
    # so the CLI never emits session_state_changed and is_pending() can drift
    # after a merged turn (fix-3-brief root cause). Surface that once per
    # conversation rather than silently.
    c, handle = convo
    reader = asyncio.create_task(c.run_reader())
    with caplog.at_level("WARNING", logger="optio_claudecode.conversation"):
        handle.stdout.feed({"type": "result", "subtype": "success",
                            "result": "done", "is_error": False})
        await asyncio.sleep(0)
        handle.stdout.feed({"type": "result", "subtype": "success",
                            "result": "again", "is_error": False})
        await asyncio.sleep(0)
    handle.stdout.eof()
    await reader
    warnings = [r for r in caplog.records if r.levelname == "WARNING"]
    assert len(warnings) == 1
    assert "CLAUDE_CODE_EMIT_SESSION_STATE_EVENTS" in warnings[0].message


@pytest.mark.asyncio
async def test_result_after_a_state_event_logs_no_warning(convo, caplog):
    c, handle = convo
    reader = asyncio.create_task(c.run_reader())
    with caplog.at_level("WARNING", logger="optio_claudecode.conversation"):
        handle.stdout.feed({"type": "system", "subtype": "session_state_changed",
                            "state": "running"})
        await asyncio.sleep(0)
        handle.stdout.feed({"type": "result", "subtype": "success",
                            "result": "done", "is_error": False})
        await asyncio.sleep(0)
    handle.stdout.eof()
    await reader
    assert not [r for r in caplog.records if r.levelname == "WARNING"]
