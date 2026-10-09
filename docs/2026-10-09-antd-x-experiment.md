# Ant Design X experiment, session controls and vultus one-of: handoff log

Status as of 2026-10-09 ~17:30 CEST, written by "antd port 3" (aoe:ebb2453dfe4c), the current
holder (since 12:38). Earlier holders, advisory now: "antd port 2" (aoe:fa9e60c76e02, until
12:36) and "conversation-ui tweaks" (aoe:c1d32028376a, the first X work).

**Where things stand:** the detours are closed. The session controls (permission modes, model
list from the CLI, picks across a resume, compact bar, sorted model list with inline
descriptions, dev-server knobs) are on **optio main 193c035e** (pushed 17:15; "optio perf
work" runs the optio release batch). vultus **core 0.3.0 / antd 0.5.0** are on npm (unitas main
41cb663). `csillag/antd-x` is rebased onto that main. Left: the permission card review (step 4
below), then the remaining X candidates.

## 00. Goal hierarchy: why we are where we are (read this first)

The **main goal** is the Ant Design X experiment (§1–2): replace optio-conversation-ui's
widgets with X ones, one at a time, compared live against the vanilla UI. The detours it
spawned (a Claude Code permission-mode selector, the vultus one-of field, the session controls
on vultus) are done and merged; see §4.

**Unwind order** (what remains is in bold):
1. ~~OneOfSlider~~ (unitas eb66a0e).
2. ~~Compact controls bar; Claude's model list reviewed and committed~~ (resume decision:
   all three picks kept, a vanished model matches its family).
3. ~~Demo rebuilt on the session controls~~ (two worktrees now, §3).
4. **Back to the reason for detour 1: the owner reviews the permission card** in the gated
   "Claude Code conversation — Config #1" task (process 6ac7ee8304fb01ee4e6f26a1; the
   "conversation+task" task has no gate and runs bypass, so it can show no card). The card is
   the uncommitted change in the antd-x worktree; commit it on antd-x if approved.
5. ~~Push session controls to optio main; vultus release~~ (17:15, 17:07).
6. **Resume the main goal: the remaining X candidates (§2).**

## 0. Work streams at a glance

