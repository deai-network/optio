import dataclasses
import pytest
from optio_agents import AgentInfo


def test_agent_info_fields_and_frozen():
    info = AgentInfo(slug="x", name="X Name", url="https://x.example")
    assert info.slug == "x"
    assert info.name == "X Name"
    assert info.url == "https://x.example"
    with pytest.raises(dataclasses.FrozenInstanceError):
        info.name = "changed"  # type: ignore[misc]
