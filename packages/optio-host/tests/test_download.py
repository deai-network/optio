"""Tests for optio_host.download — the URL → file task factory and HookContext.download_file."""


def test_download_failed_fields_and_str():
    from optio_host.download import DownloadFailed
    err = DownloadFailed(
        url="https://example/foo.bin",
        target="/tmp/foo.bin",
        exit_code=22,
        stderr_tail="curl: (22) The requested URL returned error: 404\n",
    )
    assert err.url == "https://example/foo.bin"
    assert err.target == "/tmp/foo.bin"
    assert err.exit_code == 22
    assert "curl" in err.stderr_tail
    s = str(err)
    assert "https://example/foo.bin" in s
    assert "22" in s
    assert "404" in s


def test_create_download_task_returns_taskinstance_with_fields():
    from optio_core.models import TaskInstance
    from optio_host.download import create_download_task

    t = create_download_task(
        process_id="p.download-0",
        name="download foo.bin",
        url="https://example.com/foo.bin",
        target="/tmp/foo.bin",
        host=None,
        description="grab the binary",
    )

    assert isinstance(t, TaskInstance)
    assert t.process_id == "p.download-0"
    assert t.name == "download foo.bin"
    assert t.description == "grab the binary"
    assert t.cancellable is True
    assert t.supports_resume is False
    assert t.auto_cancel_children is True
    assert t.ui_widget is None
    assert callable(t.execute)


def test_create_download_task_defaults_description():
    from optio_host.download import create_download_task

    t = create_download_task(
        process_id="p.download-0",
        name="download foo.bin",
        url="https://example.com/foo.bin",
        target="/tmp/foo.bin",
    )
    assert t.description is None
