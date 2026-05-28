"""Stand-in for the `claude` CLI during integration tests.

Reads the scenario name from the env var ``FAKE_CLAUDE_SCENARIO``
(default ``happy``) and runs a deterministic script of optio.log writes
+ sleeps + (optionally) deliverable writes. Stays alive until DONE or
ERROR has been emitted; the framework signals SIGTERM to terminate the
wrapping ttyd process at that point.
"""

import argparse
import os
import sys
import time
from pathlib import Path


SCENARIOS = ("happy", "deliverable", "error", "long")


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
    _log("DELIVERABLE: greeting.txt")
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


def main() -> int:
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
    {
        "happy": _scenario_happy,
        "deliverable": _scenario_deliverable,
        "error": _scenario_error,
        "long": _scenario_long,
    }[scenario]()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