| Stream | Where | State |
|---|---|---|
| X experiment | optio branch `csillag/antd-x`, worktree `.worktrees/csillag/antd-x`, https://excavator:5180; also runs the demo engine and API | main 193c035e + X deps (4d2c895f) + experiment + docs; permission card uncommitted |
| Non-X work (optio main-bound) | optio branch `csillag/session-controls`, worktree `.worktrees/csillag/session-controls` (own `.venv`, owns the unitas worktree's node_modules links), https://excavator:5181 | equal to origin/main 193c035e; collect the next non-X changes here |
| vultus | unitas main 41cb663, worktree `.worktrees/csillag/unitas` detached at it | released: core 0.3.0, antd 0.5.0 |

All paths are on host `excavator` (`ssh -F /home/csillag/share/fleet-ssh/config excavator`),
repo `~/deai/optio`, worktrees under `~/deai/optio/.worktrees/csillag/`.

## 1. Goal and owner rulings (X experiment)

Keep optio's transport, reducers and `ChatItem` model; swap UI widgets for Ant Design X ones,
one at a time, compared live against the vanilla UI. X's markdown engine replaced ours.
Rulings: no hand-painted CSS except the owner-approved glass look (set once via XProvider);
commit a UI change only after the owner reviewed it live; tests pinning old DOM are rewritten
only for widgets we keep.

## 2. X experiment status (branch `csillag/antd-x`, on optio main 193c035e)

Committed (newest first): `tool rows as X ThoughtChain, details as CodeHighlighter`
(CodeBlock.tsx wraps CodeHighlighter with Prism oneDark in dark mode; adds react-syntax-highlighter
dep), `glass bubbles, theme-tinted; System rows as Bubble.System` (homepage glass copied from
ant-design/x .dumi CustomizationProvider; user bubbles tinted with colorPrimary, System rows with
preset purple, via CSS vars on the view root; stylesheet rewritten at module load so HMR updates),
`X default bubbles on colorBgContainer`, the doc, `drop our mermaid and math libraries`,
`answers rendered by XMarkdown`, `message bubbles as X Bubble`, and first
`Ant Design X experiment dependencies` (@ant-design/x, x-markdown; lockfile resolved
lockfile-only). The dev-server knobs and trusted origins went to main.

Uncommitted in the antd-x worktree: the permission card shows tool input as code blocks
(`renderToolInput`; description next to the tool name; old KV table removed), awaiting the
owner's live review (step 4).

Remaining X candidates: Think (thinking rows, compaction summary), Bubble.Divider (conversation
ended), error rows as error-tinted glass, Bubble.List + role map, Sender (+ Attachments),
FileCard for downloads, Prompts for single-choice questions. Tests: 9 view tests fail by design
(they pin the old bubble DOM).

## 3. Methodology

Two worktrees (owner, 2026-10-09): `session-controls` collects everything non-X and serves
:5181; `antd-x` is always rebased onto it (now onto main) and serves :5180. One demo backend
from the antd-x worktree (tmux `antd-x-demo`: windows `demo` (optio_demo under watchfiles, db
`optio-demo`, prefix `optio`), `api` (:3100), `vite-antd-x` (:5180), `vite-main` (:5181, from
the session-controls worktree), `vultus-gallery` (Storybook :6008 from the unitas worktree)).
Login admin@optio.local / optio-dev. Screenshots: superego `~/chat/antd-x-shots`
(playwright-core + /usr/bin/chromium; `shoot.mjs <path> <name>` shoots both ports; probe-*.mjs).
**Hot reload is the point**: the owner watches :5180/:5181 live.

**Start the Vites with the vite binary, never `pnpm ... dev`**: pnpm auto-installs before a
run when the workspace changed, which relinks the unitas worktree's shared package
node_modules into that worktree's store (it did at 14:38 and broke session-controls' tests
with two Reacts). Command (in `packages/optio-dashboard` of the worktree):
`OPTIO_DEV_VULTUS=~/deai/optio/.worktrees/csillag/unitas OPTIO_API_URL=http://localhost:3100
OPTIO_DEV_PORT=5180|5181 OPTIO_DEV_HTTPS=1 OPTIO_DEV_HOST=excavator ./node_modules/.bin/vite`.
OPTIO_DEV_VULTUS aliases vultus-antd to the unitas worktree's source (HMR) and dedupes antd /
react-i18next / i18next. If the unitas links get relinked anyway: remove
`unitas/packages/vultus-{core,antd}/node_modules` and `pnpm install` in session-controls.

Rebase recipe for antd-x: stash the card change (`git stash push -- <ConversationView.tsx>`),
`git rebase --onto <new base> <old base> csillag/antd-x`, pop, `pnpm install --lockfile-only`
(no relink) and commit the lock if it changed. vite.config rarely changes, so the Vites can
stay up; the demo engine restarts itself (watchfiles) and cancels running demo sessions, which
the owner resumes.

## 4. Merged to optio main (193c035e) and released

From `csillag/session-controls`, 18 commits on 7b73cf45 (trailers stripped): the `System:`
kickoff; the `session_controls` allowlist and `SessionControlsConfigMixin` in every agent; all
six Claude Code permission modes with tri-state `allow_bypass_permissions`; select controls as
vultus one-of selects with descriptions, variants, confirmations; control descriptions as
questions; Claude's model list from the CLI (`initialize`), the operator's picks (model,
effort, permission mode) kept across an optio resume (`picks.py`; a short initialize-only
probe before the resumed launch, asked with the configured model or `default`, never the saved
one; a vanished model matches its family via `models.restore_model`); the compact controls
bar (BoolSwitch, OneOfSegmented, OneOfSlider; labels hidden when the toolbar would overflow);
the model select sorted (current first, older versions under "Older versions",
`ControlOption.group`) with inline descriptions (`inlineDescriptions`, every agent's model
list); opt-in dev-server knobs (OPTIO_API_URL, OPTIO_DEV_PORT/HTTPS/HOST, OPTIO_DEV_VULTUS,
OPTIO_TRUSTED_ORIGINS); optio-conversation-ui needs vultus-antd >=0.5.0. vultus 0.3.0/0.5.0
(unitas 41cb663): one-of field and widgets, choice groups, inline descriptions, OneOfSlider
(field tooltip on the whole slider), tooltips held back while a confirmation is open.

