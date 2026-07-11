"""Public data types for optio-claudecode consumers.

The generic ``DeliverableCallback`` / ``HookCallback`` types are owned by
``optio-agents`` and ``SSHConfig`` by ``optio-host``. This module
re-exports them alongside the package-specific ``ClaudeCodeTaskConfig``.
"""

from dataclasses import dataclass
from typing import Any, Awaitable, Callable, Literal

from bson import ObjectId

from optio_agents import (
    AllowedDir,
    ConversationMode,
    SeedProvider,
    SeedUnavailableError,
    ThinkingVerbosity,
    TOOL_VERBOSITIES,
    ToolVerbosity,
)
from optio_agents.config_types import ClaustrumConfigMixin
from optio_agents.protocol.session import (
    CallerMessageCallback,
    DeliverableCallback,
    HookCallback,
)
from optio_agents.uploads import UploadCallback
from optio_host.types import SSHConfig

# AllowedDir, ConversationMode, ToolVerbosity, ThinkingVerbosity, SeedProvider
# and SeedUnavailableError are the shared vocabulary owned by optio_agents; they
# are imported above and re-exported here (see __all__) so existing
# ``from optio_claudecode.types import AllowedDir, …`` sites keep working. The
# shared AllowedDir validates ``mode`` at construction (rox/rwx superset;
# Landlock-only claustrum treats rox==ro, rwx==rw). A leading ``~/`` in an
# AllowedDir.path is expanded against the REAL host home at launch time (the
# claude process runs under an isolated $HOME, so grants cannot rely on its
# shell expansion).


__all__ = [
    "CallerMessageCallback",
    "DeliverableCallback",
    "HookCallback",
    "UploadCallback",
    "SSHConfig",
    "ClaudeCodeTaskConfig",
    "ConversationMode",
    "PermissionMode",
    "ReasoningEffort",
    "SeedProvider",
    "SeedUnavailableError",
    "ToolVerbosity",
    "ThinkingVerbosity",
    "AllowedDir",
]


PermissionMode = Literal["default", "plan", "acceptEdits", "bypassPermissions", "dontAsk"]
_VALID_PERMISSION_MODES = {"default", "plan", "acceptEdits", "bypassPermissions", "dontAsk"}
_HEADLESS_SAFE_PERMISSION_MODES = {"acceptEdits", "bypassPermissions", "dontAsk"}

# Graded reasoning-effort levels claude's --effort flag accepts (ordered). The
# live control (id="reasoning_effort") is a slider over these; only models that
# advertise graded effort expose it (see models.model_effort).
ReasoningEffort = Literal["low", "medium", "high", "xhigh", "max"]
_VALID_REASONING_EFFORTS = {"low", "medium", "high", "xhigh", "max"}

_VALID_TOOL_VERBOSITY = TOOL_VERBOSITIES

# Visibility of reasoning/thinking traces in the conversation widget. Task-level,
# mirrors ToolVerbosity; the UI never decides. (Claude reasoning is not yet wired
# to a distinct thinking row, but the option ships for cross-engine parity.)
_VALID_THINKING_VERBOSITY = {"hidden", "visible"}


def _identity_resume_refresh(config: "ClaudeCodeTaskConfig") -> "ClaudeCodeTaskConfig":
    """Default ``on_resume_refresh``: recompose CLAUDE.md from the unchanged
    config on resume, so a resumed session picks up instruction/template
    changes instead of freezing at first launch (no config mutation)."""
    return config


