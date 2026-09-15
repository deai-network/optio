"""Conversation steering: send when ready, interrupt and send, interrupt.

The engine-neutral scaffolding every wrapper's conversation listener uses for
its ``POST /send``, ``/steer`` and ``/interrupt`` routes. A wrapper declares
how its agent treats a message sent while a turn runs (``busy_send``, per
agent with per-model overrides); ``Steering`` adds what the agent lacks:
optio's own queue for agents that cannot take a busy send, one deadline
bounding both the interrupt call and the wait for the turn end, at most one
``x-optio-interrupt`` per turn, and the synthetic events the conversation UI
renders (``x-optio-queued``, ``x-optio-taken``, ``x-optio-interrupt``,
``x-optio-requeued``). The events go through the wrapper's ``emit`` hook into
its own event stream, so the listener buffers (and a resume persists) them
like native events.

Fix 13a (owner rulings from manual testing, 2026-09-15): every message a
``Steering`` writes carries its own id as the underlying transport's message
identity too (``Conversation.send(text, uuid=...)`` — a backend without
message identity just ignores it), so a wrapper that DOES have one (Claude
Code's CLI stdin ``uuid``) can key its own lifecycle events by it. A message
written onto a native queue (``NATIVE_QUEUE``, one CLI message per
``send_when_ready``/``interrupt_and_send`` call rather than one optio-joined
prompt) gets a trailing blank line so the agent does not read it run-on
against whatever it gets folded with. And ``interrupt_and_send`` grew
``up_to``: "Send now" for a native-queue agent up to one queued message,
implemented with the agent's own ``cancel_async_message`` and
``command_lifecycle`` events (both optional constructor hooks; without them
``up_to`` degrades to today's meaning, deliver everything queued).

See docs/2026-09-13-conversation-steering-design.md §2 and §3.
"""

from __future__ import annotations

