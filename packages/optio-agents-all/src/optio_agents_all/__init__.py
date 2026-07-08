"""optio-agents-all — meta-factory over every wrapped optio agent engine.

Exposes a single ``create_task`` that dispatches to the right per-engine
factory based on ``config.agent_type``, over a tagged discriminated union
of the seven engine ``TaskConfig`` dataclasses.
"""

from __future__ import annotations

from optio_antigravity import create_antigravity_task
from optio_claudecode import create_claudecode_task
from optio_codex import create_codex_task
from optio_cursor import create_cursor_task
from optio_grok import create_grok_task
from optio_kimicode import create_kimicode_task
from optio_opencode import create_opencode_task

from optio_agents_all.factory import create_task
from optio_agents_all.info import AGENTS, get_agent_info
from optio_agents_all.types import (
    AgentTaskConfig,
    AgentType,
    AntigravityTaskConfig,
    ClaudeCodeTaskConfig,
    CodexTaskConfig,
    CursorTaskConfig,
    GrokTaskConfig,
    KimiCodeTaskConfig,
    OpencodeTaskConfig,
)

__all__ = [
    "create_task",
    "AGENTS",
    "get_agent_info",
    "AgentType",
    "AgentTaskConfig",
    "KimiCodeTaskConfig",
    "GrokTaskConfig",
    "CursorTaskConfig",
    "ClaudeCodeTaskConfig",
    "CodexTaskConfig",
    "OpencodeTaskConfig",
    "AntigravityTaskConfig",
    "create_kimicode_task",
    "create_grok_task",
    "create_cursor_task",
    "create_claudecode_task",
    "create_codex_task",
    "create_opencode_task",
    "create_antigravity_task",
]
