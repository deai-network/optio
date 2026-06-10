"""In-session HTTP listener that receives human-typed messages and a small
lock helper that serializes them against system-message injection.

The listener runs INSIDE the session's asyncio loop (engine-side), so its
handler natively holds the session's injector and lock — no registry, no RPC,
no Mongo poll. It is reached through the API widget proxy exactly as ttyd is
(registered as controlUpstream). See
docs/2026-06-08-claudecode-input-channel-design.md.
"""
from __future__ import annotations

import asyncio
from typing import Awaitable, Callable

from aiohttp import web


# Navigation keys the iframe-input widget may send (as `{key: "<name>"}`) when
# the input box is empty, for driving claude's TUI menus. These are tmux
# send-keys key names; only this exact set is accepted — never an arbitrary
# string (which would let a caller inject other key sequences).
NAV_KEYS = frozenset({"Up", "Down", "Left", "Right", "Enter", "Escape", "Tab"})


def serialized(
    lock: asyncio.Lock, send: Callable[[str], Awaitable[None]],
) -> Callable[[str], Awaitable[None]]:
    """Wrap `send` so every call holds `lock` for the whole injection burst.
    Both the system path (_agent_sender) and the human path go through one
    such wrapper sharing one lock → bursts never interleave."""
    async def _send(text: str) -> None:
        async with lock:
            await send(text)
    return _send


async def start_input_listener(
    *,
    bind_iface: str,
    on_input: Callable[[str], Awaitable[None]],
    on_key: Callable[[str], Awaitable[None]] | None = None,
) -> tuple[web.AppRunner, int]:
    """Start a one-route aiohttp app: POST /input -> inject into the TUI.

    Two payload shapes:
      * ``{"text": "..."}``  -> ``on_input(text)``  (a typed message; non-empty).
      * ``{"key": "<name>"}`` -> ``on_key(name)``   (a single NAV_KEYS keystroke,
        for driving TUI menus from an empty input box).

    Returns (runner, port). Bind on port 0 (OS-assigned); the actual port is
    read back from the bound socket. on_input/on_key raise on injection failure;
    that becomes a 502 {ok:false, reason:"send-failed"}.
    """
    async def handle(request: web.Request) -> web.Response:
        try:
            payload = await request.json()
        except Exception:
            return web.json_response({"ok": False, "reason": "bad-json"}, status=400)
        key = payload.get("key")
        if key is not None:
            if on_key is None or not isinstance(key, str) or key not in NAV_KEYS:
                return web.json_response({"ok": False, "reason": "bad-key"}, status=400)
            try:
                await on_key(key)
            except Exception:
                return web.json_response({"ok": False, "reason": "send-failed"}, status=502)
            return web.json_response({"ok": True})
        text = payload.get("text")
        if not isinstance(text, str) or not text:
            return web.json_response({"ok": False, "reason": "bad-text"}, status=400)
        try:
            await on_input(text)
        except Exception:
            return web.json_response({"ok": False, "reason": "send-failed"}, status=502)
        return web.json_response({"ok": True})

    app = web.Application()
    app.router.add_post("/input", handle)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, bind_iface, 0)
    await site.start()
    # Read the OS-assigned port back from the bound server socket.
    server = site._server  # aiohttp exposes the asyncio.Server here
    port = server.sockets[0].getsockname()[1]
    return runner, port
