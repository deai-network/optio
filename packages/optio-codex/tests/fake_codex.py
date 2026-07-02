"""Stand-in for the `codex` CLI during integration tests."""

import os
import time
from pathlib import Path


SCENARIOS = ("happy", "deliverable", "error", "exit_zero", "exit_nonzero", "long")


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
        print(f"unknown FAKE_CODEX_SCENARIO={scenario!r}", file=__import__("sys").stderr)
        return 2
    {
        "happy": _scenario_happy,
        "deliverable": _scenario_deliverable,
        "error": _scenario_error,
        "exit_zero": _scenario_exit_zero,
        "exit_nonzero": _scenario_exit_nonzero,
        "long": _scenario_long,
    }[scenario]()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())