"""The Requesty provider: an account's models in three groups, typed errors."""

import asyncio
import logging
import socket
import traceback

import pytest
import pytest_asyncio
from aiohttp import web

from optio_claudecode.gateway import (
    GatewayAuthError,
    GatewayEmptyList,
    GatewayError,
    GatewayModel,
    GatewaySeed,
    GatewayUnreachable,
)
from optio_claudecode.gateways.requesty import ModelChoice, PROVIDER, list_models, new_seed

TOKEN = "rq-secret-9876"

MODELS = {"data": [
    {"id": "prov-a/kimi-k3.1-pro", "model_canonical_name": "kimi-k3.1-pro",
     "reasoning": {"supported_efforts": ["minimal", "low", "high"]}},
    {"id": "prov-b/glm-5", "model_canonical_name": "glm-5"},
    {"id": "policy/example-policy", "reasoning": {"supported_efforts": ["low"]}}]}
MANAGED = {"data": [{"id": "kimi-k3.1-pro", "model_canonical_name": "kimi-k3.1-pro"},
                    {"id": "glm-5@eu", "model_canonical_name": "glm-5"}]}

MODELS_PATH = "/v1/models"
MANAGED_PATH = "/v1/models/managed"
HANG = object()  # the handler waits until the fixture's teardown


class FakeRequesty:
    """Answers each path with its entry in `answers`: a JSON body (dict or
    list), an int (that status, with a JSON error body), a str (that text as
    the body, status 200) or HANG. Records every Authorization header."""

    def __init__(self) -> None:
        self.url = ""
        self.auth: list[str | None] = []
        self.answers: dict[str, object] = {MODELS_PATH: MODELS, MANAGED_PATH: MANAGED}
        self.release = asyncio.Event()

    async def handle(self, request: web.Request) -> web.StreamResponse:
        self.auth.append(request.headers.get("Authorization"))
        answer = self.answers[request.path]
        if answer is HANG:
            await self.release.wait()
            return web.json_response({"data": []})
        if isinstance(answer, int):
            return web.json_response({"error": {"message": "no"}}, status=answer)
        if isinstance(answer, str):
            return web.Response(text=answer)
        return web.json_response(answer)


@pytest_asyncio.fixture
async def requesty():
    fake = FakeRequesty()
    app = web.Application()
    app.router.add_get(MODELS_PATH, fake.handle)
    app.router.add_get(MANAGED_PATH, fake.handle)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0)
    await site.start()
    fake.url = f"http://127.0.0.1:{runner.addresses[0][1]}"
    yield fake
    fake.release.set()
    await runner.cleanup()


def _closed_port_url() -> str:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    return f"http://127.0.0.1:{port}"


async def test_eu_lists_the_three_groups(requesty):                         # RF3
    got = await list_models(TOKEN, "eu", base_url=requesty.url)
    assert got == [ModelChoice("glm-5@eu", "glm-5@eu", "managed", None, True),
                   ModelChoice("kimi-k3.1-pro", "kimi-k3.1-pro", "managed", ("low", "high"), False),
                   ModelChoice("prov-a/kimi-k3.1-pro", "prov-a/kimi-k3.1-pro", "pinned", ("low", "high"), False),
                   ModelChoice("prov-b/glm-5", "prov-b/glm-5", "pinned", None, False),
                   ModelChoice("policy/example-policy", "policy/example-policy", "policy", None, False)]
    assert requesty.auth == [f"Bearer {TOKEN}"] * 2


async def test_global_leaves_out_eu_names(requesty):
    got = await list_models(TOKEN, "global", base_url=requesty.url)
    assert [c.id for c in got] == [
        "kimi-k3.1-pro", "prov-a/kimi-k3.1-pro", "prov-b/glm-5", "policy/example-policy",
    ]
    assert not any(c.eu_only for c in got)


