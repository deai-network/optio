"""ConversationListener unit tests against a fake Conversation."""

import asyncio
import base64
import json
import logging

import aiohttp
import pytest

from optio_agents.conversation import ConversationClosed, PermissionDecision
from optio_claudecode.conversation_listener import ConversationListener
from optio_claudecode.steering import make_steering


class FakeConversation:
    def __init__(self):
        self.handlers = []
        self.perm_handler = None
        self.sent = []
        self.uuids = []
        self.interrupts = 0
        self.closed = False
        # Steering surface: busy is set by the test; interrupt() ends a busy
        # turn at once with a result event (the CLI's turn end).
        self.pending = False
        self.runtime_model = None

    def on_event(self, h):
        self.handlers.append(h)
        return lambda: self.handlers.remove(h)

    def on_permission_request(self, h):
        self.perm_handler = h
        return lambda: None

    def is_pending(self):
        return self.pending

    def emit_event(self, event):
        self.fire(event)

    async def send(self, text, *, uuid=None):
        if self.closed:
            raise ConversationClosed("closed")
        self.sent.append(text)
        self.uuids.append(uuid)

    async def interrupt(self):
        if self.closed:
            raise ConversationClosed("closed")
        self.interrupts += 1
        if self.pending:
            self.pending = False
            self.fire({"type": "result", "subtype": "error_during_execution",
                       "is_error": True, "terminal_reason": "aborted_streaming"})

    def fire(self, event):
        for h in list(self.handlers):
            h(event)


def _auth(pw):
    token = base64.b64encode(f"optio:{pw}".encode()).decode()
    return {"Authorization": f"Basic {token}"}


@pytest.fixture
async def listener():
    conv = FakeConversation()
    lst = ConversationListener(conv, password="pw")
    port = await lst.start("127.0.0.1")
    yield conv, lst, f"http://127.0.0.1:{port}"
    await lst.stop()


async def _read_events(resp, n, timeout=60):
    """Parse n SSE data frames from an open aiohttp response."""
    out = []
    buf = b""

    async def _go():
        nonlocal buf
        while len(out) < n:
            chunk = await resp.content.read(1024)
            if not chunk:
                break
            buf += chunk
            while b"\n\n" in buf:
                frame, buf = buf.split(b"\n\n", 1)
                data = [l[5:] for l in frame.split(b"\n") if l.startswith(b"data:")]
                if data:
                    out.append(json.loads(b"".join(data).strip()))

    await asyncio.wait_for(_go(), timeout)
    return out


async def test_replay_then_live_and_partial_exclusion(listener):
    conv, lst, url = listener
    conv.fire({"type": "user", "n": 1})
    conv.fire({"type": "stream_event", "n": "partial"})  # live-only
    conv.fire({"type": "result", "n": 2})
    async with aiohttp.ClientSession() as s:
        async with s.get(f"{url}/events", headers=_auth("pw")) as resp:
            assert resp.status == 200
            replay = await _read_events(resp, 2)
            assert [e["type"] for e in replay] == ["user", "result"]  # no partial
            conv.fire({"type": "stream_event", "n": "live-partial"})
            live = await _read_events(resp, 1)
            assert live[0]["type"] == "stream_event"  # partials DO flow live


async def test_last_event_id_resume(listener):
    conv, lst, url = listener
    conv.fire({"type": "user", "n": 1})    # seq 1
    conv.fire({"type": "result", "n": 2})  # seq 2
    async with aiohttp.ClientSession() as s:
        headers = {**_auth("pw"), "Last-Event-ID": "1"}
        async with s.get(f"{url}/events", headers=headers) as resp:
            events = await _read_events(resp, 1)
            assert events[0]["n"] == 2  # seq 1 skipped


