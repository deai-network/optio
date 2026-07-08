from optio_codex import AGENT_INFO
from optio_codex.types import CodexTaskConfig


def test_agent_info_values():
    assert AGENT_INFO.slug == "codex"
    assert AGENT_INFO.name == "Codex"
    assert AGENT_INFO.url == "https://openai.com/codex"


def test_agent_info_slug_matches_agent_type():
    import dataclasses

    fields = {f.name: f for f in dataclasses.fields(CodexTaskConfig)}
    assert fields["agent_type"].default == AGENT_INFO.slug
