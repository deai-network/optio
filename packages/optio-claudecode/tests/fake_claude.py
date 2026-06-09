"""Stand-in for the `claude` CLI during integration tests.

Two modes:

- **Scenario mode** (default, tmux/iframe-era): reads the scenario name
  from the env var ``FAKE_CLAUDE_SCENARIO`` (default ``happy``) and runs
  a deterministic script of optio.log writes + sleeps + (optionally)
  deliverable writes. Stays alive until DONE or ERROR has been emitted;
  the framework signals SIGTERM to terminate the wrapping ttyd process
  at that point.
- **Stream-json mode** (conversation-era): activated when
  ``--input-format`` appears in argv. Speaks bidirectional NDJSON on
  stdin/stdout — one scripted reply per user message; see
  ``run_stream_json_mode`` for the env knobs.
"""

import argparse
import json
import os
import sys
import time
from pathlib import Path


SCENARIOS = (
    "happy", "deliverable", "error", "long",
    "long_then_signaled", "idempotent_done", "seed",
)


def _log(line: str) -> None:
    log = Path.cwd() / "optio.log"
    with log.open("a", encoding="utf-8") as fh:
        fh.write(line.rstrip("\n") + "\n")
        fh.flush()


def _scenario_happy() -> None:
    time.sleep(0.05)
    _log("STATUS: 10% fake claude alive")
    time.sleep(0.05)
    _log("STATUS: 50% pretending to work")
    time.sleep(0.05)
    _log("DONE: scenario completed")
    time.sleep(30.0)


def _scenario_deliverable() -> None:
    workdir = Path.cwd()
    (workdir / "deliverables").mkdir(exist_ok=True)
    (workdir / "deliverables" / "greeting.txt").write_text(
        "hello from fake claude\n", encoding="utf-8",
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


def _scenario_long() -> None:
    # Stays alive indefinitely — used to test cancellation paths.
    while True:
        time.sleep(0.5)


def _record_argv(argv: list[str]) -> None:
    """Record the argv claude was launched with, so resume tests can
    assert that ``--continue`` was passed. Written under the isolated
    HOME (``$HOME`` is ``<workdir>/home`` under HOME-isolation) so it
    travels in the session blob, not the plaintext workdir blob.

    Appends one JSON line per launch so multiple runs are observable.
    """
    home = os.environ.get("HOME")
    if not home:
        return
    target = Path(home) / ".claude" / "fake_claude_argv.json"
    target.parent.mkdir(parents=True, exist_ok=True)
    with target.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(argv) + "\n")
        fh.flush()


def _scenario_long_then_signaled() -> None:
    # Emit a STATUS so the dashboard sees life, then stay alive
    # indefinitely until SIGTERM/SIGKILL from the framework.
    _log("STATUS: 10% long-running, awaiting signal")
    while True:
        time.sleep(0.5)


def _scenario_idempotent_done() -> None:
    # Emits the same DONE line as `happy`; used across two runs to verify
    # the agent's perspective of continuity survives capture+restore.
    # Also write a claude transcript file under the isolated HOME so that
    # _has_transcript() returns True for resumed sessions (keeps the
    # passes-continue resume test green).
    home = os.environ.get("HOME")
    if home:
        transcript = Path(home) / ".claude" / "projects" / "resumed" / "session.jsonl"
        transcript.parent.mkdir(parents=True, exist_ok=True)
        transcript.write_text('{"type":"message"}', encoding="utf-8")
    time.sleep(0.05)
    _log("STATUS: 10% resumed claude alive")
    time.sleep(0.05)
    _log("DONE: scenario completed")
    time.sleep(30.0)


def _scenario_seed() -> None:
    """Plant a representative environment under the isolated HOME so seed
    capture has INCLUDE files to tar and EXCLUDE files to skip, then DONE.

    `$HOME` is `<workdir>/home` under HOME-isolation. The `.claude.json`
    `projects` map is keyed to the run's cwd so the consume-time rekey has
    a single entry to rewrite.
    """
    home = os.environ.get("HOME")
    if home:
        claude = Path(home) / ".claude"
        (claude / "plugins" / "marketplace").mkdir(parents=True, exist_ok=True)
        (claude / "projects" / "session-x").mkdir(parents=True, exist_ok=True)
        # INCLUDE (environment)
        (claude / ".credentials.json").write_text('{"token": "abc"}', encoding="utf-8")
        (claude / "settings.json").write_text('{"theme": "dark"}', encoding="utf-8")
        (claude / "mcp-needs-auth-cache.json").write_text("{}", encoding="utf-8")
        (claude / "plugins" / "marketplace" / "p.json").write_text("{}", encoding="utf-8")
        # EXCLUDE (session / transcript) — must NOT travel in the seed
        (claude / "projects" / "session-x" / "transcript.jsonl").write_text(
            '{"msg": "secret-transcript"}', encoding="utf-8",
        )
        (claude / "history.jsonl").write_text("h\n", encoding="utf-8")
        # .claude.json with a single projects entry keyed to the run cwd.
        # Under CLAUDE_CONFIG_DIR=<home>/.claude it lives inside .claude/ (real
        # claude's location), not the old home root.
        (claude / ".claude.json").write_text(
            json.dumps({
                "userID": "u1",
                "oauthAccount": {"email": "x@y.z"},
                "projects": {str(Path.cwd()): {"allowedTools": ["Bash"]}},
            }),
            encoding="utf-8",
        )
    time.sleep(0.05)
    _log("STATUS: 10% configuring environment")
    time.sleep(0.05)
    _log("DONE: seed environment ready")
    time.sleep(30.0)