async def test_send_interrupt_forwarding_and_closed(listener):
    conv, lst, url = listener
    async with aiohttp.ClientSession() as s:
        r = await s.post(f"{url}/send", json={"text": "hi"}, headers=_auth("pw"))
        assert r.status == 200 and conv.sent == ["hi"]
        r = await s.post(f"{url}/interrupt", json={}, headers=_auth("pw"))
        assert r.status == 200 and conv.interrupts == 1
        conv.closed = True
        r = await s.post(f"{url}/send", json={"text": "x"}, headers=_auth("pw"))
        assert r.status == 409


async def test_permission_roundtrip_and_second_answer_404(listener):
    conv, lst, url = listener

    class Req:
        raw = {"request_id": "perm-1"}
        tool_name = "Bash"
        input = {}

    task = asyncio.create_task(conv.perm_handler(Req()))
    # Wait until the handler has actually parked the pending request before we
    # answer it; polling this observable avoids a fixed wall-clock delay that
    # flakes when the handler task is CPU-starved and hasn't registered yet.
    import time
    end = time.monotonic() + 60
    while time.monotonic() < end:
        if "perm-1" in lst._pending_permissions:
            break
        await asyncio.sleep(0.02)
    assert "perm-1" in lst._pending_permissions
    async with aiohttp.ClientSession() as s:
        r = await s.post(f"{url}/permission",
                         json={"request_id": "perm-1", "behavior": "allow"},
                         headers=_auth("pw"))
        assert r.status == 200
        decision = await asyncio.wait_for(task, 60)
        assert isinstance(decision, PermissionDecision)
        assert decision.behavior == "allow"
        r = await s.post(f"{url}/permission",
                         json={"request_id": "perm-1", "behavior": "deny"},
                         headers=_auth("pw"))
        assert r.status == 404
    # answered broadcast landed in the buffer
    assert any(e.get("type") == "x-optio-permission-answered"
               for _, e in lst._buffer)


async def test_auth_rejected(listener):
    conv, lst, url = listener
    async with aiohttp.ClientSession() as s:
        r = await s.get(f"{url}/events", headers=_auth("WRONG"))
        assert r.status == 401
        r = await s.post(f"{url}/send", json={"text": "x"})
        assert r.status == 401


async def test_stop_returns_promptly_with_open_sse(listener):
    # An open /events SSE handler is a long-lived loop. stop() must wake it so
    # runner.cleanup() does not block on aiohttp's graceful-shutdown wait —
    # otherwise the session's cooperative-cancel teardown overruns its grace
    # period and the resume snapshot is never captured.
    conv, lst, url = listener
    async with aiohttp.ClientSession() as s:
        async with s.get(f"{url}/events", headers=_auth("pw")) as resp:
            conv.fire({"type": "system", "subtype": "init"})
            await _read_events(resp, 1)  # handler is now in its live loop
            # Must return well under aiohttp's 60s default graceful wait.
            await asyncio.wait_for(lst.stop(), timeout=5)


async def test_buffer_export_reprime_continues_seq():
    # Resume persistence: a listener's buffer can be exported and used to
    # re-prime a fresh listener, so a viewer after resume sees prior history;
    # new events continue the seq monotonically above the restored max.
    conv = FakeConversation()
    lst = ConversationListener(conv, password="pw")
    conv.fire({"type": "user", "n": 1})
    conv.fire({"type": "result", "n": 2})
    exported = lst.export_buffer()
    assert [e["n"] for _, e in [(x[0], x[1]) for x in exported]] == [1, 2]

    conv2 = FakeConversation()
    lst2 = ConversationListener(
        conv2, password="pw", initial_events=[(x[0], x[1]) for x in exported],
    )
    # The re-prime appends one resume marker after the restored history.
    assert [e.get("n") for _, e in lst2._buffer] == [1, 2, None]
    assert lst2._buffer[-1] == (exported[-1][0] + 1, {"type": "x-optio-resumed"})
    conv2.fire({"type": "user", "n": 3})
    assert lst2._buffer[-1][0] > exported[-1][0]


