# vultus-core

Framework-neutral action model, hooks, and error-routing for the vultus UI
family. Consumed by `vultus-antd` (and, transitively, excavator +
optio-conversation-ui).

## Hard rule: this package is ANTD-FREE

Do **not** import `antd` here — not even `import type`. This is enforced by
`src/no-antd.test.ts`. All Ant Design bindings live in `vultus-antd`:

- Toasts/notifications → inject via `MessageSinkContext` (default no-op here;
  `vultus-antd`'s `VultusProvider` wires `antd.message.error`).
- Form field errors → the structural `FormLike` interface (antd's
  `FormInstance` is assignable to it), never the antd type.
- SPA links → inject via `InternalLinkContext` (default plain `<a>`).

If you need a UI-framework primitive, add it to `vultus-antd`, or add a new
injection seam (context + neutral default) here. Keeping core neutral is what
lets non-antd hosts reuse the action model.
