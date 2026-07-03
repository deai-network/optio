"""Stand-in for the `cursor-agent` CLI during integration tests.

Scenario mode: reads the scenario name from the env var
``FAKE_CURSOR_SCENARIO`` (default ``happy``) and runs a deterministic script
of optio.log writes + sleeps + (optionally) deliverable writes. Stays alive
until DONE or ERROR has been emitted; the framework signals SIGTERM to
terminate the wrapping tmux/ttyd tree at that point.

ACP mode: when the argv carries the ``acp`` subcommand (``cursor-agent
[--model M] [--force] acp``), runs a minimal ACP (JSON-RPC 2.0 over stdio)
responder instead — adapted from fake_grok's ``_run_acp_stdio``.

Adapted from optio-grok's ``fake_grok.py``.
"""

import argparse
import json
import os
import re
import sys
import time
from pathlib import Path


SCENARIOS = ("happy", "deliverable", "error", "resume", "seed", "seed_rotate")


def _log(line: str) -> None:
    log = Path.cwd() / "optio.log"
    with log.open("a", encoding="utf-8") as fh:
        fh.write(line.rstrip("\n") + "\n")
        fh.flush()


def _scenario_happy() -> None:
    time.sleep(0.05)
    _log("STATUS: 10% fake cursor alive")
    time.sleep(0.05)
    _log("STATUS: 50% pretending to work")
    time.sleep(0.05)
    _log("DONE: scenario completed")
    time.sleep(30.0)


def _scenario_deliverable() -> None:
    workdir = Path.cwd()
    (workdir / "deliverables").mkdir(exist_ok=True)
    (workdir / "deliverables" / "greeting.txt").write_text(
        "hello from fake cursor\n", encoding="utf-8",
    )
    time.sleep(0.05)
    _log("DELIVERABLE: ./deliverables/greeting.txt")
    time.sleep(0.05)
    _log("DONE")
    time.sleep(30.0)


def _scenario_error() -> None:
    time.sleep(0.05)
    _log("ERROR: scenario asked for failure")
    time.sleep(30.0)


def _cursor_home() -> Path:
    """The per-task cursor state dir (``<workdir>/home/.cursor``).

    The launcher sets ``HOME=<workdir>/home``, so ``$HOME/.cursor`` lives
    INSIDE the workdir and anything written here is captured by the workdir
    snapshot and restored on resume — exactly like real cursor-agent's chat
    store.
    """
    home = os.environ.get("HOME") or str(Path.cwd() / "home")
    return Path(home) / ".cursor"


def _scenario_resume() -> None:
    """Model cursor's cwd-keyed chat persistence for the resume test.

    Records every launch's cursor flags to
    ``<HOME>/.cursor/fake_cursor_argv.jsonl`` (append-only, one JSON array per
    launch) and drops a one-time seed marker. After a workdir restore the seed
    run's line survives, so the file carries one line per launch — proving the
    restore happened and revealing whether ``--continue`` was passed on the
    resumed launch.
    """
    ch = _cursor_home()
    ch.mkdir(parents=True, exist_ok=True)
    with (ch / "fake_cursor_argv.jsonl").open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(sys.argv[1:]) + "\n")
        fh.flush()
    marker = ch / "seed_marker.txt"
    if not marker.exists():
        marker.write_text("SEEDED\n", encoding="utf-8")
    time.sleep(0.05)
    _log("STATUS: 10% resume-scenario alive")
    time.sleep(0.05)
    _log("DONE: resume scenario completed")
    time.sleep(30.0)


def _cursor_config_dir() -> Path:
    """The per-task cursor credential dir (``<workdir>/home/.config/cursor``).

    The launcher sets ``XDG_CONFIG_HOME=<workdir>/home/.config``, so this dir
    lives INSIDE the workdir — exactly where real cursor-agent keeps its
    ``auth.json`` (``status`` reads it, ``logout`` deletes it).
    """
    xdg = os.environ.get("XDG_CONFIG_HOME")
    if xdg:
        return Path(xdg) / "cursor"
    home = os.environ.get("HOME") or str(Path.cwd() / "home")
    return Path(home) / ".config" / "cursor"