async def test_a_policy_lends_no_efforts(requesty):                         # RF3
    requesty.answers[MODELS_PATH] = {"data": [
        {"id": "policy/p", "model_canonical_name": "glm-5",
         "reasoning": {"supported_efforts": ["low", "high"]}}]}
    requesty.answers[MANAGED_PATH] = {"data": [{"id": "glm-5", "model_canonical_name": "glm-5"}]}
    got = await list_models(TOKEN, "global", base_url=requesty.url)
    assert [(c.id, c.efforts) for c in got] == [("glm-5", None), ("policy/p", None)]


async def test_a_duplicate_id_keeps_the_first_entry(requesty):
    requesty.answers[MODELS_PATH] = {"data": [
        {"id": "glm-5", "reasoning": {"supported_efforts": ["max"]}}]}
    requesty.answers[MANAGED_PATH] = {"data": [
        {"id": "glm-5", "model_canonical_name": "glm-5",
         "reasoning": {"supported_efforts": ["low"]}}]}
    got = await list_models(TOKEN, "global", base_url=requesty.url)
    assert got == [ModelChoice("glm-5", "glm-5", "managed", ("max",), False)]


async def test_a_bare_list_is_taken_as_the_data(requesty):
    requesty.answers[MANAGED_PATH] = MANAGED["data"]
    got = await list_models(TOKEN, "eu", base_url=requesty.url)
    assert got[:2] == [ModelChoice("glm-5@eu", "glm-5@eu", "managed", None, True),
                       ModelChoice("kimi-k3.1-pro", "kimi-k3.1-pro", "managed", ("low", "high"), False)]


@pytest.mark.parametrize("path, answer, expected", [
    (MODELS_PATH, 401, GatewayAuthError),
    (MANAGED_PATH, 403, GatewayAuthError),
    (MODELS_PATH, 503, GatewayUnreachable),
    (MANAGED_PATH, 429, GatewayError),
    (MODELS_PATH, "closed-port", GatewayUnreachable),
    (MANAGED_PATH, HANG, GatewayUnreachable),
    (None, {"data": []}, GatewayEmptyList),
    (MANAGED_PATH, {"data": "x"}, GatewayError),
    (MODELS_PATH, "not json", GatewayError),
], ids=["401", "403", "503", "429", "closed-port", "timeout", "both-empty", "bad-shape", "not-json"])
async def test_errors_are_typed_and_carry_no_token(requesty, caplog, path, answer, expected):  # RF1
    caplog.set_level(logging.DEBUG)
    base_url = requesty.url
    if answer == "closed-port":
        base_url = _closed_port_url()
    elif path is None:
        requesty.answers[MODELS_PATH] = requesty.answers[MANAGED_PATH] = answer
    else:
        requesty.answers[path] = answer
    timeout = 0.2 if answer is HANG else 60.0
    with pytest.raises(GatewayError) as info:
        await list_models(TOKEN, "eu", timeout=timeout, base_url=base_url)
    exc = info.value
    assert type(exc) is expected
    if path is not None:
        assert f"{path}:" in str(exc)
    if isinstance(answer, int):
        assert str(answer) in str(exc)
    shown = "".join(traceback.format_exception(exc))
    for text in (str(exc), repr(exc), shown, caplog.text):
        assert TOKEN not in text


async def test_list_models_refuses_an_unknown_region(requesty):
    with pytest.raises(ValueError):
        await list_models(TOKEN, "mars", base_url=requesty.url)
    assert requesty.auth == []


def test_new_seed_takes_the_regions_url():
    models = [GatewayModel("glm-5", "glm-5", "managed", None)]
    seed = new_seed(token=TOKEN, region="global", models=models, default_model="glm-5")
    assert seed == GatewaySeed(
        provider=PROVIDER, region="global", base_url="https://router.requesty.ai", token=TOKEN,
        models=tuple(models), default_model="glm-5",
    )
    assert PROVIDER == "requesty"
    eu = new_seed(token=TOKEN, region="eu", models=models, default_model="glm-5")
    assert eu.base_url == "https://router.eu.requesty.ai"
    with pytest.raises(ValueError) as info:
        new_seed(token=TOKEN, region="mars", models=models, default_model="glm-5")
    assert TOKEN not in str(info.value)