Open items, not fixed (owner told):
- optio-core race (executor run teardown fails a relaunch's result future); optio perf work
  takes it. optio-claudecode's `_wait_terminal` test helper works around it.
- After an effort-only relaunch the model select can briefly show `default` when a task's
  settings.json names a model (runtime_model reset in `attach()`); needs a turn-timed fake to
  test.
- `ClaudeCodeConversation._private_acks` keeps an id per failed initialize (harmless).

## 5. Owner rulings of 2026-10-09 (beyond the tables above)

Glass bubbles (dark exact copy, light ours with shadows); System rows as Bubble.System, purple;
tool rows as ThoughtChain with CodeHighlighter details; dashboard process list collapsible;
vultus one-of field: per-choice description (+ optional valueDescriptions), Select + Segmented
(+ Slider), variants like actions (disabled never tinted, closed select mirrors, danger
confirmation OK), icons + iconOnly, markdown descriptions, tooltip paragraphs led by an info
sign (description) and a **gray** no-entry sign (reason; red rejected: disabled is no danger),
no native titles; Bypass gets a simple confirmation. Later the same day (to antd port 3):
OneOfSlider approved; its handle must not jump back while a choice is asked or committed; a
widget's tooltips are held back while its confirmation is open, in every confirming widget.
Afternoon: resume keeps all three picks; a saved model the CLI no longer lists matches its
family; a pinned config model stays pinned on resume (the CLI shows it as "Newer version
available"); model select: current models first, older versions under an "Older versions"
heading, by family, newest first; model lists show descriptions inline (bold label, smaller
wrapping description, only a disabled reason in a tooltip, closed select shows the label);
the slider's field tooltip on any part of it; compact mode approved; two worktrees, dev knobs
to main; vultus merged and released, session controls merged to main, antd-x stays separate.

## 6. Conventions learned (keep)

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
- In jsdom a closed antd tooltip stays mounted in its `-leave` motion; assert with
  `openTooltips()` (vultus-antd `src/__tests__/helpers/tooltips.ts`), not with text presence.
- The unitas worktree's packages resolve node_modules through the session-controls worktree's
  pnpm store (it owns those links; see §3 for restoring them); antd 6.6.5 and
  @rc-component/slider 1.1.1 match unitas's own lockfile, so vultus tests there are
  representative.
- In a worktree: optio-demo's tests need `pip install -e packages/optio-demo` in its `.venv`;
  optio-dashboard's `tsc` needs `pnpm --filter "optio-api..." run build` first.
- optio pushes: `git push origin csillag/session-controls:main` (fast-forward) from the
  worktree; ~/deai/optio and the excavator engine belong to optio perf work.
- @rc-component/slider ends a keyboard move on keyup (tests send keydown + keyup).
- Browser probes for vultus stories: superego `~/chat/antd-x-shots/probe-slider*.mjs`,
  `probe-confirm-overlap.mjs` (Storybook iframe URLs, http://excavator:6008).

## 7. Coordination

Excavator stack: "optio perf work" (aoe:c723ee016726); log heavy steps in topics.log; tell it
before pushing optio main. Peers who know things: "conversation-ui tweaks" (aoe:c1d32028376a,
earlier X work), "antd update" (aoe:01d5d646c447, vultus design history), "Eduina review +
resurrect" (aoe:e03e7f973cfd, last vultus release). Side findings logged in topics.log: excavator
PreferenceHydrator writes local defaults before stored prefs (harmless race); three orphaned
demo watcher loops were killed.
