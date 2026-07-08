from optio_grok import AGENT_INFO
from optio_grok.types import GrokTaskConfig


def test_agent_info_values():
    assert AGENT_INFO.slug == "grok"
    assert AGENT_INFO.name == "Grok Build"
    assert AGENT_INFO.url == "https://x.ai/cli"


def test_agent_info_slug_matches_agent_type():
    import dataclasses

    fields = {f.name: f for f in dataclasses.fields(GrokTaskConfig)}
    assert fields["agent_type"].default == AGENT_INFO.slug
