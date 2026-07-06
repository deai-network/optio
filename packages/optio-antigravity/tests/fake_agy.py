"""Stand-in for the ``agy`` (Antigravity CLI) binary during integration tests.

Two surfaces, matching the two real ``agy`` usages optio drives:

* **Scenario / TUI mode** — a bare launch (``agy [flags]``, optionally with a
  trailing positional prompt) runs a deterministic script of ``optio.log``
  writes (``STATUS:`` / ``DELIVERABLE:`` / ``DONE`` / ``ERROR``) then parks,
  modelling the interactive TUI embedded under ttyd (Stage 0). The scenario is
  chosen by ``FAKE_AGY_SCENARIO`` (default ``happy``).

* **Print / conversation mode** — ``agy -p/--print [--conversation <id>]
  [--model <m>] [--dangerously-skip-permissions] <prompt>`` models one
  synthetic conversation turn in the REAL ``agy`` layout (captured 2026-07-06):
  turn 1 (no ``--conversation``) MINTS a uuid, records
  ``$HOME/.gemini/antigravity-cli/cache/last_conversations.json`` =
  ``{<workdir>: <uuid>}``, and appends real-schema lines (``USER_INPUT`` +
  ``PLANNER_RESPONSE``) to
  ``$HOME/.gemini/antigravity-cli/brain/<uuid>/.system_generated/logs/transcript.jsonl``;
  a ``--conversation <uuid>`` turn appends to that SAME file. It echoes the
  canned reply on stdout.

Adapted from optio-grok's ``fake_grok.py``. The real ``agy`` has **no** ACP /
stream-json surface (design §1), so — unlike fake_grok — there is no JSON-RPC
responder; the conversation is transcript-file-driven.
"""

import argparse
import json
import os
import sys
import time
import uuid
from pathlib import Path


SCENARIOS = ("happy", "deliverable", "error", "resume", "seed", "seed_rotate")


def _gemini_dir() -> Path:
    """The per-task Antigravity state dir (``$HOME/.gemini/antigravity``).

    Under optio's HOME-isolation this lands at
    ``<workdir>/home/.gemini/antigravity`` — inside the workdir, so it is
    captured by the workdir snapshot and restored on resume, exactly like the
    real ``agy`` transcript/state tree.
    """
    home = os.environ.get("HOME") or str(Path.cwd() / "home")
    return Path(home) / ".gemini" / "antigravity"


def _record_launch() -> None:
    """Durably record this launch's argv (Stage 8 sandbox-wiring assertions).

    When ``FAKE_AGY_RECORD`` names a path, append one JSON object per launch:
    ``{"argv": [...]}``. The workdir is wiped on teardown, so this record
    (outside the workdir) is how a wiring test asserts what flags were passed.
    """
    dest = os.environ.get("FAKE_AGY_RECORD")
    if not dest:
        return
    with open(dest, "a", encoding="utf-8") as fh:
        fh.write(json.dumps({"argv": sys.argv[1:]}) + "\n")
        fh.flush()


def _log(line: str) -> None:
    log = Path.cwd() / "optio.log"
    with log.open("a", encoding="utf-8") as fh:
        fh.write(line.rstrip("\n") + "\n")
        fh.flush()


def _scenario_happy() -> None:
    time.sleep(0.05)
    _log("STATUS: 10% fake agy alive")
    time.sleep(0.05)
    _log("STATUS: 50% pretending to work")
    time.sleep(0.05)
    _log("DONE: scenario completed")
    time.sleep(30.0)


def _scenario_deliverable() -> None:
    workdir = Path.cwd()
    (workdir / "deliverables").mkdir(exist_ok=True)
    (workdir / "deliverables" / "greeting.txt").write_text(
        "hello from fake agy\n", encoding="utf-8",
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


def _scenario_resume() -> None:
    """Model agy's per-workdir session persistence for the resume test.

    Records every launch's flags to
    ``<gemini>/fake_agy_argv.jsonl`` (append-only, one JSON array per launch)
    and drops a one-time seed marker. After a workdir restore the seed run's
    line survives, so the file carries one line per launch — proving the
    restore happened and revealing whether ``--continue`` was passed on the
    resumed launch.
    """
    gem = _gemini_dir()
    gem.mkdir(parents=True, exist_ok=True)
    with (gem / "fake_agy_argv.jsonl").open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(sys.argv[1:]) + "\n")
        fh.flush()
    marker = gem / "seed_marker.txt"
    if not marker.exists():
        marker.write_text("SEEDED\n", encoding="utf-8")
    time.sleep(0.05)
    _log("STATUS: 10% resume-scenario alive")
    time.sleep(0.05)
    _log("DONE: resume scenario completed")
    time.sleep(30.0)


