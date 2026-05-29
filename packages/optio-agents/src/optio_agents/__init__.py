"""optio-agents — agent-coordination layer for optio task types.

Owns the optio.log keyword protocol (parser + session driver), the
``HookContext`` passed to agent task hooks, and the single source of
truth for the LLM-facing keyword-protocol documentation.
"""

from optio_agents.context import HookContext, HookContextProtocol
from optio_agents.protocol import (
    DELIVERABLE_QUEUE_BOUND,
    BrowserMode,
    DeliverableCallback,
    DeliverableEvent,
    DoneEvent,
    ErrorEvent,
    HookCallback,
    LogEvent,
    Protocol,
    StatusEvent,
    UnknownLine,
    build_log_channel_prompt,
    fetch_deliverable_text,
    get_protocol,
    parse_log_line,
    relativize_deliverable_path,
    run_log_protocol_session,
    validate_deliverable_path,
)
from optio_agents import browser_shims
from optio_agents import seeds

__all__ = [
    "HookContext",
    "HookContextProtocol",
    "browser_shims",
    "seeds",
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
    "get_protocol",
    "Protocol",
    "BrowserMode",
    "build_log_channel_prompt",
]
