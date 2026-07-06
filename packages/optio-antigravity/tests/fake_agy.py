"""Stand-in for the ``agy`` (Antigravity CLI) binary during integration tests.

Two surfaces, matching the two real ``agy`` usages optio drives:

* **Scenario / TUI mode** — a bare launch (``agy [flags]``, optionally with a
  trailing positional prompt) runs a deterministic script of ``optio.log``
  writes (``STATUS:`` / ``DELIVERABLE:`` / ``DONE`` / ``ERROR``) then parks,
  modelling the interactive TUI embedded under ttyd (Stage 0). The scenario is
  chosen by ``FAKE_AGY_SCENARIO`` (default ``happy``).

* **Print / conversation mode** — ``agy -p/--print [--conversation <id>]
  [--model <m>] [--dangerously-skip-permissions] <prompt>`` models one
  synthetic conversation turn (§5 of the design): it appends structured lines
  to ``$HOME/.gemini/antigravity/transcript.jsonl`` (the real transcript path)
  and echoes a canned reply (plus the conversation id) on stdout. Turn 1 omits
  ``--conversation`` and MINTS an id; later turns pass it back.

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
    """The seed's encrypted-token-store stand-in.

    TODO(S1): reconcile with the real-login spike. The real ``agy`` keeps its
    Google OAuth token in the OS keyring; the design's likely fallback (§2
    option 1) is an encrypted file when no Secret Service is present. This fake
    models that file at ``<gemini>/oauth_creds.json`` so the Stage-3/4 seed +
    save-back machinery has a concrete file to capture/rotate. Adjust the path
    once S1 pins where the token actually lives.
    """
    return gem / "oauth_creds.json"


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
        token.write_text(
            json.dumps({
                "access_token": "fake-access",
                "refresh_token": "fake-refresh",
                "expires_at": 9999999999,
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
    data["refresh_token"] = new_refresh
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


def _append_transcript(gem: Path, conversation_id: str, prompt: str, reply: str) -> None:
    """Append one turn's structured events to the transcript.jsonl.

    TODO(S3): reconcile with the real transcript schema spike. This models a
    minimal line shape (``type`` in {user, assistant} + ``conversationId``);
    Stage 6's reducer is written against the real captured fixture.
    """
    gem.mkdir(parents=True, exist_ok=True)
    transcript = gem / "transcript.jsonl"
    with transcript.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps({
            "type": "user", "conversationId": conversation_id, "text": prompt,
        }) + "\n")
        fh.write(json.dumps({
            "type": "assistant", "conversationId": conversation_id, "text": reply,
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

    Mints a conversation id on turn 1 (``conversation_id is None``), appends the
    turn to the transcript, and echoes the reply + id on stdout. ``slow`` (from
    ``FAKE_AGY_SLOW``) sleeps first so an ``interrupt()`` test can kill an
    in-flight turn.
    """
    if slow:
        time.sleep(30.0)
    cid = conversation_id or f"conv-{uuid.uuid4().hex[:12]}"
    reply = _reply_for(prompt)
    _append_transcript(_gemini_dir(), cid, prompt, reply)
    # Echo the conversation id (so a caller can capture it on turn 1) and reply.
    print(f"conversation: {cid}", flush=True)
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
