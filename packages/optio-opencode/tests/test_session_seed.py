"""Session seed (capture + consume) and auto-start integration tests.

Mirrors optio_claudecode/tests/test_session_seed_capture.py /
test_session_seed_consume.py, adapted to opencode's fake_opencode harness:

* opencode needs no consume-time rekey (the seed manifest's
  ``consume_transform`` is None), so the consume probe only asserts the
  isolated ``home/.local/share/opencode/auth.json`` is present — there is
  no ``.claude.json`` projects-key rewrite to check.
* fake_opencode.py has no dedicated "seed" scenario, and this task's file
  scope is the test file only, so the representative environment is planted
  under the isolated ``$HOME`` (``<workdir>/home``) via a ``before_execute``
  hook instead of inside the fake binary. ``before_execute`` fires after the
  binary is in place and before launch, so the planted files persist into
  the outer ``finally`` where ``capture_seed`` tars them.

The ``_supply_scenario`` fixture is the same fake-opencode substitution used
by test_session_local.py / test_session_resume.py (see those for the
rationale behind the substitution shape). It is copied here rather than
shared so this module stays self-contained.
"""

import asyncio
import io
import os
import sys
import tarfile

import pytest
import pytest_asyncio
from motor.motor_asyncio import AsyncIOMotorClient, AsyncIOMotorGridFSBucket

from optio_core.context import ProcessContext
from optio_core.models import TaskInstance
from optio_core.store import upsert_process

from optio_agents import seeds
from optio_opencode import OpencodeTaskConfig
from optio_opencode.seed_manifest import OPENCODE_SEED_SUFFIX
from optio_opencode.session import run_opencode_session


FAKE_OPENCODE = os.path.join(os.path.dirname(__file__), "fake_opencode.py")


@pytest_asyncio.fixture
async def mongo_db():
    client = AsyncIOMotorClient(os.environ.get("MONGO_URL", "mongodb://localhost:27017"))
    db_name = f"optio_oc_seed_{os.getpid()}"
    db = client[db_name]
    yield db
    await client.drop_database(db_name)
    client.close()


@pytest.fixture
def task_root(tmp_path, monkeypatch):
    monkeypatch.setenv("OPTIO_OPENCODE_TASK_ROOT", str(tmp_path))
    return tmp_path


@pytest.fixture(autouse=True)
def _supply_scenario(monkeypatch):
    """Substitute fake_opencode.py for the real opencode binary.

    Identical to the substitution in test_session_local.py /
    test_session_resume.py — only ``--scenario <name>`` is meaningful to the
    fake; the trailing ``web --port=0 …`` from launch_opencode is harmless.
    """
    from optio_opencode import host_actions
    orig_launch = host_actions.launch_opencode
    holder = {"name": "happy"}

    async def _launch(host, password, *, ready_timeout_s=30.0, opencode_executable="opencode", hostname="127.0.0.1", extra_env=None, env_remove=None):
        del opencode_executable
        return await orig_launch(
            host, password,
            ready_timeout_s=ready_timeout_s,
            opencode_executable=(
                f"{sys.executable} {FAKE_OPENCODE} --scenario {holder['name']}"
            ),
            hostname=hostname,
            extra_env=extra_env,
        )
    monkeypatch.setattr(host_actions, "launch_opencode", _launch)

    async def _ensure(hook_ctx, install_if_missing=True, *, install_dir=None):
        return "opencode"
    monkeypatch.setattr(host_actions, "ensure_opencode_installed", _ensure)

    async def _version(host, *, opencode_executable="opencode"):
        return None
    monkeypatch.setattr(host_actions, "opencode_version", _version)

    orig_export = host_actions.opencode_export

    async def _export(host, opencode_db_path, session_id, *, opencode_executable="opencode"):
        return await orig_export(
            host, opencode_db_path, session_id,
            opencode_executable=f"{sys.executable} {FAKE_OPENCODE}",
        )
    monkeypatch.setattr(host_actions, "opencode_export", _export)

    orig_import = host_actions.opencode_import

    async def _import(host, opencode_db_path, session_json, *, opencode_executable="opencode"):
        return await orig_import(
            host, opencode_db_path, session_json,
            opencode_executable=f"{sys.executable} {FAKE_OPENCODE}",
        )
    monkeypatch.setattr(host_actions, "opencode_import", _import)

    return holder


