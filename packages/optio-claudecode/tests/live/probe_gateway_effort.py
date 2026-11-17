"""Which "no effort" form the real Claude Code honours behind a gateway.

Not collected by pytest (not a ``test_*.py`` file). Run it on a host with a
cached claude binary, from the optio repo:

    python packages/optio-claudecode/tests/live/probe_gateway_effort.py

Only the loopback is used: an aiohttp server on 127.0.0.1 stands in for the
gateway, records every POST body and answers ``/v1/messages*`` with a 400, so
no turn ever reaches a model. Each scenario runs the newest cached claude in
optio's conversation mode (stream-json in and out) with a fresh HOME and the
gateway environment, optionally switches it live with one
``apply_flag_settings``, sends one user message and waits for its result. It
prints every key path naming ``effort`` or ``thinking`` in the bodies sent
for the model under test; the request's content (``messages``, ``system``,
``tools``) is skipped, since only parameters matter. Both model ids are
unknown to Claude Code, so it treats them alike; "levels" and "plain" only
name the role each plays in optio's catalog.

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
PLAIN, LEVELS = "gw/plain", "gw/levels"
UNSET = {"CLAUDE_CODE_EFFORT_LEVEL": "unset"}
AUTO = {"CLAUDE_CODE_EFFORT_LEVEL": "auto"}
PROBE_ERROR = {"type": "error", "error": {"type": "invalid_request_error", "message": "probe"}}
NOT_FOUND = {"type": "error", "error": {"type": "not_found_error", "message": "probe"}}
_CONTENT = {"messages", "system", "tools"}

# (name, extra env, launch model, launch effort, live switch, model under test)
SCENARIOS = [
    ("A", {}, PLAIN, None, None, PLAIN),
    ("B", {}, LEVELS, "high", {"model": PLAIN}, PLAIN),
    ("C", {}, LEVELS, "high", {"model": PLAIN, "effortLevel": None}, PLAIN),
    ("D", UNSET, LEVELS, "high", None, LEVELS),
    ("E", UNSET, PLAIN, None, {"model": LEVELS, "effortLevel": "high"}, LEVELS),
    ("F", UNSET, LEVELS, "high", {"model": PLAIN}, PLAIN),
    # The other forms Claude Code documents: CLAUDE_CODE_EFFORT_LEVEL=auto,
    # and auto / unset as a live effortLevel.
    ("G", AUTO, PLAIN, None, None, PLAIN),
    ("H", AUTO, LEVELS, "high", None, LEVELS),
    ("I", AUTO, PLAIN, None, {"model": LEVELS, "effortLevel": "high"}, LEVELS),
    ("J", AUTO, LEVELS, "high", {"model": PLAIN}, PLAIN),
    ("K", {}, LEVELS, "high", {"model": PLAIN, "effortLevel": "auto"}, PLAIN),
    ("L", {}, LEVELS, "high", {"model": PLAIN, "effortLevel": "unset"}, PLAIN),
    # A level other than the default (high), at launch and live: whether an
    # explicit level reaches the wire at all.
    ("M", {}, LEVELS, "low", None, LEVELS),
    ("N", {}, PLAIN, None, {"model": LEVELS, "effortLevel": "low"}, LEVELS),
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


def effort_paths(body) -> list[str]:
    """``path=value`` for every key naming effort or thinking, content skipped."""
    out: list[str] = []

    def walk(node, path):
        if isinstance(node, dict):
            for key, value in node.items():
                if not path and key in _CONTENT:
                    continue
                here = f"{path}.{key}" if path else key
                if "effort" in key.lower() or "thinking" in key.lower():
                    out.append(f"{here}={json.dumps(value, separators=(',', ':'))}")
                else:
                    walk(value, here)
        elif isinstance(node, list):
            for i, value in enumerate(node):
                walk(value, f"{path}[{i}]")

    walk(body, "")
    return out


class Gateway:
    def __init__(self) -> None:
        self.posts: list[tuple[str, object]] = []

    async def handle(self, request: web.Request) -> web.Response:
        if request.method == "POST":
            raw = await request.read()
            try:
                body = json.loads(raw)
            except ValueError:
                body = None
            self.posts.append((request.path, body))
        if request.path.startswith("/v1/messages"):
            return web.json_response(PROBE_ERROR, status=400)
        return web.json_response(NOT_FOUND, status=404)


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


async def _drive(proc, switch) -> str:
    """The switch's answer ("-" without one); raises on a missing result."""
    answer = "-"
    if switch is not None:
        await _write(proc, {"type": "control_request", "request_id": "probe-1",
                            "request": {"subtype": "apply_flag_settings", "settings": switch}})
        obj = await asyncio.wait_for(_read_until(proc, lambda o: (
            o.get("type") == "control_response"
            and (o.get("response") or {}).get("request_id") == "probe-1")), TURN_TIMEOUT_S)
        response = obj["response"]
        answer = response.get("subtype") or "?"
        if answer == "error":
            answer = f"error: {response.get('error')}"
    await _write(proc, {"type": "user", "uuid": str(uuid.uuid4()), "message": {
        "role": "user", "content": [{"type": "text", "text": "Reply with the word ok."}]}})
    await asyncio.wait_for(_read_until(proc, lambda o: o.get("type") == "result"), TURN_TIMEOUT_S)
    return answer


