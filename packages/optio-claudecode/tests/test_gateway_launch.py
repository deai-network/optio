"""Gateway launch values from the seed; secrets reach the child over stdin;
the settings files the agent can write cannot move the gateway."""

import asyncio
import json
import os
import pathlib
import shlex
from dataclasses import replace

import pytest

from optio_claudecode.gateway import (
    GATEWAY_ENV_REMOVE,
    GATEWAY_SECRET_ENV,
    GatewayError,
    GatewayModel,
    GatewaySeed,
    gateway_catalog,
    gateway_launch_env,
    gateway_settings_json,
    launch_effort,
    launch_model,
)
from optio_claudecode.host_actions import (
    AGENT_SETTINGS_FILES,
    build_claude_flags,
    strip_settings_env,
    with_stdin_secrets,
    write_stdin_secrets,
)
from optio_claudecode.types import _gateway_owned_keys
from optio_host import LocalHost, ProcessHandle, RemoteHost, SSHConfig
from optio_host.host import proc_wait

KIMI = GatewayModel("prov-a/kimi-k3.1-pro", "prov-a/kimi-k3.1-pro", "pinned", ("low", "high"), status="ok")
GLM = GatewayModel("glm-flash", "glm-flash", "managed", None, status="ok")
BROKEN = GatewayModel("prov-b/broken", "prov-b/broken", "pinned", ("high",), status="failed", reason="no answer")
SEED = GatewaySeed(
    provider="requesty",
    region="eu",
    base_url="https://router.eu.requesty.ai",
    token="rq-test-0123456789abcd",
    models=(KIMI, GLM, BROKEN),
    default_model="prov-a/kimi-k3.1-pro",
)
# The default model failed its last test; glm-flash is the first usable one.
DEFAULT_FAILED = replace(SEED, models=(replace(KIMI, status="failed"), GLM, BROKEN))
NONE_USABLE = replace(SEED, models=(replace(KIMI, status="failed"), replace(GLM, status="failed"), BROKEN))


def test_the_catalog_is_the_usable_models():
    assert gateway_catalog(SEED) == [
        {"id": "prov-a/kimi-k3.1-pro", "label": "prov-a/kimi-k3.1-pro", "effort": ["low", "high"]},
        {"id": "glm-flash", "label": "glm-flash"},
    ]


def test_the_launch_env():
    env = gateway_launch_env(SEED)
    assert env == {
        "ANTHROPIC_BASE_URL": "https://router.eu.requesty.ai",
        "ANTHROPIC_DEFAULT_OPUS_MODEL": "prov-a/kimi-k3.1-pro",
        "ANTHROPIC_DEFAULT_SONNET_MODEL": "prov-a/kimi-k3.1-pro",
        "ANTHROPIC_DEFAULT_HAIKU_MODEL": "prov-a/kimi-k3.1-pro",
        "ANTHROPIC_DEFAULT_FABLE_MODEL": "prov-a/kimi-k3.1-pro",
        "CLAUDE_CODE_AUTO_MODE_SERVER": "0",
        "CLAUDE_CODE_SUBPROCESS_ENV_SCRUB": "1",
        "CLAUDE_CODE_ALWAYS_ENABLE_EFFORT": "1",
    }
    assert all(SEED.token not in value for value in env.values())
    # No levels anywhere: no key. Nor when only a failed model has levels.
    no_levels = replace(SEED, models=(replace(KIMI, efforts=None), GLM, replace(BROKEN, efforts=None)))
    assert "CLAUDE_CODE_ALWAYS_ENABLE_EFFORT" not in gateway_launch_env(no_levels)
    only_failed_levels = replace(SEED, models=(replace(KIMI, efforts=None), GLM, BROKEN))
    assert "CLAUDE_CODE_ALWAYS_ENABLE_EFFORT" not in gateway_launch_env(only_failed_levels)
    # The aliases point at the effective default model.
    fallback = gateway_launch_env(DEFAULT_FAILED)
    assert {fallback[f"ANTHROPIC_DEFAULT_{a}_MODEL"] for a in ("OPUS", "SONNET", "HAIKU", "FABLE")} == {"glm-flash"}
    with pytest.raises(GatewayError):
        gateway_launch_env(NONE_USABLE)


