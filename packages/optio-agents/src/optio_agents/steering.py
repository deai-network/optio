"""Conversation steering: send when ready, interrupt and send, interrupt.

The engine-neutral scaffolding every wrapper's conversation listener uses for
its ``POST /send``, ``/steer`` and ``/interrupt`` routes. A wrapper declares
how its agent treats a message sent while a turn runs (``busy_send``, per
agent with per-model overrides); ``Steering`` adds what the agent lacks:
optio's own queue for agents that cannot take a busy send, one deadline
bounding both the interrupt call and the wait for the turn end, at most one
``x-optio-interrupt`` per turn, and the synthetic events the conversation UI
renders (``x-optio-queued``, ``x-optio-taken``, ``x-optio-interrupt``). The
events go through the wrapper's ``emit`` hook into its own event stream, so
the listener buffers (and a resume persists) them like native events.

See docs/2026-09-13-conversation-steering-design.md §2 and §3.
"""

from __future__ import annotations

import asyncio
import logging
import re
import uuid
from dataclasses import dataclass, field
from typing import Callable, Literal, Mapping

from optio_agents.conversation import ConversationClosed

_LOG = logging.getLogger(__name__)

# What the agent does with a message sent while a turn runs:
#   joins-next-step  taken at the next tool result, same turn (Claude Code, codex)
#   queues-to-end    the agent's own queue, runs after the turn (grok)
#   cuts-in          cancels the running turn and starts a new one (cursor)
#   rejected         refused while busy (kimicode)
#   unsafe           no defined behaviour (antigravity; any unmeasured agent)
BusySend = Literal["joins-next-step", "queues-to-end", "cuts-in", "rejected", "unsafe"]
BUSY_SEND_VALUES: tuple[str, ...] = (
    "joins-next-step", "queues-to-end", "cuts-in", "rejected", "unsafe",
)
# The agent holds a busy send itself: optio sends it straight through.
NATIVE_QUEUE: frozenset[str] = frozenset({"joins-next-step", "queues-to-end"})

QUEUED_EVENT = "x-optio-queued"
TAKEN_EVENT = "x-optio-taken"
INTERRUPT_EVENT = "x-optio-interrupt"
# Messages optio delivers together form one prompt, in the order written.
PROMPT_SEPARATOR = "\n\n"
# Upper bound on the wait for the turn end after an interrupt.
TURN_END_TIMEOUT_S = 15.0

_VARIANT_SUFFIX = re.compile(r"\[[^\]]*\]$")


@dataclass(frozen=True)
class BusySendDeclaration:
    """A wrapper's measured ``busy_send``: the agent's value, plus overrides
    for models a recording showed behaving differently."""

    agent: BusySend
    models: Mapping[str, BusySend] = field(default_factory=dict)

    def __post_init__(self) -> None:
        for value in (self.agent, *self.models.values()):
            if value not in BUSY_SEND_VALUES:
                raise ValueError(f"unknown busy_send value: {value!r}")

    def for_model(self, model: str | None) -> BusySend:
        """The model's own value, else the agent's. A runtime ``[variant]``
        suffix (e.g. ``claude-opus-4-8[1m]``) is ignored."""
        if model:
            key = _VARIANT_SUFFIX.sub("", model)
            if key in self.models:
                return self.models[key]
        return self.agent


def resolve_busy_send(declaration: BusySendDeclaration | None, model: str | None) -> BusySend:
    """An agent without a declaration is ``unsafe``: always correct, only slower."""
    if declaration is None:
        return "unsafe"
    return declaration.for_model(model)


@dataclass(frozen=True)
class SendOutcome:
    """Result of send_when_ready: the message's id, and whether it waits
    (the agent or optio holds it) rather than starting a turn now."""

    id: str
    queued: bool


