from optio_antigravity import AGENT_INFO
from optio_antigravity.types import AntigravityTaskConfig


def test_agent_info_values():
    assert AGENT_INFO.slug == "antigravity"
    assert AGENT_INFO.name == "Antigravity CLI"
    assert AGENT_INFO.url == "https://antigravity.google"


def test_agent_info_slug_matches_agent_type():
    import dataclasses

    fields = {f.name: f for f in dataclasses.fields(AntigravityTaskConfig)}
    assert fields["agent_type"].default == AGENT_INFO.slug
