"""Stand-in for the `kimi` CLI during integration tests.

Two roles, both driven by the invocation form:

* **iframe surface** — ``kimi server run [--foreground] [--host H] [--port P]``
  (and its ``kimi web`` alias). Binds a real loopback HTTP server, serves a
  stub SPA page, prints the ready banner (the access line the wrapper parses
  for the port + ``#token=`` bearer), then runs a deterministic optio.log
  scenario (STATUS/DELIVERABLE/DONE/ERROR) simulating the kimi agent doing the
  task, and blocks serving until SIGTERM. Readiness = the server port is up
  (the banner is printed only after the socket is listening).

Adapted from optio-grok's ``fake_grok.py``. The key delta: kimi serves its own
web SPA, so this fake IS the web server (grok needed ttyd in front of a tmux
TUI). The ACP conversation mode, headless probe, seed, and resume roles are
later stages and are not implemented here yet.
"""

import os
import signal
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path


SCENARIOS = ("happy", "deliverable", "error")

# The default bearer token the fake advertises in its ready banner. The
# wrapper parses it out of the ``#token=`` fragment and injects it into the
# iframe URL so the SPA authenticates to the loopback server.
_DEFAULT_TOKEN = "fake-kimi-token"


def _log(line: str) -> None:
    log = Path.cwd() / "optio.log"
    with log.open("a", encoding="utf-8") as fh:
        fh.write(line.rstrip("\n") + "\n")
        fh.flush()


def _scenario_happy() -> None:
    time.sleep(0.05)
    _log("STATUS: 10% fake kimi alive")
    time.sleep(0.05)
    _log("STATUS: 50% pretending to work")
    time.sleep(0.05)
    _log("DONE: scenario completed")


def _scenario_deliverable() -> None:
    workdir = Path.cwd()
    (workdir / "deliverables").mkdir(exist_ok=True)
    (workdir / "deliverables" / "greeting.txt").write_text(
        "hello from fake kimi\n", encoding="utf-8",
    )
    time.sleep(0.05)
    _log("DELIVERABLE: ./deliverables/greeting.txt")
    time.sleep(0.05)
    _log("DONE")


def _scenario_error() -> None:
    time.sleep(0.05)
    _log("ERROR: scenario asked for failure")


_SCENARIOS = {
    "happy": _scenario_happy,
    "deliverable": _scenario_deliverable,
    "error": _scenario_error,
}


class _StubHandler(BaseHTTPRequestHandler):
    """Serves a tiny stub SPA page for any GET, so the iframe/tunnel has a real
    page to load. Real kimi serves the ``apps/kimi-web`` bundle here."""

    def do_GET(self) -> None:  # noqa: N802 (stdlib naming)
        body = b"<!doctype html><title>Kimi (fake)</title><h1>fake kimi web</h1>"
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args) -> None:  # silence request logging
        return


def _parse_host_port(argv: list[str]) -> tuple[str, int]:
    """Extract ``--host``/``--port`` from the server argv.

    Defaults mirror real kimi: host ``127.0.0.1``, port ``58627``. ``--port 0``
    requests an ephemeral port (the banner then reports the real one)."""
    host = "127.0.0.1"
    port = 58627
    for i, tok in enumerate(argv):
        if tok == "--host" and i + 1 < len(argv):
            host = argv[i + 1]
        elif tok == "--port" and i + 1 < len(argv):
            try:
                port = int(argv[i + 1])
            except ValueError:
                pass
    return host, port


def _run_server(argv: list[str]) -> int:
    host, port = _parse_host_port(argv)
    httpd = ThreadingHTTPServer((host, port), _StubHandler)
    actual_port = httpd.server_address[1]
    token = os.environ.get("FAKE_KIMI_TOKEN", _DEFAULT_TOKEN)

    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()

    # Ready banner — the access line the wrapper parses for the actual port and
    # the `#token=` bearer. Printed only after the socket is listening, so it
    # doubles as the readiness signal (mirrors kimi's onReady banner).
    print(f"Local:    http://{host}:{actual_port}/#token={token}", flush=True)

    def _term(_signum, _frame) -> None:
        try:
            httpd.shutdown()
        except Exception:
            pass
        sys.exit(0)

    signal.signal(signal.SIGTERM, _term)

    scenario = os.environ.get("FAKE_KIMI_SCENARIO", "happy").strip()
    if scenario not in _SCENARIOS:
        print(f"unknown FAKE_KIMI_SCENARIO={scenario!r}", file=sys.stderr)
        return 2
    # Simulate the kimi agent driving the task via the optio.log protocol.
    _SCENARIOS[scenario]()

    # Keep serving the SPA until the wrapper tears us down (SIGTERM). The task's
    # completion is signalled through optio.log above, not by this process
    # exiting — real kimi's server is long-lived too.
    while True:
        time.sleep(3600)


def _is_server_launch(argv: list[str]) -> bool:
    if "web" in argv:
        return True
    return "server" in argv and "run" in argv


def main() -> int:
    argv = sys.argv[1:]
    if "--version" in argv:
        print("kimi 0.1.0 (fake)")
        return 0
    if _is_server_launch(argv):
        return _run_server(argv)
    print(f"fake_kimi: unsupported invocation {argv!r}", file=sys.stderr)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