class Steering:
    """send_when_ready / interrupt_and_send / interrupt over one Conversation.

    ``busy_send`` is read on every call (it can follow the running model).
    ``emit`` puts a synthetic event into the wrapper's event stream.
    ``is_turn_end`` recognises the native event that ends a turn.
    """

    def __init__(
        self,
        conversation,
        *,
        busy_send: Callable[[], BusySend],
        emit: Callable[[dict], None],
        is_turn_end: Callable[[dict], bool],
        turn_end_timeout_s: float = TURN_END_TIMEOUT_S,
        new_id: Callable[[], str] | None = None,
    ) -> None:
        self._conv = conversation
        self._busy_send = busy_send
        self._emit = emit
        self._is_turn_end = is_turn_end
        self._timeout_s = turn_end_timeout_s
        self._new_id = new_id or (lambda: uuid.uuid4().hex)
        # optio's own queue (cuts-in / rejected / unsafe): (id, text), in order.
        self._held: list[tuple[str, str]] = []
        self._lock = asyncio.Lock()
        self._turn_end = asyncio.Event()
        self._flush_task: asyncio.Task | None = None
        # Set when x-optio-interrupt is emitted for the turn now running;
        # cleared on that turn's end. Shared by interrupt() and
        # interrupt_and_send() so at most one marker (and one underlying
        # interrupt() call) happens per turn, however many times either is
        # called while it runs.
        self._interrupted = False
        self._unsubscribe = conversation.on_event(self._on_event)

    @property
    def held_ids(self) -> list[str]:
        return [qid for qid, _ in self._held]

    def close(self) -> None:
        unsubscribe, self._unsubscribe = self._unsubscribe, (lambda: None)
        unsubscribe()

    # -- turn end --------------------------------------------------------------

    def _on_event(self, event: dict) -> None:
        if self._is_turn_end(event):
            self._turn_end.set()
            self._interrupted = False
            if self._held and (self._flush_task is None or self._flush_task.done()):
                self._flush_task = asyncio.ensure_future(self._flush_after_turn())
        # A merged turn (Send when ready taken mid-turn) has two sends and
        # one result, so is_pending() can stay True for a few ms after that
        # result's turn-end event above, until a later, non-turn-end event
        # (the wrapper's own idle) actually clears it (final-review M1). An
        # Interrupt landing in that window sets _interrupted again after the
        # line above cleared it; without this second check nothing would
        # ever clear it, and the NEXT turn's first Interrupt would silently
        # no-op (see Steering.interrupt/interrupt_and_send). _route (or the
        # wrapper's equivalent) has already updated is_pending() by the time
        # any event reaches here, so this is safe to run unconditionally: a
        # stale idle racing a fresh send leaves is_pending() True and this
        # is a no-op.
        if self._interrupted and not self._conv.is_pending():
            self._interrupted = False

    async def _flush_after_turn(self) -> None:
        async with self._lock:
            try:
                await self._deliver([])
            except ConversationClosed:
                _LOG.warning("steering: conversation closed before held messages were delivered")

    async def settle(self) -> None:
        """Wait for a turn-end flush in progress (tests, orderly teardown)."""
        task = self._flush_task
        if task is not None:
            await task

    # -- helpers ---------------------------------------------------------------

    def _check_open(self) -> None:
        if getattr(self._conv, "closed", False):
            raise ConversationClosed("conversation closed")

    async def _deliver(self, extra: list[str]) -> None:
        """Send everything optio holds plus ``extra`` as ONE prompt, and report
        the held ids in one x-optio-taken (sent one by one, a cuts-in agent
        would cancel each previous message)."""
        ids = [qid for qid, _ in self._held]
        texts = [text for _, text in self._held] + [t for t in extra if t]
        self._held = []
        if ids:
            self._emit({"type": TAKEN_EVENT, "ids": ids})
        if texts:
            await self._conv.send(PROMPT_SEPARATOR.join(texts))

    async def _interrupt_and_wait(self, *, call_interrupt: bool) -> None:
        """Stop (unless someone already did, this turn) and wait for the turn
        end, both under one ``turn_end_timeout_s`` deadline: a live but
        unresponsive agent's ``interrupt()`` must not hold ``_lock``
        (and every later send/steer behind it) indefinitely."""
        self._turn_end.clear()
        try:
            async with asyncio.timeout(self._timeout_s):
                if call_interrupt:
                    await self._conv.interrupt()
                await self._turn_end.wait()
        except TimeoutError:
            _LOG.warning(
                "steering: no turn end within %.0f s of the interrupt; sending anyway",
                self._timeout_s,
            )

    # -- the three operations ---------------------------------------------------

    async def send_when_ready(self, text: str) -> SendOutcome:
        """Idle: a plain send. Busy: the agent's own queue takes it
        (joins-next-step, queues-to-end), or optio holds it until the turn
        ends. A busy send reports x-optio-queued first."""
        self._check_open()
        qid = self._new_id()
        async with self._lock:
            busy = self._conv.is_pending()
            if not busy and not self._held:
                await self._conv.send(text)
                return SendOutcome(id=qid, queued=False)
            if self._busy_send() in NATIVE_QUEUE and not self._held:
                self._emit({"type": QUEUED_EVENT, "id": qid, "text": text})
                await self._conv.send(text)
                return SendOutcome(id=qid, queued=True)
            self._held.append((qid, text))
            self._emit({"type": QUEUED_EVENT, "id": qid, "text": text})
            if not busy:
                # The turn already ended and a flush is due: deliver now.
                await self._deliver([])
            return SendOutcome(id=qid, queued=True)

    async def interrupt_and_send(self, text: str) -> str | None:
        """Stop the running step, then deliver what optio holds plus ``text``
        as one prompt. ``cuts-in`` agents cancel natively on the send itself;
        every other agent is interrupted and given at most
        ``turn_end_timeout_s`` to end the turn (its own queue goes first).
        Empty ``text`` is Send now: returns None. Send now with nothing held
        on an agent that cannot take a busy send (not in NATIVE_QUEUE) is a
        no-op: there is nothing to interrupt and nothing to send, so it
        neither emits x-optio-interrupt nor stops the turn now running.
        At most one x-optio-interrupt (and one underlying interrupt() call)
        happens per turn: if interrupt() already marked this turn
        interrupted, this still waits for the turn end and delivers, but
        emits no second marker and sends no second interrupt. Non-empty
        ``text`` gets its own x-optio-queued (id, text) — the same id this
        call returns — emitted after the interrupt marker (if any) and
        just before the send, so the reducer can dedupe the steer's local
        echo against the wire by id (final-review M3)."""
        self._check_open()
        if not text and not self._held and self._busy_send() not in NATIVE_QUEUE:
            return None
        qid = self._new_id() if text else None
        async with self._lock:
            if self._conv.is_pending():
                already_interrupted = self._interrupted
                if not already_interrupted:
                    self._interrupted = True
                    self._emit({"type": INTERRUPT_EVENT, "by": "user"})
                if self._busy_send() != "cuts-in":
                    await self._interrupt_and_wait(call_interrupt=not already_interrupted)
            if text:
                self._emit({"type": QUEUED_EVENT, "id": qid, "text": text})
            await self._deliver([text])
        return qid

    async def interrupt(self) -> None:
        """Stop only. Emits x-optio-interrupt at most once per turn, shared
        with interrupt_and_send: a second Interrupt (double click, or one
        pressed while interrupt_and_send still waits for the turn end) emits
        nothing and sends no second control_request. Deliberately lock-free,
        so it never waits behind an interrupt_and_send."""
        self._check_open()
        if self._conv.is_pending():
            if self._interrupted:
                return
            self._interrupted = True
            self._emit({"type": INTERRUPT_EVENT, "by": "user"})
        await self._conv.interrupt()
