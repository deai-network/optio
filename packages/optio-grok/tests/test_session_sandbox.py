"""Session-level wiring test for Stage 8 filesystem isolation.

Runs a local iframe task with the default ``fs_isolation=True`` and asserts:

* ``<workdir>/home/.grok/sandbox.toml`` was planted with ``[profiles.optio]``
  (``extends="strict"``, workdir in ``read_write``); and
* the fake grok was launched with ``--sandbox optio`` in its argv.

The workdir is wiped on teardown, so the fake grok records both facts to a
durable path (``FAKE_GROK_RECORD``) that outlives the task.
"""

from __future__ import annotations

import json
import pathlib

import pytest

from optio_grok import AllowedDir, GrokTaskConfig, create_grok_task


def _read_record(path: pathlib.Path) -> dict:
    lines = [l for l in path.read_text().splitlines() if l.strip()]
    assert lines, f"fake grok wrote no launch record to {path}"
    return json.loads(lines[-1])


@pytest.mark.asyncio
async def test_iframe_sandbox_wired_and_profile_planted(
    shim_install_dir: pathlib.Path,
    ctx_and_captures,
    tmp_path: pathlib.Path,
    monkeypatch,
):
    ctx, *_ = ctx_and_captures
    monkeypatch.setenv("FAKE_GROK_SCENARIO", "happy")
    record = tmp_path / "grok_record.jsonl"
    monkeypatch.setenv("FAKE_GROK_RECORD", str(record))

    task = create_grok_task(
        process_id="grok-sandbox-iframe",
        name="s",
        config=GrokTaskConfig(
            consumer_instructions="do the thing",
            grok_install_dir=str(shim_install_dir),
            ttyd_install_dir=str(shim_install_dir),
            extra_allowed_dirs=[AllowedDir("/scratch", "rw")],
        ),
    )
    await task.execute(ctx)

    rec = _read_record(record)
    # The launch carried the fail-closed custom sandbox profile.
    assert "--sandbox" in rec["argv"]
    assert rec["argv"][rec["argv"].index("--sandbox") + 1] == "optio"
    # The custom profile was planted under the per-task GROK_HOME.
    toml_text = rec["sandbox_toml"]
    assert toml_text is not None, "sandbox.toml was not planted before launch"
    assert "[profiles.optio]" in toml_text
    assert 'extends = "strict"' in toml_text
    assert "/scratch" in toml_text


@pytest.mark.asyncio
async def test_iframe_no_sandbox_when_disabled(
    shim_install_dir: pathlib.Path,
    ctx_and_captures,
    tmp_path: pathlib.Path,
    monkeypatch,
):
    ctx, *_ = ctx_and_captures
    monkeypatch.setenv("FAKE_GROK_SCENARIO", "happy")
    record = tmp_path / "grok_record.jsonl"
    monkeypatch.setenv("FAKE_GROK_RECORD", str(record))

    task = create_grok_task(
        process_id="grok-sandbox-off",
        name="s",
        config=GrokTaskConfig(
            consumer_instructions="do the thing",
            grok_install_dir=str(shim_install_dir),
            ttyd_install_dir=str(shim_install_dir),
            fs_isolation=False,
        ),
    )
    await task.execute(ctx)

    rec = _read_record(record)
    assert "--sandbox" not in rec["argv"]
    assert rec["sandbox_toml"] is None
