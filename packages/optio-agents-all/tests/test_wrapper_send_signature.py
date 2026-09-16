"""Fix 29/W5 (wave-2 re-review of Fix 23, I2): a real wrapper's
``Conversation.send`` must accept the keyword-only advisory ``uuid``
Steering always passes (Fix 13a). ``optio-agents``' own
``test_steering.py`` only pins the ``Conversation`` Protocol's own
declaration -- a Protocol method body is ``...``, so nothing there ever
calls a real implementation, and reverting any one wrapper's signature
breaks nothing there. Walk every shipped wrapper's own ``send`` signature
the same way here, in the one package that depends on all of them, so a
narrower ``send(self, text)`` is caught before it raises ``TypeError`` on
the operator's first busy send."""
import inspect

import pytest

from optio_antigravity.conversation import AntigravityConversation
from optio_claudecode.conversation import ClaudeCodeConversation
from optio_codex.conversation import CodexConversation
from optio_cursor.conversation import CursorConversation
from optio_grok.conversation import GrokConversation
from optio_kimicode.conversation import KimiCodeConversation
from optio_opencode.conversation import OpencodeConversation

CONVERSATION_CLASSES = [
    AntigravityConversation,
    ClaudeCodeConversation,
    CodexConversation,
    CursorConversation,
    GrokConversation,
    KimiCodeConversation,
    OpencodeConversation,
]


@pytest.mark.parametrize("cls", CONVERSATION_CLASSES, ids=lambda c: c.__name__)
def test_send_accepts_the_keyword_only_advisory_uuid(cls):
    params = inspect.signature(cls.send).parameters
    assert "uuid" in params
    assert params["uuid"].kind is inspect.Parameter.KEYWORD_ONLY
    assert params["uuid"].default is None
