from optio_opencode import AGENT_INFO
from optio_opencode.types import OpencodeTaskConfig


def test_agent_info_values():
    assert AGENT_INFO.slug == "opencode"
    assert AGENT_INFO.name == "OpenCode"
    assert AGENT_INFO.url == "https://opencode.ai"


def test_agent_info_slug_matches_agent_type():
    import dataclasses
    fields = {f.name: f for f in dataclasses.fields(OpencodeTaskConfig)}
    assert fields["agent_type"].default == AGENT_INFO.slug
