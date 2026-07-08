"""Real-binary, PRE-AUTH launch tests — the layer that actually proves the iframe
surface starts.

These reproduce the exact failure that a green fake suite hid: the wrapper launches
``kimi server run --foreground`` and waits for its ready banner, but

  * the worker had a *different* product on PATH under the same ``kimi`` name (the
    Python ``kimi-cli``, which has no ``server`` command), so the server "exited
    before printing a ready banner"; and
  * ``resolve_real_kimi`` / the Tier-1 install had no identity check, so the whole
    opt-in suite would either exercise the wrong binary or skip while a real
    kimi-code sat installed under a location it didn't probe.

Both are caught here WITHOUT credentials or network — ``kimi server`` starts before
any login (the device-code flow happens later, in the SPA). Opt-in (real binary),
skip-if-absent with a precise reason; run with::

    OPTIO_KIMICODE_REAL_E2E=1 .venv/bin/python -m pytest \
        packages/optio-kimicode/tests/test_real_server_ready.py -v
"""

from __future__ import annotations

import os
import shutil
from pathlib import Path

import pytest

from optio_kimicode import host_actions
from optio_kimicode.types import KimiCodeTaskConfig
import realbin

_FLAG = "OPTIO_KIMICODE_REAL_E2E"
_skip = realbin.real_kimi_skip_reason(_FLAG, need_creds=False)
pytestmark = pytest.mark.skipif(_skip is not None, reason=_skip or "")


async def test_is_kimicode_rejects_the_kimi_cli_name_collision(tmp_path):
    """The identity probe accepts real kimi-code and rejects the Python kimi-cli
    that shares the ``kimi`` command name — the discriminator the install logic
    relies on to never adopt the wrong product."""
    host = host_actions.build_host(None, str(tmp_path))
    real = realbin.resolve_real_kimi()
    assert real is not None
    assert await host_actions._is_kimicode(host, real) is True

    # The vendor installer preserves a pre-existing kimi-cli as ``kimi-legacy``;
    # uv installs it under its tool dir. If either is present, it MUST be rejected.
    legacy_candidates = [
        Path.home() / ".local" / "bin" / "kimi-legacy",
        Path.home() / ".local" / "share" / "uv" / "tools" / "kimi-cli" / "bin" / "kimi",
    ]
    checked_a_legacy = False
    for cand in legacy_candidates:
        if cand.exists() and os.access(cand, os.X_OK):
            checked_a_legacy = True
            assert await host_actions._is_kimicode(host, str(cand)) is False, (
                f"{cand} (kimi-cli) was wrongly accepted as kimi-code"
            )
    if not checked_a_legacy:
        pytest.skip("no kimi-cli present to prove the negative case (positive still asserted)")


async def test_real_kimi_server_reaches_ready(tmp_path):
    """``kimi server run --foreground`` prints its ready banner (host+port, and a
    ``#token=`` when kimi mints one) — the exact signal the iframe surface waits
    for. This is the reproduction of "kimi server exited before ready banner"."""
    host = host_actions.build_host(None, str(tmp_path))
    kimi = realbin.resolve_real_kimi()
    assert kimi is not None
    Path(host.workdir, "home").mkdir(parents=True, exist_ok=True)

    handle, port, token = await host_actions.launch_kimi_web(
        host,
        kimi_path=kimi,
        bind_iface="127.0.0.1",
        extra_env={},
        env_remove=None,
        ready_timeout_s=60.0,
    )
    try:
        assert port > 0
    finally:
        await host.terminate_subprocess(handle, aggressive=True)


async def test_real_kimi_server_reaches_ready_under_claustrum(tmp_path, monkeypatch):
    """The launch reaches ready even wrapped in the PRODUCTION claustrum Landlock
    sandbox (fs-isolation was ON in the failing run — the cursor ``/tmp`` precedent
    made this worth proving). Uses the real ``_build_claustrum_wrap`` grants and the
    real cache-linked launch path, so grant alignment matches production exactly.
    Skips when Landlock cannot enforce on this kernel."""
    claustrum = realbin.claustrum_binary(tmp_path)
    if claustrum is None or not realbin.landlock_available() or not realbin.landlock_enforces(claustrum):
        pytest.skip("claustrum/Landlock cannot enforce here; server-under-sandbox untested")

    real = realbin.resolve_real_kimi()
    assert real is not None
    # Seed an isolated optio cache with the real kimi-code so ensure_kimicode_installed
    # cache-HITs (no network) and the launch runs from the cache-linked path the
    # claustrum grants cover — exactly the production arrangement.
    cache_bin = tmp_path / "cache" / "bin"
    cache_bin.mkdir(parents=True)
    shutil.copy2(real, cache_bin / "kimi")
    (cache_bin / "kimi").chmod(0o755)
    monkeypatch.setenv("OPTIO_KIMICODE_CACHE_DIR", str(cache_bin))

    host = host_actions.build_host(None, str(tmp_path))
    Path(host.workdir, "home").mkdir(parents=True, exist_ok=True)
    kimi = await host_actions.ensure_kimicode_installed(host)

    # fs_isolation defaults on; delivery_type is then mandatory (routes the
    # claustrum-update notice) — supply it so the config builds.
    config = KimiCodeTaskConfig(consumer_instructions="", delivery_type="audit")
    wrap = await host_actions._build_claustrum_wrap(host, config, claustrum)
    assert wrap is not None

    handle, port, token = await host_actions.launch_kimi_web(
        host,
        kimi_path=kimi,
        bind_iface="127.0.0.1",
        extra_env={},
        env_remove=None,
        ready_timeout_s=60.0,
        claustrum_wrap=wrap,
    )
    try:
        assert port > 0
    finally:
        await host.terminate_subprocess(handle, aggressive=True)