def _token_store(gem: Path) -> Path:
    """The seed's OAuth token store — mirrors the REAL agy path (S1).

    ``gem`` is ``~/.gemini/antigravity`` (the state dir); the token store the
    real agy writes on login is ``~/.gemini/antigravity-cli/antigravity-oauth-token``
    (a plain JSON file, NOT the keyring) — the path the production seed manifest
    (``_TOKEN_STORE_RELPATH``) and cred watcher capture/rotate. The fake writes
    the same nested Google-token shape agy uses (see ``_scenario_seed``).
    """
    return gem.parent / "antigravity-cli" / "antigravity-oauth-token"


def _scenario_seed() -> None:
    """Model agy's logged-in identity for the Stage-3 seed tests.

    Two roles, distinguished by whether the token store is already present:

    * CONSUME (seed merged in): the seed engine planted the token store before
      launch. Record that via a deliverable so the test can assert the seed
      reached the workdir before agy started.
    * CAPTURE (fresh login): no token yet, so write a fake logged-in identity
      (token store + settings.json). Teardown capture then stores it as a seed.
    """
    gem = _gemini_dir()
    gem.mkdir(parents=True, exist_ok=True)
    token = _token_store(gem)
    if token.exists():
        workdir = Path.cwd()
        (workdir / "deliverables").mkdir(exist_ok=True)
        (workdir / "deliverables" / "seed_present.txt").write_text(
            "SEED_PRESENT\n", encoding="utf-8",
        )
        time.sleep(0.05)
        _log("DELIVERABLE: ./deliverables/seed_present.txt")
    else:
        token.parent.mkdir(parents=True, exist_ok=True)
        token.write_text(
            json.dumps({
                "auth_method": "consumer",
                "token": {
                    "access_token": "fake-access",
                    "token_type": "Bearer",
                    "refresh_token": "fake-refresh",
                    "expiry": "2099-01-01T00:00:00Z",
                },
            }),
            encoding="utf-8",
        )
        settings = gem.parent / "antigravity-cli"
        settings.mkdir(parents=True, exist_ok=True)
        (settings / "settings.json").write_text(
            json.dumps({"model": "gemini-fake"}), encoding="utf-8",
        )
    time.sleep(0.05)
    _log("STATUS: 10% seed scenario alive")
    time.sleep(0.05)
    _log("DONE: seed scenario completed")
    time.sleep(30.0)


def _rotate_token(gem: Path, new_refresh: str) -> None:
    """Rotate the refresh_token in the token store, modelling Google's OAuth
    refresh-token rotation (what the credential watcher must save back)."""
    token = _token_store(gem)
    try:
        data = json.loads(token.read_text(encoding="utf-8"))
    except (FileNotFoundError, ValueError):
        data = {}
    # Rotate the NESTED refresh token (agy's real shape: {token: {refresh_token}}).
    data.setdefault("token", {})["refresh_token"] = new_refresh
    token.write_text(json.dumps(data), encoding="utf-8")


def _scenario_seed_rotate() -> None:
    """CONSUME role that rotates the refresh token mid-session (Stage-4)."""
    gem = _gemini_dir()
    gem.mkdir(parents=True, exist_ok=True)
    _rotate_token(gem, "ROTATED-INSESSION")
    time.sleep(0.05)
    _log("STATUS: 10% rotate scenario alive")
    time.sleep(0.05)
    _log("DONE: rotate scenario completed")
    time.sleep(30.0)


def _agy_cli_dir() -> Path:
    """The real ``agy`` state root: ``$HOME/.gemini/antigravity-cli``.

    (``_gemini_dir`` is ``$HOME/.gemini/antigravity`` — the seed/state dir the
    seed scenarios use; the conversation transcript + cache live one level over
    in ``antigravity-cli``, matching the real binary.)
    """
    return _gemini_dir().parent / "antigravity-cli"


def _transcript_path(conversation_id: str) -> Path:
    return (
        _agy_cli_dir() / "brain" / conversation_id
        / ".system_generated" / "logs" / "transcript.jsonl"
    )


def _record_last_conversation(conversation_id: str) -> None:
    """Record ``cache/last_conversations.json`` = ``{<workdir>: <uuid>}`` (the
    map the driver reads to discover turn 1's minted uuid)."""
    cache = _agy_cli_dir() / "cache"
    cache.mkdir(parents=True, exist_ok=True)
    path = cache / "last_conversations.json"
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            data = {}
    except (FileNotFoundError, ValueError):
        data = {}
    data[os.getcwd()] = conversation_id
    path.write_text(json.dumps(data), encoding="utf-8")


