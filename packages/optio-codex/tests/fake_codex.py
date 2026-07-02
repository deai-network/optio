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
    "resume",
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


def main() -> int:
    import argparse

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
    {
        "happy": _scenario_happy,
        "deliverable": _scenario_deliverable,
        "error": _scenario_error,
        "exit_zero": _scenario_exit_zero,
        "exit_nonzero": _scenario_exit_nonzero,
        "long": _scenario_long,
        "resume": _scenario_resume,
    }[scenario]()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())