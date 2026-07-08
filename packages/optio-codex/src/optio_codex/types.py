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


@dataclass(frozen=True, kw_only=True)
class CodexTaskConfig(ClaustrumConfigMixin):
    """Configuration for one optio-codex task instance.

    Inherits the claustrum filesystem-isolation triad (``fs_isolation`` /
    ``extra_allowed_dirs`` / ``delivery_type``) from ``ClaustrumConfigMixin``;
    those fields stay top-level here (callers write ``fs_isolation=`` /
    ``delivery_type=`` verbatim). Frozen because the mixin is frozen;
    ``kw_only`` because the mixin contributes defaulted fields ahead of the
    required ``consumer_instructions``.
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
    # codex-native sandbox mode. Claustrum (see session.py), NOT this native
    # mode, owns filesystem isolation. codex's native sandbox is BUBBLEWRAP,
    # which cannot nest inside claustrum, so with fs_isolation=True (default) the
    # native mode MUST be danger-full-access (no bwrap) — None (default) resolves
    # to danger-full-access, and an explicit workspace-write/read-only +
    # fs_isolation is rejected. Set fs_isolation=False to run codex's native
    # sandbox standalone (then None → workspace-write is NOT auto-picked; pass an
    # explicit sandbox=). Decoupled from ``fs_isolation``.
    sandbox: SandboxMode | None = None
    # Grant network to sandboxed tool commands. This is a native-bubblewrap
    # ([sandbox_workspace_write]) knob and therefore applies ONLY when codex runs
    # its native workspace-write sandbox standalone (fs_isolation=False,
    # sandbox='workspace-write'). Under claustrum (fs_isolation=True) codex has NO
    # network confinement — bwrap can't nest — so this field is a NO-OP there and
    # session.py warns at launch. The pending shared pasta/netns layer will
    # restore network isolation universally.
    network_access: bool = False

    # --- filesystem isolation ------------------------------------------------
    # fs_isolation / extra_allowed_dirs / delivery_type are INHERITED from
    # ClaustrumConfigMixin. Claustrum (Landlock, fail-closed) confines codex
    # and its whole tool-subprocess tree to the task workdir + explicit grants;
    # ``extra_allowed_dirs`` rw grants ALSO feed codex's native ``writable_roots``
    # so the redundant native layer never blocks a write claustrum permits.
    # ``~/`` grants expand against the real host home at launch. delivery_type
    # is MANDATORY when fs_isolation is on (routes the "newer claustrum
    # available" security notice via on_deliverable).

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
        # Claustrum (Landlock, see session.py) is the sole fs-isolation layer.
        # codex's NATIVE sandbox (bubblewrap) CANNOT nest inside claustrum — its
        # user+mount-namespace setup fails ("setting up uid map / make / slave:
        # Permission denied") because Landlock denies the /proc write and the
        # mount-propagation op. So under claustrum the native sandbox is DISABLED
        # (danger-full-access = no bwrap) and claustrum alone confines the fs.
        # Consequence: network confinement (a bubblewrap-only feature) is
        # unavailable here — session.py warns at launch; the pending shared
        # pasta/netns layer will restore it. Explicit ``sandbox=`` is honored
        # (power-user escape hatch), but workspace-write/read-only + fs_isolation
        # is rejected in __post_init__ (it would try to nest bwrap and fail).
        if self.sandbox is not None:
            return self.sandbox
        return "danger-full-access"

    def __post_init__(self) -> None:
        self._validate_claustrum()
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
        # fs_isolation drives CLAUSTRUM (see session.py). codex's native
        # bubblewrap sandbox cannot nest inside claustrum's Landlock domain, so
        # an explicit restrictive native mode + fs_isolation is a hard error:
        # the launch would try to start bwrap under claustrum and fail-closed.
        if self.fs_isolation and self.sandbox in ("workspace-write", "read-only"):
            raise ValueError(
                f"CodexTaskConfig: sandbox={self.sandbox!r} (codex's native "
                "bubblewrap sandbox) cannot run inside claustrum — bwrap's "
                "user/mount-namespace setup fails under Landlock. With "
                "fs_isolation=True, claustrum is the fs sandbox and the native "
                "mode must be danger-full-access (the default). Set "
                "fs_isolation=False to use codex's native sandbox standalone."
            )
        # Native-mode-internal couplings (apply only with fs_isolation=False +
        # an explicit native mode): rw grants + network both need workspace-write,
        # since read-only carries neither.
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