async def test_export_buffer_excludes_terminal_closed():
    # The terminal x-optio-closed marks THIS run's end, not conversation
    # content; persisting + replaying it on resume would make the UI treat the
    # live resumed session as already closed. export_buffer must drop it.
    conv = FakeConversation()
    lst = ConversationListener(conv, password="pw")
    conv.fire({"type": "user", "n": 1})
    conv.fire({"type": "result", "n": 2})
    conv.fire({"type": "x-optio-closed", "reason": "process ended"})
    exported = lst.export_buffer()
    types = [e.get("type") for _, e in [(x[0], x[1]) for x in exported]]
    assert "x-optio-closed" not in types
    assert types == ["user", "result"]


async def test_reprime_appends_one_resumed_marker():
    # A resumed run's process is new: whatever the prior run left running (a
    # background task, an unanswered tool call) will never report. The
    # re-prime appends exactly one x-optio-resumed marker after the restored
    # history so the widget can stop those rows; live events follow it.
    conv = FakeConversation()
    lst = ConversationListener(
        conv, password="pw",
        initial_events=[(1, {"type": "user"}), (2, {"type": "result"})],
    )
    assert list(lst._buffer) == [
        (1, {"type": "user"}), (2, {"type": "result"}),
        (3, {"type": "x-optio-resumed"}),
    ]
    conv.fire({"type": "assistant"})
    assert lst._buffer[-1] == (4, {"type": "assistant"})


async def test_resumed_marker_persists_and_is_added_once_per_reprime():
    # The marker is persisted with the buffer (unlike x-optio-closed), so a
    # second resume replays the first marker where the first run ended and
    # appends its own.
    first = ConversationListener(
        FakeConversation(), password="pw", initial_events=[(1, {"type": "user"})],
    )
    exported = first.export_buffer()
    assert [e["type"] for _, e in exported] == ["user", "x-optio-resumed"]
    second = ConversationListener(
        FakeConversation(), password="pw",
        initial_events=[(x[0], x[1]) for x in exported],
    )
    assert [(seq, e["type"]) for seq, e in second._buffer] == [
        (1, "user"), (2, "x-optio-resumed"), (3, "x-optio-resumed"),
    ]


async def test_fresh_start_has_no_resumed_marker():
    assert list(ConversationListener(FakeConversation(), password="pw")._buffer) == []
    empty = ConversationListener(FakeConversation(), password="pw", initial_events=[])
    assert list(empty._buffer) == []


# -- steering routes (docs/2026-09-13-conversation-steering-design.md) --------

async def test_send_returns_id_and_queued_and_buffers_the_queued_event(listener):
    conv, lst, url = listener
    async with aiohttp.ClientSession() as s:
        r = await s.post(f"{url}/send", json={"text": "hi"}, headers=_auth("pw"))
        idle = await r.json()
        assert r.status == 200 and idle["ok"] is True and idle["queued"] is False
        assert isinstance(idle["id"], str) and idle["id"]
        conv.pending = True
        r = await s.post(f"{url}/send", json={"text": "steer"}, headers=_auth("pw"))
        busy = await r.json()
        assert busy["queued"] is True and busy["id"] != idle["id"]
    # the busy send gets a trailing blank line on the wire (Fix 13a); the
    # x-optio-queued event below still carries the ORIGINAL text.
    assert conv.sent == ["hi", "steer\n\n"]
    queued = [e for _, e in lst._buffer if e.get("type") == "x-optio-queued"]
    assert queued == [{"type": "x-optio-queued", "id": busy["id"], "text": "steer"}]


async def test_steer_interrupts_waits_for_the_turn_end_then_sends(listener):
    conv, lst, url = listener
    conv.pending = True
    async with aiohttp.ClientSession() as s:
        r = await s.post(f"{url}/steer", json={"text": "now"}, headers=_auth("pw"))
        body = await r.json()
    assert r.status == 200 and body["ok"] is True and isinstance(body["id"], str)
    assert conv.interrupts == 1 and conv.sent == ["now\n\n"]  # blank line (Fix 13a)
    types = [e.get("type") for _, e in lst._buffer]
    assert types.index("x-optio-interrupt") < types.index("result")


