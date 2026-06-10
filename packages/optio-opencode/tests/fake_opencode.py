"""Stand-in for `opencode web` during integration tests.

Usage::

    python fake_opencode.py --port <N> --scenario <name>

Behavior:

1. Binds a TCP socket on 127.0.0.1 on ``--port`` (or a free port if 0).
2. Prints exactly one line to stdout:
   ``Listening on http://127.0.0.1:<port>/``
3. Runs the named scenario: appends scripted lines to ``./optio.log``
   and writes scripted files to ``./deliverables/``, sleeping a little
   between steps.
4. Keeps the socket open (serves a trivial 200 OK on any request) until
   SIGTERM/SIGINT or the scenario completes.

Scenarios are driven by the CWD containing ``optio.log`` and
``deliverables/`` (optio-opencode's workdir convention).
"""

import argparse
import asyncio
import json
import os
import socket
import sys


# ---------------------------------------------------------------------------
# Early dispatch: import / export / --env-dump
# These subcommands are handled synchronously before the asyncio `web` path
# runs so they can be invoked from simple subprocess calls in tests.
# ---------------------------------------------------------------------------

def _handle_env_dump() -> None:
    """If --env-dump <path> is in sys.argv, write os.environ as JSON to <path>
    and remove those two arguments so subsequent parsing is not confused."""
    argv = sys.argv[1:]
    if "--env-dump" not in argv:
        return
    idx = argv.index("--env-dump")
    if idx + 1 >= len(argv):
        sys.exit("--env-dump requires a path argument")
    dump_path = argv[idx + 1]
    with open(dump_path, "w", encoding="utf-8") as fh:
        json.dump(dict(os.environ), fh)
    # Remove --env-dump <path> from sys.argv so the rest of the script sees
    # clean args.
    del sys.argv[sys.argv.index("--env-dump") : sys.argv.index("--env-dump") + 2]


def _cmd_import(path: str) -> None:
    """Read a JSON session file and store it in $OPENCODE_DB."""
    with open(path, encoding="utf-8") as fh:
        data = json.load(fh)
    db_path = os.environ.get("OPENCODE_DB")
    if not db_path:
        sys.exit("OPENCODE_DB is not set")
    with open(db_path, "w", encoding="utf-8") as fh:
        json.dump(data, fh)
    sys.exit(0)


def _cmd_export(session_id: str) -> None:
    """Write a minimal session JSON for session_id to stdout."""
    db_path = os.environ.get("OPENCODE_DB")
    if db_path and os.path.isfile(db_path):
        with open(db_path, encoding="utf-8") as fh:
            stored = json.load(fh)
        # If the stored data has the requested id, return it enriched;
        # otherwise fall through to the minimal stub.
        if stored.get("id") == session_id:
            print(json.dumps(stored), flush=True)
            sys.exit(0)
    # Minimal stub so callers always get a parseable response.
    print(json.dumps({"id": session_id, "messages": []}), flush=True)
    sys.exit(0)


# Run env-dump first (it may strip args), then check for import/export.
_handle_env_dump()

# Mirror the real opencode CLI's ``--version``: print a fake version and exit.
# Smart-install.sh calls ``opencode --version`` to decide whether the installed
# binary is up-to-date; without this branch, the fake falls through to the web
# scenario path and hangs forever, which in turn hangs ``ensure_opencode_installed``
# whenever it is called outside the protocol driver's tail-and-cancel loop.
if "--version" in sys.argv[1:]:
    print("0.0.0-fake")
    sys.exit(0)

if len(sys.argv) >= 2:
    _sub = sys.argv[1]
    if _sub == "import":
        if len(sys.argv) < 3:
            sys.exit("Usage: fake_opencode.py import <path>")
        _cmd_import(sys.argv[2])
    elif _sub == "export":
        if len(sys.argv) < 3:
            sys.exit("Usage: fake_opencode.py export <session-id>")
        _cmd_export(sys.argv[2])


