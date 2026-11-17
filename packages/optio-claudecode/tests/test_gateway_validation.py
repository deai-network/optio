"""Gateway validation: one short real conversation per model, on fake_claude."""

import asyncio
import json
import os

from optio_claudecode import gateway_setup
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
    assert envs["m/pass"]["CLAUDE_CODE_ALWAYS_ENABLE_EFFORT"] == "1"
    assert "CLAUDE_CODE_ALWAYS_ENABLE_EFFORT" not in envs["plain"]


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