def _scenario_seed() -> None:
    """Model cursor's logged-in identity for the Stage-3 seed tests.

    Two roles, distinguished by whether ``auth.json`` is already present at
    launch:

    * CONSUME (seed already merged in): the seed engine planted
      ``home/.config/cursor/auth.json`` before launch. Record that fact via a
      deliverable so the test can assert the seed reached the workdir before
      cursor started.
    * CAPTURE (fresh login): no auth yet, so write a fake logged-in identity
      (auth.json + cli-config.json) under the per-task home. Teardown capture
      then stores it as a reusable seed.
    """
    cfg_dir = _cursor_config_dir()
    cfg_dir.mkdir(parents=True, exist_ok=True)
    auth = cfg_dir / "auth.json"
    if auth.exists():
        workdir = Path.cwd()
        (workdir / "deliverables").mkdir(exist_ok=True)
        (workdir / "deliverables" / "seed_present.txt").write_text(
            "SEED_PRESENT\n", encoding="utf-8",
        )
        # Real cursor-agent would block on the interactive workspace-trust gate
        # unless the marker was pre-planted; record that it reached the workdir.
        slug = re.sub(r"[^A-Za-z0-9]+", "-", str(workdir)).strip("-")
        marker = _cursor_home() / "projects" / slug / ".workspace-trusted"
        if marker.exists():
            (workdir / "deliverables" / "trust_present.txt").write_text(
                "TRUST_PRESENT\n", encoding="utf-8",
            )
            _log("DELIVERABLE: ./deliverables/trust_present.txt")
        # CURSOR_DATA_DIR must be a SHORT symlink into <workdir>/home/.cursor,
        # else real cursor falls back to an ungranted /tmp/.cursor (EACCES).
        dd = os.environ.get("CURSOR_DATA_DIR", "")
        if (dd and os.path.islink(dd)
                and os.path.realpath(dd) == os.path.realpath(str(_cursor_home()))):
            (workdir / "deliverables" / "datadir_present.txt").write_text(
                "DATADIR_PRESENT\n", encoding="utf-8",
            )
            _log("DELIVERABLE: ./deliverables/datadir_present.txt")
        time.sleep(0.05)
        _log("DELIVERABLE: ./deliverables/seed_present.txt")
    else:
        auth.write_text(
            json.dumps({
                "accessToken": "fake-access-token",
                "refreshToken": "fake-refresh-token",
            }),
            encoding="utf-8",
        )
        ch = _cursor_home()
        ch.mkdir(parents=True, exist_ok=True)
        (ch / "cli-config.json").write_text(
            json.dumps({"version": 1, "editor": {"vimMode": False}}),
            encoding="utf-8",
        )
    time.sleep(0.05)
    _log("STATUS: 10% seed scenario alive")
    time.sleep(0.05)
    _log("DONE: seed scenario completed")
    time.sleep(30.0)


def _rotate_auth(cfg_dir: Path, new_refresh: str) -> None:
    """Rotate the refreshToken in ``<XDG_CONFIG_HOME>/cursor/auth.json``,
    modelling the refresh-token rotation a real cursor-agent may perform on
    token use (what the credential watcher must save back). Cursor's auth.json
    is a flat ``accessToken``/``refreshToken`` object (unlike grok's
    per-account map)."""
    auth = cfg_dir / "auth.json"
    try:
        data = json.loads(auth.read_text(encoding="utf-8"))
    except (FileNotFoundError, ValueError):
        data = {}
    data["refreshToken"] = new_refresh
    auth.write_text(json.dumps(data), encoding="utf-8")


def _scenario_seed_rotate() -> None:
    """CONSUME role that rotates the refresh token mid-session.

    The seed engine planted ``home/.config/cursor/auth.json`` before launch;
    this run rotates its refreshToken (as real cursor-agent would on a token
    refresh), so the session's teardown save-back must write the rotated
    auth.json back into the seed. Used by the Stage-4 lease/save-back session
    test."""
    cfg_dir = _cursor_config_dir()
    cfg_dir.mkdir(parents=True, exist_ok=True)
    _rotate_auth(cfg_dir, "ROTATED-INSESSION")
    time.sleep(0.05)
    _log("STATUS: 10% rotate scenario alive")
    time.sleep(0.05)
    _log("DONE: rotate scenario completed")
    time.sleep(30.0)


