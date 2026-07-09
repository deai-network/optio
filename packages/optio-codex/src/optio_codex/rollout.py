"""Reconstruct a codex conversation's FULL history from its on-disk rollout.

Why this exists
---------------
A viewer reconnecting to a live codex session over the SSE ``/events`` endpoint
replays the listener's in-memory buffer — a ``deque(maxlen=BUFFER_MAXLEN)`` of
every wire event, including each token delta. A long session streams far more
than ``BUFFER_MAXLEN`` deltas, so the deque evicts its earliest entries and the
viewer can no longer scroll to the start of the conversation.

The authoritative full history is the codex *rollout* JSONL on disk:
``<CODEX_HOME>/sessions/YYYY/MM/DD/rollout-<ts>-<uuid>.jsonl`` (CODEX_HOME =
``<workdir>/home/.codex``). This module reduces that file to the SAME
``item/completed`` + ``turn/completed`` notifications the live listener and the
UI reducer already consume — mirroring
:meth:`optio_codex.conversation.CodexConversation.replay_history` (the resume
path), which re-emits ``thread/resume``'s inline ``thread.turns[].items[]`` the
same way. Both paths build their event envelopes through
:func:`item_completed_event` / :func:`turn_completed_event` here so the two
reconstructions cannot drift.

This module is PURE (path in, event list out) — unit-tested against a fixture
rollout, and fail-soft: a missing/unreadable/malformed rollout yields ``[]``
rather than raising into the SSE handler.

Rollout wire facts (verified against a real codex-cli 0.142.5 rollout)
---------------------------------------------------------------------
Each line is one JSON object. Relevant shapes:

* ``{"type":"session_meta","payload":{"session_id":..., ...}}`` — carries the
  thread id used as ``threadId`` on the emitted notifications.
* ``{"type":"event_msg","payload":{"type":"task_started"|"task_complete",
  "turn_id":..., ...}}`` — turn brackets (used for structure only, never
  emitted). ``token_count`` / ``user_message`` / ``agent_message`` are NOISE
  (skipped — the ``response_item`` entries carry the same content in the shape
  the reducer wants, so emitting the ``event_msg`` copies would double them).
* ``{"type":"turn_context","payload":{"turn_id":..., ...}}`` — turn metadata
  (structure only).
* ``{"type":"response_item","payload":{"type":"message"|"reasoning"|
  "function_call"|"function_call_output"|"custom_tool_call", ...,
  "internal_chat_message_metadata_passthrough":{"turn_id":...}}}`` — the
  renderable items. Every ``response_item`` carries its real codex ``turn_id``
  in the passthrough (the SAME id the live wire uses — hence a reliable dedup
  key for the listener). ``message`` role ``assistant`` -> agentMessage,
  role ``user`` (real prompt) -> userMessage; role ``developer`` and the
  ``<environment_context>`` / ``<permissions instructions>`` injected role=user
  context messages are codex-internal and are filtered. ``function_call`` maps
  by name to the tool item the UI ``toolRow()`` renders; ``function_call_output``
  is skipped (the command row already rendered from ``function_call``).
"""

from __future__ import annotations

import json
import logging
import os

_LOG = logging.getLogger(__name__)

# Rollout filenames embed an ISO-8601 timestamp, so a lexicographic name sort is
# a chronological sort (and unlike mtime it survives a workdir tar restore).
_ROLLOUT_PREFIX = "rollout-"
_ROLLOUT_SUFFIX = ".jsonl"

# Injected role=user context messages codex prepends to a turn — never a real
# user prompt. Matched as a prefix of the message text.
_INJECTED_USER_PREFIXES = (
    "<environment_context>",
    "<user_instructions>",
    "<permissions instructions>",
)

# function_call names -> app-server tool item type. Names outside this map
# (e.g. update_plan) have no UI tool row and are skipped, matching the live
# reducer, which no-ops item types its toolRow() does not know.
_SHELL_CALL_NAMES = ("exec_command", "shell", "local_shell", "bash", "container.exec")
_PATCH_CALL_NAMES = ("apply_patch",)
_SEARCH_CALL_NAMES = ("web_search", "web.search")


