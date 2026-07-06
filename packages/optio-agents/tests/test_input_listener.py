"""POST /input delivers to on_input and maps results to acks."""
import aiohttp

from optio_agents.input_listener import start_input_listener


async def _post(port, payload):
    async with aiohttp.ClientSession() as s:
        async with s.post(f"http://127.0.0.1:{port}/input", json=payload) as r:
            return r.status, await r.json()


async def test_listener_delivers_text_and_acks_ok():
    seen = []

    async def on_input(text):
        seen.append(text)

    runner, port = await start_input_listener(bind_iface="127.0.0.1", on_input=on_input)
    try:
        status, body = await _post(port, {"text": "hello world"})
        assert status == 200 and body == {"ok": True}
        assert seen == ["hello world"]
    finally:
        await runner.cleanup()


async def test_listener_502_on_injection_failure():
    async def on_input(text):
        raise RuntimeError("tmux boom")

    runner, port = await start_input_listener(bind_iface="127.0.0.1", on_input=on_input)
    try:
        status, body = await _post(port, {"text": "x"})
        assert status == 502 and body["reason"] == "send-failed"
    finally:
        await runner.cleanup()


async def test_listener_400_on_empty_text():
    async def on_input(text):
        pass

    runner, port = await start_input_listener(bind_iface="127.0.0.1", on_input=on_input)
    try:
        status, _ = await _post(port, {"text": ""})
        assert status == 400
    finally:
        await runner.cleanup()


async def test_listener_delivers_allowlisted_key():
    seen = []

    async def on_input(text):
        pass

    async def on_key(key):
        seen.append(key)

    runner, port = await start_input_listener(
        bind_iface="127.0.0.1", on_input=on_input, on_key=on_key,
    )
    try:
        status, body = await _post(port, {"key": "Up"})
        assert status == 200 and body["ok"] is True
        assert seen == ["Up"]
    finally:
        await runner.cleanup()


async def test_listener_400_on_disallowed_key():
    async def on_input(text):
        pass

    async def on_key(key):
        raise AssertionError("on_key must not be called for a disallowed key")

    runner, port = await start_input_listener(
        bind_iface="127.0.0.1", on_input=on_input, on_key=on_key,
    )
    try:
        status, body = await _post(port, {"key": "rm -rf"})
        assert status == 400 and body["reason"] == "bad-key"
    finally:
        await runner.cleanup()
