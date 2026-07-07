from __future__ import annotations

from typing import Callable

from optio_antigravity import create_antigravity_task
from optio_claudecode import create_claudecode_task
from optio_codex import create_codex_task
from optio_cursor import create_cursor_task
from optio_grok import create_grok_task
from optio_kimicode import create_kimicode_task
from optio_opencode import create_opencode_task

from optio_agents_all.types import AgentTaskConfig, AgentType

_REGISTRY: dict[AgentType, Callable] = {
    "kimicode": create_kimicode_task,
    "grok": create_grok_task,
    "cursor": create_cursor_task,
    "claudecode": create_claudecode_task,
    "codex": create_codex_task,
    "opencode": create_opencode_task,
    "antigravity": create_antigravity_task,
}


def create_task(
    process_id,
    name,
    config: AgentTaskConfig,
    description=None,
    metadata=None,
):
    """Create a task for any wrapped agent, dispatched by config.agent_type."""
    factory = _REGISTRY.get(config.agent_type)
    if factory is None:
        raise ValueError(f"unknown agent_type: {config.agent_type!r}")
    return factory(
        process_id, name, config, description=description, metadata=metadata
    )  # type: ignore[arg-type]
