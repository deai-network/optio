"""optio-agents — agent-coordination layer for optio task types.

Owns the optio.log keyword protocol (parser + session driver), the
``HookContext`` passed to agent task hooks, and the single source of
truth for the LLM-facing keyword-protocol documentation.
"""

from optio_agents.context import HookContext, HookContextProtocol, SYSTEM_MESSAGE_PREFIX
from optio_agents.conversation import (
    Conversation,
    ConversationClosed,
    PermissionDecision,
    PermissionRequest,
)
from optio_agents.protocol import (
    CALLER_MESSAGE_QUEUE_BOUND,
    DELIVERABLE_QUEUE_BOUND,
    RESUME_NOTICE,
    AgentSender,
    BrowserMode,
    CallerMessageCallback,
    DeliverableCallback,
    DeliverableEvent,
    DoneEvent,
    ErrorEvent,
    HookCallback,
    LogEvent,
    Protocol,
    ProtocolFeatures,
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
from optio_agents import claustrum
from optio_agents import seeds
from optio_agents import session_controls
from optio_agents.session_controls import ControlOption, SessionControl
from optio_agents import input_listener
from optio_agents import tmux_input
from optio_agents.input_listener import serialized, start_input_listener
from optio_agents.tmux_input import NAV_KEYS, send_key_to_tmux, send_text_to_tmux

__all__ = [
    "input_listener",
    "tmux_input",
    "serialized",
    "start_input_listener",
    "NAV_KEYS",
    "send_key_to_tmux",
    "send_text_to_tmux",
    "HookContext",
    "HookContextProtocol",
    "SYSTEM_MESSAGE_PREFIX",
    "Conversation",
    "ConversationClosed",
    "PermissionDecision",
    "PermissionRequest",
    "browser_shims",
    "claustrum",
    "seeds",
    "session_controls",
    "SessionControl",
    "ControlOption",
    "run_log_protocol_session",
    "fetch_deliverable_text",
    "AgentSender",
    "DeliverableCallback",
    "CallerMessageCallback",
    "HookCallback",
    "DELIVERABLE_QUEUE_BOUND",
    "CALLER_MESSAGE_QUEUE_BOUND",
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
    "ProtocolFeatures",
    "BrowserMode",
    "RESUME_NOTICE",
    "build_log_channel_prompt",
]
