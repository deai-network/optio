"""AGENTS.md composer for optio-claudecode.

v1 has no resume support, so this is currently a one-line forward to
``optio_host.agents.compose_agents_md`` with ``resume_section=None``.
When resume lands, render and pass the section here.
"""

from optio_host.agents import compose_agents_md as _host_compose_agents_md


__all__ = ["compose_agents_md"]


def compose_agents_md(consumer_instructions: str) -> str:
    """Render <workdir>/AGENTS.md for an optio-claudecode task."""
    return _host_compose_agents_md(consumer_instructions, resume_section=None)
