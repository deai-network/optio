"""Stand-in for the `cursor-agent` CLI during integration tests.

Scenario mode: reads the scenario name from the env var
``FAKE_CURSOR_SCENARIO`` (default ``happy``) and runs a deterministic script
of optio.log writes + sleeps + (optionally) deliverable writes. Stays alive
until DONE or ERROR has been emitted; the framework signals SIGTERM to
terminate the wrapping tmux/ttyd tree at that point.

Adapted from optio-grok's ``fake_grok.py`` (scenario mode only; the ACP
conversation mode and probe/seed scenarios are later stages).
"""

import argparse
import json
import os
import sys
import time
from pathlib import Path


SCENARIOS = ("happy", "deliverable", "error", "resume", "seed", "seed_rotate")


def _log(line: str) -> None:
    log = Path.cwd() / "optio.log"
    with log.open("a", encoding="utf-8") as fh:
        fh.write(line.rstrip("\n") + "\n")
        fh.flush()


def _scenario_happy() -> None:
    time.sleep(0.05)
    _log("STATUS: 10% fake cursor alive")
    time.sleep(0.05)
    _log("STATUS: 50% pretending to work")
    time.sleep(0.05)
    _log("DONE: scenario completed")
    time.sleep(30.0)


def _scenario_deliverable() -> None:
    workdir = Path.cwd()
    (workdir / "deliverables").mkdir(exist_ok=True)
    (workdir / "deliverables" / "greeting.txt").write_text(
        "hello from fake cursor\n", encoding="utf-8",
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


def _cursor_home() -> Path:
    """The per-task cursor state dir (``<workdir>/home/.cursor``).

    The launcher sets ``HOME=<workdir>/home``, so ``$HOME/.cursor`` lives
    INSIDE the workdir and anything written here is captured by the workdir
    snapshot and restored on resume — exactly like real cursor-agent's chat
    store.
    """
    home = os.environ.get("HOME") or str(Path.cwd() / "home")
    return Path(home) / ".cursor"


def _scenario_resume() -> None:
    """Model cursor's cwd-keyed chat persistence for the resume test.

    Records every launch's cursor flags to
    ``<HOME>/.cursor/fake_cursor_argv.jsonl`` (append-only, one JSON array per
    launch) and drops a one-time seed marker. After a workdir restore the seed
    run's line survives, so the file carries one line per launch — proving the
    restore happened and revealing whether ``--continue`` was passed on the
    resumed launch.
    """
    ch = _cursor_home()
    ch.mkdir(parents=True, exist_ok=True)
    with (ch / "fake_cursor_argv.jsonl").open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(sys.argv[1:]) + "\n")
        fh.flush()
    marker = ch / "seed_marker.txt"
    if not marker.exists():
        marker.write_text("SEEDED\n", encoding="utf-8")
    time.sleep(0.05)
    _log("STATUS: 10% resume-scenario alive")
    time.sleep(0.05)
    _log("DONE: resume scenario completed")
    time.sleep(30.0)


def _cursor_config_dir() -> Path:
    """The per-task cursor credential dir (``<workdir>/home/.config/cursor``).

    The launcher sets ``XDG_CONFIG_HOME=<workdir>/home/.config``, so this dir
    lives INSIDE the workdir — exactly where real cursor-agent keeps its
    ``auth.json`` (``status`` reads it, ``logout`` deletes it).
    """
    xdg = os.environ.get("XDG_CONFIG_HOME")
    if xdg:
        return Path(xdg) / "cursor"
    home = os.environ.get("HOME") or str(Path.cwd() / "home")
    return Path(home) / ".config" / "cursor"


def _scenario_seed() -> None:
    """Model cursor's logged-in identity for the Stage-3 seed tests.

    Two roles, distinguished by whether ``auth.json`` is already present at
    launch:

    * CONSUME (seed already merged in): the seed engine planted
      ``home/.config/cursor/auth.json`` before launch. Record that fact via a
      deliverable so the test can assert the seed reached the workdir before
      cursor started.
    * CAPTURE (fresh login): no auth yet, so write a fake logged-in identity
      (auth.json + cli-config.json) under the per-task home. Teardown capture
      then stores it as a reusable seed.
    """
    cfg_dir = _cursor_config_dir()
    cfg_dir.mkdir(parents=True, exist_ok=True)
    auth = cfg_dir / "auth.json"
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
                "accessToken": "fake-access-token",
                "refreshToken": "fake-refresh-token",
            }),
            encoding="utf-8",
        )
        ch = _cursor_home()
        ch.mkdir(parents=True, exist_ok=True)
        (ch / "cli-config.json").write_text(
            json.dumps({"version": 1, "editor": {"vimMode": False}}),
            encoding="utf-8",
        )
    time.sleep(0.05)
    _log("STATUS: 10% seed scenario alive")
    time.sleep(0.05)
    _log("DONE: seed scenario completed")
    time.sleep(30.0)


def _rotate_auth(cfg_dir: Path, new_refresh: str) -> None:
    """Rotate the refreshToken in ``<XDG_CONFIG_HOME>/cursor/auth.json``,
    modelling the refresh-token rotation a real cursor-agent may perform on
    token use (what the credential watcher must save back). Cursor's auth.json
    is a flat ``accessToken``/``refreshToken`` object (unlike grok's
    per-account map)."""
    auth = cfg_dir / "auth.json"
    try:
        data = json.loads(auth.read_text(encoding="utf-8"))
    except (FileNotFoundError, ValueError):
        data = {}
    data["refreshToken"] = new_refresh
    auth.write_text(json.dumps(data), encoding="utf-8")


def _scenario_seed_rotate() -> None:
    """CONSUME role that rotates the refresh token mid-session.

    The seed engine planted ``home/.config/cursor/auth.json`` before launch;
    this run rotates its refreshToken (as real cursor-agent would on a token
    refresh), so the session's teardown save-back must write the rotated
    auth.json back into the seed. Used by the Stage-4 lease/save-back session
    test."""
    cfg_dir = _cursor_config_dir()
    cfg_dir.mkdir(parents=True, exist_ok=True)
    _rotate_auth(cfg_dir, "ROTATED-INSESSION")
    time.sleep(0.05)
    _log("STATUS: 10% rotate scenario alive")
    time.sleep(0.05)
    _log("DONE: rotate scenario completed")
    time.sleep(30.0)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--version", action="store_true")
    parser.add_argument("--model", default=None)
    parser.add_argument("--sandbox", default=None)
    args, _unknown = parser.parse_known_args()
    if args.version:
        print("2026.07.01-fake")
        return 0
    scenario = os.environ.get("FAKE_CURSOR_SCENARIO", "happy").strip()
    if scenario not in SCENARIOS:
        print(f"unknown FAKE_CURSOR_SCENARIO={scenario!r}", file=sys.stderr)
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
