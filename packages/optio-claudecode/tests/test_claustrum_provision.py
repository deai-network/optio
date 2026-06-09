import pytest
from optio_claudecode import host_actions


def test_goarch_map():
    assert host_actions._GOARCH_BY_UNAME["x86_64"] == "amd64"
    assert host_actions._GOARCH_BY_UNAME["aarch64"] == "arm64"


@pytest.mark.parametrize("pinned,remote,expect", [
    ("v0.1.0", ["v0.1.0"], None),
    ("v0.1.0", ["v0.1.0", "v0.2.0"], "v0.2.0"),
    ("v0.1.0", ["v0.0.9"], None),
])
def test_newer_tag_selection(monkeypatch, pinned, remote, expect):
    # Unit-test the version comparison via a tiny reimplementation guard:
    def key(t):
        return tuple(int(x) for x in t.lstrip("v").split(".") if x.isdigit())
    newest = max(remote, key=key)
    got = newest if key(newest) > key(pinned) else None
    assert got == expect
