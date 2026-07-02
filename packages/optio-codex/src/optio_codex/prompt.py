"""AGENTS.md composition for optio-codex.

Codex reads an ``AGENTS.md`` file in its workdir. The shared framing and
the keyword-protocol documentation are owned by ``optio-agents`` (the
prompt SSOT); this module only threads codex's protocol mode through.
"""

from optio_agents.prompt import compose_agents_md as _compose_agents_md_host
from optio_agents.protocol import ProtocolFeatures, build_log_channel_prompt


# Self-contained System: explainer for sessions without the keyword-protocol
# docs (which normally explain the convention). Per-wrapper copy is the
# established pattern (claudecode/opencode each carry their own).
_SYSTEM_PREFIX_EXPLAINER = """\
(Messages prefixed `System:` on your input channel originate from the
harness coordinating this session, not from the human user.)
"""


def compose_agents_md(
    consumer_instructions: str,
    *,
    documentation: str | None = None,
    host_protocol: bool = True,
) -> str:
    """Build the full AGENTS.md body.

    ``documentation`` is the keyword-protocol block; the session passes
    ``get_protocol(browser="suppress").documentation``. Defaults (for unit
    tests / standalone callers) to codex's ``suppress`` docs. It must
    always come from the session's ``Protocol`` where one exists — never
    rebuild features at a second site.

    ``host_protocol=False`` omits the keyword-protocol documentation and
    instead includes a self-contained ``System:`` message explainer
    (guide Part 2D). Stage 0's iframe mode always runs with
    ``host_protocol=True`` (validated in ``CodexTaskConfig``); the False
    branch serves conversation mode in a later stage.
    """
    if host_protocol:
        if documentation is None:
            documentation = build_log_channel_prompt(
                ProtocolFeatures(browser="suppress")
            )
        return _compose_agents_md_host(
            consumer_instructions, documentation=documentation,
        )
    return _compose_agents_md_host(
        consumer_instructions,
        documentation=None,
        resume_section=_SYSTEM_PREFIX_EXPLAINER,
    )
