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
import os
import sys
import time
from pathlib import Path


SCENARIOS = ("happy", "deliverable", "error")


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


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--version", action="store_true")
    parser.add_argument("--permission-mode", default=None)
    parser.add_argument("--model", default=None)
    args, _unknown = parser.parse_known_args()
    if args.version:
        print("grok 0.2.77 (fake)")
        return 0
    scenario = os.environ.get("FAKE_GROK_SCENARIO", "happy").strip()
    if scenario not in SCENARIOS:
        print(f"unknown FAKE_GROK_SCENARIO={scenario!r}", file=sys.stderr)
        return 2
    {
        "happy": _scenario_happy,
        "deliverable": _scenario_deliverable,
        "error": _scenario_error,
    }[scenario]()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