async def _make_ctx(mongo_db, process_id, *, resume=False):
    task = TaskInstance(
        execute=lambda c: None,  # type: ignore[arg-type, return-value]
        process_id=process_id, name=process_id, supports_resume=True,
    )
    proc = await upsert_process(mongo_db, "test", task)
    await mongo_db["test_processes"].update_one(
        {"_id": proc["_id"]}, {"$set": {"status": {"state": "running"}}},
    )
    return ProcessContext(
        process_oid=proc["_id"], process_id=process_id, root_oid=proc["_id"],
        depth=0, params={}, services={}, db=mongo_db, prefix="test",
        cancellation_flag=asyncio.Event(), child_counter={"next": 0}, resume=resume,
    )


async def _plant_env(hook_ctx) -> None:
    """before_execute probe: plant a representative opencode env under the
    isolated HOME so seed capture has INCLUDE files to tar and an EXCLUDE
    file to skip.

    ``<workdir>/home`` is the seed manifest's ``home_subdir``; the launch's
    XDG_DATA_HOME / XDG_CONFIG_HOME point inside it. Mirrors fake_claude's
    ``_scenario_seed`` planting, done from the test side because
    fake_opencode has no seed scenario and is out of this task's file scope.
    """
    home = f"{hook_ctx._host.workdir.rstrip('/')}/home"
    script = "; ".join([
        # INCLUDE (environment)
        f"mkdir -p '{home}/.local/share/opencode'",
        f"mkdir -p '{home}/.config/opencode/plugins'",
        f"printf '%s' '{{\"token\": \"abc\"}}' > '{home}/.local/share/opencode/auth.json'",
        f"printf '%s' '{{\"theme\": \"dark\"}}' > '{home}/.config/opencode/opencode.json'",
        f"printf '%s' '{{}}' > '{home}/.config/opencode/plugins/p.json'",
        # EXCLUDE (session / message store) — must NOT travel in the seed
        f"mkdir -p '{home}/.local/share/opencode/storage'",
        f"printf '%s' 'secret-session' > '{home}/.local/share/opencode/storage/messages.json'",
    ])
    await hook_ctx._host.run_command(script)


async def test_capture_fires_callback_and_stores_env_only_seed(
    mongo_db, task_root, _supply_scenario,
):
    _supply_scenario["name"] = "happy"
    captured: list[str] = []

    async def _on_seed_saved(seed_id, info=None) -> None:
        captured.append(seed_id)

    ctx = await _make_ctx(mongo_db, "oc_seed_cap")
    cfg = OpencodeTaskConfig(
        consumer_instructions="(seed setup)",
        supports_resume=False,
        on_seed_saved=_on_seed_saved,
        before_execute=_plant_env,
    )
    await run_opencode_session(ctx, cfg)

    # callback fired with a single hex id
    assert len(captured) == 1
    seed_id = captured[0]

    # a seed doc + blob exist under the opencode suffix
    doc = await seeds.load_seed(
        mongo_db, prefix="test", suffix=OPENCODE_SEED_SUFFIX, seed_id=seed_id,
    )
    assert doc is not None

    # the seed tar contains ONLY the INCLUDE paths, never the message store
    bucket = AsyncIOMotorGridFSBucket(mongo_db)
    stream = await bucket.open_download_stream(doc["blobId"])
    blob = await stream.read()
    with tarfile.open(fileobj=io.BytesIO(blob), mode="r:gz") as tar:
        names = tar.getnames()
    assert any(n.endswith("auth.json") for n in names), names
    assert any(n.endswith(".config/opencode/opencode.json") for n in names), names
    assert any("plugins" in n for n in names), names
    assert not any("storage" in n for n in names), names
    assert not any("messages.json" in n for n in names), names


async def test_capture_synthesises_model_into_opencode_json(
    mongo_db, task_root, _supply_scenario, monkeypatch,
):
    """At seed capture, the operator's last-used model is resolved from the
    live opencode session and merged into the seed's ``opencode.json``
    ``model`` field — so an unattended seeded session runs that model instead
    of opencode's first-provider fallback. ``_plant_env`` writes
    ``opencode.json`` with ``{"theme": "dark"}``; the synthesis must add
    ``model`` while preserving ``theme``."""
    import json
    import optio_opencode.session as session_mod

    _supply_scenario["name"] = "happy"

    async def _fake_resolve(port, password, session_id):
        return "xai/grok-4.3"

    monkeypatch.setattr(session_mod, "_resolve_session_model", _fake_resolve)

    captured: list[tuple[str, str | None]] = []

    async def _on_seed_saved(seed_id, info=None) -> None:
        captured.append((seed_id, info))

    ctx = await _make_ctx(mongo_db, "oc_seed_model")
    await run_opencode_session(ctx, OpencodeTaskConfig(
        consumer_instructions="(seed setup)",
        supports_resume=False,
        on_seed_saved=_on_seed_saved,
        before_execute=_plant_env,
    ))
    assert len(captured) == 1
    seed_id, info = captured[0]
    # the resolved model is passed as on_seed_saved's 2nd arg
    assert info == "xai/grok-4.3"

    doc = await seeds.load_seed(
        mongo_db, prefix="test", suffix=OPENCODE_SEED_SUFFIX, seed_id=seed_id,
    )
    assert doc is not None

    bucket = AsyncIOMotorGridFSBucket(mongo_db)
    stream = await bucket.open_download_stream(doc["blobId"])
    blob = await stream.read()
    with tarfile.open(fileobj=io.BytesIO(blob), mode="r:gz") as tar:
        member = next(
            m for m in tar.getmembers()
            if m.name.endswith(".config/opencode/opencode.json")
        )
        cfg = json.loads(tar.extractfile(member).read().decode("utf-8"))

    assert cfg["model"] == "xai/grok-4.3"
    # the pre-existing config key planted by _plant_env survives the merge
    assert cfg["theme"] == "dark"