def run_stream_json_mode(argv: list[str]) -> int:
    """Bidirectional NDJSON fake: one scripted reply per user message.

    Env knobs:
      FAKE_CLAUDE_REPLY          — reply text template; '{n}' = turn number
                                   (default 'reply-{n}')
      FAKE_CLAUDE_PERMISSION     — '1': before the first result, emit a
                                   can_use_tool control_request and wait for
                                   the control_response; the decision is
                                   echoed into the result text.
      FAKE_CLAUDE_EXIT_AFTER     — int: exit(7) after that many results
                                   (simulates unexpected death).
    """
    def emit(obj):
        sys.stdout.write(json.dumps(obj) + "\n")
        sys.stdout.flush()

    reply_tpl = os.environ.get("FAKE_CLAUDE_REPLY", "reply-{n}")
    want_permission = os.environ.get("FAKE_CLAUDE_PERMISSION") == "1"
    exit_after = int(os.environ.get("FAKE_CLAUDE_EXIT_AFTER", "0"))
    session_id = "fake-session-0000"
    emit({"type": "system", "subtype": "init", "session_id": session_id,
          "model": "fake-model", "cwd": os.getcwd()})
    n = 0
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        msg = json.loads(line)
        if msg.get("type") == "control_request":
            sub = (msg.get("request") or {}).get("subtype")
            if sub == "interrupt":
                emit({"type": "control_response", "response": {
                    "subtype": "success", "request_id": msg.get("request_id"),
                }})
                emit({"type": "result", "subtype": "error_during_execution",
                      "result": "", "session_id": session_id, "is_error": True})
                n += 1
            continue
        if msg.get("type") != "user":
            continue
        n += 1
        decision_note = ""
        if want_permission and n == 1:
            emit({"type": "control_request", "request_id": "perm-1",
                  "request": {"subtype": "can_use_tool", "tool_name": "Bash",
                              "input": {"command": "echo hi"}}})
            for resp_line in sys.stdin:
                resp = json.loads(resp_line)
                if resp.get("type") == "control_response":
                    inner = (resp.get("response") or {}).get("response") or {}
                    decision_note = f" perm:{inner.get('behavior')}"
                    break
        text = reply_tpl.format(n=n) + decision_note
        emit({"type": "assistant", "message": {
            "role": "assistant", "content": [{"type": "text", "text": text}],
        }, "session_id": session_id})
        emit({"type": "result", "subtype": "success", "result": text,
              "session_id": session_id, "is_error": False,
              "total_cost_usd": 0.0})
        if exit_after and n >= exit_after:
            return 7
    return 0


def main() -> int:
    if "--input-format" in sys.argv:
        return run_stream_json_mode(sys.argv)
    parser = argparse.ArgumentParser()
    parser.add_argument("--version", action="store_true")
    parser.add_argument("--permission-mode", default=None)
    parser.add_argument("--allowed-tools", default=None)
    parser.add_argument("--disallowed-tools", default=None)
    parser.add_argument("--print", default=None, nargs="?", const="")
    args, _unknown = parser.parse_known_args()
    if args.version:
        print("2.1.153 (Claude Code) [fake_claude.py]")
        return 0
    scenario = os.environ.get("FAKE_CLAUDE_SCENARIO", "happy").strip()
    if scenario not in SCENARIOS:
        print(f"unknown FAKE_CLAUDE_SCENARIO={scenario!r}", file=sys.stderr)
        return 2
    _record_argv(sys.argv[1:])
    {
        "happy": _scenario_happy,
        "deliverable": _scenario_deliverable,
        "error": _scenario_error,
        "long": _scenario_long,
        "long_then_signaled": _scenario_long_then_signaled,
        "idempotent_done": _scenario_idempotent_done,
        "seed": _scenario_seed,
    }[scenario]()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