@dataclass(frozen=True, kw_only=True)
class ClaudeCodeTaskConfig(ClaustrumConfigMixin):
    """Configuration for one optio-claudecode task instance.

    See ``docs/2026-05-28-optio-claudecode-design.md`` for full field
    semantics.

    Inherits the claustrum filesystem-isolation triad (``fs_isolation`` /
    ``extra_allowed_dirs`` / ``delivery_type``) from ``ClaustrumConfigMixin``;
    those fields stay top-level here (callers write ``fs_isolation=`` /
    ``delivery_type=`` verbatim). Frozen because the mixin is frozen; ``kw_only``
    because the mixin's defaulted triad would otherwise precede the required
    ``consumer_instructions``.
    """

    consumer_instructions: str

    agent_type: Literal["claudecode"] = "claudecode"

    credentials_json: dict[str, Any] | bytes | str | None = None
    claude_config: dict[str, Any] | None = None
    env: dict[str, str] | None = None
    # Glob patterns (fnmatch) of env var NAMES to strip from the Claude Code
    # subprocess, so inherited provider creds (e.g. ANTHROPIC_API_KEY) don't
    # override the customer's subscription seed. e.g. ["*_API_KEY", "*_TOKEN"].
    scrub_env: list[str] | None = None

    permission_mode: PermissionMode | None = None
    allowed_tools: list[str] | None = None
    disallowed_tools: list[str] | None = None
    # When True, a fresh launch passes a trailing positional prompt
    # ("Read CLAUDE.md and execute the task it describes") so claude starts the
    # task unattended. Suppressed on resume (--continue) to avoid re-triggering.
    auto_start: bool = False
    # When True, run claude in focus view + fullscreen TUI (settings.json
    # tui=fullscreen, viewMode=focus) with CLAUDE_CODE_NO_FLICKER=1 in the launch
    # env, so tool calls collapse to one-line summaries instead of showing every
    # bash command/output. Layered onto any consumer-supplied claude_config.
    focus_mode: bool = False

    # --- filesystem isolation (claustrum) ------------------------------
    # fs_isolation / extra_allowed_dirs / delivery_type are inherited from
    # ClaustrumConfigMixin (claustrum Landlock sandbox, fail-closed). When
    # fs_isolation is on (default), delivery_type is MANDATORY — validated by
    # _validate_claustrum(), called from __post_init__ below.

    ssh: SSHConfig | None = None

    install_if_missing: bool = True
    install_ttyd_if_missing: bool = True
    # Override for the optio-owned claude **version cache** directory (where
    # claude version binaries are installed/cached on the worker, via the
    # per-task home/.local/share/claude/versions symlink). None → the worker's
    # ``OPTIO_CLAUDECODE_CACHE_DIR`` or ``${XDG_CACHE_HOME:-$HOME/.cache}/
    # optio-claudecode/versions``. Never the host user's ~/.local/~/.claude.
    install_dir: str | None = None
    ttyd_install_dir: str | None = None

    before_execute: HookCallback | None = None
    after_execute: HookCallback | None = None
    on_deliverable: DeliverableCallback | None = None
    # Enable the CLIENT_MESSAGE keyword: agent-pushed messages routed to the
    # originating browser session's frontend (stored as sessionEvents,
    # surfaced via optio-ui's onClientMessage). Off by default.
    use_client_messages: bool = False
    # Enable the CALLER_MESSAGE keyword: agent-pushed messages routed to this
    # callback in the embedding application. A non-None return value is sent
    # back to the agent as feedback. Off (None) by default.
    on_caller_message: CallerMessageCallback | None = None

    # --- resume surface (mirrors OpencodeTaskConfig) --------------------
    supports_resume: bool = True
    workdir_exclude: list[str] | None = None
    # Optional pair of synchronous bytes->bytes transforms wrapping the
    # home/.claude session tar at GridFS write/read. Both set → encrypted
    # at rest; both None (default) → plaintext. Setting only one is a
    # config error (asymmetric usage is always a mistake).
    session_blob_encrypt: Callable[[bytes], bytes] | None = None
    session_blob_decrypt: Callable[[bytes], bytes] | None = None
    # Hook fired on resume only (never on fresh start). Receives the original
    # config; returns a (possibly mutated) config. The harness re-renders
    # CLAUDE.md from the returned config and writes it back only when it
    # differs from the file on disk, tagging the next resume.log line with
    # `REFRESHED:CLAUDE.md` so the agent re-reads. Default = identity (recompose
    # from the same config, so instruction/template changes reach a resumed
    # session instead of freezing at first launch). Pass None to disable.
    on_resume_refresh: "Callable[[ClaudeCodeTaskConfig], ClaudeCodeTaskConfig] | None" = _identity_resume_refresh

    # --- seed surface (start fresh from a stored environment) -----------
    # Consumed (default/fallback): merge this seed's environment into a
    # fresh workdir before launch, beginning a NEW conversation (no
    # --continue). Baked at task-creation time; no per-launch channel.
    seed_id: "str | SeedProvider | None" = None
    # Capture intent: a (sync or async) callback fired on teardown of a fresh
    # session after a successful capture, with two args: (seed_id, info).
    # ``info`` is a human-readable account summary derived from the seeded
    # OAuth token (e.g. "Plan: Claude Max 20x for Jane Doe <jane@x.com>"), or
    # None if it could not be resolved. Its presence is what enables seed
    # capture. Both default None, so existing consumers are unaffected. Both
    # are ignored on resume.
    on_seed_saved: "Callable[[str, str | None], Awaitable[None] | None] | None" = None

    # --- conversation surface (spec: 2026-06-10 conversation gate) -------
    # "iframe" = today's tmux+ttyd behavior (default, unchanged).
    # "conversation" = headless stream-json session; the task publishes a
    # ClaudeCodeConversation via ctx.publish_result.
    mode: ConversationMode = "iframe"
    # Opt-out for the optio.log keyword channel (STATUS/DELIVERABLE/DONE/…).
    # iframe mode requires True (it is the only completion signal there).
    host_protocol: bool = True
    # Conversation mode only: route Claude Code's can_use_tool permission
    # questions to the Conversation's on_permission_request handler over the
    # stream-json control protocol.
    permission_gate: bool = False
    # Conversation mode only: pass --include-partial-messages so stream_event
    # text deltas reach on_event subscribers while a turn runs. Also implied
    # by conversation_ui (its live view is fed by partials).
    include_partial_messages: bool = False
    # Opt-in dashboard conversation UI: the task starts a per-task listener
    # (SSE event stream + send/interrupt/permission POSTs) and registers it
    # as widgetUpstream. The published Conversation object remains the
    # default gate; this is a deliberate parallel path. Conversation mode only.
    conversation_ui: bool = False
    # Conversation-UI tool-call rendering: "verbose" = full input table,
    # "description-only" = one summary line, "silent" = nothing. Carried to the
    # widget via widgetData; only affects conversation_ui rendering.
    tool_verbosity: ToolVerbosity = "description-only"
    # Whether the conversation widget shows the agent's reasoning/thinking traces.
    # Default hidden — thinking is noisy; opt in per task.
    thinking_verbosity: ThinkingVerbosity = "hidden"
    # Show the generic session controls (currently the model picker) in the
    # conversation widget. Requires mode="conversation" and
    # conversation_ui=True. The default model is config.model (no separate
    # field).
    show_session_controls: bool = False
    # Replace the generic working-spinner with claudecode's on-brand native
    # spinner in the conversation widget. Requires mode="conversation" and
    # conversation_ui=True.
    native_spinner: bool = False
    # Show the file-upload control in the conversation widget. Requires
    # mode="conversation" and conversation_ui=True. Uploaded files are written
    # under <workdir>/uploads on the host; carried to the widget via widgetData.
    show_file_upload: bool = False
    # Optional per-task callback fired AFTER an uploaded file lands in
    # <workdir>/uploads/<name>, with (hook_ctx, relpath). Additive to the
    # System: LLM announce; a raising callback is logged, never fatal. Mirrors
    # on_deliverable minus the text arg.
    on_upload: UploadCallback | None = None
    # Upper bound (bytes) on a single uploaded file; the widget refuses to POST
    # anything larger. Mirrored to the widget via widgetData.
    max_upload_bytes: int = 10_000_000
    # Offer download links for files Claude marks as deliverables in the
    # conversation widget. Requires mode="conversation" and conversation_ui=True.
    # The listener serves GET /download for paths confined under <workdir>;
    # carried to the widget via widgetData.
    file_download: bool = False
    # Upper bound (bytes) on a single downloaded file; the listener rejects
    # anything larger with HTTP 413. Mirrored to the widget via widgetData.
    max_download_bytes: int = 10_000_000

    # --- explicit session restore (spec: 2026-06-10 session restore) -----
    # session_restore_from: GridFS blob id of a home/.claude session tar (as
    # produced by on_session_saved); planted before launch on FRESH runs
    # only (optio-level resume ignores it). Conversation mode only.
    session_restore_from: "ObjectId | None" = None
    # session_restore_until: transcript entry uuid — keep history up to and
    # including this entry, drop the rest. Requires session_restore_from.
    session_restore_until: str | None = None
    # on_session_saved: (new_blob_id, end_state) fired at teardown after the
    # session blob is stored under a standalone GridFS ref. Presence opts
    # in to capture; runs on all end states (done/failed/cancelled).
    on_session_saved: "Callable[[ObjectId, str], Awaitable[None] | None] | None" = None
    # model: passed through as `--model <value>`. Not validated.
    model: str | None = None
    # reasoning_effort: initial graded reasoning effort, passed through as
    # `--effort <value>` at launch (like model). Applied only when the running
    # model supports graded effort; the live slider control (id=
    # "reasoning_effort") restarts claude with a new --effort the same way a
    # model change does. Validated against ReasoningEffort below.
    reasoning_effort: "ReasoningEffort | None" = None

    def __post_init__(self) -> None:
        # Validate the inherited claustrum triad first, so a missing
        # delivery_type (with fs_isolation on) fails fast.
        self._validate_claustrum()
        if self.permission_mode is not None and self.permission_mode not in _VALID_PERMISSION_MODES:
            raise ValueError(
                f"ClaudeCodeTaskConfig.permission_mode={self.permission_mode!r} "
                f"is not one of {sorted(_VALID_PERMISSION_MODES)}"
            )
        if self.reasoning_effort is not None and self.reasoning_effort not in _VALID_REASONING_EFFORTS:
            raise ValueError(
                f"ClaudeCodeTaskConfig.reasoning_effort={self.reasoning_effort!r} "
                f"is not one of {sorted(_VALID_REASONING_EFFORTS)}"
            )
        for field_name in ("install_dir", "ttyd_install_dir"):
            val = getattr(self, field_name)
            if val is not None and not val.startswith("/") and not val.startswith("~"):
                raise ValueError(
                    f"ClaudeCodeTaskConfig.{field_name}={val!r} must be an "
                    f"absolute path (start with '/' or '~')."
                )
        e = self.session_blob_encrypt is not None
        d = self.session_blob_decrypt is not None
        if e != d:
            raise ValueError(
                "ClaudeCodeTaskConfig: session_blob_encrypt and "
                "session_blob_decrypt must be set together (both callables) "
                "or both left as None; one without the other is a config error."
            )
        if self.mode not in ("iframe", "conversation"):
            raise ValueError(
                f"ClaudeCodeTaskConfig.mode={self.mode!r} is not one of "
                "['iframe', 'conversation']"
            )
        if self.mode == "iframe" and not self.host_protocol:
            raise ValueError(
                "ClaudeCodeTaskConfig: host_protocol=False requires "
                "mode='conversation' (in iframe mode the optio.log keyword "
                "channel is the only completion signal)."
            )
        if self.permission_gate and self.mode != "conversation":
            raise ValueError(
                "ClaudeCodeTaskConfig: permission_gate=True requires "
                "mode='conversation'."
            )
        if self.mode == "conversation" and not self.permission_gate:
            headless_ok = (
                self.permission_mode in _HEADLESS_SAFE_PERMISSION_MODES
                or bool(self.allowed_tools)
            )
            if not headless_ok:
                raise ValueError(
                    "ClaudeCodeTaskConfig: conversation mode without "
                    "permission_gate needs a non-interactive permission "
                    "setup — permission_mode in "
                    f"{sorted(_HEADLESS_SAFE_PERMISSION_MODES)} or a "
                    "non-empty allowed_tools (headless Claude cannot show "
                    "a permission dialog)."
                )
        if self.conversation_ui and self.mode != "conversation":
            raise ValueError(
                "ClaudeCodeTaskConfig: conversation_ui=True requires "
                "mode='conversation'."
            )
        if self.show_session_controls and not (self.mode == "conversation" and self.conversation_ui):
            raise ValueError(
                "ClaudeCodeTaskConfig: show_session_controls=True requires "
                "mode='conversation' and conversation_ui=True."
            )
        if self.native_spinner and not (self.mode == "conversation" and self.conversation_ui):
            raise ValueError(
                "ClaudeCodeTaskConfig: native_spinner=True requires "
                "mode='conversation' and conversation_ui=True."
            )
        if self.show_file_upload and not (self.mode == "conversation" and self.conversation_ui):
            raise ValueError(
                "ClaudeCodeTaskConfig: show_file_upload=True requires "
                "mode='conversation' and conversation_ui=True."
            )
        if self.file_download and not (self.mode == "conversation" and self.conversation_ui):
            raise ValueError(
                "ClaudeCodeTaskConfig: file_download=True requires "
                "mode='conversation' and conversation_ui=True."
            )
        if self.tool_verbosity not in _VALID_TOOL_VERBOSITY:
            raise ValueError(
                f"ClaudeCodeTaskConfig.tool_verbosity={self.tool_verbosity!r} "
                f"is not one of {sorted(_VALID_TOOL_VERBOSITY)}"
            )
        if self.thinking_verbosity not in _VALID_THINKING_VERBOSITY:
            raise ValueError(
                f"ClaudeCodeTaskConfig.thinking_verbosity={self.thinking_verbosity!r} "
                f"is not one of {sorted(_VALID_THINKING_VERBOSITY)}"
            )
        if self.session_restore_until is not None and self.session_restore_from is None:
            raise ValueError(
                "session_restore_until requires session_restore_from"
            )
        if self.session_restore_from is not None and self.mode != "conversation":
            raise ValueError(
                "session_restore_from requires mode='conversation'"
            )
        if self.session_restore_from is not None and self.auto_start:
            raise ValueError(
                "session_restore_from is incompatible with auto_start "
                "(a restored conversation is continued by the caller)"
            )
