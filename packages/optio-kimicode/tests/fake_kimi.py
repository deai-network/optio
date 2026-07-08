"""Stand-in for the `kimi` CLI during integration tests.

Two roles, both driven by the invocation form:

* **iframe surface** — ``kimi server run [--foreground] [--host H] [--port P]``
  (and its ``kimi web`` alias). Binds a real loopback HTTP server, serves a
  stub SPA page, prints the ready banner (the access line the wrapper parses
  for the port + ``#token=`` bearer), then runs a deterministic optio.log
  scenario (STATUS/DELIVERABLE/DONE/ERROR) simulating the kimi agent doing the
  task, and blocks serving until SIGTERM. Readiness = the server port is up
  (the banner is printed only after the socket is listening).

  The HTTP server also answers the subset of kimi's REST surface the wrapper
  drives (all under the real ``/api/v1`` prefix, matching
  ``apps/kimi-web/src/api/config.ts``'s ``buildRestUrl``):

  - ``POST /api/v1/sessions`` — create a session. Mirrors kimi's
    ``createSessionRequestSchema`` (``{metadata:{cwd}}``) and envelope reply
    (``{code,msg,data:{id,...},request_id}``). Side effect: writes a minimal
    session store under ``$KIMI_CODE_HOME/sessions/`` so the wrapper's snapshot
    capture (session-present guard) has a real subtree to tar — exactly what
    real kimi does when a session is created.
  - ``GET /api/v1/sessions/{id}`` — fetch a session (envelope reply).
  - ``POST /api/v1/sessions/{id}/prompts`` — submit a prompt (kimi's
    ``promptSubmissionSchema``: ``{content:[{type:'text',text}]}``). Each
    submission is recorded to the external journal (``FAKE_KIMI_PROMPTS_LOG``)
    so tests can assert which prompts (auto-start kickoff, resume notice) the
    wrapper pushed.

  On startup the server journals a ``startup`` record carrying the count of
  files already present under ``$KIMI_CODE_HOME/sessions`` — a resumed launch
  sees a non-zero count (the restored session store), a fresh launch sees zero.

Adapted from optio-grok's ``fake_grok.py``. The key delta: kimi serves its own
web SPA, so this fake IS the web server (grok needed ttyd in front of a tmux
TUI). The ACP conversation mode, headless probe, and seed roles are later
stages and are not implemented here yet.
"""

import json
import os
import re
import signal
import socket
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path


SCENARIOS = (
    "happy", "deliverable", "error", "seed_rotate", "seed_rotate_on_term",
)

# The default bearer token the fake advertises in its ready banner. The
# wrapper parses it out of the ``#token=`` fragment and injects it into the
# iframe URL so the SPA authenticates to the loopback server.
_DEFAULT_TOKEN = "fake-kimi-token"

# Monotonic session-id source + a lock guarding both it and the journal (the
# HTTP server is threaded, so handlers run concurrently).
_LOCK = threading.Lock()
_SESSION_SEQ = {"n": 0}

_SESSION_ROUTE_RE = re.compile(r"^/api/v1/sessions/([^/]+)$")
_PROMPTS_ROUTE_RE = re.compile(r"^/api/v1/sessions/([^/]+)/prompts$")


def _log(line: str) -> None:
    log = Path.cwd() / "optio.log"
    with log.open("a", encoding="utf-8") as fh:
        fh.write(line.rstrip("\n") + "\n")
        fh.flush()


def _kimi_home() -> str:
    return os.environ.get("KIMI_CODE_HOME") or os.path.join(os.getcwd(), "home")


def _sessions_dir() -> str:
    return os.path.join(_kimi_home(), "sessions")


def _cred_path() -> str:
    """The rotating single-use credential file the seed watcher save-back tracks
    (``$KIMI_CODE_HOME/credentials/kimi-code.json``, verified from kimi source)."""
    return os.path.join(_kimi_home(), "credentials", "kimi-code.json")


def _rotate_cred(new_refresh: str) -> None:
    """Rotate ``refresh_token`` (and ``access_token``) in the planted
    ``kimi-code.json``, modelling kimi's single-use refresh-token rotation on a
    token refresh — the change the credential watcher/backstop must save back."""
    path = _cred_path()
    try:
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
    except (FileNotFoundError, ValueError):
        data = {}
    if not isinstance(data, dict):
        data = {}
    data["refresh_token"] = new_refresh
    data["access_token"] = f"access-{new_refresh}"
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(data, fh)