async def test_second_session_consumes_seed(
    mongo_db, task_root, _supply_scenario,
):
    _supply_scenario["name"] = "happy"

    # 1) capture
    captured: list[str] = []

    async def _on_seed_saved(seed_id, info=None) -> None:
        captured.append(seed_id)

    ctx1 = await _make_ctx(mongo_db, "oc_seed_src")
    await run_opencode_session(ctx1, OpencodeTaskConfig(
        consumer_instructions="(seed setup)",
        supports_resume=False,
        on_seed_saved=_on_seed_saved,
        before_execute=_plant_env,
    ))
    assert len(captured) == 1
    seed_id = captured[0]

    # 2) consume in a DIFFERENT process; probe the merged env via before_execute.
    #    The second session does NOT re-plant — the auth.json must come purely
    #    from the seed merge that runs before before_execute fires.
    observed: dict = {}

    async def _probe(hook_ctx):
        wd = hook_ctx._host.workdir.rstrip("/")
        observed["auth"] = os.path.exists(
            f"{wd}/home/.local/share/opencode/auth.json"
        )
        observed["plugins"] = os.path.exists(
            f"{wd}/home/.config/opencode/plugins"
        )
        # the excluded message store from the seed-source session must NOT
        # have been restored
        observed["storage"] = os.path.exists(
            f"{wd}/home/.local/share/opencode/storage"
        )

    ctx2 = await _make_ctx(mongo_db, "oc_seed_dst")
    await run_opencode_session(ctx2, OpencodeTaskConfig(
        consumer_instructions="(seeded fresh)",
        supports_resume=False,
        seed_id=seed_id,
        before_execute=_probe,
    ))

    assert observed["auth"] is True
    assert observed["plugins"] is True
    # the message store was an EXCLUDE path, so it never travelled in the seed
    assert observed["storage"] is False


async def test_auto_start_posts_on_fresh_and_not_on_resume(
    mongo_db, task_root, _supply_scenario, monkeypatch,
):
    """auto_start=True POSTs the kickoff prompt on a fresh launch and is
    suppressed on resume (the restored session already carries its
    conversation)."""
    import optio_opencode.session as session_mod

    _supply_scenario["name"] = "happy"

    posts: list[tuple[str, str]] = []

    async def _fake_post(port, password, session_id, message):
        posts.append((session_id, message))

    monkeypatch.setattr(session_mod, "_post_opencode_prompt", _fake_post)

    pid = "oc_autostart"

    # fresh launch (supports_resume=True so a snapshot is captured for the
    # resume leg) → must POST exactly once
    ctx_fresh = await _make_ctx(mongo_db, pid, resume=False)
    await run_opencode_session(ctx_fresh, OpencodeTaskConfig(
        consumer_instructions="(scenario: happy)",
        auto_start=True,
    ))
    assert len(posts) == 1
    posted_session_id, posted_message = posts[0]
    assert posted_message == session_mod.AUTO_START_PROMPT
    assert posted_session_id  # a real (pre-created) session id

    # resume the same process → must NOT POST again
    ctx_resume = await _make_ctx(mongo_db, pid, resume=True)
    await run_opencode_session(ctx_resume, OpencodeTaskConfig(
        consumer_instructions="(scenario: happy)",
        auto_start=True,
    ))
    assert len(posts) == 1, posts