SCENARIOS = {
    "happy": [
        ("log", "STATUS: starting"),
        ("sleep", 0.05),
        ("write", "deliverables/out.txt", "hello 42 blue"),
        ("log", "DELIVERABLE: ./deliverables/out.txt"),
        ("sleep", 0.05),
        ("log", "DONE: all good"),
    ],
    "status_percent": [
        ("log", "STATUS: 10% just starting"),
        ("sleep", 0.05),
        ("log", "STATUS: 100% finished"),
        ("log", "DONE"),
    ],
    "error": [
        ("log", "STATUS: trying"),
        ("sleep", 0.05),
        ("log", "ERROR: auth failed"),
    ],
    "no_done_then_exit": [
        ("log", "STATUS: halfway"),
        ("sleep", 0.05),
        ("exit", 0),  # Exit 0 before writing DONE — should become failed.
    ],
    "escape_path": [
        ("log", "DELIVERABLE: ../etc/passwd"),
        ("sleep", 0.05),
        ("log", "DONE"),
    ],
    "inside_workdir_not_deliverables": [
        ("write", "stray.txt", "hi"),
        ("log", "DELIVERABLE: ./stray.txt"),
        ("sleep", 0.05),
        ("log", "DONE"),
    ],
    "non_utf8": [
        ("write_bytes", "deliverables/bad.bin", b"\xff\xfe\x00\x01"),
        ("log", "DELIVERABLE: ./deliverables/bad.bin"),
        ("sleep", 0.05),
        ("log", "DONE"),
    ],
    "sleep_forever": [
        ("log", "STATUS: waiting to be cancelled"),
        ("sleep", 3600),
    ],
    "conversation": [
        ("sleep", 0.1),
        ("conv_event", {"type": "message.part.delta",
                        "properties": {"sessionID": "fake-session-id",
                                       "messageID": "m1", "partID": "p1",
                                       "delta": "Hello"}}),
        ("conv_event", {"type": "message.part.updated",
                        "properties": {"part": {"id": "p1", "messageID": "m1",
                                                "sessionID": "fake-session-id",
                                                "type": "text", "text": "Hello from fake"}}}),
        ("conv_event", {"type": "message.updated",
                        "properties": {"info": {"id": "m1",
                                                "sessionID": "fake-session-id",
                                                "role": "assistant",
                                                "time": {"created": 1, "completed": 2}}}}),
        ("conv_event", {"type": "session.status",
                        "properties": {"sessionID": "fake-session-id",
                                       "status": {"type": "idle"}}}),
        ("sleep", 3600),  # hold the server open; tests terminate the process
    ],
    "conversation_then_done": [
        ("sleep", 0.1),
        ("conv_event", {"type": "message.part.delta",
                        "properties": {"sessionID": "fake-session-id",
                                       "messageID": "m1", "partID": "p1",
                                       "delta": "Hello"}}),
        ("conv_event", {"type": "message.part.updated",
                        "properties": {"part": {"id": "p1", "messageID": "m1",
                                                "sessionID": "fake-session-id",
                                                "type": "text", "text": "Hello from fake"}}}),
        ("conv_event", {"type": "message.updated",
                        "properties": {"info": {"id": "m1",
                                                "sessionID": "fake-session-id",
                                                "role": "assistant",
                                                "time": {"created": 1, "completed": 2}}}}),
        ("conv_event", {"type": "session.status",
                        "properties": {"sessionID": "fake-session-id",
                                       "status": {"type": "idle"}}}),
        ("sleep", 0.5),
        ("log", "DONE: chat over"),
    ],
    "conversation_early_exit": [
        ("sleep", 0.3),
        ("exit", 1),
    ],
    "caller_message": [
        ("log", 'CALLER_MESSAGE: ping {"n": 1}'),
        ("sleep", 0.1),
        ("log", "DONE"),
    ],
    "client_message": [
        ("log", 'CLIENT_MESSAGE: notify {"msg": "hi"}'),
        ("sleep", 0.05),
        ("log", "DONE"),
    ],
}

# Conversation-surface state: events queued by scenarios for the /global/event
# SSE stream, and pending permission requests served by GET /permission.
CONV_EVENTS: list[dict] = []
PENDING_PERMISSIONS: list[dict] = []


