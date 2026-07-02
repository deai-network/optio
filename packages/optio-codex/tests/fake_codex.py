"""Stand-in for the `codex` CLI during integration tests."""

import datetime
import json
import os
import sys
import time
import uuid
from pathlib import Path


SCENARIOS = (
    "happy", "deliverable", "error",
    "exit_zero", "exit_nonzero", "long",
    "resume", "seed", "seed_rotate",
)


def _log(line: str) -> None:
    log = Path.cwd() / "optio.log"
    with log.open("a", encoding="utf-8") as fh:
        fh.write(line.rstrip("\n") + "\n")
        fh.flush()


def _scenario_happy() -> None:
    time.sleep(0.05)
    _log("STATUS: 10% fake codex alive")
    time.sleep(0.05)
    _log("STATUS: 50% pretending to work")
    time.sleep(0.05)
    _log("DONE: scenario completed")
    time.sleep(30.0)


def _scenario_deliverable() -> None:
    workdir = Path.cwd()
    (workdir / "deliverables").mkdir(exist_ok=True)
    (workdir / "deliverables" / "greeting.txt").write_text(
        "hello from fake codex\n", encoding="utf-8",
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


def _scenario_exit_zero() -> None:
    # Exits 0 WITHOUT writing DONE itself: the wrapper's shell payload must
    # append DONE (the exit-status channel, host_actions rc-branch).
    time.sleep(0.05)
    _log("STATUS: 50% about to exit cleanly")


def _scenario_exit_nonzero() -> None:
    # Exits 3 — the shell payload must append 'ERROR: codex exited 3'.
    time.sleep(0.05)
    _log("STATUS: 50% about to crash")
    raise SystemExit(3)


def _scenario_long() -> None:
    # Never finishes — for the cancellation test.
    _log("STATUS: 10% running until cancelled")
    time.sleep(600.0)


def _codex_home() -> Path:
    """The per-task CODEX_HOME (``<workdir>/home/.codex``) set by the launcher.

    Lives INSIDE the workdir, so anything written here is captured by the
    workdir snapshot and restored on resume — exactly like real codex's
    rollout store (``$CODEX_HOME/sessions``).
    """
    ch = os.environ.get("CODEX_HOME") or str(Path.cwd() / "home" / ".codex")
    return Path(ch)


def _rollouts(ch: Path) -> "list[Path]":
    sessions = ch / "sessions"
    if not sessions.is_dir():
        return []
    return sorted(sessions.rglob("rollout-*.jsonl"))


def _write_rollout(ch: Path) -> Path:
    """Create a plausible codex rollout JSONL for a NEW session.

    Real codex: ``$CODEX_HOME/sessions/YYYY/MM/DD/rollout-<ts>-<uuid>.jsonl``
    (UUIDv7; any UUID satisfies the wrapper's filename scan)."""
    now = datetime.datetime.now(datetime.timezone.utc)
    day_dir = (
        ch / "sessions" / now.strftime("%Y") / now.strftime("%m")
        / now.strftime("%d")
    )
    day_dir.mkdir(parents=True, exist_ok=True)
    session_id = str(uuid.uuid4())
    ts = now.strftime("%Y-%m-%dT%H-%M-%S")
    path = day_dir / f"rollout-{ts}-{session_id}.jsonl"
    path.write_text(
        json.dumps({
            "type": "session_meta",
            "payload": {"id": session_id, "cwd": str(Path.cwd())},
        }) + "\n",
        encoding="utf-8",
    )
    return path


def _scenario_resume() -> None:
    """Model codex's session-id-keyed rollout persistence for the resume test.

    Every launch appends its argv to ``$CODEX_HOME/fake_codex_argv.jsonl``
    (append-only; after a workdir restore the first run's line survives, so
    the file carries one line per launch — proving the restore worked and
    revealing whether the resumed launch led with ``resume <id>``).

    Fresh launch (argv does not start with ``resume``): writes a NEW
    rollout. Resumed launch: appends a turn to the newest EXISTING rollout —
    real ``codex resume <id>`` continues the same session, same id. Also
    plants exclusion-proof junk (packages/ blob, sqlite index) that the
    snapshot MUST drop.
    """
    ch = _codex_home()
    ch.mkdir(parents=True, exist_ok=True)
    with (ch / "fake_codex_argv.jsonl").open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(sys.argv[1:]) + "\n")
        fh.flush()
    if sys.argv[1:2] == ["resume"]:
        existing = _rollouts(ch)
        if existing:
            with existing[-1].open("a", encoding="utf-8") as fh:
                fh.write(
                    json.dumps({"type": "turn_context", "resumed": True}) + "\n"
                )
        else:
            _write_rollout(ch)
    else:
        _write_rollout(ch)
    # Junk the default workdir_exclude must drop (asserted by the test).
    (ch / "packages").mkdir(exist_ok=True)
    (ch / "packages" / "blob.bin").write_bytes(b"\x00" * 1024)
    (ch / "state.sqlite3").write_bytes(b"sqlite-junk")
    time.sleep(0.05)
    _log("STATUS: 10% resume-scenario alive")
    time.sleep(0.05)
    _log("DONE: resume scenario completed")
    time.sleep(30.0)