async def test_steer_with_empty_text_only_interrupts(listener):
    conv, lst, url = listener
    conv.pending = True
    async with aiohttp.ClientSession() as s:
        r = await s.post(f"{url}/steer", json={"text": ""}, headers=_auth("pw"))
        body = await r.json()
    assert r.status == 200 and body == {"ok": True, "id": None}
    assert conv.interrupts == 1 and conv.sent == []


async def test_steer_rejects_bad_up_to(listener):
    conv, lst, url = listener
    async with aiohttp.ClientSession() as s:
        r = await s.post(f"{url}/steer", json={"text": "", "upTo": 5}, headers=_auth("pw"))
        assert r.status == 400


async def test_steer_with_up_to_only_interrupts(listener):
    # Fix 17: Send now on a queued bubble posts upTo. Queued messages
    # already reach Claude one at a time, in order, so upTo needs nothing
    # beyond the interrupt: no cancel, no re-send, no x-optio-requeued.
    conv, lst, url = listener
    conv.pending = True
    async with aiohttp.ClientSession() as s:
        r = await s.post(f"{url}/steer", json={"text": "", "upTo": "some-id"},
                          headers=_auth("pw"))
        body = await r.json()
    assert r.status == 200 and body == {"ok": True, "id": None}
    assert conv.interrupts == 1 and conv.sent == []
    assert not any(e.get("type") == "x-optio-requeued" for _, e in lst._buffer)


async def test_steer_rejects_bad_text_closed_and_unauthorized(listener):
    conv, lst, url = listener
    async with aiohttp.ClientSession() as s:
        r = await s.post(f"{url}/steer", json={"text": 5}, headers=_auth("pw"))
        assert r.status == 400
        r = await s.post(f"{url}/steer", json={"text": "x"})
        assert r.status == 401
        conv.closed = True
        r = await s.post(f"{url}/steer", json={"text": "x"}, headers=_auth("pw"))
        assert r.status == 409


async def test_interrupt_emits_the_marker_only_while_busy(listener):
    conv, lst, url = listener
    async with aiohttp.ClientSession() as s:
        await s.post(f"{url}/interrupt", json={}, headers=_auth("pw"))
        assert not any(e.get("type") == "x-optio-interrupt" for _, e in lst._buffer)
        conv.pending = True
        await s.post(f"{url}/interrupt", json={}, headers=_auth("pw"))
    assert [e for _, e in lst._buffer if e.get("type") == "x-optio-interrupt"] == [
        {"type": "x-optio-interrupt", "by": "user"},
    ]
    assert conv.interrupts == 2


async def test_steering_events_persist_across_a_resume():
    conv = FakeConversation()
    lst = ConversationListener(conv, password="pw")
    conv.pending = True
    await lst._steering.send_when_ready("steer")
    await lst._steering.interrupt()
    exported = lst.export_buffer()
    types = [e["type"] for _, e in exported]
    assert "x-optio-queued" in types and "x-optio-interrupt" in types
    lst2 = ConversationListener(
        FakeConversation(), password="pw",
        initial_events=[(x[0], x[1]) for x in exported],
    )
    assert [e["type"] for _, e in lst2._buffer] == types + ["x-optio-resumed"]


# -- start-of-message marker (Fix 12) -----------------------------------------
# Owner ruling 2026-09-14: an interval "HH:MM - HH:MM" for a streamed agent
# message needs an EXACT start, stamped once by the listener (a server clock
# read at receipt), so the value is identical live and on replay. The clock is
# injected so these tests are deterministic (never read the wall clock).

def _message_start(msg_id: str) -> dict:
    return {"type": "stream_event", "event": {"type": "message_start", "message": {"id": msg_id, "content": []}}}


async def test_message_start_gets_a_marker_before_the_stream_event_in_the_buffer():
    conv = FakeConversation()
    lst = ConversationListener(conv, password="pw", clock=lambda: 1700000000123)
    conv.fire(_message_start("msg_1"))
    # The stream_event itself stays unbuffered (UNBUFFERED_TYPES); only the
    # marker this fix adds lands in the buffer.
    assert [e for _, e in lst._buffer] == [
        {"type": "x-optio-message-start", "id": "msg_1", "ts": 1700000000123},
    ]


