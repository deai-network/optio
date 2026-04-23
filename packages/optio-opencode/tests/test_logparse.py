import pytest

from optio_opencode.logparse import (
    DeliverableEvent,
    DoneEvent,
    ErrorEvent,
    StatusEvent,
    UnknownLine,
    parse_log_line,
    validate_deliverable_path,
)


# ---- STATUS ----

def test_status_plain():
    ev = parse_log_line("STATUS: working on it")
    assert isinstance(ev, StatusEvent)
    assert ev.percent is None
    assert ev.message == "working on it"


def test_status_with_percent():
    ev = parse_log_line("STATUS: 42% halfway there")
    assert isinstance(ev, StatusEvent)
    assert ev.percent == 42
    assert ev.message == "halfway there"


def test_status_with_zero_percent():
    ev = parse_log_line("STATUS: 0% just starting")
    assert isinstance(ev, StatusEvent)
    assert ev.percent == 0
    assert ev.message == "just starting"


def test_status_with_percent_over_100_is_clamped():
    ev = parse_log_line("STATUS: 150% overachiever")
    assert isinstance(ev, StatusEvent)
    assert ev.percent == 100


def test_status_empty_message_ok():
    ev = parse_log_line("STATUS: ")
    assert isinstance(ev, StatusEvent)
    assert ev.percent is None
    assert ev.message == ""


# ---- DELIVERABLE ----

def test_deliverable_relative():
    ev = parse_log_line("DELIVERABLE: ./deliverables/out.txt")
    assert isinstance(ev, DeliverableEvent)
    assert ev.path == "./deliverables/out.txt"


def test_deliverable_absolute():
    ev = parse_log_line("DELIVERABLE: /tmp/wd/deliverables/a.md")
    assert isinstance(ev, DeliverableEvent)
    assert ev.path == "/tmp/wd/deliverables/a.md"


def test_deliverable_trims_trailing_whitespace():
    ev = parse_log_line("DELIVERABLE: ./x   ")
    assert isinstance(ev, DeliverableEvent)
    assert ev.path == "./x"


# ---- DONE ----

def test_done_bare():
    ev = parse_log_line("DONE")
    assert isinstance(ev, DoneEvent)
    assert ev.summary is None


def test_done_with_summary():
    ev = parse_log_line("DONE: wrote the report")
    assert isinstance(ev, DoneEvent)
    assert ev.summary == "wrote the report"


# ---- ERROR ----

def test_error_bare():
    ev = parse_log_line("ERROR")
    assert isinstance(ev, ErrorEvent)
    assert ev.message is None


def test_error_with_message():
    ev = parse_log_line("ERROR: provider auth failed")
    assert isinstance(ev, ErrorEvent)
    assert ev.message == "provider auth failed"


# ---- Unknown ----

def test_unknown_line_preserved_verbatim():
    ev = parse_log_line("just some narration from the llm")
    assert isinstance(ev, UnknownLine)
    assert ev.text == "just some narration from the llm"


def test_empty_line_is_unknown():
    ev = parse_log_line("")
    assert isinstance(ev, UnknownLine)
    assert ev.text == ""


# ---- validate_deliverable_path ----

def test_validate_relative_ok(tmp_workdir):
    import os
    resolved = validate_deliverable_path("./deliverables/x.txt", tmp_workdir)
    assert resolved == os.path.join(tmp_workdir, "deliverables", "x.txt")


def test_validate_absolute_inside_workdir_ok(tmp_workdir):
    import os
    p = os.path.join(tmp_workdir, "a.txt")
    assert validate_deliverable_path(p, tmp_workdir) == p


def test_validate_escape_via_dotdot_rejected(tmp_workdir):
    with pytest.raises(ValueError):
        validate_deliverable_path("../etc/passwd", tmp_workdir)


def test_validate_absolute_outside_workdir_rejected(tmp_workdir):
    with pytest.raises(ValueError):
        validate_deliverable_path("/etc/passwd", tmp_workdir)
