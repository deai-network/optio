"""Stand-in for the `grok` CLI during integration tests.

Scenario mode: reads the scenario name from the env var
``FAKE_GROK_SCENARIO`` (default ``happy``) and runs a deterministic script
of optio.log writes + sleeps + (optionally) deliverable writes. Stays alive
until DONE or ERROR has been emitted; the framework signals SIGTERM to
terminate the wrapping tmux/ttyd tree at that point.

Adapted from optio-claudecode's ``fake_claude.py`` (scenario mode only;
the stream-json conversation mode is a later stage).
"""

import argparse
import json
import os
import sys
import time
from pathlib import Path


SCENARIOS = ("happy", "deliverable", "error", "resume", "seed", "seed_rotate")


def _record_launch() -> None:
    """Durably record this launch's argv + planted sandbox profile (Stage 8).

    When ``FAKE_GROK_RECORD`` names a path, append one JSON object per launch:
    ``{"argv": [...], "sandbox_toml": <content|null>}``. The workdir is wiped
    on teardown, so this record (outside the workdir) is how the wiring test
    asserts that ``--sandbox optio`` was passed AND that
    ``$GROK_HOME/sandbox.toml`` was planted before launch. The fake ACCEPTS
    and otherwise IGNORES ``--sandbox`` — it enforces nothing.
    """
    dest = os.environ.get("FAKE_GROK_RECORD")
    if not dest:
        return
    gh = os.environ.get("GROK_HOME") or str(Path.cwd() / "home" / ".grok")
    sandbox_path = Path(gh) / "sandbox.toml"
    try:
        sandbox_toml = sandbox_path.read_text(encoding="utf-8")
    except FileNotFoundError:
        sandbox_toml = None
    with open(dest, "a", encoding="utf-8") as fh:
        fh.write(json.dumps({
            "argv": sys.argv[1:],
            "sandbox_toml": sandbox_toml,
        }) + "\n")
        fh.flush()


def _log(line: str) -> None:
    log = Path.cwd() / "optio.log"
    with log.open("a", encoding="utf-8") as fh:
        fh.write(line.rstrip("\n") + "\n")
        fh.flush()


def _scenario_happy() -> None:
    time.sleep(0.05)
    _log("STATUS: 10% fake grok alive")
    time.sleep(0.05)
    _log("STATUS: 50% pretending to work")
    time.sleep(0.05)
    _log("DONE: scenario completed")
    time.sleep(30.0)


def _scenario_deliverable() -> None:
    workdir = Path.cwd()
    (workdir / "deliverables").mkdir(exist_ok=True)
    (workdir / "deliverables" / "greeting.txt").write_text(
        "hello from fake grok\n", encoding="utf-8",
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


def _grok_home() -> Path:
    """The per-task GROK_HOME (``<workdir>/home/.grok``) set by the launcher.

    This lives INSIDE the workdir, so anything written here is captured by the
    workdir snapshot and restored on resume — exactly like real grok's session
    store (``<GROK_HOME>/sessions``).
    """
    gh = os.environ.get("GROK_HOME") or str(Path.cwd() / "home" / ".grok")
    return Path(gh)


def _scenario_resume() -> None:
    """Model grok's cwd-keyed session persistence for the resume test.

    Records every launch's grok flags to ``<GROK_HOME>/fake_grok_argv.jsonl``
    (append-only, one JSON array per launch) and drops a one-time seed marker.
    After a workdir restore the seed run's line survives, so the file carries
    one line per launch — proving the restore happened and revealing whether
    ``-c`` (continue) was passed on the resumed launch.
    """
    gh = _grok_home()
    gh.mkdir(parents=True, exist_ok=True)
    with (gh / "fake_grok_argv.jsonl").open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(sys.argv[1:]) + "\n")
        fh.flush()
    marker = gh / "seed_marker.txt"
    if not marker.exists():
        marker.write_text("SEEDED\n", encoding="utf-8")
    time.sleep(0.05)
    _log("STATUS: 10% resume-scenario alive")
    time.sleep(0.05)
    _log("DONE: resume scenario completed")
    time.sleep(30.0)


def _scenario_seed() -> None:
    """Model grok's logged-in identity for the Stage-3 seed tests.

    Two roles, distinguished by whether ``auth.json`` is already present at
    launch:

    * CONSUME (seed already merged in): the seed engine planted
      ``home/.grok/auth.json`` before launch. Record that fact via a
      deliverable so the test can assert the seed reached the workdir before
      grok started.
    * CAPTURE (fresh login): no auth yet, so write a fake logged-in identity
      (auth.json + config.toml) under GROK_HOME. Teardown capture then stores
      it as a reusable seed.
    """
    gh = _grok_home()
    gh.mkdir(parents=True, exist_ok=True)
    auth = gh / "auth.json"
    if auth.exists():
        workdir = Path.cwd()
        (workdir / "deliverables").mkdir(exist_ok=True)
        (workdir / "deliverables" / "seed_present.txt").write_text(
            "SEED_PRESENT\n", encoding="utf-8",
        )
        time.sleep(0.05)
        _log("DELIVERABLE: ./deliverables/seed_present.txt")
    else:
        auth.write_text(
            json.dumps({
                "https://auth.x.ai::00000000-0000-0000-0000-000000000000": {
                    "key": "fake-key",
                    "refresh_token": "fake-refresh",
                    "expires_at": 9999999999,
                },
            }),
            encoding="utf-8",
        )
        (gh / "config.toml").write_text('model = "grok-fake"\n', encoding="utf-8")
    time.sleep(0.05)
    _log("STATUS: 10% seed scenario alive")
    time.sleep(0.05)
    _log("DONE: seed scenario completed")
    time.sleep(30.0)