async def test_message_start_marker_precedes_the_stream_event_live():
    # Not the shared `listener` fixture: this needs an injected clock, and the
    # fixture always builds a ConversationListener with the real one.
    conv = FakeConversation()
    lst = ConversationListener(conv, password="pw", clock=lambda: 1)
    port = await lst.start("127.0.0.1")
    try:
        async with aiohttp.ClientSession() as s:
            async with s.get(f"http://127.0.0.1:{port}/events", headers=_auth("pw")) as resp:
                conv.fire(_message_start("msg_1"))
                live = await _read_events(resp, 2)
    finally:
        await lst.stop()
    assert live[0] == {"type": "x-optio-message-start", "id": "msg_1", "ts": 1}
    assert live[1]["type"] == "stream_event"


async def test_message_start_marker_appears_exactly_once_per_message_start():
    conv = FakeConversation()
    lst = ConversationListener(conv, password="pw", clock=lambda: 1)
    conv.fire(_message_start("m1"))
    conv.fire({"type": "stream_event", "event": {"type": "content_block_start"}})
    conv.fire({"type": "stream_event", "event": {"type": "content_block_delta", "delta": {}}})
    conv.fire(_message_start("m2"))
    markers = [e for _, e in lst._buffer if e.get("type") == "x-optio-message-start"]
    assert [m["id"] for m in markers] == ["m1", "m2"]


async def test_message_start_marker_is_persisted_by_export_buffer():
    conv = FakeConversation()
    lst = ConversationListener(conv, password="pw", clock=lambda: 42)
    conv.fire(_message_start("msg_1"))
    conv.fire({"type": "result", "n": 1})
    exported = lst.export_buffer()
    assert [e["type"] for _, e in exported] == ["x-optio-message-start", "result"]
    assert exported[0][1] == {"type": "x-optio-message-start", "id": "msg_1", "ts": 42}


async def test_message_start_with_no_message_id_emits_no_marker():
    # Defensive: a malformed/missing id must not produce a marker with no id.
    conv = FakeConversation()
    lst = ConversationListener(conv, password="pw", clock=lambda: 1)
    conv.fire({"type": "stream_event", "event": {"type": "message_start", "message": {}}})
    assert list(lst._buffer) == []


# -- session end (Fix 19, docs/2026-09-15-steering-session-end-design.md) -----
# Owner rulings 2026-09-15 (finding 6): a session that ends mid-turn gets a
# persisted {"type":"x-optio-interrupt","by":"session"} marker, and the text
# a cut-off message had streamed is kept as x-optio-partial. Both reach the
# buffer before x-optio-closed, so export_buffer() persists them.

RUNNING = {"type": "system", "subtype": "session_state_changed", "state": "running"}
IDLE = {"type": "system", "subtype": "session_state_changed", "state": "idle"}
CLOSED = {"type": "x-optio-closed", "reason": "process ended"}
SESSION_END = {"type": "x-optio-interrupt", "by": "session"}


def _delta(text: str, kind: str = "text_delta") -> dict:
    field = "thinking" if kind == "thinking_delta" else "text"
    return {"type": "stream_event", "event": {
        "type": "content_block_delta", "index": 0, "delta": {"type": kind, field: text}}}


def _assistant(msg_id: str, text: str) -> dict:
    return {"type": "assistant", "message": {
        "id": msg_id, "role": "assistant", "content": [{"type": "text", "text": text}]}}


def _session_events(lst) -> list[dict]:
    return [e for _, e in lst._buffer if e.get("type") in ("x-optio-partial", "x-optio-interrupt")]