# -- shared event envelopes (DRY seam with conversation.replay_history) -------


def item_completed_event(thread_id: str | None, turn_id: str | None, item: dict) -> dict:
    """The ``item/completed`` notification the live stream sends per item.

    Shared by BOTH history reconstructions (the resume path
    ``CodexConversation.replay_history`` and this rollout path) so the wire
    envelope cannot drift between them."""
    return {
        "method": "item/completed",
        "params": {"threadId": thread_id, "turnId": turn_id, "item": item},
    }


def turn_completed_event(
    thread_id: str | None, turn_id: str | None, status: str = "completed",
) -> dict:
    """The ``turn/completed`` notification that closes a turn's answer bubble
    (and opens the next turn's). Shared with ``replay_history`` — see
    :func:`item_completed_event`."""
    return {
        "method": "turn/completed",
        "params": {"threadId": thread_id, "turn": {"id": turn_id, "status": status}},
    }


# -- rollout item mapping -----------------------------------------------------


def _message_text(payload: dict) -> str:
    content = payload.get("content")
    if not isinstance(content, list):
        return ""
    return "".join(
        c.get("text", "") for c in content
        if isinstance(c, dict) and isinstance(c.get("text"), str)
    )


def _is_injected_user(text: str) -> bool:
    stripped = text.lstrip()
    return any(stripped.startswith(p) for p in _INJECTED_USER_PREFIXES)


def _tool_item_from_function_call(payload: dict) -> dict | None:
    """Map a ``function_call`` response_item to the app-server tool item shape
    the UI ``toolRow()`` renders (commandExecution / fileChange / webSearch),
    or None for a call with no dedicated UI row (e.g. update_plan)."""
    name = payload.get("name") or ""
    item_id = payload.get("id") or payload.get("call_id")
    args = payload.get("arguments")
    if isinstance(args, str):
        try:
            args = json.loads(args)
        except ValueError:
            args = {}
    if not isinstance(args, dict):
        args = {}
    if name in _SHELL_CALL_NAMES:
        command = args.get("cmd") or args.get("command") or name
        if isinstance(command, list):
            command = " ".join(str(c) for c in command)
        return {
            "type": "commandExecution",
            "id": item_id,
            "command": str(command),
            "cwd": args.get("workdir") or args.get("cwd"),
            "status": "completed",
        }
    if name in _PATCH_CALL_NAMES:
        return {
            "type": "fileChange",
            "id": item_id,
            "changes": args.get("changes") or args.get("input") or args,
            "status": "completed",
        }
    if name in _SEARCH_CALL_NAMES:
        return {
            "type": "webSearch",
            "id": item_id,
            "query": args.get("query") or args.get("q") or "",
        }
    return None


def _tool_item_from_custom_tool_call(payload: dict) -> dict | None:
    """Map a ``custom_tool_call`` (MCP tool) response_item to an mcpToolCall
    item. Best-effort — the field names vary by codex version."""
    name = payload.get("name") or payload.get("tool") or "tool"
    server, _, tool = str(name).partition(".")
    if not tool:
        server, tool = payload.get("server") or "mcp", name
    args = payload.get("arguments") or payload.get("input") or {}
    if isinstance(args, str):
        try:
            args = json.loads(args)
        except ValueError:
            args = {"raw": args}
    return {
        "type": "mcpToolCall",
        "id": payload.get("id") or payload.get("call_id"),
        "server": server,
        "tool": tool,
        "arguments": args,
    }


