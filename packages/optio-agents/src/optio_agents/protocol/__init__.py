"""optio-agents log/deliverables coordination protocol.

Built on top of optio_host.host primitives. Knows nothing about specific
consumer task types (opencode, recipe execution, ...).
"""

from optio_agents.protocol.parser import (
    AttentionEvent,
    BrowserEvent,
    CallerMessageEvent,
    ClientMessageEvent,
    DeliverableEvent,
    DoneEvent,
    ErrorEvent,
    LogEvent,
    StatusEvent,
    UnknownLine,
    parse_log_line,
    relativize_deliverable_path,
    validate_deliverable_path,
)
from optio_agents.protocol.session import (
    CALLER_MESSAGE_QUEUE_BOUND,
    DELIVERABLE_QUEUE_BOUND,
    AgentSender,
    CallerMessageCallback,
    DeliverableCallback,
    HookCallback,
    fetch_deliverable_text,
    run_log_protocol_session,
)
from optio_agents.protocol.features import ProtocolFeatures
from optio_agents.protocol.protocol import BrowserMode, Protocol, get_protocol
from optio_agents.protocol.prompt import RESUME_NOTICE, build_log_channel_prompt

__all__ = [
    # parser
    "parse_log_line",
    "DeliverableEvent",
    "DoneEvent",
    "ErrorEvent",
    "BrowserEvent",
    "AttentionEvent",
    "ClientMessageEvent",
    "CallerMessageEvent",
    "LogEvent",
    "StatusEvent",
    "UnknownLine",
    "validate_deliverable_path",
    "relativize_deliverable_path",
    # session
    "run_log_protocol_session",
    "AgentSender",
    "DeliverableCallback",
    "CallerMessageCallback",
    "HookCallback",
    "fetch_deliverable_text",
    "DELIVERABLE_QUEUE_BOUND",
    "CALLER_MESSAGE_QUEUE_BOUND",
    # protocol variation
    "get_protocol",
    "Protocol",
    "ProtocolFeatures",
    "BrowserMode",
    "RESUME_NOTICE",
    "build_log_channel_prompt",
]