def _journal(kind: str, **payload: object) -> None:
    """Append one JSON record to the external prompts/events journal.

    The path comes from ``FAKE_KIMI_PROMPTS_LOG`` — deliberately OUTSIDE the
    workdir so it survives teardown (which wipes the taskdir). No-op when the
    env var is unset (production / non-asserting tests)."""
    path = os.environ.get("FAKE_KIMI_PROMPTS_LOG")
    if not path:
        return
    record = {"kind": kind, **payload}
    with _LOCK:
        with open(path, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(record) + "\n")
            fh.flush()


def _count_session_files() -> int:
    total = 0
    for _root, _dirs, files in os.walk(_sessions_dir()):
        total += len(files)
    return total


def _iso_now() -> str:
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).isoformat()


def _record_session_prompt(session_id: str, text: str) -> None:
    """Mirror real kimi recording a submitted prompt into the session's
    state.json (``lastPrompt`` + ``updatedAt`` bump, and ``title`` on first
    prompt) so resume-side recovery can distinguish a real conversation from an
    empty/notice session."""
    state_path = os.path.join(_sessions_dir(), "wd_fake", session_id, "state.json")
    try:
        with open(state_path, encoding="utf-8") as fh:
            state = json.load(fh)
    except (OSError, ValueError):
        return
    state["lastPrompt"] = text
    state["updatedAt"] = _iso_now()
    if not state.get("title"):
        state["title"] = text[:60]
    with open(state_path, "w", encoding="utf-8") as fh:
        json.dump(state, fh)


def _write_session_store(session_id: str, cwd: str | None) -> None:
    """Materialise a minimal on-disk session store for ``session_id``.

    Mirrors real kimi writing ``$KIMI_CODE_HOME/sessions/<workDirKey>/<id>/``
    (``state.json`` + ``agents/main/wire.jsonl``) when a session is created, so
    the wrapper's snapshot capture has a real subtree, and its resume-side
    session-id recovery (``find … state.json``) can find the id."""
    session_dir = os.path.join(_sessions_dir(), "wd_fake", session_id)
    agents_dir = os.path.join(session_dir, "agents", "main")
    os.makedirs(agents_dir, exist_ok=True)
    # Match real kimi's state.json shape (title / lastPrompt / updatedAt): a
    # freshly-created session has an EMPTY lastPrompt (no turns yet); it is filled
    # in by _record_session_prompt when a prompt is submitted. Resume-side
    # recovery (_recover_session_id) keys on lastPrompt to tell a real
    # conversation from an empty/notice session.
    with open(os.path.join(session_dir, "state.json"), "w", encoding="utf-8") as fh:
        json.dump({
            "id": session_id, "cwd": cwd, "title": "", "lastPrompt": "",
            "createdAt": _iso_now(), "updatedAt": _iso_now(),
        }, fh)
    # Append-only wire log (empty is fine for the fake).
    open(os.path.join(agents_dir, "wire.jsonl"), "a", encoding="utf-8").close()
    # Keep session_index.jsonl consistent (real kimi's --continue/list read it).
    index_path = os.path.join(_sessions_dir(), "session_index.jsonl")
    with open(index_path, "a", encoding="utf-8") as fh:
        fh.write(json.dumps({
            "sessionId": session_id, "sessionDir": session_dir, "workDir": cwd,
        }) + "\n")


def _new_session(cwd: str | None) -> str:
    with _LOCK:
        _SESSION_SEQ["n"] += 1
        session_id = f"sess_{_SESSION_SEQ['n']:04d}"
    _write_session_store(session_id, cwd)
    _journal("create", id=session_id, cwd=cwd)
    return session_id


def _envelope(data: object) -> bytes:
    return json.dumps({
        "code": 0, "msg": "success", "data": data, "request_id": "fake-req",
    }).encode("utf-8")


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


def _scenario_seed_rotate() -> None:
    """CONSUME role that rotates the single-use refresh token mid-session, EAGERLY.

    The seed engine planted ``credentials/kimi-code.json`` before launch; this
    run rotates it on the disk right away (as real kimi does on a token
    refresh), then reaches DONE. With the watcher's poll interval left long, the
    rotation lands ONLY via the teardown finally backstop — proving the backstop
    fires on an early (clean) exit."""
    _rotate_cred("ROTATED-INSESSION")
    time.sleep(0.05)
    _log("STATUS: 10% rotate scenario alive")
    time.sleep(0.05)
    _log("DONE: rotate scenario completed")


