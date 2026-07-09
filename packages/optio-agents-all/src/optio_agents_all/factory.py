from __future__ import annotations

from typing import Awaitable, Callable

from optio_agents.account import EMPTY, AccountInfo

from optio_antigravity import create_antigravity_task
from optio_claudecode import analyze_account as _claudecode_analyze
from optio_codex import analyze_account as _codex_analyze
from optio_cursor import analyze_account as _cursor_analyze
from optio_kimicode import analyze_account as _kimicode_analyze
from optio_antigravity import analyze_account as _antigravity_analyze
from optio_grok import analyze_account as _grok_analyze
from optio_claudecode import create_claudecode_task
from optio_codex import create_codex_task
from optio_cursor import create_cursor_task
from optio_grok import create_grok_task
from optio_kimicode import create_kimicode_task
from optio_opencode import analyze_accounts as _opencode_analyze_accounts
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


# Per-engine account analyzers, keyed by agent_type. claudecode is the reference
# implementation; the other six land as their follow-up plans give them an
# ``analyze_account`` (until then they are absent from the registry).
_ANALYZE_REGISTRY: dict[AgentType, Callable] = {
    "claudecode": _claudecode_analyze,
    "codex": _codex_analyze,
    "cursor": _cursor_analyze,
    "kimicode": _kimicode_analyze,
    "antigravity": _antigravity_analyze,
    "grok": _grok_analyze,
}


async def analyze_account(agent_type: AgentType, creds):
    """Dispatch to the per-engine analyzer by agent_type. Raises ValueError for
    an unknown/not-yet-implemented engine."""
    fn = _ANALYZE_REGISTRY.get(agent_type)
    if fn is None:
        raise ValueError(f"no analyze_account for agent_type: {agent_type!r}")
    return await fn(creds)


# --- plural account seam ------------------------------------------------------
#
# ``analyze_accounts`` is the plural generalization: a seed may back more than
# one account (opencode fans out to one account per configured provider). Every
# single-account engine wraps its singular ``analyze_account`` into a 1-element
# list, dropping the fail-soft ``EMPTY`` sentinel to an empty list. opencode
# registers its real per-provider meta-analyzer (``optio_opencode``), which maps
# an ``auth.json`` dict into one AccountInfo per configured provider.


def _single(
    analyze: Callable[..., Awaitable[AccountInfo]],
) -> Callable[..., Awaitable[list[AccountInfo]]]:
    """Adapt a singular ``analyze_account`` into the plural shape: a 1-element
    list, or ``[]`` when analysis failed soft (``None``/``EMPTY``)."""

    async def _wrap(creds) -> list[AccountInfo]:
        info = await analyze(creds)
        return [info] if info is not None and info != EMPTY else []

    return _wrap


_ANALYZE_ACCOUNTS_REGISTRY: dict[
    AgentType, Callable[..., Awaitable[list[AccountInfo]]]
] = {
    "claudecode": _single(_claudecode_analyze),
    "codex": _single(_codex_analyze),
    "cursor": _single(_cursor_analyze),
    "kimicode": _single(_kimicode_analyze),
    "antigravity": _single(_antigravity_analyze),
    "grok": _single(_grok_analyze),
    # opencode's creds are its ``auth.json`` dict (provider_id -> entry); the
    # meta-analyzer fans out to one AccountInfo per configured provider.
    "opencode": _opencode_analyze_accounts,
}


async def analyze_accounts(agent_type: AgentType, creds) -> list[AccountInfo]:
    """Dispatch to the per-engine plural analyzer by agent_type, returning the
    list of accounts backing the seed. Raises ValueError for an unknown engine."""
    fn = _ANALYZE_ACCOUNTS_REGISTRY.get(agent_type)
    if fn is None:
        raise ValueError(f"no analyze_accounts for agent_type: {agent_type!r}")
    return await fn(creds)
