import pytest

from optio_agents.protocol.features import ProtocolFeatures
from optio_agents.protocol.parser import (
    AttentionEvent,
    BrowserEvent,
    CallerMessageEvent,
    ClientMessageEvent,
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


# ---- relativize_deliverable_path ----

from optio_agents.protocol.parser import relativize_deliverable_path


def test_relativize_direct_child_of_deliverables(tmp_workdir):
    import os
    abs_path = os.path.join(tmp_workdir, "deliverables", "foo.md")
    assert relativize_deliverable_path(abs_path, tmp_workdir) == "foo.md"


def test_relativize_nested_under_deliverables(tmp_workdir):
    import os
    abs_path = os.path.join(tmp_workdir, "deliverables", "sub", "foo.md")
    expected = os.path.join("sub", "foo.md")
    assert relativize_deliverable_path(abs_path, tmp_workdir) == expected


def test_relativize_inside_workdir_but_not_deliverables_rejected(tmp_workdir):
    import os
    abs_path = os.path.join(tmp_workdir, "foo.md")
    with pytest.raises(ValueError):
        relativize_deliverable_path(abs_path, tmp_workdir)


def test_relativize_sibling_dir_with_deliverables_prefix_rejected(tmp_workdir):
    import os
    abs_path = os.path.join(tmp_workdir, "deliverables_other", "foo.md")
    with pytest.raises(ValueError):
        relativize_deliverable_path(abs_path, tmp_workdir)


def test_relativize_deliverables_root_itself_rejected(tmp_workdir):
    import os
    abs_path = os.path.join(tmp_workdir, "deliverables")
    with pytest.raises(ValueError):
        relativize_deliverable_path(abs_path, tmp_workdir)


def test_relativize_outside_workdir_rejected(tmp_workdir):
    with pytest.raises(ValueError):
        relativize_deliverable_path("/etc/passwd", tmp_workdir)


# ---- BROWSER / ATTENTION / CLIENT_MESSAGE / CALLER_MESSAGE ----

def test_browser_event():
    ev = parse_log_line(
        'BROWSER: "https://example.com/login"',
        features=ProtocolFeatures(browser="redirect"),
    )
    assert isinstance(ev, BrowserEvent)
    # The shim's delimiter quotes are stripped — the consumer opens the bare URL.
    assert ev.url == 'https://example.com/login'


def test_browser_event_unquoted():
    ev = parse_log_line(
        "BROWSER: https://example.com",
        features=ProtocolFeatures(browser="redirect"),
    )
    assert isinstance(ev, BrowserEvent)
    assert ev.url == "https://example.com"


def test_attention_event():
    ev = parse_log_line("ATTENTION: please approve")
    assert isinstance(ev, AttentionEvent)
    assert ev.reason == "please approve"


_MSGS_ON = ProtocolFeatures(client_messages=True, caller_messages=True)


def test_client_message_event():
    ev = parse_log_line(
        'CLIENT_MESSAGE: build-done {"artifact": "app.zip"}', features=_MSGS_ON,
    )
    assert isinstance(ev, ClientMessageEvent)
    assert ev.keyword == "build-done"
    assert ev.data == {"artifact": "app.zip"}


def test_caller_message_event():
    ev = parse_log_line(
        'CALLER_MESSAGE: tests-passed {"suite": "unit"}', features=_MSGS_ON,
    )
    assert isinstance(ev, CallerMessageEvent)
    assert ev.keyword == "tests-passed"
    assert ev.data == {"suite": "unit"}


def test_message_keywords_disabled_by_default():
    for line in ('CLIENT_MESSAGE: k {"n": 1}', 'CALLER_MESSAGE: k {"n": 1}'):
        ev = parse_log_line(line)
        assert isinstance(ev, UnknownLine)
        assert ev.text == line


def test_message_malformed_json_drops_to_unknown():
    for line in ("CLIENT_MESSAGE: k {not json}", "CALLER_MESSAGE: k {not json}"):
        ev = parse_log_line(line, features=_MSGS_ON)
        assert isinstance(ev, UnknownLine)


def test_domain_message_keyword_is_gone():
    # Removal regression pin: the old keyword is inert even with messages on.
    ev = parse_log_line('DOMAIN_MESSAGE: k {"n": 1}', features=_MSGS_ON)
    assert isinstance(ev, UnknownLine)


# ---- feature toggles ----

def test_browser_not_recognized_by_default():
    # Conservative default: ProtocolFeatures() has browser="ignore".
    ev = parse_log_line("BROWSER: https://example.com")
    assert isinstance(ev, UnknownLine)
    assert ev.text == "BROWSER: https://example.com"


def test_browser_recognized_under_redirect():
    ev = parse_log_line(
        "BROWSER: https://example.com",
        features=ProtocolFeatures(browser="redirect"),
    )
    assert isinstance(ev, BrowserEvent)


def test_attention_recognized_in_every_mode():
    for features in (ProtocolFeatures(), ProtocolFeatures(browser="redirect")):
        ev = parse_log_line("ATTENTION: ping", features=features)
        assert isinstance(ev, AttentionEvent)
