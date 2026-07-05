"""Public data types for optio-codex consumers."""

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


# "iframe" = ttyd TUI in the browser. "conversation" = a headless
# ``codex app-server`` session; the task publishes a live CodexConversation
# via ctx.publish_result (Stage 6).
ConversationMode = Literal["iframe", "conversation"]
_VALID_MODES = {"iframe", "conversation"}

# Verbosity of tool-call rendering in the conversation widget
# (conversation_ui only). Mirrors optio-claudecode/grok; consumed by the
# dashboard reducer.
ToolVerbosity = Literal["silent", "description-only", "verbose"]
_VALID_TOOL_VERBOSITY = {"silent", "description-only", "verbose"}

# Visibility of the agent's reasoning trace in the conversation widget
# (conversation_ui only). Mirrors optio-grok; consumed by the dashboard
# reducer/ConversationView (thinkingVerbosity gate).
ThinkingVerbosity = Literal["hidden", "visible"]
_VALID_THINKING_VERBOSITY = {"hidden", "visible"}

ApprovalPolicy = Literal["untrusted", "on-failure", "on-request", "never"]
_VALID_APPROVAL_POLICIES = {"untrusted", "on-failure", "on-request", "never"}

SandboxMode = Literal["read-only", "workspace-write", "danger-full-access"]
_VALID_SANDBOX_MODES = {"read-only", "workspace-write", "danger-full-access"}


# A seed provider resolves a usable seed_id at launch time (e.g. leasing one
# from a pool). Mirrors optio-grok's SeedProvider; the callable/lease path
# is exercised by the Stage-4 wiring — a static string seed_id carries no
# lease.
SeedProvider = Callable[[str], Awaitable[str]]


class SeedUnavailableError(Exception):
    """Raised by a seed provider when no usable seed is available; the
    message is surfaced as the process failure."""


@dataclass
class AllowedDir:
    """A caller-supplied extra path grant for filesystem isolation (Stage 8).

    ``mode`` is ``"ro"`` or ``"rw"``. Grants are ADDITIVE: they may widen the
    sandbox allowlist, never mask the baseline (the workdir/cwd and ``/tmp``
    are always writable in workspace-write).

    codex divergence (vs grok/claudecode, whose sandboxes also deny reads):
    codex ``workspace-write`` restricts WRITES only — the read side is open,
    so ``mode="ro"`` is trivially satisfied and changes nothing (documented
    no-op, kept for cross-wrapper config portability). Only ``mode="rw"``
    grants alter behavior, via ``sandbox_workspace_write.writable_roots``.
    A leading ``~/`` expands against the REAL host home at launch time (the
    codex process runs under an isolated ``$HOME``).
    """

    path: str
    mode: Literal["ro", "rw"]

    def __post_init__(self) -> None:
        if self.mode not in ("ro", "rw"):
            raise ValueError(
                f"AllowedDir.mode={self.mode!r} must be one of 'ro', 'rw' "
                f"(path={self.path!r})."
            )


@dataclass
class CodexTaskConfig:
    """Configuration for one optio-codex task instance (Stage 0).

    Stages 0-2 cover iframe/ttyd mode on local and SSH-remote hosts with
    resume/snapshots. Seeds, conversation mode, and filesystem isolation
    arrive in later stages.
    """

    consumer_instructions: str

    env: dict[str, str] | None = None
    scrub_env: list[str] | None = None

    model: str | None = None
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
    # no-op on codex (reads are unrestricted in workspace-write).
    extra_allowed_dirs: list[AllowedDir] | None = None

    ssh: SSHConfig | None = None

    install_if_missing: bool = True
    install_ttyd_if_missing: bool = True
    codex_install_dir: str | None = None
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
    # Model preselected in the widget's model picker. Requires
    # mode="conversation" and conversation_ui=True. Defaults to the live
    # thread model when unset. (config.model still drives thread/start;
    # this only controls the picker's initial value.)
    default_model: str | None = None
    # Show the session controls (the model picker is the id="model" control).
    # Codex switches the model INLINE: the chosen model rides the next
    # turn/start and sticks — no process restart.
    show_session_controls: bool = False
    # Show the file-upload control. Uploaded bytes land under
    # <workdir>/uploads and are referenced to codex via a System: path line.
    show_file_upload: bool = False
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
        if self.default_model is not None and not conv_ui:
            raise ValueError(
                "CodexTaskConfig: default_model requires mode='conversation' "
                "and conversation_ui=True."
            )
        if self.show_session_controls and not conv_ui:
            raise ValueError(
                "CodexTaskConfig: show_session_controls=True requires "
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
        rw_extras = [d for d in (self.extra_allowed_dirs or []) if d.mode == "rw"]
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
        for field_name in ("codex_install_dir", "ttyd_install_dir"):
            val = getattr(self, field_name)
            if val is not None and not val.startswith("/") and not val.startswith("~"):
                raise ValueError(
                    f"CodexTaskConfig.{field_name}={val!r} must be an "
                    f"absolute path (start with '/' or '~')."
                )