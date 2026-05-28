"""Public-API surface tests."""

import optio_claudecode


def test_top_level_exports_factory_and_config():
    assert hasattr(optio_claudecode, "create_claudecode_task")
    assert hasattr(optio_claudecode, "ClaudeCodeTaskConfig")
    assert hasattr(optio_claudecode, "run_claudecode_session")


def test_re_exports_from_optio_host():
    assert hasattr(optio_claudecode, "SSHConfig")
    assert hasattr(optio_claudecode, "HookContext")
    assert hasattr(optio_claudecode, "HookContextProtocol")
    assert hasattr(optio_claudecode, "HostCommandError")
    assert hasattr(optio_claudecode, "RunResult")


def test_re_exports_callable_types():
    assert hasattr(optio_claudecode, "HookCallback")
    assert hasattr(optio_claudecode, "DeliverableCallback")
