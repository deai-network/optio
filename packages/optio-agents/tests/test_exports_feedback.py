def test_root_exports():
    from optio_agents import (  # noqa: F401
        AgentSender,
        RESUME_NOTICE,
        SYSTEM_MESSAGE_PREFIX,
    )

    assert SYSTEM_MESSAGE_PREFIX == "System: "
    assert RESUME_NOTICE == "you have been resumed"


def test_protocol_exports():
    from optio_agents.protocol import AgentSender, RESUME_NOTICE  # noqa: F401
