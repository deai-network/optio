"""Conversation steering: send when ready, interrupt and send, interrupt.

The engine-neutral scaffolding every wrapper's conversation listener uses for
its ``POST /send``, ``/steer`` and ``/interrupt`` routes. A wrapper declares
how its agent treats a message sent while a turn runs (``busy_send``, per
agent with per-model overrides); ``Steering`` adds what the agent lacks:
optio's own queue for agents that cannot take a busy send, one-at-a-time
delivery for agents that queue a busy send themselves, one deadline bounding
both the interrupt call and the wait for the turn end, at most one
``x-optio-interrupt`` per turn, and the synthetic events the conversation UI
renders (``x-optio-queued``, ``x-optio-taken``, ``x-optio-interrupt``). The
events go through the wrapper's ``emit`` hook into its own event stream, so
the listener buffers (and a resume persists) them like native events.

Fix 13a (owner rulings from manual testing, 2026-09-15): every message a
``Steering`` writes carries its own id as the underlying transport's message
identity too (``Conversation.send(text, uuid=...)`` — a backend without
message identity just ignores it), so a wrapper that DOES have one (Claude
Code's CLI stdin ``uuid``) can key its own lifecycle events by it. A message
written onto a native queue (``NATIVE_QUEUE``) gets a trailing blank line.

Fix 17 (owner ruling 2026-09-15, see
docs/2026-09-15-steering-individual-delivery-design.md): a native-queue
agent (Claude Code) folds every prompt waiting in its own queue into ONE
user message when it starts a turn, but takes them one by one at tool
boundaries. With at most ONE optio message in that queue at any time, every
message arrives as its own user message. So messages sent while such an
agent is busy wait in an ordered pending list, and at most one is "in
flight" (written to the agent, not yet taken). The next is written when the
in-flight message's lifecycle (the optional ``command_lifecycle`` hook)
reaches ``started`` or any final state, or, as a safety net, when a turn
ends or the agent is idle with nothing in flight. Send now
(``interrupt_and_send`` with empty text, with or without ``up_to``) is only
an interrupt: delivery then continues in order.

Fix 19 (owner ruling 2026-09-15, see
docs/2026-09-15-steering-session-end-design.md): on resume, ``requeue``
sends again, in order and under new ids, the messages the previous run
left queued and undelivered, announced by one ``x-optio-requeued`` each.

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
# The agent holds a busy send itself: optio feeds it one message at a time.
NATIVE_QUEUE: frozenset[str] = frozenset({"joins-next-step", "queues-to-end"})

QUEUED_EVENT = "x-optio-queued"
TAKEN_EVENT = "x-optio-taken"
INTERRUPT_EVENT = "x-optio-interrupt"
# A message a previous run left undelivered, sent again under a new id
# (Fix 19, Steering.requeue): {"type": REQUEUED_EVENT, "id": old, "new_id": new}.
REQUEUED_EVENT = "x-optio-requeued"
# Messages optio delivers together form one prompt, in the order written.
PROMPT_SEPARATOR = "\n\n"
# Upper bound on the wait for the turn end after an interrupt.
TURN_END_TIMEOUT_S = 15.0
# command_lifecycle states after which the in-flight native-queue message
# no longer waits in the agent's queue (Fix 17): it was drained into a turn
# ("started"), or its life ended without that. A final state can arrive
# without "started" (cli-queue-lifecycle.md), so waiting for "started" alone
# could stall delivery for good.
_LIFECYCLE_ADVANCE_STATES = frozenset(
    {"started", "completed", "cancelled", "discarded", "refused"},
)

_VARIANT_SUFFIX = re.compile(r"\[[^\]]*\]$")


def _with_trailing_blank_line(text: str) -> str:
    """A message written directly onto the agent's own native queue (one CLI
    message per call, as opposed to one optio-joined prompt) needs its own
    separator: the CLI can fold it with adjacent queued messages into one
    turn with one text block each, and the model then reads them run-on
    (Fix 13a, owner ruling 1)."""
    return text if text.endswith("\n\n") else text + PROMPT_SEPARATOR


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
    ``command_lifecycle`` is optional: a native-queue wrapper whose
    transport reports per-message lifecycle state (Claude Code) passes it,
    and one-at-a-time delivery then advances the moment the agent takes the
    message in flight (Fix 17). Without it, delivery advances when the
    agent is idle (the safety net), one message per turn.
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
        command_lifecycle: "Callable[[dict], tuple[str, str] | None] | None" = None,
    ) -> None:
        self._conv = conversation
        self._busy_send = busy_send
        self._emit = emit
        self._is_turn_end = is_turn_end
        self._timeout_s = turn_end_timeout_s
        # A standard uuid4 string (dashed), not .hex: for a native-queue
        # wrapper this id doubles as the transport's own message uuid
        # (Fix 13a — "the uuid IS the steering id"), which for Claude Code's
        # CLI must be a schema-valid uuid.
        self._new_id = new_id or (lambda: str(uuid.uuid4()))
        self._command_lifecycle = command_lifecycle
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
        # One-at-a-time delivery onto a native queue (Fix 17): messages sent
        # while the agent was busy and not yet written to it, in order, as
        # (id, text as it will be written); and the id of the one message
        # written to the agent that it has not taken yet (at most one).
        self._native_pending: list[tuple[str, str]] = []
        self._in_flight: str | None = None
        # The exact text (already carrying its trailing blank line, if any)
        # last written for _in_flight, kept only so reset_transport() (Fix
        # 25) can write it again if the transport dies before taking it.
        # Stale once _in_flight is cleared; only ever read while it is set.
        self._in_flight_text: str | None = None
        self._advance_task: asyncio.Task | None = None
        self._unsubscribe = conversation.on_event(self._on_event)

    @property
    def held_ids(self) -> list[str]:
        return [qid for qid, _ in self._held]

    @property
    def pending_ids(self) -> list[str]:
        """Native-queue messages not written to the agent yet, in order (Fix 17)."""
        return [qid for qid, _ in self._native_pending]

    @property
    def in_flight_id(self) -> str | None:
        """The native-queue message written to the agent and not taken yet (Fix 17)."""
        return self._in_flight

    def close(self) -> None:
        unsubscribe, self._unsubscribe = self._unsubscribe, (lambda: None)
        unsubscribe()

    # -- turn end --------------------------------------------------------------

    def _on_event(self, event: dict) -> None:
        advanced = False
        if self._in_flight is not None and self._command_lifecycle is not None:
            parsed = self._command_lifecycle(event)
            if (
                parsed is not None
                and parsed[0] == self._in_flight
                and parsed[1] in _LIFECYCLE_ADVANCE_STATES
            ):
                self._in_flight = None
                advanced = True
        turn_end = self._is_turn_end(event)
        if turn_end:
            self._turn_end.set()
            self._interrupted = False
            if self._held and (self._flush_task is None or self._flush_task.done()):
                self._flush_task = asyncio.ensure_future(self._flush_after_turn())
        idle = not self._conv.is_pending()
        if idle:
            # An idle agent holds nothing in its queue: the message in flight
            # has been taken (or dropped). This is what lets the safety net
            # below also serve an agent that reports no lifecycle at all.
            self._in_flight = None
        if self._native_pending and self._in_flight is None and (advanced or turn_end or idle):
            self._schedule_advance()
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
        if self._interrupted and idle:
            self._interrupted = False

    async def _flush_after_turn(self) -> None:
        async with self._lock:
            try:
                await self._deliver([])
            except ConversationClosed:
                _LOG.warning("steering: conversation closed before held messages were delivered")

    def _schedule_advance(self) -> None:
        if self._advance_task is None or self._advance_task.done():
            self._advance_task = asyncio.ensure_future(self._advance())

    async def _advance(self) -> None:
        """Write the next pending native-queue message while none is in
        flight (Fix 17). Runs as its own task: _on_event is called from the
        wrapper's event dispatch and must never wait for _lock there."""
        async with self._lock:
            try:
                while self._in_flight is None and self._native_pending:
                    self._check_open()
                    await self._write_next_locked()
            except ConversationClosed:
                _LOG.warning(
                    "steering: conversation closed; %d queued message(s) not delivered",
                    len(self._native_pending),
                )

    async def settle(self) -> None:
        """Wait for a turn-end flush or a native-queue write in progress
        (tests, orderly teardown)."""
        for task in (self._flush_task, self._advance_task):
            if task is not None:
                await task

    # -- helpers ---------------------------------------------------------------

    def _check_open(self) -> None:
        if getattr(self._conv, "closed", False):
            raise ConversationClosed("conversation closed")

    async def _deliver(self, extra: list[str], *, extra_id: str | None = None) -> None:
        """Send everything optio holds plus ``extra`` as ONE prompt, and report
        the held ids in one x-optio-taken (sent one by one, a cuts-in agent
        would cancel each previous message). ``extra_id`` becomes the sent
        message's uuid, but only when it is the ONLY thing being sent (no
        held ids joined in) — a multi-item join has no single id to carry."""
        ids = [qid for qid, _ in self._held]
        texts = [text for _, text in self._held] + [t for t in extra if t]
        self._held = []
        if ids:
            self._emit({"type": TAKEN_EVENT, "ids": ids})
        if texts:
            send_uuid = extra_id if not ids and len(texts) == 1 else None
            await self._conv.send(PROMPT_SEPARATOR.join(texts), uuid=send_uuid)

    async def _write_next_locked(self) -> None:
        """Write the oldest pending native-queue message, unless one is
        already in flight (Fix 17). It already carries its trailing blank
        line, and its id becomes its transport uuid. The caller holds
        _lock."""
        if self._in_flight is not None or not self._native_pending:
            return
        qid, text = self._native_pending.pop(0)
        self._in_flight = qid
        self._in_flight_text = text
        await self._conv.send(text, uuid=qid)

    async def reset_transport(self) -> None:
        """Fix 25 (final-review-2 I3 / ledger line 399): the transport under
        this Steering was just replaced — a model/effort relaunch SIGTERMs
        the CLI mid-turn and reattaches a whole new process underneath the
        same Conversation. The dead process took the in-flight message with
        it: its ``command_lifecycle`` (and the turn's own ``result``) will
        now never arrive, so ``_in_flight`` would otherwise stay set
        forever and the native queue (Fix 17's one-at-a-time gate) would
        never advance again — the messages behind it silently lost.

        Puts that message back at the front of ``_native_pending`` — written
        again, not dropped — and writes the next deliverable message (it, if
        nothing else was already in flight) to the newly attached transport.
        ``_native_pending`` (and optio's own held queue) are otherwise left
        exactly as they were: nothing here is lost or reordered. Callers
        route this through the same place they already reach this Steering
        (no new plumbing): the session re-attaches the new process, THEN
        calls this, so the write below reaches it, not the dead one."""
        self._check_open()
        async with self._lock:
            if self._in_flight is not None:
                self._native_pending.insert(0, (self._in_flight, self._in_flight_text))
                self._in_flight = None
                self._in_flight_text = None
            # The turn that _interrupted tracked died with the old process;
            # nothing is left to mark interrupted, and a stale True would
            # silently swallow the new process's first Interrupt.
            self._interrupted = False
            await self._write_next_locked()

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
        """Idle: a plain send. Busy: a native-queue agent (joins-next-step,
        queues-to-end) gets it one at a time (Fix 17): it joins the pending
        list and is written at once only if nothing is in flight. Any other
        agent: optio holds it until the turn ends. A busy send reports
        x-optio-queued first, with the ORIGINAL text. The message's id
        (``qid``) is also its transport uuid (Fix 13a); a native-queue
        message additionally gets a trailing blank line. A send that finds
        a native-queue agent idle while earlier messages are still pending
        queues behind them rather than overtaking them."""
        self._check_open()
        qid = self._new_id()
        async with self._lock:
            busy = self._conv.is_pending()
            if self._busy_send() in NATIVE_QUEUE and not self._held:
                if not busy:
                    self._in_flight = None  # an idle agent holds nothing in its queue
                if not busy and not self._native_pending:
                    await self._conv.send(text, uuid=qid)
                    return SendOutcome(id=qid, queued=False)
                self._emit({"type": QUEUED_EVENT, "id": qid, "text": text})
                self._native_pending.append((qid, _with_trailing_blank_line(text)))
                await self._write_next_locked()
                return SendOutcome(id=qid, queued=True)
            if not busy and not self._held:
                await self._conv.send(text, uuid=qid)
                return SendOutcome(id=qid, queued=False)
            self._held.append((qid, text))
            self._emit({"type": QUEUED_EVENT, "id": qid, "text": text})
            if not busy:
                # The turn already ended and a flush is due: deliver now.
                await self._deliver([])
            return SendOutcome(id=qid, queued=True)

    async def interrupt_and_send(self, text: str, *, up_to: str | None = None) -> str | None:
        """Stop the running step and deliver ``text``. Empty ``text`` is
        Send now: returns None.

        Native-queue agent (Fix 17): see _interrupt_and_send_native.
        ``up_to`` (the queued id "Send now" was clicked on) is accepted and
        needs nothing more: the agent's queue never holds more than the one
        message in flight, so messages after ``up_to`` are only written once
        their predecessors started, and are taken when the agent is ready.

        Any other agent: deliver what optio holds plus ``text`` as one
        prompt. ``cuts-in`` agents cancel natively on the send itself;
        every other agent is interrupted and given at most
        ``turn_end_timeout_s`` to end the turn. Send now with nothing held
        is a no-op: there is nothing to interrupt and nothing to send, so it
        neither emits x-optio-interrupt nor stops the turn now running.
        ``up_to`` is ignored.

        Either way, at most one x-optio-interrupt (and one underlying
        interrupt() call) happens per turn: if interrupt() already marked
        this turn interrupted, this still waits for the turn end, but emits
        no second marker and sends no second interrupt. Non-empty ``text``
        gets its own x-optio-queued (id, text) — the same id this call
        returns — emitted after the interrupt marker (if any), so the
        reducer can dedupe the steer's local echo against the wire by id
        (final-review M3)."""
        self._check_open()
        if self._busy_send() in NATIVE_QUEUE and not self._held:
            return await self._interrupt_and_send_native(text)
        if not text and not self._held and self._busy_send() not in NATIVE_QUEUE:
            return None
        qid = self._new_id() if text else None
        async with self._lock:
            pending = self._conv.is_pending()
            if pending:
                already_interrupted = self._interrupted
                if not already_interrupted:
                    self._interrupted = True
                    self._emit({"type": INTERRUPT_EVENT, "by": "user"})
                if self._busy_send() != "cuts-in":
                    await self._interrupt_and_wait(call_interrupt=not already_interrupted)
            send_text = text
            if text:
                if pending and self._busy_send() in NATIVE_QUEUE:
                    send_text = _with_trailing_blank_line(text)
                self._emit({"type": QUEUED_EVENT, "id": qid, "text": text})
            await self._deliver([send_text], extra_id=qid)
        return qid

    async def _interrupt_and_send_native(self, text: str) -> str | None:
        """interrupt_and_send for a native-queue agent (Fix 17).

        Busy: emit the interrupt marker (once per turn), then x-optio-queued
        for ``text`` and append it to the pending list WITHOUT writing it
        (the design's order: append, then interrupt; a message only written
        after the turn end cannot be taken into the turn the interrupt cuts
        off), interrupt, and wait for the turn end under the one deadline.
        The agent keeps the message in flight through the interrupt and
        runs it next; the rest follow one at a time, so ``text`` arrives
        last, as its own message. With nothing in flight, the next pending
        message is written right after the turn end (or the deadline).

        Idle: ``text`` is a plain send, unless earlier messages still wait
        (then it queues behind them)."""
        qid = self._new_id() if text else None
        async with self._lock:
            pending = self._conv.is_pending()
            if not pending:
                self._in_flight = None  # an idle agent holds nothing in its queue
            call_interrupt = pending and not self._interrupted
            if call_interrupt:
                self._interrupted = True
                self._emit({"type": INTERRUPT_EVENT, "by": "user"})
            if text:
                self._emit({"type": QUEUED_EVENT, "id": qid, "text": text})
                if pending or self._native_pending:
                    self._native_pending.append((qid, _with_trailing_blank_line(text)))
                else:
                    await self._conv.send(text, uuid=qid)
            if pending:
                await self._interrupt_and_wait(call_interrupt=call_interrupt)
            await self._write_next_locked()
        return qid

    async def requeue(self, messages: list[tuple[str, str]]) -> list[str]:
        """Resume (Fix 19, owner ruling 2026-09-15, finding 6 #2): deliver
        again messages a previous run of the agent left queued and never
        delivered, given as (old id, ORIGINAL text) in their original order.
        Each gets a NEW id (the old one may already be known to the agent's
        own transcript) and one x-optio-requeued {id: old, new_id: new},
        emitted before anything is written; never a second x-optio-queued,
        so the UI re-keys the bubble it already shows. A native-queue agent
        gets them one at a time (Fix 17), ahead of any message sent since
        the resume: the first is written at once if nothing is in flight.
        Any other agent holds them at the front of optio's queue, delivered
        at the turn end, or at once when idle. Returns the new ids."""
        self._check_open()
        if not messages:
            return []
        async with self._lock:
            renamed = [(old, self._new_id(), text) for old, text in messages]
            for old, new, _text in renamed:
                self._emit({"type": REQUEUED_EVENT, "id": old, "new_id": new})
            if self._busy_send() in NATIVE_QUEUE and not self._held:
                if not self._conv.is_pending():
                    self._in_flight = None  # an idle agent holds nothing in its queue
                self._native_pending[0:0] = [
                    (new, _with_trailing_blank_line(text)) for _old, new, text in renamed
                ]
                await self._write_next_locked()
            else:
                self._held[0:0] = [(new, text) for _old, new, text in renamed]
                if not self._conv.is_pending():
                    await self._deliver([])
        return [new for _old, new, _text in renamed]

    async def interrupt(self) -> None:
        """Stop only. Emits x-optio-interrupt at most once per turn, shared
        with interrupt_and_send: a second Interrupt (double click, or one
        pressed while interrupt_and_send still waits for the turn end) emits
        nothing and sends no second control_request. Deliberately lock-free,
        so it never waits behind an interrupt_and_send. Messages pending on
        a native queue keep going one at a time afterwards (Fix 17)."""
        self._check_open()
        if self._conv.is_pending():
            if self._interrupted:
                return
            self._interrupted = True
            self._emit({"type": INTERRUPT_EVENT, "by": "user"})
        await self._conv.interrupt()