async def test_a_session_end_mid_stream_buffers_the_partial_then_the_marker_before_closed():
    conv = FakeConversation()
    lst = ConversationListener(conv, password="pw", clock=lambda: 1)
    for event in (RUNNING, _message_start("m1"), _delta("Because I"), _delta(" could")):
        conv.fire(event)
    conv.fire(CLOSED)
    partial = {"type": "x-optio-partial", "id": "m1", "text": "Because I could"}
    assert [e for _, e in lst._buffer][-3:] == [partial, SESSION_END, CLOSED]
    assert [e for _, e in lst.export_buffer()][-2:] == [partial, SESSION_END]


async def test_hidden_thinking_leaves_no_partial_only_the_marker():
    conv = FakeConversation()
    lst = ConversationListener(conv, password="pw", clock=lambda: 1)
    for event in (
        RUNNING, _message_start("m1"), _delta("", "thinking_delta"),
        {"type": "assistant", "message": {"id": "m1", "role": "assistant",
                                          "content": [{"type": "thinking", "thinking": "", "signature": "s"}]}},
    ):
        conv.fire(event)
    conv.fire(CLOSED)
    assert _session_events(lst) == [SESSION_END]


async def test_an_idle_session_end_adds_nothing():
    conv = FakeConversation()
    lst = ConversationListener(conv, password="pw", clock=lambda: 1)
    for event in (
        RUNNING, _message_start("m1"), _delta("Done"), _assistant("m1", "Done"),
        {"type": "result", "subtype": "success", "result": "Done"}, IDLE,
    ):
        conv.fire(event)
    conv.fire(CLOSED)
    assert _session_events(lst) == []
    fresh = ConversationListener(FakeConversation(), password="pw")
    fresh._conversation.fire(CLOSED)
    assert _session_events(fresh) == []


async def test_the_partial_holds_only_the_open_block_of_the_current_message():
    # Reset at the block's final assistant event ...
    conv = FakeConversation()
    lst = ConversationListener(conv, password="pw", clock=lambda: 1)
    for event in (RUNNING, _message_start("m1"), _delta("Intro"), _assistant("m1", "Intro"), _delta("Second")):
        conv.fire(event)
    conv.fire(CLOSED)
    assert _session_events(lst)[0] == {"type": "x-optio-partial", "id": "m1", "text": "Second"}
    # ... and at the next message_start.
    conv2 = FakeConversation()
    lst2 = ConversationListener(conv2, password="pw", clock=lambda: 1)
    for event in (RUNNING, _message_start("m1"), _delta("Old"), _message_start("m2"), _delta("New")):
        conv2.fire(event)
    conv2.fire(CLOSED)
    assert _session_events(lst2)[0] == {"type": "x-optio-partial", "id": "m2", "text": "New"}


async def test_is_pending_counts_as_a_running_turn_without_state_events():
    conv = FakeConversation()
    conv.pending = True
    lst = ConversationListener(conv, password="pw", clock=lambda: 1)
    for event in (_message_start("m1"), _delta("Half")):
        conv.fire(event)
    conv.fire(CLOSED)
    assert _session_events(lst) == [
        {"type": "x-optio-partial", "id": "m1", "text": "Half"}, SESSION_END,
    ]


async def test_an_announced_session_end_is_marked_once_and_a_flushed_answer_needs_no_partial():
    # The graceful path: the marker goes out before the interrupt; the CLI
    # then flushes the partial as its own final assistant event.
    conv = FakeConversation()
    lst = ConversationListener(conv, password="pw", clock=lambda: 1)
    for event in (RUNNING, _message_start("m1"), _delta("Because I")):
        conv.fire(event)
    lst.announce_session_end()
    lst.announce_session_end()
    for event in (
        _assistant("m1", "Because I could"),
        {"type": "user", "message": {"role": "user", "content": [
            {"type": "text", "text": "[Request interrupted by user]"}]}},
        {"type": "result", "subtype": "error_during_execution", "is_error": True,
         "terminal_reason": "aborted_streaming"},
        IDLE,
    ):
        conv.fire(event)
    conv.fire(CLOSED)
    assert _session_events(lst) == [SESSION_END]
    types = [e.get("type") for _, e in lst._buffer]
    assert types.index("x-optio-interrupt") < types.index("assistant")


