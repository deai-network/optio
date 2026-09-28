"""Progress rule for a conversation's todo list."""

from optio_agents.todos import TodoItem, TodoProgress, TodoUpdate


def _item(text, status, id="", active=None):
    return TodoItem(id=id, text=text, status=status, active=active)


def test_all_pending_is_zero():
    progress = TodoProgress()
    report = progress.apply(TodoUpdate(items=[
        _item("a", "pending", "1"),
        _item("b", "pending", "2"),
        _item("c", "pending", "3"),
    ], merge=False))
    assert report is not None
    assert report.percent == 0
    assert report.message == "0 of 3 done"


def test_mixed_uses_active_text():
    progress = TodoProgress()
    report = progress.apply(TodoUpdate(items=[
        _item("Done one", "completed", "1"),
        _item("Write", "in_progress", "2", active="Writing"),
        _item("Later", "pending", "3"),
    ], merge=False))
    assert report.percent == 33
    assert report.message == "Writing"


def test_several_in_progress_join():
    progress = TodoProgress()
    report = progress.apply(TodoUpdate(items=[
        _item("A", "in_progress", "1"),
        _item("B", "in_progress", "2"),
    ], merge=False))
    assert report.message == "A; B"


def test_all_complete():
    progress = TodoProgress()
    report = progress.apply(TodoUpdate(items=[
        _item("a", "completed", "1"),
        _item("b", "completed", "2"),
    ], merge=False))
    assert report.percent == 100
    assert report.message == "2 of 2 done"


def test_cancelled_excluded():
    progress = TodoProgress()
    report = progress.apply(TodoUpdate(items=[
        _item("a", "completed", "1"),
        _item("b", "cancelled", "2"),
    ], merge=False))
    assert report.percent == 100
    assert report.message == "1 of 1 done"
    assert [item.status for item in progress.items] == ["completed", "cancelled"]


def test_unknown_status_counts_as_pending():
    progress = TodoProgress()
    report = progress.apply(TodoUpdate(items=[
        _item("a", "bogus", "1"),
    ], merge=False))
    assert progress.items[0].status == "pending"
    assert report.percent == 0
    assert report.message == "0 of 1 done"


def test_half_rounds_up():
    progress = TodoProgress()
    eighth = progress.apply(TodoUpdate(items=[
        _item("done", "completed", "d"),
        *[_item(f"p{i}", "pending", str(i)) for i in range(7)],
    ], merge=False))
    assert eighth.percent == 13
    half = progress.apply(TodoUpdate(items=[
        _item("done", "completed", "d"),
        _item("left", "pending", "p"),
    ], merge=False))
    assert half.percent == 50


def test_zero_countable_returns_none():
    progress = TodoProgress()
    report = progress.apply(TodoUpdate(items=[
        _item("gone", "cancelled", "1"),
    ], merge=False))
    assert report is None


def test_replace_drops_old_ids():
    progress = TodoProgress()
    progress.apply(TodoUpdate(items=[_item("old", "pending", "old")], merge=False))
    progress.apply(TodoUpdate(items=[_item("new", "pending", "new")], merge=False))
    assert [item.id for item in progress.items] == ["new"]


def test_merge_patches_and_appends():
    progress = TodoProgress()
    progress.apply(TodoUpdate(items=[
        _item("A", "pending", "a"),
        _item("B", "pending", "b"),
    ], merge=False))
    progress.apply(TodoUpdate(items=[
        _item("A", "completed", "a"),
        _item("C", "pending", ""),
    ], merge=True))
    assert [(item.id, item.text, item.status) for item in progress.items] == [
        ("a", "A", "completed"),
        ("b", "B", "pending"),
        ("C", "C", "pending"),
    ]


def test_json_round_trip():
    progress = TodoProgress()
    progress.apply(TodoUpdate(items=[
        _item("Write", "in_progress", "1", active="Writing"),
        _item("Later", "pending", "2"),
    ], merge=False))
    raw = progress.to_json()
    assert '"active":"Writing"' in raw or '"active": "Writing"' in raw
    assert "Later" in raw
    # active omitted on the row that has none
    loaded = TodoProgress.from_json(raw)
    assert loaded is not None
    assert [(i.id, i.text, i.status, i.active) for i in loaded.items] == [
        ("1", "Write", "in_progress", "Writing"),
        ("2", "Later", "pending", None),
    ]


def test_empty_json_loads():
    loaded = TodoProgress.from_json('{"items":[]}')
    assert loaded is not None
    assert loaded.report() is None


def test_garbage_json_is_none():
    assert TodoProgress.from_json("{") is None
    assert TodoProgress.from_json('{"items":[{}]}') is None
    assert TodoProgress.from_json("[]") is None
