"""Abstract conversation surface for optio agent backends.

Type surface only: the semantic operations (send / events / answers / busy /
close) are shared across backends; every concrete event payload is
backend-specific and passed through transparently as a dict. Concrete
implementations live in the agent packages (ClaudeCodeConversation in
optio-claudecode; an opencode implementation over HTTP+SSE may follow).

See docs/2026-06-10-claudecode-conversation-gate-design.md §2.1.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Awaitable, Callable, Literal, Protocol, runtime_checkable


class ConversationClosed(Exception):
    """Raised by send()/interrupt() after the conversation has ended."""


@dataclass(frozen=True)
class PermissionRequest:
    """One tool-permission question from the agent backend."""
    tool_name: str
    input: dict
    raw: dict  # full backend payload, transparent


@dataclass(frozen=True)
class PermissionDecision:
    behavior: Literal["allow", "deny"]
    updated_input: dict | None = None  # allow with modified input
    message: str | None = None         # deny reason, surfaced to the model


EventHandler = Callable[[dict], "Awaitable[None] | None"]
MessageHandler = Callable[[str], "Awaitable[None] | None"]
PermissionHandler = Callable[[PermissionRequest], "Awaitable[PermissionDecision]"]
Unsubscribe = Callable[[], None]


@runtime_checkable
class Conversation(Protocol):
    """Live conversation with a running agent session.

    Secondary interface: process management (monitor/cancel) stays on the
    regular optio surface; this object only talks to the model.
    """

    async def send(self, text: str) -> None:
        """Queue one user message. Raises ConversationClosed after end."""
        ...

    def on_event(self, handler: EventHandler) -> Unsubscribe:
        """Subscribe to the transparent backend event stream (dicts,
        unmodified, live-only). Returns an unsubscribe function."""
        ...

    def on_message(self, handler: MessageHandler) -> Unsubscribe:
        """Simplified tier: one call per completed turn with the final
        answer text."""
        ...

    def on_permission_request(self, handler: PermissionHandler) -> Unsubscribe:
        """Register the permission gate (at most one; replaces prior)."""
        ...

    def is_pending(self) -> bool:
        """True while at least one sent message awaits its turn result."""
        ...

    async def interrupt(self) -> None:
        """Abort the current turn at the next safe point. No-op when idle."""
        ...

    async def close(self) -> None:
        """Request cooperative shutdown of the owning task. Idempotent."""
        ...

    @property
    def closed(self) -> bool:
        """True once the session has ended (any cause)."""
        ...