def _scenario_seed_rotate_on_term() -> None:
    """CONSUME role that rotates the token ONLY inside its SIGTERM handler.

    Emits no DONE — it holds until torn down. The rotation is deferred to the
    ``_term`` handler (see ``_run_server``), so the rotated token reaches disk
    ONLY when the wrapper tears kimi down GRACEFULLY (SIGTERM + wait). An
    aggressive SIGKILL kills this process before the handler runs, leaving the
    original token on disk — which is exactly the race the seeded graceful
    teardown must avoid on cancel."""
    time.sleep(0.05)
    _log("STATUS: 10% rotate-on-term scenario alive (holding)")


_SCENARIOS = {
    "happy": _scenario_happy,
    "deliverable": _scenario_deliverable,
    "error": _scenario_error,
    "seed_rotate": _scenario_seed_rotate,
    "seed_rotate_on_term": _scenario_seed_rotate_on_term,
}


class _StubHandler(BaseHTTPRequestHandler):
    """Serves the stub SPA page for non-API GETs and the wrapper-driven subset
    of kimi's ``/api/v1`` REST surface for session create / get / prompt.

    ``protocol_version = 'HTTP/1.1'`` with an explicit ``Connection: close`` and
    a write-side ``shutdown`` per response: over the asyncssh local forward the
    wrapper drives (RemoteHost), a plain close with any unread bytes triggers a
    TCP RST that the client sees as ``RemoteDisconnected``; a Content-Length'd
    reply plus an orderly half-close makes the forward see a clean FIN instead
    (the same robustness optio-opencode's raw-socket fake hand-rolls)."""

    protocol_version = "HTTP/1.1"

    def _send(self, status: int, body: bytes, ctype: str) -> None:
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Connection", "close")
        self.end_headers()
        try:
            self.wfile.write(body)
            self.wfile.flush()
            self.connection.shutdown(socket.SHUT_WR)
        except OSError:
            pass

    def _read_body(self) -> dict:
        length = int(self.headers.get("Content-Length", 0) or 0)
        raw = self.rfile.read(length) if length else b""
        if not raw:
            return {}
        try:
            parsed = json.loads(raw)
            return parsed if isinstance(parsed, dict) else {}
        except ValueError:
            return {}

    def do_GET(self) -> None:  # noqa: N802 (stdlib naming)
        path = self.path.split("?", 1)[0]
        m = _SESSION_ROUTE_RE.match(path)
        if m:
            # GET /api/v1/sessions/{id} — envelope with a minimal session.
            self._send(200, _envelope({"id": m.group(1), "title": ""}),
                       "application/json")
            return
        if path.startswith("/api/"):
            self._send(200, _envelope(None), "application/json")
            return
        # SPA page — covers '/', the '/sessions/<id>' deep link, and any other
        # extension-less path (the daemon SPA-falls-back to index.html).
        body = b"<!doctype html><title>Kimi (fake)</title><h1>fake kimi web</h1>"
        self._send(200, body, "text/html; charset=utf-8")

    def do_POST(self) -> None:  # noqa: N802 (stdlib naming)
        path = self.path.split("?", 1)[0]
        body = self._read_body()

        if path == "/api/v1/sessions":
            metadata = body.get("metadata") if isinstance(body.get("metadata"), dict) else {}
            cwd = metadata.get("cwd") if isinstance(metadata, dict) else None
            session_id = _new_session(cwd if isinstance(cwd, str) else None)
            self._send(200, _envelope({
                "id": session_id, "title": "", "metadata": {"cwd": cwd},
            }), "application/json")
            return

        m = _PROMPTS_ROUTE_RE.match(path)
        if m:
            session_id = m.group(1)
            text = ""
            content = body.get("content")
            if isinstance(content, list):
                for part in content:
                    if isinstance(part, dict) and part.get("type") == "text":
                        text = str(part.get("text", ""))
                        break
            _journal("prompt", sid=session_id, text=text)
            _record_session_prompt(session_id, text)
            self._send(200, _envelope({
                "prompt_id": "p_fake", "user_message_id": "m_fake",
                "status": "running", "content": content or [],
                "created_at": "2026-07-03T00:00:00Z",
            }), "application/json")
            return

        # Any other POST: a generic envelope so proxied calls don't break.
        self._send(200, _envelope(None), "application/json")

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

    # Journal the session-store state visible at launch: a resumed launch sees
    # the restored subtree (>0 files), a fresh launch sees none.
    _journal("startup", session_files=_count_session_files())

    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()

    # Ready banner — the access line the wrapper parses for the actual port and
    # the `#token=` bearer. Printed only after the socket is listening, so it
    # doubles as the readiness signal (mirrors kimi's onReady banner).
    print(f"Local:    http://{host}:{actual_port}/#token={token}", flush=True)

    scenario = os.environ.get("FAKE_KIMI_SCENARIO", "happy").strip()

    def _term(_signum, _frame) -> None:
        # Graceful-flush hook: the ``seed_rotate_on_term`` scenario writes the
        # rotated single-use token to disk ONLY here, so it survives a graceful
        # SIGTERM teardown but is lost to an aggressive SIGKILL — letting the
        # seeded-cancel test distinguish the two.
        if scenario == "seed_rotate_on_term":
            try:
                _rotate_cred("ROTATED-ON-TERM")
            except Exception:
                pass
        try:
            httpd.shutdown()
        except Exception:
            pass
        sys.exit(0)

    signal.signal(signal.SIGTERM, _term)

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