def append_log(line: str) -> None:
    with open("optio.log", "a", encoding="utf-8") as fh:
        fh.write(line + "\n")
        fh.flush()


def _journal(kind: str, payload: dict) -> None:
    with open("conv_journal.jsonl", "a", encoding="utf-8") as f:
        f.write(json.dumps({"kind": kind, **payload}) + "\n")


async def run_scenario(name: str) -> int:
    steps = SCENARIOS[name]
    for step in steps:
        op = step[0]
        if op == "log":
            append_log(step[1])
        elif op == "sleep":
            await asyncio.sleep(step[1])
        elif op == "write":
            path = step[1]
            os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
            with open(path, "w", encoding="utf-8") as fh:
                fh.write(step[2])
        elif op == "write_bytes":
            path = step[1]
            os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
            with open(path, "wb") as fh:
                fh.write(step[2])
        elif op == "exit":
            return step[1]
        elif op == "conv_event":
            CONV_EVENTS.append(step[1])
        elif op == "permission_pending":
            PENDING_PERMISSIONS.append(step[1])
    # Scenario finished; hold open until killed.
    while True:
        await asyncio.sleep(3600)


async def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=0)
    parser.add_argument("--scenario", required=True)
    # Use parse_known_args so the shim can pass through args like `web` or
    # `--hostname=127.0.0.1` that the real opencode accepts but we don't need.
    args, _ = parser.parse_known_args()

    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock.bind(("127.0.0.1", args.port))
    sock.listen(16)
    port = sock.getsockname()[1]
    print(f"Listening on http://127.0.0.1:{port}/", flush=True)

    # HTTP responder.  For POST /session returns JSON with a fake id so
    # optio-opencode's pre-create-session step can parse it; everything else
    # gets a generic 200 OK so proxied requests don't break.  Each accepted
    # connection is serviced in its own task — otherwise the accept loop
    # blocks while a request is in flight, which causes RemoteDisconnected
    # errors when asyncssh's port-forwarder opens multiple connections in
    # quick succession.
    async def handle(conn: socket.socket) -> None:
        loop = asyncio.get_event_loop()
        try:
            # Read headers (everything up to and including the \r\n\r\n).
            buf = b""
            while b"\r\n\r\n" not in buf and len(buf) < 16384:
                chunk = await asyncio.wait_for(loop.sock_recv(conn, 4096), timeout=5.0)
                if not chunk:
                    break
                buf += chunk
            header_end = buf.find(b"\r\n\r\n")
            header_bytes = buf if header_end < 0 else buf[:header_end]
            body_so_far = b"" if header_end < 0 else buf[header_end + 4 :]
            # Drain the request body (Content-Length) so we don't leave
            # unread bytes in the kernel recv buffer — closing with unread
            # data triggers TCP RST on Linux instead of FIN, which the
            # client sees as RemoteDisconnected before our response arrives.
            clen = 0
            for line in header_bytes.split(b"\r\n"):
                if line.lower().startswith(b"content-length:"):
                    try:
                        clen = int(line.split(b":", 1)[1].strip())
                    except ValueError:
                        clen = 0
                    break
            remaining = max(0, clen - len(body_so_far))
            request_body = body_so_far
            while remaining > 0:
                chunk = await asyncio.wait_for(loop.sock_recv(conn, min(remaining, 4096)), timeout=5.0)
                if not chunk:
                    break
                request_body += chunk
                remaining -= len(chunk)
            try:
                parsed_body = json.loads(request_body) if request_body else {}
            except ValueError:
                parsed_body = {}

            first_line = header_bytes.split(b"\r\n", 1)[0] if header_bytes else b""
            req_parts = first_line.decode("latin-1").split(" ")
            method = req_parts[0] if req_parts else ""
            path = req_parts[1].split("?", 1)[0] if len(req_parts) > 1 else ""

            if method == "GET" and path == "/global/event":
                # SSE stream mirroring the real server's /global/event:
                # connection headers, a payload-only server.connected frame,
                # then poll CONV_EVENTS (per-connection index), wrapping each
                # event as {"directory", "project", "payload": {...}} — the
                # shape recorded in Task 8's fixtures — until the socket
                # closes (a failed send raises, falling through to close()).
                await loop.sock_sendall(
                    conn,
                    b"HTTP/1.1 200 OK\r\n"
                    b"Content-Type: text/event-stream\r\n"
                    b"Cache-Control: no-cache\r\n"
                    b"Connection: close\r\n\r\n"
                    b'data: {"payload":{"id":"evt_0","type":"server.connected","properties":{}}}\n\n',
                )
                sent = 0
                while True:
                    while sent < len(CONV_EVENTS):
                        wrapped = {
                            "directory": os.getcwd(),
                            "project": "global",
                            "payload": CONV_EVENTS[sent],
                        }
                        frame = f"data: {json.dumps(wrapped)}\n\n"
                        await loop.sock_sendall(conn, frame.encode())
                        sent += 1
                    await asyncio.sleep(0.05)

            is_session_post = (
                first_line.startswith(b"POST /session ")
                or first_line.startswith(b"POST /session?")
            )
            if is_session_post:
                body = b'{"id": "fake-session-id"}'
                ctype = b"application/json"
            elif method == "POST" and path.startswith("/session/") and path.endswith("/prompt_async"):
                _journal("prompt_async", {"sid": path.split("/")[2], "body": parsed_body})
                body = b"ok"
                ctype = b"text/plain"
            elif method == "POST" and path.startswith("/session/") and path.endswith("/abort"):
                _journal("abort", {"sid": path.split("/")[2]})
                body = b"true"
                ctype = b"application/json"
            elif method == "GET" and path == "/config/providers":
                providers = {
                    "providers": [
                        {
                            "id": "opencode",
                            "name": "OpenCode Zen",
                            "models": {
                                "deepseek-v4-flash": {"id": "deepseek-v4-flash", "providerID": "opencode", "name": "DeepSeek V4 Flash"},
                                "big-pickle": {"id": "big-pickle", "providerID": "opencode", "name": "Big Pickle"},
                            },
                        },
                        {
                            "id": "xai",
                            "name": "xAI",
                            "models": {
                                "grok-5": {"id": "grok-5", "providerID": "xai", "name": "Grok 5"},
                            },
                        },
                    ],
                    "default": {"opencode": "big-pickle", "xai": "grok-5"},
                }
                body = json.dumps(providers).encode()
                ctype = b"application/json"
            elif method == "GET" and path == "/permission":
                body = json.dumps(PENDING_PERMISSIONS).encode()
                ctype = b"application/json"
            elif method == "POST" and path.startswith("/permission/") and path.endswith("/reply"):
                rid = path.split("/")[2]
                _journal("perm_reply", {"rid": rid, "body": parsed_body})
                PENDING_PERMISSIONS[:] = [
                    p for p in PENDING_PERMISSIONS if p.get("id") != rid
                ]
                CONV_EVENTS.append({
                    "type": "permission.replied",
                    "properties": {"sessionID": "fake-session-id",
                                   "requestID": rid,
                                   "reply": parsed_body.get("reply")},
                })
                body = b"true"
                ctype = b"application/json"
            else:
                body = b"ok"
                ctype = b"text/plain"
            headers = (
                b"HTTP/1.1 200 OK\r\n"
                b"Content-Type: " + ctype + b"\r\n"
                b"Content-Length: " + str(len(body)).encode() + b"\r\n"
                b"Connection: close\r\n\r\n"
            )
            await loop.sock_sendall(conn, headers + body)
            try:
                conn.shutdown(socket.SHUT_WR)
            except OSError:
                pass
        except Exception:
            pass
        finally:
            conn.close()

    async def serve() -> None:
        loop = asyncio.get_event_loop()
        while True:
            try:
                conn, _ = await loop.sock_accept(sock)
            except Exception:
                return
            conn.setblocking(False)
            asyncio.create_task(handle(conn))

    sock.setblocking(False)
    serve_task = asyncio.create_task(serve())
    try:
        code = await run_scenario(args.scenario)
        return code
    finally:
        serve_task.cancel()
        sock.close()


if __name__ == "__main__":
    try:
        sys.exit(asyncio.run(main()))
    except KeyboardInterrupt:
        sys.exit(0)
