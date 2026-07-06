"""AntigravityConversation — engine-side driver for one synthetic, transcript-
driven Antigravity conversation.

Antigravity's ``agy`` has **no live transport** — no ACP, no stream-json, no
HTTP (design §1). A conversation is therefore *synthesised* from ``agy``'s
one-shot ``-p``/``--print`` mode plus the structured transcript file it writes.
The paths below are the REAL layout captured from the ``agy`` binary
(2026-07-06); the isolated per-task ``HOME`` is ``<workdir>/home``:

* ``send(text)`` spawns ``agy -p [--conversation <id>] [--model <m>]
  --dangerously-skip-permissions <text>`` **under a PTY** (mandatory —
  ``--print`` swallows stdout under a non-TTY, design §1) via
  ``host.launch_subprocess``. Turn 1 passes **no** ``--conversation`` — a fresh
  workdir has no prior conversation, so ``agy`` mints one. After the turn-1
  process exits, the conversation uuid is **discovered** from
  ``<HOME>/.gemini/antigravity-cli/cache/last_conversations.json`` — a JSON
  object ``{"<workdir-abs-path>": "<conv-uuid>"}`` keyed by the workdir; every
  later turn resumes it via ``--conversation <uuid>`` (verified: this appends
  to the SAME transcript and continues context).
* Events are read from the per-conversation transcript at
  ``<HOME>/.gemini/antigravity-cli/brain/<uuid>/.system_generated/logs/transcript.jsonl``
  — NOT stdout (the #76 stdout-swallow bug). Each new transcript line since the
  turn began is parsed into a raw dict and fanned out to ``on_event``
  subscribers **unmodified** (the TS reducer parses this exact shape); synthetic
  optio events use the ``x-optio-`` type prefix. At turn end the answer text is
  coalesced into a single ``on_message`` — the ``content`` of the LAST
  ``PLANNER_RESPONSE`` with non-empty content in that turn's newly-appended
  lines.

Real transcript line schema (one JSON object per line): common keys ``type``,
``source`` (USER_EXPLICIT|MODEL|SYSTEM), ``status``, ``step_index``,
``created_at``. ``USER_INPUT`` (source USER_EXPLICIT) carries ``content`` =
``"<USER_REQUEST>\n…\n</USER_REQUEST>\n<ADDITIONAL_METADATA>…"``.
``PLANNER_RESPONSE`` (source MODEL) is the assistant: ``content`` = answer text
(absent/null when the step is only a tool call), ``thinking`` = reasoning
(optional), ``tool_calls`` = ``[{name, args}]`` (optional). Tool-result types
(e.g. ``LIST_DIRECTORY``), ``CHECKPOINT``, ``CONVERSATION_HISTORY``,
``GENERIC``, ``SYSTEM_MESSAGE`` are system/marker lines. ``unwrap_user_request``
extracts the bare request text from a ``USER_INPUT`` ``content``.

Parity gaps are inherent to the one-shot transport and named, not hidden
(design §7): **no live token streaming** (an answer arrives per completed
turn, so transcript events are surfaced once the ``-p`` process exits, not
delta-by-delta); **turn-level permissions only** (turns run
``--dangerously-skip-permissions``, so ``on_permission_request`` is a no-op
seam); **coarse interrupt** (``interrupt()`` kills the in-flight ``-p``
process — there is no cooperative mid-turn cancel).

Implements ``optio_agents.conversation.Conversation``. Structurally the analog
of optio-grok's ``GrokConversation``, but frames repeated ``agy -p`` turns +
transcript tailing instead of a live ACP stream.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import shlex
from dataclasses import dataclass, field

from optio_host.host import proc_wait

from optio_agents.conversation import ConversationClosed

_LOG = logging.getLogger(__name__)

# How long interrupt() waits for a just-started turn's process handle to
# materialise before treating the conversation as idle (a no-op interrupt).
# wait_for returns as soon as the handle appears, so an in-flight turn is
# caught immediately; only a genuinely idle interrupt pays this bound.
_INTERRUPT_HANDLE_WAIT = 3.0

_USER_REQUEST_RE = re.compile(r"<USER_REQUEST>\s*(.*?)\s*</USER_REQUEST>", re.DOTALL)


def unwrap_user_request(content: str | None) -> str | None:
    """Extract the bare request from a ``USER_INPUT`` line's ``content``.

    Real ``USER_INPUT`` content wraps the request as
    ``"<USER_REQUEST>\n<the request>\n</USER_REQUEST>\n<ADDITIONAL_METADATA>…"``;
    this returns just ``<the request>`` (stripped), or ``None`` when the
    envelope is absent.
    """
    if not content:
        return None
    m = _USER_REQUEST_RE.search(content)
    return m.group(1).strip() if m else None


@dataclass(frozen=True)
class TurnMessage:
    """One completed turn's coalesced answer (the ``on_message`` payload).

    Antigravity emits one message per ``agy -p`` turn (no streaming), so the
    simplified tier carries the full answer ``text`` plus the raw transcript
    ``events`` that produced it.
    """
    text: str
    events: tuple[dict, ...] = field(default_factory=tuple)


class AntigravityConversation:
    """Implements optio_agents.conversation.Conversation for Antigravity.

    Synthetic + transcript-driven: each ``send`` runs one ``agy -p`` turn under
    a PTY on ``host`` and reads the events ``agy`` appended to the
    per-conversation transcript under the isolated ``home``.
    """

    def __init__(
        self,
        *,
        host,
        agy_path: str,
        cwd: str,
        home: str,
        env: dict[str, str] | None = None,
        model: str | None = None,
        skip_permissions: bool = True,
        pty: bool = True,
        claustrum_wrap: list[str] | None = None,
    ) -> None:
        self._host = host
        self._agy_path = agy_path
        self._cwd = cwd
        # The isolated per-task HOME (``<workdir>/home``). The real ``agy``
        # writes its conversation state under ``<home>/.gemini/antigravity-cli``
        # (the transcript is per-conversation, discovered from last_conversations
        # after turn 1 — hence a home root, not a fixed transcript path).
        self._home = home
        self._env = dict(env or {})
        self._model = model
        # Stage 8 fs-isolation: claustrum argv prefix prepended to each turn so
        # ``agy -p`` runs Landlock-confined (None → unconfined). Set once at
        # construction from host_actions._build_claustrum_wrap.
        self._claustrum_wrap = list(claustrum_wrap) if claustrum_wrap else None
        # -p turns are non-interactive, so permissions must be skipped
        # (design §7). Kept configurable for a future turn-level gate.
        self._skip_permissions = skip_permissions
        # Wrap each turn in a PTY (``script -qec``); mandatory for the real
        # ``agy`` (§1 non-TTY stdout-swallow bug).
        self._pty = pty

        # Discovered after turn 1 from last_conversations.json (keyed by the
        # workdir); every later turn resumes it via --conversation.
        self._conversation_id: str | None = None
        self._pending = 0                     # turns whose process is live
        self._closed = False
        self._current_handle = None
        self._handle_ready = asyncio.Event()  # set while a turn's handle is live
        self._interrupted = False
        # Raw argv of the most recent turn (for assertions / debugging).
        self._last_argv: list[str] = []

        self._event_handlers: list = []
        self._message_handlers: list = []
        # Permission handler is a no-op seam: stored but never invoked (turns
        # run skip-permissions). Present for Conversation-protocol conformance.
        self._permission_handler = None

        # Cooperative-shutdown request towards the owning task body (mirrors
        # grok's close_requested seam).
        self.close_requested = asyncio.Event()

    # -- Conversation protocol surface --------------------------------------

    async def send(self, text: str) -> None:
        if self._closed:
            raise ConversationClosed("conversation closed")

        pre_offset = self._transcript_size()
        argv = self._build_argv(text)
        self._last_argv = list(argv)
        command = self._wrap_command(argv)

        self._interrupted = False
        handle = await self._host.launch_subprocess(
            command, env=self._env, cwd=self._cwd, merge_stderr=True,
        )
        self._current_handle = handle
        self._pending += 1
        self._handle_ready.set()
        try:
            # Draining stdout to EOF is the turn-end signal (the process closed
            # its stdout). We do not parse stdout for the answer — the #76
            # stdout-swallow bug means the transcript file is the source of
            # truth — but we must drain it so a PTY pipe never back-pressures.
            async for _chunk in handle.stdout:
                pass
            rc = await proc_wait(handle)
        finally:
            self._current_handle = None
            self._handle_ready.clear()
            self._pending = max(0, self._pending - 1)

        if self._interrupted:
            self._interrupted = False
            raise RuntimeError("agy -p turn interrupted")
        if rc != 0:
            raise RuntimeError(f"agy -p turn exited with code {rc}")

        # Turn 1 minted a fresh conversation; discover its uuid from
        # last_conversations.json now that the process has flushed it, so the
        # transcript path resolves and later turns can resume via --conversation.
        if not self._conversation_id:
            self._conversation_id = self._discover_conversation_id()

        await self._consume_turn(pre_offset)

    def on_event(self, handler):
        self._event_handlers.append(handler)
        return lambda: self._event_handlers.remove(handler)

    def on_message(self, handler):
        self._message_handlers.append(handler)
        return lambda: self._message_handlers.remove(handler)

    def on_permission_request(self, handler):
        # No-op seam: -p turns run --dangerously-skip-permissions, so no
        # permission request ever surfaces (design §7). Stored for symmetry
        # with the shared protocol; never invoked.
        self._permission_handler = handler

        def _unsub() -> None:
            if self._permission_handler is handler:
                self._permission_handler = None
        return _unsub

    def is_pending(self) -> bool:
        return self._pending > 0

    async def interrupt(self) -> None:
        if self._closed:
            raise ConversationClosed("conversation closed")
        # Wait briefly for an in-flight (possibly just-scheduled) turn's handle
        # to appear; returns instantly once it does. A genuinely idle interrupt
        # falls through the timeout and is a no-op.
        try:
            await asyncio.wait_for(
                self._handle_ready.wait(), timeout=_INTERRUPT_HANDLE_WAIT,
            )
        except asyncio.TimeoutError:
            return
        handle = self._current_handle
        if handle is None:
            return
        # Coarse cancel: kill the in-flight -p process group (design §7). The
        # flag makes the awaiting send() raise.
        self._interrupted = True
        await self._host.terminate_subprocess(handle, aggressive=True)

    async def set_control(self, control_id: str, value) -> None:
        """Push a session-control change. Antigravity exposes only ``model``;
        because every turn is a fresh ``agy -p`` invocation, a model switch is
        simply the next turn's ``--model`` (restart-with-new-model — the
        claudecode precedent). Unknown control ids are ignored."""
        if control_id != "model":
            return
        if self._closed:
            raise ConversationClosed("conversation closed")
        self._model = value

    async def close(self, aggressive: bool = True) -> None:
        """Close the conversation, reaping any in-flight ``-p`` turn.

        ``aggressive`` gates how the live turn is torn down. A SEEDED session
        passes ``aggressive=False`` (SIGTERM-and-wait) so agy can flush a
        rotated OAuth token store before the teardown save-back reads it; a
        non-seeded session keeps the default fast kill. See
        ``session._teardown_aggressive``."""
        self._closed = True
        self.close_requested.set()
        # Kill any in-flight turn so a parked -p process never leaks.
        handle = self._current_handle
        if handle is not None:
            self._interrupted = True
            try:
                await self._host.terminate_subprocess(handle, aggressive=aggressive)
            except Exception:  # noqa: BLE001 — teardown must not raise
                _LOG.exception("antigravity conversation: terminate on close failed")

    @property
    def closed(self) -> bool:
        return self._closed

    @property
    def conversation_id(self) -> str | None:
        return self._conversation_id

    def last_argv_contains(self, substring: str) -> bool:
        """True if the most recent turn's argv (space-joined) contains
        ``substring`` (test/debug helper)."""
        return substring in " ".join(self._last_argv)

    # -- internals -----------------------------------------------------------

    def _build_argv(self, text: str) -> list[str]:
        # Stage 8: when fs-isolation is on, the claustrum wrap goes AHEAD of agy
        # so claustrum (under the PTY) applies Landlock then execve's ``agy -p``;
        # agy + its tool subprocesses inherit the confinement.
        argv = [*(self._claustrum_wrap or []), self._agy_path, "-p"]
        if self._conversation_id:
            argv += ["--conversation", self._conversation_id]
        # Turn 1 passes NO --conversation: a fresh workdir has no prior
        # conversation, so ``agy`` mints one (verified against the real binary).
        if self._model:
            argv += ["--model", self._model]
        if self._skip_permissions:
            argv += ["--dangerously-skip-permissions"]
        argv.append(text)
        return argv

    def _wrap_command(self, argv: list[str]) -> str:
        inner = " ".join(shlex.quote(a) for a in argv)
        if not self._pty:
            return inner
        # ``script -qec CMD /dev/null`` runs CMD in a PTY (util-linux), quiet,
        # propagating the child's exit code and discarding the typescript.
        return f"script -qec {shlex.quote(inner)} /dev/null"

    def _agy_cli_dir(self) -> str:
        """The real ``agy`` state root: ``<home>/.gemini/antigravity-cli``."""
        return os.path.join(self._home, ".gemini", "antigravity-cli")

    def _cache_path(self) -> str:
        """``<agy-cli>/cache/last_conversations.json`` — ``{workdir: uuid}``."""
        return os.path.join(self._agy_cli_dir(), "cache", "last_conversations.json")

    def _transcript_path(self) -> str | None:
        """Per-conversation transcript path, or ``None`` before the uuid is
        known (turn 1, pre-discovery)."""
        if not self._conversation_id:
            return None
        return os.path.join(
            self._agy_cli_dir(), "brain", self._conversation_id,
            ".system_generated", "logs", "transcript.jsonl",
        )

    def _discover_conversation_id(self) -> str | None:
        """Read the workdir→uuid map ``agy`` wrote to last_conversations.json
        and return this workdir's conversation uuid (``None`` if unresolved)."""
        try:
            with open(self._cache_path(), "r", encoding="utf-8") as fh:
                data = json.load(fh)
        except (OSError, ValueError):
            return None
        if not isinstance(data, dict):
            return None
        # The key is the workdir's absolute path; try it raw then realpath'd
        # (agy records the resolved cwd), finally fall back to the sole entry.
        for key in (self._cwd, os.path.realpath(self._cwd)):
            uuid = data.get(key)
            if uuid:
                return uuid
        if len(data) == 1:
            return next(iter(data.values()))
        return None

    def _transcript_size(self) -> int:
        path = self._transcript_path()
        if not path:
            return 0
        try:
            return os.path.getsize(path)
        except OSError:
            return 0

    def _read_new_events(self, offset: int) -> list[dict]:
        """Parse every transcript line appended since ``offset`` (bytes)."""
        path = self._transcript_path()
        if not path:
            return []
        try:
            with open(path, "rb") as fh:
                fh.seek(offset)
                data = fh.read()
        except OSError:
            return []
        events: list[dict] = []
        for line in data.decode("utf-8", errors="replace").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                events.append(json.loads(line))
            except ValueError:
                _LOG.warning(
                    "antigravity conversation: unparseable transcript line: %.200s",
                    line,
                )
                events.append({"type": "x-optio-unparseable", "line": line})
        return events

    async def _consume_turn(self, pre_offset: int) -> None:
        """Fan the turn's new transcript events out to on_event (raw dicts,
        unmodified), then emit the one coalesced on_message answer.

        The answer is the ``content`` of the LAST ``PLANNER_RESPONSE`` with
        non-empty content in this turn's newly-appended lines (a PLANNER_RESPONSE
        that is only a tool call has null/absent content and is skipped)."""
        events = self._read_new_events(pre_offset)
        final_answer = ""
        for ev in events:
            if ev.get("type") == "PLANNER_RESPONSE":
                content = ev.get("content")
                if content:
                    final_answer = content
            await self._emit(self._event_handlers, ev, "on_event")
        message = TurnMessage(text=final_answer, events=tuple(events))
        await self._emit(self._message_handlers, message, "on_message")

    async def _emit(self, handlers, arg, label: str) -> None:
        for handler in list(handlers):
            try:
                result = handler(arg)
                if asyncio.iscoroutine(result):
                    await result
            except Exception:  # noqa: BLE001 — subscriber bugs never kill the driver
                _LOG.exception("antigravity conversation: %s handler raised", label)
