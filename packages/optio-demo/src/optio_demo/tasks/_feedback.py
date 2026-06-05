"""Shared on_deliverable helper that exercises the agent feedback channel.

The harness withholds one formatting requirement from the task prompt (the
"prank"): the deliverable must end with "over and out". When the agent's
delivery lacks it, the hook returns a nudge string; the deliverable loop
routes that back into the live agent via send_to_agent (HTTP for opencode,
tmux fake-typing for claudecode). The agent re-emits a corrected delivery.

A per-process nudge cap bounds a non-complying agent so the demo can't
ping-pong a real paid session forever.
"""

from __future__ import annotations

_MARKER = "over and out"
_NUDGE = (
    'Always finish your deliverables by "over and out." '
    "Otherwise I won't know that you have finished talking."
)
_CAP = 2
_nudges: dict[str, int] = {}  # process_id -> count


def make_feedback_on_deliverable(tag: str):
    """Build an on_deliverable callback that rejects deliveries missing the
    marker (returning a nudge for the loop to send), accepts those that have
    it, and gives up after _CAP nudges per process."""

    async def _on_deliverable(hook_ctx, path, text) -> "str | None":
        print(f"[{tag}] deliverable {path}:\n{text}")
        if text.strip().rstrip(".").lower().endswith(_MARKER):
            return None  # accept
        pid = hook_ctx.process_id
        n = _nudges.get(pid, 0)
        if n >= _CAP:
            hook_ctx.report_progress(None, "feedback: nudge cap reached, accepting")
            return None
        _nudges[pid] = n + 1
        hook_ctx.report_progress(None, f"feedback: nudging agent (#{n + 1})")
        return _NUDGE  # loop auto-sends via send_to_agent (return-value routing)

    return _on_deliverable
