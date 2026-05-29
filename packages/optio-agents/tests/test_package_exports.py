"""optio_agents top-level package exports the coordination surface."""


def test_top_level_exports_hook_context():
    import optio_agents
    assert hasattr(optio_agents, "HookContext")
    assert hasattr(optio_agents, "HookContextProtocol")


def test_top_level_exports_protocol_surface():
    import optio_agents
    for name in (
        "run_log_protocol_session",
        "fetch_deliverable_text",
        "DeliverableCallback",
        "HookCallback",
        "parse_log_line",
    ):
        assert hasattr(optio_agents, name), name
