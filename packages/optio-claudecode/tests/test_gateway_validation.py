"""Gateway validation: one short real conversation per model, on fake_claude."""

import asyncio
import json
import logging
import os
from dataclasses import replace

import pytest

from optio_claudecode import gateway_setup
from optio_claudecode.conversation import ClaudeCodeConversation
from optio_claudecode.gateway import GatewayModel, GatewaySeed
from optio_claudecode.gateway_setup import validate_gateway_models

TOKEN = "rq-validate-token-5678wxyz"
ALIASES = ("OPUS", "SONNET", "HAIKU", "FABLE")
# How a turn goes on each model (fake_claude's FAKE_CLAUDE_GATEWAY_MODELS).
BEHAVIOURS = {
    "m/pass": "pass",
    "m/wrong": "wrong",
    "m/error": "error",
    "m/error-with-token": "error-with-token",
    "m/hang": "hang",
    "m/exit": "exit",
    "plain": "pass",
}
# A seed being set up: every model untested, so none is usable yet. "plain"
# publishes no effort levels.
SEED = GatewaySeed(
    provider="requesty",
    region="eu",
    base_url="https://router.eu.requesty.ai",
    token=TOKEN,
    models=tuple(
        GatewayModel(mid, mid, "managed", None) if mid == "plain"
        else GatewayModel(mid, mid, "pinned", ("low", "high"))
        for mid in BEHAVIOURS
    ),
    default_model="m/pass",
)


def _fake(monkeypatch) -> None:
    monkeypatch.setenv("FAKE_CLAUDE_GATEWAY_MODELS", json.dumps(BEHAVIOURS))


def _flag(argv: list[str], name: str) -> str:
    return argv[argv.index(name) + 1]


async def test_each_model_passes_or_fails_with_its_reason(task_root, claude_cache_dir, monkeypatch):
    _fake(monkeypatch)
    assert SEED.usable_models() == ()
    out = await validate_gateway_models(SEED, ["m/pass", "m/wrong", "m/error"],
                                        install_dir=str(claude_cache_dir), per_model_timeout=60)
    assert out["m/pass"] == (True, None)
    assert out["m/wrong"] == (False, "did not answer with the word (answered 'I could not read it')")
    assert out["m/error"] == (False, "API Error: 404 model not available")
    assert os.listdir(task_root) == []


async def test_a_model_that_never_answers_times_out(task_root, claude_cache_dir, monkeypatch):
    _fake(monkeypatch)
    out = await validate_gateway_models(SEED, ["m/hang"],
                                        install_dir=str(claude_cache_dir), per_model_timeout=0.5)
    assert out == {"m/hang": (False, "no answer within 0.5 s")}
    assert os.listdir(task_root) == []


async def test_a_claude_that_exits_fails_its_model(task_root, claude_cache_dir, monkeypatch):
    _fake(monkeypatch)
    out = await validate_gateway_models(SEED, ["m/exit"],
                                        install_dir=str(claude_cache_dir), per_model_timeout=60)
    assert out == {"m/exit": (False, "claude exited (exit 3) before answering")}
    assert os.listdir(task_root) == []


async def test_the_token_never_reaches_a_reason(task_root, claude_cache_dir, monkeypatch):
    # The fake appends the ANTHROPIC_AUTH_TOKEN it got to its error.
    _fake(monkeypatch)
    out = await validate_gateway_models(SEED, ["m/error-with-token"],
                                        install_dir=str(claude_cache_dir), per_model_timeout=60)
    ok, reason = out["m/error-with-token"]
    assert ok is False and TOKEN not in reason
    assert reason == "API Error: 404 model not available ***"