def _map_response_item(payload: dict) -> dict | None:
    """Reduce one ``response_item`` payload to an app-server item, or None when
    it is codex-internal noise (injected context, function_call_output, an
    unrenderable function_call, ...)."""
    ptype = payload.get("type")
    if ptype == "message":
        role = payload.get("role")
        text = _message_text(payload)
        if role == "assistant":
            return {"type": "agentMessage", "id": payload.get("id"), "text": text}
        if role == "user":
            if _is_injected_user(text):
                return None  # environment_context / user_instructions injection
            return {"type": "userMessage", "id": payload.get("id"), "text": text}
        return None  # developer (permissions instructions) and any other role
    if ptype == "reasoning":
        # Emitted for parity with the resume replay path; the UI reducer renders
        # a replayed reasoning item/completed as a no-op (reasoning surfaces live
        # only through item/reasoning/*Delta).
        return {
            "type": "reasoning",
            "id": payload.get("id"),
            "summary": payload.get("summary") or [],
        }
    if ptype == "function_call":
        return _tool_item_from_function_call(payload)
    if ptype == "custom_tool_call":
        return _tool_item_from_custom_tool_call(payload)
    # function_call_output, and anything else -> not rendered.
    return None


def _turn_id_of(payload: dict) -> str | None:
    passthrough = payload.get("internal_chat_message_metadata_passthrough")
    if isinstance(passthrough, dict) and passthrough.get("turn_id"):
        return passthrough["turn_id"]
    return payload.get("turn_id")


# -- public API ---------------------------------------------------------------


def read_rollout_events(path: str) -> list[dict]:
    """Parse a codex rollout JSONL and reduce it to the ordered app-server
    ``item/completed`` + ``turn/completed`` notifications the live listener / UI
    reducer consume (mirroring ``CodexConversation.replay_history``).

    Renderable ``response_item`` entries are grouped into turns by their codex
    ``turn_id`` (contiguous in the file); a ``turn/completed`` is emitted after
    each turn's items — exactly like a live multi-turn stream, so the reducer
    closes each turn's bubble and opens the next. PURE and fail-soft: a missing,
    unreadable, or malformed rollout yields ``[]`` (unparseable lines skipped).
    """
    try:
        with open(path, "r", encoding="utf-8") as fh:
            lines = fh.readlines()
    except OSError:
        return []

    thread_id: str | None = None
    events: list[dict] = []
    cur_turn: str | None = None
    cur_items: list[dict] = []

    def _flush() -> None:
        nonlocal cur_items
        for item in cur_items:
            events.append(item_completed_event(thread_id, cur_turn, item))
        if cur_items:
            events.append(turn_completed_event(thread_id, cur_turn))
        cur_items = []

    for line in lines:
        line = line.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
        except ValueError:
            _LOG.debug("rollout: skipping unparseable line: %.120s", line)
            continue
        if not isinstance(obj, dict):
            continue
        otype = obj.get("type")
        payload = obj.get("payload")
        if not isinstance(payload, dict):
            continue
        if otype == "session_meta":
            thread_id = payload.get("session_id") or payload.get("id") or thread_id
            continue
        if otype != "response_item":
            continue  # event_msg / turn_context / ... — structure/noise only
        item = _map_response_item(payload)
        if item is None:
            continue
        turn_id = _turn_id_of(payload)
        if cur_items and turn_id != cur_turn:
            _flush()  # turn boundary — close the previous turn's bubble
        cur_turn = turn_id
        cur_items.append(item)

    _flush()
    return events


def resolve_latest_rollout(codex_home: str) -> str | None:
    """Path of the newest ``rollout-*.jsonl`` under ``<codex_home>/sessions``,
    or None when the sessions tree is absent/empty.

    Newest by FILENAME (lexicographic): rollout names embed an ISO-ordered
    timestamp, so a name sort is a chronological sort — and unlike mtime it
    survives a workdir tar restore (mirrors host_actions.read_latest_session_id).
    """
    sessions = os.path.join(codex_home, "sessions")
    best: str | None = None
    best_key: str | None = None
    try:
        walker = os.walk(sessions)
    except OSError:
        return None
    for dirpath, _dirs, files in walker:
        for name in files:
            if name.startswith(_ROLLOUT_PREFIX) and name.endswith(_ROLLOUT_SUFFIX):
                if best_key is None or name > best_key:
                    best_key = name
                    best = os.path.join(dirpath, name)
    return best
