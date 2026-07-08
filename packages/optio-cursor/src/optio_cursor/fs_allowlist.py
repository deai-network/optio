"""Build the claustrum filesystem-allowlist flags for a cursor-agent launch.

STAGE-8 MECHANISM DECISION (Task 0 probe, 2026-07-02) — CLAUSTRUM, not native.
================================================================================
Host was NOT logged into cursor, so the live enforcement half was skipped; the
decision rests on binary/schema analysis of the cursor-agent dist bundle
(cursorsandbox --help, ``strings cursorsandbox``, and the Node ``index.js``
bundle).

Cursor DOES ship a native sandbox, and it clears two of the three bars:
  * (a) Allowlist-configurable — YES. ``cursorsandbox --policy <json>`` takes a
        unified policy file: PolicyFile{version, logFormat, policy}; ``policy``
        is an internally-tagged SandboxPolicy enum
        (workspace_readwrite | workspace_readonly | insecure_none) with
        ``cwd``, ``additionalReadwritePaths``, ``additionalReadonlyPaths``,
        ``networkAccess``, ``disableTmpWrite``, ``blockGitWrites``,
        ``ignoreMapping``, plus ``networkPolicy`` (allow/deny/default) and
        ``networkPolicyStrict``.
  * (c) Fail-closed — YES, for shell-exec. In index.js, te() gates the
        sandboxed spawn: for any non-``insecure_none`` policy it calls
        isSandboxHelperSupported() (which runs
        ``cursorsandbox --policy … --preflight-only -- /bin/true``; exit 0 =
        supported, exit 2 = unsupported kernel), and THROWS "Sandbox policy '…'
        is not supported on this system" when unsupported. It never falls back
        to an unconfined run.

But it FAILS the decisive architectural bar: WHOLE-PROCESS confinement.
  * cursorsandbox is self-described as "Sandboxing helper for Everysphere
    shell-exec". It wraps each SHELL command the agent runs (oe() -> te() -> the
    ``cursorsandbox --policy … -- <cmd>`` spawn, env CURSOR_SANDBOX=native).
  * The Node AGENT process is never Landlock-confined: there is no restrict_self
    / prctl / seccomp / landlock in the agent bundle (index.js) — those symbols
    exist ONLY inside the standalone ``cursorsandbox`` binary, applied to the
    wrapped command. So the agent's OWN in-process file tools (Write/Edit, via
    the native ``file_service.*.node`` module + Node fs) write UNCONFINED and
    escape the path allowlist entirely.

That is the material difference from optio-grok, whose native sandbox qualified
precisely because grok Landlock-confines its ENTIRE process at startup (its own
writes included). Cursor has no equivalent whole-process self-sandbox.

The Stage-8 goal is to confine the cursor agent AND every tool/subprocess,
kernel-enforced, fail-closed. A shell-exec-only sandbox leaves the agent's
primary file-mutation capability outside kernel enforcement, so native is
insufficient. We therefore port claudecode's claustrum: wrap the WHOLE
cursor-agent launch argv in the claustrum Landlock CLI (confines the Node agent
process and all descendants), fail-closed, and launch cursor-agent with
``--sandbox disabled`` so its own per-command helper does not nest under the
outer Landlock ruleset (wiring is Stage-8 Task 2).

--------------------------------------------------------------------------------
The grant-flag machinery (system BASELINE + workdir/cache + caller extras) now
lives in the shared ``optio_agents.fs_grants.build_grant_flags`` (single source
of truth across every wrapper); the claustrum argv prefix is
``optio_agents.claustrum.build_claustrum_wrap``. session.py's
``_build_claustrum_wrap`` consumes both. This module retains only the mechanism
DECISION rationale above, which the code references.
"""

from __future__ import annotations
