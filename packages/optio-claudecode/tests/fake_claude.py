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

# The model list the real CLI (2.1.268) answers the stream-json control request
# ``initialize`` with, verbatim (an empty, logged-out HOME; no --model). Tests
# import it as the expected catalog.
CLI_MODELS = [
    {"value": "default", "resolvedModel": "claude-opus-5[1m]",
     "displayName": "Default (recommended)",
     "description": "Use the default model (currently Opus 5 (1M context)) \u00b7 $5/$25 per Mtok",
     "supportsEffort": True, "supportedEffortLevels": ["low", "medium", "high", "xhigh", "max"],
     "supportsAdaptiveThinking": True, "supportsFastMode": True, "supportsAutoMode": True},
    {"value": "opus[1m]", "resolvedModel": "claude-opus-5[1m]",
     "displayName": "Opus (1M context)",
     "description": "Opus 5 with 1M context \u00b7 Best for everyday, complex tasks \u00b7 $5/$25 per Mtok",
     "supportsEffort": True, "supportedEffortLevels": ["low", "medium", "high", "xhigh", "max"],
     "supportsAdaptiveThinking": True, "supportsFastMode": True, "supportsAutoMode": True},
    {"value": "sonnet", "resolvedModel": "claude-sonnet-5",
     "displayName": "Sonnet",
     "description": "Sonnet 5 \u00b7 Efficient for routine tasks \u00b7 $2/$10 per Mtok",
     "supportsEffort": True, "supportedEffortLevels": ["low", "medium", "high", "xhigh", "max"],
     "supportsAdaptiveThinking": True, "supportsAutoMode": True},
    {"value": "haiku", "resolvedModel": "claude-haiku-4-5-20251001",
     "displayName": "Haiku",
     "description": "Haiku 4.5 \u00b7 Fastest for quick answers \u00b7 $1/$5 per Mtok"},
]


def _cli_list() -> "list[dict]":
    """CLI_MODELS, or the list in the file FAKE_CLAUDE_MODELS_FILE names (a
    test changing what the CLI offers between runs)."""
    path = os.environ.get("FAKE_CLAUDE_MODELS_FILE")
    return json.loads(Path(path).read_text(encoding="utf-8")) if path else CLI_MODELS


def cli_models_for(model_arg: "str | None") -> "tuple[list[dict], str]":
    """(the initialize model list, the model system/init reports) for a
    launch with ``--model model_arg``, as the real CLI does it: an alias runs
    its resolvedModel; a value that is neither an alias nor some alias's
    resolvedModel (a pinned full id) runs as given and is appended to the list
    as an entry of its own; no --model runs the default."""
    listed = [dict(m) for m in _cli_list()]
    if model_arg and not any(model_arg in (m["value"], m["resolvedModel"]) for m in listed):
        listed.append({"value": model_arg, "resolvedModel": model_arg,
                       "displayName": model_arg, "description": "Custom model"})
    wanted = model_arg or "default"
    running = next((m["resolvedModel"] for m in listed if m["value"] == wanted), wanted)
    return listed, running


