"""CodexConversation unit tests against an in-process fake app-server handle.

The fake handle feeds JSONL lines (like `codex app-server` would emit on
stdout) and captures what the driver writes to stdin. Mirrors optio-grok's
test_conversation.py, but frames the codex app-server protocol (JSON-RPC 2.0
with the "jsonrpc" field omitted) instead of ACP.
"""

import asyncio
import json

import pytest

from optio_agents.conversation import (
    Conversation,
    ConversationClosed,
    PermissionDecision,
)
from optio_codex.conversation import CodexConversation


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


MODEL_LIST = {
    "data": [
        {"id": "gpt-5.5", "displayName": "GPT-5.5", "description": "",
         "hidden": False, "isDefault": True, "model": "gpt-5.5",
         "defaultReasoningEffort": "medium", "supportedReasoningEfforts": []},
        {"id": "gpt-5.4-mini", "displayName": "GPT-5.4 Mini", "description": "",
         "hidden": False, "isDefault": False, "model": "gpt-5.4-mini",
         "defaultReasoningEffort": "medium", "supportedReasoningEfforts": []},
    ],
    "nextCursor": None,
}


async def _bootstrap(c, handle, thread_id="t1", resume=False):
    """Drive the app-server handshake by answering the driver's requests.

    Wire order (pinned by the live probe): initialize (request) ->
    initialized (notification) -> account/read -> model/list ->
    thread/start | thread/resume.
    """
    boot = asyncio.create_task(c.bootstrap())
    req = await asyncio.wait_for(handle.stdin.lines.get(), 1)
    assert req["method"] == "initialize"
    assert "jsonrpc" not in req                      # omitted on the wire
    assert req["params"]["clientInfo"]["name"] == "optio_codex"
    assert "experimentalApi" not in json.dumps(req)  # stable surface only
    handle.stdout.feed({"id": req["id"], "result": {
        "userAgent": "codex/0.142.5-fake", "codexHome": "/h",
        "platformFamily": "fake", "platformOs": "fake"}})
    note = await asyncio.wait_for(handle.stdin.lines.get(), 1)
    assert note == {"method": "initialized"}         # notification, no id
    req = await asyncio.wait_for(handle.stdin.lines.get(), 1)
    assert req["method"] == "account/read"
    assert req["params"] == {"refreshToken": False}
    handle.stdout.feed({"id": req["id"], "result": {
        "account": {"type": "apikey"}, "requiresOpenaiAuth": False}})
    req = await asyncio.wait_for(handle.stdin.lines.get(), 1)
    assert req["method"] == "model/list"
    handle.stdout.feed({"id": req["id"], "result": MODEL_LIST})
    req = await asyncio.wait_for(handle.stdin.lines.get(), 1)
    if resume:
        assert req["method"] == "thread/resume"
        assert req["params"]["threadId"] == thread_id
    else:
        assert req["method"] == "thread/start"
        assert req["params"]["cwd"] == "/w"
        # 0.142.5 schema: the field is `sandbox` (kebab-case enum), NOT
        # `sandboxPolicy` (that object exists only on turn/start).
        assert req["params"]["sandbox"] == "workspace-write"
        assert req["params"]["approvalPolicy"] in (
            "never", "on-request", "untrusted", "on-failure")
    handle.stdout.feed({"id": req["id"], "result": {
        "thread": {"id": thread_id}, "model": "gpt-5.5"}})
    await asyncio.wait_for(boot, 1)


def _delta(item_id: str, delta: str, turn_id="turn-1"):
    return {"method": "item/agentMessage/delta", "params": {
        "threadId": "t1", "turnId": turn_id, "itemId": item_id, "delta": delta}}


def _item_completed(item: dict, turn_id="turn-1"):
    return {"method": "item/completed", "params": {
        "threadId": "t1", "turnId": turn_id, "item": item, "completedAtMs": 0}}


def _turn_completed(turn_id="turn-1", status="completed"):
    return {"method": "turn/completed", "params": {
        "threadId": "t1", "turn": {"id": turn_id, "status": status, "items": []}}}