async def test_each_check_runs_its_model_alone_through_the_gateway(
        task_root, claude_cache_dir, monkeypatch, tmp_path):
    _fake(monkeypatch)
    argv_log, env_log = tmp_path / "argv.jsonl", tmp_path / "env.jsonl"
    monkeypatch.setenv("FAKE_CLAUDE_ARGV_LOG", str(argv_log))
    monkeypatch.setenv("FAKE_CLAUDE_ENV_LOG", str(env_log))
    monkeypatch.setenv("ANTHROPIC_API_KEY", "inherited-key")
    out = await validate_gateway_models(SEED, ["m/pass", "plain"],
                                        install_dir=str(claude_cache_dir), per_model_timeout=60)
    assert out == {"m/pass": (True, None), "plain": (True, None)}
    argvs = {_flag(a, "--model"): a for a in map(json.loads, argv_log.read_text().splitlines())}
    assert sorted(argvs) == ["m/pass", "plain"]
    for argv in argvs.values():
        assert _flag(argv, "--permission-mode") == "dontAsk"
        assert _flag(argv, "--tools") == "Read" and _flag(argv, "--allowed-tools") == "Read"
        assert TOKEN not in " ".join(argv)
    # The model's preselected level; none for a model without levels.
    assert _flag(argvs["m/pass"], "--effort") == "high"
    assert "--effort" not in argvs["plain"]
    # Each check runs its model alone: the aliases point at it too.
    envs = {e["ANTHROPIC_DEFAULT_HAIKU_MODEL"]: e for e in map(json.loads, env_log.read_text().splitlines())}
    assert sorted(envs) == ["m/pass", "plain"]
    for model, env in envs.items():
        assert {env[f"ANTHROPIC_DEFAULT_{alias}_MODEL"] for alias in ALIASES} == {model}
        assert env["ANTHROPIC_AUTH_TOKEN"] == TOKEN
        assert env["ANTHROPIC_BASE_URL"] == SEED.base_url
        assert "ANTHROPIC_API_KEY" not in env
        assert "CLAUDE_CODE_EFFORT_LEVEL" not in env
    # As a session on this seed: the flag is process-wide, and "plain"'s
    # siblings have levels, so "plain" is checked under it too.
    assert envs["m/pass"]["CLAUDE_CODE_ALWAYS_ENABLE_EFFORT"] == "1"
    assert envs["plain"]["CLAUDE_CODE_ALWAYS_ENABLE_EFFORT"] == "1"


async def test_a_model_without_levels_is_checked_under_the_effort_flag_its_seed_sets(
        task_root, claude_cache_dir, monkeypatch, tmp_path):
    """Important 1: a session on a seed where some model has levels runs with
    CLAUDE_CODE_ALWAYS_ENABLE_EFFORT=1 for every model, so Claude Code sends
    its default effort to a model without levels as well; the check of that
    model runs under the same flag. A seed where no model has levels sets it
    for none."""
    _fake(monkeypatch)
    env_log = tmp_path / "env.jsonl"
    argv_log = tmp_path / "argv.jsonl"
    monkeypatch.setenv("FAKE_CLAUDE_ENV_LOG", str(env_log))
    monkeypatch.setenv("FAKE_CLAUDE_ARGV_LOG", str(argv_log))
    plain = next(m for m in SEED.models if m.id == "plain")
    levelled = next(m for m in SEED.models if m.id == "m/pass")
    mixed = replace(SEED, models=(plain, levelled), default_model="plain")
    plain_only = replace(SEED, models=(plain,), default_model="plain")

    assert await validate_gateway_models(mixed, ["plain"], install_dir=str(claude_cache_dir),
                                         per_model_timeout=60) == {"plain": (True, None)}
    assert await validate_gateway_models(plain_only, ["plain"], install_dir=str(claude_cache_dir),
                                         per_model_timeout=60) == {"plain": (True, None)}

    in_mixed, alone = [json.loads(line) for line in env_log.read_text().splitlines()]
    assert in_mixed["CLAUDE_CODE_ALWAYS_ENABLE_EFFORT"] == "1"
    assert "CLAUDE_CODE_ALWAYS_ENABLE_EFFORT" not in alone
    # The aliases still point at the model under test, and no --effort is
    # sent for it either way.
    assert {in_mixed[f"ANTHROPIC_DEFAULT_{alias}_MODEL"] for alias in ALIASES} == {"plain"}
    for argv in map(json.loads, argv_log.read_text().splitlines()):
        assert "--effort" not in argv


async def test_at_most_concurrency_checks_run_at_once(monkeypatch):
    running = peak = 0

    async def _check(seed, model_id, *, ssh, install_dir, per_model_timeout, install_lock):
        nonlocal running, peak
        running += 1
        peak = max(peak, running)
        for _ in range(5):
            await asyncio.sleep(0)
        running -= 1
        return True, None

    monkeypatch.setattr(gateway_setup, "_check_model", _check)
    ids = [f"m/{i}" for i in range(7)]
    out = await validate_gateway_models(SEED, ids)
    assert peak == 3 and out == {mid: (True, None) for mid in ids}
    peak = 0
    await validate_gateway_models(SEED, ids, concurrency=2)
    assert peak == 2


