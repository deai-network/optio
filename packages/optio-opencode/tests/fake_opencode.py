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
import os
import socket
import sys


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
}


def append_log(line: str) -> None:
    with open("optio.log", "a", encoding="utf-8") as fh:
        fh.write(line + "\n")
        fh.flush()


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
            while remaining > 0:
                chunk = await asyncio.wait_for(loop.sock_recv(conn, min(remaining, 4096)), timeout=5.0)
                if not chunk:
                    break
                remaining -= len(chunk)

            first_line = header_bytes.split(b"\r\n", 1)[0] if header_bytes else b""
            is_session_post = (
                first_line.startswith(b"POST /session ")
                or first_line.startswith(b"POST /session?")
            )
            if is_session_post:
                body = b'{"id": "fake-session-id"}'
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
