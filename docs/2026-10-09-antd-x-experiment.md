# Ant Design X in optio-conversation-ui: experiment log

Status as of 2026-10-09 00:30 CEST. Branch `csillag/antd-x` (local, not pushed).
Written so the work can be picked up cold, by the owner or by an agent after a
context reset.

## 1. What we are doing, and why

optio-conversation-ui renders agent conversations. Underneath sits a rich, hard-won
framework of its own: per-engine transports (the listener's SSE stream, with replay),
an engine-neutral `ChatItem` model, and per-agent reducers (claudecode, grok, cursor,
kimicode, opencode, codex, antigravity). These handle live/replay parity, steering
queues, session end and resume, compaction, timestamps and more. On top sits a
hand-built antd UI (`ConversationView.tsx`, about 2000 lines) that gets the job done
but is not fancy, and that we have to maintain ourselves.

[Ant Design X](https://x.ant.design/) is Ant Design's component library for AI chat
interfaces. The direction (owner, 2026-10-08): **keep our framework, use their
widgets.** Replace UI-level widgets with Ant Design X ones, one at a time, and compare
the result side by side with the current UI on the same live data.

Owner rulings during the experiment:

- Keep the transport, reducers and `ChatItem` model; only the rendering changes.
- **Do not adjust X's CSS.** The point is to stop maintaining styling ourselves. Learn
  X's own system first: someone building with it should not need manual CSS painting.
- Swap one widget at a time; tests that pin the old DOM are rewritten only for widgets
  we decide to keep.
- X's markdown engine is in (replacing ours); our own mermaid and math libraries go.

## 2. Research: what Ant Design X is

Packages (npm, all 2.9.0, published 2026-07-28):

| Package | What it is | Fit for us |
|---|---|---|
| `@ant-design/x` | Chat UI components on antd. Peer deps: `antd ^6.1.1`, React >= 18 (we have antd 6, React 19). Depends on `mermaid` ^11.12 and `react-syntax-highlighter`. | Good: maps onto our item kinds |
| `@ant-design/x-markdown` | Streaming-safe markdown renderer (marked + DOMPurify + html-react-parser, `katex` for its Latex plugin) | Good: now in use |
| `@ant-design/x-sdk` | Data layer: `useXChat`, `XRequest`, chat providers | Poor: assumes the browser drives each request/reply; we observe a server-side agent's event stream (replay, many item kinds, turns the browser did not start). Not adopted. |
| `@ant-design/x-skill` | Coding-agent skills for building with X | Optional tooling |
| `@ant-design/x-card` | Agent-generated UI from A2UI JSON streams | Possible future direction |

Component mapping (ours -> theirs):

| Ours | Ant Design X |
|---|---|
| user / answer bubbles | `Bubble`, `Bubble.List` (roles `ai`, `user`, `system`, `divider`, custom roles) |
| System rows | `Bubble.System` |
| resume / compaction bands | `Bubble.Divider` or a custom role |
| tool rows (running/done/failed/stopped) | `ThoughtChain` (status loading/success/error/abort) |
| thinking rows | `Think` |
| upload / download | `Attachments` / `FileCard` |
| input bar | `Sender` (no queue/interrupt semantics: our steer button goes in its actions) |
| per-answer controls | `Actions` (`Actions.Copy`, feedback, ...) |
| markdown + mermaid + math | `XMarkdown` + `Mermaid` + `CodeHighlighter` + Latex plugin |

No X counterpart (stay ours): steering queue (queued bubbles, Send now, Interrupt and
send), permission and question cards, session controls, the task sidebar, tool
verbosity modes, start-end time labels.

### X's styling system (what replaces "painting CSS")

- **There is no chat-background component.** `Bubble.List` is transparent (padding,
  15% reserved on the far side, scrollbar). The app layout provides the background:
  every official template (`independent`, `copilot`, `ultramodern`, `agent-tbox` in
  `packages/x/docs/playground/` of github.com/ant-design/x) paints its chat area
  `token.colorBgContainer` (white in light mode); only side panels use
  `colorBgLayout` (at 50% alpha).
- Bubble's look comes from **global antd tokens**: `filled` = `colorFillContent`,
  `outlined` = 1px `colorBorderSecondary` (no fill), `shadow` = `boxShadowTertiary`
  (no fill), `borderless` = nothing. Bubble's own component tokens only cover the typing
  cursor (`typingContent`, `typingAnimationName`, `typingAnimationDuration`).
- **XProvider** = antd ConfigProvider + per-component defaults (`style`, `styles`,
  `className`, `classNames` for bubble, sender, thoughtChain, ...) + X component
  tokens in `theme.components`. Templates wrap the whole app in it.
- Every component takes semantic `styles` / `classNames` per part (`root`, `content`,
  `header`, `footer`, `avatar`, `extra`).
- XMarkdown ships `themes/light.css` and `themes/dark.css`; X's demos pick
  `x-markdown-light` / `x-markdown-dark` from `theme.useToken().theme.id`.
- Template composition of the assistant role (`Bubble.List` `role` map): `placement`
  start, `header` = a `ThoughtChain.Item` showing status, `footer` = actions (retry,
  copy, like/dislike), `contentRender` = `XMarkdown` with
  `streaming={{ hasNextChunk, enableAnimation }}` and custom tags (`<think>`). User role:
  `placement: 'end'`. **Neither sets a variant or shape** (both default `filled`).

XMarkdown API in brief: `content`, `components` (tag -> component; `code` receives
`lang` and `block`), `config` (marked extensions, e.g. `Latex()` from
`@ant-design/x-markdown/plugins/Latex`), `dompurifyConfig`, `streaming`
(`hasNextChunk`, `enableAnimation`, `tail`), `openLinksInNewTab`, `paragraphTag`.

## 3. Methodology: one backend, two frontends, screenshots

All on the excavator host (LAN `excavator`), using optio's own demo app (optio-demo)
and the optio-dashboard.

- **One backend** (run from the experiment worktree; its server code is identical to
  main, since the branch only changes client code and dev config):
  - optio-demo engine: database `optio-demo`, prefix `optio`, on the excavator stack's
    MongoDB (`localhost:27017`, a single-member replica set) and Redis. Never the
    stack's own `excavator`/`gm`.
  - optio-dashboard API on port 3100, pinned:
    `OPTIO_PREFIX=optio MONGODB_URL=mongodb://localhost:27017/optio-demo
    OPTIO_TRUSTED_ORIGINS=https://excavator:5180,https://excavator:5181 PORT=3100
    REDIS_URL=redis://localhost:6379 make run-api` (in `packages/optio-dashboard`).
- **Two Vite frontends**, both proxying `/api` to 3100:
  - **https://excavator:5180/**: experiment (worktree `antd-x`).
  - **https://excavator:5181/**: baseline, the main UI (worktree `antd-x-base`).
  - Started with `OPTIO_API_URL=http://localhost:3100 OPTIO_DEV_PORT=<port>
    OPTIO_DEV_HTTPS=1 OPTIO_DEV_HOST=excavator pnpm --dir <worktree> --filter
    optio-dashboard dev`. Self-signed certificate (accept once per port).
- Login: `admin@optio.local` / `optio-dev`. Cookies are per host, so one login covers
  every port. URLs: `/process/<id>` selects a process (survives reload).
- **tmux session `antd-x-demo`** on excavator, windows: `demo`, `api`, `vite-antd-x`,
  `vite-main`. Logs: `/tmp/antd-x-demo.log`, `/tmp/antd-x-api.log`,
  `/tmp/antd-x-vite.log`, `/tmp/antd-x-base-vite.log`. Do not kill the session.
- **Reference conversation:** "Claude Code conversation, Config #1",
  `/process/6ac7ee8304fb01ee4e6f26a1` (markdown, code, tables and five Mermaid diagrams).
- **Screenshots** (superego, `~/chat/antd-x-shots`): `playwright-core` 1.64 driving
  the distro Chromium (`/usr/bin/chromium`, v154), no bundled browsers.
  - `node shoot.mjs <path> <name> [--wait <css>] [--only x|main] [--scroll top|bottom]
    [--full] [--dark] [--width W --height H]` writes `shots/<name>-x.png` (5180) and
    `shots/<name>-main.png` (5181). It logs in, waits for all Mermaid diagrams, and
    reports console errors, page errors and failed requests.
  - `probe-render.mjs` (renderer identity, diagram timeline), `probe-bubble.mjs`
    (computed colours of the answer bubble vs its background), `probe-mermaid.mjs`,
    `probe-load.mjs` (cold-cache load of production builds).
  - Known noise on both sides: 404 `favicon.ico`; antd 6.6 warns that `List` is
    deprecated (used by optio-ui's process list).
- **Production comparison:** `npx vite build` in each worktree's
  `packages/optio-dashboard`, then `vite preview` on 5182 (experiment) / 5183 (main)
  with the same env; `probe-load.mjs` logs in through 5180 and times cold loads.
- **Monitoring:** while running, a watch on the four logs (errors, restarts), endpoint
  health (5180/5181/3100 every 30 s) and failed `optio-demo` processes.

## 4. Paths, branches, commits

- Repo: `excavator:~/deai/optio` (github.com/deai-network/optio). **Its main checkout
  is the excavator stack's**: the stack's engine runs from its Python sources (any edit
  there restarts it) and the stack's pnpm workspace owns
  `~/deai/optio/packages/*/node_modules`. **Never `pnpm install` there**; work in
  worktrees.
- Worktrees live under `~/deai/optio/.worktrees/csillag/` on purpose: optio's
  `pnpm-workspace.yaml` links `../unitas/packages/vultus-*` relatively, which does not
  exist from there, so an install cannot relink `~/deai/unitas`. Worktrees therefore use
  the published `vultus-antd`/`vultus-core`, and their `pnpm-lock.yaml` differs from
  main's: **regenerate the lockfile in the main checkout (coordinated) before merging
  anything from here.**
- `csillag/antd-x` (worktree `.worktrees/csillag/antd-x`), on top of main `cf061416`:

  | Commit | What |
  |---|---|
  | `aaf59093` | dashboard dev proxy target from `OPTIO_API_URL` |
  | `bd813622` | setup: `@ant-design/x` + `x-markdown` deps; dashboard dev server `OPTIO_DEV_PORT`/`OPTIO_DEV_HTTPS`/`OPTIO_DEV_HOST` (+ `@vitejs/plugin-basic-ssl`) |
  | `fd210f59` | dashboard API trusts extra origins from `OPTIO_TRUSTED_ORIGINS` |
  | `d59cafd9` | message bubbles as X `Bubble` |
  | `bfed926d` | answers rendered by XMarkdown |
  | `c7bffe94` | our mermaid and math libraries dropped |
  | (this doc) | |

- `csillag/antd-x-base` (worktree `.worktrees/csillag/antd-x-base`) = the two setup
  commits only (`aaf59093`, `bd813622`): UI code identical to main.
- Rebasing onto a new main: **stop both Vites first** (a rebase passes through main's
  `vite.config.ts`, which lacks the dev-port settings, and a running Vite restarts with
  it), rebase `csillag/antd-x` onto `origin/main`, reset `csillag/antd-x-base` to the
  rebased `antd-x`'s setup commit (`HEAD~N`), rebuild TS deps in both worktrees
  (`pnpm -r --filter "optio-dashboard^..." build`), restart the API and both Vites.

### Main-branch work done along the way (pushed)

| Commit | What | Released |
|---|---|---|
| `ab8fd7cb` | conversation-ui: resume band, compaction bands (+ collapsed summary row), grok message timestamps | optio-conversation-ui 0.6.2 |
| `6a4caf70` | claudecode listener: `system/thinking_tokens` live-only (no longer fills the saved replay buffer) | optio-claudecode 0.6.8 |
| `ad3816c2` | claudecode: a claustrum-confined Claude gets `CLAUDE_CODE_TMPDIR=<workdir>/.claude-tmp` (it refused `/tmp/claude-<uid>` and exited 1; broke seed setup) | no |
| `d6f942bf` | dashboard/optio-api: documented env pinning works again (explicit `MONGODB_URL` database = single-db API; `OPTIO_PREFIX` filters discovery; no selector for one instance) | no |
| `cf061416` | dashboard: selected process in the URL (`/process/<id>`) | no |

## 5. Status

### Replaced (experiment branch)

- **User messages** (plain and queued): X `Bubble`, `placement="end"`, `filled`,
  `corner`. Time label in the bubble footer.
- **Answers:** X `Bubble`, `placement="start"`, `outlined`, `corner`, `streaming` while
  pending; time label and **`Actions.Copy`** in the footer (always visible).
- **Markdown:** `AnswerBlock` now renders with XMarkdown (answers, compaction summary,
  task descriptions): X `CodeHighlighter` for code blocks, X `Mermaid` (with its
  Image/Code/zoom/download toolbar) for mermaid fences, the Latex plugin, x-markdown
  themes, X's tail cursor while streaming. Our `optio-file:` download links go through
  XMarkdown's `a` component (plus `optio-file:` in DOMPurify's URI allow-list).
- **Removed:** our `Markdown.tsx` and `Mermaid.tsx`; dependencies `mermaid`, `katex`,
  `remark-math`, `rehype-katex` (X brings its own). `AnswerBlock`'s export and props
  (`{ text }`, plus optional `pending`) stay.

### Open issue: answer bubbles nearly invisible

The `outlined` answer bubble has no fill and a 1px `#f0f0f0` border on our transcript
background `#f5f5f5` (contrast about 1.04:1). Our `ConversationView` paints the
transcript `colorBgLayout`, which X reserves for side panels; X's templates put the
chat on `colorBgContainer`, and use the default `filled` variant with no shape. The
`outlined`/`corner` choices were mine, not X's. **Not yet decided**; the X-native step
would be: transcript on `colorBgContainer`, drop the variant/shape overrides, then move
to `Bubble.List` with a role map and `XProvider`.

### Remaining candidates

`Bubble.List` + role map (instead of per-item Bubbles), `XProvider` at the root,
`ThoughtChain` for tool rows, `Think` for thinking rows, `Bubble.System` /
`Bubble.Divider` for System rows and bands, `Sender` for the input bar, `Attachments` /
`FileCard` for uploads, `Welcome` / `Prompts` for empty states; later perhaps
`x-card` (A2UI).

### Tests (package `optio-conversation-ui`, experiment branch)

698 pass. Six view tests fail by design, all pinning the old bubble DOM (corner radius,
dashed style, interrupt class placement, our copy button, time label as a sibling).
New: `answer-block.test.tsx`; `file-download.test.tsx` now drives `AnswerBlock`;
`markdown.test.tsx` removed with our renderer.

## 6. Measurements

Reference conversation, three loads each.

Production builds (`vite build` + `vite preview`, cold cache):

| | JS files | All JS (gzip) | Up front (gzip) | DOMContentLoaded | Transcript | All 5 diagrams | Transferred |
|---|---|---|---|---|---|---|---|
| main | 52 | 4.47 MB (1.31) | 1.70 MB (0.53) | 0.62-0.87 s | 3.2-3.7 s | 4.2-5.0 s | 1.03 MB |
| experiment | 243 | 6.27 MB (1.93) | 2.48 MB (0.73) | 0.80-0.92 s | 3.7-4.4 s | 5.0-5.8 s | 1.09 MB |

- X costs about +0.2 MB gzip up front (+38%) and about 0.5 s to the transcript. Most of
  the ~3.5 s to the transcript is the conversation replay from the API, not the bundle.
- On the Vite **dev** servers the gap looks like ~3 s (8.7-9.5 s vs 5.7-6.3 s): an
  artifact of dev mode's unbundled modules, not representative.
- Diagrams: with XMarkdown + X Mermaid every diagram renders once and stays (count only
  grows). With our renderer on the dev server the count rose and fell (diagrams
  unmounted and re-rendered) and settled at 3-4 of 5; in production builds both show
  all 5.

## 7. Coordination

- The excavator stack belongs to the agent "optio perf work" (`aoe:c723ee016726`).
  Agreed: no `pnpm install` in the main checkout; log heavy steps (installs, builds,
  test runs, demo runs) with times in `~/commissura/topics.log`; the demo stays on
  `optio-demo`/`optio`; main pushes that touch optio Python sources restart the
  excavator engine once, so tell that agent first.
- This work's agent: `aoe:c1d32028376a`, crew line in `~/commissura/crew.md`.