import asyncio
import logging
import re
import uuid
from dataclasses import dataclass, field
from typing import Awaitable, Callable, Literal, Mapping

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
REQUEUED_EVENT = "x-optio-requeued"
# Messages optio delivers together form one prompt, in the order written.
PROMPT_SEPARATOR = "\n\n"
# Upper bound on the wait for the turn end after an interrupt.
TURN_END_TIMEOUT_S = 15.0
# command_lifecycle states that end a native-queue message's life for good
# (Fix 13a). Deliberately excludes "cancelled": that state can mean our own
# cancel_async_message succeeded (the ONLY case Steering itself resends from,
# handled explicitly in _send_now_up_to) or an unrelated abort, and the CLI
# schema itself warns resenders not to react to it blindly — only a
# correlated cancel_async_message reply is trustworthy.
_NATIVE_QUEUE_TERMINAL_STATES = frozenset({"completed", "discarded", "refused"})

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
    ``command_lifecycle`` and ``cancel_async_message`` are optional: a
    native-queue wrapper whose transport reports per-message lifecycle state
    (Claude Code) passes both to enable ``interrupt_and_send``'s ``up_to``;
    without them ``up_to`` is ignored.
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
        cancel_async_message: "Callable[[str], Awaitable[bool]] | None" = None,
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
        self._cancel_async_message = cancel_async_message
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
        # Native-queue bookkeeping for up_to (Fix 13a): ids of messages sent
        # straight through to the agent's own queue, in the order sent;
        # their as-sent text (for a cancel+re-send); and the latest
        # command_lifecycle state seen for each. Populated only when
        # command_lifecycle is wired (native-queue agents that report it).
        self._native_order: list[str] = []
        self._native_texts: dict[str, str] = {}
        self._native_lifecycle: dict[str, str] = {}
        self._lifecycle_waiters: dict[str, asyncio.Event] = {}
        self._unsubscribe = conversation.on_event(self._on_event)

    @property
    def held_ids(self) -> list[str]:
        return [qid for qid, _ in self._held]

    def close(self) -> None:
        unsubscribe, self._unsubscribe = self._unsubscribe, (lambda: None)
        unsubscribe()

    # -- turn end --------------------------------------------------------------

    def _on_event(self, event: dict) -> None:
        if self._command_lifecycle is not None:
            parsed = self._command_lifecycle(event)
            if parsed is not None:
                command_uuid, state = parsed
                self._native_lifecycle[command_uuid] = state
                if state == "started":
                    waiter = self._lifecycle_waiters.get(command_uuid)
                    if waiter is not None:
                        waiter.set()
                if state in _NATIVE_QUEUE_TERMINAL_STATES:
                    self._native_lifecycle.pop(command_uuid, None)
                    self._native_texts.pop(command_uuid, None)
                    if command_uuid in self._native_order:
                        self._native_order.remove(command_uuid)
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
        ends. A busy send reports x-optio-queued first. The message's id
        (``qid``) is also its transport uuid (Fix 13a); a native-queue send
        additionally gets a trailing blank line so the agent's own fold of
        separately-sent queued messages does not run this one into the
        next — x-optio-queued keeps the ORIGINAL text."""
        self._check_open()
        qid = self._new_id()
        async with self._lock:
            busy = self._conv.is_pending()
            if not busy and not self._held:
                await self._conv.send(text, uuid=qid)
                return SendOutcome(id=qid, queued=False)
            if self._busy_send() in NATIVE_QUEUE and not self._held:
                sent_text = _with_trailing_blank_line(text)
                self._emit({"type": QUEUED_EVENT, "id": qid, "text": text})
                if self._command_lifecycle is not None:
                    self._native_order.append(qid)
                    self._native_texts[qid] = sent_text
                    self._native_lifecycle[qid] = "queued"
                await self._conv.send(sent_text, uuid=qid)
                return SendOutcome(id=qid, queued=True)
            self._held.append((qid, text))
            self._emit({"type": QUEUED_EVENT, "id": qid, "text": text})
            if not busy:
                # The turn already ended and a flush is due: deliver now.
                await self._deliver([])
            return SendOutcome(id=qid, queued=True)

    async def interrupt_and_send(self, text: str, *, up_to: str | None = None) -> str | None:
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
        echo against the wire by id (final-review M3). While busy, ``text``
        also gets the trailing-blank-line treatment (Fix 13a): it is about
        to join the agent's own native queue exactly like a busy
        send_when_ready would.

        ``up_to`` (Fix 13a, owner ruling 2): "Send now" up to one already
        queued message id, for a native-queue agent that reports
        command_lifecycle and supports cancel_async_message (both optional
        constructor hooks — without either, ``up_to`` is silently ignored
        and this call keeps today's meaning). See _send_now_up_to."""
        self._check_open()
        if (
            up_to is not None
            and self._busy_send() in NATIVE_QUEUE
            and self._cancel_async_message is not None
            and self._command_lifecycle is not None
        ):
            async with self._lock:
                return await self._send_now_up_to(up_to)
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

    async def _send_now_up_to(self, up_to: str) -> None:
        """Cancel every not-yet-started message after ``up_to`` in the
        agent's native queue, interrupt (plain — the CLI then runs 1..up_to
        as its next turn), and once ``up_to`` has itself been seen
        ``started`` (bounded by ``turn_end_timeout_s``; on timeout, resend
        anyway and log it), re-send each successfully cancelled message, in
        queue order, with the same text and a fresh uuid — emitting
        ``x-optio-requeued`` for each so the UI re-keys its bubble. A
        ``cancelled:false`` reply means the message was already taken (or
        unknown) and will be delivered normally: it is not re-sent. Assumes
        the caller holds ``_lock``."""
        try:
            k_index = self._native_order.index(up_to)
        except ValueError:
            k_index = len(self._native_order) - 1
        later_ids = list(self._native_order[k_index + 1:])
        cancelled_ids = []
        for mid in later_ids:
            if self._native_lifecycle.get(mid) == "started":
                continue  # already taken; will be delivered anyway
            if await self._cancel_async_message(mid):
                cancelled_ids.append(mid)
        if self._conv.is_pending():
            already_interrupted = self._interrupted
            if not already_interrupted:
                self._interrupted = True
                self._emit({"type": INTERRUPT_EVENT, "by": "user"})
            await self._interrupt_and_wait(call_interrupt=not already_interrupted)
        for mid in cancelled_ids:
            if mid in self._native_order:
                self._native_order.remove(mid)
        if not cancelled_ids:
            return None
        await self._wait_for_started(up_to)
        for mid in cancelled_ids:
            resend_text = self._native_texts.pop(mid, "")
            new_id = self._new_id()
            self._native_order.append(new_id)
            self._native_texts[new_id] = resend_text
            self._native_lifecycle[new_id] = "queued"
            await self._conv.send(resend_text, uuid=new_id)
            self._emit({"type": REQUEUED_EVENT, "id": mid, "new_id": new_id})
        return None

    async def _wait_for_started(self, command_uuid: str) -> None:
        """Wait until ``command_uuid``'s command_lifecycle reports
        'started', bounded by ``turn_end_timeout_s``; on timeout, log and
        return anyway so the caller re-sends regardless."""
        if self._native_lifecycle.get(command_uuid) == "started":
            return
        waiter = self._lifecycle_waiters.setdefault(command_uuid, asyncio.Event())
        try:
            async with asyncio.timeout(self._timeout_s):
                await waiter.wait()
        except TimeoutError:
            _LOG.warning(
                "steering: no 'started' for message %s within %.0f s; "
                "re-sending the queued messages after it anyway",
                command_uuid, self._timeout_s,
            )
        finally:
            self._lifecycle_waiters.pop(command_uuid, None)

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
