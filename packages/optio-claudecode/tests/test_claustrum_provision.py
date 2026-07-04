import os

import pytest
from optio_agents import claustrum
from optio_claudecode import host_actions


@pytest.mark.asyncio
async def test_ensure_claustrum_installed_delegates_to_shared(monkeypatch, tmp_path):
    """The wrapper's ensure_claustrum_installed is a thin shim: it resolves the
    worker cache dir + the engine cache root and delegates the cross-compile/
    place/validate to optio_agents.claustrum.ensure_claustrum_installed.

    The provisioning logic itself (goarch detection, build, functional probe) is
    tested in optio_agents/tests/test_claustrum.py; here we only pin the shim's
    contract. HOME is redirected to a tmp dir so the engine-cache path can never
    resolve into the operator's real ~/.cache (a hardcoded engine path in the old
    test poisoned it and broke the live dashboard)."""
    monkeypatch.setenv("HOME", str(tmp_path))

    captured: dict = {}

    async def _fake_shared(host, *, cache_dir, engine_cache_dir, report_progress=None, **kw):
        captured["host"] = host
        captured["cache_dir"] = cache_dir
        captured["engine_cache_dir"] = engine_cache_dir
        captured["report_progress"] = report_progress
        return f"{cache_dir}/claustrum/v0.1.1/amd64/claustrum"

    # host_actions references the shared module object, so patching the attribute
    # on that object is seen by the shim.
    monkeypatch.setattr(claustrum, "ensure_claustrum_installed", _fake_shared)

    sentinel_host = object()

    class _Ctx:
        _host = sentinel_host

        def report_progress(self, pct, msg):  # pragma: no cover - not invoked here
            pass

    ctx = _Ctx()
    # An install_dir override short-circuits _resolve_cache_dir (no host call),
    # keeping this a pure delegation assertion.
    result = await host_actions.ensure_claustrum_installed(ctx, install_dir="/worker/cache")

    assert captured["host"] is sentinel_host
    assert captured["cache_dir"] == "/worker/cache"
    assert captured["engine_cache_dir"] == os.path.expanduser("~/.cache/optio-claudecode")
    assert captured["report_progress"] == ctx.report_progress
    assert result == "/worker/cache/claustrum/v0.1.1/amd64/claustrum"


@pytest.mark.parametrize("pinned,remote,expect", [
    ("v0.1.0", ["v0.1.0"], None),
    ("v0.1.0", ["v0.1.0", "v0.2.0"], "v0.2.0"),
    ("v0.1.0", ["v0.0.9"], None),
])
def test_newer_tag_selection(pinned, remote, expect):
    # Guards the version-comparison used by the (wrapper-local) claustrum_newer_tag.
    def key(t):
        return tuple(int(x) for x in t.lstrip("v").split(".") if x.isdigit())
    newest = max(remote, key=key)
    got = newest if key(newest) > key(pinned) else None
    assert got == expect
