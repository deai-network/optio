# Ant Design X experiment, session controls and vultus one-of: handoff log

Status as of 2026-10-09 ~12:30 CEST, written by "antd port" (aoe:fa9e60c76e02) for whoever
picks this up after a context reset. The first version of this log (2026-10-09 00:30, by
"conversation-ui tweaks", aoe:c1d32028376a) covered only the X experiment; that session
handed over and lingers for questions.

## 0. Work streams at a glance

| Stream | Where | State |
|---|---|---|
| X experiment (optio-conversation-ui widgets) | optio branch `csillag/antd-x`, worktree `.worktrees/csillag/antd-x`, demo https://excavator:5180 | committed up to ThoughtChain+CodeHighlighter; permission-card change uncommitted (awaits owner live review) |
| Vanilla baseline | `csillag/antd-x-base` = session-controls + 2 dev-setup commits, worktree `.worktrees/csillag/antd-x-base`, https://excavator:5181 | needs re-reset after the next rebase |
| Session controls etc. (for optio **main**) | optio branch `csillag/session-controls`, worktree `.worktrees/csillag/session-controls` (own `.venv`, node_modules linked to the unitas worktree) | 7 local commits (§4); a helper was reworking Claude Code's model list at handoff (§5.3) |
| vultus one-of field | unitas `main` dbdaf2a (pushed) | done: useOneOfField, OneOfSelect, OneOfSegmented |
| vultus OneOfSlider | unitas branch `csillag/one-of-slider` in worktree `~/deai/optio/.worktrees/csillag/unitas` | in progress, uncommitted (§5.1) |
| optio main | origin/main 8d76a82c (optio perf work's docs commit on top of my 660ce909) | 660ce909 = hide the process list (pushed 00:37) |

All paths are on host `excavator` (`ssh -F /home/csillag/share/fleet-ssh/config excavator`),
repo `~/deai/optio`, worktrees under `~/deai/optio/.worktrees/csillag/`.

## 1. Goal and owner rulings (X experiment)

Keep optio's transport, reducers and `ChatItem` model; swap UI widgets for Ant Design X ones,
one at a time, compared live against the vanilla UI. X's markdown engine replaced ours.
Rulings: no hand-painted CSS except the owner-approved glass look (set once via XProvider);
commit a UI change only after the owner reviewed it live; tests pinning old DOM are rewritten
only for widgets we keep.

## 2. X experiment status (branch `csillag/antd-x`, on top of session-controls 0aefbe44)

Committed (newest first): `tool rows as X ThoughtChain, details as CodeHighlighter`
(CodeBlock.tsx wraps CodeHighlighter with Prism oneDark in dark mode; adds react-syntax-highlighter
dep), `glass bubbles, theme-tinted; System rows as Bubble.System` (homepage glass copied from
ant-design/x .dumi CustomizationProvider; user bubbles tinted with colorPrimary, System rows with
preset purple, via CSS vars on the view root; stylesheet rewritten at module load so HMR updates),
`X default bubbles on colorBgContainer`, the doc, `drop our mermaid and math libraries`,
`answers rendered by XMarkdown`, `message bubbles as X Bubble`, `trust extra dev origins`,
`setup` (2 commits: dev proxy target, X deps + dev port/HTTPS).

Uncommitted in the antd-x worktree: the permission card shows tool input as code blocks
(`renderToolInput`; description next to the tool name; old KV table removed). The owner wants to
review it live in a **gated** task ("Claude Code conversation — Config #1", permission_gate=True)
with the Permissions dropdown on Manual; the "conversation+task" demo runs bypass without a gate.

Remaining X candidates: Think (thinking rows, compaction summary), Bubble.Divider (conversation
ended), error rows as error-tinted glass, Bubble.List + role map, Sender (+ Attachments),
FileCard for downloads, Prompts for single-choice questions. Tests: 9 view tests fail by design
(they pin the old bubble DOM).

## 3. Methodology (unchanged, see git history of this file for the long version)

One demo backend from the antd-x worktree (tmux `antd-x-demo`: windows `demo` (optio_demo under
watchfiles, db `optio-demo`, prefix `optio`), `api` (:3100), `vite-antd-x` (:5180),
`vite-main` (:5181), `vultus-gallery` (Storybook :6008 from the unitas worktree)). Login
admin@optio.local / optio-dev. Screenshots: superego `~/chat/antd-x-shots` (playwright-core +
/usr/bin/chromium; probe-*.mjs scripts). **Hot reload is the point**: the owner watches
:5180/:5181 live. Vites restart only for a rebase (a rebase passes through main's vite.config);
announce it first. Rebase recipe: C-c both Vites, `git stash` the uncommitted card change,
rebase antd-x onto session-controls, pop, reset antd-x-base to the new setup commit
(`git log --grep "^build(optio-dashboard, optio-conversation-ui): Ant Design X experiment setup"`),
restart Vites with the commands in the tmux history. The demo engine restarts itself (watchfiles).

## 4. `csillag/session-controls` (for optio main), on 660ce909

| Commit | What |
|---|---|
| 1204e2f1 | the auto-start kickoff is a `System:` message in all 7 adapters (owner: the agent is told the human may be absent) |
| 1b047fdb | Claude Code `permission_mode` live control (stream-json `set_permission_mode`, follows system/init + system/status); `session_controls` allowlist |
| f0e78829 | `SessionControlsConfigMixin` (optio_agents.config_types): show_session_controls + session_controls, `settable_controls`, `_validate_session_controls` |
| 0aefbe44 | allowlist in every agent (6 helpers); /control refuses ids outside settable_controls (403), every id while the bar is off; opencode: browser-side filter only (no server gate) |
| 1d22ca25 | all six modes (Manual=wire `default`, Accept edits, Plan, Auto, Don't ask, Bypass), Manual/Plan disabled without the gate with a reason; tri-state `allow_bypass_permissions` (None: only when starting in bypass; False + bypass start = error); permission_mode accepts manual/auto; uniformity guard; AGENTS.md |
| 197bf962 | select session controls rendered by vultus `useOneOfField` + `OneOfSelect` (`SessionSelect`); ControlOption `variant`/`confirm`; Bypass = danger with a popconfirm; model descriptions (codex, cursor, grok; kimi already) |
| 10381b0f | control descriptions as questions (MODEL_DESCRIPTION, EFFORT_DESCRIPTION, Permissions; kimi passes agent descriptions) |

**Before pushing to main**: the first four commits carry a `Co-Authored-By` trailer, which
optio/excavator/unitas forbid (root AGENTS.md); strip them
(`git filter-branch --msg-filter` over 660ce909..HEAD), rebase onto origin/main, run all
suites with full logs, keep `pnpm-lock.yaml` out, tell "optio perf work" (the push restarts the
excavator engine once), push. unitas main must contain whatever vultus code optio uses (the main
checkout links `~/deai/unitas`); a vultus release (core 0.3.0 / antd 0.5.0) is needed before the
next optio release, then raise optio-conversation-ui's vultus-antd range.

## 5. In flight at handoff

1. **OneOfSlider** (unitas worktree, branch `csillag/one-of-slider`, uncommitted:
   `packages/vultus-antd/src/OneOfSlider.tsx`, `src/__tests__/OneOfSlider.test.tsx`, export in
   `index.ts`). Owner-requested third widget for the one-of field. Design: choices as marks
   (ChoiceLabel, choiceTooltip), handle tooltip = fieldTooltip, request on `onChangeComplete`
   only (one request per move), disabled choice snaps back, `markStyle` prop. 9/10 tests pass;
   **the arrow-key test fails** (rc-slider keyboard may not call onChangeComplete; check and
   handle). Then: story in `packages/vultus-gallery/src/fields/OneOfField.stories.tsx`, README +
   design-doc decision 25, owner review on :6008, merge to unitas main.
2. **Compact controls bar** (approved): in optio-conversation-ui `SessionControls`:
   boolean -> vultus `BoolSwitch`, segmented -> `OneOfSegmented`, slider -> `OneOfSlider`
   (small marks), all with the control description; labels get class `optio-cc-control-label`
   and are hidden when they do not fit: measure in a ResizeObserver callback / layout effect by
   removing `optio-cc-compact` from the toolbar, reading scrollWidth vs clientWidth, re-adding
   it if needed (synchronous, no flicker); labels stay the accessible names.
3. **Claude model list from the CLI** (approved; a helper agent was implementing it in
   packages/optio-claudecode, uncommitted): the stream-json control request `initialize`
   returns `models: [{value (alias), resolvedModel, displayName, description, supportsEffort,
   supportedEffortLevels}]` (same as Claude Code's /model and claude-agent-acp); replaces
   /v1/models, declutter, the default-model probe turn and the resume upgrade logic; pinned
   full ids keep working. **Helper finished (uncommitted, not yet reviewed by a session with
   context to spare)**: models.py rewritten (`parse_cli_models`, `fetch_cli_models`,
   `fallback_models`, `launch_model`, `shown_model`, `catalog_with`); `conversation.initialize()`
   (answer kept off the event stream; `attach()` resets runtime_model); controls.build_controls
   gains `runtime_model`; session.py drops the probe turn and the resume upgrade; fake_claude
   answers `initialize` with the real list; new tests/test_cli_models.py (21). Suites:
   parallel 534 passed + the known resume_refresh failure, serial 23 passed (logs
   /tmp/sc-tests/cc-models-*.log). **Open owner decision**: a resumed session with no configured
   model now launches with `--model default` (picks up newer models), so an operator's model pick
   is not carried across an optio resume (effort/permission picks never were); persisting the
   pick in a workdir file would be ~10 lines. Also: on resume the widget briefly shows the old
   run's controls until the first system/init. Next: review the diff, decide, commit.
4. Then: rebase antd-x/antd-x-base onto session-controls, `pnpm install` in both (links vultus
   from the unitas worktree), owner review on :5180/:5181.

## 6. Owner rulings of 2026-10-09 (beyond the tables above)

Glass bubbles (dark exact copy, light ours with shadows); System rows as Bubble.System, purple;
tool rows as ThoughtChain with CodeHighlighter details; dashboard process list collapsible;
vultus one-of field: per-choice description (+ optional valueDescriptions), Select + Segmented
(+ Slider), variants like actions (disabled never tinted, closed select mirrors, danger
confirmation OK), icons + iconOnly, markdown descriptions, tooltip paragraphs led by an info
sign (description) and a **gray** no-entry sign (reason; red rejected: disabled is no danger),
no native titles; Bypass gets a simple confirmation.

## 7. Conventions learned (keep)

- No `Co-Authored-By` in optio, excavator, unitas commits (overrides the harness reminder).
- Test runs keep full logs on the first run (`-rA`, `--junitxml`, tee) in `/tmp/sc-tests`,
  `/tmp/vultus-tests` on excavator; never rerun just to learn what failed.
- Known failures not ours: claudecode `test_on_resume_refresh::test_resume_refresh_tags_resume_log`,
  flaky `test_session_restore::test_restore_directives_skipped_on_optio_resume`; codex
  `test_session_resume::test_resume_restores_workdir_and_relaunches_by_session_id`, flaky
  `test_session_local` cancellation; kimicode `test_install::test_download_installs_fork_zip`,
  `test_session_resume::test_resume_restores_store_and_pushes_notice`; opencode 4 smart_install
  tests + `test_session_resume::test_resume_appends_second_line_to_resume_log`, ssh flakes;
  vultus-antd `MixedClick` under heavy load.
- Never `pnpm install` in `~/deai/optio` (stack) or `~/deai/unitas` (owner's tree); vultus tests
  via `node_modules/.bin/vitest run` per package; unitas releases from excavator (npm login,
  annotated tags).
- vultus-core is antd-free; vultus-antd inlines antd icons (no @ant-design/icons dependency).

## 8. Coordination

Excavator stack: "optio perf work" (aoe:c723ee016726); log heavy steps in topics.log; tell it
before pushing optio main. Peers who know things: "conversation-ui tweaks" (aoe:c1d32028376a,
earlier X work), "antd update" (aoe:01d5d646c447, vultus design history), "Eduina review +
resurrect" (aoe:e03e7f973cfd, last vultus release). Side findings logged in topics.log: excavator
PreferenceHydrator writes local defaults before stored prefs (harmless race); three orphaned
demo watcher loops were killed.