def _record_launch() -> None:
    """Durably record this launch's argv + the config.toml planted in
    CODEX_HOME at launch time.

    When ``FAKE_CODEX_RECORD`` names a path, append one JSON object per
    launch: ``{"argv": [...], "config_toml": <content|null>}``. The workdir
    is wiped on teardown, so this record (outside the workdir) is how tests
    assert launch-time facts — e.g. that the seeded config.toml carried the
    workdir pre-trust entry BEFORE codex started (Stage 3), and later which
    sandbox flags were passed (Stage 8). The fake ACCEPTS and otherwise
    IGNORES all flags — it enforces nothing.
    """
    dest = os.environ.get("FAKE_CODEX_RECORD")
    if not dest:
        return
    config_path = _codex_home() / "config.toml"
    try:
        config_toml = config_path.read_text(encoding="utf-8")
    except FileNotFoundError:
        config_toml = None
    with open(dest, "a", encoding="utf-8") as fh:
        fh.write(json.dumps({
            "argv": sys.argv[1:],
            "config_toml": config_toml,
        }) + "\n")
        fh.flush()


def _scenario_seed() -> None:
    """Model codex's logged-in identity for the Stage-3 seed tests.

    Two roles, distinguished by whether ``auth.json`` is already present at
    launch:

    * CONSUME (seed already merged in): the seed engine planted
      ``home/.codex/auth.json`` before launch. Record that fact via a
      deliverable so the test can assert the seed reached the workdir
      before codex started.
    * CAPTURE (fresh login): no auth yet, so write a fake logged-in
      ChatGPT-mode identity (auth.json + config.toml) under CODEX_HOME.
      Teardown capture then stores it as a reusable seed.
    """
    ch = _codex_home()
    ch.mkdir(parents=True, exist_ok=True)
    auth = ch / "auth.json"
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
                "auth_mode": "chatgpt",
                "tokens": {
                    "id_token": "fake-id",
                    "access_token": "fake-access",
                    "refresh_token": "fake-refresh",
                },
                "last_refresh": "2026-07-02T00:00:00Z",
            }),
            encoding="utf-8",
        )
        (ch / "config.toml").write_text('model = "gpt-5.5"\n', encoding="utf-8")
    time.sleep(0.05)
    _log("STATUS: 10% seed scenario alive")
    time.sleep(0.05)
    _log("DONE: seed scenario completed")
    time.sleep(30.0)