async def test_a_check_that_raises_fails_only_its_model(task_root, claude_cache_dir, monkeypatch):
    _fake(monkeypatch)
    real_check = gateway_setup._check_model

    async def _check(seed, model_id, **kwargs):
        if model_id == "m/boom":
            raise RuntimeError("boom")
        return await real_check(seed, model_id, **kwargs)

    monkeypatch.setattr(gateway_setup, "_check_model", _check)
    out = await validate_gateway_models(SEED, ["m/pass", "m/boom", "plain"],
                                        install_dir=str(claude_cache_dir), per_model_timeout=60)
    assert out == {
        "m/pass": (True, None),
        "m/boom": (False, "check failed: RuntimeError: boom"),
        "plain": (True, None),
    }
    assert os.listdir(task_root) == []


async def test_report_follows_each_model(task_root, claude_cache_dir, monkeypatch):
    _fake(monkeypatch)
    calls = []
    out = await validate_gateway_models(SEED, ["m/pass", "m/wrong", "plain"],
                                        install_dir=str(claude_cache_dir), per_model_timeout=60,
                                        report=lambda *args: calls.append(args))
    assert [(done, total) for done, total, *_ in calls] == [(1, 3), (2, 3), (3, 3)]
    assert {model: (ok, reason) for _, _, model, ok, reason in calls} == out


async def _passes(seed, model_id, *, ssh, install_dir, per_model_timeout, install_lock):
    return True, None


async def test_a_report_that_raises_leaves_every_outcome(monkeypatch):
    monkeypatch.setattr(gateway_setup, "_check_model", _passes)
    reported = []

    def _report(done, total, model_id, ok, reason):
        reported.append(model_id)
        raise RuntimeError("report broke")

    ids = [f"m/{i}" for i in range(4)]
    out = await validate_gateway_models(SEED, ids, report=_report)
    assert out == {mid: (True, None) for mid in ids}
    assert sorted(reported) == ids


async def test_concurrency_must_be_at_least_one(monkeypatch):
    checked = []

    async def _check(seed, model_id, **kwargs):
        checked.append(model_id)
        return True, None

    monkeypatch.setattr(gateway_setup, "_check_model", _check)
    for bad in (0, -1):
        with pytest.raises(ValueError, match="concurrency must be at least 1"):
            await asyncio.wait_for(validate_gateway_models(SEED, ["m/pass"], concurrency=bad), 60)
    assert checked == []


async def test_a_reader_that_fails_leaves_the_outcome_and_the_cleanup(
        task_root, claude_cache_dir, monkeypatch, caplog):
    # The reader ends with an error, as asyncssh's does when the connection
    # drops, whether it reached the end of claude's output or was stopped.
    _fake(monkeypatch)

    class _LosesTheConnection(ClaudeCodeConversation):
        async def run_reader(self):
            try:
                await super().run_reader()
            except asyncio.CancelledError:
                pass
            raise ConnectionResetError("connection lost")

    monkeypatch.setattr(gateway_setup, "ClaudeCodeConversation", _LosesTheConnection)
    with caplog.at_level(logging.WARNING, logger=gateway_setup.__name__):
        out = await validate_gateway_models(SEED, ["m/pass"],
                                            install_dir=str(claude_cache_dir), per_model_timeout=60)
    assert out == {"m/pass": (True, None)}
    assert os.listdir(task_root) == []
    assert any("stopping the reader failed" in r.getMessage() for r in caplog.records)


async def test_the_cleanup_runs_every_step_through_a_failure_and_a_cancellation():
    ran = []
    blocked = asyncio.Event()

    async def _hangs():
        ran.append("hangs")
        blocked.set()
        await asyncio.Event().wait()

    async def _fails():
        ran.append("fails")
        raise OSError("gone")

    async def _last():
        ran.append("last")

    cleanup = asyncio.create_task(
        gateway_setup._cleanup([("hangs", _hangs), ("fails", _fails), ("last", _last)]),
    )
    await asyncio.wait_for(blocked.wait(), 60)
    cleanup.cancel()
    await asyncio.wait({cleanup}, timeout=60)
    assert ran == ["hangs", "fails", "last"]
    # The cancellation is not lost: it ends the cleanup once every step ran.
    assert cleanup.cancelled()