def _cmd_approval(req_id: int, command="echo hi"):
    return {"id": req_id, "method": "item/commandExecution/requestApproval",
            "params": {"threadId": "t1", "turnId": "turn-1", "itemId": "i-1",
                       "command": command, "cwd": "/w", "reason": None,
                       "startedAtMs": 0}}


# Stored prior conversation the real thread/resume returns inline as
# result.thread.turns[].items[] (verified live against a real codex rollout):
# two completed turns — user+agent messages and a tool call — each item shaped
# like a live item/completed's `item` (type, id, + type-specific fields).
_RESUME_TURNS = [
    {"id": "turn-h1", "items": [
        {"type": "userMessage", "id": "u1", "text": "prior question"},
        {"type": "agentMessage", "id": "a1", "text": "prior answer one"},
    ]},
    {"id": "turn-h2", "items": [
        {"type": "userMessage", "id": "u2", "text": "second question"},
        {"type": "commandExecution", "id": "c1", "command": "ls",
         "cwd": "/w", "status": "completed", "exitCode": 0},
        {"type": "agentMessage", "id": "a2", "text": "prior answer two"},
    ]},
]


async def _bootstrap_resume(c, handle, thread_id="t9", turns=None):
    """Drive the handshake, answering thread/resume with a result that carries
    the prior conversation inline (result.thread.turns[].items[]) — the live
    shape backfill parses."""
    boot = asyncio.create_task(c.bootstrap())
    req = await asyncio.wait_for(handle.stdin.lines.get(), 1)
    assert req["method"] == "initialize"
    handle.stdout.feed({"id": req["id"], "result": {
        "userAgent": "codex/0.142.5-fake", "codexHome": "/h",
        "platformFamily": "fake", "platformOs": "fake"}})
    assert await asyncio.wait_for(handle.stdin.lines.get(), 1) == {"method": "initialized"}
    req = await asyncio.wait_for(handle.stdin.lines.get(), 1)
    assert req["method"] == "account/read"
    handle.stdout.feed({"id": req["id"], "result": {
        "account": {"type": "apikey"}, "requiresOpenaiAuth": False}})
    req = await asyncio.wait_for(handle.stdin.lines.get(), 1)
    assert req["method"] == "model/list"
    handle.stdout.feed({"id": req["id"], "result": MODEL_LIST})
    req = await asyncio.wait_for(handle.stdin.lines.get(), 1)
    assert req["method"] == "thread/resume"
    thread = {"id": thread_id}
    if turns is not None:
        thread["turns"] = turns
    handle.stdout.feed({"id": req["id"], "result": {
        "thread": thread, "model": "gpt-5.5"}})
    await asyncio.wait_for(boot, 1)


@pytest.fixture
def convo():
    handle = _FakeHandle()
    c = CodexConversation(cwd="/w", permission_gate=True)
    c.attach(handle)
    return c, handle


def test_satisfies_conversation_protocol(convo):
    c, _ = convo
    assert isinstance(c, Conversation)


@pytest.mark.asyncio
async def test_send_receive_and_on_event_transparent(convo):
    c, handle = convo
    reader = asyncio.create_task(c.run_reader())
    await _bootstrap(c, handle)

    events, texts = [], []
    c.on_event(events.append)
    c.on_message(texts.append)

    assert not c.is_pending()
    await c.send("say PONG")
    assert c.is_pending()
    turn_req = await asyncio.wait_for(handle.stdin.lines.get(), 1)
    assert turn_req["method"] == "turn/start"
    assert turn_req["params"]["threadId"] == "t1"
    assert turn_req["params"]["input"] == [{"type": "text", "text": "say PONG"}]

    # ACK is NOT the turn end.
    handle.stdout.feed({"id": turn_req["id"], "result": {
        "turn": {"id": "turn-1", "status": "inProgress", "items": []}}})
    await _wait_for(lambda: c.is_pending())

    for piece in ("PO", "NG"):
        handle.stdout.feed(_delta("i-msg", piece))
    handle.stdout.feed(_item_completed(
        {"type": "agentMessage", "id": "i-msg", "text": "PONG"}))
    handle.stdout.feed(_turn_completed())

    reply = await asyncio.wait_for(_first(texts), 2)
    assert reply == "PONG"
    await _wait_for(lambda: not c.is_pending())

    methods = [e.get("method") for e in events]
    assert methods.count("item/agentMessage/delta") >= 2  # raw objects, unmodified

    handle.stdout.eof()
    await reader


