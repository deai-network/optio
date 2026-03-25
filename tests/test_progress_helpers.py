"""Tests for progress helper functions."""

from unittest.mock import MagicMock
from feldwebel.models import ChildProgressInfo
from feldwebel.progress_helpers import sequential_progress, average_progress, mapped_progress


def test_sequential_progress_first_child_partial():
    """First child at 80% with 4 total = 20% parent."""
    ctx = MagicMock()
    callback = sequential_progress(ctx, total_children=4)
    callback([ChildProgressInfo("c1", "C1", "running", 80, None)])
    ctx.report_progress.assert_called_with(20.0)


def test_sequential_progress_first_done_second_partial():
    """Child 1 done + child 2 at 50% with 4 total = 37.5% parent."""
    ctx = MagicMock()
    callback = sequential_progress(ctx, total_children=4)
    callback([
        ChildProgressInfo("c1", "C1", "done", 100, None),
        ChildProgressInfo("c2", "C2", "running", 50, None),
    ])
    ctx.report_progress.assert_called_with(37.5)


def test_sequential_progress_three_done_fourth_partial():
    """Children 1-3 done + child 4 at 60% with 4 total = 90% parent."""
    ctx = MagicMock()
    callback = sequential_progress(ctx, total_children=4)
    callback([
        ChildProgressInfo("c1", "C1", "done", 100, None),
        ChildProgressInfo("c2", "C2", "done", 100, None),
        ChildProgressInfo("c3", "C3", "done", 100, None),
        ChildProgressInfo("c4", "C4", "running", 60, None),
    ])
    ctx.report_progress.assert_called_with(90.0)


def test_sequential_progress_all_done():
    """All 4 done = 100% parent."""
    ctx = MagicMock()
    callback = sequential_progress(ctx, total_children=4)
    callback([
        ChildProgressInfo("c1", "C1", "done", 100, None),
        ChildProgressInfo("c2", "C2", "done", 100, None),
        ChildProgressInfo("c3", "C3", "done", 100, None),
        ChildProgressInfo("c4", "C4", "done", 100, None),
    ])
    ctx.report_progress.assert_called_with(100.0)


def test_sequential_progress_none_percent_treated_as_zero():
    """Child with None percent is treated as 0%."""
    ctx = MagicMock()
    callback = sequential_progress(ctx, total_children=2)
    callback([ChildProgressInfo("c1", "C1", "running", None, None)])
    ctx.report_progress.assert_called_with(0.0)


def test_average_progress_basic():
    """Average of 50% and 100% = 75%."""
    ctx = MagicMock()
    callback = average_progress(ctx)
    callback([
        ChildProgressInfo("c1", "C1", "running", 50, None),
        ChildProgressInfo("c2", "C2", "done", 100, None),
    ])
    ctx.report_progress.assert_called_with(75.0)


def test_average_progress_none_as_zero():
    """Child with None percent treated as 0%."""
    ctx = MagicMock()
    callback = average_progress(ctx)
    callback([
        ChildProgressInfo("c1", "C1", "running", None, None),
        ChildProgressInfo("c2", "C2", "running", 50, None),
    ])
    ctx.report_progress.assert_called_with(25.0)


def test_average_progress_done_as_100():
    """Done child treated as 100% regardless of stored percent."""
    ctx = MagicMock()
    callback = average_progress(ctx)
    callback([
        ChildProgressInfo("c1", "C1", "done", None, None),
    ])
    ctx.report_progress.assert_called_with(100.0)


def test_mapped_progress_first_quarter():
    """Child at 50% mapped to 0.0-0.25 range = 12.5% parent."""
    ctx = MagicMock()
    callback = mapped_progress(ctx, 0.0, 0.25)
    callback([ChildProgressInfo("c1", "C1", "running", 50, None)])
    ctx.report_progress.assert_called_with(12.5)


def test_mapped_progress_second_quarter():
    """Child at 50% mapped to 0.25-0.5 range = 37.5% parent."""
    ctx = MagicMock()
    callback = mapped_progress(ctx, 0.25, 0.5)
    callback([ChildProgressInfo("c1", "C1", "running", 50, None)])
    ctx.report_progress.assert_called_with(37.5)


def test_mapped_progress_done_child():
    """Done child in 0.0-0.25 range = 25% parent."""
    ctx = MagicMock()
    callback = mapped_progress(ctx, 0.0, 0.25)
    callback([ChildProgressInfo("c1", "C1", "done", 100, None)])
    ctx.report_progress.assert_called_with(25.0)


def test_mapped_progress_none_percent():
    """Child with None percent treated as 0% -> range_start."""
    ctx = MagicMock()
    callback = mapped_progress(ctx, 0.25, 0.5)
    callback([ChildProgressInfo("c1", "C1", "running", None, None)])
    ctx.report_progress.assert_called_with(25.0)