def _rotate_auth(ch: Path, new_refresh: str) -> None:
    """Rotate ``tokens.refresh_token`` in ``<CODEX_HOME>/auth.json``,
    modelling codex's single-use refresh-token rotation (manager.rs rewrites
    auth.json in place on refresh) — what the credential watcher must save
    back."""
    auth = ch / "auth.json"
    try:
        data = json.loads(auth.read_text(encoding="utf-8"))
    except (FileNotFoundError, ValueError):
        data = {}
    tokens = data.get("tokens")
    if isinstance(tokens, dict):
        tokens["refresh_token"] = new_refresh
    auth.write_text(json.dumps(data), encoding="utf-8")


def _scenario_seed_rotate() -> None:
    """CONSUME role that rotates the refresh token mid-session.

    The seed engine planted ``home/.codex/auth.json`` before launch; this
    run rotates its refresh_token (as real codex would on a token refresh),
    so the session's teardown save-back must write the rotated auth.json
    back into the seed. Used by the Stage-4 lease/save-back session test."""
    ch = _codex_home()
    ch.mkdir(parents=True, exist_ok=True)
    _rotate_auth(ch, "ROTATED-INSESSION")
    time.sleep(0.05)
    _log("STATUS: 10% rotate scenario alive")
    time.sleep(0.05)
    _log("DONE: rotate scenario completed")
    time.sleep(30.0)


def _scenario_probe(prompt: str) -> int:
    """One-shot headless probe (``codex exec --json … '<prompt>'``) for
    verify_and_refresh.

    Mode via ``FAKE_CODEX_PROBE`` (default ``alive``):
      * ``alive`` — rotate the refresh token (as a live codex would: its
        8-day proactive refresh / refresh-on-401 rewrites auth.json in
        place) and print exec-style JSONL carrying the challenge answer;
        exit 0.
      * ``dead``  — print an auth error and exit 1 (no answer token).
      * ``echo``  — echo the prompt back verbatim and exit 1 (proves a
        prompt-echoing error path does not false-positive: the answer token
        is absent from the prompt).
      * ``alive_badexit`` — answer present but exit 3 (stdout-only verdict).
      * ``env_apikey`` — models codex's API-key auth FALLBACK: alive iff an
        ambient ``OPENAI_API_KEY`` is present in the probe env (ignores the
        seed's auth.json entirely), else a dead auth error. verify must
        scrub OPENAI_API_KEY from the probe env, so a dead ChatGPT-mode seed
        stays dead even when the verifying host carries an ambient key.
    """
    mode = os.environ.get("FAKE_CODEX_PROBE", "alive").strip()
    if mode == "dead":
        print(json.dumps({"type": "error",
                          "message": "401 Unauthorized (invalid_grant)"}),
              flush=True)
        return 1
    if mode == "env_apikey":
        if os.environ.get("OPENAI_API_KEY"):
            print(json.dumps({"type": "item.completed", "item": {
                "type": "agent_message",
                "text": "The capital of France is Paris."}}), flush=True)
            return 0
        print(json.dumps({"type": "error",
                          "message": "401 Unauthorized (invalid_grant)"}),
              flush=True)
        return 1
    if mode == "echo":
        print(json.dumps({"type": "error",
                          "message": f"cannot process request: {prompt}"}),
              flush=True)
        return 1
    ch = _codex_home()
    ch.mkdir(parents=True, exist_ok=True)
    _rotate_auth(ch, "ROTATED-BY-PROBE")
    print(json.dumps({"type": "thread.started", "thread_id": "fake-thread"}),
          flush=True)
    print(json.dumps({"type": "item.completed", "item": {
        "type": "agent_message",
        "text": "The capital of France is Paris."}}), flush=True)
    print(json.dumps({"type": "turn.completed", "usage": {}}), flush=True)
    return 3 if mode == "alive_badexit" else 0


def _as_send(obj: dict) -> None:
    # The real wire omits the "jsonrpc" field (probed against 0.142.5).
    sys.stdout.write(json.dumps(obj) + "\n")
    sys.stdout.flush()