async def test_an_announced_session_end_whose_turn_never_ended_adds_the_partial_after_the_marker():
    conv = FakeConversation()
    lst = ConversationListener(conv, password="pw", clock=lambda: 1)
    for event in (RUNNING, _message_start("m1"), _delta("Because I")):
        conv.fire(event)
    lst.announce_session_end()
    conv.fire(_delta(" could"))
    conv.fire(CLOSED)
    assert [e for _, e in lst._buffer][-3:] == [
        SESSION_END, {"type": "x-optio-partial", "id": "m1", "text": "Because I could"}, CLOSED,
    ]


async def test_the_partial_and_the_marker_reach_live_viewers_before_closed(listener):
    conv, lst, url = listener
    async with aiohttp.ClientSession() as s:
        async with s.get(f"{url}/events", headers=_auth("pw")) as resp:
            conv.fire(RUNNING)
            conv.fire(_message_start("m1"))   # its marker + the stream_event
            conv.fire(_delta("Half"))
            conv.fire(CLOSED)
            live = await _read_events(resp, 7)
    assert [e["type"] for e in live][-3:] == ["x-optio-partial", "x-optio-interrupt", "x-optio-closed"]
    assert live[-2] == SESSION_END


# -- re-queue on resume (Fix 19, owner ruling 2026-09-15, finding 6 #2) -------

def _restored(*events):
    return list(enumerate(events, start=1))


def _queued(qid, text):
    return {"type": "x-optio-queued", "id": qid, "text": text}


def _lc(qid, state):
    return {"type": "command_lifecycle", "command_uuid": qid, "state": state,
            "uuid": f"lc-{qid}-{state}", "session_id": "s"}


async def test_the_resume_marker_lists_the_ids_it_will_requeue():
    lst = ConversationListener(FakeConversation(), password="pw", initial_events=_restored(
        _queued("q1", "one"), _lc("q1", "queued"), _queued("q2", "two"),
    ))
    assert lst._buffer[-1] == (4, {"type": "x-optio-resumed", "requeued": ["q1", "q2"]})


async def test_requeue_undelivered_resends_in_order_under_new_ids_and_skips_what_started():
    conv = FakeConversation()
    ids = iter(["n1", "n2"])
    lst = ConversationListener(conv, password="pw", initial_events=_restored(
        _queued("q1", "one"), _queued("q2", "two"), _lc("q2", "started"), _queued("q3", "three"),
    ), steering=make_steering(conv, new_id=lambda: next(ids)))
    conv.pending = True  # the resume notice's turn runs
    assert await lst.requeue_undelivered() == ["q1", "q3"]
    assert conv.sent == ["one\n\n"] and conv.uuids == ["n1"]
    assert [e for _, e in lst._buffer if e.get("type") == "x-optio-requeued"] == [
        {"type": "x-optio-requeued", "id": "q1", "new_id": "n1"},
        {"type": "x-optio-requeued", "id": "q3", "new_id": "n2"},
    ]
    conv.fire(_lc("n1", "started"))
    await lst._steering.settle()
    assert conv.sent == ["one\n\n", "three\n\n"]
    assert await lst.requeue_undelivered() == []  # once only


async def test_requeue_undelivered_on_a_fresh_start_does_nothing():
    conv = FakeConversation()
    lst = ConversationListener(conv, password="pw")
    assert await lst.requeue_undelivered() == []
    assert conv.sent == []


async def test_requeue_undelivered_after_the_conversation_closed_sends_nothing(caplog):
    conv = FakeConversation()
    conv.closed = True
    lst = ConversationListener(conv, password="pw", initial_events=_restored(_queued("q1", "one")))
    with caplog.at_level(logging.WARNING, logger="optio_claudecode.conversation_listener"):
        assert await lst.requeue_undelivered() == []
    assert conv.sent == [] and "re-queued" in caplog.text