def _append_transcript(conversation_id: str, prompt: str, reply: str) -> None:
    """Append one turn's real-schema lines to the per-conversation transcript.

    Models the captured ``agy`` shape: a ``USER_INPUT`` (source USER_EXPLICIT)
    whose ``content`` wraps the request in ``<USER_REQUEST>…</USER_REQUEST>``,
    then a ``PLANNER_RESPONSE`` (source MODEL) whose ``content`` is the answer
    (with a ``thinking`` reasoning string, as the real assistant emits)."""
    transcript = _transcript_path(conversation_id)
    transcript.parent.mkdir(parents=True, exist_ok=True)
    # Continue step_index across turns appending to the same file.
    step = sum(1 for _ in transcript.open()) if transcript.exists() else 0
    with transcript.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps({
            "step_index": step,
            "source": "USER_EXPLICIT",
            "type": "USER_INPUT",
            "status": "DONE",
            "content": (
                f"<USER_REQUEST>\n{prompt}\n</USER_REQUEST>\n"
                "<ADDITIONAL_METADATA>\nfake agy\n</ADDITIONAL_METADATA>"
            ),
        }) + "\n")
        fh.write(json.dumps({
            "step_index": step + 1,
            "source": "MODEL",
            "type": "PLANNER_RESPONSE",
            "status": "DONE",
            "content": reply,
            "thinking": "**Thinking**\nfake agy reasoning about the request.\n",
        }) + "\n")
        fh.flush()


def _reply_for(prompt: str) -> str:
    """Canned reply: ``say X`` → ``X`` (so a test can assert a round-trip);
    otherwise a fixed acknowledgement."""
    stripped = prompt.strip()
    low = stripped.lower()
    if low.startswith("say "):
        return stripped[4:].strip()
    return "DONE"


def _print_turn(conversation_id: str | None, prompt: str, slow: bool) -> int:
    """One synthetic conversation turn (``agy -p``).

    On turn 1 (``conversation_id is None``) mints a uuid and records it in
    ``cache/last_conversations.json`` keyed by the workdir (how the real ``agy``
    lets a caller discover the fresh conversation); appends the turn to the
    per-conversation transcript and echoes the reply on stdout. ``slow`` (from
    ``FAKE_AGY_SLOW``) sleeps first so an ``interrupt()`` test can kill an
    in-flight turn.
    """
    if slow:
        time.sleep(30.0)
    fresh = conversation_id is None
    cid = conversation_id or str(uuid.uuid4())
    if fresh:
        _record_last_conversation(cid)
    reply = _reply_for(prompt)
    _append_transcript(cid, prompt, reply)
    print(reply, flush=True)
    return 0


def _print_models() -> int:
    """Canned ``agy models`` output (Gemini + BYO), one id per line."""
    for line in (
        "gemini-2.5-pro",
        "gemini-2.5-flash",
        "claude-sonnet-4",
        "gpt-oss-120b",
    ):
        print(line, flush=True)
    return 0


def main() -> int:
    argv = sys.argv[1:]

    # ``agy models`` — a subcommand, detected before argparse.
    if argv and argv[0] == "models":
        _record_launch()
        return _print_models()

    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--help", action="store_true")
    parser.add_argument("--version", action="store_true")
    parser.add_argument("-p", "--print", dest="print_mode", action="store_true")
    parser.add_argument("--conversation", default=None)
    parser.add_argument("-c", "--continue", dest="cont", action="store_true")
    parser.add_argument("--model", default=None)
    parser.add_argument("--new-project", action="store_true")
    parser.add_argument(
        "--dangerously-skip-permissions", action="store_true",
    )
    args, unknown = parser.parse_known_args()

    if args.help:
        print("agy — fake Antigravity CLI (test shim)")
        print("usage: agy [--print] [--conversation ID] [--model M] [PROMPT]")
        print("commands: models")
        return 0
    if args.version:
        print("agy 1.0.16 (fake)")
        return 0

    if args.print_mode:
        _record_launch()
        prompt = unknown[0] if unknown else ""
        slow = os.environ.get("FAKE_AGY_SLOW", "").strip() not in ("", "0")
        return _print_turn(args.conversation, prompt, slow)

    # Bare/TUI launch: run the optio.log scenario script (Stage 0 iframe).
    _record_launch()
    scenario = os.environ.get("FAKE_AGY_SCENARIO", "happy").strip()
    if scenario not in SCENARIOS:
        print(f"unknown FAKE_AGY_SCENARIO={scenario!r}", file=sys.stderr)
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
