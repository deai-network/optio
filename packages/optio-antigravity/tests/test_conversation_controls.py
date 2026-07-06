"""AntigravityConversation.set_control tests (engine-neutral session controls).

Antigravity exposes only the ``model`` control. Unlike grok (INLINE ACP
``session/set_model``, no restart), ``agy`` has no live transport (design §1):
each turn is a fresh ``agy -p`` process, so a model switch is **restart-based**
(claudecode precedent) — set_control records the new model id and the *next*
turn carries it as ``--model <new>`` while still resuming the captured
conversation via ``--conversation <id>`` (i.e. restart-with-new-model +
continue, without dropping conversation state).

These tests drive the fake ``agy`` (same fixtures as test_conversation.py) and
assert the argv the driver emits after a set_control("model", …) round-trip,
plus that unknown control ids are a no-op and that a closed conversation
rejects further control pushes.
"""

from __future__ import annotations

import pytest

from optio_antigravity.conversation import AntigravityConversation, ConversationClosed


async def test_set_control_model_switches_next_turn(fake_agy_conv):
    # Turn 1 mints the conversation (captures its id). set_control("model", …)
    # then records the new model; turn 2 must carry --model <new> AND resume the
    # captured conversation via --conversation <id> (restart-with-new-model +
    # continue — no fresh --new-project, no state loss).
    conv = fake_agy_conv
    await conv.send("first")
    cid = conv.conversation_id
    assert cid  # captured from turn 1

    await conv.set_control("model", "claude-sonnet-4")

    await conv.send("second")
    assert conv.last_argv_contains("--model claude-sonnet-4")
    assert conv.last_argv_contains(f"--conversation {cid}")
    assert not conv.last_argv_contains("--new-project")


async def test_set_control_unknown_id_is_noop(fake_agy_conv):
    # Antigravity exposes only the model control; any other id is silently
    # ignored (model unchanged, no state mutated).
    conv = fake_agy_conv
    await conv.set_control("model", "gemini-2.5-flash")
    await conv.set_control("thinking", "high")
    await conv.send("first")
    # The bogus control never overrode the model.
    assert conv.last_argv_contains("--model gemini-2.5-flash")


async def test_set_control_after_close_raises(fake_agy_conv):
    conv = fake_agy_conv
    await conv.close()
    assert conv.closed is True
    with pytest.raises(ConversationClosed):
        await conv.set_control("model", "claude-sonnet-4")
