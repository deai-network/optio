"""Fix 19 (owner ruling 2026-09-15, finding 6 #2): which queued messages the
last run left undelivered, computed from the restored replay buffer."""

import pytest

from optio_claudecode.steering import undelivered_queued

RESUMED = {"type": "x-optio-resumed"}


def _q(qid, text):
    return {"type": "x-optio-queued", "id": qid, "text": text}


def _lc(qid, state):
    return {"type": "command_lifecycle", "command_uuid": qid, "state": state,
            "uuid": f"lc-{qid}-{state}", "session_id": "s"}


def _echo(uuid, *texts):
    return {"type": "user", "message": {"role": "user", "content": [
        {"type": "text", "text": t} for t in texts]}, "uuid": uuid, "isReplay": True}


def _requeued(old, new):
    return {"type": "x-optio-requeued", "id": old, "new_id": new}


def _buffer(*events):
    return list(enumerate(events, start=1))


def test_a_queued_message_nothing_took_is_undelivered_in_the_order_sent():
    assert undelivered_queued(_buffer(_q("q1", "one"), _lc("q1", "queued"), _q("q2", "two"))) == [
        ("q1", "one"), ("q2", "two"),
    ]


@pytest.mark.parametrize("taken", [_lc("q1", "started"), _lc("q1", "completed"), _echo("q1", "one\n\n")])
def test_started_completed_or_its_own_echo_means_delivered(taken):
    assert undelivered_queued(_buffer(_q("q1", "one"), taken, _q("q2", "two"))) == [("q2", "two")]


@pytest.mark.parametrize("state", ["cancelled", "discarded", "refused"])
def test_a_final_state_without_started_leaves_it_undelivered(state):
    assert undelivered_queued(_buffer(_q("q1", "one"), _lc("q1", "queued"), _lc("q1", state))) == [
        ("q1", "one"),
    ]


def test_started_then_cancelled_was_delivered():
    # "cancelled" after "started": consumed into a turn that was aborted.
    assert undelivered_queued(_buffer(_q("q1", "one"), _lc("q1", "started"), _lc("q1", "cancelled"))) == []


def test_a_requeue_renames_the_message_in_place():
    assert undelivered_queued(_buffer(_q("q1", "one"), _q("q2", "two"), _requeued("q1", "n1"))) == [
        ("n1", "one"), ("q2", "two"),
    ]
    assert undelivered_queued(_buffer(_q("q1", "one"), _requeued("q1", "n1"), _lc("n1", "started"))) == []


def test_messages_an_earlier_run_left_behind_are_not_resent():
    assert undelivered_queued(_buffer(_q("q1", "one"), RESUMED, _q("q2", "two"))) == [("q2", "two")]


def test_a_message_the_last_resume_requeued_and_that_still_waits_is_resent_again():
    buffer = _buffer(_q("q1", "one"), _q("q2", "two"), RESUMED,
                     _requeued("q1", "n1"), _requeued("q2", "n2"), _lc("n1", "started"))
    assert undelivered_queued(buffer) == [("n2", "two")]


def test_a_fold_echo_confirms_every_message_it_carries():
    buffer = _buffer(_q("q1", "a"), _q("q2", "b"), _q("q3", "c"),
                     _echo("q3", "a\n\n", "b\n\n", "c\n\n"))
    assert undelivered_queued(buffer) == []


def test_a_legacy_echo_with_a_cli_minted_uuid_matches_by_text():
    buffer = _buffer(_q("q1", "hello"), _q("q2", "hello again"), _echo("cli-minted", "hello"))
    assert undelivered_queued(buffer) == [("q2", "hello again")]


def test_a_uuid_match_does_not_also_consume_a_same_text_message():
    buffer = _buffer(_q("q1", "yes"), _q("q2", "yes"), _echo("q1", "yes\n\n"))
    assert undelivered_queued(buffer) == [("q2", "yes")]


def test_tool_results_and_malformed_events_change_nothing():
    buffer = _buffer(
        _q("q1", "one"),
        {"type": "user", "message": {"role": "user", "content": [
            {"type": "tool_result", "tool_use_id": "t1", "content": "one"}]}},
        {"type": "x-optio-queued", "id": 5, "text": "bad"},
        {"type": "command_lifecycle", "command_uuid": None, "state": "started"},
        {"type": "x-optio-requeued", "id": "q1"},
        "not a dict",
    )
    assert undelivered_queued(buffer) == [("q1", "one")]
