"""Whether a settings file the agent can write sends Claude Code's gateway
calls to another host, and whether a ``--settings`` pin keeps them on the
gateway.

Not collected by pytest (not a ``test_*.py`` file). Run it on a host with a
cached claude binary, from the optio repo:

    python packages/optio-claudecode/tests/live/probe_gateway_settings_env.py

Only the loopback is used: two aiohttp servers on 127.0.0.1, A (the gateway,
the process's ``ANTHROPIC_BASE_URL``) and B (another host), record every POST
with its auth header and answer ``/v1/messages*`` with a 400, so no turn ever
reaches a model. Each scenario runs the newest cached claude in optio's
conversation mode (stream-json in and out) laid out as a gateway session is:
cwd ``<root>/work``, ``HOME=<root>/work/home``, a fresh TMPDIR, and the
gateway environment (with ``CLAUDE_CODE_SUBPROCESS_ENV_SCRUB=1`` in S0 only:
without bubblewrap on the host, claude does not start with it, and S0 records
that). Some settings files carry an ``env`` block whose
``ANTHROPIC_BASE_URL`` is B, at launch or written between two turns (then the
next turn waits ``RELOAD_WAIT_S`` for Claude Code's settings watcher). With
``pin`` the launch adds ``--settings <json>`` holding the gateway's
non-secret environment (never the token), as gateway mode does.

For each turn it prints which server got the turn's ``/v1/messages`` POSTs
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

# (name, files at launch {relpath: "B" | "empty"}, pin, steps). A step is
# "turn", ("write", {relpath: "B"}) or ("switch", apply_flag_settings settings).
# S0 runs with CLAUDE_CODE_SUBPROCESS_ENV_SCRUB=1, as gateway mode sets it;
# the others leave it out, since Claude Code 2.1.319 does not start with it on
# a host without bubblewrap (S0 shows the message).
SCENARIOS = [
    ("S0", {}, False, ["turn"]),
    ("S1", {}, False, ["turn"]),
    ("S2", {HOME_SETTINGS: "B"}, False, ["turn"]),
    ("S3", {PROJECT: "B"}, False, ["turn"]),
    ("S4", {LOCAL: "B"}, False, ["turn"]),
    ("S5", {HOME_LOCAL: "B"}, False, ["turn"]),
    ("S6", {f: "B" for f in AGENT_FILES}, True, ["turn"]),
    ("S7", {f: "B" for f in AGENT_FILES}, True, [("switch", {"model": MODEL}), "turn"]),
    ("S8", {f: "empty" for f in AGENT_FILES}, False,
     ["turn", ("write", {f: "B" for f in AGENT_FILES}), "turn"]),
    ("S9", {f: "empty" for f in AGENT_FILES}, True,
     ["turn", ("write", {f: "B" for f in AGENT_FILES}), "turn"]),
    ("S10", {}, False, ["turn", ("write", {f: "B" for f in AGENT_FILES}), "turn"]),
    ("S11", {}, True, ["turn", ("write", {f: "B" for f in AGENT_FILES}), "turn"]),
    ("S12", {f: "empty" for f in AGENT_FILES}, True,
     ["turn", ("switch", {"model": MODEL}), ("write", {f: "B" for f in AGENT_FILES}), "turn"]),
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


def _write_file(work: str, rel: str, kind: str, b: Server) -> None:
    path = os.path.join(work, rel)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    body = {} if kind == "empty" else {"env": {"ANTHROPIC_BASE_URL": b.url}}
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


def _where(a: Server, b: Server, a0: int, b0: int) -> str:
    out = []
    for server, start in ((a, a0), (b, b0)):
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


async def run_scenario(binary: str, a: Server, b: Server, scenario) -> str:
    name, at_launch, pin, steps = scenario
    root = tempfile.mkdtemp(prefix=f"probe-settings-{name}-")
    try:
        work = os.path.join(root, "work")
        tmp = os.path.join(root, "tmp")
        os.makedirs(os.path.join(work, "home"))
        os.mkdir(tmp)
        for rel, kind in at_launch.items():
            _write_file(work, rel, kind, b)
        scrub = name == "S0"
        env = {
            "HOME": os.path.join(work, "home"),
            "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
            "TMPDIR": tmp,
            "DISABLE_AUTOUPDATER": "1",
            "CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC": "1",
            "ANTHROPIC_AUTH_TOKEN": TOKEN,
            **gateway_env(a, scrub=scrub),
        }
        argv = [binary, "-p", "--input-format", "stream-json", "--output-format",
                "stream-json", "--verbose", "--model", MODEL]
        pin_json = json.dumps({"env": gateway_env(a, scrub=scrub)})
        if pin:
            argv += ["--settings", pin_json]
        stderr_path = os.path.join(root, "stderr.txt")
        with open(stderr_path, "wb") as stderr:
            proc = await asyncio.create_subprocess_exec(
                *argv, cwd=work, env=env, stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE, stderr=stderr, limit=16 * 1024 * 1024)
        results: list[str] = []
        failure = None
        try:
            for n, step in enumerate(steps):
                if step == "turn":
                    a0, b0 = len(a.posts), len(b.posts)
                    await _turn(proc)
                    results.append(f"turn -> {_where(a, b, a0, b0)}")
                elif step[0] == "switch":
                    results.append(f"switch {json.dumps(step[1])} -> {await _switch(proc, step[1], n)}")
                elif step[0] == "write":
                    for rel, kind in step[1].items():
                        _write_file(work, rel, kind, b)
                    await asyncio.sleep(RELOAD_WAIT_S)
                    results.append(f"rewrite {'+'.join(sorted(step[1]))} (waited {RELOAD_WAIT_S:g} s)")
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
        row = (f"{name} | scrub {'yes' if scrub else 'no'} | at launch {launch} | "
               f"pin {'yes' if pin else 'no'} | " + "; ".join(results))
        row += f"\n    files under the root holding {files}"
        if failure is not None:
            with open(stderr_path, "rb") as f:
                tail = f.read()[-2000:].decode("utf-8", "replace")
            row += f"\n  {failure}; stderr tail:\n{tail}"
        return row
    finally:
        shutil.rmtree(root, ignore_errors=True)


async def main() -> int:
    binary = newest_binary()
    if binary is None:
        print("no cached claude binary")
        return 2
    a, b = Server("A"), Server("B")
    runners = [await _serve(a), await _serve(b)]
    try:
        print(f"claude {os.path.basename(binary)} ({binary}); bwrap on PATH: {shutil.which('bwrap') or 'no'}")
        print(f"A (gateway) {a.url}, B (elsewhere) {b.url}")
        for scenario in SCENARIOS:
            print(await run_scenario(binary, a, b, scenario), flush=True)
    finally:
        for runner in runners:
            await runner.cleanup()
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
