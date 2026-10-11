# Spinner slot (optio side)

Design, agreed with the owner on 2026-10-11. The full design, including the contract, lives in unitas
`docs/2026-10-11-spinner-slot-design.md` (vultus-core owns it); this is optio's part.

## Decisions (owner, 2026-10-11)

- An app lends its loading spinner through vultus-core's `VultusComponents = { Spinner }`
  (`<VultusProvider components={{ Spinner }}>`); the spinner's identity is a required `spinnerKey`.
- **optio-ui gains a peer dependency on vultus-core** (one copy, one context per app).
- Keys are namespaced (`optio:...`) and scoped: each agent view wraps its conversation in
  `SpinnerScope name="optio:process:<id>"`, which also scopes the vultus send bar, Interrupt and
  session controls inside.
- **The provided spinner wins over the agent-brand "native" spinner** in the conversation's
  "working…" indicator.
- Without a provider nothing changes (antd's spinners, as today). optio-dashboard has no provider.

## Changes

- optio-ui: ProcessList's loading indicator, LaunchControls' "Resurrecting" icon, and the
  "Loading…" texts of ProcessDetailView and IframeWidget (spinner before the text). AGENTS.md and README
  (deps, peers). optio-dashboard's `OPTIO_DEV_VULTUS` alias and dedupe include vultus-core.
- optio-conversation-ui: background-task, tool-row, working and todo spinners; OpencodeView's
  "Loading…"; the scopes in the seven agent views; the `vultus-antd` peer raised to the release with
  the slot; README's stale vultus-antd range.
- Releases after vultus-core and vultus-antd: optio-ui, then optio-conversation-ui (minor each), each
  with the owner's go.
