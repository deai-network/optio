from __future__ import annotations

from typing import Literal

from optio_antigravity.types import AntigravityTaskConfig
from optio_claudecode.types import ClaudeCodeTaskConfig
from optio_codex.types import CodexTaskConfig
from optio_cursor.types import CursorTaskConfig
from optio_grok.types import GrokTaskConfig
from optio_kimicode.types import KimiCodeTaskConfig
from optio_opencode.types import OpencodeTaskConfig

AgentType = Literal[
    "kimicode",
    "grok",
    "cursor",
    "claudecode",
    "codex",
    "opencode",
    "antigravity",
]

AgentTaskConfig = (
    KimiCodeTaskConfig
    | GrokTaskConfig
    | CursorTaskConfig
    | ClaudeCodeTaskConfig
    | CodexTaskConfig
    | OpencodeTaskConfig
    | AntigravityTaskConfig
)