@pytest.mark.asyncio
async def test_reasoning_deltas_not_folded_into_answer(convo):
    c, handle = convo
    reader = asyncio.create_task(c.run_reader())
    await _bootstrap(c, handle)
    texts = []
    c.on_message(texts.append)
    await c.send("think then answer")
    turn_req = await asyncio.wait_for(handle.stdin.lines.get(), 1)
    handle.stdout.feed({"id": turn_req["id"], "result": {
        "turn": {"id": "turn-1", "status": "inProgress", "items": []}}})
    handle.stdout.feed({"method": "item/reasoning/summaryTextDelta", "params": {
        "threadId": "t1", "turnId": "turn-1", "itemId": "i-r",
        "delta": "hmm", "summaryIndex": 0}})
    handle.stdout.feed(_delta("i-msg", "ANSWER"))
    handle.stdout.feed(_turn_completed())
    reply = await asyncio.wait_for(_first(texts), 2)
    assert reply == "ANSWER"
    handle.stdout.eof()
    await reader


@pytest.mark.asyncio
async def test_item_completed_text_is_authoritative(convo):
    # A replay/drop gap in deltas is healed by item/completed's full text.
    c, handle = convo
    reader = asyncio.create_task(c.run_reader())
    await _bootstrap(c, handle)
    texts = []
    c.on_message(texts.append)
    await c.send("x")
    await asyncio.wait_for(handle.stdin.lines.get(), 1)
    handle.stdout.feed(_delta("i-msg", "PO"))  # "NG" delta lost
    handle.stdout.feed(_item_completed(
        {"type": "agentMessage", "id": "i-msg", "text": "PONG"}))
    handle.stdout.feed(_turn_completed())
    assert await asyncio.wait_for(_first(texts), 2) == "PONG"
    handle.stdout.eof()
    await reader


@pytest.mark.asyncio
async def test_two_agent_messages_in_one_turn_concatenate(convo):
    c, handle = convo
    reader = asyncio.create_task(c.run_reader())
    await _bootstrap(c, handle)
    texts = []
    c.on_message(texts.append)
    await c.send("x")
    await asyncio.wait_for(handle.stdin.lines.get(), 1)
    handle.stdout.feed(_delta("i-1", "part1 "))
    handle.stdout.feed(_item_completed(
        {"type": "agentMessage", "id": "i-1", "text": "part1 "}))
    handle.stdout.feed(_delta("i-2", "part2"))
    handle.stdout.feed(_item_completed(
        {"type": "agentMessage", "id": "i-2", "text": "part2"}))
    handle.stdout.feed(_turn_completed())
    assert await asyncio.wait_for(_first(texts), 2) == "part1 part2"
    handle.stdout.eof()
    await reader


@pytest.mark.asyncio
async def test_permission_request_roundtrip_deny(convo):
    c, handle = convo
    reader = asyncio.create_task(c.run_reader())
    await _bootstrap(c, handle)

    seen = {}

    async def handler(req):
        seen["tool"] = req.tool_name
        seen["input"] = req.input
        seen["raw_id"] = req.raw.get("id")
        return PermissionDecision(behavior="deny", message="nope")

    c.on_permission_request(handler)
    handle.stdout.feed(_cmd_approval(99))
    resp = await asyncio.wait_for(handle.stdin.lines.get(), 2)
    assert resp["id"] == 99
    assert resp["result"] == {"decision": "decline"}
    assert seen["tool"] == "echo hi"          # command string as the tool name
    assert seen["input"]["command"] == "echo hi"
    assert seen["raw_id"] == 99               # correlation key for the listener
    handle.stdout.eof()
    await reader


