"""Public data types for optio-codex consumers.

The generic ``DeliverableCallback`` / ``HookCallback`` / ``UploadCallback`` /
``CallerMessageCallback`` types are owned by ``optio-agents`` and ``SSHConfig``
by ``optio-host``; the config vocabulary (``ConversationMode`` /
``ToolVerbosity`` / ``ThinkingVerbosity`` / ``SeedProvider`` /
``SeedUnavailableError`` / ``AllowedDir``) is owned by
``optio_agents.config_types``. This module imports and re-exports them (see
``__all__``) so existing ``from optio_codex.types import …`` sites keep
working. The shared ``AllowedDir`` validates ``mode`` at construction against
the 4-value superset ``ro``/``rw``/``rox``/``rwx``; codex's native sandbox has
no execute-bit concept, so it treats ``rox``==``ro`` and ``rwx``==``rw``.

Native tool allow/deny gap: codex exposes NO per-tool allow/deny mechanism the
wrapper can drive — its permission profiles cover filesystem/network posture
only (``ask_for_approval`` + ``sandbox``/``network_access``), and the
``[tools]`` config keys are boolean capability toggles, not an allow/deny
list. So ``CodexTaskConfig`` deliberately OMITS the generic
``allowed_tools``/``disallowed_tools`` fields present on the engines that can
honor them (kimi/grok/cursor/claudecode/opencode). Revisit only if codex adds
a real per-tool permission grammar.
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
    "AllowedDir",
    "CodexTaskConfig",
    "ConversationMode",
    "ToolVerbosity",
    "ThinkingVerbosity",
    "ApprovalPolicy",
    "SandboxMode",
    "SeedProvider",
    "SeedUnavailableError",
]


# ``ConversationMode`` values: "iframe" = ttyd TUI in the browser;
# "conversation" = a headless ``codex app-server`` session (the task publishes
# a live CodexConversation via ctx.publish_result, Stage 6).
_VALID_MODES = {"iframe", "conversation"}
# Validation sets mirroring the shared ``ToolVerbosity`` / ``ThinkingVerbosity``
# Literals (used by ``__post_init__``; the aliases themselves are imported
# above from optio_agents).
_VALID_TOOL_VERBOSITY = {"silent", "description-only", "verbose"}
_VALID_THINKING_VERBOSITY = {"hidden", "visible"}

ApprovalPolicy = Literal["untrusted", "on-failure", "on-request", "never"]
_VALID_APPROVAL_POLICIES = {"untrusted", "on-failure", "on-request", "never"}

SandboxMode = Literal["read-only", "workspace-write", "danger-full-access"]
_VALID_SANDBOX_MODES = {"read-only", "workspace-write", "danger-full-access"}

# Graded reasoning effort levels codex accepts on ``turn/start.effort`` (the
# per-turn override; app-server contract). The concrete set a given model
# supports is advertised live in its ``model/list`` entry's
# ``supportedReasoningEfforts`` — this Literal is the validation superset.
ReasoningEffort = Literal["none", "minimal", "low", "medium", "high", "xhigh"]
_VALID_REASONING_EFFORTS = {"none", "minimal", "low", "medium", "high", "xhigh"}


def _identity_resume_refresh(config: "CodexTaskConfig") -> "CodexTaskConfig":
    """Default ``on_resume_refresh``: recompose AGENTS.md from the unchanged
    config on resume, so a resumed session picks up instruction/template
    changes instead of freezing at first launch (no config mutation)."""
    return config


@dataclass
class CodexTaskConfig:
    """Configuration for one optio-codex task instance (Stage 0).

    Stages 0-2 cover iframe/ttyd mode on local and SSH-remote hosts with
    resume/snapshots. Seeds, conversation mode, and filesystem isolation
    arrive in later stages.
    """

    consumer_instructions: str

    agent_type: Literal["codex"] = "codex"

    env: dict[str, str] | None = None
    scrub_env: list[str] | None = None

    # Model id passed to codex at launch (``--model`` in iframe/exec,
    # thread/start in conversation mode). In conversation_ui it ALSO seeds the
    # model picker's initial selection (falling back to the live thread model
    # when unset); codex switches the model INLINE on the next turn/start.
    model: str | None = None
    # Initial graded reasoning effort applied at launch (like ``model``): rides
    # the first ``turn/start`` as ``effort`` and sticks until the operator moves
    # the reasoning_effort slider (INLINE, per-turn; the app-server has no
    # dedicated set-effort request). None (default) → codex uses each model's
    # ``defaultReasoningEffort``. Only meaningful for models that advertise
    # ``supportedReasoningEfforts`` in conversation mode; validated against the
    # ReasoningEffort superset (the live per-model set is the true gate).
    reasoning_effort: "ReasoningEffort | None" = None
    # IFRAME-ONLY. Interactive iframe defaults: unattended launch in ttyd
    # (mirrors claudecode bypassPermissions for embedded sessions nobody is
    # watching). In conversation mode the thread's approvalPolicy is derived
    # from permission_gate (never / on-request), NOT from this field.
    ask_for_approval: ApprovalPolicy = "never"
    # codex-native sandbox mode. None (default) derives from fs_isolation:
    # workspace-write when isolation is on, danger-full-access when off.
    # Explicit values are cross-validated against fs_isolation below.
    sandbox: SandboxMode | None = None
    # Grant network to sandboxed tool commands (codex workspace-write default
    # is network OFF — [sandbox_workspace_write] network_access). False
    # mirrors codex; note this is STRICTER than grok/claudecode, whose fs
    # sandboxes do not restrict the network at all.
    network_access: bool = False

    # --- filesystem isolation (Stage 8) ---------------------------------
    # Confine codex tool subprocesses to the task workdir + /tmp + explicit
    # rw grants, kernel-enforced via codex's NATIVE sandbox (bundled
    # bubblewrap primary, Landlock+seccomp fallback on Linux). Default-ON.
    fs_isolation: bool = True
    # Additional path grants beyond the workdir + temp dirs. ``~/`` expands
    # against the real host home at launch. "ro" grants are a documented
    # no-op on codex (reads are unrestricted in workspace-write). codex's
    # native sandbox has no execute bit, so a ``rox`` grant is treated as
    # ``ro`` and a ``rwx`` grant as ``rw``.
    extra_allowed_dirs: list[AllowedDir] | None = None

    ssh: SSHConfig | None = None

    install_if_missing: bool = True
    install_ttyd_if_missing: bool = True
    install_dir: str | None = None
    ttyd_install_dir: str | None = None

    # When True, a fresh launch kicks off the first turn itself — iframe mode
    # types a trailing positional prompt, conversation mode sends the
    # AUTO_START_PROMPT ("Read AGENTS.md and execute the task it describes").
    # This is for UNATTENDED task execution; a task must opt in. Defaults to
    # False (parity with claudecode/grok/opencode): a conversation/chat task
    # must NOT auto-fire a kickoff, or codex starts an agentic loop on launch
    # and blocks the operator's first real prompt (queued behind it).
    auto_start: bool = False

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
    # Consumed (default/fallback): merge this seed's environment (codex
    # auth.json + config.toml) into a fresh workdir before launch, beginning
    # a NEW session already logged-in; the workdir is pre-trusted right
    # after the merge. A plain string is used as-is; a SeedProvider callable
    # is awaited at launch to resolve one (lease path — holder is the
    # process_id). Ignored on resume.
    seed_id: "str | SeedProvider | None" = None
    # Capture intent: a (sync or async) callback fired on teardown of a
    # fresh session after a successful capture, with two args:
    # (seed_id, info). ``info`` is a human-readable account summary (None
    # for now; resolved in a later stage). Its presence is what enables
    # seed capture. Ignored on resume.
    on_seed_saved: "Callable[[str, str | None], Awaitable[None] | None] | None" = None

    mode: ConversationMode = "iframe"
    host_protocol: bool = True

    # --- conversation surface (Stage 6) ---------------------------------
    # Conversation mode only: route codex's item/*/requestApproval server
    # requests to the published conversation's on_permission_request handler
    # (the caller registers one). When False, the thread is started with
    # approvalPolicy="never" so tools run without prompting.
    permission_gate: bool = False
    # Opt-in dashboard conversation UI: the task starts a per-task listener
    # and publishes a live chat widget. Conversation mode only.
    conversation_ui: bool = False
    # How much tool-call detail the conversation widget renders; only
    # affects conversation_ui rendering.
    tool_verbosity: ToolVerbosity = "description-only"
    # Whether the agent's reasoning trace is shown in the conversation
    # widget; only affects conversation_ui rendering. Default hidden.
    thinking_verbosity: ThinkingVerbosity = "hidden"

    # --- conversation frontend parity (Stage 7) -------------------------
    # Show the session controls (the model picker is the id="model" control).
    # Codex switches the model INLINE: the chosen model rides the next
    # turn/start and sticks — no process restart.
    show_session_controls: bool = False
    # Replace the generic working-spinner with codex's on-brand native
    # spinner in the conversation widget. Requires mode="conversation" and
    # conversation_ui=True.
    native_spinner: bool = False
    # Show the file-upload control. Uploaded bytes land under
    # <workdir>/uploads and are referenced to codex via a System: path line.
    show_file_upload: bool = False
    # Per-task upload hook, fired (additively to the System: path line) after an
    # uploaded file lands under <workdir>/uploads. Mirrors on_deliverable minus
    # the text arg: ``async on_upload(hook_ctx, "uploads/<name>")``. Optional;
    # the workdir write + System: reference happen whether or not it is set.
    on_upload: UploadCallback | None = None
    # Upper bound (bytes) on a single uploaded file; the listener rejects
    # larger with HTTP 413. Mirrored to the widget via widgetData.
    max_upload_bytes: int = 10_000_000
    # Offer download links for files codex marks with the optio-file:
    # sentinel. The listener serves GET /download confined to <workdir>.
    file_download: bool = False
    # Upper bound (bytes) on a single downloaded file (HTTP 413 above).
    max_download_bytes: int = 10_000_000

    # Resume/snapshots (Stage 2). When True (default) the session captures a
    # workdir snapshot — plus the codex sessionId — at teardown, and a later
    # run with ctx.resume=True restores it and relaunches via
    # `codex resume <id>`.
    supports_resume: bool = True
    # Snapshot exclude list. None (default) resolves to
    # optio_codex.snapshots.CODEX_WORKDIR_EXCLUDE_DEFAULT (framework defaults
    # + CODEX_HOME junk: packages/, *.sqlite*, cache/, tmp/, …). MUST NOT be
    # set to exclude home/.codex/sessions — that is the resume source.
    workdir_exclude: list[str] | None = None
    # Optional pair of synchronous bytes->bytes transforms wrapping the resume
    # workdir tar (which carries home/.codex — sessions, auth, config) at
    # GridFS write/read. Both set → encrypted at rest; both None (default) →
    # plaintext. Setting only one is a config error (asymmetric usage is always
    # a mistake, cross-checked in __post_init__).
    session_blob_encrypt: Callable[[bytes], bytes] | None = None
    session_blob_decrypt: Callable[[bytes], bytes] | None = None
    # Hook fired on resume only (never on fresh start). Receives the original
    # config; returns a (possibly mutated) config. The harness re-renders
    # AGENTS.md from the returned config and writes it back only when it differs
    # from the file restored in the snapshot, tagging the next resume.log line
    # with `REFRESHED:AGENTS.md` so codex re-reads. Default = identity
    # (recompose from the same config, so instruction/template changes reach a
    # resumed session instead of freezing at first launch). Pass None to
    # disable.
    on_resume_refresh: "Callable[[CodexTaskConfig], CodexTaskConfig] | None" = _identity_resume_refresh

    @property
    def effective_sandbox_mode(self) -> SandboxMode:
        if self.sandbox is not None:
            return self.sandbox
        return "workspace-write" if self.fs_isolation else "danger-full-access"

    def __post_init__(self) -> None:
        if self.mode not in _VALID_MODES:
            raise ValueError(
                f"CodexTaskConfig.mode={self.mode!r} is not one of "
                f"{sorted(_VALID_MODES)}"
            )
        if self.mode == "iframe" and not self.host_protocol:
            raise ValueError(
                "CodexTaskConfig: host_protocol=False requires "
                "mode='conversation' (in iframe mode the optio.log keyword "
                "channel is the only completion signal)."
            )
        if self.permission_gate and self.mode != "conversation":
            raise ValueError(
                "CodexTaskConfig: permission_gate=True requires "
                "mode='conversation'."
            )
        if self.conversation_ui and self.mode != "conversation":
            raise ValueError(
                "CodexTaskConfig: conversation_ui=True requires "
                "mode='conversation'."
            )
        if self.tool_verbosity not in _VALID_TOOL_VERBOSITY:
            raise ValueError(
                f"CodexTaskConfig.tool_verbosity={self.tool_verbosity!r} "
                f"is not one of {sorted(_VALID_TOOL_VERBOSITY)}"
            )
        if self.thinking_verbosity not in _VALID_THINKING_VERBOSITY:
            raise ValueError(
                f"CodexTaskConfig.thinking_verbosity={self.thinking_verbosity!r} "
                f"is not one of {sorted(_VALID_THINKING_VERBOSITY)}"
            )
        # Frontend-parity features are opt-in flags that only make sense
        # with the conversation UI wired (mirrors claudecode/grok).
        conv_ui = self.mode == "conversation" and self.conversation_ui
        if self.show_session_controls and not conv_ui:
            raise ValueError(
                "CodexTaskConfig: show_session_controls=True requires "
                "mode='conversation' and conversation_ui=True."
            )
        if self.native_spinner and not conv_ui:
            raise ValueError(
                "CodexTaskConfig: native_spinner=True requires "
                "mode='conversation' and conversation_ui=True."
            )
        if self.show_file_upload and not conv_ui:
            raise ValueError(
                "CodexTaskConfig: show_file_upload=True requires "
                "mode='conversation' and conversation_ui=True."
            )
        if self.file_download and not conv_ui:
            raise ValueError(
                "CodexTaskConfig: file_download=True requires "
                "mode='conversation' and conversation_ui=True."
            )
        if self.ask_for_approval not in _VALID_APPROVAL_POLICIES:
            raise ValueError(
                f"CodexTaskConfig.ask_for_approval={self.ask_for_approval!r} "
                f"is not one of {sorted(_VALID_APPROVAL_POLICIES)}"
            )
        if self.sandbox is not None and self.sandbox not in _VALID_SANDBOX_MODES:
            raise ValueError(
                f"CodexTaskConfig.sandbox={self.sandbox!r} "
                f"is not one of {sorted(_VALID_SANDBOX_MODES)}"
            )
        if (
            self.reasoning_effort is not None
            and self.reasoning_effort not in _VALID_REASONING_EFFORTS
        ):
            raise ValueError(
                f"CodexTaskConfig.reasoning_effort={self.reasoning_effort!r} "
                f"is not one of {sorted(_VALID_REASONING_EFFORTS)}"
            )
        if self.fs_isolation and self.effective_sandbox_mode == "danger-full-access":
            raise ValueError(
                "CodexTaskConfig: fs_isolation=True is incompatible with "
                "sandbox='danger-full-access' — fs_isolation exists to "
                "guarantee a kernel-enforced sandbox. Set fs_isolation=False "
                "to run unconfined."
            )
        if not self.fs_isolation and self.sandbox in ("read-only", "workspace-write"):
            raise ValueError(
                "CodexTaskConfig: fs_isolation=False launches codex "
                "unconfined (danger-full-access); an explicit restrictive "
                f"sandbox={self.sandbox!r} contradicts it. Drop one of the "
                "two settings."
            )
        rw_extras = [
            d for d in (self.extra_allowed_dirs or []) if d.mode in ("rw", "rwx")
        ]
        if rw_extras and self.effective_sandbox_mode == "read-only":
            raise ValueError(
                "CodexTaskConfig: extra_allowed_dirs rw grants "
                f"({[d.path for d in rw_extras]}) cannot be honored under "
                "sandbox='read-only' — writable_roots is a workspace-write "
                "feature. ('ro' grants are fine: codex never restricts reads.)"
            )
        if self.network_access and self.effective_sandbox_mode == "read-only":
            raise ValueError(
                "CodexTaskConfig: network_access=True is a "
                "[sandbox_workspace_write] knob and cannot apply under "
                "sandbox='read-only'."
            )
        for field_name in ("install_dir", "ttyd_install_dir"):
            val = getattr(self, field_name)
            if val is not None and not val.startswith("/") and not val.startswith("~"):
                raise ValueError(
                    f"CodexTaskConfig.{field_name}={val!r} must be an "
                    f"absolute path (start with '/' or '~')."
                )
        e = self.session_blob_encrypt is not None
        d = self.session_blob_decrypt is not None
        if e != d:
            raise ValueError(
                "CodexTaskConfig: session_blob_encrypt and session_blob_decrypt "
                "must be set together or both left None; one without the other "
                "is a config error."
            )