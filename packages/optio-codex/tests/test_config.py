import pytest

from optio_codex import CodexTaskConfig


def test_defaults_and_validation():
    c = CodexTaskConfig(consumer_instructions="do it")
    assert c.mode == "iframe" and c.host_protocol is True and c.auto_start is True
    assert c.ask_for_approval == "never" and c.sandbox == "workspace-write"
    with pytest.raises(ValueError):
        CodexTaskConfig(consumer_instructions="x", codex_install_dir="relative/path")
    with pytest.raises(ValueError):
        CodexTaskConfig(consumer_instructions="x", ask_for_approval="bogus")


@pytest.mark.asyncio
async def test_ssh_config_rejected_at_stage0():
    from optio_codex import SSHConfig
    from optio_codex.session import run_codex_session

    config = CodexTaskConfig(
        consumer_instructions="x",
        ssh=SSHConfig(host="worker.example", user="u", key_path="/k"),
    )
    with pytest.raises(NotImplementedError, match="remote"):
        await run_codex_session(None, config)