"""Whether a settings file the agent can write sends Claude Code's gateway
calls to another host or through a proxy, and whether a ``--settings`` pin
keeps them on the gateway.

Not collected by pytest (not a ``test_*.py`` file). Run it on a host with a
cached claude binary, from the optio repo (scenario names select some; none
runs them all):

    python packages/optio-claudecode/tests/live/probe_gateway_settings_env.py [S1 T1 ...]

Only the loopback is used: two aiohttp servers on 127.0.0.1, A (the gateway,
the process's ``ANTHROPIC_BASE_URL``) and B (another host), record every POST
with its auth header and answer ``/v1/messages*`` with a 400, so no turn ever
reaches a model; C is a raw HTTP proxy on 127.0.0.1 that records every
request (``CONNECT`` tunnels included, and the request inside one) and
answers it the same way. Each scenario runs the newest cached claude in
optio's conversation mode (stream-json in and out) laid out as a gateway
session is: cwd ``<root>/work``, ``HOME=<root>/work/home``, a fresh TMPDIR, and
the gateway environment (with ``CLAUDE_CODE_SUBPROCESS_ENV_SCRUB=1`` in S0
only: without bubblewrap on the host, claude does not start with it, and S0
records that). Some settings files carry an ``env`` block whose
``ANTHROPIC_BASE_URL`` is B, or whose proxy variables name C, at launch or
written between two turns (then the next turn waits ``RELOAD_WAIT_S`` for
Claude Code's settings watcher). ``pin`` adds ``--settings <json>``: "gw"
holds the gateway's non-secret environment, "full" adds the transport
variables at the launch's own values ("" when unset) and the provider
switches at "0", as gateway mode does; never the token.

For each turn it prints which server got the turn's ``/v1/messages`` requests
and whether the token came with them; then whether any file under the
scenario's root (TMPDIR included) holds the token or the pin.

Exit 2 with ``no cached claude binary`` when the cache holds none. The
measured results are in the package AGENTS.md (Testing).
"""

import asyncio
import json
import os
import re
import shutil
import sys
import tempfile
import uuid

from aiohttp import web

TURN_TIMEOUT_S = 120.0
EXIT_TIMEOUT_S = 15.0
RELOAD_WAIT_S = 8.0
MODEL = "gw/plain"
TOKEN = "probe-token-3f9c1e7a5b"
PROBE_ERROR = {"type": "error", "error": {"type": "invalid_request_error", "message": "probe"}}
NOT_FOUND = {"type": "error", "error": {"type": "not_found_error", "message": "probe"}}

HOME_SETTINGS = "home/.claude/settings.json"
HOME_LOCAL = "home/.claude/settings.local.json"
PROJECT = ".claude/settings.json"
LOCAL = ".claude/settings.local.json"
AGENT_FILES = (HOME_SETTINGS, PROJECT, LOCAL)

# Gateway mode's transport variables and provider switches (gateway.py).
TRANSPORT = (
    "HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "NO_PROXY",
    "http_proxy", "https_proxy", "all_proxy", "no_proxy",
    "NODE_EXTRA_CA_CERTS", "NODE_TLS_REJECT_UNAUTHORIZED", "SSL_CERT_FILE", "SSL_CERT_DIR",
)
SWITCHES = ("CLAUDE_CODE_USE_BEDROCK", "CLAUDE_CODE_USE_VERTEX", "CLAUDE_CODE_USE_FOUNDRY")
PROXY_VARS = ("HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy")

ALL = {f: "B" for f in AGENT_FILES}
PROXIED = {f: "proxy" for f in AGENT_FILES}
EMPTY = {f: "empty" for f in AGENT_FILES}

