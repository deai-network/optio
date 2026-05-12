"""Tests for structured child failure propagation."""
import pytest

from optio_core.exceptions import ChildProcessFailed


class _SampleErr(Exception):
    def __init__(self, url: str, exit_code: int):
        self.url = url
        self.exit_code = exit_code
        super().__init__(f"sample error for {url}")


def test_child_process_failed_carries_name_pid_and_original():
    original = _SampleErr("http://example.com", 42)
    cpf = ChildProcessFailed("Sample Child", "child-pid", original)
    assert cpf.name == "Sample Child"
    assert cpf.process_id == "child-pid"
    assert cpf.original is original
    assert isinstance(cpf.original, _SampleErr)
    assert cpf.original.url == "http://example.com"
    assert cpf.original.exit_code == 42


def test_child_process_failed_message_includes_repr_of_original():
    original = _SampleErr("u", 1)
    cpf = ChildProcessFailed("N", "P", original)
    msg = str(cpf)
    assert "N" in msg
    assert "P" in msg
    assert repr(original) in msg


def test_child_process_failed_is_an_exception():
    original = _SampleErr("u", 1)
    cpf = ChildProcessFailed("N", "P", original)
    assert isinstance(cpf, Exception)
    with pytest.raises(ChildProcessFailed):
        raise cpf


from optio_core.models import ChildOutcome


def test_child_outcome_default_no_exception():
    outcome = ChildOutcome(state="done")
    assert outcome.state == "done"
    assert outcome.original_exception is None


def test_child_outcome_with_exception():
    exc = _SampleErr("u", 9)
    outcome = ChildOutcome(state="failed", original_exception=exc)
    assert outcome.state == "failed"
    assert outcome.original_exception is exc


from optio_core.models import ChildResult


def test_child_result_defaults():
    r = ChildResult(process_id="p", state="done")
    assert r.process_id == "p"
    assert r.state == "done"
    assert r.error is None
    assert r.name == ""
    assert r.original_exception is None


def test_child_result_carries_name_and_original():
    exc = _SampleErr("u", 1)
    r = ChildResult(
        process_id="p", state="failed", error="Child failed",
        name="Sample", original_exception=exc,
    )
    assert r.name == "Sample"
    assert r.original_exception is exc
