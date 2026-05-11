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