async def run_scenario(binary, port, gateway, scenario) -> str:
    name, extra_env, model, effort, switch, under_test = scenario
    home = tempfile.mkdtemp(prefix=f"probe-effort-{name}-")
    try:
        work = os.path.join(home, "work")
        os.mkdir(work)
        env = {
            "HOME": home,
            "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
            "DISABLE_AUTOUPDATER": "1",
            "CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC": "1",
            "ANTHROPIC_BASE_URL": f"http://127.0.0.1:{port}",
            "ANTHROPIC_AUTH_TOKEN": "probe-token",
            "CLAUDE_CODE_ALWAYS_ENABLE_EFFORT": "1",
            **extra_env,
        }
        argv = [binary, "-p", "--input-format", "stream-json", "--output-format",
                "stream-json", "--verbose", "--model", model]
        if effort is not None:
            argv += ["--effort", effort]
        gateway.posts.clear()
        stderr_path = os.path.join(home, "stderr.txt")
        with open(stderr_path, "wb") as stderr:
            proc = await asyncio.create_subprocess_exec(
                *argv, cwd=work, env=env, stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE, stderr=stderr, limit=16 * 1024 * 1024)
        failure = None
        try:
            answer = await _drive(proc, switch)
        except (EOFError, asyncio.TimeoutError) as e:
            answer, failure = "?", type(e).__name__
        finally:
            proc.stdin.close()
            try:
                await asyncio.wait_for(proc.wait(), EXIT_TIMEOUT_S)
            except asyncio.TimeoutError:
                proc.kill()
                await proc.wait()
        bodies = [body for path, body in gateway.posts
                  if path.startswith("/v1/messages") and isinstance(body, dict)
                  and body.get("model") == under_test]
        seen = []
        for body in bodies:
            paths = effort_paths(body)
            if paths not in seen:
                seen.append(paths)
        found = " | ".join(", ".join(p) or "(none)" for p in seen) or "(no body)"
        sent = "yes" if any("effort" in p.split("=", 1)[0] for ps in seen for p in ps) else "no"
        env_text = " ".join(f"{k}={v}" for k, v in extra_env.items()) or "-"
        launch = f"--model {model}" + (f" --effort {effort}" if effort else "")
        row = (f"{name} | {env_text} | {launch} | {json.dumps(switch) if switch else '-'} "
               f"-> {answer} | {under_test} x{len(bodies)}: effort sent: {sent}; {found}")
        if failure is not None:
            with open(stderr_path, "rb") as f:
                tail = f.read()[-2000:].decode("utf-8", "replace")
            row += f"\n  {failure}; stderr tail:\n{tail}"
        return row
    finally:
        shutil.rmtree(home, ignore_errors=True)


async def main() -> int:
    binary = newest_binary()
    if binary is None:
        print("no cached claude binary")
        return 2
    gateway = Gateway()
    app = web.Application()
    app.router.add_route("*", "/{tail:.*}", gateway.handle)
    runner = web.AppRunner(app, access_log=None)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0)
    await site.start()
    port = runner.addresses[0][1]
    try:
        print(f"claude {os.path.basename(binary)} ({binary})")
        print("scenario | extra env | launch | apply_flag_settings -> answer | bodies for the model under test")
        for scenario in SCENARIOS:
            print(await run_scenario(binary, port, gateway, scenario), flush=True)
    finally:
        await runner.cleanup()
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
