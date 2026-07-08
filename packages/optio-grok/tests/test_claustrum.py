"""Task 8 — grok replaces its native ``--sandbox`` profile with claustrum.

Unit assertions for the claustrum wiring:

  (a) ``session._build_claustrum_wrap`` returns the shared claustrum argv shape
      with a ``--rwx <workdir>`` and ``--rox <grok cache>`` grant.
  (b) ``build_conversation_argv`` no longer emits ``--sandbox`` or the
      controlling-tty wrapper (claustrum does not open ``/dev/tty``).
  (c) ``GrokTaskConfig(fs_isolation=True)`` requires ``delivery_type`` (the
      shared ``ClaustrumConfigMixin`` validation), and opting out with
      ``fs_isolation=False`` needs no ``delivery_type``.
"""

from __future__ import annotations

import inspect
import pathlib

import pytest

from optio_grok import GrokTaskConfig, create_grok_task
from optio_grok import host_actions
from optio_grok import session as grok_session
from optio_grok.host_actions import build_conversation_argv


class _FakeHost:
    """Minimal host: ``_build_claustrum_wrap`` only reads ``.workdir`` when the
    cache dir is supplied via ``install_dir`` (override) and there are no
    ``extra_allowed_dirs`` (so ``resolve_host_home`` is never called)."""

    def __init__(self, workdir: str = "/wd") -> None:
        self.workdir = workdir


@pytest.mark.asyncio
async def test_build_claustrum_wrap_shape():
    host = _FakeHost(workdir="/wd")
    # install_dir is the override -> _resolve_grok_cache_dir returns it directly
    # (no run_command), so the fake host needs only .workdir.
    config = GrokTaskConfig(
        consumer_instructions="x",
        install_dir="/opt/grok/cache",
        delivery_type="audit",
    )
    wrap = await grok_session._build_claustrum_wrap(host, config, "/c/claustrum")
    assert wrap[:4] == ["/c/claustrum", "--best-effort", "--abi-min", "1"]
    assert wrap[-1] == "--"
    # workdir rwx grant + grok-cache-ROOT rox grant, trailing (just before `--`).
    # The rox grant is the PARENT of the resolved cache/bin dir (/opt/grok/cache
    # -> /opt/grok): grok's real ELF is a symlink target in the sibling
    # <root>/.grok/downloads, outside the bin dir, so bin alone gets exec-denied.
    assert wrap[-5:] == ["--rwx", "/wd", "--rox", "/opt/grok", "--"]
    # system baseline present.
    assert "--rox" in wrap and "/usr" in wrap


@pytest.mark.asyncio
async def test_build_claustrum_wrap_none_when_isolation_off():
    host = _FakeHost()
    config = GrokTaskConfig(consumer_instructions="x", fs_isolation=False)
    assert await grok_session._build_claustrum_wrap(host, config, None) is None


def test_conversation_argv_has_no_native_sandbox_or_ctty():
    """The native ``--sandbox`` coupling and the controlling-tty wrapper are
    gone: claustrum confines the whole process tree from the outside, so
    ``build_conversation_argv`` no longer takes ``fs_isolation`` and never emits
    ``--sandbox`` nor the ``TIOCSCTTY`` python helper."""
    assert "fs_isolation" not in inspect.signature(build_conversation_argv).parameters
    argv = build_conversation_argv("/x/grok")
    assert "--sandbox" not in argv
    assert argv[0] == "/x/grok"
    assert "TIOCSCTTY" not in " ".join(argv)


def test_fs_isolation_on_requires_delivery_type():
    with pytest.raises(ValueError, match="delivery_type"):
        GrokTaskConfig(consumer_instructions="x", fs_isolation=True)
    c = GrokTaskConfig(
        consumer_instructions="x", fs_isolation=True, delivery_type="audit",
    )
    assert c.delivery_type == "audit"
    off = GrokTaskConfig(consumer_instructions="x", fs_isolation=False)
    assert off.delivery_type is None


# --- session-flow wiring (default-on, fake claustrum shim) -------------------
#
# Spawns grok in a real tmux/ttyd session under the claustrum shim; fixed
# session name + shared workdir state make it unsafe under concurrency.


@pytest.mark.serial
@pytest.mark.asyncio
async def test_iframe_default_on_wraps_grok_launch(
    shim_install_dir: pathlib.Path,
    ctx_and_captures,
    monkeypatch,
    tmp_path,
):
    """Default-on iframe launch: grok runs UNDER claustrum — the grants + the
    ``--`` separator + the wrapped grok reach the sandbox layer. Proven via the
    durable claustrum-shim record (the workdir is wiped on teardown)."""
    ctx, *_ = ctx_and_captures
    monkeypatch.setenv("FAKE_GROK_SCENARIO", "happy")
    record = tmp_path / "claustrum-launch.log"
    monkeypatch.setenv("FAKE_CLAUSTRUM_RECORD", str(record))

    task = create_grok_task(
        process_id="grok-fs-iframe",
        name="fs",
        config=GrokTaskConfig(
            consumer_instructions="do it",
            install_dir=str(shim_install_dir),
            ttyd_install_dir=str(shim_install_dir),
            delivery_type="audit",
            # fs_isolation defaults True — no opt-in.
        ),
    )
    await task.execute(ctx)

    assert record.exists(), "claustrum was not invoked (fs_isolation not wired)"
    line = record.read_text(encoding="utf-8")
    # claustrum flags + a workdir rwx grant + a baseline system grant.
    assert "--best-effort" in line and "--abi-min 1" in line
    assert "--rwx" in line and "--rox /usr" in line
    # The `--` separator, then the wrapped grok.
    assert " -- " in line
    tail = line[line.index(" -- ") + 4:]
    assert "grok" in tail


@pytest.mark.serial
@pytest.mark.asyncio
async def test_fs_isolation_is_fail_closed(
    shim_install_dir: pathlib.Path,
    ctx_and_captures,
    monkeypatch,
):
    """Fail-closed (non-negotiable): when fs_isolation is on and claustrum
    cannot be provisioned, the task refuses to launch — never an unconfined
    run."""
    ctx, *_ = ctx_and_captures
    monkeypatch.setenv("FAKE_GROK_SCENARIO", "happy")

    async def _boom(hook_ctx, *, install_dir=None):
        raise RuntimeError("claustrum unavailable: kernel lacks Landlock")

    monkeypatch.setattr(host_actions, "ensure_claustrum_installed", _boom)

    task = create_grok_task(
        process_id="grok-fs-failclosed",
        name="fc",
        config=GrokTaskConfig(
            consumer_instructions="do it",
            install_dir=str(shim_install_dir),
            ttyd_install_dir=str(shim_install_dir),
            delivery_type="audit",
        ),
    )
    with pytest.raises(RuntimeError, match="Landlock"):
        await task.execute(ctx)