def _transcript_model() -> "str | None":
    """The model the newest transcript ended on (its last ``message.model``):
    what the real CLI runs on ``--continue`` without ``--model``."""
    config_dir = (os.environ.get("CLAUDE_CONFIG_DIR")
                  or os.path.join(os.environ.get("HOME", ""), ".claude"))
    files = sorted(Path(config_dir, "projects").glob("*/*.jsonl"),
                   key=lambda f: f.stat().st_mtime)
    model = None
    for line in files[-1].read_text(encoding="utf-8").splitlines() if files else []:
        try:
            msg = json.loads(line).get("message")
        except (ValueError, AttributeError):
            continue
        if isinstance(msg, dict) and isinstance(msg.get("model"), str):
            model = msg["model"]
    return model


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
        (claude / ".credentials.json").write_text(
            '{"claudeAiOauth": {"refreshToken": "abc", "accessToken": "abc"}}',
            encoding="utf-8",
        )
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
      FAKE_CLAUDE_HOLD_TURN      — '1': after a user message, hold the turn
                                   open and block (no deadline) until a
                                   genuine interrupt control_request arrives
                                   on stdin, then answer it and end the turn
                                   (Fix 27 I7: widens the pending-turn window
                                   for a cancellation test — real claude is
                                   not instantaneous either; without this the
                                   reply races the test's own cancel()).
                                   Event-driven, not timed: nothing else is
                                   sent while holding, so a caller that never
                                   interrupts blocks forever here — bounded
                                   from the outside by the product's own
                                   GRACEFUL_INTERRUPT_TIMEOUT_S (which kills
                                   this process), not by a fake sleep.
      FAKE_CLAUDE_INITIALIZE     — 'error-fresh': answer the control request
                                   initialize with an error (as a CLI without
                                   it would) unless launched with --continue
                                   (a relaunch answers normally).
    """
    def emit(obj):
        sys.stdout.write(json.dumps(obj) + "\n")
        sys.stdout.flush()

    reply_tpl = os.environ.get("FAKE_CLAUDE_REPLY", "reply-{n}")
    want_permission = os.environ.get("FAKE_CLAUDE_PERMISSION") == "1"
    exit_after = int(os.environ.get("FAKE_CLAUDE_EXIT_AFTER", "0"))
    hold_turn = os.environ.get("FAKE_CLAUDE_HOLD_TURN") == "1"
    session_id = "fake-session-0000"
    # Permission mode as the real CLI reports and switches it (verified on
    # 2.1.268): init names the launch mode; set_permission_mode answers, then
    # announces the new mode in a system/status event; bypassPermissions only
    # when the process was launched able to bypass.
    mode = (argv[argv.index("--permission-mode") + 1]
            if "--permission-mode" in argv else "default")
    bypass_ok = (mode == "bypassPermissions"
                 or "--allow-dangerously-skip-permissions" in argv
                 or "--dangerously-skip-permissions" in argv)
    # The model: --model resolved the way the real CLI does (cli_models_for);
    # the stream-json control request `initialize` answers with the list.
    # --continue without --model keeps the model the transcript ended on.
    model_arg = argv[argv.index("--model") + 1] if "--model" in argv else None
    if model_arg is None and "--continue" in argv:
        model_arg = _transcript_model()
    models, running_model = cli_models_for(model_arg)
    emit({"type": "system", "subtype": "init", "session_id": session_id,
          "model": running_model, "cwd": os.getcwd(), "permissionMode": mode})
    n = 0
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        msg = json.loads(line)
        if msg.get("type") == "control_request":
            sub = (msg.get("request") or {}).get("subtype")
            if sub == "initialize" and (
                    os.environ.get("FAKE_CLAUDE_INITIALIZE") == "error-fresh"
                    and "--continue" not in argv):
                emit({"type": "control_response", "response": {
                    "subtype": "error", "request_id": msg.get("request_id"),
                    "error": "Unsupported control request subtype: initialize",
                }})
                continue
            if sub == "initialize":
                emit({"type": "control_response", "response": {
                    "subtype": "success", "request_id": msg.get("request_id"),
                    "response": {"commands": [], "agents": [], "models": models,
                                 "current_permission_mode": mode},
                }})
                continue
            if sub == "set_permission_mode":
                want = (msg.get("request") or {}).get("mode")
                if want == "bypassPermissions" and not bypass_ok:
                    emit({"type": "control_response", "response": {
                        "subtype": "error", "request_id": msg.get("request_id"),
                        "error": "Cannot set permission mode to bypassPermissions "
                                 "because the session was not launched with "
                                 "--dangerously-skip-permissions",
                    }})
                else:
                    mode = want
                    emit({"type": "control_response", "response": {
                        "subtype": "success", "request_id": msg.get("request_id"),
                        "response": {"mode": want},
                    }})
                    emit({"type": "system", "subtype": "status", "status": None,
                          "permissionMode": want, "session_id": session_id})
                continue
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
        if hold_turn:
            # Hold the turn open until a genuine interrupt control_request
            # arrives — event-driven, no deadline (Fix 27 review r1: a
            # wall-clock hold made the covering test's assertions racy under
            # host load). Anything else read while holding is ignored; EOF
            # (stdin closed, e.g. this process being killed after the
            # product's own GRACEFUL_INTERRUPT_TIMEOUT_S) ends the turn
            # early via ``for line in sys.stdin`` finishing next iteration.
            held = False
            for interrupt_line in sys.stdin:
                interrupt_line = interrupt_line.strip()
                if not interrupt_line:
                    continue
                interrupt_msg = json.loads(interrupt_line)
                if interrupt_msg.get("type") != "control_request":
                    continue
                interrupt_sub = (interrupt_msg.get("request") or {}).get("subtype")
                if interrupt_sub != "interrupt":
                    continue
                emit({"type": "control_response", "response": {
                    "subtype": "success", "request_id": interrupt_msg.get("request_id"),
                }})
                emit({"type": "result", "subtype": "error_during_execution",
                      "result": "", "session_id": session_id, "is_error": True})
                held = True
                break
            if held:
                continue
            # stdin closed without an interrupt arriving: nothing left to
            # reply to.
            return 0
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