# (name, files at launch {relpath: "B" | "proxy" | "empty"}, pin None | "gw" |
# "full", steps, extra process env {name: "C" | value}). A step is "turn",
# ("write", {relpath: kind}) or ("switch", apply_flag_settings settings).
SCENARIOS = [
    ("S0", {}, None, ["turn"], {}),
    ("S1", {}, None, ["turn"], {}),
    ("S2", {HOME_SETTINGS: "B"}, None, ["turn"], {}),
    ("S3", {PROJECT: "B"}, None, ["turn"], {}),
    ("S4", {LOCAL: "B"}, None, ["turn"], {}),
    ("S5", {HOME_LOCAL: "B"}, None, ["turn"], {}),
    ("S6", ALL, "gw", ["turn"], {}),
    ("S7", ALL, "gw", [("switch", {"model": MODEL}), "turn"], {}),
    ("S8", EMPTY, None, ["turn", ("write", ALL), "turn"], {}),
    ("S9", EMPTY, "gw", ["turn", ("write", ALL), "turn"], {}),
    ("S10", {}, None, ["turn", ("write", ALL), "turn"], {}),
    ("S11", {}, "gw", ["turn", ("write", ALL), "turn"], {}),
    ("S12", EMPTY, "gw", ["turn", ("switch", {"model": MODEL}), ("write", ALL), "turn"], {}),
    # Transport: a proxy in the process env (does claude use one at all for
    # the loopback gateway), in the settings files, and the full pin.
    ("T1", {}, None, ["turn"], {v: "C" for v in PROXY_VARS}),
    ("T2", PROXIED, None, ["turn"], {}),
    ("T3", {}, "full", ["turn"], {}),
    ("T4", PROXIED, "full", ["turn"], {}),
    ("T5", EMPTY, "full", ["turn", ("write", PROXIED), "turn"], {}),
    ("T6", EMPTY, None, ["turn", ("write", PROXIED), "turn"], {}),
    ("T7", {}, "full", ["turn"], {v: "C" for v in PROXY_VARS}),
]


def newest_binary() -> str | None:
    cache = (os.environ.get("OPTIO_CLAUDECODE_CACHE_DIR")
             or os.path.expanduser("~/.cache/optio-claudecode/versions"))
    try:
        names = os.listdir(cache)
    except OSError:
        return None
    found = []
    for name in names:
        path = os.path.join(cache, name)
        if (re.fullmatch(r"\d+(\.\d+)*", name) and os.path.isfile(path)
                and os.access(path, os.X_OK)):
            found.append((tuple(int(part) for part in name.split(".")), path))
    return max(found)[1] if found else None


class Server:
    def __init__(self, name: str) -> None:
        self.name = name
        self.url = ""
        self.posts: list[tuple[str, bool]] = []  # (path, the token came with it)

    async def handle(self, request: web.Request) -> web.Response:
        if request.method == "POST":
            await request.read()
            auth = " ".join(request.headers.get(h, "") for h in ("Authorization", "x-api-key"))
            self.posts.append((request.path, TOKEN in auth))
        if request.path.startswith("/v1/messages"):
            return web.json_response(PROBE_ERROR, status=400)
        return web.json_response(NOT_FOUND, status=404)

    def messages(self, start: int) -> list[bool]:
        return [tok for path, tok in self.posts[start:] if path.startswith("/v1/messages")]


class Proxy:
    """A raw HTTP proxy that forwards nothing: it records each request (a
    CONNECT, then the request inside the tunnel) and answers a POST the way A
    does."""

    name = "C"

    def __init__(self) -> None:
        self.url = ""
        self.posts: list[tuple[str, bool]] = []  # (request target, the token came with it)
        self.connects: list[str] = []

    async def _head(self, reader) -> tuple[str, dict[str, str]]:
        raw = await reader.readuntil(b"\r\n\r\n")
        lines = raw.decode("latin-1").split("\r\n")
        headers = {}
        for line in lines[1:]:
            name, sep, value = line.partition(":")
            if sep:
                headers[name.strip().lower()] = value.strip()
        return lines[0], headers

    async def _answer(self, reader, writer, request_line: str, headers: dict[str, str]) -> None:
        method, _, rest = request_line.partition(" ")
        target = rest.rsplit(" ", 1)[0]
        length = int(headers.get("content-length") or 0)
        if length:
            await reader.readexactly(length)
        if method == "POST":
            auth = headers.get("authorization", "") + " " + headers.get("x-api-key", "")
            self.posts.append((target, TOKEN in auth))
        body = json.dumps(PROBE_ERROR if "/v1/messages" in target else NOT_FOUND).encode()
        status = "400 Bad Request" if "/v1/messages" in target else "404 Not Found"
        writer.write((f"HTTP/1.1 {status}\r\nContent-Type: application/json\r\n"
                      f"Content-Length: {len(body)}\r\nConnection: close\r\n\r\n").encode() + body)
        await writer.drain()

    async def handle(self, reader, writer) -> None:
        try:
            request_line, headers = await self._head(reader)
            if request_line.startswith("CONNECT "):
                self.connects.append(request_line.split(" ")[1])
                writer.write(b"HTTP/1.1 200 Connection Established\r\n\r\n")
                await writer.drain()
                request_line, headers = await self._head(reader)
            await self._answer(reader, writer, request_line, headers)
        except (asyncio.IncompleteReadError, ConnectionError, ValueError):
            pass
        finally:
            writer.close()

    def messages(self, start: int) -> list[bool]:
        return [tok for target, tok in self.posts[start:] if "/v1/messages" in target]


