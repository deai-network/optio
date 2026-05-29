"""optio-agents — agent-coordination layer for optio task types.

Owns the optio.log keyword protocol (parser + session driver), the
``HookContext`` passed to agent task hooks, and the single source of
truth for the LLM-facing keyword-protocol documentation.
"""

from optio_agents.context import HookContext, HookContextProtocol
from optio_agents.protocol import (
    DELIVERABLE_QUEUE_BOUND,
    DeliverableCallback,
    DeliverableEvent,
    DoneEvent,
    ErrorEvent,
    HookCallback,
    LogEvent,
    StatusEvent,
    UnknownLine,
    fetch_deliverable_text,
    parse_log_line,
    relativize_deliverable_path,
    run_log_protocol_session,
    validate_deliverable_path,
)
from optio_agents import browser_capture

__all__ = [
    "HookContext",
    "browser_capture",
    "HookContextProtocol",
    "run_log_protocol_session",
    "fetch_deliverable_text",
    "DeliverableCallback",
    "HookCallback",
    "DELIVERABLE_QUEUE_BOUND",
    "parse_log_line",
    "DeliverableEvent",
    "DoneEvent",
    "ErrorEvent",
    "LogEvent",
    "StatusEvent",
    "UnknownLine",
    "validate_deliverable_path",
    "relativize_deliverable_path",
]
