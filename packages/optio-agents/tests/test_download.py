"""Tests for HookContext.download_file routing (optio-agents owns HookContext)."""

import pytest


class _RoutingFakeCtx:
    """Fake ProcessContext for testing HookContext.download_file routing only."""

    def __init__(self, *, process_id="p"):
        self.process_id = process_id
        self._child_counter = {"next": 0}
        self.run_child_calls = []

    async def run_child_task(self, task, **kw):
        self.run_child_calls.append(
            (task.execute, task.process_id, task.name, task.description)
        )
        self._child_counter["next"] += 1
        return "done"


class _RoutingFakeHost:
    def __init__(self, *, workdir="/wd", host_home="/home/u"):
        self.workdir = workdir
        self._host_home = host_home

    async def resolve_host_home(self):
        return self._host_home


async def test_download_file_routes_through_run_child_with_generated_id_and_name():
    from optio_agents.context import HookContext

    ctx = _RoutingFakeCtx(process_id="root.parent")
    host = _RoutingFakeHost(workdir="/wd")
    h = HookContext(ctx, host)

    await h.download_file(
        "https://example/foo.bin",
        "downloads/foo.bin",
    )

    assert len(ctx.run_child_calls) == 1
    execute, pid, name, description = ctx.run_child_calls[0]
    assert pid == "root.parent.download-0"
    assert name == "download foo.bin"
    assert description is None


async def test_download_file_second_call_increments_counter():
    from optio_agents.context import HookContext

    ctx = _RoutingFakeCtx(process_id="root.parent")
    host = _RoutingFakeHost(workdir="/wd")
    h = HookContext(ctx, host)

    await h.download_file("https://example/a.bin", "a.bin")
    await h.download_file("https://example/b.bin", "b.bin")

    assert ctx.run_child_calls[0][1] == "root.parent.download-0"
    assert ctx.run_child_calls[1][1] == "root.parent.download-1"


async def test_download_file_passes_description_through():
    from optio_agents.context import HookContext

    ctx = _RoutingFakeCtx()
    host = _RoutingFakeHost()
    h = HookContext(ctx, host)

    await h.download_file(
        "https://example/foo.bin", "foo.bin",
        description="grab it",
    )
    assert ctx.run_child_calls[0][3] == "grab it"


async def test_download_file_resolves_workdir_relative_target_to_absolute(monkeypatch):
    """The factory should receive an already-resolved absolute target path."""
    from optio_agents import context as ctx_mod

    captured: dict = {}
    original = ctx_mod.create_download_task

    def spy(*args, **kwargs):
        captured.update(kwargs)
        return original(*args, **kwargs)

    monkeypatch.setattr(ctx_mod, "create_download_task", spy)

    ctx = _RoutingFakeCtx()
    host = _RoutingFakeHost(workdir="/wd")
    h = ctx_mod.HookContext(ctx, host)
    await h.download_file("https://example/foo.bin", "sub/foo.bin")

    assert captured["target"] == "/wd/sub/foo.bin"
    assert captured["url"] == "https://example/foo.bin"
    assert captured["host"] is host


async def test_download_file_rejects_workdir_escape_without_spawning():
    from optio_agents.context import HookContext

    ctx = _RoutingFakeCtx()
    host = _RoutingFakeHost()
    h = HookContext(ctx, host)

    with pytest.raises(ValueError):
        await h.download_file("https://example/foo", "../escape")
    assert ctx.run_child_calls == []


def test_download_file_appears_on_hook_context_protocol():
    from optio_agents.context import HookContextProtocol
    methods = {m for m in dir(HookContextProtocol) if not m.startswith("_")}
    assert "download_file" in methods
