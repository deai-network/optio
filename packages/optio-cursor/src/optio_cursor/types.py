"""Public data types for optio-cursor consumers.

The generic ``DeliverableCallback`` / ``HookCallback`` types are owned by
``optio-agents`` and ``SSHConfig`` by ``optio-host``. This module
re-exports them alongside the package-specific ``CursorTaskConfig``.
"""

from dataclasses import dataclass
from typing import Awaitable, Callable, Literal

from optio_agents.protocol.session import (
    DeliverableCallback,
    HookCallback,
)
from optio_host.types import SSHConfig


__all__ = [
    "DeliverableCallback",
    "HookCallback",
    "SSHConfig",
    "CursorTaskConfig",
    "SandboxMode",
    "ConversationMode",
    "ToolVerbosity",
    "SeedProvider",
    "SeedUnavailableError",
]


# A seed provider resolves a usable seed_id at launch time (e.g. leasing one
# from a pool). Mirrors optio-grok's SeedProvider; the callable/lease path is
# exercised in Stage 4 — Stage 3 only needs a static string seed_id.
SeedProvider = Callable[[str], Awaitable[str]]


class SeedUnavailableError(Exception):
    """Raised by a seed provider when no usable seed is available; the message
    is surfaced as the process failure."""


# Passed through as ``--sandbox``. cursor-agent's own filesystem sandbox
# toggle; None omits the flag (cursor's default applies).
SandboxMode = Literal["enabled", "disabled"]
_VALID_SANDBOX_MODES = {"enabled", "disabled"}

# "iframe" = ttyd TUI in the browser (Stages 0-5). "conversation" = a headless
# ``cursor-agent acp`` (ACP) session; the task publishes a live
# CursorConversation via ctx.publish_result (Stage 6).
ConversationMode = Literal["iframe", "conversation"]

# Verbosity of tool-call rendering in the conversation widget (conversation_ui
# only). Mirrors optio-grok/claudecode; consumed by the dashboard reducer.
ToolVerbosity = Literal["silent", "description-only", "verbose"]
_VALID_TOOL_VERBOSITY = {"silent", "description-only", "verbose"}