@pytest.mark.parametrize(
    ("seed", "preferred", "expected"),
    [
        (SEED, "glm-flash", "glm-flash"),
        (SEED, "prov-b/broken", "prov-a/kimi-k3.1-pro"),
        (SEED, "default", "prov-a/kimi-k3.1-pro"),
        (DEFAULT_FAILED, None, "glm-flash"),
    ],
)
def test_launch_model(seed, preferred, expected):
    assert launch_model(seed, preferred) == expected


def test_launch_model_with_nothing_usable():
    with pytest.raises(GatewayError):
        launch_model(NONE_USABLE, "glm-flash")


def test_launch_effort():
    kimi, glm = KIMI.id, GLM.id
    assert launch_effort(SEED, kimi, "low") == "low"
    assert launch_effort(SEED, kimi, "xhigh") == "high"
    assert launch_effort(SEED, kimi, None) == "high"
    assert launch_effort(SEED, glm, "high") is None
    # No "high" among the levels: the highest one.
    low_medium = replace(SEED, models=(replace(KIMI, efforts=("low", "medium")), GLM))
    assert launch_effort(low_medium, kimi, None) == "medium"


def test_with_stdin_secrets():
    assert with_stdin_secrets("claude -p", ["ANTHROPIC_AUTH_TOKEN"]) == (
        "IFS= read -r ANTHROPIC_AUTH_TOKEN && export ANTHROPIC_AUTH_TOKEN && exec claude -p"
    )
    for name in ("A B", "A\n", "1A"):
        with pytest.raises(ValueError):
            with_stdin_secrets("claude -p", [name])
    # A bare str would be read as one name per character.
    with pytest.raises(TypeError):
        with_stdin_secrets("claude -p", "ANTHROPIC_AUTH_TOKEN")


# Prints its SECRET_X, its own command line, then the next line on stdin. One
# line of code, so the printed command line is one line too.
_CHILD = "; ".join([
    "import os, sys",
    "print(os.environ.get('SECRET_X'))",
    "print(open('/proc/self/cmdline', 'rb').read().replace(bytes(1), b' ').decode())",
    "print(sys.stdin.readline().rstrip())",
])


async def _all_output(handle) -> bytes:
    return b"".join([chunk async for chunk in handle.stdout])


async def test_a_local_launch_gets_the_secret_in_its_env_only(tmp_path):
    host = LocalHost(taskdir=str(tmp_path))
    os.makedirs(host.workdir, exist_ok=True)
    command = with_stdin_secrets(shlex.join(["python3", "-c", _CHILD]), ["SECRET_X"])
    handle = await host.launch_subprocess(command, stdin=True)
    try:
        await write_stdin_secrets(handle, ["s3cret"], names=["SECRET_X"])
        handle.stdin.write(b"after\n")
        await handle.stdin.drain()
        handle.stdin.close()
        out = await asyncio.wait_for(_all_output(handle), 60)
        assert await asyncio.wait_for(proc_wait(handle), 60) == 0
    finally:
        if handle.pid_like.returncode is None:
            await host.terminate_subprocess(handle, aggressive=True)
    env_line, cmdline, stdin_line = out.decode().splitlines()
    assert env_line == "s3cret"
    assert "python3" in cmdline and "s3cret" not in cmdline
    assert stdin_line == "after"


class _FakeWriter:
    def __init__(self):
        self.writes: list[bytes] = []

    def write(self, data) -> None:
        self.writes.append(bytes(data))

    async def drain(self) -> None:
        pass


async def _nothing():
    return
    yield


class _FakeProcess:
    def __init__(self):
        self.stdin = _FakeWriter()
        self.stdout = _nothing()
        self.stderr = _nothing()


class _FakeConnection:
    """Records the command line sshd would run."""

    def __init__(self):
        self.commands: list[str] = []
        self.process = _FakeProcess()

    async def create_process(self, full, encoding=None):
        assert encoding is None
        self.commands.append(full)
        return self.process