def test_post_opencode_prompt_uses_prompt_async_parts_body(monkeypatch):
    """The auto-start POST must hit opencode's v1 fire-and-forget route
    ``POST /session/:sessionID/prompt_async`` with a ``PromptPayload`` body:
    ``{"parts": [{"type": "text", "text": <msg>}]}`` (``parts`` is required).

    The earlier targets were both wrong against opencode 1.14.x and crashed
    the task: ``{"parts": [{"type": "text", ...}]}`` to the experimental v2
    route ``/api/session/:id/prompt`` (and the ``{"prompt": {"text": ...}}``
    guess) 400 with ``Expected Session.Message`` — the retries exhaust, a
    RuntimeError aborts the session, and opencode is torn down (the web UI
    then 502s its own backend). ``prompt_async`` returns 204 immediately,
    which is the unattended-kickoff semantics we want; the sync ``/message``
    route would block streaming the whole AI response."""
    import json
    import urllib.request
    from optio_opencode import session as session_mod

    captured: dict = {}

    class _Resp:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def read(self):
            return b""

    def fake_urlopen(req, timeout=None):
        captured["url"] = req.full_url
        captured["body"] = json.loads(req.data.decode("utf-8"))
        return _Resp()

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    session_mod._post_opencode_prompt_sync(4096, "pw", "ses_abc", "do the thing")

    assert captured["url"].endswith("/session/ses_abc/prompt_async")
    assert captured["body"] == {
        "parts": [{"type": "text", "text": "do the thing"}]
    }


def test_resolve_session_model_returns_last_assistant_model(monkeypatch):
    """``_resolve_session_model_sync`` GETs ``/session/:id/message`` and returns
    the operator's last-used model as ``"providerID/modelID"`` — the LAST
    assistant message wins (the operator may switch models mid-session; e.g.
    start on anthropic's default, then switch to xai/grok). This value is
    synthesised into the seed's ``opencode.json`` ``model`` so an unattended
    seeded session runs the operator's model instead of opencode's
    first-provider fallback (anthropic, no key → ``invalid x-api-key``)."""
    import json
    import urllib.request
    from optio_opencode import session as session_mod

    messages = [
        {"info": {"role": "user",
                  "model": {"providerID": "anthropic", "modelID": "claude-sonnet-4-6"}},
         "parts": []},
        {"info": {"role": "assistant",
                  "providerID": "anthropic", "modelID": "claude-sonnet-4-6"},
         "parts": []},
        {"info": {"role": "assistant",
                  "providerID": "xai", "modelID": "grok-4.3"},
         "parts": []},
    ]
    captured: dict = {}

    class _Resp:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def read(self):
            return json.dumps(messages).encode("utf-8")

    def fake_urlopen(req, timeout=None):
        captured["url"] = req.full_url
        captured["method"] = req.get_method()
        return _Resp()

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    model = session_mod._resolve_session_model_sync(4096, "pw", "ses_abc")

    assert model == "xai/grok-4.3"
    assert captured["url"].endswith("/session/ses_abc/message")
    assert captured["method"] == "GET"


def test_resolve_session_model_none_when_no_assistant(monkeypatch):
    """No assistant message (operator connected but never sent one) → None, so
    the caller skips writing a model default (seed behaviour unchanged)."""
    import json
    import urllib.request
    from optio_opencode import session as session_mod

    messages = [{"info": {"role": "user",
                          "model": {"providerID": "xai", "modelID": "grok-4.3"}},
                 "parts": []}]

    class _Resp:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def read(self):
            return json.dumps(messages).encode("utf-8")

    monkeypatch.setattr(urllib.request, "urlopen", lambda req, timeout=None: _Resp())
    assert session_mod._resolve_session_model_sync(4096, "pw", "ses_x") is None


async def test_write_seed_model_config_creates_and_merges(tmp_path):
    """``_write_seed_model_config`` writes ``model`` into the seed's XDG
    ``<workdir>/home/.config/opencode/opencode.json`` — creating the file when
    absent and merging (preserving other keys) when present."""
    import json
    from optio_host.host import LocalHost
    from optio_opencode import session as session_mod

    taskdir = str(tmp_path / "task")
    os.makedirs(taskdir, exist_ok=True)
    host = LocalHost(taskdir=taskdir)
    os.makedirs(host.workdir, exist_ok=True)
    cfg_path = f"{host.workdir}/home/.config/opencode/opencode.json"

    # case 1: no existing config → created with model
    await session_mod._write_seed_model_config(host, "xai/grok-4.3")
    with open(cfg_path) as f:
        data = json.load(f)
    assert data["model"] == "xai/grok-4.3"

    # case 2: existing config with other keys → model added/updated, rest kept
    with open(cfg_path, "w") as f:
        json.dump({"theme": "dark"}, f)
    await session_mod._write_seed_model_config(host, "anthropic/claude-x")
    with open(cfg_path) as f:
        data = json.load(f)
    assert data["model"] == "anthropic/claude-x"
    assert data["theme"] == "dark"