def _scenario_probe(prompt: str) -> int:
    """One-shot headless probe (``cursor-agent -p "<prompt>" --trust``) for
    verify_and_refresh.

    Mode via ``FAKE_CURSOR_PROBE`` (default ``alive``):
      * ``alive`` — rotate the refresh token (as a live cursor-agent would)
        and print the challenge answer to stdout; exit 0.
      * ``dead``  — print an auth error and exit 1 (no answer token).
      * ``echo``  — echo the prompt back verbatim and exit 1 (proves a prompt-
        echoing error path does not false-positive: the answer token is absent
        from the prompt).
    """
    mode = os.environ.get("FAKE_CURSOR_PROBE", "alive").strip()
    if mode == "dead":
        print("Error: Unauthorized (invalid_grant)", flush=True)
        return 1
    if mode == "echo":
        print(f"cannot process request: {prompt}", flush=True)
        return 1
    cfg_dir = _cursor_config_dir()
    cfg_dir.mkdir(parents=True, exist_ok=True)
    _rotate_auth(cfg_dir, "ROTATED-BY-PROBE")
    print("The capital of France is Paris.", flush=True)
    # ``alive_badexit`` proves the verdict is stdout-only: the answer is
    # present but the process exits non-zero, and the seed must still be alive.
    return 3 if mode == "alive_badexit" else 0


def _acp_send(obj: dict) -> None:
    sys.stdout.write(json.dumps(obj) + "\n")
    sys.stdout.flush()


def _acp_notify_update(session_id: str, update: dict) -> None:
    _acp_send({
        "jsonrpc": "2.0", "method": "session/update",
        "params": {"sessionId": session_id, "update": update},
    })


def _run_acp_stdio() -> int:
    """Fake ``cursor-agent … acp`` — a minimal ACP (JSON-RPC 2.0) responder.

    Implements the wire pinned in optio_cursor/conversation.py (grok-pinned
    shapes; cursor's unauthenticated handshake verified live):
      * ``initialize`` / ``session/new`` handshake.
      * ``session/prompt`` → an ``agent_message_chunk`` notification then the
        turn-end response ``{stopReason:"end_turn"}``. Replies are numbered
        ``reply-N`` per prompt (so an auto-start kickoff shifts the caller's
        first message to ``reply-2``, mirroring fake_grok).
      * Permission scenario: a prompt whose text contains ``TOOL`` emits a
        ``tool_call`` update + a ``session/request_permission`` request, blocks
        for the client's answer, then reports ``tool-ran`` (an allow option was
        selected) or ``tool-denied`` (rejected/cancelled).
      * ``session/cancel`` notification is accepted (no ack).

    ``FAKE_CURSOR_EXIT_AFTER=N`` makes the process exit non-zero after N
    prompt turns, modelling an unexpected crash for the session-failure test.
    """
    session_id = "fake-cursor-session"
    exit_after = int(os.environ.get("FAKE_CURSOR_EXIT_AFTER", "0") or "0")
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
                "protocolVersion": 1,
                "agentCapabilities": {
                    "loadSession": True,
                    "promptCapabilities": {"image": True},
                    "sessionCapabilities": {"list": {}},
                },
                "authMethods": [{"id": "cursor_login"}]}})
        elif method == "session/new":
            _acp_send({"jsonrpc": "2.0", "id": mid, "result": {
                "sessionId": session_id, "models": []}})
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
                                   {"optionId": "allow-once", "name": "Yes", "kind": "allow_once"},
                                   {"optionId": "reject-once", "name": "No", "kind": "reject_once"}]}})
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
            # Notification: a real cursor-agent would abort the in-flight
            # turn. The fake's turns are instantaneous, so nothing to abort.
            continue
        # Any other message (agent notifications the client shouldn't send,
        # unadvertised-capability errors, …) is ignored.


def main() -> int:
    # ACP conversation mode: `cursor-agent [--model M] [--force] acp`.
    # Detected before argparse so the acp positional doesn't trip the option
    # parser.
    if "acp" in sys.argv[1:]:
        return _run_acp_stdio()

    parser = argparse.ArgumentParser()
    parser.add_argument("--version", action="store_true")
    parser.add_argument("-p", "--print", dest="print_mode", action="store_true")
    parser.add_argument("--trust", action="store_true")
    parser.add_argument("--model", default=None)
    parser.add_argument("--sandbox", default=None)
    args, _unknown = parser.parse_known_args()
    if args.version:
        print("2026.07.01-fake")
        return 0
    if args.print_mode:
        # Headless probe: the prompt is the remaining positional argument.
        prompt = _unknown[0] if _unknown else ""
        return _scenario_probe(prompt)
    scenario = os.environ.get("FAKE_CURSOR_SCENARIO", "happy").strip()
    if scenario not in SCENARIOS:
        print(f"unknown FAKE_CURSOR_SCENARIO={scenario!r}", file=sys.stderr)
        return 2
    {
        "happy": _scenario_happy,
        "deliverable": _scenario_deliverable,
        "error": _scenario_error,
        "resume": _scenario_resume,
        "seed": _scenario_seed,
        "seed_rotate": _scenario_seed_rotate,
    }[scenario]()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