def _rotate_auth(gh: Path, new_refresh: str) -> None:
    """Rotate the refresh_token in every account of ``<GROK_HOME>/auth.json``,
    modelling xAI's single-use refresh-token rotation that real grok performs
    on each token use (what the credential watcher must save back)."""
    auth = gh / "auth.json"
    try:
        data = json.loads(auth.read_text(encoding="utf-8"))
    except (FileNotFoundError, ValueError):
        data = {}
    for key, val in list(data.items()):
        if isinstance(val, dict):
            val["refresh_token"] = new_refresh
    auth.write_text(json.dumps(data), encoding="utf-8")


def _scenario_seed_rotate() -> None:
    """CONSUME role that rotates the refresh token mid-session.

    The seed engine planted ``home/.grok/auth.json`` before launch; this run
    rotates its refresh_token (as real grok would on a token refresh), so the
    session's teardown save-back must write the rotated auth.json back into the
    seed. Used by the Stage-4 lease/save-back session test."""
    gh = _grok_home()
    gh.mkdir(parents=True, exist_ok=True)
    _rotate_auth(gh, "ROTATED-INSESSION")
    time.sleep(0.05)
    _log("STATUS: 10% rotate scenario alive")
    time.sleep(0.05)
    _log("DONE: rotate scenario completed")
    time.sleep(30.0)


def _scenario_probe(prompt: str) -> int:
    """One-shot headless probe (``grok -p "<prompt>"``) for verify_and_refresh.

    Mode via ``FAKE_GROK_PROBE`` (default ``alive``):
      * ``alive`` — rotate the refresh token (as a live grok would) and print
        the challenge answer to stdout; exit 0.
      * ``dead``  — print an auth error and exit 1 (no answer token).
      * ``echo``  — echo the prompt back verbatim and exit 1 (proves a prompt-
        echoing error path does not false-positive: the answer token is absent
        from the prompt).
    """
    mode = os.environ.get("FAKE_GROK_PROBE", "alive").strip()
    if mode == "dead":
        print("Error: Unauthorized (invalid_grant)", flush=True)
        return 1
    if mode == "echo":
        print(f"cannot process request: {prompt}", flush=True)
        return 1
    gh = _grok_home()
    gh.mkdir(parents=True, exist_ok=True)
    _rotate_auth(gh, "ROTATED-BY-PROBE")
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
    """Fake ``grok agent … stdio`` — a minimal ACP (JSON-RPC 2.0) responder.

    Implements the wire pinned by the Stage-6 live probe:
      * ``initialize`` / ``session/new`` handshake.
      * ``session/prompt`` → an ``agent_message_chunk`` notification then the
        turn-end response ``{stopReason:"end_turn"}``. Replies are numbered
        ``reply-N`` per prompt (so an auto-start kickoff shifts the caller's
        first message to ``reply-2``, mirroring fake_claude).
      * Permission scenario: a prompt whose text contains ``TOOL`` emits a
        ``tool_call`` update + a ``session/request_permission`` request, blocks
        for the client's answer, then reports ``tool-ran`` (an allow option was
        selected) or ``tool-denied`` (rejected/cancelled).
      * ``session/cancel`` notification is accepted (no ack).

    ``FAKE_GROK_EXIT_AFTER=N`` makes the process exit non-zero after N prompt
    turns, modelling an unexpected crash for the session-failure test.
    """
    session_id = "fake-grok-session"
    exit_after = int(os.environ.get("FAKE_GROK_EXIT_AFTER", "0") or "0")
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
                "protocolVersion": 1, "agentCapabilities": {}, "authMethods": []}})
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
            # Notification: a real grok would abort the in-flight turn. The
            # fake's turns are instantaneous, so nothing to abort.
            continue
        # Any other message (agent notifications the client shouldn't send,
        # unadvertised-capability errors, …) is ignored.


def main() -> int:
    # ACP conversation mode: `grok agent [flags] stdio`. Detected before
    # argparse so the agent/stdio positionals don't trip the option parser.
    argv = sys.argv[1:]
    if "agent" in argv and "stdio" in argv:
        _record_launch()
        return _run_acp_stdio()

    parser = argparse.ArgumentParser()
    parser.add_argument("--version", action="store_true")
    parser.add_argument("-p", "--print", dest="print_mode", action="store_true")
    parser.add_argument("--permission-mode", default=None)
    parser.add_argument("--model", default=None)
    args, _unknown = parser.parse_known_args()
    if args.version:
        print("grok 0.2.77 (fake)")
        return 0
    if args.print_mode:
        # Headless probe: the prompt is the remaining positional argument.
        prompt = _unknown[0] if _unknown else ""
        return _scenario_probe(prompt)
    # Iframe/scenario launch: record argv + planted sandbox profile (Stage 8).
    _record_launch()
    scenario = os.environ.get("FAKE_GROK_SCENARIO", "happy").strip()
    if scenario not in SCENARIOS:
        print(f"unknown FAKE_GROK_SCENARIO={scenario!r}", file=sys.stderr)
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
