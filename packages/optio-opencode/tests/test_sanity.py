def test_package_imports():
    import optio_opencode  # noqa: F401


def test_tmp_workdir_fixture(tmp_workdir):
    import os
    assert os.path.isdir(tmp_workdir)


def test_hosts_importable():
    from optio_opencode.host import Host, LocalHost, RemoteHost  # noqa


def test_create_opencode_task_declares_resume_support():
    from optio_opencode import create_opencode_task, OpencodeTaskConfig
    task = create_opencode_task(
        process_id="demo", name="Demo",
        config=OpencodeTaskConfig(consumer_instructions="hi"),
    )
    assert task.supports_resume is True


def test_optio_opencode_exports_hook_context_types():
    import optio_opencode
    assert hasattr(optio_opencode, "HookContext")
    assert hasattr(optio_opencode, "HookContextProtocol")
    assert hasattr(optio_opencode, "RunResult")
    assert hasattr(optio_opencode, "HostCommandError")
    assert hasattr(optio_opencode, "HookCallback")


def test_create_opencode_task_supports_resume_off():
    from optio_opencode import create_opencode_task, OpencodeTaskConfig
    task = create_opencode_task(
        process_id="demo-noresume", name="DemoNoResume",
        config=OpencodeTaskConfig(consumer_instructions="hi", supports_resume=False),
    )
    assert task.supports_resume is False