async def test_a_remote_launch_carries_no_secret_on_its_command_line():
    host = RemoteHost(SSHConfig(host="h", user="u", key_path="/k"), "/tmp/x")
    conn = _FakeConnection()
    host._conn = conn
    handle = await host.launch_subprocess(
        with_stdin_secrets("claude -p", GATEWAY_SECRET_ENV),
        env=gateway_launch_env(SEED),
        env_remove=list(GATEWAY_ENV_REMOVE),
        stdin=True,
    )
    await write_stdin_secrets(handle, [SEED.token], names=GATEWAY_SECRET_ENV)
    [full] = conn.commands
    assert "rq-" not in full and SEED.token not in full
    assert b"".join(conn.process.stdin.writes) == SEED.token.encode() + b"\n"
    # The token is read after the inherited ANTHROPIC_* are unset.
    assert full.index("unset") < full.index("IFS= read -r ANTHROPIC_AUTH_TOKEN")
    assert "export ANTHROPIC_BASE_URL=https://router.eu.requesty.ai;" in full


async def test_write_stdin_secrets_refuses_a_line_break():
    for bad in ("x\ny", "x\ry", "x" + chr(0) + "y"):
        writer = _FakeWriter()
        handle = ProcessHandle(pid_like=None, stdout=_nothing(), stdin=writer)
        with pytest.raises(ValueError) as info:
            await write_stdin_secrets(handle, ["fine", bad], names=["A", "B"])
        message = str(info.value)
        assert bad not in message and repr(bad) not in message
        assert writer.writes == []


def _handle(writer=None) -> ProcessHandle:
    return ProcessHandle(pid_like=None, stdout=_nothing(), stdin=writer)


async def test_write_stdin_secrets_checks_every_value_before_writing_any():
    # A lone surrogate cannot be encoded: refused like a line break, before
    # "fine" is written, and with no exception context (a UnicodeEncodeError's
    # `.object` is the whole value).
    writer = _FakeWriter()
    with pytest.raises(ValueError) as info:
        await write_stdin_secrets(_handle(writer), ["fine", "rq-" + chr(0xD800)], names=["A", "B"])
    assert "rq-" not in str(info.value)
    assert info.value.__context__ is None and info.value.__cause__ is None
    assert writer.writes == []
    # A value that is not a str.
    with pytest.raises(TypeError) as info:
        await write_stdin_secrets(_handle(writer), ["fine", b"rq-bytes"], names=["A", "B"])
    assert "rq-" not in str(info.value)
    assert writer.writes == []


async def test_write_stdin_secrets_refuses_a_bare_str():
    writer = _FakeWriter()
    with pytest.raises(TypeError) as info:
        await write_stdin_secrets(_handle(writer), "rq-token", names=["A"])
    assert "rq-" not in str(info.value)
    with pytest.raises(TypeError):
        await write_stdin_secrets(_handle(writer), ["rq-token"], names="A")
    assert writer.writes == []


async def test_write_stdin_secrets_refuses_a_count_mismatch():
    writer = _FakeWriter()
    for values, names in ((["rq-a", "rq-b"], ["A"]), (["rq-a"], ["A", "B"]), ([], ["A"])):
        with pytest.raises(ValueError) as info:
            await write_stdin_secrets(_handle(writer), values, names=names)
        assert "rq-" not in str(info.value)
    assert writer.writes == []


async def test_write_stdin_secrets_refuses_a_handle_without_stdin():
    with pytest.raises(ValueError) as info:
        await write_stdin_secrets(_handle(None), ["rq-token"], names=["A"])
    assert "stdin=True" in str(info.value) and "rq-" not in str(info.value)


# --- settings the agent can write (Important 2) ------------------------------


def test_the_settings_pin_is_the_non_secret_environment():
    env = gateway_launch_env(SEED)
    pinned = gateway_settings_json(env)
    assert json.loads(pinned) == {"env": env}
    assert SEED.token not in pinned
    # The token never goes into it: a secret name is refused, by name only.
    with pytest.raises(ValueError) as info:
        gateway_settings_json({**env, "ANTHROPIC_AUTH_TOKEN": SEED.token})
    assert "ANTHROPIC_AUTH_TOKEN" in str(info.value) and SEED.token not in str(info.value)
    assert info.value.__context__ is None and info.value.__cause__ is None