def _acp_send(obj: dict) -> None:
    sys.stdout.write(json.dumps(obj) + "\n")
    sys.stdout.flush()


def _acp_notify_update(session_id: str, update: dict) -> None:
    _acp_send({
        "jsonrpc": "2.0", "method": "session/update",
        "params": {"sessionId": session_id, "update": update},
    })


def _run_acp_stdio() -> int:
    """Fake ``kimi acp`` — a minimal ACP (JSON-RPC 2.0) responder over stdio.

    Implements the wire pinned by reading the Kimi Code ``@moonshot-ai/
    acp-adapter`` source (server.ts / session.ts / approval.ts /
    config-options.ts):
      * ``initialize`` / ``session/new`` handshake. **KIMI DELTA**: session/new
        returns ``configOptions`` (the unified PLAN-D11 picker surface) — a
        single ``model`` select option — NOT grok/cursor's ``models`` block.
      * ``session/prompt`` → an ``agent_message_chunk`` notification then the
        turn-end response ``{stopReason:"end_turn"}``. Replies are numbered
        ``reply-N`` per prompt (so an auto-start kickoff shifts the caller's
        first message to ``reply-2``).
      * Permission scenario: a prompt whose text contains ``TOOL`` emits a
        ``tool_call`` update + a ``session/request_permission`` request, blocks
        for the client's answer, then reports ``tool-ran`` (an allow option was
        selected) or ``tool-denied`` (rejected/cancelled).
      * ``session/set_model`` request is acked with an empty result.
      * ``session/cancel`` notification is accepted (no ack).

    ``FAKE_KIMI_EXIT_AFTER=N`` makes the process exit non-zero after N prompt
    turns, modelling an unexpected crash for the session-failure test.

    ``FAKE_KIMI_ACP_FAIL_LAUNCH`` makes the process print a diagnostic to stderr
    and exit non-zero BEFORE answering ``initialize`` — modelling a hard exit at
    launch (bad binary / sandbox exec denial / missing runtime lib). Used to
    verify the wrapper folds that stderr into the raised launch error instead of
    surfacing a bare "process ended".
    """
    fail = os.environ.get("FAKE_KIMI_ACP_FAIL_LAUNCH", "").strip()
    if fail:
        sys.stderr.write(fail + "\n")
        sys.stderr.flush()
        return 3
    session_id = "fake-kimi-session"
    exit_after = int(os.environ.get("FAKE_KIMI_EXIT_AFTER", "0") or "0")
    turn = 0
    next_perm_id = 1000
    while True:
        line = sys.stdin.readline()
        if not line:
            return 0
        line = line.strip()
        if not line:
            continue
        try:
            msg = json.loads(line)
        except ValueError:
            continue
        method = msg.get("method")
        mid = msg.get("id")
        if method == "initialize":
            _acp_send({"jsonrpc": "2.0", "id": mid, "result": {
                "protocolVersion": 1, "agentCapabilities": {},
                "authMethods": []}})
        elif method == "session/new":
            _acp_send({"jsonrpc": "2.0", "id": mid, "result": {
                "sessionId": session_id,
                "configOptions": [{
                    "type": "select", "id": "model", "name": "Model",
                    "category": "model", "currentValue": "kimi-k2",
                    "options": [
                        {"value": "kimi-k2", "name": "Kimi K2"},
                        {"value": "kimi-k2-thinking", "name": "Kimi K2 Thinking"},
                    ],
                }]}})
        elif method == "session/set_model":
            _acp_send({"jsonrpc": "2.0", "id": mid, "result": {}})
        elif method == "session/prompt":
            turn += 1
            prompt = (msg.get("params") or {}).get("prompt") or []
            text = " ".join(p.get("text", "") for p in prompt)
            if "TOOL" in text:
                next_perm_id += 1
                _acp_notify_update(session_id, {
                    "sessionUpdate": "tool_call", "toolCallId": "tc",
                    "title": "Shell", "rawInput": {"command": "echo hi"}})
                _acp_send({"jsonrpc": "2.0", "id": next_perm_id,
                           "method": "session/request_permission",
                           "params": {"sessionId": session_id, "toolCall": {
                               "toolCallId": "tc", "kind": "execute",
                               "title": "Execute `echo hi`",
                               "rawInput": {"command": "echo hi"}},
                               "options": [
                                   {"optionId": "allow-once", "name": "Approve once", "kind": "allow_once"},
                                   {"optionId": "reject-once", "name": "Reject", "kind": "reject_once"}]}})
                # Block for the client's permission answer (next stdin line).
                answer_line = sys.stdin.readline()
                outcome = {}
                if answer_line.strip():
                    try:
                        outcome = (json.loads(answer_line).get("result") or {}).get("outcome") or {}
                    except ValueError:
                        outcome = {}
                allowed = (
                    outcome.get("outcome") == "selected"
                    and "allow" in (outcome.get("optionId") or "")
                )
                reply = "tool-ran" if allowed else "tool-denied"
                _acp_notify_update(session_id, {
                    "sessionUpdate": "agent_message_chunk",
                    "content": {"type": "text", "text": reply}})
                _acp_send({"jsonrpc": "2.0", "id": mid,
                           "result": {"stopReason": "end_turn"}})
            else:
                _acp_notify_update(session_id, {
                    "sessionUpdate": "agent_message_chunk",
                    "content": {"type": "text", "text": f"reply-{turn}"}})
                _acp_send({"jsonrpc": "2.0", "id": mid,
                           "result": {"stopReason": "end_turn"}})
            if exit_after and turn >= exit_after:
                return 7
        elif method == "session/cancel":
            # Notification: a real kimi would abort the in-flight turn. The
            # fake's turns are instantaneous, so nothing to abort.
            continue
        # Any other message (agent notifications the client shouldn't send,
        # unadvertised-capability errors, …) is ignored.


def _is_server_launch(argv: list[str]) -> bool:
    if "web" in argv:
        return True
    return "server" in argv and "run" in argv


def main() -> int:
    argv = sys.argv[1:]
    if "--version" in argv:
        print("kimi 0.1.0 (fake)")
        return 0
    # Identity probe: `kimi server run --help` — real kimi-code prints help and
    # exits 0 (it is how host_actions._is_kimicode distinguishes kimi-code from the
    # name-colliding Python kimi-cli, which has no `server`). The fake must answer
    # it FAST and NOT start the blocking server, else the probe hangs. Handled
    # before the server-launch route below.
    if "--help" in argv or "-h" in argv:
        if "server" in argv:
            print("Usage: kimi server run [--foreground] [--host H] [--port P]")
            return 0
        print("Usage: kimi [OPTIONS] COMMAND")
        return 0
    # ACP conversation mode: `kimi acp`. Detected before the server-launch
    # check so the `acp` subcommand routes to the JSON-RPC stdio responder.
    if "acp" in argv:
        return _run_acp_stdio()
    if _is_server_launch(argv):
        return _run_server(argv)
    print(f"fake_kimi: unsupported invocation {argv!r}", file=sys.stderr)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