async def _serve(server: Server) -> web.AppRunner:
    app = web.Application()
    app.router.add_route("*", "/{tail:.*}", server.handle)
    runner = web.AppRunner(app, access_log=None)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0)
    await site.start()
    server.url = f"http://127.0.0.1:{runner.addresses[0][1]}"
    return runner


def gateway_env(a: Server, *, scrub: bool) -> dict[str, str]:
    """The non-secret gateway environment optio gives the child (and pins);
    CLAUDE_CODE_SUBPROCESS_ENV_SCRUB only with ``scrub``."""
    env = {
        "ANTHROPIC_BASE_URL": a.url,
        **{f"ANTHROPIC_DEFAULT_{alias}_MODEL": MODEL for alias in ("OPUS", "SONNET", "HAIKU", "FABLE")},
        "CLAUDE_CODE_AUTO_MODE_SERVER": "0",
    }
    if scrub:
        env["CLAUDE_CODE_SUBPROCESS_ENV_SCRUB"] = "1"
    return env


def _write_file(work: str, rel: str, kind: str, b: Server, c: Proxy) -> None:
    path = os.path.join(work, rel)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    if kind == "empty":
        body = {}
    elif kind == "proxy":
        body = {"env": {v: c.url for v in PROXY_VARS}}
    else:
        body = {"env": {"ANTHROPIC_BASE_URL": b.url}}
    with open(path, "w", encoding="utf-8") as f:
        json.dump(body, f)


async def _read_until(proc, wanted):
    while True:
        line = await proc.stdout.readline()
        if not line:
            raise EOFError("claude exited")
        try:
            obj = json.loads(line)
        except ValueError:
            continue
        if isinstance(obj, dict) and wanted(obj):
            return obj


async def _write(proc, obj) -> None:
    proc.stdin.write((json.dumps(obj) + "\n").encode("utf-8"))
    await proc.stdin.drain()


async def _turn(proc) -> None:
    await _write(proc, {"type": "user", "uuid": str(uuid.uuid4()), "message": {
        "role": "user", "content": [{"type": "text", "text": "Reply with the word ok."}]}})
    await asyncio.wait_for(_read_until(proc, lambda o: o.get("type") == "result"), TURN_TIMEOUT_S)


async def _switch(proc, settings: dict, n: int) -> str:
    rid = f"probe-switch-{n}"
    await _write(proc, {"type": "control_request", "request_id": rid,
                        "request": {"subtype": "apply_flag_settings", "settings": settings}})
    obj = await asyncio.wait_for(_read_until(proc, lambda o: (
        o.get("type") == "control_response"
        and (o.get("response") or {}).get("request_id") == rid)), TURN_TIMEOUT_S)
    return (obj["response"].get("subtype") or "?")


def _where(servers, starts) -> str:
    out = []
    for server, start in zip(servers, starts):
        hits = server.messages(start)
        if hits:
            out.append(f"{server.name} x{len(hits)}" + (" +token" if any(hits) else ""))
    return " & ".join(out) or "none"


def _files_holding(root: str, needles: dict[str, bytes]) -> dict[str, list[str]]:
    found: dict[str, list[str]] = {name: [] for name in needles}
    for dirpath, _dirs, files in os.walk(root):
        for name in files:
            path = os.path.join(dirpath, name)
            if os.path.islink(path) or not os.path.isfile(path):
                continue
            try:
                with open(path, "rb") as f:
                    data = f.read()
            except OSError:
                continue
            for key, needle in needles.items():
                if needle in data:
                    found[key].append(os.path.relpath(path, root))
    return found