@dataclass
class CursorTaskConfig:
    """Configuration for one optio-cursor task instance (Stage 0).

    Stage 0 covers iframe/ttyd mode on the local host. Resume, seeds,
    conversation mode, and filesystem isolation arrive in later stages.
    """

    consumer_instructions: str

    env: dict[str, str] | None = None
    # Glob patterns (fnmatch) of env var NAMES to strip from the cursor-agent
    # subprocess, so inherited provider creds don't override the task's
    # own configuration. e.g. ["*_API_KEY", "*_TOKEN"].
    scrub_env: list[str] | None = None

    # Permission rules are config-planted, not argv: cursor-agent has no
    # --allow/--deny flags. These map to ``permissions.allow`` /
    # ``permissions.deny`` in ``<home>/.cursor/cli-config.json``.
    allowed_tools: list[str] | None = None
    disallowed_tools: list[str] | None = None
    # ``--force``: skip cursor's interactive confirmation prompts.
    force: bool = False
    # ``--auto-review``: cursor reviews its own edits before applying.
    auto_review: bool = False
    # ``--sandbox enabled|disabled``: cursor-agent's native sandbox toggle.
    sandbox: SandboxMode | None = None
    # Passed through as ``--model``. Not validated — vendor strings change.
    model: str | None = None
    # Injected as ``CURSOR_API_KEY`` in the launch env, never argv.
    api_key: str | None = None

    ssh: SSHConfig | None = None

    install_if_missing: bool = True
    install_ttyd_if_missing: bool = True
    # Override for where the ``cursor-agent`` binary is resolved on the host.
    cursor_install_dir: str | None = None
    ttyd_install_dir: str | None = None

    # When True, a fresh launch passes a trailing positional prompt
    # ("Read AGENTS.md and execute the task it describes") so cursor-agent
    # starts the task unattended.
    auto_start: bool = True

    before_execute: HookCallback | None = None
    after_execute: HookCallback | None = None
    on_deliverable: DeliverableCallback | None = None

    # --- seed surface (start fresh from a stored environment) -----------
    # Consumed (default/fallback): merge this seed's environment (cursor
    # auth.json + cli-config.json) into a fresh workdir before launch,
    # beginning a NEW session already logged-in. A plain string is used as-is;
    # a SeedProvider callable is awaited at launch to resolve one (Stage 4
    # lease path). Baked at task-creation time; ignored on resume.
    seed_id: "str | SeedProvider | None" = None
    # Capture intent: a (sync or async) callback fired on teardown of a fresh
    # session after a successful capture, with two args: (seed_id, info).
    # ``info`` is a human-readable account summary (None in Stage 3; resolved
    # in a later stage). Its presence is what enables seed capture. Both
    # default None, so existing consumers are unaffected. Both are ignored on
    # resume.
    on_seed_saved: "Callable[[str, str | None], Awaitable[None] | None] | None" = None

    # Resume machinery (Stage 2). ON by default: cursor persists its chat
    # state under $HOME/.cursor inside the workdir, so restoring the workdir
    # tar + passing --continue rehydrates the conversation.
    supports_resume: bool = True
    # fnmatch patterns of workdir paths to omit from the resume snapshot tar.
    # None → the framework defaults (see optio_host.archive). Keep home/.cursor
    # OUT of this list: it carries the cursor chat state --continue needs.
    workdir_exclude: list[str] | None = None

    # "iframe" = ttyd TUI (Stages 0-5). "conversation" = headless ACP
    # (cursor-agent acp); the task publishes a live CursorConversation
    # (Stage 6).
    mode: ConversationMode = "iframe"
    # Opt-out for the optio.log keyword channel (STATUS/DELIVERABLE/DONE/…).
    # iframe mode requires True (it is the only completion signal there);
    # conversation mode may set it False (the Conversation drives completion).
    host_protocol: bool = True

    # --- conversation surface (Stage 6) ---------------------------------
    # Conversation mode only: route cursor's ACP session/request_permission to
    # the published conversation's on_permission_request handler (the caller
    # registers one). When False, cursor launches with --force so tools run
    # without prompting.
    permission_gate: bool = False
    # Opt-in dashboard conversation UI: the task starts a per-task listener and
    # publishes a live chat widget (wired in Group 6b). Conversation mode only.
    conversation_ui: bool = False
    # How much tool-call detail the conversation widget renders; only affects
    # conversation_ui rendering.
    tool_verbosity: ToolVerbosity = "description-only"

    def __post_init__(self) -> None:
        if self.sandbox is not None and self.sandbox not in _VALID_SANDBOX_MODES:
            raise ValueError(
                f"CursorTaskConfig.sandbox={self.sandbox!r} "
                f"is not one of {sorted(_VALID_SANDBOX_MODES)}"
            )
        if self.mode not in ("iframe", "conversation"):
            raise ValueError(
                f"CursorTaskConfig.mode={self.mode!r} is not one of "
                "['iframe', 'conversation']"
            )
        if self.mode == "iframe" and not self.host_protocol:
            raise ValueError(
                "CursorTaskConfig: host_protocol=False requires "
                "mode='conversation' (in iframe mode the optio.log keyword "
                "channel is the only completion signal)."
            )
        if self.permission_gate and self.mode != "conversation":
            raise ValueError(
                "CursorTaskConfig: permission_gate=True requires "
                "mode='conversation'."
            )
        if self.conversation_ui and self.mode != "conversation":
            raise ValueError(
                "CursorTaskConfig: conversation_ui=True requires "
                "mode='conversation'."
            )
        if self.tool_verbosity not in _VALID_TOOL_VERBOSITY:
            raise ValueError(
                f"CursorTaskConfig.tool_verbosity={self.tool_verbosity!r} "
                f"is not one of {sorted(_VALID_TOOL_VERBOSITY)}"
            )
        for field_name in ("cursor_install_dir", "ttyd_install_dir"):
            val = getattr(self, field_name)
            if val is not None and not val.startswith("/") and not val.startswith("~"):
                raise ValueError(
                    f"CursorTaskConfig.{field_name}={val!r} must be an "
                    f"absolute path (start with '/' or '~')."
                )
