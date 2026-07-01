import pytest

from optio_grok import GrokTaskConfig


def test_defaults_and_validation():
    c = GrokTaskConfig(consumer_instructions="do it")
    assert c.mode == "iframe" and c.no_leader is True and c.host_protocol is True
    with pytest.raises(ValueError):
        GrokTaskConfig(consumer_instructions="x", permission_mode="nope")
