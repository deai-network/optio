"""Per-provider account handlers for the opencode meta-analyzer.

opencode has no native account — a seed's ``auth.json`` holds credentials for
whichever third-party provider(s) the operator logged into. Each handler
``async def handle(entry: dict) -> AccountInfo | None`` maps one provider's
auth entry into the shared ``AccountInfo`` (or ``None`` → the meta-analyzer
emits a placeholder). This phase ships the three OAuth reuse handlers
(anthropic, openai, xai), each delegating to the extracted vendor map helper.
"""

from optio_opencode.providers import anthropic, openai, xai

__all__ = ["anthropic", "openai", "xai"]
