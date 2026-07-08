"""Public data types for optio-grok consumers.

The generic ``DeliverableCallback`` / ``HookCallback`` /
``CallerMessageCallback`` types are owned by ``optio-agents`` and ``SSHConfig``
by ``optio-host``. The engine-neutral config vocabulary (``ConversationMode`` /
``ToolVerbosity`` / ``ThinkingVerbosity`` / ``SeedProvider`` /
``SeedUnavailableError`` / ``AllowedDir``) is owned by
``optio_agents.config_types``. This module re-exports them all alongside the
package-specific ``GrokTaskConfig`` so existing
``from optio_grok.types import ...`` sites keep working unchanged.
"""

from dataclasses import dataclass
from typing import Awaitable, Callable, Literal

from optio_agents import (
    AllowedDir,
    ConversationMode,
    SeedProvider,
    SeedUnavailableError,
    ThinkingVerbosity,
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


__all__ = [
    "CallerMessageCallback",
    "DeliverableCallback",
    "HookCallback",
    "UploadCallback",
    "SSHConfig",
    "GrokTaskConfig",
    "PermissionMode",
    "GrokReasoningEffort",
    "ConversationMode",
    "ToolVerbosity",
    "ThinkingVerbosity",
    "SeedProvider",
    "SeedUnavailableError",
    "AllowedDir",
]


PermissionMode = Literal[
    "default", "acceptEdits", "auto", "dontAsk", "bypassPermissions", "plan"
]
_VALID_PERMISSION_MODES = {
    "default", "acceptEdits", "auto", "dontAsk", "bypassPermissions", "plan"
}

# Graded reasoning-effort levels grok accepts at launch (ordered low→high).
# Unlike the free-form ``effort`` passthrough, this is the engine's per-model
# reasoning budget, validated at construction time (the level set is stable
# across vendor releases; a bad value is a caller bug, not a vendor drift).
# NOTE: launch-only. grok's ACP does not advertise per-model reasoning-effort
# capability, so no live id="reasoning_effort" slider is surfaced (see
# models.parse_acp_models / conversation.set_control).
GrokReasoningEffort = Literal["low", "medium", "high", "xhigh"]
_VALID_REASONING_EFFORT = {"low", "medium", "high", "xhigh"}

# Local validation sets derived from the shared ``ToolVerbosity`` /
# ``ThinkingVerbosity`` Literals (imported from optio_agents), kept for the
# construction-time checks in ``__post_init__``.
_VALID_TOOL_VERBOSITY = {"silent", "description-only", "verbose"}
_VALID_THINKING_VERBOSITY = {"hidden", "visible"}


def _identity_resume_refresh(config: "GrokTaskConfig") -> "GrokTaskConfig":
    """Default ``on_resume_refresh``: recompose AGENTS.md from the unchanged
    config on resume, so a resumed session picks up instruction/template
    changes instead of freezing at first launch (no config mutation)."""
    return config


@dataclass(frozen=True, kw_only=True)
class GrokTaskConfig(ClaustrumConfigMixin):
    """Configuration for one optio-grok task instance.

    Inherits the claustrum filesystem-isolation triad (``fs_isolation`` /
    ``extra_allowed_dirs`` / ``delivery_type``) from ``ClaustrumConfigMixin``;
    those fields stay top-level (callers write ``fs_isolation=`` /
    ``delivery_type=`` verbatim). Frozen because the mixin is frozen;
    ``kw_only`` because the mixin contributes defaulted fields ahead of the
    required ``consumer_instructions``.
    """

    consumer_instructions: str

    agent_type: Literal["grok"] = "grok"

    env: dict[str, str] | None = None
    # Glob patterns (fnmatch) of env var NAMES to strip from the grok
    # subprocess, so inherited provider creds don't override the task's
    # own configuration. e.g. ["*_API_KEY", "*_TOKEN"].
    scrub_env: list[str] | None = None

    permission_mode: PermissionMode | None = None
    allowed_tools: list[str] | None = None
    disallowed_tools: list[str] | None = None
    # Passed through as ``--model``/``--effort``. Not validated — vendor strings
    # change.
    model: str | None = None
    effort: str | None = None
    # Graded reasoning budget (low/medium/high/xhigh). Applied at launch as the
    # initial effort (``--reasoning-effort``, like ``--model``). Launch-only:
    # grok's ACP advertises no per-model reasoning-effort capability, so there
    # is no live mid-session slider (see conversation.set_control /
    # models.parse_acp_models). Validated against the Literal below.
    reasoning_effort: GrokReasoningEffort | None = None

    ssh: SSHConfig | None = None

    install_if_missing: bool = True
    install_ttyd_if_missing: bool = True
    # Override for where the ``grok`` binary is resolved on the host.
    install_dir: str | None = None
    ttyd_install_dir: str | None = None

    # When True, a fresh launch kicks off the first turn itself — iframe mode
    # types a trailing positional prompt, conversation mode sends the
    # AUTO_START_PROMPT ("Read AGENTS.md and execute the task it describes"). This
    # is for UNATTENDED task execution; a task must opt in. Defaults to False
    # (parity with claudecode): a conversation/chat task must NOT auto-fire a
    # kickoff, or grok-build starts an agentic loop on launch and blocks the
    # operator's first real prompt (queued behind it as task_already_running).
    auto_start: bool = False
    # Always pass ``--no-leader`` so tasks never share a grok backend and
    # never touch ~/.grok/leader.sock.
    no_leader: bool = True

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

    # --- seed surface (start fresh from a stored environment) -----------
    # Consumed (default/fallback): merge this seed's environment (grok
    # auth.json + config.toml) into a fresh workdir before launch, beginning
    # a NEW session already logged-in. A plain string is used as-is; a
    # SeedProvider callable is awaited at launch to resolve one (Stage 4
    # lease path). Baked at task-creation time; ignored on resume.
    seed_id: "str | SeedProvider | None" = None
    # Capture intent: a (sync or async) callback fired on teardown of a fresh
    # session after a successful capture, with two args: (seed_id, info).
    # ``info`` is a human-readable account summary (None in Stage 3; resolved
    # in a later stage). Its presence is what enables seed capture. Both
    # default None, so existing consumers are unaffected. Both are ignored on
    # resume.
    on_seed_saved: "Callable[[str, str | None], Awaitable[None] | None] | None" = None

    # Resume machinery (Stage 2). ON by default: grok persists its session
    # under <GROK_HOME>/sessions inside the workdir, so restoring the workdir
    # tar + passing --continue rehydrates the conversation.
    supports_resume: bool = True
    # fnmatch patterns of workdir paths to omit from the resume snapshot tar.
    # None → the framework defaults (see optio_host.archive). Keep home/.grok
    # OUT of this list: it carries the grok session state that --continue needs.
    workdir_exclude: list[str] | None = None
    # Optional pair of synchronous bytes->bytes transforms wrapping the resume
    # snapshot's workdir tar at GridFS write/read. grok's session store lives
    # under home/.grok INSIDE the workdir, so this one tar IS the session blob
    # (unlike claudecode's separate home/.claude blob). Both set → encrypted at
    # rest; both None (default) → plaintext. Setting only one is a config error
    # (asymmetric usage is always a mistake).
    session_blob_encrypt: Callable[[bytes], bytes] | None = None
    session_blob_decrypt: Callable[[bytes], bytes] | None = None
    # Hook fired on resume only (never on fresh start). Receives the original
    # config; returns a (possibly mutated) config. The harness re-renders
    # AGENTS.md from the returned config and writes it back only when it differs
    # from the file restored by the snapshot, tagging the next resume.log line
    # with `REFRESHED:AGENTS.md` so the agent re-reads. Default = identity
    # (recompose from the same config, so instruction/template changes reach a
    # resumed session instead of freezing at first launch). Pass None to disable.
    on_resume_refresh: "Callable[[GrokTaskConfig], GrokTaskConfig] | None" = _identity_resume_refresh

    # "iframe" = ttyd TUI (Stages 0-5). "conversation" = headless ACP
    # (grok agent stdio); the task publishes a live GrokConversation (Stage 6).
    mode: ConversationMode = "iframe"
    # Opt-out for the optio.log keyword channel (STATUS/DELIVERABLE/DONE/…).
    # iframe mode requires True (it is the only completion signal there);
    # conversation mode may set it False (the Conversation drives completion).
    host_protocol: bool = True

    # --- conversation surface (Stage 6) ---------------------------------
    # Conversation mode only: route grok's ACP session/request_permission to
    # the published conversation's on_permission_request handler (the caller
    # registers one). When False, grok launches with --always-approve so tools
    # run without prompting.
    permission_gate: bool = False
    # Opt-in dashboard conversation UI: the task starts a per-task listener and
    # publishes a live chat widget (wired in Group 6b). Conversation mode only.
    conversation_ui: bool = False
    # How much tool-call detail the conversation widget renders; only affects
    # conversation_ui rendering.
    tool_verbosity: ToolVerbosity = "description-only"

    # Whether the conversation widget shows grok's reasoning/thinking traces
    # (agent_thought_chunk). Default hidden — thinking is noisy; opt in per task.
    thinking_verbosity: ThinkingVerbosity = "hidden"

    # --- conversation frontend parity (Stage 7) -------------------------
    # Show the engine-neutral session controls in the conversation widget.
    # Grok exposes only the model control, switched inline over ACP
    # (session/set_model) — no process restart. Requires mode="conversation"
    # and conversation_ui=True.
    show_session_controls: bool = False
    # Replace the generic working-spinner with grok's on-brand native spinner
    # in the conversation widget. Requires mode="conversation" and
    # conversation_ui=True.
    native_spinner: bool = False
    # Show the file-upload control. Uploaded bytes are written under
    # <workdir>/uploads and referenced to grok via a System: path line so it
    # reads them with its own tools. Requires mode="conversation" and
    # conversation_ui=True.
    show_file_upload: bool = False
    # Per-task upload hook, fired (additively to the System: path line) after an
    # uploaded file lands under <workdir>/uploads. Mirrors on_deliverable minus
    # the text arg: ``async on_upload(hook_ctx, "uploads/<name>")``. Optional;
    # the workdir write + System: reference happen whether or not it is set.
    on_upload: UploadCallback | None = None
    # Upper bound (bytes) on a single uploaded file; the listener rejects
    # anything larger with HTTP 413. Mirrored to the widget via widgetData.
    max_upload_bytes: int = 10_000_000
    # Offer download links for files grok marks with the optio-file: sentinel.
    # The listener serves GET /download for paths confined under <workdir>.
    # Requires mode="conversation" and conversation_ui=True.
    file_download: bool = False
    # Upper bound (bytes) on a single downloaded file; the listener rejects
    # anything larger with HTTP 413. Mirrored to the widget via widgetData.
    max_download_bytes: int = 10_000_000

    # --- filesystem isolation -------------------------------------------
    # The claustrum triad — ``fs_isolation`` (default-ON), ``extra_allowed_dirs``,
    # and the MANDATORY-when-on ``delivery_type`` — is inherited from
    # ClaustrumConfigMixin. Claustrum (Landlock, fail-closed) confines the grok
    # process and every tool/subprocess it spawns to the task workdir + explicit
    # grants; if the kernel cannot apply Landlock the task refuses to launch.
    # ``~/`` grants expand against the real host home at launch. See
    # session._build_claustrum_wrap for the grant set.

    def __post_init__(self) -> None:
        # Validate the claustrum triad first so a missing delivery_type fails
        # fast (before the other, engine-specific checks below).
        self._validate_claustrum()
        if (
            self.permission_mode is not None
            and self.permission_mode not in _VALID_PERMISSION_MODES
        ):
            raise ValueError(
                f"GrokTaskConfig.permission_mode={self.permission_mode!r} "
                f"is not one of {sorted(_VALID_PERMISSION_MODES)}"
            )
        if (
            self.reasoning_effort is not None
            and self.reasoning_effort not in _VALID_REASONING_EFFORT
        ):
            raise ValueError(
                f"GrokTaskConfig.reasoning_effort={self.reasoning_effort!r} "
                f"is not one of {sorted(_VALID_REASONING_EFFORT)}"
            )
        if self.mode not in ("iframe", "conversation"):
            raise ValueError(
                f"GrokTaskConfig.mode={self.mode!r} is not one of "
                "['iframe', 'conversation']"
            )
        if self.mode == "iframe" and not self.host_protocol:
            raise ValueError(
                "GrokTaskConfig: host_protocol=False requires "
                "mode='conversation' (in iframe mode the optio.log keyword "
                "channel is the only completion signal)."
            )
        if self.permission_gate and self.mode != "conversation":
            raise ValueError(
                "GrokTaskConfig: permission_gate=True requires "
                "mode='conversation'."
            )
        if self.conversation_ui and self.mode != "conversation":
            raise ValueError(
                "GrokTaskConfig: conversation_ui=True requires "
                "mode='conversation'."
            )
        if self.tool_verbosity not in _VALID_TOOL_VERBOSITY:
            raise ValueError(
                f"GrokTaskConfig.tool_verbosity={self.tool_verbosity!r} "
                f"is not one of {sorted(_VALID_TOOL_VERBOSITY)}"
            )
        if self.thinking_verbosity not in _VALID_THINKING_VERBOSITY:
            raise ValueError(
                f"GrokTaskConfig.thinking_verbosity={self.thinking_verbosity!r} "
                f"is not one of {sorted(_VALID_THINKING_VERBOSITY)}"
            )
        # Session-blob transforms are all-or-nothing: encrypting on write with
        # no matching decrypt on read (or vice versa) always corrupts resume.
        e = self.session_blob_encrypt is not None
        d = self.session_blob_decrypt is not None
        if e != d:
            raise ValueError(
                "GrokTaskConfig: session_blob_encrypt and session_blob_decrypt "
                "must be set together or both left None; one without the other "
                "is a config error."
            )
        # Frontend-parity features are opt-in flags that only make sense with
        # the conversation UI wired (mirrors optio-claudecode).
        conv_ui = self.mode == "conversation" and self.conversation_ui
        if self.show_session_controls and not conv_ui:
            raise ValueError(
                "GrokTaskConfig: show_session_controls=True requires "
                "mode='conversation' and conversation_ui=True."
            )
        if self.native_spinner and not conv_ui:
            raise ValueError(
                "GrokTaskConfig: native_spinner=True requires "
                "mode='conversation' and conversation_ui=True."
            )
        if self.show_file_upload and not conv_ui:
            raise ValueError(
                "GrokTaskConfig: show_file_upload=True requires "
                "mode='conversation' and conversation_ui=True."
            )
        if self.file_download and not conv_ui:
            raise ValueError(
                "GrokTaskConfig: file_download=True requires "
                "mode='conversation' and conversation_ui=True."
            )
        for field_name in ("install_dir", "ttyd_install_dir"):
            val = getattr(self, field_name)
            if val is not None and not val.startswith("/") and not val.startswith("~"):
                raise ValueError(
                    f"GrokTaskConfig.{field_name}={val!r} must be an "
                    f"absolute path (start with '/' or '~')."
                )
