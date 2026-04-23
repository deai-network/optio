def test_package_imports():
    import optio_opencode  # noqa: F401


def test_tmp_workdir_fixture(tmp_workdir):
    import os
    assert os.path.isdir(tmp_workdir)


def test_hosts_importable():
    from optio_opencode.host import Host, LocalHost, RemoteHost  # noqa
