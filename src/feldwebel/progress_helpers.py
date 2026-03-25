"""Progress helper functions — return callbacks for on_child_progress."""

from __future__ import annotations
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from feldwebel.context import ProcessContext
    from feldwebel.models import ChildProgressInfo


def sequential_progress(ctx: "ProcessContext", total_children: int):
    """Return a callback that maps sequential children into equal progress slots.

    Each child fills one slot of size (100 / total_children)%. Completed children
    count as full slots. Children are assigned to slots by their position in the
    snapshot list (spawn order).
    """
    slot_size = 100.0 / total_children

    def _callback(children: list["ChildProgressInfo"]) -> None:
        total = 0.0
        for i, child in enumerate(children):
            if i >= total_children:
                break
            pct = child.percent if child.percent is not None else 0.0
            if child.state in ("done", "failed", "cancelled"):
                pct = 100.0
            total += pct / 100.0 * slot_size
        ctx.report_progress(total)

    return _callback


def average_progress(ctx: "ProcessContext"):
    """Return a callback that averages all children's percent.

    Done/failed/cancelled children count as 100%. Children with None percent
    count as 0%.
    """
    def _callback(children: list["ChildProgressInfo"]) -> None:
        if not children:
            return
        total = 0.0
        for child in children:
            if child.state in ("done", "failed", "cancelled"):
                total += 100.0
            elif child.percent is not None:
                total += child.percent
        ctx.report_progress(total / len(children))

    return _callback


def mapped_progress(ctx: "ProcessContext", range_start: float, range_end: float):
    """Return a callback that maps a single child's 0-100% into a parent range.

    range_start and range_end are fractions (0.0 to 1.0). For example,
    mapped_progress(ctx, 0.0, 0.25) maps child progress into 0-25% of parent.
    Uses the last child in the snapshot (the most recently spawned one).
    """
    span = (range_end - range_start) * 100.0
    base = range_start * 100.0

    def _callback(children: list["ChildProgressInfo"]) -> None:
        if not children:
            return
        child = children[-1]
        if child.state in ("done", "failed", "cancelled"):
            pct = 100.0
        elif child.percent is not None:
            pct = child.percent
        else:
            pct = 0.0
        ctx.report_progress(base + pct / 100.0 * span)

    return _callback