@pytest.mark.asyncio
async def test_permission_request_allow_answers_accept(convo):
    c, handle = convo
    reader = asyncio.create_task(c.run_reader())
    await _bootstrap(c, handle)

    async def handler(req):
        return PermissionDecision(behavior="allow")

    c.on_permission_request(handler)
    handle.stdout.feed(_cmd_approval(7))
    resp = await asyncio.wait_for(handle.stdin.lines.get(), 2)
    assert resp == {"id": 7, "result": {"decision": "accept"}}
    handle.stdout.eof()
    await reader


@pytest.mark.asyncio
async def test_file_change_approval_maps_too(convo):
    c, handle = convo
    reader = asyncio.create_task(c.run_reader())
    await _bootstrap(c, handle)

    async def handler(req):
        assert req.tool_name == "file change"
        return PermissionDecision(behavior="allow")

    c.on_permission_request(handler)
    handle.stdout.feed({"id": 8, "method": "item/fileChange/requestApproval",
                        "params": {"threadId": "t1", "turnId": "turn-1",
                                   "itemId": "i-2", "reason": None,
                                   "startedAtMs": 0}})
    resp = await asyncio.wait_for(handle.stdin.lines.get(), 2)
    assert resp == {"id": 8, "result": {"decision": "accept"}}
    handle.stdout.eof()
    await reader


@pytest.mark.asyncio
async def test_permission_queued_until_handler_registered(convo):
    # The request arrives BEFORE on_permission_request — it must be queued,
    # not dropped/denied (closes the publish/registration race).
    c, handle = convo
    reader = asyncio.create_task(c.run_reader())
    await _bootstrap(c, handle)
    handle.stdout.feed(_cmd_approval(55))
    await asyncio.sleep(0.05)                  # let the reader route it
    assert handle.stdin.lines.empty()          # nothing answered yet

    async def handler(req):
        return PermissionDecision(behavior="allow")

    c.on_permission_request(handler)
    resp = await asyncio.wait_for(handle.stdin.lines.get(), 2)
    assert resp == {"id": 55, "result": {"decision": "accept"}}
    handle.stdout.eof()
    await reader


@pytest.mark.asyncio
async def test_gate_off_denies_permission_defensively():
    handle = _FakeHandle()
    c = CodexConversation(cwd="/w")  # permission_gate=False (default)
    c.attach(handle)
    reader = asyncio.create_task(c.run_reader())
    await _bootstrap(c, handle)
    handle.stdout.feed(_cmd_approval(5))
    resp = await asyncio.wait_for(handle.stdin.lines.get(), 2)
    assert resp == {"id": 5, "result": {"decision": "decline"}}
    handle.stdout.eof()
    await reader


@pytest.mark.asyncio
async def test_unknown_server_request_gets_method_not_found(convo):
    c, handle = convo
    reader = asyncio.create_task(c.run_reader())
    await _bootstrap(c, handle)
    handle.stdout.feed({"id": 12, "method": "item/tool/requestUserInput",
                        "params": {"threadId": "t1"}})
    resp = await asyncio.wait_for(handle.stdin.lines.get(), 2)
    assert resp["id"] == 12
    assert resp["error"]["code"] == -32601
    handle.stdout.eof()
    await reader


@pytest.mark.asyncio
async def test_interrupt_sends_turn_interrupt(convo):
    c, handle = convo
    reader = asyncio.create_task(c.run_reader())
    await _bootstrap(c, handle)
    await c.send("count to 100")
    turn_req = await asyncio.wait_for(handle.stdin.lines.get(), 1)
    handle.stdout.feed({"id": turn_req["id"], "result": {
        "turn": {"id": "turn-1", "status": "inProgress", "items": []}}})
    await _wait_for(lambda: c.current_turn_id == "turn-1")
    assert c.is_pending()

    intr_task = asyncio.create_task(c.interrupt())
    intr = await asyncio.wait_for(handle.stdin.lines.get(), 1)
    assert intr["method"] == "turn/interrupt"
    assert intr["params"] == {"threadId": "t1", "turnId": "turn-1"}
    handle.stdout.feed({"id": intr["id"], "result": {}})  # ACK, not completion
    await asyncio.wait_for(intr_task, 1)
    assert c.is_pending()                                  # still in flight
    handle.stdout.feed(_turn_completed(status="interrupted"))
    await _wait_for(lambda: not c.is_pending())
    handle.stdout.eof()
    await reader