def test_build_claude_flags_pins_settings_only_when_asked():
    base = dict(permission_mode=None, allowed_tools=None, disallowed_tools=None, model="m")
    assert "--settings" not in build_claude_flags(**base)        # an ordinary session
    flags = build_claude_flags(**base, tools=["Read"], settings='{"env":{}}')
    assert flags[flags.index("--settings") + 1] == '{"env":{}}'


_OWNED = {
    "ANTHROPIC_BASE_URL": "https://elsewhere.invalid",
    "ANTHROPIC_API_KEY": "sk-other",
    "CLAUDE_CODE_SUBPROCESS_ENV_SCRUB": "0",
    "CLAUDE_CODE_EFFORT_LEVEL": "unset",
}


def _settings_host(tmp_path) -> tuple[LocalHost, pathlib.Path]:
    host = LocalHost(taskdir=str(tmp_path / "task"))
    work = pathlib.Path(host.workdir)
    (work / "home/.claude").mkdir(parents=True)
    (work / ".claude").mkdir()
    return host, work


async def test_the_strip_removes_gateway_keys_from_each_settings_file_the_agent_can_write(tmp_path):
    host, work = _settings_host(tmp_path)
    planted = {"cleanupPeriodDays": 7, "env": {"FOO": "bar", **_OWNED},
               "permissions": {"allow": ["Read"]}}
    for rel in AGENT_SETTINGS_FILES:
        (work / rel).write_text(json.dumps(planted))
    # Claude Code does not read this one (measured, 2.1.319): left as it is.
    unread = work / "home/.claude/settings.local.json"
    unread.write_text(json.dumps(planted))

    await strip_settings_env(host, owned=_gateway_owned_keys)

    assert sorted(AGENT_SETTINGS_FILES) == [
        ".claude/settings.json", ".claude/settings.local.json", "home/.claude/settings.json",
    ]
    for rel in AGENT_SETTINGS_FILES:
        assert json.loads((work / rel).read_text()) == {
            "cleanupPeriodDays": 7, "env": {"FOO": "bar"}, "permissions": {"allow": ["Read"]},
        }
    assert json.loads(unread.read_text()) == planted


async def test_the_strip_leaves_what_it_need_not_touch_and_removes_what_it_cannot_check(tmp_path):
    host, work = _settings_host(tmp_path)
    home, project, local = (work / rel for rel in (
        "home/.claude/settings.json", ".claude/settings.json", ".claude/settings.local.json",
    ))
    # No gateway key: not rewritten (byte for byte).
    clean = '{ "env": {"FOO": "bar"},\n  "cleanupPeriodDays": 7 }\n'
    home.write_text(clean)
    # Not a JSON object Python can read: its keys cannot be checked.
    project.write_text('{"env": {"ANTHROPIC_BASE_URL": "https://elsewhere.invalid"}, // a comment\n}')
    # A byte-order mark in front of the JSON.
    local.write_text(chr(0xFEFF) + json.dumps({"env": dict(_OWNED)}), encoding="utf-8")

    await strip_settings_env(host, owned=_gateway_owned_keys)

    assert home.read_text() == clean
    assert not project.exists()
    assert json.loads(local.read_text()) == {"env": {}}

    # An env block that is not an object, and a list: left (no env to apply)
    # and removed (not an object) respectively; missing files are fine.
    home.write_text('{"env": "ANTHROPIC_BASE_URL=x"}')
    project.write_text('["env"]')
    local.unlink()
    await strip_settings_env(host, owned=_gateway_owned_keys)
    assert home.read_text() == '{"env": "ANTHROPIC_BASE_URL=x"}'
    assert not project.exists() and not local.exists()


async def test_the_strip_replaces_a_symlinked_settings_file(tmp_path):
    host, work = _settings_host(tmp_path)
    outside = tmp_path / "outside.json"
    outside.write_text(json.dumps({"env": {"FOO": "bar", **_OWNED}}))
    link = work / ".claude/settings.local.json"
    link.symlink_to(outside)

    await strip_settings_env(host, owned=_gateway_owned_keys)

    assert not link.is_symlink()
    assert json.loads(link.read_text()) == {"env": {"FOO": "bar"}}
    # The link's target, outside the workdir, is not written to.
    assert json.loads(outside.read_text()) == {"env": {"FOO": "bar", **_OWNED}}
