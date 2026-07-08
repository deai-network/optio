from optio_cursor import AGENT_INFO
from optio_cursor.types import CursorTaskConfig


def test_agent_info_values():
    assert AGENT_INFO.slug == "cursor"
    assert AGENT_INFO.name == "Cursor CLI"
    assert AGENT_INFO.url == "https://cursor.com/cli"


def test_agent_info_slug_matches_agent_type():
    import dataclasses

    fields = {f.name: f for f in dataclasses.fields(CursorTaskConfig)}
    assert fields["agent_type"].default == AGENT_INFO.slug