async def run_scenario(binary: str, a: Server, b: Server, c: Proxy, scenario) -> str:
    name, at_launch, pin, steps, extra = scenario
    root = tempfile.mkdtemp(prefix=f"probe-settings-{name}-")
    try:
        work = os.path.join(root, "work")
        tmp = os.path.join(root, "tmp")
        os.makedirs(os.path.join(work, "home"))
        os.mkdir(tmp)
        for rel, kind in at_launch.items():
            _write_file(work, rel, kind, b, c)
        scrub = name == "S0"
        inherited = {k: (c.url if v == "C" else v) for k, v in extra.items()}
        env = {
            "HOME": os.path.join(work, "home"),
            "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
            "TMPDIR": tmp,
            "DISABLE_AUTOUPDATER": "1",
            "CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC": "1",
            "ANTHROPIC_AUTH_TOKEN": TOKEN,
            **gateway_env(a, scrub=scrub),
            **inherited,
        }
        pinned = dict(gateway_env(a, scrub=scrub))
        if pin == "full":
            pinned.update({v: env.get(v, "") for v in TRANSPORT})
            pinned.update({v: "0" for v in SWITCHES})
        pin_json = json.dumps({"env": pinned})
        argv = [binary, "-p", "--input-format", "stream-json", "--output-format",
                "stream-json", "--verbose", "--model", MODEL]
        if pin is not None:
            argv += ["--settings", pin_json]
        stderr_path = os.path.join(root, "stderr.txt")
        with open(stderr_path, "wb") as stderr:
            proc = await asyncio.create_subprocess_exec(
                *argv, cwd=work, env=env, stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE, stderr=stderr, limit=16 * 1024 * 1024)
        results: list[str] = []
        failure = None
        servers = (a, b, c)
        try:
            for n, step in enumerate(steps):
                if step == "turn":
                    starts = [len(s.posts) for s in servers]
                    connects = len(c.connects)
                    await _turn(proc)
                    tunnels = c.connects[connects:]
                    results.append(f"turn -> {_where(servers, starts)}"
                                   + (f" (C CONNECT x{len(tunnels)})" if tunnels else ""))
                elif step[0] == "switch":
                    results.append(f"switch {json.dumps(step[1])} -> {await _switch(proc, step[1], n)}")
                elif step[0] == "write":
                    for rel, kind in step[1].items():
                        _write_file(work, rel, kind, b, c)
                    await asyncio.sleep(RELOAD_WAIT_S)
                    kinds = sorted(set(step[1].values()))
                    results.append(f"rewrite {'+'.join(sorted(step[1]))} = {'/'.join(kinds)} "
                                   f"(waited {RELOAD_WAIT_S:g} s)")
        except (EOFError, asyncio.TimeoutError) as e:
            failure = type(e).__name__
        finally:
            proc.stdin.close()
            try:
                await asyncio.wait_for(proc.wait(), EXIT_TIMEOUT_S)
            except asyncio.TimeoutError:
                proc.kill()
                await proc.wait()
        holding = _files_holding(root, {
            "token": TOKEN.encode(), "pin": pin_json.encode(), "A url": a.url.encode(),
        })
        holding = {k: [p for p in v if p != "stderr.txt"] for k, v in holding.items()}
        files = ", ".join(f"{k}: {v or 'none'}" for k, v in holding.items())
        launch = "+".join(f"{rel}={kind}" for rel, kind in at_launch.items()) or "-"
        env_text = " ".join(f"{k}={v}" for k, v in extra.items()) or "-"
        row = (f"{name} | scrub {'yes' if scrub else 'no'} | env {env_text} | at launch {launch} | "
               f"pin {pin or 'no'} | " + "; ".join(results))
        row += f"\n    files under the root holding {files}"
        if failure is not None:
            with open(stderr_path, "rb") as f:
                tail = f.read()[-2000:].decode("utf-8", "replace")
            row += f"\n  {failure}; stderr tail:\n{tail}"
        return row
    finally:
        shutil.rmtree(root, ignore_errors=True)


async def main(selected: list[str]) -> int:
    binary = newest_binary()
    if binary is None:
        print("no cached claude binary")
        return 2
    a, b, c = Server("A"), Server("B"), Proxy()
    runners = [await _serve(a), await _serve(b)]
    proxy = await asyncio.start_server(c.handle, "127.0.0.1", 0)
    c.url = f"http://127.0.0.1:{proxy.sockets[0].getsockname()[1]}"
    try:
        print(f"claude {os.path.basename(binary)} ({binary}); bwrap on PATH: {shutil.which('bwrap') or 'no'}")
        print(f"A (gateway) {a.url}, B (elsewhere) {b.url}, C (proxy) {c.url}")
        for scenario in SCENARIOS:
            if selected and scenario[0] not in selected:
                continue
            print(await run_scenario(binary, a, b, c, scenario), flush=True)
    finally:
        proxy.close()
        for runner in runners:
            await runner.cleanup()
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main(sys.argv[1:])))