@pytest.mark.asyncio
async def test_interrupt_idle_is_noop(convo):
    c, handle = convo
    reader = asyncio.create_task(c.run_reader())
    await _bootstrap(c, handle)
    await c.interrupt()                       # no pending turn -> no write
    assert handle.stdin.lines.empty()
    handle.stdout.eof()
    await reader


@pytest.mark.asyncio
async def test_unparseable_line_becomes_synthetic_event(convo):
    c, handle = convo
    events = []
    c.on_event(events.append)
    reader = asyncio.create_task(c.run_reader())
    await _bootstrap(c, handle)
    handle.stdout.queue.put_nowait(b"this is not json\n")
    handle.stdout.eof()
    await reader
    assert any(e.get("type") == "x-optio-unparseable" for e in events)


@pytest.mark.asyncio
async def test_eof_closes_and_emits_synthetic_closed(convo):
    c, handle = convo
    events = []
    c.on_event(events.append)
    reader = asyncio.create_task(c.run_reader())
    await _bootstrap(c, handle)
    handle.stdout.eof()
    await reader
    assert c.closed
    assert events[-1].get("type") == "x-optio-closed"  # drain guarantee
    with pytest.raises(ConversationClosed):
        await c.send("too late")
    with pytest.raises(ConversationClosed):
        await c.interrupt()


@pytest.mark.asyncio
async def test_close_sets_close_requested(convo):
    c, handle = convo
    await c.close()
    assert c.close_requested.is_set()


@pytest.mark.asyncio
async def test_bootstrap_captures_account_models_and_thread_id(convo):
    c, handle = convo
    reader = asyncio.create_task(c.run_reader())
    await _bootstrap(c, handle)
    assert c.thread_id == "t1"
    assert c.account == {"type": "apikey"}
    assert c.model_list["data"][1]["id"] == "gpt-5.4-mini"
    assert c.current_model_id == "gpt-5.5"    # thread/start result.model
    handle.stdout.eof()
    await reader


@pytest.mark.asyncio
async def test_bootstrap_resume_uses_thread_resume():
    handle = _FakeHandle()
    c = CodexConversation(cwd="/w", resume_thread_id="t9")
    c.attach(handle)
    reader = asyncio.create_task(c.run_reader())
    await _bootstrap(c, handle, thread_id="t9", resume=True)
    assert c.thread_id == "t9"
    handle.stdout.eof()
    await reader


@pytest.mark.asyncio
async def test_resume_replay_history_emits_stored_items_in_order():
    # thread/resume returns the prior conversation inline as thread.turns[].
    # items[]; bootstrap stashes it and replay_history re-emits each stored item
    # as the item/completed the live stream would have sent, plus a
    # turn/completed per turn, through on_event — so a late viewer reconstructs
    # the prior history exactly like live turns (the reducer renders a replayed
    # item/completed identically to a streamed one). It is buffer backfill, NOT a
    # turn: on_message never fires (no phantom answer synthesised).
    handle = _FakeHandle()
    c = CodexConversation(cwd="/w", resume_thread_id="t9")
    c.attach(handle)
    reader = asyncio.create_task(c.run_reader())
    await _bootstrap_resume(c, handle, thread_id="t9", turns=_RESUME_TURNS)

    events, texts = [], []
    c.on_event(events.append)
    c.on_message(texts.append)

    n = await c.replay_history()
    assert n == 7  # 5 items + one turn/completed per turn

    # Filter to the backfill events (a stray handshake response may ride the
    # event queue; backfill is the only source of item/completed + turn/completed
    # here). Order is load-bearing — the reducer coalesces each turn's answer.
    backfill = [e for e in events if e.get("method") in ("item/completed", "turn/completed")]
    assert [e["method"] for e in backfill] == [
        "item/completed", "item/completed", "turn/completed",
        "item/completed", "item/completed", "item/completed", "turn/completed",
    ]
    # Each stored item is wrapped faithfully into item/completed's `item`.
    items = [e["params"]["item"] for e in backfill if e["method"] == "item/completed"]
    assert [it["type"] for it in items] == [
        "userMessage", "agentMessage",
        "userMessage", "commandExecution", "agentMessage",
    ]
    assert items[1]["text"] == "prior answer one"
    assert items[3]["command"] == "ls"
    # turn/completed carries the historic turn id (so the reducer closes that
    # turn's bubble and opens the next turn's).
    tc = [e for e in backfill if e["method"] == "turn/completed"]
    assert tc[0]["params"]["turn"] == {"id": "turn-h1", "status": "completed"}
    assert tc[0]["params"]["threadId"] == "t9"
    # Backfill drives ONLY on_event — never a coalesced on_message.
    assert texts == []

    handle.stdout.eof()
    await reader