def _notify(method: str, params: dict) -> None:
    _as_send({"method": method, "params": params})


def _run_app_server() -> int:
    """Fake ``codex app-server`` — a minimal JSONL JSON-RPC responder.

    Implements the wire pinned by the Stage-6 probe/schemas:
      * ``initialize``/``initialized`` + ``account/read`` + ``model/list`` +
        ``thread/start``/``thread/resume`` handshake.
      * ``turn/start`` → ACK, ``turn/started``, an agentMessage item with two
        ``item/agentMessage/delta`` halves + ``item/completed``, then
        ``turn/completed``. Replies are numbered ``reply-N`` per turn (so an
        auto-start kickoff shifts the caller's first message to ``reply-2``).
      * Permission scenario: a turn whose text contains ``TOOL`` emits a
        commandExecution ``item/started`` + an
        ``item/commandExecution/requestApproval`` REQUEST, blocks for the
        client's answer, then reports ``tool-ran`` (decision accept*) or
        ``tool-denied`` (decline/cancel).
      * ``turn/interrupt`` → {} ACK + ``turn/completed`` status interrupted.

    ``FAKE_CODEX_EXIT_AFTER=N`` makes the process exit non-zero (7) after N
    turns, modelling an unexpected crash for the session-failure test.
    """
    thread_id = "fake-codex-thread"
    exit_after = int(os.environ.get("FAKE_CODEX_EXIT_AFTER", "0") or "0")
    turn = 0
    next_req_id = 1000
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
            _as_send({"id": mid, "result": {
                "userAgent": "codex/0.142.5-fake",
                "codexHome": os.environ.get("CODEX_HOME", ""),
                "platformFamily": "fake", "platformOs": "fake"}})
        elif method == "initialized":
            continue
        elif method == "account/read":
            _as_send({"id": mid, "result": {
                "account": {"type": "apikey"}, "requiresOpenaiAuth": False}})
        elif method == "model/list":
            _as_send({"id": mid, "result": {"data": [
                {"id": "gpt-5.5", "displayName": "GPT-5.5", "description": "",
                 "hidden": False, "isDefault": True, "model": "gpt-5.5",
                 "defaultReasoningEffort": "medium",
                 "supportedReasoningEfforts": []},
                {"id": "gpt-5.4-mini", "displayName": "GPT-5.4 Mini",
                 "description": "", "hidden": False, "isDefault": False,
                 "model": "gpt-5.4-mini", "defaultReasoningEffort": "medium",
                 "supportedReasoningEfforts": []},
            ], "nextCursor": None}})
        elif method in ("thread/start", "thread/resume"):
            params = msg.get("params") or {}
            if method == "thread/resume" and params.get("threadId"):
                thread_id = params["threadId"]
            _as_send({"id": mid, "result": {
                "thread": {"id": thread_id},
                "model": params.get("model") or "gpt-5.5"}})
            _notify("thread/started", {"thread": {"id": thread_id}})
        elif method == "turn/start":
            turn += 1
            turn_id = f"turn-{turn}"
            params = msg.get("params") or {}
            text = " ".join(
                p.get("text", "") for p in (params.get("input") or [])
                if isinstance(p, dict)
            )
            _as_send({"id": mid, "result": {"turn": {
                "id": turn_id, "status": "inProgress", "items": []}}})
            _notify("turn/started", {"threadId": thread_id, "turn": {
                "id": turn_id, "status": "inProgress", "items": []}})
            if "TOOL" in text:
                item = {"type": "commandExecution", "id": f"item-{turn}-tool",
                        "command": "echo hi", "cwd": "/w",
                        "status": "inProgress"}
                _notify("item/started", {"threadId": thread_id,
                                         "turnId": turn_id, "item": item,
                                         "startedAtMs": 0})
                next_req_id += 1
                _as_send({"id": next_req_id,
                          "method": "item/commandExecution/requestApproval",
                          "params": {"threadId": thread_id, "turnId": turn_id,
                                     "itemId": item["id"], "command": "echo hi",
                                     "cwd": "/w", "reason": None,
                                     "startedAtMs": 0}})
                # Block for the client's approval answer (next stdin line).
                answer_line = sys.stdin.readline()
                decision = None
                if answer_line.strip():
                    try:
                        decision = (json.loads(answer_line).get("result")
                                    or {}).get("decision")
                    except ValueError:
                        decision = None
                allowed = decision in ("accept", "acceptForSession")
                _notify("item/completed", {
                    "threadId": thread_id, "turnId": turn_id,
                    "item": dict(item, status="completed" if allowed else "declined"),
                    "completedAtMs": 0})
                reply = "tool-ran" if allowed else "tool-denied"
            else:
                reply = f"reply-{turn}"
            msg_item_id = f"item-{turn}-msg"
            _notify("item/started", {"threadId": thread_id, "turnId": turn_id,
                                     "item": {"type": "agentMessage",
                                              "id": msg_item_id, "text": ""},
                                     "startedAtMs": 0})
            half = max(1, len(reply) // 2)
            for piece in (reply[:half], reply[half:]):
                if piece:
                    _notify("item/agentMessage/delta", {
                        "threadId": thread_id, "turnId": turn_id,
                        "itemId": msg_item_id, "delta": piece})
            _notify("item/completed", {"threadId": thread_id,
                                       "turnId": turn_id,
                                       "item": {"type": "agentMessage",
                                                "id": msg_item_id,
                                                "text": reply},
                                       "completedAtMs": 0})
            _notify("turn/completed", {"threadId": thread_id, "turn": {
                "id": turn_id, "status": "completed", "items": []}})
            if exit_after and turn >= exit_after:
                return 7
        elif method == "turn/interrupt":
            _as_send({"id": mid, "result": {}})
            _notify("turn/completed", {"threadId": thread_id, "turn": {
                "id": (msg.get("params") or {}).get("turnId") or "turn-0",
                "status": "interrupted", "items": []}})
        elif mid is not None:
            _as_send({"id": mid, "error": {
                "code": -32601,
                "message": f"fake codex: unknown method {method}"}})


def main() -> int:
    import argparse

    # app-server conversation mode: `codex app-server`. Detected before
    # argparse so the positional doesn't trip the option parser.
    if "app-server" in sys.argv[1:]:
        return _run_app_server()

    # Headless probe mode: `codex exec --json [-s MODE] [--skip-git-repo-check]
    # [-C DIR] '<prompt>'`. Detected before argparse; the prompt is the last
    # positional argument.
    argv = sys.argv[1:]
    if argv and argv[0] == "exec":
        prompt = argv[-1] if len(argv) > 1 and not argv[-1].startswith("-") else ""
        return _scenario_probe(prompt)

    parser = argparse.ArgumentParser()
    parser.add_argument("--version", action="store_true")
    parser.add_argument("--model", default=None)
    args, _unknown = parser.parse_known_args()
    if args.version:
        print("codex 0.1.0 (fake)")
        return 0

    scenario = os.environ.get("FAKE_CODEX_SCENARIO", "happy").strip()
    if scenario not in SCENARIOS:
        print(f"unknown FAKE_CODEX_SCENARIO={scenario!r}", file=sys.stderr)
        return 2
    _record_launch()
    {
        "happy": _scenario_happy,
        "deliverable": _scenario_deliverable,
        "error": _scenario_error,
        "exit_zero": _scenario_exit_zero,
        "exit_nonzero": _scenario_exit_nonzero,
        "long": _scenario_long,
        "resume": _scenario_resume,
        "seed": _scenario_seed,
        "seed_rotate": _scenario_seed_rotate,
    }[scenario]()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())