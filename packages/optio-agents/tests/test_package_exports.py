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


def test_top_level_exports_protocol_variation():
    import optio_agents
    for name in ("get_protocol", "Protocol", "BrowserMode",
                 "build_log_channel_prompt", "browser_shims", "seeds"):
        assert hasattr(optio_agents, name), name


def test_top_level_exports_deliverable_metadata():
    import optio_agents
    for name in ("meta_get", "clamp", "agent_entries", "DeliverableMetadata",
                 "KEY_MODEL", "SOURCE_AGENT", "ORIGIN_HARNESS", "invoke_on_deliverable"):
        assert hasattr(optio_agents, name), name


def test_deliverable_callback_takes_three_or_four_arguments():
    import typing
    import optio_agents
    # get_args of a plain Callable is also 2 long ([params], return): check the Union first.
    assert typing.get_origin(optio_agents.DeliverableCallback) is typing.Union
    forms = typing.get_args(optio_agents.DeliverableCallback)
    assert len(forms) == 2
    assert sorted(len(typing.get_args(form)[0]) for form in forms) == [3, 4]
