"""optio-host log/deliverables coordination protocol (L1).

Built on top of optio_host.host primitives. Knows nothing about specific
consumer task types (opencode, recipe execution, ...).
"""

from optio_host.protocol.parser import (
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
from optio_host.protocol.session import (
    DELIVERABLE_QUEUE_BOUND,
    DeliverableCallback,
    HookCallback,
    fetch_deliverable_text,
    run_log_protocol_session,
)

__all__ = [
    # parser
    "parse_log_line",
    "DeliverableEvent",
    "DoneEvent",
    "ErrorEvent",
    "LogEvent",
    "StatusEvent",
    "UnknownLine",
    "validate_deliverable_path",
    "relativize_deliverable_path",
    # session
    "run_log_protocol_session",
    "DeliverableCallback",
    "HookCallback",
    "fetch_deliverable_text",
    "DELIVERABLE_QUEUE_BOUND",
]
