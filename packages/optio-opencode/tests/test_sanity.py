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
