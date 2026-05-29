"""optio-agents log/deliverables coordination protocol.

Built on top of optio_host.host primitives. Knows nothing about specific
consumer task types (opencode, recipe execution, ...).
"""

from optio_agents.protocol.parser import (
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
]