@pytest.mark.asyncio
async def test_replay_history_no_prior_turns_is_noop(convo):
    # A fresh thread/start (or a resume with no prior turns) carries no history:
    # replay_history emits nothing and returns 0, never raises.
    c, handle = convo
    reader = asyncio.create_task(c.run_reader())
    await _bootstrap(c, handle)  # thread/start — no turns
    events = []
    c.on_event(events.append)
    assert await c.replay_history() == 0
    assert [e for e in events if e.get("method") in ("item/completed", "turn/completed")] == []
    handle.stdout.eof()
    await reader


@pytest.mark.asyncio
async def test_set_control_model_applies_on_next_turn(convo):
    # INLINE switch, no wire write: the next turn/start pins the model and it
    # sticks for subsequent turns (app-server contract).
    c, handle = convo
    reader = asyncio.create_task(c.run_reader())
    await _bootstrap(c, handle)
    await c.set_control("model", "gpt-5.4-mini")
    assert c.current_model_id == "gpt-5.4-mini"   # optimistic
    assert handle.stdin.lines.empty()              # nothing on the wire yet
    await c.send("hello")
    turn_req = await asyncio.wait_for(handle.stdin.lines.get(), 1)
    assert turn_req["params"]["model"] == "gpt-5.4-mini"
    handle.stdout.eof()
    await reader


@pytest.mark.asyncio
async def test_set_control_unknown_id_is_noop(convo):
    # Codex exposes only the model control; other ids are ignored.
    c, handle = convo
    reader = asyncio.create_task(c.run_reader())
    await _bootstrap(c, handle)
    await c.set_control("thinking", "high")
    assert c.current_model_id == "gpt-5.5"         # unchanged
    assert c._requested_model is None              # no inline override armed
    assert handle.stdin.lines.empty()
    handle.stdout.eof()
    await reader


@pytest.mark.asyncio
async def test_set_control_after_close_raises(convo):
    c, handle = convo
    reader = asyncio.create_task(c.run_reader())
    await _bootstrap(c, handle)
    handle.stdout.eof()
    await reader
    with pytest.raises(ConversationClosed):
        await c.set_control("model", "gpt-5.4-mini")


@pytest.mark.asyncio
async def test_turn_start_error_response_unwinds_pending(convo):
    c, handle = convo
    reader = asyncio.create_task(c.run_reader())
    await _bootstrap(c, handle)
    await c.send("x")
    turn_req = await asyncio.wait_for(handle.stdin.lines.get(), 1)
    handle.stdout.feed({"id": turn_req["id"], "error": {
        "code": -32001, "message": "Server overloaded; retry later."}})
    await _wait_for(lambda: not c.is_pending())   # no turn will ever complete
    handle.stdout.eof()
    await reader


# --- tiny polling helpers ---------------------------------------------------

async def _first(bucket: list, timeout: float = 2.0):
    end = asyncio.get_event_loop().time() + timeout
    while asyncio.get_event_loop().time() < end:
        if bucket:
            return bucket[0]
        await asyncio.sleep(0.01)
    raise AssertionError("no item arrived")


async def _wait_for(pred, timeout: float = 2.0):
    end = asyncio.get_event_loop().time() + timeout
    while asyncio.get_event_loop().time() < end:
        if pred():
            return
        await asyncio.sleep(0.01)
    raise AssertionError("condition not met")
