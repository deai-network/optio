"""Conversation steering: send when ready, interrupt and send, interrupt.

The engine-neutral scaffolding every wrapper's conversation listener uses for
its ``POST /send``, ``/steer`` and ``/interrupt`` routes. A wrapper declares
how its agent treats a message sent while a turn runs (``busy_send``, per
agent with per-model overrides); ``Steering`` adds what the agent lacks:
optio's own queue for agents that cannot take a busy send, the bounded wait
for the turn end after an interrupt, and the synthetic events the
conversation UI renders (``x-optio-queued``, ``x-optio-taken``,
``x-optio-interrupt``). The events go through the wrapper's ``emit`` hook
into its own event stream, so the listener buffers (and a resume persists)
them like native events.

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
        self._unsubscribe = conversation.on_event(self._on_event)

    @property
    def held_ids(self) -> list[str]:
        return [qid for qid, _ in self._held]

    def close(self) -> None:
        unsubscribe, self._unsubscribe = self._unsubscribe, (lambda: None)
        unsubscribe()

    # -- turn end --------------------------------------------------------------

    def _on_event(self, event: dict) -> None:
        if not self._is_turn_end(event):
            return
        self._turn_end.set()
        if self._held and (self._flush_task is None or self._flush_task.done()):
            self._flush_task = asyncio.ensure_future(self._flush_after_turn())

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

    async def _interrupt_and_wait(self) -> None:
        self._turn_end.clear()
        await self._conv.interrupt()
        if self._turn_end.is_set():
            return
        try:
            await asyncio.wait_for(self._turn_end.wait(), self._timeout_s)
        except asyncio.TimeoutError:
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
        Empty ``text`` is Send now: returns None."""
        self._check_open()
        qid = self._new_id() if text else None
        async with self._lock:
            if self._conv.is_pending():
                self._emit({"type": INTERRUPT_EVENT, "by": "user"})
                if self._busy_send() != "cuts-in":
                    await self._interrupt_and_wait()
            await self._deliver([text])
        return qid

    async def interrupt(self) -> None:
        """Stop only. Emits x-optio-interrupt while a turn runs. Deliberately
        lock-free, so it never waits behind an interrupt_and_send."""
        self._check_open()
        if self._conv.is_pending():
            self._emit({"type": INTERRUPT_EVENT, "by": "user"})
        await self._conv.interrupt()
