from optio_kimicode import AGENT_INFO
from optio_kimicode.types import KimiCodeTaskConfig


def test_agent_info_values():
    assert AGENT_INFO.slug == "kimicode"
    assert AGENT_INFO.name == "Kimi Code"
    assert AGENT_INFO.url == "https://www.kimi.com/coding"


def test_agent_info_slug_matches_agent_type():
    import dataclasses

    fields = {f.name: f for f in dataclasses.fields(KimiCodeTaskConfig)}
    assert fields["agent_type"].default == AGENT_INFO.slug
