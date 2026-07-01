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


SCENARIOS = ("happy", "deliverable", "error", "resume", "seed")


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
        "resume": _scenario_resume,
        "seed": _scenario_seed,
    }[scenario]()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
